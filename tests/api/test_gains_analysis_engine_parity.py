"""Byte-parity gate for the P&L-engine migration of ``get_gains_analysis``.

Release 1 / Step 3 of the P&L unification (docs/plans/2026-08-02-pnl-unification-
and-manual-cost.md). ``get_gains_analysis`` (/performance/gains) is now a thin
formatter over ``compute_portfolio_pnl`` (period mode); its private per-asset
loop was deleted. This test pins the engine-backed endpoint to the exact V7.8.3
behavior on a fixed-FX fixture with distinct return_pct values (no sort-key
ties → exact ordering).

Anchors: (1) ``_legacy_gains`` — a frozen copy of the pre-engine loop; the engine
output must equal it field-by-field. (2) ``GOLDEN`` — hand-checked per-asset
constants (anti-vacuity). (3) a treatment mutation guard — balance-only omission,
cash zero-gain, traded ranking, and the non-balanceable filter are asserted
directly, so breaking the engine's treatment turns this red.
"""
import asyncio

import duckdb
import pytest

from src.database.connector import DatabaseConnector
from src.api.routes import performance as perf
from src.services.currency import calculate_cost_basis_cny
from src.services.portfolio_helpers import (
    get_display_name, resolve_top_class, is_non_balanceable_class,
    fetch_non_balanceable_asset_ids, calculate_realized_pnl,
)

FIXED_FX = 7.1


# ── Fixture: distinct return_pct per asset → no sort ties ────────────────────
#   US_STK_VOO   USD traded         ret -75.0   (in list)
#   CN_FUND_A    CNY traded + sell  ret 7.29    (in list; realized 1500)
#   MM_CASH      Money Market cash  ret 0.0     (in list; cash zero-gain)
#   INS_POLICY   Insurance traded   ret 20.0    (in list when included; non-rebal)
#   Bond_BAL     balance-only       OMITTED from list; mv counts in total only
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
            currency VARCHAR, transaction_date DATE, source_system VARCHAR,
            is_provisional BOOLEAN)"""
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
        ('INS_POLICY','安泰人生','Insurance',FALSE),
        ('Bond_BAL','招行固收债券','CN Bonds',TRUE)"""
    )
    conn.execute(
        """INSERT INTO holdings VALUES
        ('US_STK_VOO','Vanguard S&P 500','Schwab_CSV',142000.0,400.0,100.0,200.0,'USD',DATE '2026-07-01',FALSE),
        ('CN_FUND_A','易方达','CN_Fund_Excel',50000.0,1.2,1.4,40000.0,'CNY',DATE '2026-07-01',FALSE),
        ('MM_CASH','示例流动货币B','CN_Fund_Excel',20000.0,1.0,1.0,20000.0,'CNY',DATE '2026-07-01',FALSE),
        ('INS_POLICY','安泰人生','Insurance_Excel',30000.0,100.0,120.0,250.0,'CNY',DATE '2026-07-01',FALSE),
        ('Bond_BAL','招行固收债券','Financial_Summary_Excel',200000.0,NULL,200000.0,1.0,'CNY',DATE '2026-07-01',FALSE)"""
    )
    conn.execute(
        """INSERT INTO transactions VALUES
        ('US_STK_VOO','Vanguard S&P 500','buy',200.0,400.0,80000.0,'USD',DATE '2025-01-15','Schwab_CSV',FALSE),
        ('CN_FUND_A','易方达','buy',40000.0,1.2,48000.0,'CNY',DATE '2025-02-10','CN_Fund_Excel',FALSE),
        ('CN_FUND_A','易方达','sell',10000.0,1.35,13500.0,'CNY',DATE '2025-06-01','CN_Fund_Excel',FALSE),
        ('INS_POLICY','安泰人生','buy',250.0,100.0,25000.0,'CNY',DATE '2024-05-01','Insurance_Excel',FALSE)"""
    )
    conn.close()


