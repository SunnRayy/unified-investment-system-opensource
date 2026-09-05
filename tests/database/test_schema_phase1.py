

def test_holdings_has_shadow_columns():
    """Test that holdings table includes is_shadow and authority_source columns."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema
    
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    
    result = connector.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'holdings'
    """)
    columns = [row[0] for row in result.fetchall()]
    
    assert 'is_shadow' in columns, "is_shadow column missing from holdings"
    assert 'authority_source' in columns, "authority_source column missing from holdings"
    
    connector.close()

def test_source_authority_rules_table_dropped():
    """source_authority_rules must NOT exist after bootstrap (dropped by Migration 16, Pass F)."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import bootstrap_database

    connector = DatabaseConnector(":memory:")
    bootstrap_database(connector)

    # Table must be absent after Migration 16 drop
    result = connector.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'main'
          AND table_name = 'source_authority_rules'
    """)
    assert result.fetchone() is None, \
        "source_authority_rules should have been dropped by Migration 16 (Pass F)"

    connector.close()
