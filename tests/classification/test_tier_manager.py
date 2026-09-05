"""Tests for TierManager."""
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
    """DB with 3 pre-seeded tiers."""
    from src.classification.tier_manager import TierManager
    mgr = TierManager(db)
    mgr.create_tier(id="tier_1_core", name="第一梯队 (底仓/价值型)",
                    name_en="Core / Value", target_pct=50.0,
                    color="blue", sort_order=1)
    mgr.create_tier(id="tier_2_diversification", name="第二梯队 (辅助/分散)",
                    name_en="Diversification", target_pct=35.0,
                    color="green", sort_order=2)
    mgr.create_tier(id="tier_3_trading", name="第三梯队 (交易/择时)",
                    name_en="Trading", target_pct=15.0,
                    color="orange", sort_order=3)
    return db


# [MUST-HAVE] Test 1: Create tier
def test_create_tier(db):
    from src.classification.tier_manager import TierManager
    mgr = TierManager(db)
    mgr.create_tier(id="tier_1_core", name="Core", target_pct=50.0)
    tier = mgr.get_tier("tier_1_core")
    assert tier is not None
    assert tier.target_pct == 50.0


# [MUST-HAVE] Test 2: Get all tiers sorted
def test_get_all_tiers_sorted(seeded_db):
    from src.classification.tier_manager import TierManager
    mgr = TierManager(seeded_db)
    tiers = mgr.get_all_tiers()
    assert len(tiers) == 3
    assert tiers[0].id == "tier_1_core"
    assert tiers[1].id == "tier_2_diversification"
    assert tiers[2].id == "tier_3_trading"


# [MUST-HAVE] Test 3: Tier target percentages sum to 100
def test_tier_targets_sum_to_100(seeded_db):
    from src.classification.tier_manager import TierManager
    mgr = TierManager(seeded_db)
    tiers = mgr.get_all_tiers()
    total = sum(t.target_pct for t in tiers)
    assert abs(total - 100.0) < 0.01


# Test 4: Update tier target
def test_update_tier_target(seeded_db):
    from src.classification.tier_manager import TierManager
    mgr = TierManager(seeded_db)
    mgr.update_tier("tier_1_core", target_pct=55.0)
    tier = mgr.get_tier("tier_1_core")
    assert tier.target_pct == 55.0


# Test 5: Get tier returns AssetTier dataclass
def test_get_tier_returns_dataclass(seeded_db):
    from src.classification.tier_manager import TierManager
    from src.classification.models import AssetTier
    mgr = TierManager(seeded_db)
    tier = mgr.get_tier("tier_1_core")
    assert isinstance(tier, AssetTier)


# Test 6: Get non-existent tier returns None
def test_get_tier_not_found(db):
    from src.classification.tier_manager import TierManager
    mgr = TierManager(db)
    tier = mgr.get_tier("tier_99_nonexistent")
    assert tier is None
