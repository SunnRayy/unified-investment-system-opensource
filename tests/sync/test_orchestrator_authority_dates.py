"""Tests for orchestrator applying authority rules across reader snapshot dates."""

from unittest.mock import MagicMock, patch
import pytest

pytestmark = pytest.mark.pipeline

from datetime import date
from src.sync.orchestrator import run_full_sync_v3

@patch('src.sync.orchestrator.sync_asset_registry')
@patch('src.sync.orchestrator.sync_current_allocations')
@patch('src.sync.orchestrator.validate_cost_basis')
@patch('src.sync.orchestrator.validate_allocations')
@patch('src.identity.authority_resolver.AuthorityResolver')
@patch('src.sync.holdings_aggregator.HoldingsAggregator')
def test_authority_applied_for_all_reader_dates(
    MockAggregator, MockResolver,
    mock_val_alloc, mock_val_cost,
    mock_sync_alloc,
    mock_sync_registry
):
    """Authority should run for reader snapshot dates, not only date.today()."""
    connector = MagicMock()
    config = {
        'sources': {
            'pis': {'excel_path': 'dummy.xlsx', 'sqlite_path': 'dummy.db'}
        },
        'validation': {
            'freshness': {'enabled': False},
            'taxonomy': {'enabled': False}
        }
    }
    
    # Mock connector.execute for snapshot dates
    # We want to return a list of dates when orchestrator queries for distinct snapshot dates
    def execute_side_effect(query, params=None):
        mock_cursor = MagicMock()
        if "SELECT DISTINCT snapshot_date" in query:
            mock_cursor.fetchall.return_value = [(date(2026, 2, 27),), (date(2026, 3, 2),)]
        else:
            mock_cursor.fetchall.return_value = []
            mock_cursor.fetchone.return_value = [0]
        return mock_cursor
        
    connector.execute.side_effect = execute_side_effect
    
    mock_aggregator_instance = MockAggregator.return_value
    
    run_full_sync_v3(connector, config)
    
    # Ensure apply_authority_rules is called for dates from DB, AND potentially today
    calls = mock_aggregator_instance.apply_authority_rules.call_args_list
    assert len(calls) >= 2
    
    called_dates = [c[0][1] for c in calls]
    assert date(2026, 2, 27) in called_dates
    assert date(2026, 3, 2) in called_dates
    
