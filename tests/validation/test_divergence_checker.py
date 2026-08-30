
import pytest

pytestmark = pytest.mark.pipeline

from datetime import date
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema

from src.validation.divergence_checker import DivergenceChecker

@pytest.fixture
def db_connector():
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    return connector

def test_divergence_checker_logs_audit(db_connector):
    today = date.today()
    
    # Setup Data (Reusing logic from reconciliation test implicitly)
    # Insert Divergent Record (>10%)
    db_connector.execute("""
        INSERT INTO holdings (snapshot_date, asset_id, source_system, market_value, is_shadow, authority_source)
        VALUES 
            (?, 'US_STK_BAD', 'AIA', 1200, FALSE, 'AIA'),
            (?, 'US_STK_BAD', 'PIS', 1000, TRUE, 'AIA')
    """, (today, today))
    
    # Insert OK Record (<10%)
    db_connector.execute("""
        INSERT INTO holdings (snapshot_date, asset_id, source_system, market_value, is_shadow, authority_source)
        VALUES 
            (?, 'US_STK_GOOD', 'AIA', 100, FALSE, 'AIA'),
            (?, 'US_STK_GOOD', 'PIS', 95, TRUE, 'AIA')
    """, (today, today))
    
    checker = DivergenceChecker(db_connector)
    # Check with default threshold 10%
    checker.check_divergence(threshold_pct=10.0)
    
    # Verify Audit Log
    # Should have 1 entry for US_STK_BAD
    audit_rows = db_connector.execute("SELECT * FROM sync_audit_logs").fetchall()
    assert len(audit_rows) == 1

    # Re-query the specific columns rather than indexing the SELECT * tuple,
    # so the assertions below don't depend on physical column order.
    res = db_connector.execute("""
        SELECT record_key, conflict_type, source_system 
        FROM sync_audit_logs
    """).fetchone()
    
    assert res[0] == 'US_STK_BAD'
    assert res[1] == 'holdings_divergence'
    assert res[2] == 'AIA'
