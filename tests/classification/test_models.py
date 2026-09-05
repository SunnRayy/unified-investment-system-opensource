"""Tests for classification data models."""


# [MUST-HAVE] Test 1: TaxonomyClass dataclass fields
def test_taxonomy_class_fields():
    from src.classification.models import TaxonomyClass
    tc = TaxonomyClass(
        id=1, name="CN Equity", name_cn="A股",
        parent_id=None, level=0, sort_order=1,
        is_rebalanceable=True, description="Chinese equities"
    )
    assert tc.id == 1
    assert tc.name == "CN Equity"
    assert tc.parent_id is None
    assert tc.is_rebalanceable is True


# [MUST-HAVE] Test 2: AssetTier dataclass fields
def test_asset_tier_fields():
    from src.classification.models import AssetTier
    tier = AssetTier(
        id="tier_1_core", name="第一梯队 (底仓/价值型)",
        name_en="Core / Value", target_pct=50.0,
        description="High Sharpe, long-term hold", color="blue", sort_order=1
    )
    assert tier.id == "tier_1_core"
    assert tier.target_pct == 50.0
    assert tier.color == "blue"


# [MUST-HAVE] Test 3: RiskProfile dataclass fields
def test_risk_profile_fields():
    from src.classification.models import RiskProfile
    profile = RiskProfile(
        id=1, name="均衡型", name_en="Balanced",
        is_active=True, description="40% equity, 30% bonds"
    )
    assert profile.is_active is True
    assert profile.name_en == "Balanced"


# Test 4: ClassificationRule dataclass with all rule types
def test_classification_rule_fields():
    from src.classification.models import ClassificationRule
    rule = ClassificationRule(
        id=1, rule_type="exact_id", pattern="QQQ",
        class_id=3, tier_id="tier_1_core", priority=10, source="seed"
    )
    assert rule.rule_type == "exact_id"
    assert rule.priority == 10


# Test 5: ClassificationResult from auto-tagger
def test_classification_result_fields():
    from src.classification.models import ClassificationResult
    result = ClassificationResult(
        asset_id="US_STK_QQQ", class_id=3, tier_id="tier_1_core",
        method="auto_exact_id", confidence=1.0
    )
    assert result.method == "auto_exact_id"
    assert result.confidence == 1.0


# Test 6: ClassificationResult for unclassified asset
def test_classification_result_unclassified():
    from src.classification.models import ClassificationResult
    result = ClassificationResult(
        asset_id="UNKNOWN_XYZ", class_id=None, tier_id=None,
        method="auto_unclassified", confidence=0.0
    )
    assert result.class_id is None
    assert result.method == "auto_unclassified"
