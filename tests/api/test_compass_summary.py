import asyncio

import duckdb

from src.api.routes.compass import get_compass_summary


class DuckDBAdapter:
    def __init__(self, conn):
        self.connection = conn

    def execute(self, query, params=None):
        if params is None:
            return self.connection.execute(query)
        return self.connection.execute(query, params)


def test_compass_summary_reads_last_sync_metadata_from_sync_audit_reports(tmp_path):
    conn = duckdb.connect(str(tmp_path / "compass_summary.duckdb"))
    db = DuckDBAdapter(conn)

    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            quantity DOUBLE,
            market_value DOUBLE,
            currency VARCHAR,
            source_system VARCHAR,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE asset_registry (
            canonical_id VARCHAR,
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
            parent_id INTEGER,
            is_rebalanceable BOOLEAN,
            level INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE sync_audit_reports (
            id VARCHAR,
            created_at TIMESTAMP,
            report_type VARCHAR,
            by_source_after JSON
        )
        """
    )

    conn.execute(
        """
        INSERT INTO taxonomy_classes VALUES
        (1, 'Equity', NULL, TRUE, 0),
        (2, 'US Equity', 1, TRUE, 1)
        """
    )
    conn.execute(
        """
        INSERT INTO asset_registry VALUES
        ('US_STK_TEST', 'US Equity', TRUE)
        """
    )
    conn.execute(
        """
        INSERT INTO holdings VALUES
        ('2026-03-10', 'US_STK_TEST', 'Test Asset', 1, 100000, 'CNY', 'Schwab_CSV', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO sync_audit_reports VALUES
        ('run-old', '2026-03-12 12:00:00', 'sync', '{"Gold_Excel": {"count": 1, "value": 1000}}'),
        ('run-new', '2026-03-13 17:09:50', 'sync', '{"Schwab_CSV": {"count": 7, "value": 590000.0}, "CN_Fund_Excel": {"count": 19, "value": 1560000.0}}')
        """
    )

    result = asyncio.run(get_compass_summary(db=db, include_non_rebalanceable=False))

    assert result["last_sync_date"] == "2026-03-13"
    assert result["last_sync_source"] == "CN_Fund_Excel, Schwab_CSV"

    conn.close()
