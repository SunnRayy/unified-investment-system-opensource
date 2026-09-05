"""Byte-parity gate for the P&L-engine migration of ``get_performance_summary``.

Release 1 / Step 1 of the P&L unification (docs/plans/2026-08-02-pnl-unification-
and-manual-cost.md). ``get_performance_summary`` was re-expressed as a thin
formatter over ``src.services.pnl.compute_portfolio_pnl``; the old private loop
was deleted from the route. This test pins the engine-backed endpoint to the
*exact* V7.8.3 pre-engine behavior on a fixed-FX, frozen-clock fixture DB.

Two independent anchors so the parity claim cannot be vacuous:

1. ``_legacy_summary`` — a frozen, verbatim copy of the pre-engine V7.8.3 loop,
   retained here (and ONLY here) as the reference implementation. The engine
   output must equal it field-by-field. This is the standard strangler-fig
   parity technique; the duplicate lives in the test, never in production.
2. ``GOLDEN_*`` — hand-checked constants. If the engine AND the frozen reference
   ever drift together, the constants still fail (anti-vacuity anchor).

The treatment mutation guard asserts the per-asset cash / balance-only / traded
classification directly, so breaking the engine's treatment logic turns this
file red even if the aggregates happened to coincide.
"""
import asyncio

import duckdb
import pytest

from src.database.connector import DatabaseConnector
from src.api.routes import performance as perf
from src.services.pnl import Scope, Treatment, compute_portfolio_pnl
from src.services.currency import calculate_cost_basis_cny
from src.services.portfolio_helpers import (
    get_display_name,
    resolve_top_class,
    is_non_balanceable_class,
    fetch_non_balanceable_asset_ids,
    calculate_realized_pnl,
)

FIXED_FX = 7.1


# ── Fixture: five current assets covering every treatment + one closed asset ──
#   Bond_CMB_CNY            balance-only (no cost, no txn) — in net worth, not gain
#   US_STK_VOO              traded, USD                    — FIFO cost × today FX
#   CASH_Deposit_TEST_CNY   cash-equivalent               — cost == value, gain 0
#   CN_FUND_900013          traded, CNY, with a sell       — realized + unrealized
#   Property_HOME           balance-only AND non-balanceable (Real Estate)
#   US_STK_SOLD             fully sold (closed)            — realized only, USD
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
        """CREATE TABLE asset_registry (
            canonical_id VARCHAR, display_name VARCHAR, asset_class VARCHAR,
            is_rebalanceable BOOLEAN)"""
    )
    conn.execute(
        """CREATE TABLE taxonomy_classes (
            id INTEGER, name VARCHAR, name_cn VARCHAR, parent_id INTEGER,
            is_rebalanceable BOOLEAN)"""
    )
    conn.execute(
        """INSERT INTO taxonomy_classes VALUES
        (1,'Fixed Income','固定收益',NULL,TRUE),
        (2,'CN Bonds','中国债券',1,TRUE),
        (3,'Equity','股票',NULL,TRUE),
        (4,'US Equity','美股',3,TRUE),
        (5,'Cash','现金',NULL,TRUE),
        (6,'Cash Checking','活期',5,TRUE),
        (7,'CN Equity','A股',3,TRUE),
        (8,'Real Estate','房地产',NULL,FALSE),
        (9,'Property','房产',8,FALSE)"""
    )
    conn.execute(
        """INSERT INTO asset_registry VALUES
        ('Bond_CMB_CNY','招行固收债券','CN Bonds',TRUE),
        ('US_STK_VOO','Vanguard S&P 500','US Equity',TRUE),
        ('CASH_Deposit_TEST_CNY','测试活期','Cash Checking',TRUE),
        ('CN_FUND_900013','易方达','CN Equity',TRUE),
        ('US_STK_SOLD','Sold Co','US Equity',TRUE),
        ('Property_HOME','自住房','Property',FALSE)"""
    )
    conn.execute(
        """INSERT INTO holdings VALUES
        ('Bond_CMB_CNY','招行固收债券','Financial_Summary_Excel',200108.77,NULL,200108.77,1.0,'CNY',DATE '2026-07-01',FALSE),
        ('US_STK_VOO','Vanguard S&P 500','Schwab_CSV',142000.0,400.0,100.0,200.0,'USD',DATE '2026-07-01',FALSE),
        ('CASH_Deposit_TEST_CNY','测试活期','Financial_Summary_Excel',30000.0,NULL,30000.0,1.0,'CNY',DATE '2026-07-01',FALSE),
        ('CN_FUND_900013','易方达','CN_Fund_Excel',50000.0,1.2,1.4,40000.0,'CNY',DATE '2026-07-01',FALSE),
        ('Property_HOME','自住房','Financial_Summary_Excel',100000.0,NULL,100000.0,1.0,'CNY',DATE '2026-07-01',FALSE)"""
    )
    conn.execute(
        """INSERT INTO transactions VALUES
        ('US_STK_VOO','Vanguard S&P 500','buy',200.0,400.0,80000.0,'USD',DATE '2025-01-15','Schwab_CSV',FALSE),
        ('CN_FUND_900013','易方达','buy',40000.0,1.2,48000.0,'CNY',DATE '2025-02-10','CN_Fund_Excel',FALSE),
        ('CN_FUND_900013','易方达','sell',10000.0,1.35,13500.0,'CNY',DATE '2025-06-01','CN_Fund_Excel',FALSE),
        ('US_STK_SOLD','Sold Co','buy',100.0,50.0,5000.0,'USD',DATE '2025-03-01','Schwab_CSV',FALSE),
        ('US_STK_SOLD','Sold Co','sell',100.0,60.0,6000.0,'USD',DATE '2025-09-01','Schwab_CSV',FALSE)"""
    )
    conn.close()


