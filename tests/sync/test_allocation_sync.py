"""Tests for current allocation sync."""
import pytest

pytestmark = pytest.mark.pipeline

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.sync.allocation_sync import sync_current_allocations


class TestAllocationSync:
    @pytest.fixture
    def connector(self):
        conn = DatabaseConnector(":memory:")
        initialize_schema(conn)
        # Add sample holdings
        conn.execute("""
            INSERT INTO holdings (asset_id, asset_name, quantity, market_value,
                                  cost_price_unit, snapshot_date)
            VALUES
                ('CN_FUND_900002', '嘉实新兴', 1000, 50000, 45, CURRENT_DATE),
                ('CN_STK_600519.SH', '贵州茅台', 10, 30000, 2500, CURRENT_DATE),
                ('Property_BlueCounty', '房产', 1, 200000, 150000, CURRENT_DATE)
        """)
        # Add asset registry
        # Note: asset_taxonomy INSERT removed — table dropped in Migration 16 (Pass F).
        # sync_current_allocations does not query asset_taxonomy.
        conn.execute("""
            INSERT INTO asset_registry (canonical_id, display_name, asset_class,
                                        asset_subclass, is_rebalanceable)
            VALUES
                ('CN_FUND_900002', '嘉实新兴', 'Equity', 'CN Fund', TRUE),
                ('CN_STK_600519.SH', '贵州茅台', 'Equity', 'CN Stock', TRUE),
                ('Property_BlueCounty', '房产', 'Real Estate', NULL, FALSE)
        """)
        yield conn
        conn.close()

    def test_calculates_rebalanceable_allocations(self, connector):
        """Should calculate allocations for rebalanceable assets only."""
        sync_current_allocations(connector)

        # Total rebalanceable = 50000 + 30000 = 80000
        # CN Fund: 50000/80000 = 62.5%
        # CN Stock: 30000/80000 = 37.5%

        allocations = connector.execute("""
            SELECT asset_class, current_pct FROM current_allocations
            WHERE is_rebalanceable = TRUE
            ORDER BY asset_class
        """).fetchall()

        assert len(allocations) == 2
        # Verify percentages sum to ~100%
        total_pct = sum(a[1] for a in allocations)
        assert abs(total_pct - 100) < 1

    def test_excludes_non_rebalanceable_from_pct_calc(self, connector):
        """Should not include non-rebalanceable in percentage denominator."""
        sync_current_allocations(connector)

        # Property should be tracked but not in % calculation
        property_row = connector.execute("""
            SELECT current_pct, market_value, is_rebalanceable
            FROM current_allocations
            WHERE asset_class = 'Real Estate'
        """).fetchone()

        assert property_row is not None
        assert property_row[2] == False  # is_rebalanceable
        # Property value should be stored
        assert property_row[1] == 200000

    def test_returns_sync_counts(self, connector):
        """Should return count of synced allocations."""
        result = sync_current_allocations(connector)

        assert result['synced'] >= 2

    def test_null_market_value_does_not_crash(self):
        """NULL market_value in holdings does not raise TypeError."""
        conn = DatabaseConnector(":memory:")
        initialize_schema(conn)
        conn.execute("""
            INSERT INTO holdings (asset_id, asset_name, quantity, market_value,
                                  cost_price_unit, snapshot_date)
            VALUES ('US_STK_AAPL', 'Apple', 5, NULL, 150, CURRENT_DATE)
        """)
        conn.execute("""
            INSERT INTO asset_registry (canonical_id, display_name, asset_class,
                                        asset_subclass, is_rebalanceable)
            VALUES ('US_STK_AAPL', 'Apple', 'Equity', 'US Stock', TRUE)
        """)
        # Should not raise
        result = sync_current_allocations(conn)
        assert result['synced'] >= 0
        conn.close()

    def test_duplicate_key_prevented_for_mixed_is_rebalanceable(self):
        """Two assets with same (asset_class, asset_subclass) but different is_rebalanceable
        values must NOT cause a UNIQUE constraint violation on current_allocations."""
        conn = DatabaseConnector(":memory:")
        initialize_schema(conn)
        # Simulate the real-world "Bank Wealth/Insurance, Unclassified" scenario:
        # asset_registry.is_rebalanceable is unreliable — Insurance/BankWealth may be
        # set to TRUE even when the class should be non-rebalanceable.
        conn.execute("""
            INSERT INTO holdings (asset_id, asset_name, quantity, market_value,
                                  cost_price_unit, snapshot_date)
            VALUES
                ('INS_PolicyA', '保险A', 1, 100000, 80000, CURRENT_DATE),
                ('INS_PolicyB', '保险B', 1, 50000, 40000, CURRENT_DATE)
        """)
        conn.execute("""
            INSERT INTO asset_registry (canonical_id, display_name, asset_class,
                                        asset_subclass, is_rebalanceable)
            VALUES
                ('INS_PolicyA', '保险A', 'Bank Wealth/Insurance', 'Unclassified', FALSE),
                ('INS_PolicyB', '保险B', 'Bank Wealth/Insurance', 'Unclassified', TRUE)
        """)
        # Must not raise a UNIQUE constraint error
        result = sync_current_allocations(conn)
        assert result['synced'] >= 1
        rows = conn.execute(
            "SELECT COUNT(*) FROM current_allocations WHERE asset_class = 'Bank Wealth/Insurance'"
        ).fetchone()[0]
        # Exactly one row for the class — not two
        assert rows == 1
        # MIN semantics: FALSE wins (non-rebalanceable) when values disagree
        row = conn.execute(
            "SELECT is_rebalanceable FROM current_allocations WHERE asset_class = 'Bank Wealth/Insurance'"
        ).fetchone()
        assert row[0] == False
        conn.close()

    def test_latest_per_asset_aggregates_mixed_snapshot_dates(self):
        """Holdings from different snapshot_dates are combined via latest-per-asset CTE."""
        conn = DatabaseConnector(":memory:")
        initialize_schema(conn)
        # Insert two assets with DIFFERENT snapshot dates
        conn.execute("""
            INSERT INTO holdings (asset_id, asset_name, quantity, market_value,
                                  cost_price_unit, snapshot_date)
            VALUES
                ('CN_FUND_900002', 'Fund', 1000, 50000, 45, '2026-02-27'),
                ('US_STK_AAPL', 'Apple', 5, 3000, 150, '2026-03-01')
        """)
        conn.execute("""
            INSERT INTO asset_registry (canonical_id, display_name, asset_class,
                                        asset_subclass, is_rebalanceable)
            VALUES
                ('CN_FUND_900002', 'Fund', 'Equity', 'CN Fund', TRUE),
                ('US_STK_AAPL', 'Apple', 'Equity', 'US Stock', TRUE)
        """)
        sync_current_allocations(conn)
        # Both assets should be included (not just the one with latest date)
        total_mv = conn.execute(
            "SELECT SUM(market_value) FROM current_allocations"
        ).fetchone()[0]
        assert total_mv == pytest.approx(53000.0, rel=0.01)
        conn.close()
