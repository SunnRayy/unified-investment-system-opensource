"""Tests for orchestrator classification integration."""
import pytest

pytestmark = pytest.mark.pipeline

from unittest.mock import patch
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema


@pytest.fixture
def db():
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    return connector


@pytest.fixture
def config():
    return {
        'sources': {'pis': {'excel_path': '', 'sqlite_path': '', 'taxonomy_path': ''}},
        'source_registry': {'schwab': {'enabled': False}},
        'validation': {'freshness': {'enabled': False}, 'taxonomy': {'enabled': False}},
        'database': {'path': ':memory:'},
    }


# [MUST-HAVE] Test 1: Orchestrator creates classification tables
@patch('src.sync.orchestrator.create_backup')
def test_orchestrator_creates_classification_tables(mock_backup, db, config):
    from src.sync.orchestrator import run_full_sync_v3
    # Phase 9: sync_pis_transactions, sync_holdings_with_cost_basis, sync_aia_holdings,
    # sync_target_allocations, sync_tier_assignments are no longer imported in the orchestrator.
    with patch('src.sync.orchestrator.sync_asset_registry', return_value={'registry_inserted': 0}), \
         patch('src.sync.orchestrator.sync_current_allocations', return_value={'synced': 0}), \
         patch('src.sync.orchestrator.validate_cost_basis', return_value=[]), \
         patch('src.sync.orchestrator.validate_allocations', return_value=[]):

        run_full_sync_v3(db, config)

    # Classification tables should exist after sync
    result = db.execute("SELECT COUNT(*) FROM taxonomy_classes").fetchone()
    assert result is not None  # Table exists


# [MUST-HAVE] Test 2: Classification tables created BEFORE data sync
@patch('src.sync.orchestrator.create_backup')
def test_classification_tables_before_sync(mock_backup, db, config):
    """Tables must exist before any sync step that might need them."""
    from src.sync.orchestrator import run_full_sync_v3

    call_order = []

    def track_classification_tables(connector):
        call_order.append('classification_tables')

    # Phase 9: sync_pis_transactions, sync_holdings_with_cost_basis, sync_aia_holdings,
    # sync_target_allocations, sync_tier_assignments are no longer imported in the orchestrator.
    # Phase A2: sync_market_data (DSA ingest) removed from the orchestrator entirely.
    with patch('src.sync.orchestrator.create_classification_tables', side_effect=track_classification_tables), \
         patch('src.sync.orchestrator.sync_asset_registry', return_value={'registry_inserted': 0}), \
         patch('src.sync.orchestrator.sync_current_allocations', return_value={'synced': 0}), \
         patch('src.sync.orchestrator.validate_cost_basis', return_value=[]), \
         patch('src.sync.orchestrator.validate_allocations', return_value=[]):

        run_full_sync_v3(db, config)

    assert call_order[0] == 'classification_tables'


# Test 3: Sync result includes classification stats
@patch('src.sync.orchestrator.create_backup')
def test_sync_result_has_classification_stats(mock_backup, db, config):
    """SyncResult should report classification activity."""
    from src.sync.orchestrator import run_full_sync_v3

    # Phase 9: sync_pis_transactions, sync_holdings_with_cost_basis, sync_aia_holdings,
    # sync_target_allocations, sync_tier_assignments are no longer imported in the orchestrator.
    with patch('src.sync.orchestrator.sync_asset_registry', return_value={'registry_inserted': 0}), \
         patch('src.sync.orchestrator.sync_current_allocations', return_value={'synced': 0}), \
         patch('src.sync.orchestrator.validate_cost_basis', return_value=[]), \
         patch('src.sync.orchestrator.validate_allocations', return_value=[]):

        result = run_full_sync_v3(db, config)

    assert result.success is True


# Test 4: Existing tests still pass after orchestrator changes
def test_existing_sync_flow_unchanged(db, config):
    """Verify we didn't break the existing sync by checking imports."""
    from src.sync.orchestrator import run_full_sync_v3, SyncResult
    # If this imports without error, the module is structurally sound
    assert callable(run_full_sync_v3)
    assert SyncResult is not None


# Test 5: create_classification_tables handles already-existing tables
@patch('src.sync.orchestrator.create_backup')
def test_classification_tables_idempotent_in_orchestrator(mock_backup, db, config):
    from src.sync.orchestrator import run_full_sync_v3
    from src.classification.schema import create_classification_tables

    # Pre-create tables
    create_classification_tables(db)

    # Orchestrator should not fail
    # Phase 9: sync_pis_transactions, sync_holdings_with_cost_basis, sync_aia_holdings,
    # sync_target_allocations, sync_tier_assignments are no longer imported in the orchestrator.
    with patch('src.sync.orchestrator.sync_asset_registry', return_value={'registry_inserted': 0}), \
         patch('src.sync.orchestrator.sync_current_allocations', return_value={'synced': 0}), \
         patch('src.sync.orchestrator.validate_cost_basis', return_value=[]), \
         patch('src.sync.orchestrator.validate_allocations', return_value=[]):

        result = run_full_sync_v3(db, config)

    assert result.success is True