# ── Frozen V7.8.3 reference — verbatim pre-engine loop, retained ONLY here ────
def _legacy_summary(db, start_date=None, exclude_non_balanceable=False):
    latest_cte, latest_params = perf.latest_snapshot_cte(start_date)
    excluded_asset_ids = (
        fetch_non_balanceable_asset_ids(db) if exclude_non_balanceable else set()
    )
    today_fx = FIXED_FX
    holdings_rows = db.execute(
        f"""
        {latest_cte}
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
        """,
        latest_params or None,
    ).fetchall()
    txn_asset_ids = perf._assets_with_transactions(db)
    net_worth = 0.0
    total_cost_basis = 0.0
    measurable_value = 0.0
    active_assets = set()
    for aid, top_class, sub_class, market_value, quantity, cost_price_unit, currency in holdings_rows:
        if not aid:
            continue
        resolved_top = resolve_top_class(top_class or "")
        resolved_sub = get_display_name(sub_class or "")
        if exclude_non_balanceable:
            if aid in excluded_asset_ids:
                continue
            if is_non_balanceable_class(resolved_top) or is_non_balanceable_class(resolved_sub):
                continue
        market_value = float(market_value or 0.0)
        if perf._is_balance_only(
            top_class=resolved_top, sub_class=resolved_sub,
            cost_price_unit=cost_price_unit, aid=aid, txn_ids=txn_asset_ids,
        ):
            net_worth += market_value
            active_assets.add(aid)
            continue
        cost_basis = calculate_cost_basis_cny(
            market_value=market_value, quantity=float(quantity or 0.0),
            cost_price_unit=float(cost_price_unit or 0.0), currency=str(currency or "CNY"),
            top_class=resolved_top, sub_class=resolved_sub, today_fx=today_fx,
        )
        net_worth += market_value
        measurable_value += market_value
        total_cost_basis += cost_basis
        active_assets.add(aid)
    total_unrealized_pl = measurable_value - total_cost_basis
    unrealized_pl_pct = (total_unrealized_pl / total_cost_basis * 100) if total_cost_basis != 0 else 0.0
    tx_filter = "WHERE transaction_date >= ?" if start_date else ""
    all_assets_query = f"""
        {latest_cte}
        SELECT DISTINCT h.asset_id FROM holdings h
        JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        WHERE h.is_shadow = FALSE
        UNION SELECT DISTINCT asset_id FROM transactions {tx_filter}
    """
    all_assets_params = [*latest_params]
    if start_date:
        all_assets_params.append(start_date)
    all_assets = db.execute(all_assets_query, all_assets_params or None).fetchall()
    total_realized_pl = 0.0
    for row in all_assets:
        aid = row[0]
        if aid:
            if exclude_non_balanceable and aid in excluded_asset_ids:
                continue
            realized_amount, realized_currency = calculate_realized_pnl(db, aid, start_date=start_date)
            total_realized_pl += float(realized_amount or 0.0) * (
                today_fx if realized_currency == "USD" else 1.0
            )
    total_lifetime_pl = total_unrealized_pl + total_realized_pl
    if start_date:
        dr = db.execute(
            "SELECT MAX(snapshot_date) FROM holdings WHERE is_shadow=FALSE AND snapshot_date >= ?",
            (start_date,),
        ).fetchone()
    else:
        dr = db.execute("SELECT MAX(snapshot_date) FROM holdings WHERE is_shadow=FALSE").fetchone()
    return {
        "net_worth": net_worth,
        "total_cost_basis": total_cost_basis,
        "total_unrealized_pl": total_unrealized_pl,
        "unrealized_pl_pct": round(unrealized_pl_pct, 2),
        "total_realized_pl": total_realized_pl,
        "total_lifetime_pl": total_lifetime_pl,
        "asset_count": len(active_assets),
        "snapshot_date": str(dr[0]) if dr and dr[0] else None,
    }


