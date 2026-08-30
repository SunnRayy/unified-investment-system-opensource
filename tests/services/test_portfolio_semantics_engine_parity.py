"""Byte-parity gate for the P&L-engine migration of ``portfolio_semantics``.

Release 1 / Step 5 of the P&L unification. Both AI-advisor semantics functions —
``build_portfolio_summary_semantics`` (aggregate) and
``fetch_wealthos_active_holdings`` (per-asset) — are now thin formatters over
``compute_portfolio_pnl``; their private per-asset loops were deleted. Both
already carried the V7.8.3 balance-only rule (non-cash balance-only excluded from
gain aggregates; cash checked first), so this is BYTE-PARITY — no number changes.

Anchors: (1) ``_legacy_summary`` / ``_legacy_wealthos`` — frozen copies of the
pre-engine functions; engine output == them field-by-field on a fixed-FX fixture.
(2) ``GOLDEN_*`` — hand-checked constants. (3) a balance-only mutation guard —
breaking the balance-only exclusion turns it red.
"""
from __future__ import annotations

import duckdb
import pytest

from src.database.connector import DatabaseConnector
import src.services.pnl.engine as engine_mod
from src.services.portfolio_semantics import (
    build_portfolio_summary_semantics,
    fetch_wealthos_active_holdings,
)
from src.services.currency import (
    calculate_cost_basis_cny,
    is_balance_only_holding,
    is_cash_equivalent_asset,
)
from src.services.portfolio_helpers import (
    calculate_realized_pnl,
    get_display_name,
    is_non_balanceable_class,
    resolve_top_class,
)
from src.services.rebalanceable_filter import fetch_non_rebalanceable_asset_ids
from src.services.pnl.snapshot import sold_after_snapshot as snapshot_sold_after

FIXED_FX = 7.1

_CASH_KEYWORDS = ("Cash", "现金", "Money Market", "Bank Wealth", "货币")
_NON_REBAL_TYPES = {
    "Real Estate", "Insurance", "房地产", "保险", "Property (房产)",
    "Insurance (保险)", "Residential (住宅)", "Commercial (商业)", "REITs (信托)",
}
_SOLD_CLOSE_SOURCES = {"Schwab_CSV", "CN_Fund_Excel", "Gold_Excel", "Insurance_Excel", "RSU_Excel"}


