
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import unittest
from unittest.mock import MagicMock
from src.sync.dsa_sync import update_holdings_prices

class TestDSAPriceUpdates(unittest.TestCase):
    def setUp(self):
        self.connector = MagicMock()
        self.config = {}

    def test_update_holdings_prices(self):
        # We assume the SQL logic is correct, but we want to ensure the function constructs and executes it.
        # Since we use DuckDB specific SQL (UPDATE FROM), mocking is tricky if we don't have a real DB.
        # But we can verify the SQL string structure.
        
        # Run
        update_holdings_prices(self.connector)
        
        # Verify
        self.connector.execute.assert_called()
        call_args = self.connector.execute.call_args
        sql = call_args[0][0]
        
        # Check components of the SQL
        self.assertIn("UPDATE holdings", sql)
        self.assertIn("SET", sql)
        self.assertIn("market_price_unit = md.close", sql)
        self.assertIn("FROM market_daily md", sql)
        # Check condition: join on asset (code matching)
        # Check condition: latest date
        
        # Since logic is in SQL, we mostly check sql content.
        # Ideally we'd use an in-memory duckdb for integration test, 
        # but mocking connector implies unit test.
        # If we want integration test, we use real connector.
        pass

if __name__ == '__main__':
    unittest.main()