# Hand-checked anti-vacuity anchors (see fixture; FX = 7.1, period = all-time).
#   cost = VOO 80000×7.1 + CN_FUND 1.2×40000 + cash 30000 = 646000
#   unrealized = measurable(142000+50000+30000) − cost = 222000 − 646000 = −424000
#   realized = CN_FUND (1.35−1.2)×10000 + SOLD (60−50)×100×7.1 = 1500 + 7100 = 8600
GOLDEN_ALL_TIME = {
    "net_worth": 522108.77,
    "total_cost_basis": 646000.0,
    "total_unrealized_pl": -424000.0,
    "unrealized_pl_pct": -65.63,
    "total_realized_pl": 8600.0,
    "total_lifetime_pl": -415400.0,
    "asset_count": 5,
    "snapshot_date": "2026-07-01",
}
# exclude_non_balanceable drops Property_HOME (¥100,000, Real Estate) from
# net_worth + count; its realized is 0 so gains are unchanged.
GOLDEN_EXCLUDE = {**GOLDEN_ALL_TIME, "net_worth": 422108.77, "asset_count": 4}


@pytest.fixture
def frozen_fx(monkeypatch):
    """Pin USD→CNY to a constant on every module that binds the name."""
    import src.services.pnl.engine as engine_mod

    monkeypatch.setattr(engine_mod, "get_today_usd_cny_rate", lambda: FIXED_FX)
    monkeypatch.setattr(perf, "get_today_usd_cny_rate", lambda: FIXED_FX)


def _summary(db, *, exclude):
    return asyncio.new_event_loop().run_until_complete(
        perf.get_performance_summary(
            period=perf.PERIOD_ALL_TIME,
            exclude_non_balanceable=exclude,
            include_non_rebalanceable=not exclude,
            db=db,
        )
    )


@pytest.mark.parametrize("exclude", [False, True])
def test_engine_summary_equals_frozen_legacy(tmp_path, frozen_fx, exclude):
    """The engine-backed endpoint == the frozen V7.8.3 loop, field-by-field."""
    db_path = tmp_path / "parity.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        legacy = _legacy_summary(db, start_date=None, exclude_non_balanceable=exclude)
        new = _summary(db, exclude=exclude)
    finally:
        db.close()
    assert new == legacy


@pytest.mark.parametrize(
    "exclude,golden", [(False, GOLDEN_ALL_TIME), (True, GOLDEN_EXCLUDE)]
)
def test_engine_summary_matches_golden_constants(tmp_path, frozen_fx, exclude, golden):
    """Anti-vacuity: hand-checked constants pin the numbers absolutely."""
    db_path = tmp_path / "golden.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        new = _summary(db, exclude=exclude)
    finally:
        db.close()
    for key, expected in golden.items():
        assert new[key] == pytest.approx(expected), f"{key}: {new[key]} != {expected}"


def test_net_worth_unchanged_to_the_cent(tmp_path, frozen_fx):
    """The hard invariant: the migration must not move net worth by a cent."""
    db_path = tmp_path / "networth.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        legacy = _legacy_summary(db)
        new = _summary(db, exclude=False)
    finally:
        db.close()
    assert new["net_worth"] == legacy["net_worth"] == 522108.77


def test_engine_treatment_classification_mutation_guard(tmp_path, frozen_fx):
    """Pin the per-asset treatments so breaking cash/balance-only turns red.

    A regression that (say) charged the balance-only bond in at cost=value, or
    stopped treating the cash deposit as cash-equivalent, would change these
    per-asset fields even if some aggregate coincidentally still balanced.
    """
    db_path = tmp_path / "treatment.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        portfolio = compute_portfolio_pnl(db, scope=Scope(start_date=None))
    finally:
        db.close()
    by_id = {a.asset_id: a for a in portfolio.assets}

    bond = by_id["Bond_CMB_CNY"]
    assert bond.treatment is Treatment.balance_only
    assert bond.cost_basis_cny is None and bond.unrealized_cny is None
    assert bond.market_value_cny == pytest.approx(200108.77)  # still real money

    cash = by_id["CASH_Deposit_TEST_CNY"]
    assert cash.treatment is Treatment.cash
    assert cash.cost_basis_cny == pytest.approx(30000.0)  # cost == value
    assert cash.unrealized_cny == pytest.approx(0.0)      # a real zero gain

    voo = by_id["US_STK_VOO"]
    assert voo.treatment is Treatment.traded
    assert voo.cost_basis_cny == pytest.approx(80000.0 * FIXED_FX)

    sold = by_id["US_STK_SOLD"]
    assert sold.is_current is False  # closed / transaction-only
    assert sold.realized_cny == pytest.approx(1000.0 * FIXED_FX)


def test_manual_override_seam_is_empty_on_a_pre_v86_db():
    """Release 2 (#7) makes the seam live; this pins its *empty* contract.

    Supersedes Release 1's `test_manual_override_seam_is_dormant`, which asserted
    `_load_manual_overrides(db=None) == {}` — a hardcoded return, unreachable now
    that the seam reads a table. A DB predating the V86 migration has no
    `manual_asset_pnl`, and must still yield {} rather than raise.

    The populated-table behaviour and the full overlay precedence live in
    tests/services/pnl/test_manual_pnl_overlay.py.
    """
    import duckdb

    from src.services.pnl.manual import load_manual_overrides

    pre_v86 = duckdb.connect(":memory:")   # no manual_asset_pnl table
    try:
        assert load_manual_overrides(pre_v86) == {}
    finally:
        pre_v86.close()
