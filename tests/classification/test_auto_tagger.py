"""Tests for AutoTagger classification engine."""
import pytest
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.classification.schema import create_classification_tables
from src.classification.models import ClassificationResult


@pytest.fixture
def db():
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    create_classification_tables(connector)
    return connector


@pytest.fixture
def seeded_db(db):
    """DB with taxonomy classes + classification rules."""
    # Create classes
    db.execute("INSERT INTO taxonomy_classes (id, name, level, parent_id) VALUES (1, '股票', 0, NULL)")
    db.execute("INSERT INTO taxonomy_classes (id, name, level, parent_id) VALUES (2, 'CN Equity', 1, 1)")
    db.execute("INSERT INTO taxonomy_classes (id, name, level, parent_id) VALUES (3, 'US Equity', 1, 1)")
    db.execute("INSERT INTO taxonomy_classes (id, name, level, parent_id) VALUES (4, '固定收益', 0, NULL)")
    db.execute("INSERT INTO taxonomy_classes (id, name, level, parent_id) VALUES (5, '美国政府债券', 1, 4)")
    db.execute("INSERT INTO taxonomy_classes (id, name, level, parent_id) VALUES (6, '加密货币', 1, NULL)")

    # Create tiers
    db.execute("INSERT INTO asset_tiers (id, name, target_pct) VALUES ('tier_1_core', 'Core', 50.0)")
    db.execute("INSERT INTO asset_tiers (id, name, target_pct) VALUES ('tier_2_diversification', 'Diversification', 35.0)")

    # Classification rules
    # Exact ID matches (priority 10)
    db.execute("""INSERT INTO classification_rules (id, rule_type, pattern, class_id, tier_id, priority)
                  VALUES (1, 'exact_id', 'US_STK_QQQ', 3, 'tier_1_core', 10)""")
    db.execute("""INSERT INTO classification_rules (id, rule_type, pattern, class_id, tier_id, priority)
                  VALUES (2, 'exact_id', 'US_STK_FBTC', 6, 'tier_2_diversification', 10)""")

    # Exact name matches (priority 20)
    db.execute("""INSERT INTO classification_rules (id, rule_type, pattern, class_id, priority)
                  VALUES (3, 'exact_name', 'INVESCO QQQ TRUST SERIES 1', 3, 20)""")

    # Regex matches (priority 50)
    db.execute("""INSERT INTO classification_rules (id, rule_type, pattern, class_id, priority)
                  VALUES (4, 'regex', 'S&P 500|标普500', 3, 50)""")
    db.execute("""INSERT INTO classification_rules (id, rule_type, pattern, class_id, priority)
                  VALUES (5, 'regex', 'Bond|Treasury|债券', 5, 51)""")
    db.execute("""INSERT INTO classification_rules (id, rule_type, pattern, class_id, tier_id, priority)
                  VALUES (6, 'regex', 'Bitcoin|BTC|Ethereum|ETH|Crypto', 6, 'tier_2_diversification', 52)""")

    return db


# [MUST-HAVE] Test 1: Exact ID match (highest priority)
def test_classify_exact_id_match(seeded_db):
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    result = tagger.classify(asset_id="US_STK_QQQ", asset_name="INVESCO QQQ TRUST")
    assert result.class_id == 3  # US Equity
    assert result.tier_id == "tier_1_core"
    assert result.method == "auto_exact_id"


# [MUST-HAVE] Test 2: Exact name match (second priority)
def test_classify_exact_name_match(seeded_db):
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    # asset_id not in rules, but name matches
    result = tagger.classify(asset_id="US_STK_NEWQQQ", asset_name="INVESCO QQQ TRUST SERIES 1")
    assert result.class_id == 3  # US Equity
    assert result.method == "auto_exact_name"
    assert result.asset_id == "US_STK_NEWQQQ"  # Verify asset_id is set


# [MUST-HAVE] Test 3: Regex match (third priority)
def test_classify_regex_match(seeded_db):
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    result = tagger.classify(asset_id="US_STK_VOO", asset_name="VANGUARD S&P 500 ETF")
    assert result.class_id == 3  # US Equity (via "S&P 500" regex)
    assert result.method == "auto_regex"
    assert result.asset_id == "US_STK_VOO"  # Verify asset_id is set


# [MUST-HAVE] Test 4: No match → unclassified
def test_classify_unclassified(seeded_db):
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    result = tagger.classify(asset_id="UNKNOWN_XYZ", asset_name="Random Thing")
    assert result.class_id is None
    assert result.tier_id is None
    assert result.method == "auto_unclassified"


