# tests/e2e/test_financial_integrity.py
"""
Golden assertion tests for financial data integrity.

These tests assert on FINANCIAL OUTCOMES (net worth, TWR range, shadow correctness)
not just mechanics (column names, SQL syntax). Each test case prevents recurrence of
a specific historical bug.

Uses in-memory DuckDB seeded with a frozen test dataset covering:
- Mixed sources (Schwab + CN Fund + Gold + Insurance)
- Sold assets (last snapshot then no holdings)
- RSU with vest-price cost basis
- QDII with T+2 date lag
- Cash holdings (should have zero P&L)

The fixture creates ~60 holdings rows + ~25 transactions.
"""

import pytest

pytestmark = pytest.mark.critical

from datetime import date
from src.database.connector import DatabaseConnector
from src.validation.data_integrity_gate import run_integrity_checks


# ─────────────────────────────────────────────────────────────────────────────
# Test fixture: in-memory DuckDB with controlled seed data
# ─────────────────────────────────────────────────────────────────────────────




def _seed_normal_portfolio(db: DatabaseConnector):
    """
    Seed a realistic 4-source portfolio at two snapshot dates.
    Net worth ≈ 5.4M CNY. All values in CNY. No currency mixing.

    All quantities/prices/dollar amounts below are invented round numbers for
    exercising integrity-check LOGIC, not anyone's real financial data. The
    three CN_FUND_ asset_ids (000198/110020/519674) are public fund codes
    reused from tools/demo_data/persona.yaml's cn_funds.catalog (OSR WS-1.7 —
    the codes previously here, 900002/001810/900015, matched real holdings
    of the project owner even though the surrounding amounts never did).
    """
    today = date(2026, 3, 6)
    yesterday = date(2026, 3, 5)
    six_months_ago = date(2025, 9, 6)  # Full prior snapshot for TWR check (6 months = avoid annualization amplification)

    holdings = [
        # (id, asset_id, asset_name, source_system, snapshot_date, qty, price, mv, cost, currency, is_shadow)
        # Note: unrealized_pnl is NOT a column in production schema — computed inline as (mv - cost*qty)

        # Prior full snapshot (6 months ago) — all main assets at slightly lower values
        (20, "US_STK_AAPL", "Apple Inc", "Schwab_CSV", six_months_ago, 10, 130_000, 1_300_000, 100_000, "CNY", False),
        (21, "US_STK_MSFT", "Microsoft", "Schwab_CSV", six_months_ago, 5, 265_000, 1_325_000, 210_000, "CNY", False),
        (22, "CN_FUND_000198", "天弘余额宝货币A", "CN_Fund_Excel", six_months_ago, 50_000, 2.0, 100_000, 1.8, "CNY", False),
        (23, "ALTS_Paper_Gold", "Paper Gold", "Gold_Excel", six_months_ago, 500, 500, 250_000, 400, "CNY", False),
        (24, "INS_LIC_001", "Life Insurance", "Insurance_Excel", six_months_ago, 1, 790_000, 790_000, 600_000, "CNY", False),
        (25, "RSU_AMZN", "Amazon RSU", "RSU_Excel", six_months_ago, 20, 120_000, 2_400_000, 100_000, "CNY", False),
        (26, "CASH_CNY", "CNY Cash", "CN_Fund_Excel", six_months_ago, 50_000, 1, 50_000, 1, "CNY", False),

        # Schwab US stocks — values in CNY (USD * 7.0)
        (1,  "US_STK_AAPL", "Apple Inc", "Schwab_CSV", today, 10, 140_000, 1_400_000, 100_000, "CNY", False),
        (2,  "US_STK_MSFT", "Microsoft", "Schwab_CSV", today, 5, 280_000, 1_400_000, 210_000, "CNY", False),

        # CN mutual funds — cost_price_unit is PER UNIT (not total cost)
        (3,  "CN_FUND_000198", "天弘余额宝货币A", "CN_Fund_Excel", today, 50_000, 2.1, 105_000, 1.8, "CNY", False),
        (4,  "CN_FUND_110020", "易方达沪深300ETF联接A", "CN_Fund_Excel", today, 30_000, 3.5, 105_000, 3.17, "CNY", False),

        # QDII fund — 2 days older snapshot (T+2 lag)
        (5,  "CN_FUND_519674", "银河创新成长混合A", "CN_Fund_Excel", yesterday, 20_000, 4.8, 96_000, 4.0, "CNY", False),

        # Gold
        (6,  "ALTS_Paper_Gold", "Paper Gold", "Gold_Excel", today, 500, 520, 260_000, 400, "CNY", False),

        # Insurance
        (7,  "INS_LIC_001", "Life Insurance A", "Insurance_Excel", today, 1, 800_000, 800_000, 600_000, "CNY", False),

        # RSU — vest price cost basis (¥100,000 per share vest price)
        (8,  "RSU_AMZN", "Amazon RSU", "RSU_Excel", today, 20, 126_000, 2_520_000, 100_000, "CNY", False),

        # Cash — cost_price_unit = 1 (face value), should have zero P&L
        (9,  "CASH_CNY", "CNY Cash", "CN_Fund_Excel", today, 50_000, 1, 50_000, 1, "CNY", False),
        (10, "CASH_USD", "USD Cash", "Schwab_CSV", today, 5_000, 7, 35_000, 7, "CNY", False),

        # PIS shadow rows for assets covered by readers (is_shadow=TRUE)
        (11, "US_STK_AAPL", "Apple Inc (PIS)", "PIS", today, 10, 135_000, 1_350_000, 100_000, "CNY", True),
        (12, "CN_FUND_000198", "天弘余额宝货币A (PIS)", "PIS", today, 50_000, 2.0, 100_000, 90_000, "CNY", True),
    ]

    db.executemany("""
        INSERT INTO holdings (id, asset_id, asset_name, source_system, snapshot_date,
            quantity, market_price_unit, market_value, cost_price_unit,
            currency, is_shadow)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, holdings)

    # Transactions for XIRR proxy check (BUY transactions)
    transactions = [
        (1, "US_STK_AAPL", "Schwab_CSV", date(2023, 1, 15), "BUY", 10, 100_000, 1_000_000, "CNY"),
        (2, "US_STK_MSFT", "Schwab_CSV", date(2023, 3, 10), "BUY", 5, 210_000, 1_050_000, "CNY"),
        (3, "CN_FUND_000198", "CN_Fund_Excel", date(2022, 6, 1), "BUY", 50_000, 1.8, 90_000, "CNY"),
        (4, "CN_FUND_110020", "CN_Fund_Excel", date(2022, 8, 15), "BUY", 30_000, 3.17, 95_000, "CNY"),
        (5, "ALTS_Paper_Gold", "Gold_Excel", date(2021, 11, 1), "BUY", 500, 400, 200_000, "CNY"),
        (6, "INS_LIC_001", "Insurance_Excel", date(2020, 3, 1), "DEPOSIT", 1, 600_000, 600_000, "CNY"),
        (7, "RSU_AMZN", "RSU_Excel", date(2023, 7, 15), "VEST", 20, 100_000, 2_000_000, "CNY"),
    ]

    db.executemany("""
        INSERT INTO transactions (id, asset_id, source_system, transaction_date,
            transaction_type, quantity, price_unit, amount_net, currency)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, transactions)

    # Add historical snapshot to balance_sheet_monthly for TWR calculations
    # Total of the 6 months ago holdings = 1300000 + 1325000 + 100000 + 250000 + 790000 + 2400000 + 50000 = 6215000
    db.execute("""
        INSERT INTO balance_sheet_monthly (id, snapshot_date, payload)
        VALUES (1, ?, '{"合计总资产": 6215000.0}')
    """, [six_months_ago])


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Net worth plausibility
# ─────────────────────────────────────────────────────────────────────────────

