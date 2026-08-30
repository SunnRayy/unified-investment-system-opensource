"""Balance-only holdings must never report their whole value as profit.

A Financial-Summary column reports what an account is *worth*, not what was paid
for it: the melted row carries quantity=1, price=value, no cost basis, and no
transactions.  Reading that missing cost as zero turns the entire balance into
unrealized gain — which is exactly how ``Bond_CMB_CNY`` (+~¥200K) and
``Bond_CMB_USD`` (+~¥190K) came to show a 100% lifetime gain on WealthOS,
and inflated the "Total Lifetime Gain" KPI from ~¥54K to ~¥445K.

The pre-existing guard keyed on the asset-class *string* ("Cash", "Money Market",
"Bank Wealth"), so it protected a balance column only for as long as somebody
spelled its class like cash.  These two were classified "CN Bonds" / "US Bonds".
"""
import asyncio

import duckdb
import pytest

from src.services.currency import is_balance_only_holding


# ── The predicate itself ────────────────────────────────────────────────────


def test_no_cost_and_no_transactions_is_a_balance():
    assert is_balance_only_holding(cost_price_unit=None, has_transactions=False)
    assert is_balance_only_holding(cost_price_unit=0.0, has_transactions=False)


def test_a_purchase_history_means_it_is_a_position():
    """Money-market funds have a NULL cost basis but they *are* traded.

    They stay on the cash-equivalent path; this predicate must not claim them,
    otherwise the two rules would collide instead of forming a union.
    """
    assert not is_balance_only_holding(cost_price_unit=None, has_transactions=True)
    assert not is_balance_only_holding(cost_price_unit=0.0, has_transactions=True)


def test_a_known_cost_means_it_is_a_position():
    assert not is_balance_only_holding(cost_price_unit=12.5, has_transactions=False)
    assert not is_balance_only_holding(cost_price_unit=12.5, has_transactions=True)


# ── End-to-end through the WealthOS endpoint ────────────────────────────────