# ── Frozen V7.8.3 reference — faithful copy of the pre-engine gains loop ──────
def _legacy_gains(db, period="all_time", exclude=False):
    start_date = perf.period_start_date(period)
    latest_cte, latest_params = perf.latest_snapshot_cte(start_date)
    excluded_asset_ids = fetch_non_balanceable_asset_ids(db) if exclude else set()
    today_fx = FIXED_FX
    txn_asset_ids = perf._assets_with_transactions(db)
    query = f"""
        {latest_cte}
        SELECT h.asset_id, MAX(h.asset_name) as name, SUM(h.market_value) as market_value,
            COALESCE(MAX(parent_tc.name), MAX(tc.name), MAX(r.asset_class), 'Unclassified') as top_class,
            COALESCE(MAX(r.asset_class), 'Unclassified') as sub_class,
            MAX(h.market_price_unit) as market_price_unit, MAX(h.cost_price_unit) as cost_price_unit,
            SUM(h.quantity) as qty_sum, MAX(h.currency) as currency
        FROM holdings h JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
        LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
        LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
        WHERE h.is_shadow = FALSE GROUP BY h.asset_id
    """
    rows = db.execute(query, latest_params or None).fetchall()
    assets = []
    tu = tr = tc = tmv = 0.0
    for row in rows:
        aid = row[0]
        if not aid:
            continue
        if exclude and aid in excluded_asset_ids:
            continue
        name = row[1]
        mv = float(row[2] or 0.0)
        resolved_top = resolve_top_class(row[3] or "")
        resolved_sub = get_display_name(row[4] or "")
        if exclude and is_non_balanceable_class(resolved_top):
            continue
        top_class = get_display_name(row[3])
        mpu = float(row[5] or 0.0)
        cpu = float(row[6] or 0.0)
        qty = float(row[7] or 0.0)
        curr = str(row[8] or "CNY")
        if perf._is_balance_only(top_class=resolved_top, sub_class=resolved_sub,
                                 cost_price_unit=cpu, aid=aid, txn_ids=txn_asset_ids):
            tmv += mv
            continue
        cost = calculate_cost_basis_cny(market_value=mv, quantity=qty, cost_price_unit=cpu, currency=curr,
                                        top_class=resolved_top, sub_class=resolved_sub, today_fx=today_fx)
        unrealized, unrealized_native = perf.calculate_unrealized_pl_values(
            market_value=mv, quantity=qty, cost_price_unit=cpu, market_price_unit=mpu, currency=curr,
            top_class=resolved_top, sub_class=resolved_sub, today_fx=today_fx)
        realized_native, pnl_currency = calculate_realized_pnl(db, aid, start_date=start_date)
        realized = realized_native * today_fx if pnl_currency == "USD" else realized_native
        pp = unrealized + realized
        ret_pct = (pp / cost * 100) if cost != 0 else 0.0
        assets.append({"asset_id": aid, "name": name, "top_class": top_class, "currency": curr,
                       "cost_basis": cost, "market_value": mv, "unrealized_pl": unrealized,
                       "realized_pl": realized, "pnl_currency": pnl_currency,
                       "unrealized_pl_native": unrealized_native, "realized_pl_native": realized_native,
                       "period_pl": pp, "return_pct": round(ret_pct, 2)})
        tu += unrealized
        tr += realized
        tc += cost
        tmv += mv
    assets.sort(key=lambda x: x["return_pct"], reverse=True)
    tl = tu + tr
    return {"total_unrealized_pl": tu, "total_realized_pl": tr, "total_lifetime_pl": tl,
            "total_cost_basis": tc, "total_market_value": tmv,
            "unrealized_pl_pct": round((tl / tc * 100) if tc != 0 else 0.0, 2), "assets": assets}


@pytest.fixture
def frozen_fx(monkeypatch):
    import src.services.pnl.engine as engine_mod
    monkeypatch.setattr(engine_mod, "get_today_usd_cny_rate", lambda: FIXED_FX)
    monkeypatch.setattr(perf, "get_today_usd_cny_rate", lambda: FIXED_FX)


def _new(db, period, exclude):
    return asyncio.new_event_loop().run_until_complete(
        perf.get_gains_analysis(period=period, exclude_non_balanceable=exclude,
                                include_non_rebalanceable=not exclude, db=db))


def _strip_cn_fields(payload: dict) -> dict:
    """Program BIL / WS-9 added an additive ``top_class_cn`` companion field to
    /performance/gains — a DELIBERATE divergence from the frozen V7.8.3 legacy
    shape, not drift. ``_legacy_gains`` stays a verbatim copy of the pre-engine
    loop, so strip the companion from the engine output before the byte-parity
    comparison. Every other field must still match exactly."""
    stripped = {**payload}
    stripped["assets"] = [
        {k: v for k, v in row.items() if not k.endswith("_cn")}
        for row in payload["assets"]
    ]
    return stripped


