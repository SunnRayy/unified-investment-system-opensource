"""Tests for auto-classification of registry assets."""
import pytest
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.classification.schema import create_classification_tables


@pytest.fixture
def db():
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    create_classification_tables(connector)
    return connector


@pytest.fixture
def seeded_db(db):
    """DB with classification rules + some assets in registry."""
    # Classes
    db.execute("INSERT INTO taxonomy_classes (id, name, level) VALUES (1, '股票', 0)")
    db.execute("INSERT INTO taxonomy_classes (id, name, level, parent_id) VALUES (2, 'US Equity', 1, 1)")

    # Tiers
    db.execute("INSERT INTO asset_tiers (id, name, target_pct) VALUES ('tier_1_core', 'Core', 50.0)")

    # Rules
    db.execute("""INSERT INTO classification_rules (id, rule_type, pattern, class_id, tier_id, priority)
                  VALUES (1, 'exact_id', 'US_STK_QQQ', 2, 'tier_1_core', 10)""")
    db.execute("""INSERT INTO classification_rules (id, rule_type, pattern, class_id, priority)
                  VALUES (2, 'regex', 'APPLE', 2, 50)""")

    # Assets in registry (no classification yet)
    db.execute("""INSERT INTO asset_registry (canonical_id, display_name, asset_class)
                  VALUES ('US_STK_QQQ', 'INVESCO QQQ', 'US Equity')""")
    db.execute("""INSERT INTO asset_registry (canonical_id, display_name, asset_class)
                  VALUES ('US_STK_AAPL', 'APPLE INC', NULL)""")
    db.execute("""INSERT INTO asset_registry (canonical_id, display_name, asset_class)
                  VALUES ('UNKNOWN_XYZ', 'Random Corp', NULL)""")

    return db


# [MUST-HAVE] Test 1: classify_registry_assets updates assets with exact_id match
def test_classify_exact_id(seeded_db):
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    tagger.classify_registry(seeded_db)
    # QQQ should be classified
    row = seeded_db.execute(
        "SELECT asset_class FROM asset_registry WHERE canonical_id = 'US_STK_QQQ'"
    ).fetchone()
    assert row[0] == 'US Equity'


# [MUST-HAVE] Test 2: classify_registry_assets updates assets with regex match
def test_classify_regex(seeded_db):
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    tagger.classify_registry(seeded_db)
    # AAPL should match "APPLE" regex
    row = seeded_db.execute(
        "SELECT asset_class FROM asset_registry WHERE canonical_id = 'US_STK_AAPL'"
    ).fetchone()
    assert row[0] is not None


# [MUST-HAVE] Test 3: Unmatched assets keep NULL classification
def test_unmatched_stays_null(seeded_db):
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    tagger.classify_registry(seeded_db)
    row = seeded_db.execute(
        "SELECT asset_class FROM asset_registry WHERE canonical_id = 'UNKNOWN_XYZ'"
    ).fetchone()
    # Unclassified: asset_class stays NULL (or stays whatever it was)
    # The auto_tagger should NOT overwrite with "Unclassified" string — just leave as-is
    assert row[0] is None


# Test 4: classify_registry returns count of classified assets
def test_classify_returns_count(seeded_db):
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    result = tagger.classify_registry(seeded_db)
    assert result['classified'] >= 2  # QQQ + AAPL
    assert result['unclassified'] >= 1  # UNKNOWN_XYZ


# Test 5: Audit log populated after classification
def test_audit_log_populated(seeded_db):
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    tagger.classify_registry(seeded_db)
    count = seeded_db.execute("SELECT COUNT(*) FROM classification_audit_log").fetchone()[0]
    assert count >= 2  # At least QQQ + AAPL logged