def test_net_worth_is_plausible(clean_db):
    """
    Net worth is computed as sum of non-shadow holdings at per-asset latest date.

    Historical bug: global MAX(snapshot_date) excluded QDII assets with T+2 lag,
    dropping net worth from ¥5.37M to ¥303K.
    """
    _seed_normal_portfolio(clean_db)
    report = run_integrity_checks(clean_db)

    nw_check = next(c for c in report.checks if c.name == "net_worth_plausible")
    assert nw_check.passed, f"Net worth check failed: {nw_check.details}"


def test_net_worth_uses_per_asset_max_date(clean_db):
    """
    The QDII fund with T+2 lag (snapshot_date = yesterday) must be INCLUDED
    in net worth — not excluded by a global MAX(snapshot_date) filter.

    If global MAX were used, CN_FUND_519674 (¥96,000) would be excluded.
    """
    _seed_normal_portfolio(clean_db)

    # Compute net worth as the integrity gate does (per-asset MAX)
    row = clean_db.execute("""
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS max_date
            FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
        )
        SELECT SUM(h.market_value)
        FROM holdings h
        JOIN latest_per_asset l ON h.asset_id=l.asset_id AND h.snapshot_date=l.max_date
        WHERE h.is_shadow=FALSE AND h.market_value > 0
    """).fetchone()
    per_asset_nw = float(row[0])

    # Compute with global MAX (wrong approach)
    row2 = clean_db.execute("""
        SELECT SUM(market_value)
        FROM holdings
        WHERE is_shadow=FALSE
          AND snapshot_date = (SELECT MAX(snapshot_date) FROM holdings WHERE is_shadow=FALSE)
          AND market_value > 0
    """).fetchone()
    global_max_nw = float(row2[0])

    # Per-asset must include the QDII fund; global MAX will miss it
    assert per_asset_nw > global_max_nw, (
        f"Per-asset NW ({per_asset_nw:,.0f}) should be higher than "
        f"global-MAX NW ({global_max_nw:,.0f}) due to QDII T+2 lag"
    )

    # QDII value should be included in per-asset
    assert per_asset_nw >= global_max_nw + 96_000 - 1, \
        "QDII fund (¥96,000) should be included when using per-asset MAX date"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Currency consistency
