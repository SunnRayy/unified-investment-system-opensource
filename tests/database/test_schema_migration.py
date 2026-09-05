
"""Test schema migration for temporal consolidation columns."""
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema

def test_holdings_has_price_updated_at_column(tmp_path):
    """Holdings table should have price_updated_at column."""
    db_path = tmp_path / "test.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    
    # Run migration (should be idempotent)
    connector.run_migrations()

    # Check column exists
    result = connector.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'holdings' AND column_name = 'price_updated_at'
    """).fetchone()

    assert result is not None, "price_updated_at column should exist in holdings"
    connector.close()

def test_transactions_has_is_provisional_column(tmp_path):
    """Transactions table should have is_provisional column."""
    db_path = tmp_path / "test.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    
    # Run migration
    connector.run_migrations()

    result = connector.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'transactions' AND column_name = 'is_provisional'
    """).fetchone()

    assert result is not None, "is_provisional column should exist in transactions"
    connector.close()

def test_run_migrations_is_idempotent(tmp_path):
    """Running migrations multiple times should not error."""
    db_path = tmp_path / "test.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    
    # Run twice
    connector.run_migrations()
    connector.run_migrations()
    
    result = connector.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'holdings' AND column_name = 'price_updated_at'
    """).fetchone()
    
    assert result is not None
    connector.close()
