
import pandas as pd
from datetime import date
from unittest.mock import patch
import duckdb

# We need to import the function we are testing
# But we need to make sure we can import it even if we are patching dependencies
from src.sync.dsa_sync import sync_market_data

class TestE2ECNFund:
    """End-to-end test for CN Fund Market Data Sync."""

    def test_sync_fetches_and_inserts_cn_funds(self, tmp_path):
        """
        Scenario:
        1. DB has a CN_FUND asset in asset_registry (active).
        2. MarketDataService returns valid price history.
        3. sync_market_data is called.
        4. Result: market_daily table contains the price record.
        """
        
        # 1. Setup Data
        db_path = tmp_path / "test.duckdb"
        # Create a simplified schema in this test DB
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE market_daily (code VARCHAR, date DATE, close DOUBLE, open DOUBLE, high DOUBLE, low DOUBLE, volume DOUBLE, amount DOUBLE, pct_chg DOUBLE, data_source VARCHAR, PRIMARY KEY(code, date))")
        conn.execute("CREATE TABLE asset_registry (canonical_id VARCHAR, asset_type VARCHAR, status VARCHAR)")

        # Seed asset
        conn.execute("INSERT INTO asset_registry VALUES ('CN_FUND_900001', 'fund', 'active')")
        
        # Create a Mock Connector that wraps this duckdb connection
        class MockConnector:
            def execute(self, query, params=None):
                # Simple execute wrapper
                return conn.execute(query, params)
            
            def fetch_df(self, query, params=None):
                 return conn.execute(query, params).df()

        connector = MockConnector()
        
        # 2. Mock MarketDataService
        # We assume sync_market_data will instantiate MarketDataService or use a singleton
        # If it instantiates, we patch the class.
        
        mock_df = pd.DataFrame([
            {"date": date(2026, 1, 29), "close": 2.762, "currency": "CNY"}
        ])
        
        with patch('src.sync.dsa_sync.MarketDataService') as MockServiceClass:
            mock_service = MockServiceClass.return_value
            mock_service.get_market_data.return_value = mock_df
            
            # 3. Validation - Skip DSA part
            # We patch get_subsystem_path to return None so DSA logic is skipped
            with patch('src.sync.dsa_sync.get_subsystem_path', return_value=None):
                
                # Run Sync
                sync_market_data(connector, {})
                
                # 4. Verify
                # Check DB for inserted record
                result = conn.execute("SELECT close, data_source FROM market_daily WHERE code = '900001' AND date = '2026-01-29'").fetchone()

                assert result is not None, "Record not found in DB"
                assert result[0] == 2.762
                assert result[1] in {'CN_Fund_Excel', 'MarketDataService', 'CN_Fund_Scraper', 'akshare'}
                
                # Check that service was called
                mock_service.get_market_data.assert_called_with(
                    "CN_FUND_900001", 
                    start_date=date(2025, 1, 1), # Assuming default start date or logic
                    end_date=date.today()
                )

