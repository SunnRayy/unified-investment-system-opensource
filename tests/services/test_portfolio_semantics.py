"""Unit tests for the AI-advisor portfolio-semantics formatters.

After the P&L-engine migration (Release 1 / Step 5) both functions route through
``compute_portfolio_pnl``, which needs the full holdings schema (currency,
market_price_unit, source_system) and a real ``transactions`` table. These
fixtures are now hermetic — full schema + real FIFO transactions — so they pass
both alone and in the suite (fixing the pre-engine missing-``currency``-column
isolation flakiness), and the realized figure comes from real FIFO rather than a
patched helper. Byte-parity against the pre-engine loop is proven separately in
test_portfolio_semantics_engine_parity.py.
"""
from __future__ import annotations

import duckdb

from src.services.portfolio_semantics import (
    build_portfolio_summary_semantics,
    fetch_wealthos_active_holdings,
)


def _create_schema(conn):
    conn.execute(
        """
        CREATE TABLE holdings (
            asset_id VARCHAR,
            asset_name VARCHAR,
            source_system VARCHAR,
            market_value DOUBLE,
            cost_price_unit DOUBLE,
            market_price_unit DOUBLE,
            quantity DOUBLE,
            currency VARCHAR,
            snapshot_date DATE,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            asset_id VARCHAR,
            asset_name VARCHAR,
            transaction_type VARCHAR,
            quantity DOUBLE,
            price_unit DOUBLE,
            amount_net DOUBLE,
            currency VARCHAR,
            transaction_date DATE,
            source_system VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE asset_registry (
            canonical_id VARCHAR,
            display_name VARCHAR,
            asset_class VARCHAR,
            is_rebalanceable BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE taxonomy_classes (
            id INTEGER,
            name VARCHAR,
            name_cn VARCHAR,
            parent_id INTEGER,
            is_rebalanceable BOOLEAN
        )
        """
    )
    conn.execute(
        """
        INSERT INTO taxonomy_classes VALUES
        (1, 'Equity', '股票', NULL, TRUE),
        (2, 'Cash', '现金', NULL, TRUE),
        (3, 'Real Estate', '房地产', NULL, FALSE),
        (4, 'US Equity', '美股', 1, TRUE),
        (5, 'Cash Checking', '活期', 2, TRUE),
        (6, 'Property', '房产', 3, FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO asset_registry VALUES
        ('US_STK_AAPL', 'Apple Inc.', 'US Equity', TRUE),
        ('CASH_CNY', 'Cash Checking', 'Cash Checking', TRUE),
        ('PROP_A', 'Property A', 'Property', FALSE)
        """
    )


def test_build_portfolio_summary_semantics_matches_performance_summary_fixture(tmp_path):
    db_path = tmp_path / "portfolio_semantics_summary.duckdb"
    conn = duckdb.connect(str(db_path))
    _create_schema(conn)
    conn.execute(
        """
        INSERT INTO holdings VALUES
        ('US_STK_AAPL', 'Apple Inc.', 'Schwab_CSV', 700000, 5000, 7000, 100, 'CNY', '2026-03-20', FALSE),
        ('CASH_CNY', 'Cash Checking', 'PIS', 100000, 100000, 100000, 1, 'CNY', '2026-03-20', FALSE),
        ('PROP_A', 'Property A', 'PIS', 300000, 250000, 300000, 1, 'CNY', '2026-03-20', FALSE)
        """
    )
    # AAPL: buy 110 @ 5000, sell 10 @ 5012 → FIFO realized = 10*(5012-5000) = 120 CNY;
    # remaining held qty 100 matches the snapshot. CASH buy is realized-exempt (0).
    conn.execute(
        """
        INSERT INTO transactions VALUES
        ('US_STK_AAPL', 'Apple Inc.', 'buy', 110, 5000, 550000, 'CNY', '2025-01-01', 'Schwab_CSV'),
        ('US_STK_AAPL', 'Apple Inc.', 'sell', 10, 5012, 50120, 'CNY', '2025-06-01', 'Schwab_CSV'),
        ('CASH_CNY', 'Cash Checking', 'buy', 1, 100000, 100000, 'CNY', '2025-01-01', 'PIS')
        """
    )

    result = build_portfolio_summary_semantics(conn, include_non_rebalanceable=False)
    conn.close()

    assert result["net_worth"] == 800000.0
    assert result["total_cost_basis"] == 600000.0
    assert result["total_unrealized_pl"] == 200000.0
    assert result["total_realized_pl"] == 120.0
    assert result["total_lifetime_pl"] == 200120.0
    assert result["asset_count"] == 2


def test_fetch_wealthos_active_holdings_excludes_sold_after_snapshot_and_non_rebalanceable(tmp_path):
    db_path = tmp_path / "portfolio_semantics_holdings.duckdb"
    conn = duckdb.connect(str(db_path))
    _create_schema(conn)
    conn.execute(
        """
        INSERT INTO holdings VALUES
        ('US_STK_AAPL', 'Apple Inc.', 'Schwab_CSV', 700000, 5000, 7000, 100, 'CNY', '2026-03-20', FALSE),
        ('CASH_CNY', 'Cash Checking', 'PIS', 100000, 100000, 100000, 1, 'CNY', '2026-03-20', FALSE),
        ('PROP_A', 'Property A', 'PIS', 300000, 250000, 300000, 1, 'CNY', '2026-03-20', FALSE)
        """
    )
    # AAPL fully sold on 2026-03-25 (after its 2026-03-20 snapshot) → dropped as a
    # sold-after-snapshot reader (Schwab_CSV) close.
    conn.execute(
        """
        INSERT INTO transactions VALUES
        ('US_STK_AAPL', 'Apple Inc.', 'buy', 100, 5000, 500000, 'CNY', '2026-03-01', 'Schwab_CSV'),
        ('US_STK_AAPL', 'Apple Inc.', 'sell', 100, 6000, 600000, 'CNY', '2026-03-25', 'Schwab_CSV'),
        ('CASH_CNY', 'Cash Checking', 'buy', 1, 100000, 100000, 'CNY', '2026-03-01', 'PIS')
        """
    )

    holdings = fetch_wealthos_active_holdings(conn, include_non_rebalanceable=False)
    conn.close()

    asset_ids = {row["asset_id"] for row in holdings}

    assert "US_STK_AAPL" not in asset_ids
    assert "PROP_A" not in asset_ids
    assert "CASH_CNY" in asset_ids
    cash_row = next(row for row in holdings if row["asset_id"] == "CASH_CNY")
    assert cash_row["name"] == "Cash Checking"
    assert cash_row["market_value"] == 100000.0
