import asyncio

import duckdb
import pytest

from src.api.routes.performance import (
    get_performance_by_class,
    get_performance_summary,
    get_gains_analysis,
    get_performance_returns,
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
    db_path = tmp_path / "performance_non_balanceable.duckdb"
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
        CREATE TABLE asset_taxonomy (
            asset_class VARCHAR,
            asset_subclass VARCHAR,
            expired_date DATE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE taxonomy_classes (
            id INTEGER,
            name VARCHAR,
            parent_id INTEGER,
            is_rebalanceable BOOLEAN
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

    conn.execute(
        """
        INSERT INTO holdings VALUES
            ('2026-01-01', 'EQ1', 'Equity One', 1, 100, 180, 180, 'CNY', 'TEST', FALSE),
            ('2026-01-01', 'RE1', 'Property One', 1, 100, 160, 160, 'CNY', 'TEST', FALSE),
            ('2026-01-01', 'INS1', 'Insurance One', 1, 20, 20, 20, 'CNY', 'TEST', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO transactions VALUES
            ('2025-01-01', 'EQ1', 'buy', 1, 100, 100, 'CNY', 'TEST', FALSE),
            ('2025-01-01', 'INS1', 'premium_payment', 1, 20, 20, 'CNY', 'TEST', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO balance_sheet_monthly VALUES
            ('BS_TOTAL', '2025-01-01', '{"合计总资产": 240, "房产": 120, "保险": 20}'),
            ('BS_TOTAL', '2025-06-01', '{"合计总资产": 300, "房产": 140, "保险": 20}')
        """
    )

    try:
        yield DuckDBAdapter(conn)
    finally:
        conn.close()


def test_performance_by_class_can_exclude_non_balanceable(perf_db):
    include_all = asyncio.run(
        get_performance_by_class(db=perf_db, period="all_time", exclude_non_balanceable=False)
    )
    exclude_non_balanceable = asyncio.run(
        get_performance_by_class(db=perf_db, period="all_time", exclude_non_balanceable=True)
    )

    include_names = {item["class_name"] for item in include_all["top_classes"]}
    exclude_names = {item["class_name"] for item in exclude_non_balanceable["top_classes"]}

    assert any("Real Estate" in name for name in include_names)
    assert any("Insurance" in name for name in include_names)
    assert not any("Real Estate" in name for name in exclude_names)
    assert not any("Insurance" in name for name in exclude_names)


def test_performance_summary_exclusion_changes_totals(perf_db):
    include_all = asyncio.run(
        get_performance_summary(db=perf_db, period="all_time", exclude_non_balanceable=False)
    )
    exclude_non_balanceable = asyncio.run(
        get_performance_summary(db=perf_db, period="all_time", exclude_non_balanceable=True)
    )

    assert include_all["net_worth"] > exclude_non_balanceable["net_worth"]
    assert include_all["asset_count"] > exclude_non_balanceable["asset_count"]


def test_gains_analysis_exclusion_removes_non_balanceable_assets(perf_db):
    include_all = asyncio.run(
        get_gains_analysis(db=perf_db, period="all_time", exclude_non_balanceable=False)
    )
    exclude_non_balanceable = asyncio.run(
        get_gains_analysis(db=perf_db, period="all_time", exclude_non_balanceable=True)
    )

    include_assets = {item["asset_id"] for item in include_all["assets"]}
    exclude_assets = {item["asset_id"] for item in exclude_non_balanceable["assets"]}

    assert "RE1" in include_assets
    assert "INS1" in include_assets
    assert "RE1" not in exclude_assets
    assert "INS1" not in exclude_assets


def test_include_non_rebalanceable_param_overrides(perf_db):
    """New include_non_rebalanceable param works as alternative to exclude_non_balanceable."""
    include_all = asyncio.run(
        get_performance_summary(
            db=perf_db, period="all_time",
            exclude_non_balanceable=False,
            include_non_rebalanceable=True,
        )
    )
    exclude = asyncio.run(
        get_performance_summary(
            db=perf_db, period="all_time",
            exclude_non_balanceable=False,
            include_non_rebalanceable=False,
        )
    )
    assert include_all["net_worth"] > exclude["net_worth"]


def test_performance_returns_exclusion_changes_twr_and_xirr(perf_db):
    include_all = asyncio.run(
        get_performance_returns(
            db=perf_db,
            period="all_time",
            include_non_rebalanceable=True,
        )
    )
    exclude = asyncio.run(
        get_performance_returns(
            db=perf_db,
            period="all_time",
            include_non_rebalanceable=False,
        )
    )

    assert include_all["twr_cumulative"] is not None
    assert exclude["twr_cumulative"] is not None
    assert include_all["mwr_xirr"] is not None
    assert exclude["mwr_xirr"] is not None
    assert include_all["twr_cumulative"] != exclude["twr_cumulative"]
    assert include_all["mwr_xirr"] != exclude["mwr_xirr"]