# Fixture: VOO USD-traded, CN_FUND_A CNY-traded+sell (realized 1500), MM_CASH cash,
# Bond_BAL balance-only, INS_POLICY non-rebalanceable, US_STK_SOLD closed (realized 7100).
def _seed(path):
    conn = duckdb.connect(str(path))
    conn.execute(
        """CREATE TABLE holdings (
            asset_id VARCHAR, asset_name VARCHAR, source_system VARCHAR,
            market_value DOUBLE, cost_price_unit DOUBLE, market_price_unit DOUBLE,
            quantity DOUBLE, currency VARCHAR, snapshot_date DATE, is_shadow BOOLEAN)"""
    )
    conn.execute(
        """CREATE TABLE transactions (
            asset_id VARCHAR, asset_name VARCHAR, transaction_type VARCHAR,
            quantity DOUBLE, price_unit DOUBLE, amount_net DOUBLE,
            currency VARCHAR, transaction_date DATE, source_system VARCHAR)"""
    )
    conn.execute(
        "CREATE TABLE asset_registry (canonical_id VARCHAR, display_name VARCHAR, asset_class VARCHAR, is_rebalanceable BOOLEAN)"
    )
    conn.execute(
        "CREATE TABLE taxonomy_classes (id INTEGER, name VARCHAR, name_cn VARCHAR, parent_id INTEGER, is_rebalanceable BOOLEAN)"
    )
    conn.execute(
        """INSERT INTO taxonomy_classes VALUES
        (1,'Fixed Income','固定收益',NULL,TRUE),
        (2,'CN Bonds','中国债券',1,TRUE),
        (3,'Equity','股票',NULL,TRUE),
        (4,'US Equity','美股',3,TRUE),
        (7,'CN Equity','A股',3,TRUE),
        (10,'Money Market','货基',NULL,TRUE),
        (11,'Insurance','保险',NULL,FALSE)"""
    )
    conn.execute(
        """INSERT INTO asset_registry VALUES
        ('US_STK_VOO','Vanguard S&P 500','US Equity',TRUE),
        ('CN_FUND_A','易方达','CN Equity',TRUE),
        ('MM_CASH','示例流动货币B','Money Market',TRUE),
        ('Bond_BAL','招行固收债券','CN Bonds',TRUE),
        ('INS_POLICY','安泰人生','Insurance',FALSE),
        ('US_STK_SOLD','Sold Co','US Equity',TRUE)"""
    )
    conn.execute(
        """INSERT INTO holdings VALUES
        ('US_STK_VOO','Vanguard S&P 500','Schwab_CSV',142000.0,400.0,100.0,200.0,'USD',DATE '2026-07-01',FALSE),
        ('CN_FUND_A','易方达','CN_Fund_Excel',50000.0,1.2,1.4,40000.0,'CNY',DATE '2026-07-01',FALSE),
        ('MM_CASH','示例流动货币B','CN_Fund_Excel',20000.0,1.0,1.0,20000.0,'CNY',DATE '2026-07-01',FALSE),
        ('Bond_BAL','招行固收债券','Financial_Summary_Excel',200000.0,NULL,200000.0,1.0,'CNY',DATE '2026-07-01',FALSE),
        ('INS_POLICY','安泰人生','Insurance_Excel',30000.0,100.0,120.0,250.0,'CNY',DATE '2026-07-01',FALSE)"""
    )
    conn.execute(
        """INSERT INTO transactions VALUES
        ('US_STK_VOO','Vanguard S&P 500','buy',200.0,400.0,80000.0,'USD',DATE '2025-01-15','Schwab_CSV'),
        ('CN_FUND_A','易方达','buy',40000.0,1.2,48000.0,'CNY',DATE '2025-02-10','CN_Fund_Excel'),
        ('CN_FUND_A','易方达','sell',10000.0,1.35,13500.0,'CNY',DATE '2025-06-01','CN_Fund_Excel'),
        ('INS_POLICY','安泰人生','buy',250.0,100.0,25000.0,'CNY',DATE '2024-05-01','Insurance_Excel'),
        ('US_STK_SOLD','Sold Co','buy',100.0,50.0,5000.0,'USD',DATE '2025-03-01','Schwab_CSV'),
        ('US_STK_SOLD','Sold Co','sell',100.0,60.0,6000.0,'USD',DATE '2025-09-01','Schwab_CSV')"""
    )
    conn.close()


# ── Frozen V7.8.3 references (faithful copies of the pre-engine functions) ────
def _safe_realized(db, asset_id):
    try:
        amt, cur = calculate_realized_pnl(db, asset_id, start_date=None)
        if cur == "USD":
            return float(amt or 0.0) * FIXED_FX
        return float(amt or 0.0)
    except Exception:
        return 0.0


_PERF_SUMMARY_SQL = """
    WITH latest_per_asset AS (
        SELECT asset_id, MAX(snapshot_date) AS latest_date
        FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
    )
    SELECT h.asset_id,
        COALESCE(MAX(parent_tc.name), MAX(tc.name), MAX(r.asset_class), 'Unclassified') AS top_class,
        COALESCE(MAX(r.asset_class), 'Unclassified') AS sub_class,
        SUM(h.market_value) AS market_value, SUM(h.quantity) AS quantity,
        MAX(h.cost_price_unit) AS cost_price_unit, MAX(h.currency) AS currency
    FROM holdings h
    JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
    LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
    LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
    LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
    WHERE h.is_shadow = FALSE GROUP BY h.asset_id
"""


