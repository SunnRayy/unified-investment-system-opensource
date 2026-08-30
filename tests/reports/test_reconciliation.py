
import pytest
from datetime import date
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema

from src.reports.reconciliation import generate_reconciliation_report

@pytest.fixture
def db_connector():
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    return connector

def test_reconciliation_detects_divergence(db_connector):
    today = date.today()
    
    # 1. Insert Data
    # Discrepancy > 10%
    # AIA (Auth) = 1200
    # PIS (Shadow) = 1000
    # Diff = 200 / 1200 = 16.6%
    
    db_connector.execute("""
        INSERT INTO holdings (snapshot_date, asset_id, source_system, market_value, is_shadow, authority_source)
        VALUES 
            (?, 'US_STK_DIVERGENT', 'AIA', 1200, FALSE, 'AIA'),
            (?, 'US_STK_DIVERGENT', 'PIS', 1000, TRUE, 'AIA')
    """, (today, today))
    
    # Match < 10%
    # AIA (Auth) = 100
    # PIS (Shadow) = 95
    # Diff = 5%
    db_connector.execute("""
        INSERT INTO holdings (snapshot_date, asset_id, source_system, market_value, is_shadow, authority_source)
        VALUES 
            (?, 'US_STK_MATCH', 'AIA', 100, FALSE, 'AIA'),
            (?, 'US_STK_MATCH', 'PIS', 95, TRUE, 'AIA')
    """, (today, today))
    
    # 2. Run Report
    report = generate_reconciliation_report(db_connector, today)
    
    # 3. Verify
    # Should contain 2 items (it reports all overlaps usually, with diff stats)
    # But specifically checking values
    
    assert len(report) >= 2
    
    divergent_item = next(r for r in report if r['canonical_id'] == 'US_STK_DIVERGENT')
    assert divergent_item['diverence_pct'] > 10.0
    assert divergent_item['auth_value'] == 1200
    assert divergent_item['shadow_value'] == 1000

    match_item = next(r for r in report if r['canonical_id'] == 'US_STK_MATCH')
    assert match_item['diverence_pct'] == 5.0