# ─────────────────────────────────────────────────────────────────────────────

def test_no_currency_mixing_on_normal_data(clean_db):
    """Schwab holdings with proper CNY values should pass the currency check."""
    _seed_normal_portfolio(clean_db)
    report = run_integrity_checks(clean_db)

    currency_check = next(c for c in report.checks if c.name == "no_raw_usd_in_schwab_holdings")
    assert currency_check.passed, f"Currency check failed on clean data: {currency_check.details}"


def test_detects_raw_usd_schwab_holding(clean_db):
    """
    A Schwab holding stored in raw USD (market_value ~ $33K) instead of CNY (~¥231K)
    should be flagged by the currency check.

    Historical bug: Schwab transformer outputted raw USD, making US positions
    appear 7x smaller than actual.
    """
    _seed_normal_portfolio(clean_db)

    # Insert a "bad" row: raw USD value for a multi-share position
    clean_db.execute("""
        INSERT INTO holdings (id, asset_id, asset_name, source_system, snapshot_date,
            quantity, market_price_unit, market_value, cost_price_unit,
            currency, is_shadow)
        VALUES (99, 'US_STK_NVDA', 'Nvidia', 'Schwab_CSV', '2026-03-06',
                5, 80, 400, 60, 'CNY', FALSE)
    """)

    report = run_integrity_checks(clean_db)
    currency_check = next(c for c in report.checks if c.name == "no_raw_usd_in_schwab_holdings")
    assert not currency_check.passed, \
        "Should detect Schwab holding with market_value < 50,000 and quantity > 1 (likely raw USD)"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Shadow mutual exclusion
# ─────────────────────────────────────────────────────────────────────────────

def test_shadow_mutual_exclusion_on_normal_data(clean_db):
    """
    The normal dataset has PIS shadow rows coexisting with reader active rows —
    but they have DIFFERENT source_systems so they don't conflict at asset-level.

    The check is: same asset_id + same snapshot_date can't have both is_shadow=TRUE
    and is_shadow=FALSE (regardless of source_system).
    """
    _seed_normal_portfolio(clean_db)
    report = run_integrity_checks(clean_db)

    shadow_check = next(c for c in report.checks if c.name == "shadow_mutual_exclusion")
    # Normal data has PIS shadow + reader active for same asset at same date
    # This is actually the expected pattern — both rows exist but PIS is marked shadow
    # The check should PASS because this is the correct pattern
    assert shadow_check.passed, \
        f"Shadow check should pass on correctly-seeded data: {shadow_check.details}"