def _legacy_summary(db, include_non_rebalanceable=False):
    rows = db.execute(_PERF_SUMMARY_SQL).fetchall()
    excluded_ids = fetch_non_rebalanceable_asset_ids(db) if not include_non_rebalanceable else set()
    today_fx = FIXED_FX
    txn_asset_ids = {
        str(r[0]) for r in db.execute(
            "SELECT DISTINCT asset_id FROM transactions WHERE asset_id IS NOT NULL"
        ).fetchall() if r and r[0]
    }
    net_worth = 0.0
    total_cost_basis = 0.0
    measurable_value = 0.0
    active_assets = set()
    for asset_id, top_class, sub_class, market_value, quantity, cost_price_unit, currency in rows:
        if not asset_id:
            continue
        resolved_top = resolve_top_class(str(top_class or ""))
        resolved_sub = get_display_name(str(sub_class or ""))
        if not include_non_rebalanceable:
            if asset_id in excluded_ids:
                continue
            if is_non_balanceable_class(resolved_top) or is_non_balanceable_class(resolved_sub):
                continue
        mv = float(market_value or 0.0)
        if not is_cash_equivalent_asset(resolved_top, resolved_sub) and is_balance_only_holding(
            cost_price_unit=cost_price_unit, has_transactions=str(asset_id) in txn_asset_ids,
        ):
            net_worth += mv
            active_assets.add(str(asset_id))
            continue
        cost_basis = calculate_cost_basis_cny(
            market_value=mv, quantity=float(quantity or 0.0),
            cost_price_unit=float(cost_price_unit or 0.0), currency=str(currency or "CNY"),
            top_class=resolved_top, sub_class=resolved_sub, today_fx=today_fx,
        )
        net_worth += mv
        measurable_value += mv
        total_cost_basis += cost_basis
        active_assets.add(str(asset_id))
    total_unrealized_pl = measurable_value - total_cost_basis
    unrealized_pl_pct = (total_unrealized_pl / total_cost_basis * 100.0) if total_cost_basis else 0.0
    all_assets = set(active_assets)
    all_assets.update(
        str(r[0]) for r in db.execute("SELECT DISTINCT asset_id FROM transactions").fetchall()
        if r and r[0]
    )
    total_realized_pl = 0.0
    for asset_id in sorted(all_assets):
        if not include_non_rebalanceable and asset_id in excluded_ids:
            continue
        total_realized_pl += _safe_realized(db, asset_id)
    row = db.execute("SELECT MAX(snapshot_date) FROM holdings WHERE is_shadow = FALSE").fetchone()
    snapshot_date = str(row[0]) if row and row[0] else None
    return {
        "net_worth": round(net_worth, 2),
        "total_cost_basis": round(total_cost_basis, 2),
        "total_unrealized_pl": round(total_unrealized_pl, 2),
        "unrealized_pl_pct": round(unrealized_pl_pct, 2),
        "total_realized_pl": round(total_realized_pl, 2),
        "total_lifetime_pl": round(total_unrealized_pl + total_realized_pl, 2),
        "asset_count": len(active_assets),
        "snapshot_date": snapshot_date,
    }


_WEALTHOS_SQL = """
    WITH latest_per_asset AS (
        SELECT asset_id, MAX(snapshot_date) AS latest_date
        FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
    )
    SELECT h.asset_id,
        COALESCE(NULLIF(TRIM(MAX(h.asset_name)), ''), NULLIF(TRIM(MAX(r.display_name)), ''), h.asset_id) AS name,
        COALESCE(MAX(r.asset_class), 'Unknown') AS asset_class,
        MAX(h.source_system) AS source_system,
        SUM(h.market_value) AS market_value,
        MAX(h.cost_price_unit) AS cost_price_unit,
        MAX(h.currency) AS currency,
        SUM(h.quantity) AS total_quantity,
        MAX(lpa.latest_date) AS latest_date
    FROM holdings h
    JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
    LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
    WHERE h.is_shadow = FALSE GROUP BY h.asset_id
    HAVING SUM(h.market_value) > 0 AND SUM(h.quantity) > 0
"""