@pytest.mark.parametrize("exclude", [False, True])
def test_engine_gains_equals_frozen_legacy(tmp_path, frozen_fx, exclude):
    db_path = tmp_path / "gains_parity.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        legacy = _legacy_gains(db, "all_time", exclude)
        new = _new(db, "all_time", exclude)
    finally:
        db.close()
    assert _strip_cn_fields(new) == legacy


# Hand-checked constants (FX = 7.1, all-time, include everything).
GOLDEN = {
    "US_STK_VOO": {"cost_basis": 568000.0, "market_value": 142000.0, "unrealized_pl": -426000.0,
                   "unrealized_pl_native": -60000.0, "realized_pl": 0.0, "pnl_currency": "USD",
                   "period_pl": -426000.0, "return_pct": -75.0},
    "CN_FUND_A": {"cost_basis": 48000.0, "market_value": 50000.0, "unrealized_pl": 2000.0,
                  "realized_pl": 1500.0, "pnl_currency": "CNY", "period_pl": 3500.0, "return_pct": 7.29},
    "MM_CASH": {"cost_basis": 20000.0, "market_value": 20000.0, "unrealized_pl": 0.0,
                "realized_pl": 0.0, "period_pl": 0.0, "return_pct": 0.0},
    "INS_POLICY": {"cost_basis": 25000.0, "market_value": 30000.0, "unrealized_pl": 5000.0,
                   "realized_pl": 0.0, "period_pl": 5000.0, "return_pct": 20.0},
}


def test_gains_matches_golden_constants(tmp_path, frozen_fx):
    db_path = tmp_path / "gains_golden.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        res = _new(db, "all_time", False)  # include everything
    finally:
        db.close()
    by_code = {r["asset_id"]: r for r in res["assets"]}
    assert "Bond_BAL" not in by_code, "balance-only asset must be omitted from the ranked list"
    for code, expected in GOLDEN.items():
        for k, v in expected.items():
            got = by_code[code][k]
            if isinstance(v, float):
                assert got == pytest.approx(v, abs=0.01), f"{code}.{k}: {got} != {v}"
            else:
                assert got == v, f"{code}.{k}: {got!r} != {v!r}"
    # Balance-only value still counts in the portfolio total; sort is return_pct DESC.
    assert res["total_market_value"] == pytest.approx(442000.0)  # incl. Bond_BAL 200000
    assert [r["asset_id"] for r in res["assets"]] == ["INS_POLICY", "CN_FUND_A", "MM_CASH", "US_STK_VOO"]


def test_treatment_and_filter_mutation_guard(tmp_path, frozen_fx):
    """Breaking balance-only omission, cash zero-gain, or the non-balanceable
    filter turns this red."""
    db_path = tmp_path / "gains_treat.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        inc = _new(db, "all_time", False)   # include non-rebalanceable
        exc = _new(db, "all_time", True)    # exclude non-rebalanceable
    finally:
        db.close()

    inc_codes = {r["asset_id"] for r in inc["assets"]}
    exc_codes = {r["asset_id"] for r in exc["assets"]}

    # Balance-only bond never appears in the ranked list, but its value is in total.
    assert "Bond_BAL" not in inc_codes and "Bond_BAL" not in exc_codes
    assert inc["total_market_value"] == pytest.approx(442000.0)

    # Cash zero-gain, but still ranked.
    mm = next(r for r in inc["assets"] if r["asset_id"] == "MM_CASH")
    assert mm["unrealized_pl"] == 0.0 and mm["realized_pl"] == 0.0 and mm["cost_basis"] == pytest.approx(20000.0)

    # Non-balanceable Insurance: present when included, dropped when excluded, and
    # its value leaves the total when excluded.
    assert "INS_POLICY" in inc_codes and "INS_POLICY" not in exc_codes
    assert exc["total_market_value"] == pytest.approx(412000.0)  # 442000 − INS 30000

    # Traded asset keeps a real non-zero P&L and is ranked by return_pct DESC.
    assert inc["assets"][0]["asset_id"] == "INS_POLICY"   # +20%
    assert inc["assets"][-1]["asset_id"] == "US_STK_VOO"  # −75%
