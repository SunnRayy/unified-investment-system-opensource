"""Tests for RiskProfileManager."""
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
    """DB with taxonomy classes + 2 risk profiles."""
    from src.classification.taxonomy_manager import TaxonomyManager
    from src.classification.risk_profile_manager import RiskProfileManager

    # Create top-level classes
    tax_mgr = TaxonomyManager(db)
    equity_id = tax_mgr.create_class(name="股票", level=0, sort_order=1)
    fi_id = tax_mgr.create_class(name="固定收益", level=0, sort_order=2)
    cash_id = tax_mgr.create_class(name="现金", level=0, sort_order=3)

    # Create 2 risk profiles with allocations
    rpm = RiskProfileManager(db)
    p1_id = rpm.create_profile(name="保守型", name_en="Conservative")
    rpm.set_allocations(p1_id, {equity_id: 20.0, fi_id: 50.0, cash_id: 30.0})

    p2_id = rpm.create_profile(name="均衡型", name_en="Balanced", is_active=True)
    rpm.set_allocations(p2_id, {equity_id: 40.0, fi_id: 30.0, cash_id: 30.0})

    return db


# [MUST-HAVE] Test 1: Create profile
def test_create_profile(db):
    from src.classification.risk_profile_manager import RiskProfileManager
    rpm = RiskProfileManager(db)
    profile_id = rpm.create_profile(name="均衡型", name_en="Balanced", is_active=True)
    assert profile_id is not None
    assert isinstance(profile_id, int)


# [MUST-HAVE] Test 2: Get active profile
def test_get_active_profile(seeded_db):
    from src.classification.risk_profile_manager import RiskProfileManager
    rpm = RiskProfileManager(seeded_db)
    active = rpm.get_active_profile()
    assert active is not None
    assert active.name == "均衡型"
    assert active.is_active is True


# [MUST-HAVE] Test 3: Set and get allocations for a profile
def test_set_and_get_allocations(seeded_db):
    from src.classification.risk_profile_manager import RiskProfileManager
    rpm = RiskProfileManager(seeded_db)
    active = rpm.get_active_profile()
    allocations = rpm.get_allocations(active.id)
    # allocations should be a dict: {class_id: target_pct}
    assert len(allocations) == 3
    total = sum(allocations.values())
    assert abs(total - 100.0) < 0.01


# [MUST-HAVE] Test 4: Switch active profile (only one active at a time)
def test_switch_active_profile(seeded_db):
    """Activating one profile deactivates all others."""
    from src.classification.risk_profile_manager import RiskProfileManager
    rpm = RiskProfileManager(seeded_db)

    # Currently 均衡型 is active
    profiles = rpm.get_all_profiles()
    conservative = [p for p in profiles if p.name == "保守型"][0]

    # Switch to conservative
    rpm.activate_profile(conservative.id)

    # Verify
    active = rpm.get_active_profile()
    assert active.name == "保守型"

    # Old active should be inactive
    balanced = [p for p in rpm.get_all_profiles() if p.name == "均衡型"][0]
    assert balanced.is_active is False


# Test 5: Get all profiles
def test_get_all_profiles(seeded_db):
    from src.classification.risk_profile_manager import RiskProfileManager
    rpm = RiskProfileManager(seeded_db)
    profiles = rpm.get_all_profiles()
    assert len(profiles) == 2
    names = [p.name for p in profiles]
    assert "保守型" in names
    assert "均衡型" in names


# Test 6: Get allocations returns RiskProfile dataclass
def test_get_profile_returns_dataclass(seeded_db):
    from src.classification.risk_profile_manager import RiskProfileManager
    from src.classification.models import RiskProfile
    rpm = RiskProfileManager(seeded_db)
    active = rpm.get_active_profile()
    assert isinstance(active, RiskProfile)


# Test 7: No active profile returns None
def test_no_active_profile_returns_none(db):
    from src.classification.risk_profile_manager import RiskProfileManager
    rpm = RiskProfileManager(db)
    rpm.create_profile(name="Test", is_active=False)
    assert rpm.get_active_profile() is None


# Test 8: Set allocations replaces existing (not appends)
def test_set_allocations_replaces(seeded_db):
    """Setting allocations should replace, not duplicate."""
    from src.classification.risk_profile_manager import RiskProfileManager
    from src.classification.taxonomy_manager import TaxonomyManager

    rpm = RiskProfileManager(seeded_db)
    tax_mgr = TaxonomyManager(seeded_db)

    active = rpm.get_active_profile()
    equity = tax_mgr.get_class_by_name("股票")

    # Set new allocations (different values)
    rpm.set_allocations(active.id, {equity.id: 60.0})

    # Should have 1 allocation (replaced, not 3+1)
    allocations = rpm.get_allocations(active.id)
    assert len(allocations) == 1
    assert allocations[equity.id] == 60.0
