"""Tests for classification schema creation."""
import pytest
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema


@pytest.fixture
def db():
    """In-memory DuckDB with base schema."""
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    return connector


# [MUST-HAVE] Test 1: All 6 tables created
def test_create_classification_tables_creates_all_six(db):
    """All 6 new tables must exist after migration."""
    from src.classification.schema import create_classification_tables
    create_classification_tables(db)

    expected_tables = [
        'taxonomy_classes', 'asset_tiers', 'risk_profiles',
        'risk_profile_allocations', 'classification_rules',
        'classification_audit_log'
    ]
    for table in expected_tables:
        result = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        assert result is not None, f"Table {table} does not exist"


# [MUST-HAVE] Test 2: Idempotent — running twice doesn't error
def test_create_classification_tables_idempotent(db):
    """Running migration twice should not raise errors."""
    from src.classification.schema import create_classification_tables
    create_classification_tables(db)
    create_classification_tables(db)  # Second call should be safe


# [MUST-HAVE] Test 3: taxonomy_classes has correct columns
def test_taxonomy_classes_columns(db):
    """taxonomy_classes table has all expected columns."""
    from src.classification.schema import create_classification_tables
    create_classification_tables(db)

    # Insert a top-level class to verify column types
    db.execute("""
        INSERT INTO taxonomy_classes (id, name, name_cn, parent_id, level, sort_order, is_rebalanceable)
        VALUES (1, '股票', 'Equities', NULL, 0, 1, TRUE)
    """)
    row = db.execute("SELECT * FROM taxonomy_classes WHERE id = 1").fetchone()
    assert row is not None
    assert row[1] == '股票'  # name
    assert row[3] is None    # parent_id (top-level)
    assert row[4] == 0       # level


# [MUST-HAVE] Test 4: risk_profile_allocations enforces unique constraint
def test_risk_profile_allocations_unique_constraint(db):
    """Cannot have two allocations for same profile+class."""
    from src.classification.schema import create_classification_tables
    create_classification_tables(db)

    # Insert a risk profile and class first
    db.execute("INSERT INTO risk_profiles (id, name) VALUES (1, '均衡型')")
    db.execute("INSERT INTO taxonomy_classes (id, name, level) VALUES (1, '股票', 0)")
    db.execute("""
        INSERT INTO risk_profile_allocations (id, profile_id, class_id, target_pct)
        VALUES (1, 1, 1, 40.0)
    """)

    # Duplicate should fail
    with pytest.raises(Exception):
        db.execute("""
            INSERT INTO risk_profile_allocations (id, profile_id, class_id, target_pct)
            VALUES (2, 1, 1, 50.0)
        """)


# Test 5: classification_rules unique constraint (rule_type + pattern)
def test_classification_rules_unique_constraint(db):
    """Cannot have duplicate rule_type + pattern."""
    from src.classification.schema import create_classification_tables
    create_classification_tables(db)

    db.execute("""
        INSERT INTO classification_rules (id, rule_type, pattern, priority)
        VALUES (1, 'exact_id', 'QQQ', 10)
    """)

    with pytest.raises(Exception):
        db.execute("""
            INSERT INTO classification_rules (id, rule_type, pattern, priority)
            VALUES (2, 'exact_id', 'QQQ', 10)
        """)
