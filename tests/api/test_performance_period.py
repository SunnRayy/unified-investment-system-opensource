import asyncio
import datetime as dt

import duckdb
import pytest

from src.api.routes.data import get_performance_history
from src.api.routes.performance import (
    calculate_realized_pnl,
    get_gains_analysis,
    get_performance_returns,
    period_start_date,
)


class DuckDBAdapter:
    def __init__(self, connection: duckdb.DuckDBPyConnection):
        self.connection = connection

    def execute(self, query, params=None):
        if params is None:
            return self.connection.execute(query)
        return self.connection.execute(query, params)


@pytest.fixture
def perf_db(tmp_path):
    db_path = tmp_path / "performance_period.duckdb"
    conn = duckdb.connect(str(db_path))

    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            quantity DOUBLE,
            cost_price_unit DOUBLE,
            market_price_unit DOUBLE,
            market_value DOUBLE,
            currency VARCHAR,
            source_system VARCHAR,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            transaction_date DATE,
            asset_id VARCHAR,
            transaction_type VARCHAR,
            quantity DOUBLE,
            price_unit DOUBLE,
            amount_net DOUBLE,
            currency VARCHAR,
            source_system VARCHAR,
            is_provisional BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE asset_registry (
            canonical_id VARCHAR,
            asset_class VARCHAR,
            asset_subclass VARCHAR,
            is_rebalanceable BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE taxonomy_classes (
            id INTEGER PRIMARY KEY,
            name VARCHAR,
            parent_id INTEGER,
            is_rebalanceable BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE asset_taxonomy (
            asset_class VARCHAR,
            asset_subclass VARCHAR,
            expired_date DATE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE balance_sheet_monthly (
            record_key VARCHAR,
            snapshot_date DATE,
            payload VARCHAR
        )
        """
    )

    conn.execute(
        """
        INSERT INTO asset_registry VALUES
            ('EQ1', 'US Equity', 'US Equity', TRUE),
            ('RE1', 'Real Estate (房地产)', 'Residential (住宅)', FALSE),
            ('INS1', 'Insurance (保险)', 'Insurance (保险)', FALSE)
        """
    )
    conn.execute("INSERT INTO asset_taxonomy VALUES ('股票', 'US Equity', NULL)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (1, 'Real Estate (房地产)', NULL, FALSE)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (2, 'Insurance (保险)', NULL, FALSE)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (3, 'US Equity', NULL, TRUE)")

    conn.execute(
        """
        INSERT INTO holdings VALUES
            ('2022-01-01', 'EQ1', 'Equity One', 1, 100, 100, 100, 'CNY', 'TEST', FALSE),
            ('2022-01-01', 'RE1', 'Property One', 1, 100, 100, 100, 'CNY', 'TEST', FALSE),
            ('2024-01-01', 'EQ1', 'Equity One', 1, 100, 140, 140, 'CNY', 'TEST', FALSE),
            ('2024-01-01', 'RE1', 'Property One', 1, 100, 120, 120, 'CNY', 'TEST', FALSE),
            ('2025-06-01', 'EQ1', 'Equity One', 1, 100, 160, 160, 'CNY', 'TEST', FALSE),
            ('2025-06-01', 'RE1', 'Property One', 1, 100, 140, 140, 'CNY', 'TEST', FALSE),
            ('2026-01-01', 'EQ1', 'Equity One', 1, 100, 180, 180, 'CNY', 'TEST', FALSE),
            ('2026-01-01', 'RE1', 'Property One', 1, 100, 160, 160, 'CNY', 'TEST', FALSE),
            ('2026-01-01', 'INS1', 'Insurance One', 1, 20, 20, 20, 'CNY', 'TEST', FALSE)
        """
    )

    conn.execute(
        """
        INSERT INTO transactions VALUES
            ('2022-01-01', 'EQ1', 'buy', 1, 100, -100, 'CNY', 'TEST', FALSE),
            -- Sell must stay inside every rolling last_12m window (a hardcoded
            -- 2025-07-01 became a time bomb on 2026-07-02) — keep it dynamic.
            ((CURRENT_DATE - INTERVAL '30 days')::DATE, 'EQ1', 'sell', 0.5, 170, 85, 'CNY', 'TEST', FALSE)
        """
    )

    conn.execute(
        """
        INSERT INTO balance_sheet_monthly VALUES
            ('BS_TOTAL', '2020-02-01', '{"合计总资产": 1000}'),
            ('BS_TOTAL', '2021-02-01', '{"合计总资产": 1200}'),
            ('BS_TOTAL', (CURRENT_DATE - INTERVAL '180 days')::DATE, '{"合计总资产": 280}')
        """
    )

    try:
        yield DuckDBAdapter(conn)
    finally:
        conn.close()


def test_performance_returns_period_filter_changes_values(perf_db):
    all_time = asyncio.run(get_performance_returns(db=perf_db, period="all_time"))
    last_12m = asyncio.run(get_performance_returns(db=perf_db, period="last_12m"))

    assert all_time["twr_cumulative"] is not None
    assert last_12m["twr_cumulative"] is not None
    assert all_time["twr_cumulative"] != last_12m["twr_cumulative"]


def test_performance_history_period_filter_trims_old_points(perf_db):
    all_time = asyncio.run(get_performance_history(db=perf_db, period="all_time"))
    last_12m = asyncio.run(get_performance_history(db=perf_db, period="last_12m"))

    assert len(all_time) > 0
    assert len(last_12m) > 0
    assert len(last_12m) < len(all_time)

    cutoff = dt.date.today() - dt.timedelta(days=365)
    assert min(dt.date.fromisoformat(item["name"]) for item in last_12m) >= cutoff


def test_performance_returns_exclusion_changes_values(perf_db):
    include_all = asyncio.run(
        get_performance_returns(
            db=perf_db,
            period="all_time",
            exclude_non_balanceable=False,
        )
    )
    exclude_non_balanceable = asyncio.run(
        get_performance_returns(
            db=perf_db,
            period="all_time",
            exclude_non_balanceable=True,
        )
    )

    assert include_all["twr_cumulative"] is not None
    assert exclude_non_balanceable["twr_cumulative"] is not None
    assert include_all["twr_cumulative"] != exclude_non_balanceable["twr_cumulative"]


def test_performance_history_exclusion_changes_latest_value(perf_db):
    include_all = asyncio.run(
        get_performance_history(
            db=perf_db,
            period="all_time",
            exclude_non_balanceable=False,
        )
    )
    exclude_non_balanceable = asyncio.run(
        get_performance_history(
            db=perf_db,
            period="all_time",
            exclude_non_balanceable=True,
        )
    )

    include_latest = next(item for item in include_all if item["name"] == "2026-01-01")
    exclude_latest = next(item for item in exclude_non_balanceable if item["name"] == "2026-01-01")

    assert include_latest["value"] > exclude_latest["value"]


def test_realized_pnl_period_uses_pre_period_lots_for_cost_basis(perf_db):
    """A period sell should use historical lots, not treat full proceeds as gain."""
    start_date = period_start_date("last_12m")
    realized, currency = calculate_realized_pnl(perf_db, "EQ1", start_date=start_date)

    # EQ1: buy 1 @100 (2022-01-01), sell 0.5 @170 (~30 days ago, always in window)
    # Realized P&L must be 0.5*(170-100)=35, not sale proceeds 85.
    assert round(realized, 2) == 35.00
    assert currency == "CNY"


def test_gains_analysis_return_pct_uses_period_profit(perf_db):
    """Per-asset return_pct should use unrealized+realized profit for selected period."""
    gains = asyncio.run(get_gains_analysis(db=perf_db, period="all_time"))
    eq1 = next(asset for asset in gains["assets"] if asset["asset_id"] == "EQ1")

    # EQ1 period profit = unrealized(80) + realized(35) = 115 on cost basis 100 -> 115%
    assert round(eq1["return_pct"], 2) == 115.00
    assert eq1["pnl_currency"] == "CNY"
    assert round(eq1["unrealized_pl_native"], 2) == 80.00
    assert round(eq1["realized_pl_native"], 2) == 35.00