def _legacy_wealthos(db, include_non_rebalanceable=False):
    active_rows = db.execute(_WEALTHOS_SQL).fetchall()
    active_source_map = {str(r[0]): r[3] for r in active_rows if r and r[0]}
    # sold-after-snapshot is byte-identical to the engine's snapshot helper
    # (the pre-engine copy was verbatim); restrict to the WealthOS candidate sources.
    sold_after_snapshot = {
        str(aid) for aid in snapshot_sold_after(db)
        if active_source_map.get(str(aid)) in _SOLD_CLOSE_SOURCES
    }
    excluded_ids = fetch_non_rebalanceable_asset_ids(db) if not include_non_rebalanceable else set()
    today_fx = FIXED_FX
    txn_asset_ids = {
        str(r[0]) for r in db.execute(
            "SELECT DISTINCT asset_id FROM transactions WHERE asset_id IS NOT NULL"
        ).fetchall() if r and r[0]
    }
    results = []
    for asset_id, name, asset_class, source_system, market_value, cost_price_unit, currency, total_quantity, _latest in active_rows:
        asset_id = str(asset_id)
        asset_type = str(asset_class or "Unknown")
        if asset_id in sold_after_snapshot:
            continue
        if not include_non_rebalanceable and (
            asset_id in excluded_ids or (asset_type in _NON_REBAL_TYPES)
        ):
            continue
        market_value_num = float(market_value or 0.0)
        total_qty = float(total_quantity or 0.0)
        cost_basis_num = calculate_cost_basis_cny(
            market_value=market_value_num, quantity=total_qty,
            cost_price_unit=float(cost_price_unit or 0.0), currency=str(currency or "CNY"),
            top_class=asset_type, sub_class=asset_type, today_fx=today_fx,
        )
        is_cash_equiv = any(kw in asset_type for kw in _CASH_KEYWORDS)
        is_bal_only = is_balance_only_holding(
            cost_price_unit=cost_price_unit, has_transactions=asset_id in txn_asset_ids,
        )
        if is_cash_equiv:
            cost_basis_out = cost_basis_num
            lifetime_pl_out = 0.0
            return_pct_out = 0.0
        elif is_bal_only:
            cost_basis_out = None
            lifetime_pl_out = None
            return_pct_out = None
        else:
            realized_pl = _safe_realized(db, asset_id)
            lifetime_pl = (market_value_num - cost_basis_num) + realized_pl
            denom = cost_basis_num if cost_basis_num != 0 else abs(realized_pl)
            cost_basis_out = cost_basis_num
            lifetime_pl_out = round(lifetime_pl, 3)
            return_pct_out = round((lifetime_pl / denom * 100.0) if denom else 0.0, 2)
        results.append({
            "asset_id": asset_id, "name": name or asset_id, "asset_class": asset_type,
            "source_system": source_system, "market_value": market_value_num,
            "cost_basis": cost_basis_out, "total_quantity": total_qty,
            "lifetime_pl": lifetime_pl_out, "return_pct": return_pct_out,
        })
    results.sort(key=lambda row: row["market_value"], reverse=True)
    return results


@pytest.fixture
def frozen_fx(monkeypatch):
    monkeypatch.setattr(engine_mod, "get_today_usd_cny_rate", lambda: FIXED_FX)


# ── Parity: engine output == frozen legacy, field-by-field ───────────────────
@pytest.mark.parametrize("inc", [False, True])
def test_summary_equals_frozen_legacy(tmp_path, frozen_fx, inc):
    db_path = tmp_path / "sem_summary_parity.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        legacy = _legacy_summary(db, include_non_rebalanceable=inc)
        new = build_portfolio_summary_semantics(db, include_non_rebalanceable=inc)
    finally:
        db.close()
    assert new == legacy


@pytest.mark.parametrize("inc", [False, True])
def test_wealthos_equals_frozen_legacy(tmp_path, frozen_fx, inc):
    db_path = tmp_path / "sem_wealthos_parity.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        legacy = _legacy_wealthos(db, include_non_rebalanceable=inc)
        new = fetch_wealthos_active_holdings(db, include_non_rebalanceable=inc)
    finally:
        db.close()
    assert new == legacy