def test_detects_shadow_conflict(clean_db):
    """
    A reader source row with is_shadow=TRUE indicates a bug (shadow direction reversed).

    Historical bug: Gold/Insurance rows were imported then all marked is_shadow=TRUE
    by incorrect shadow logic direction, making reader data invisible.
    """
    _seed_normal_portfolio(clean_db)

    # Create a bad row: Gold_Excel row with is_shadow=TRUE (reader shadowed by PIS)
    clean_db.execute("""
        INSERT INTO holdings (id, asset_id, asset_name, source_system, snapshot_date,
            quantity, market_price_unit, market_value, cost_price_unit,
            currency, is_shadow)
        VALUES (98, 'ALTS_XAU', 'Extra Gold', 'Gold_Excel', '2026-03-06',
                100, 520, 52000, 400, 'CNY', TRUE)
    """)

    report = run_integrity_checks(clean_db)
    shadow_check = next(c for c in report.checks if c.name == "shadow_mutual_exclusion")
    assert not shadow_check.passed, \
        "Should detect Gold_Excel row with is_shadow=TRUE (reader shadowed by PIS — wrong direction)"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Reader rows not all shadowed
# ─────────────────────────────────────────────────────────────────────────────

def test_reader_rows_not_all_shadowed(clean_db):
    """Reader sources should have active (non-shadow) rows in normal data."""
    _seed_normal_portfolio(clean_db)
    report = run_integrity_checks(clean_db)

    reader_check = next(c for c in report.checks if c.name == "reader_rows_not_all_shadowed")
    assert reader_check.passed, f"Reader check failed: {reader_check.details}"


def test_detects_all_reader_rows_shadowed(clean_db):
    """
    If all reader rows are marked is_shadow=TRUE, the check should fail.

    Historical bug: Gold and Insurance were imported but then all marked shadow=TRUE
    by incorrect shadow logic direction.
    """
    # Only insert reader rows that are ALL shadowed
    clean_db.execute("""
        INSERT INTO holdings (id, asset_id, asset_name, source_system, snapshot_date,
            quantity, market_price_unit, market_value, cost_price_unit,
            currency, is_shadow)
        VALUES
            (1, 'ALTS_Paper_Gold', 'Gold', 'Gold_Excel', '2026-03-06', 500, 520, 260000, 200000, 'CNY', TRUE),
            (2, 'INS_LIC_001', 'Insurance', 'Insurance_Excel', '2026-03-06', 1, 800000, 800000, 600000, 'CNY', TRUE)
    """)

    report = run_integrity_checks(clean_db)
    reader_check = next(c for c in report.checks if c.name == "reader_rows_not_all_shadowed")
    assert not reader_check.passed, \
        "Should detect that all reader rows are shadowed (reader data invisible)"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Cash P&L is zero
# ─────────────────────────────────────────────────────────────────────────────

def test_cash_pnl_is_zero_on_normal_data(clean_db):
    """CASH assets should have zero unrealized P&L in normal data."""
    _seed_normal_portfolio(clean_db)
    report = run_integrity_checks(clean_db)

    cash_check = next(c for c in report.checks if c.name == "cash_pnl_is_zero")
    assert cash_check.passed, f"Cash P&L check failed: {cash_check.details}"


def test_detects_nonzero_cash_pnl(clean_db):
    """
    A cash holding with non-zero unrealized P&L should be flagged.

    Historical bug: Cash P&L was ¥-100,000 due to incorrect cost basis assignment.
    """
    clean_db.execute("""
        INSERT INTO holdings (id, asset_id, asset_name, source_system, snapshot_date,
            quantity, market_price_unit, market_value, cost_price_unit,
            currency, is_shadow)
        VALUES (1, 'CASH_CNY', 'CNY Cash', 'CN_Fund_Excel', '2026-03-06',
                100000, 1, 100000, 2.0, 'CNY', FALSE)
    """)

    report = run_integrity_checks(clean_db)
    cash_check = next(c for c in report.checks if c.name == "cash_pnl_is_zero")
    assert not cash_check.passed, \
        "Should detect CASH_CNY with unrealized_pnl = -50,000 (>1% of market_value)"


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Active holdings have positive value
# ─────────────────────────────────────────────────────────────────────────────

def test_active_holdings_have_positive_value(clean_db):
    """All active holdings should have market_value > 0 in normal data."""
    _seed_normal_portfolio(clean_db)
    report = run_integrity_checks(clean_db)

    value_check = next(c for c in report.checks if c.name == "active_holdings_have_positive_value")
    assert value_check.passed, f"Active value check failed: {value_check.details}"


