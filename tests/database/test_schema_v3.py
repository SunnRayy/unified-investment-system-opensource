"""Tests for v3 schema additions."""
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema


def test_schema_has_asset_registry_table():
    """Asset registry table should exist after schema init."""
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    result = connector.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = 'asset_registry'
    """).fetchone()

    assert result is not None
    connector.close()


def test_schema_has_asset_source_mappings_table():
    """Asset source mappings table should exist after schema init."""
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    result = connector.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = 'asset_source_mappings'
    """).fetchone()

    assert result is not None
    connector.close()


def test_schema_has_schema_snapshots_table_dropped():
    """schema_snapshots must NOT exist after bootstrap (dropped by Migration 16, Pass F)."""
    connector = DatabaseConnector(":memory:")
    from src.database.schema import bootstrap_database
    bootstrap_database(connector)

    result = connector.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = 'schema_snapshots'
    """).fetchone()

    assert result is None, "schema_snapshots should have been dropped by Migration 16"
    connector.close()


def test_schema_has_asset_taxonomy_table_dropped():
    """asset_taxonomy must NOT exist after bootstrap (dropped by Migration 16, Pass F).

    taxonomy_classes is the live replacement for classification hierarchy.
    """
    connector = DatabaseConnector(":memory:")
    from src.database.schema import bootstrap_database
    bootstrap_database(connector)

    result = connector.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = 'asset_taxonomy'
    """).fetchone()

    assert result is None, "asset_taxonomy should have been dropped by Migration 16"
    connector.close()


def test_schema_has_current_allocations_table():
    """Current allocations table should exist after schema init."""
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    result = connector.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = 'current_allocations'
    """).fetchone()

    assert result is not None
    connector.close()
