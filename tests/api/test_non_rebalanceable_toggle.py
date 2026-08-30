import asyncio
import duckdb
import pytest
from src.api.routes.data import get_dashboard_kpi


class DuckDBAdapter:
    def __init__(self, conn):
        self.connection = conn

    def execute(self, query, params=None):
        if params is None:
            return self.connection.execute(query)
        return self.connection.execute(query, params)


@pytest.fixture
def toggle_db(tmp_path):
    conn = duckdb.connect(str(tmp_path / "toggle_test.duckdb"))
    conn.execute("""CREATE TABLE holdings (
        snapshot_date DATE, asset_id VARCHAR, asset_name VARCHAR,
        quantity DOUBLE, cost_price_unit DOUBLE, market_value DOUBLE,
        currency VARCHAR, source_system VARCHAR, is_shadow BOOLEAN
    )""")
    conn.execute("""CREATE TABLE asset_registry (
        canonical_id VARCHAR, asset_class VARCHAR,
        asset_subclass VARCHAR, is_rebalanceable BOOLEAN
    )""")
    conn.execute("""CREATE TABLE taxonomy_classes (
        id INTEGER, name VARCHAR, parent_id INTEGER, is_rebalanceable BOOLEAN
    )""")
    conn.execute("INSERT INTO taxonomy_classes VALUES (1, 'Equity', NULL, TRUE)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (2, 'US Equity', 1, TRUE)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (3, 'Real Estate (房地产)', NULL, FALSE)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (4, 'Residential (住宅)', 3, FALSE)")

    conn.execute("INSERT INTO asset_registry VALUES ('EQ1', 'US Equity', 'US Equity', TRUE)")
    conn.execute("INSERT INTO asset_registry VALUES ('RE1', 'Residential (住宅)', 'Residential (住宅)', FALSE)")

    conn.execute("""INSERT INTO holdings VALUES
        ('2026-01-01', 'EQ1', 'Equity One', 1, 100, 300000, 'CNY', 'TEST', FALSE),
        ('2026-01-01', 'RE1', 'Property One', 1, 100, 200000, 'CNY', 'TEST', FALSE)
    """)
    try:
        yield DuckDBAdapter(conn)
    finally:
        conn.close()


def test_dashboard_kpi_excludes_non_rebalanceable_by_default(toggle_db):
    result = asyncio.run(get_dashboard_kpi(db=toggle_db, include_non_rebalanceable=False))
    # Default: include_non_rebalanceable=False → exclude RE
    assert result["net_worth"] == pytest.approx(300000, rel=0.01)


def test_dashboard_kpi_includes_when_toggled(toggle_db):
    result = asyncio.run(
        get_dashboard_kpi(db=toggle_db, include_non_rebalanceable=True)
    )
    assert result["net_worth"] == pytest.approx(500000, rel=0.01)