def test_detects_zero_value_active_holding(clean_db):
    """
    An active holding with market_value = NULL/0 should be flagged.

    Historical bug: Insurance transformer output None for market_value,
    making insurance positions invisible in net worth.
    """
    clean_db.execute("""
        INSERT INTO holdings (id, asset_id, asset_name, source_system, snapshot_date,
            quantity, market_price_unit, market_value, cost_price_unit,
            currency, is_shadow)
        VALUES (1, 'INS_LIC_001', 'Insurance A', 'Insurance_Excel', '2026-03-06',
                1, NULL, NULL, 600000, 'CNY', FALSE)
    """)

    report = run_integrity_checks(clean_db)
    value_check = next(c for c in report.checks if c.name == "active_holdings_have_positive_value")
    assert not value_check.passed, \
        "Should detect active insurance holding with NULL market_value"


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Cost basis ratio
# ─────────────────────────────────────────────────────────────────────────────

def test_cost_basis_ratio_on_normal_data(clean_db):
    """Cost basis should be within 10x of market value in normal data."""
    _seed_normal_portfolio(clean_db)
    report = run_integrity_checks(clean_db)

    cost_check = next(c for c in report.checks if c.name == "cost_basis_ratio_under_10x")
    assert cost_check.passed, f"Cost basis ratio check failed: {cost_check.details}"


def test_detects_inflated_cost_basis(clean_db):
    """
    A holding where cost_price_unit * quantity >> market_value should be flagged.

    Historical bug: PIS Excel exported Cost_Price_Unit as total cost (not per-unit).
    E.g. cost_price_unit = 3,000,000 for a 10-share position worth ¥1,400,000
    means cost = 30,000,000 vs market = 1,400,000 (ratio = 21x).
    """
    clean_db.execute("""
        INSERT INTO holdings (id, asset_id, asset_name, source_system, snapshot_date,
            quantity, market_price_unit, market_value, cost_price_unit,
            currency, is_shadow)
        VALUES (1, 'US_STK_AAPL', 'Apple', 'Schwab_CSV', '2026-03-06',
                10, 140000, 1400000, 3000000, 'CNY', FALSE)
    """)

    report = run_integrity_checks(clean_db)
    cost_check = next(c for c in report.checks if c.name == "cost_basis_ratio_under_10x")
    assert not cost_check.passed, \
        "Should detect cost_price_unit * quantity > 10x market_value (total-cost-as-unit-cost bug)"


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: TWR and XIRR range checks
# ─────────────────────────────────────────────────────────────────────────────

