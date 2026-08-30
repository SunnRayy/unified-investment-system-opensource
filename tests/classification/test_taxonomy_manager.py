"""Tests for TaxonomyManager."""
import pytest
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.classification.schema import create_classification_tables


@pytest.fixture
def db():
    """In-memory DuckDB with classification tables."""
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    create_classification_tables(connector)
    return connector


@pytest.fixture
def seeded_db(db):
    """DB with pre-seeded taxonomy: 2 top-level, 3 sub-classes."""
    from src.classification.taxonomy_manager import TaxonomyManager
    mgr = TaxonomyManager(db)
    # Top-level classes
    equity_id = mgr.create_class(name="股票", name_cn="Equities", level=0, sort_order=1)
    fi_id = mgr.create_class(name="固定收益", name_cn="Fixed Income", level=0, sort_order=2)
    # Sub-classes
    mgr.create_class(name="CN Equity", parent_id=equity_id, level=1, sort_order=1)
    mgr.create_class(name="US Equity", parent_id=equity_id, level=1, sort_order=2)
    mgr.create_class(name="美国政府债券", parent_id=fi_id, level=1, sort_order=1)
    return db


# [MUST-HAVE] Test 1: Create top-level class
def test_create_top_level_class(db):
    from src.classification.taxonomy_manager import TaxonomyManager
    mgr = TaxonomyManager(db)
    class_id = mgr.create_class(name="股票", name_cn="Equities", level=0, sort_order=1)
    assert class_id is not None
    assert isinstance(class_id, int)


# [MUST-HAVE] Test 2: Create sub-class with parent
def test_create_sub_class_with_parent(db):
    from src.classification.taxonomy_manager import TaxonomyManager
    mgr = TaxonomyManager(db)
    parent_id = mgr.create_class(name="股票", level=0)
    child_id = mgr.create_class(name="CN Equity", parent_id=parent_id, level=1)
    assert child_id is not None
    assert child_id != parent_id


# [MUST-HAVE] Test 3: Get full hierarchy tree
def test_get_hierarchy(seeded_db):
    """Returns hierarchical structure: {top_class: [sub_classes]}."""
    from src.classification.taxonomy_manager import TaxonomyManager
    mgr = TaxonomyManager(seeded_db)
    hierarchy = mgr.get_hierarchy()
    # hierarchy is a list of TaxonomyClass with children
    assert len(hierarchy) == 2  # 股票, 固定收益
    equity = [c for c in hierarchy if c.name == "股票"][0]
    children = mgr.get_children(equity.id)
    assert len(children) == 2  # CN Equity, US Equity


# [MUST-HAVE] Test 4: Get class by name
def test_get_class_by_name(seeded_db):
    from src.classification.taxonomy_manager import TaxonomyManager
    mgr = TaxonomyManager(seeded_db)
    cls = mgr.get_class_by_name("CN Equity")
    assert cls is not None
    assert cls.name == "CN Equity"
    assert cls.level == 1


# Test 5: Get class by name returns None for non-existent
def test_get_class_by_name_not_found(seeded_db):
    from src.classification.taxonomy_manager import TaxonomyManager
    mgr = TaxonomyManager(seeded_db)
    cls = mgr.get_class_by_name("Nonexistent Class")
    assert cls is None


# Test 6: Get parent class of a sub-class
def test_get_parent_class(seeded_db):
    from src.classification.taxonomy_manager import TaxonomyManager
    mgr = TaxonomyManager(seeded_db)
    cn_equity = mgr.get_class_by_name("CN Equity")
    parent = mgr.get_parent(cn_equity.id)
    assert parent is not None
    assert parent.name == "股票"


# Test 7: Get all top-level classes
def test_get_top_level_classes(seeded_db):
    from src.classification.taxonomy_manager import TaxonomyManager
    mgr = TaxonomyManager(seeded_db)
    top_level = mgr.get_top_level_classes()
    assert len(top_level) == 2
    names = [c.name for c in top_level]
    assert "股票" in names
    assert "固定收益" in names


# Test 8: Update class
def test_update_class(seeded_db):
    from src.classification.taxonomy_manager import TaxonomyManager
    mgr = TaxonomyManager(seeded_db)
    cn_equity = mgr.get_class_by_name("CN Equity")
    mgr.update_class(cn_equity.id, description="Chinese A-share equities")
    updated = mgr.get_class_by_name("CN Equity")
    assert updated.description == "Chinese A-share equities"


# Test 9: Duplicate name under same parent raises error
def test_create_duplicate_class_raises(seeded_db):
    from src.classification.taxonomy_manager import TaxonomyManager
    mgr = TaxonomyManager(seeded_db)
    equity = mgr.get_class_by_name("股票")
    with pytest.raises(Exception):  # Unique constraint violation
        mgr.create_class(name="CN Equity", parent_id=equity.id, level=1)
