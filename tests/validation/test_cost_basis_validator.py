
"""Tests for cost basis validator."""
import pytest

pytestmark = pytest.mark.pipeline

import duckdb
from src.validation.cost_basis_validator import validate_cost_basis

class TestCostBasisValidator:
    @pytest.fixture
    def connector(self):
        conn = duckdb.connect(':memory:')
        # Minimal Schema — must include source_system and is_shadow for per-source validator logic
        conn.execute("CREATE TABLE holdings (asset_id VARCHAR, asset_name VARCHAR, quantity DOUBLE, cost_price_unit DOUBLE, snapshot_date DATE, currency VARCHAR default 'CNY', source_system VARCHAR DEFAULT 'Schwab_CSV', is_shadow BOOLEAN DEFAULT FALSE)")
        conn.execute("CREATE TABLE transactions (asset_id VARCHAR, transaction_type VARCHAR, quantity DOUBLE, price_unit DOUBLE, amount_net DOUBLE, currency VARCHAR default 'CNY', transaction_date DATE, source_system VARCHAR DEFAULT 'Schwab_CSV')")
        conn.execute("""
            CREATE TABLE sync_audit_logs (
                id INTEGER PRIMARY KEY,
                source_system VARCHAR,
                target_table VARCHAR,
                record_key VARCHAR,
                conflict_type VARCHAR,
                is_resolved BOOLEAN DEFAULT FALSE,
                source_value VARCHAR,
                target_value VARCHAR,
                resolution_notes VARCHAR,
                sync_timestamp TIMESTAMP,
                resolution VARCHAR, 
                resolved_by VARCHAR, 
                resolved_at TIMESTAMP
            )
        """)
        conn.execute("CREATE SEQUENCE seq_sync_audit_logs_id START 1")
        
        # Mock connector wrapper
        # If DatabaseConnector expects a path string in init, this might fail.
        # Let's check DatabaseConnector implementation or just mock it if it's simple.
        # Assuming we can inject the connection or it wraps it.
        # Actually src.database.connector usually takes a path. 
        # But for testing we can perhaps manually assign the connection?
        # Let's look at DatabaseConnector.
        
        # Temporary: Assume we can construct it or mock it.
        # If simple wrapper:
        class MockConnector:
            def __init__(self, connection):
                self.conn = connection
            def execute(self, query, params=None):
                if params:
                    return self.conn.execute(query, params)
                return self.conn.execute(query)
                
        return MockConnector(conn)

    def test_validates_with_currency_conversion(self, connector):
        """USD Schwab holdings: cost_price_unit is native USD (V5.2.0+), validator compares USD to USD."""
        connector.execute("""
            INSERT INTO transactions (asset_id, transaction_type, quantity, price_unit, amount_net, currency, transaction_date)
            VALUES ('US_STK_AAPL', 'Buy', 10, 150.0, -1500.0, 'USD', '2026-01-10')
        """)

        # V5.2.0+: Schwab holdings store cost_price_unit in native USD (150.0), not CNY (1050.0)
        connector.execute("""
            INSERT INTO holdings (asset_id, quantity, cost_price_unit, currency, snapshot_date)
            VALUES ('US_STK_AAPL', 10, 150.0, 'USD', CURRENT_DATE)
        """)

        discrepancies = validate_cost_basis(connector)

        # FIFO = 150.0 USD, synced = 150.0 USD → no discrepancy
        assert len(discrepancies) == 0

    def test_flags_real_discrepancy(self, connector):
        """Should still flag real discrepancies."""
        # Add transaction
        connector.execute("""
            INSERT INTO transactions (
                asset_id, transaction_type, quantity, price_unit, amount_net,
                currency, transaction_date, source_system
            )
            VALUES ('TEST_ASSET', 'Buy', 10, 100.0, -1000.0, 'CNY', '2026-01-10', 'CN_Fund_Excel')
        """)
        
        # Add holding with WRONG cost (e.g. 200 per share)
        connector.execute("""
            INSERT INTO holdings (
                asset_id, quantity, cost_price_unit, currency, snapshot_date, source_system
            )
            VALUES ('TEST_ASSET', 10, 200.0, 'CNY', CURRENT_DATE, 'CN_Fund_Excel')
        """)
        
        discrepancies = validate_cost_basis(connector)
        
        assert len(discrepancies) == 1
        assert discrepancies[0]['asset_id'] == 'TEST_ASSET'
        assert abs(discrepancies[0]['diff_pct'] - 100.0) < 0.1 # (200-100)/100 = 100% diff

    def test_uses_schwab_fixed_fx_convention_even_when_live_fx_differs(self, connector):
        """V5.2.0+: Schwab stores cost_price_unit in native USD; FX rate is irrelevant for validation."""
        connector.execute("""
            INSERT INTO transactions (
                asset_id, transaction_type, quantity, price_unit, amount_net,
                currency, transaction_date, source_system
            )
            VALUES ('US_STK_GOOGL', 'Buy', 13, 295.3, -3838.9, 'USD', '2025-11-19', 'Schwab_CSV')
        """)
        connector.execute("""
            INSERT INTO transactions (
                asset_id, transaction_type, quantity, price_unit, amount_net,
                currency, transaction_date, source_system
            )
            VALUES ('US_STK_GOOGL', 'Buy', 7, 311.1782, -2178.2474, 'USD', '2026-02-05', 'Schwab_CSV')
        """)
        # FIFO avg cost: (3838.9 + 2178.2474) / 20 = 300.857370 USD/share
        connector.execute("""
            INSERT INTO holdings (
                asset_id, asset_name, quantity, cost_price_unit, currency,
                snapshot_date, source_system, is_shadow
            )
            VALUES ('US_STK_GOOGL', 'Alphabet', 20, 300.85737, 'USD', CURRENT_DATE, 'Schwab_CSV', FALSE)
        """)

        discrepancies = validate_cost_basis(connector)

        assert discrepancies == []

    def test_suppresses_known_rsu_exception_with_canonical_id(self, connector):
        """RSU_AMZN is the canonical ID and should remain suppressed as a documented edge case."""
        connector.execute("""
            INSERT INTO transactions (
                asset_id, transaction_type, quantity, price_unit, amount_net,
                currency, transaction_date, source_system
            )
            VALUES
                ('RSU_AMZN', 'vest', 100, 200, 20000, 'USD', '2025-01-01', 'RSU_Excel'),
                ('RSU_AMZN', 'sell', -50, 210, -10500, 'USD', '2025-01-02', 'RSU_Excel')
        """)
        connector.execute("""
            INSERT INTO holdings (
                asset_id, asset_name, quantity, cost_price_unit, currency,
                snapshot_date, source_system, is_shadow
            )
            VALUES ('RSU_AMZN', 'Amazon RSU', 50, 9999, 'CNY', CURRENT_DATE, 'RSU_Excel', FALSE)
        """)

        discrepancies = validate_cost_basis(connector)

        assert discrepancies == []