# ── Golden constants (anti-vacuity) ──────────────────────────────────────────
GOLDEN_SUMMARY_INCLUDE = {
    "net_worth": 442000.0,          # 142000+50000+20000+200000+30000
    "total_cost_basis": 661000.0,   # 568000+48000+20000+25000 (Bond_BAL excluded)
    "total_unrealized_pl": -419000.0,  # 242000 measurable − 661000
    "total_realized_pl": 8600.0,    # CN_FUND_A 1500 + US_STK_SOLD 7100
    "total_lifetime_pl": -410400.0,
    "asset_count": 5,
}


def test_summary_matches_golden_constants(tmp_path, frozen_fx):
    db_path = tmp_path / "sem_summary_golden.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        res = build_portfolio_summary_semantics(db, include_non_rebalanceable=True)
    finally:
        db.close()
    for k, v in GOLDEN_SUMMARY_INCLUDE.items():
        got = res[k]
        if isinstance(v, float):
            assert got == pytest.approx(v, abs=0.01), f"{k}: {got} != {v}"
        else:
            assert got == v, f"{k}: {got!r} != {v!r}"


GOLDEN_WEALTHOS_INCLUDE = {
    "US_STK_VOO": {"cost_basis": 568000.0, "market_value": 142000.0, "lifetime_pl": -426000.0, "return_pct": -75.0},
    "CN_FUND_A": {"cost_basis": 48000.0, "market_value": 50000.0, "lifetime_pl": 3500.0, "return_pct": 7.29},
    "MM_CASH": {"cost_basis": 20000.0, "market_value": 20000.0, "lifetime_pl": 0.0, "return_pct": 0.0},
    "Bond_BAL": {"cost_basis": None, "market_value": 200000.0, "lifetime_pl": None, "return_pct": None},
    "INS_POLICY": {"cost_basis": 25000.0, "market_value": 30000.0, "lifetime_pl": 5000.0, "return_pct": 20.0},
}


def test_wealthos_matches_golden_constants(tmp_path, frozen_fx):
    db_path = tmp_path / "sem_wealthos_golden.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        rows = fetch_wealthos_active_holdings(db, include_non_rebalanceable=True)
    finally:
        db.close()
    by_id = {r["asset_id"]: r for r in rows}
    assert "US_STK_SOLD" not in by_id, "closed/txn-only asset must not appear in the active list"
    for code, expected in GOLDEN_WEALTHOS_INCLUDE.items():
        for k, v in expected.items():
            got = by_id[code][k]
            if isinstance(v, float):
                assert got == pytest.approx(v, abs=0.01), f"{code}.{k}: {got} != {v}"
            else:
                assert got == v, f"{code}.{k}: {got!r} != {v!r}"
    # Sorted by market value desc.
    assert [r["asset_id"] for r in rows] == ["Bond_BAL", "US_STK_VOO", "CN_FUND_A", "INS_POLICY", "MM_CASH"]


def test_balance_only_exclusion_mutation_guard(tmp_path, frozen_fx):
    """V7.8.3 rule: balance-only value counts in net worth but its unknown cost is
    excluded from the gain aggregate (summary) / null per-asset (wealthos)."""
    db_path = tmp_path / "sem_balance_only.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        summary = build_portfolio_summary_semantics(db, include_non_rebalanceable=True)
        wealthos = fetch_wealthos_active_holdings(db, include_non_rebalanceable=True)
    finally:
        db.close()

    # Summary: Bond_BAL's 200000 IS in net worth, but NOT in cost basis. If the
    # exclusion were removed (cost = value), cost would be 861000, unrealized
    # -219000 — so these exact figures fail on any regression.
    assert summary["net_worth"] == pytest.approx(442000.0)  # includes Bond_BAL 200000
    assert summary["total_cost_basis"] == pytest.approx(661000.0)  # excludes Bond_BAL
    assert summary["total_unrealized_pl"] == pytest.approx(-419000.0)

    # Wealthos: Bond_BAL is a null-cost/pl/ret record, value retained.
    bond = next(r for r in wealthos if r["asset_id"] == "Bond_BAL")
    assert bond["cost_basis"] is None and bond["lifetime_pl"] is None and bond["return_pct"] is None
    assert bond["market_value"] == pytest.approx(200000.0)