# [MUST-HAVE] Test 5: Exact ID takes priority over regex
def test_exact_id_beats_regex(seeded_db):
    """Even though FBTC contains 'BTC' (regex match for crypto),
    the exact_id rule should win because it has higher priority."""
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    result = tagger.classify(asset_id="US_STK_FBTC", asset_name="FIDELITY WISE ORIGIN BITCOIN FUND")
    assert result.method == "auto_exact_id"  # Not auto_regex
    assert result.class_id == 6  # 加密货币 (from exact_id rule, not regex)
    assert result.tier_id == "tier_2_diversification"


# Test 6: Regex rules tried in priority order (lower number = first)
def test_regex_priority_order(seeded_db):
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    # "ISHARES 7-10 YEAR TREASURY BOND ETF" matches both "Bond" (priority 51) and shouldn't match S&P
    result = tagger.classify(asset_id="US_STK_IEF", asset_name="ISHARES 7-10 YEAR TREASURY BOND ETF")
    assert result.class_id == 5  # 美国政府债券 (Bond regex, priority 51)


# Test 7: Result is ClassificationResult dataclass
def test_classify_returns_dataclass(seeded_db):
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    result = tagger.classify(asset_id="US_STK_QQQ", asset_name="QQQ")
    assert isinstance(result, ClassificationResult)


# Test 8: classify_batch handles multiple assets
def test_classify_batch(seeded_db):
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    assets = [
        ("US_STK_QQQ", "INVESCO QQQ"),
        ("UNKNOWN_ABC", "Some Unknown Thing"),
        ("US_STK_VOO", "VANGUARD S&P 500 ETF"),
    ]
    results = tagger.classify_batch(assets)
    assert len(results) == 3
    assert results[0].method == "auto_exact_id"
    assert results[1].method == "auto_unclassified"
    assert results[2].method == "auto_regex"


# Test 9: Regex with pipe-separated alternatives works
def test_regex_pipe_alternatives(seeded_db):
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    # "Ethereum" should match "Bitcoin|BTC|Ethereum|ETH|Crypto" regex
    result = tagger.classify(asset_id="US_STK_ETHA", asset_name="ISHARES ETHEREUM TRUST ETF IV")
    assert result.class_id == 6  # 加密货币


# Test 10a: classify_registry also writes tier to asset_registry
def test_classify_registry_writes_tier_to_asset_registry(seeded_db):
    """classify_registry() must update asset_registry.tier when the matched rule has tier_id."""
    from src.classification.auto_tagger import AutoTagger
    seeded_db.execute("INSERT INTO asset_registry (canonical_id, display_name) VALUES ('US_STK_QQQ', 'INVESCO QQQ')")
    tagger = AutoTagger(seeded_db)
    tagger.classify_registry(seeded_db)
    row = seeded_db.execute("SELECT tier FROM asset_registry WHERE canonical_id = 'US_STK_QQQ'").fetchone()
    assert row is not None
    assert row[0] == 'Core'  # tier_1_core has name 'Core' in seeded_db


def test_classify_registry_does_not_clear_existing_tier_when_rule_has_no_tier(seeded_db):
    """classify_registry() must not overwrite an existing tier if the matched rule has no tier_id."""
    from src.classification.auto_tagger import AutoTagger
    # Rule 3 (exact_name) matches 'INVESCO QQQ TRUST SERIES 1' but has no tier_id
    seeded_db.execute("INSERT INTO asset_registry (canonical_id, display_name, tier) VALUES ('US_STK_NEWQQQ', 'INVESCO QQQ TRUST SERIES 1', 'Core')")
    tagger = AutoTagger(seeded_db)
    tagger.classify_registry(seeded_db)
    row = seeded_db.execute("SELECT tier FROM asset_registry WHERE canonical_id = 'US_STK_NEWQQQ'").fetchone()
    # tier should remain unchanged since rule 3 has no tier_id
    assert row[0] == 'Core'


# [MUST-HAVE] Test 10: Regex pattern that MUST NOT match wrong things
def test_regex_no_false_positive(seeded_db):
    """'Bond' regex must NOT match 'James Bond ETF' if it doesn't exist.
    But it SHOULD match 'Treasury Bond'. Verifies regex isn't too greedy."""
    from src.classification.auto_tagger import AutoTagger
    tagger = AutoTagger(seeded_db)
    # This DOES contain "Bond" so it should match
    result = tagger.classify(asset_id="US_STK_TEST", asset_name="SOME TREASURY BOND FUND")
    assert result.class_id == 5  # Matched bond regex
    # A name without any regex match
    result2 = tagger.classify(asset_id="US_STK_TEST2", asset_name="APPLE INC")
    assert result2.method == "auto_unclassified"
