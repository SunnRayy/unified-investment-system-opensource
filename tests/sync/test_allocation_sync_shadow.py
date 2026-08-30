
import pytest

pytestmark = pytest.mark.pipeline

from datetime import date
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.sync.allocation_sync import sync_current_allocations

# TDD: allocation sync should exclude shadow records

@pytest.fixture
def db_connector():
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    return connector

def test_allocation_sync_excludes_shadow(db_connector):
    today = date.today()
    
    # 1. Setup Asset Registry (required for JOIN)
    db_connector.execute("""
        INSERT INTO asset_registry (canonical_id, display_name, asset_class, is_rebalanceable)
        VALUES 
            ('US_STK_AAPL', 'Apple', 'US Equity', TRUE),
            ('CN_FUND_ONLY', 'China Fund', 'CN Equity', TRUE)
    """)
    
    # 2. Insert Holdings
    # AAPL: Have both PIS (Shadow) and AIA (Auth)
    # PIS Value: 1000 (Should be ignored)
    # AIA Value: 1200 (Should be counted)
    
    db_connector.execute("""
        INSERT INTO holdings (snapshot_date, asset_id, source_system, market_value, is_shadow)
        VALUES (?, 'US_STK_AAPL', 'PIS', 1000, TRUE)
    """, (today,))
    
    db_connector.execute("""
        INSERT INTO holdings (snapshot_date, asset_id, source_system, market_value, is_shadow)
        VALUES (?, 'US_STK_AAPL', 'AIA', 1200, FALSE)
    """, (today,))
    
    # CN Fund: Auth only
    db_connector.execute("""
        INSERT INTO holdings (snapshot_date, asset_id, source_system, market_value, is_shadow)
        VALUES (?, 'CN_FUND_ONLY', 'PIS', 500, FALSE)
    """, (today,))
    
    # 3. Run Sync
    sync_current_allocations(db_connector, today)
    
    # 4. Verify
    # Total Portfolio Value should be 1200 + 500 = 1700.
    # If shadow was included, it would be 1000 + 1200 + 500 = 2700.
    
    res = db_connector.execute("SELECT SUM(market_value) FROM current_allocations").fetchone()
    total_val = res[0]
    
    assert total_val == 1700, f"Expected 1700, got {total_val}. Shadow records likely included."
    
    # Verify specific allocation
    # US Equity should be 1200
    res_us = db_connector.execute("SELECT market_value FROM current_allocations WHERE asset_class='US Equity'").fetchone()
    assert res_us[0] == 1200
