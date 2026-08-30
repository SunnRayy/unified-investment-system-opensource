"""Tests for shared transaction source selection logic."""
import pytest

pytestmark = pytest.mark.critical

from unittest.mock import MagicMock


def test_select_sources_prefers_reader_over_legacy():
    """When both Schwab_CSV and PIS sources exist, prefer Schwab_CSV."""
    from src.services.transaction_source_selector import select_transaction_sources

    db = MagicMock()
    # Transaction sources: Schwab_CSV and PIS_SQLite
    db.execute.side_effect = [
        MagicMock(fetchall=MagicMock(return_value=[("Schwab_CSV",), ("PIS_SQLite",)])),
        MagicMock(fetchall=MagicMock(return_value=[("Schwab_CSV",)])),
    ]
    result = select_transaction_sources(db, "US_STK_AAPL")
    assert "Schwab_CSV" in result
    assert "PIS_SQLite" not in result


def test_select_sources_treats_aia_as_legacy_when_schwab_present(conn):
    """AIA is in LEGACY_TRANSACTION_SOURCES, so Schwab_CSV is preferred over it."""
    conn.execute(
        """
        INSERT INTO holdings (snapshot_date, asset_id, market_value, source_system, is_shadow)
        VALUES ('2026-01-10', 'US_STK_IEF', 1000, 'Schwab_CSV', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO transactions (transaction_date, asset_id, transaction_type, amount_gross, source_system)
        VALUES
          ('2026-01-10', 'US_STK_IEF', 'sell', 1000, 'AIA'),
          ('2026-01-10', 'US_STK_IEF', 'sell', 1000, 'Schwab_CSV')
        """
    )
    from src.services.transaction_source_selector import select_transaction_sources

    result = select_transaction_sources(conn, "US_STK_IEF")
    assert "AIA" not in result
    assert "Schwab_CSV" in result


def test_select_sources_returns_empty_for_no_transactions():
    """Asset with no transactions returns empty list."""
    from src.services.transaction_source_selector import select_transaction_sources

    db = MagicMock()
    db.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))
    result = select_transaction_sources(db, "UNKNOWN_ASSET")
    assert result == []


def test_is_realized_pnl_exempt_by_prefix():
    """Assets with exempt prefixes should be flagged."""
    from src.services.transaction_source_selector import is_realized_pnl_exempt

    db = MagicMock()
    assert is_realized_pnl_exempt(db, "Pension_Personal") is True
    assert is_realized_pnl_exempt(db, "INS_AIA_Growth") is True
    assert is_realized_pnl_exempt(db, "CASH_USD") is True
    assert is_realized_pnl_exempt(db, "US_STK_AAPL") is False


def test_is_realized_pnl_exempt_bank_wealth_by_asset_class():
    """Bank Wealth products are cash-equivalent and should suppress realized P&L.

    Regression test for UNKNOWN_BankWealth_招行 showing ¥-100,058 in Cash class
    due to PIS phantom Adjustment_Sell at price=0.
    """
    from src.services.transaction_source_selector import is_realized_pnl_exempt

    db = MagicMock()
    db.execute.return_value = MagicMock(fetchone=MagicMock(return_value=("Bank Wealth",)))
    assert is_realized_pnl_exempt(db, "UNKNOWN_BankWealth_招行") is True


@pytest.fixture
def mock_db_with_mixed_dates():
    """Mock DB for QDII test where assets have different max snapshot dates."""
    db = MagicMock()
    
    def mock_execute(query, params=None):
        if "FROM transactions" in query:
            return MagicMock(fetchall=MagicMock(return_value=[("CN_Fund_Excel",)]))
        elif "WITH latest_snap" in query:
            # We are testing what happens when the correct query is run.
            # If the buggy query (no asset_id in CTE) is run, it would return empty.
            # But here we just mock the return value of the whole fetchall() assuming
            # the query gives the right result, because this is an integration-style logic test.
            # To strictly test the SQL change, we simulate the SQL engine.
            
            # The original test spec asks us to assert the behavior of the python function.
            # So if it asks for CN_FUND, we return its source if the logic is correct.
            # We will just unconditionally return the source here for the mock, BUT
            # to make it actually fail with the old code and pass with the new code,
            # we need a real DuckDB interaction or a smart mock.
            # Since the plan says "TDD: Write the QDII test first in tests/services/test_transaction_source_selector.py",
            # we can use an in-memory DuckDB for this specific test to catch the SQL bug.
            pass
            
        return MagicMock(fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=None))
    
    db.execute.side_effect = mock_execute
    return db


def test_qdii_asset_gets_own_source_not_excluded_by_later_dated_asset():
    """
    QDII fund's latest snapshot is 2026-03-04 (T+2 lag).
    Another asset (AAPL) has a snapshot at 2026-03-06 (today).

    With global MAX: QDII is excluded from authority_rows (its date != global max 2026-03-06)
    → source returned would be [] or fallback, not CN_Fund_Excel.

    With per-asset MAX: QDII's own max is 2026-03-04, so it finds its CN_Fund_Excel row.
    → source returned is ["CN_Fund_Excel"] (correct).
    """
    import duckdb
    from src.services.transaction_source_selector import select_transaction_sources
    
    # Use real in-memory duckdb to test SQL logic
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE holdings (asset_id VARCHAR, source_system VARCHAR, snapshot_date DATE, is_shadow BOOLEAN)")
    conn.execute("CREATE TABLE transactions (asset_id VARCHAR, source_system VARCHAR)")
    
    # Seed data
    conn.execute("INSERT INTO holdings VALUES ('CN_FUND_900015', 'CN_Fund_Excel', '2026-03-04', false)")
    conn.execute("INSERT INTO holdings VALUES ('US_STK_AAPL', 'Schwab_CSV', '2026-03-06', false)")
    conn.execute("INSERT INTO transactions VALUES ('CN_FUND_900015', 'CN_Fund_Excel')")
    
    class FakeDBConnector:
        def execute(self, query, params=None):
            if params:
                return conn.execute(query, params)
            return conn.execute(query)
            
    db = FakeDBConnector()
    
    sources = select_transaction_sources(db, "CN_FUND_900015")
    assert sources == ["CN_Fund_Excel"], (
        f"QDII fund should get CN_Fund_Excel via its own snapshot date, got {sources}. "
        "Global MAX(snapshot_date) bug: QDII excluded because its date != latest asset's date."
    )