def test_twr_in_range_on_normal_data(clean_db):
    """TWR should be in range for normal data spanning the check's fixed
    365-day lookback window.

    `_seed_normal_portfolio`'s oldest snapshot (`six_months_ago`, 2025-09-06)
    is only ~181 days before `today` (2026-03-06) — inside the check's
    d_start (2025-03-06), so on its own it would trigger the *legitimate*
    skip path (no valuation data at/before d_start), not the happy path this
    test is meant to exercise. Add an explicit anchor snapshot, covering the
    SAME assets `today` holds (not just one), dated before d_start — a
    single-asset anchor would understate v_start relative to v_end's full
    coverage and manufacture a spurious extreme "return", which is exactly
    the coverage-asymmetry trap this check must not fall into.
    """
    _seed_normal_portfolio(clean_db)
    one_year_before_holdings = [
        # (id, asset_id, asset_name, source_system, snapshot_date, qty, price, mv, cost, currency, is_shadow)
        (900, "US_STK_AAPL", "Apple Inc", "Schwab_CSV", date(2025, 1, 1), 10, 119_000, 1_190_000, 100_000, "CNY", False),
        (901, "US_STK_MSFT", "Microsoft", "Schwab_CSV", date(2025, 1, 1), 5, 238_000, 1_190_000, 210_000, "CNY", False),
        (902, "CN_FUND_000198", "天弘余额宝货币A", "CN_Fund_Excel", date(2025, 1, 1), 50_000, 1.78, 89_000, 1.8, "CNY", False),
        (903, "CN_FUND_110020", "易方达沪深300ETF联接A", "CN_Fund_Excel", date(2025, 1, 1), 30_000, 2.97, 89_000, 3.17, "CNY", False),
        (904, "CN_FUND_519674", "银河创新成长混合A", "CN_Fund_Excel", date(2025, 1, 1), 20_000, 4.1, 82_000, 4.0, "CNY", False),
        (905, "ALTS_Paper_Gold", "Paper Gold", "Gold_Excel", date(2025, 1, 1), 500, 442, 221_000, 400, "CNY", False),
        (906, "INS_LIC_001", "Life Insurance A", "Insurance_Excel", date(2025, 1, 1), 1, 680_000, 680_000, 600_000, "CNY", False),
        (907, "RSU_AMZN", "Amazon RSU", "RSU_Excel", date(2025, 1, 1), 20, 107_100, 2_142_000, 100_000, "CNY", False),
        (908, "CASH_CNY", "CNY Cash", "CN_Fund_Excel", date(2025, 1, 1), 42_000, 1, 42_000, 1, "CNY", False),
        (909, "CASH_USD", "USD Cash", "Schwab_CSV", date(2025, 1, 1), 30_000, 1, 30_000, 7, "CNY", False),
    ]
    clean_db.executemany("""
        INSERT INTO holdings (id, asset_id, asset_name, source_system, snapshot_date,
            quantity, market_price_unit, market_value, cost_price_unit,
            currency, is_shadow)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, one_year_before_holdings)
    report = run_integrity_checks(clean_db)

    twr_check = next(c for c in report.checks if c.name == "twr_in_range")
    assert not twr_check.skipped, (
        f"Should have evaluated (anchor row precedes d_start): {twr_check.details}"
    )
    assert twr_check.passed, f"TWR check failed: {twr_check.details}"


def test_xirr_proxy_in_range_on_normal_data(clean_db):
    """XIRR proxy should be in range for the normal seeded data."""
    _seed_normal_portfolio(clean_db)
    report = run_integrity_checks(clean_db)

    xirr_check = next(c for c in report.checks if c.name == "xirr_proxy_in_range")
    assert xirr_check.passed, f"XIRR check failed: {xirr_check.details}"


def test_detects_extreme_twr(clean_db):
    """
    TWR > 200% should be flagged.

    Historical bug: +912% TWR caused by transaction double-counting.
    We simulate by creating a tiny start value (before the check's fixed
    365-day lookback start) and a huge end value (the latest snapshot).
    """
    # Anchor row before d_start (2026-03-06 - 365d = 2025-03-06): tiny value.
    # Latest snapshot (d_end): huge value. 11x over exactly 365 days = 1000%.
    clean_db.execute("""
        INSERT INTO holdings (id, asset_id, asset_name, source_system, snapshot_date,
            quantity, market_price_unit, market_value, cost_price_unit,
            currency, is_shadow)
        VALUES
            (1, 'US_STK_AAPL', 'Apple', 'Schwab_CSV', '2025-01-01', 1, 100000, 100000, 100000, 'CNY', FALSE),
            (2, 'US_STK_AAPL', 'Apple', 'Schwab_CSV', '2026-03-06', 1, 1100000, 1100000, 100000, 'CNY', FALSE)
    """)
    clean_db.execute("INSERT INTO transactions (id, asset_id, source_system, transaction_date, transaction_type, quantity, price_unit, amount_net, currency, is_provisional) VALUES (1, 'US_STK_AAPL', 'Schwab_CSV', '2025-01-01', 'buy', 1, 100000, 100000.0, 'CNY', FALSE)")

    report = run_integrity_checks(clean_db)
    twr_check = next(c for c in report.checks if c.name == "twr_in_range")
    assert not twr_check.skipped, f"Should have evaluated: {twr_check.details}"
    assert not twr_check.passed, \
        "Should detect >200% annualized return (11x in 365 days = 1000% annualized)"


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: IntegrityReport interface
# ─────────────────────────────────────────────────────────────────────────────

def test_integrity_report_all_passed_on_normal_data(clean_db):
    """Full integrity report should pass on clean, correctly-seeded data."""
    _seed_normal_portfolio(clean_db)
    report = run_integrity_checks(clean_db)

    failed = report.failed_checks
    assert report.all_passed, (
        "Expected all checks to pass on clean data. Failed: "
        + ", ".join(f"{c.name}: {c.details}" for c in failed)
    )


def test_integrity_report_to_text(clean_db):
    """Report.to_text() should return a formatted string."""
    _seed_normal_portfolio(clean_db)
    report = run_integrity_checks(clean_db)

    text = report.to_text()
    assert "Data Integrity Report" in text
    assert "PASSED" in text or "FAILED" in text
    assert len(text) > 100


def test_cross_endpoint_consistency_on_normal_data(clean_db):
    _seed_normal_portfolio(clean_db)
    report = run_integrity_checks(clean_db)
    check = next(c for c in report.checks if c.name == "net_worth_cross_endpoint_consistency")
    assert check.passed, f"Cross-endpoint check failed on clean data: {check.details}"

def test_detects_cross_endpoint_divergence(clean_db):
    """If taxonomy_classes has duplicate name rows, both performance.py and compass.py
    paths double-count that asset's value, making net worth diverge from the simple path."""
    _seed_normal_portfolio(clean_db)
    # Insert asset_registry rows linking holdings to 'US Equity' asset class
    clean_db.execute("""
        INSERT INTO asset_registry (id, canonical_id, asset_class) VALUES
            (1, 'US_STK_AAPL', 'US Equity'),
            (2, 'US_STK_MSFT', 'US Equity')
    """)
    # Seed taxonomy_classes: Equity parent + TWO 'US Equity' rows (simulates bad data).
    # Both Path 2 (compass) and Path 3 (performance) use taxonomy_classes double-join,
    # so duplicate rows cause double-counting, diverging from Path 1 (simple SUM).
    clean_db.execute("INSERT INTO taxonomy_classes (id, name, parent_id) VALUES (1, 'Equity', NULL)")
    clean_db.execute("INSERT INTO taxonomy_classes (id, name, parent_id) VALUES (2, 'US Equity', 1)")
    clean_db.execute("INSERT INTO taxonomy_classes (id, name, parent_id) VALUES (3, 'US Equity', 1)")  # duplicate!

    report = run_integrity_checks(clean_db)
    check = next(c for c in report.checks if c.name == "net_worth_cross_endpoint_consistency")
    assert not check.passed, \
        "Should detect divergence when taxonomy_classes has duplicate rows causing double-counting"


def test_twr_xirr_consistency_detects_divergence(clean_db):
    from src.validation.data_integrity_gate import run_integrity_checks
    from tests.e2e.test_financial_integrity import _seed_normal_portfolio
    
    _seed_normal_portfolio(clean_db)
    db = clean_db
    
    # Needs mock priority for build_source_filter_clauses in XIRR
    try:
        db.execute("INSERT INTO source_system_priorities (id, source_system, priority, is_enabled) VALUES (1, 'Schwab_CSV', 1, TRUE)")
    except Exception:
        pass # Might already exist
        
    # We need mock asset_taxonomy for TWR risk assets check
    try:
        db.execute("INSERT INTO taxonomy_classes (id, name, parent_id, is_rebalanceable) VALUES (1, 'Equity', NULL, TRUE)")
        db.execute("INSERT INTO asset_taxonomy (id, asset_class, asset_subclass, expired_date) VALUES (1, 'Equity', 'US Equity', NULL)")
    except Exception:
        pass
    
    # Add massive divergent trades on top of the normal portfolio
    try:
        db.execute("INSERT INTO asset_registry (id, canonical_id, asset_name, source_system, asset_class, is_rebalanceable) VALUES (101, 'US_STK_DIVERGE', 'DIV', 'Schwab_CSV', 'Equity', TRUE)")
    except Exception:
        pass
    
    # To pass early checks (Net Worth, TWR range -80% to 200%, XIRR range -80% to 200%):
    # Let's create a portfolio that starts at 1,000,000 (Passes Check 11).
    db.execute("INSERT INTO balance_sheet_monthly (id, snapshot_date, payload) VALUES (2, '2025-01-01', '{\"合计总资产\": 1000000.0}')")
    db.execute("INSERT INTO transactions (id, asset_id, source_system, transaction_date, transaction_type, quantity, price_unit, amount_net, currency, is_provisional) VALUES (991, 'US_STK_DIVERGE', 'Schwab_CSV', '2025-01-01', 'buy', 1000, 1000, 1000000.0, 'CNY', FALSE)")
    
    # 6 months later: market goes up 50% (Price 1500). TWR = +50%
    db.execute("INSERT INTO balance_sheet_monthly (id, snapshot_date, payload) VALUES (3, '2025-07-01', '{\"合计总资产\": 1500000.0}')")

    # User buys 2,000,000 more at the top. Total Invested = 3,000,000. Value = 3,500,000
    db.execute("INSERT INTO transactions (id, asset_id, source_system, transaction_date, transaction_type, quantity, price_unit, amount_net, currency, is_provisional) VALUES (992, 'US_STK_DIVERGE', 'Schwab_CSV', '2025-07-01', 'buy', 1333.3333, 1500, 2000000.0, 'CNY', FALSE)")

    # End of timeline: market crashes back to 1100.
    # Base 1,000 shares = 1.1M. New 1,333 shares = 1.46M. Total ~2.56M.
    # Total Invested = 3M, Total Value = 2.56M. XIRR is negative. TWR is (1.5) * (1100/1500) - 1 = 1.5 * 0.733 - 1 = +10%.
    # Spread > 25%? XIRR roughly -20%, TWR +10%. Spread ~ 30%. Both inside [-80%, 200%] bounds!
    from datetime import date
    end_dt_str = date.today().isoformat()
    db.execute(f"INSERT INTO holdings (id, asset_id, asset_name, source_system, snapshot_date, quantity, market_price_unit, market_value, cost_price_unit, currency, is_shadow, is_provisional) VALUES (993, 'US_STK_DIVERGE', 'DIV', 'Schwab_CSV', '{end_dt_str}', 2333.3333, 1100, 2566666.63, 1285.71, 'CNY', FALSE, FALSE)")
    
    report = run_integrity_checks(db)
    check = next((c for c in report.checks if c.name == "twr_xirr_consistency"), None)
    
    print('XIRR Check Details:', check.details if check else 'No check found')
    assert check is not None
    assert check.passed is False
    assert "spread exceeds 25.0%" in check.details

def test_twr_xirr_consistency_skips_short_history(clean_db):
    from src.validation.data_integrity_gate import run_integrity_checks
    db = clean_db
    db.execute("INSERT INTO balance_sheet_monthly (id, snapshot_date, payload) VALUES (1, '2025-01-01', '{\"合计总资产\": 1000000.0}')")
    db.execute("INSERT INTO holdings (id, asset_id, asset_name, source_system, snapshot_date, quantity, market_price_unit, market_value, cost_price_unit, currency, is_shadow, is_provisional) VALUES (2, 'US_STK_SPY', 'SPY', 'Schwab_CSV', '2025-01-10', 1000, 1100, 1100000.0, 1000, 'CNY', FALSE, FALSE)")
    db.execute("INSERT INTO transactions (id, asset_id, source_system, transaction_date, transaction_type, quantity, price_unit, amount_net, currency, is_provisional) VALUES (1, 'US_STK_SPY', 'Schwab_CSV', '2025-01-01', 'buy', 1000, 1000, 1000000.0, 'CNY', FALSE)")
    
    # asset_taxonomy was dropped in Migration 16 (Pass F) — no production code reads it; INSERT removed
    db.execute("INSERT INTO taxonomy_classes (id, name, parent_id, is_rebalanceable) VALUES (1, 'Equity', NULL, TRUE)")
    
    report = run_integrity_checks(db)
    check = next((c for c in report.checks if c.name == "twr_xirr_consistency"), None)
    
    assert check is not None
    assert check.passed is True
    assert "skipped" in check.details.lower()
    assert "less than 30 days" in check.details.lower()

def test_integrity_report_counts(clean_db):
    """Report should have exactly INTEGRITY_CHECK_COUNT checks (derived from the registry, never hard-coded)."""
    _seed_normal_portfolio(clean_db)
    report = run_integrity_checks(clean_db)

    from src.validation.data_integrity_gate import INTEGRITY_CHECK_COUNT
    assert len(report.checks) == INTEGRITY_CHECK_COUNT, f"Expected {INTEGRITY_CHECK_COUNT} checks, got {len(report.checks)}"
    # Three-state accounting (2026-07-26): passed_count now EXCLUDES skipped
    # checks, so the partition is verified + skipped + failed, not verified +
    # failed. A skipped check evaluated nothing; counting it as a pass is what
    # let check #4 report a false PASS for its entire life.
    assert (
        report.passed_count + report.skipped_count + len(report.failed_checks)
        == len(report.checks)
    ), (
        f"three-state partition broken: verified={report.passed_count} "
        f"skipped={report.skipped_count} failed={len(report.failed_checks)} "
        f"total={len(report.checks)}"
    )
    # A skipped check must never also be counted as verified.
    # (CheckResult is an unhashable dataclass, so compare by name.)
    skipped_names = {c.name for c in report.skipped_checks}
    verified_names = {c.name for c in report.checks if c.passed and not c.skipped}
    assert not (skipped_names & verified_names), (
        f"check(s) counted as both skipped and verified: {skipped_names & verified_names}"
    )
    
    # Additional assertions from the instruction
    for check_result in report.checks:
        assert hasattr(check_result, "name")
        assert hasattr(check_result, "passed")