def _seed(db_path):
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE holdings (
            asset_id VARCHAR, asset_name VARCHAR, source_system VARCHAR,
            market_value DOUBLE, cost_price_unit DOUBLE, market_price_unit DOUBLE,
            quantity DOUBLE, currency VARCHAR, snapshot_date DATE, is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            asset_id VARCHAR, asset_name VARCHAR, transaction_type VARCHAR,
            quantity DOUBLE, price_unit DOUBLE, amount_net DOUBLE,
            currency VARCHAR, transaction_date DATE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE asset_registry (
            canonical_id VARCHAR, display_name VARCHAR, asset_class VARCHAR,
            is_rebalanceable BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE taxonomy_classes (
            id INTEGER, name VARCHAR, name_cn VARCHAR, parent_id INTEGER,
            is_rebalanceable BOOLEAN
        )
        """
    )
    conn.execute(
        """
        INSERT INTO taxonomy_classes VALUES
        (1, 'Bonds', '债券', NULL, TRUE),
        (2, 'CN Bonds', '中国债券', 1, TRUE),
        (3, 'Equity', '股票', NULL, TRUE),
        (4, 'US Equity', '美股', 3, TRUE),
        (5, 'Cash', '现金', NULL, TRUE),
        (6, 'Cash Checking', '活期', 5, TRUE)
        """
    )
    conn.execute(
        """
        INSERT INTO asset_registry VALUES
        ('Bond_CMB_CNY', '招行固收债券', 'CN Bonds', TRUE),
        ('US_STK_VOO',   'Vanguard S&P 500', 'US Equity', TRUE),
        ('CASH_Deposit_TEST_CNY', '测试活期', 'Cash Checking', TRUE)
        """
    )
    # The balance: Financial-Summary melt shape — unit quantity, price == value,
    # no cost basis, and deliberately NO transaction rows. The bond and the cash
    # deposit are structurally identical (no cost, no txn); only their class differs.
    conn.execute(
        """
        INSERT INTO holdings VALUES
        ('Bond_CMB_CNY', '招行固收债券', 'Financial_Summary_Excel',
         150000.00, NULL, 150000.00, 1.0, 'CNY', DATE '2026-07-01', FALSE),
        ('US_STK_VOO', 'Vanguard S&P 500', 'Schwab_CSV',
         97257.66, 400.0, 486.29, 200.0, 'CNY', DATE '2026-07-01', FALSE),
        ('CASH_Deposit_TEST_CNY', '测试活期', 'Financial_Summary_Excel',
         30000.0, NULL, 30000.0, 1.0, 'CNY', DATE '2026-07-01', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO transactions VALUES
        ('US_STK_VOO', 'Vanguard S&P 500', 'buy', 200.0, 400.0, 80000.0,
         'CNY', DATE '2025-01-15')
        """
    )
    conn.close()


def _wealthos_rows(db_path):
    from src.database.connector import DatabaseConnector
    from src.api.routes.data import get_wealthos_assets

    db = DatabaseConnector(str(db_path))
    try:
        res = asyncio.new_event_loop().run_until_complete(
            get_wealthos_assets(db=db)
        )
    finally:
        db.close()
    rows = res.get("assets", []) + res.get("non_rebalanceable_assets", [])
    return {r["code"]: r for r in rows}


def test_balance_only_asset_reports_unknown_not_gain(tmp_path):
    """The regression: this asset used to show pl == market_value (100% profit).

    The honest state is that its cost is UNKNOWN — the Financial-Summary source
    records a balance, never a purchase price. So invested / pl / ret must be
    null (rendered "—"), NOT a fabricated ¥0 gain and NOT cost == value.
    """
    db_path = tmp_path / "balance_only.duckdb"
    _seed(db_path)

    rows = _wealthos_rows(db_path)
    bond = rows["Bond_CMB_CNY"]

    assert bond["pl"] is None, "cost is unknown, so P&L is unknown — not zero"
    assert bond["ret"] is None
    assert bond["invested"] is None, "must not fabricate Total Invested = current value"
    assert bond["pl_native"] is None
    assert bond["unrealized_current_lots_pct"] is None
    # The value itself is real and still shown.
    assert bond["cur"] == pytest.approx(150000.00)


def test_traded_position_keeps_its_profit(tmp_path):
    """Anti-vacuity: the fix must not simply zero everybody's P&L."""
    db_path = tmp_path / "balance_only_control.duckdb"
    _seed(db_path)

    rows = _wealthos_rows(db_path)
    voo = rows["US_STK_VOO"]

    assert voo["invested"] == pytest.approx(80000.0)
    assert voo["pl"] == pytest.approx(17257.66)
    assert voo["ret"] > 0


def test_cash_deposit_is_zero_gain_not_unknown(tmp_path):
    """Cash precedence: a cash balance is also cost-less + txn-less, but its
    principal IS its balance — so it reports a genuine ¥0 gain (pl=0,
    invested=value), NOT "—". Getting this order wrong would wrongly blank out
    every cash deposit.
    """
    db_path = tmp_path / "balance_only_cash.duckdb"
    _seed(db_path)

    rows = _wealthos_rows(db_path)
    cash = rows["CASH_Deposit_TEST_CNY"]

    assert cash["pl"] == 0.0, "cash is its own principal — a real zero gain, not unknown"
    assert cash["ret"] == 0.0
    assert cash["invested"] == pytest.approx(30000.0)


def _perf_summary(db_path):
    import asyncio

    from src.database.connector import DatabaseConnector
    from src.api.routes.performance import get_performance_summary, PERIOD_ALL_TIME

    db = DatabaseConnector(str(db_path))
    try:
        return asyncio.new_event_loop().run_until_complete(
            get_performance_summary(
                period=PERIOD_ALL_TIME,
                exclude_non_balanceable=False,
                include_non_rebalanceable=True,
                db=db,
            )
        )
    finally:
        db.close()


def _perf_call(db_path, coro_fn):
    import asyncio

    from src.database.connector import DatabaseConnector
    from src.api.routes.performance import PERIOD_ALL_TIME

    db = DatabaseConnector(str(db_path))
    try:
        return asyncio.new_event_loop().run_until_complete(
            coro_fn(
                period=PERIOD_ALL_TIME,
                exclude_non_balanceable=False,
                include_non_rebalanceable=True,
                db=db,
            )
        )
    finally:
        db.close()


def test_gains_ranking_excludes_balance_only(tmp_path):
    """The Performance 'Top/Bottom Performers' bug: the bonds ranked #1/#2 on a
    fabricated 100% gain. A balance-only asset has no measurable return and must
    not appear in the ranking at all — but its value still counts in the total.
    """
    from src.api.routes.performance import get_gains_analysis

    db_path = tmp_path / "gains.duckdb"
    _seed(db_path)
    g = _perf_call(db_path, get_gains_analysis)

    codes = {a["asset_id"] for a in g["assets"]}
    assert "Bond_CMB_CNY" not in codes, "a balance-only asset cannot be a performer"
    assert "US_STK_VOO" in codes
    # Market value still counted in the portfolio total.
    assert g["total_market_value"] == pytest.approx(277257.66, abs=0.01)


def test_by_class_keeps_value_drops_phantom(tmp_path):
    """The Performance 'by class' bug: Fixed Income unrealized carried the bond's
    whole balance as class profit. The class VALUE must still include the bond,
    but its UNREALIZED must not.
    """
    from src.api.routes.performance import get_performance_by_class

    db_path = tmp_path / "byclass.duckdb"
    _seed(db_path)
    d = _perf_call(db_path, get_performance_by_class)

    bonds = next((c for c in d["top_classes"] if c["class_name"] in ("Bonds", "CN Bonds")), None)
    assert bonds is not None, "the bond's class should appear"
    # Value includes the balance ...
    assert bonds["market_value"] == pytest.approx(150000.00, abs=0.01)
    # ... but the phantom is gone from unrealized (no cost, so no measurable gain).
    assert bonds["unrealized_pl"] == pytest.approx(0.0, abs=0.01)


def test_balance_only_counts_in_net_worth_but_not_in_gain(tmp_path):
    """The bond's balance is real money — it must stay in net worth — but with
    no cost it must contribute nothing to the lifetime-gain figure (neither
    inflating it by 100% nor diluting the % denominator with a non-cost).
    """
    db_path = tmp_path / "balance_only_agg.duckdb"
    _seed(db_path)

    summary = _perf_summary(db_path)

    # Net worth = every market value: 150,000.00 + 97,257.66 + 30,000 = 277,257.66
    assert summary["net_worth"] == pytest.approx(277257.66, abs=0.01)
    # Cost basis excludes the bond (unknown) but includes cash at value (30,000)
    # and VOO at cost (80,000) = 110,000.
    assert summary["total_cost_basis"] == pytest.approx(110000.0, abs=0.01)
    # The bond's value is NOT in the gain: unrealized = measurable_value − cost =
    # (97,257.66 + 30,000) − 110,000 = 17,257.66. The bond's balance is absent.
    assert summary["total_unrealized_pl"] == pytest.approx(17257.66, abs=0.01)
