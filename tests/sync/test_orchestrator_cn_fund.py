"""Tests for orchestrator CN Fund sync wiring.
"""
import pytest

pytestmark = pytest.mark.pipeline

from unittest.mock import MagicMock, patch
import pandas as pd
from src.sync.orchestrator import run_full_sync_v3

@pytest.fixture
def mock_connector():
    return MagicMock()

@pytest.fixture
def base_config():
    return {
        'source_registry': {
            'cn_fund': {
                'enabled': True
            }
        },
        'sources': {'pis': {}},
        'validation': {'freshness': {'enabled': False}}
    }

def test_orchestrator_calls_cn_fund_sync(mock_connector, base_config):
    # Mock sync_cn_fund to return dummy DataFrames
    with patch('src.sync.orchestrator.sync_cn_fund') as mock_sync:
        mock_sync.return_value = {
            'holdings': pd.DataFrame([{'id': 1}]),
            'transactions': pd.DataFrame([{'id': 1}, {'id': 2}])
        }
        
        # Other mocks to skip heavy sync steps for speed.
        # Phase 9: sync_pis_transactions, sync_holdings_with_cost_basis, sync_aia_holdings,
        # sync_target_allocations, sync_tier_assignments are no longer imported in the orchestrator.
        with patch('src.sync.orchestrator.create_backup'), \
             patch('src.sync.orchestrator.create_classification_tables'), \
             patch('src.sync.orchestrator.sync_asset_registry'), \
             patch('src.sync.orchestrator.sync_current_allocations'), \
             patch('src.sync.orchestrator.validate_cost_basis', return_value=[]), \
             patch('src.sync.orchestrator.validate_allocations', return_value=[]):

            result = run_full_sync_v3(mock_connector, base_config)

            # ADR-023 WS-C: the orchestrator now injects the merged UI-managed
            # 操作类型 -> transaction_type vocabulary via extra_metadata. The
            # mock connector has no reader_mappings table, so the loader falls
            # back to the code defaults (CN_FUND_TYPE_MAP_SEED).
            from src.database.mapping_seeds import CN_FUND_TYPE_MAP_SEED
            mock_sync.assert_called_once_with(
                base_config,
                extra_metadata={"cn_fund_type_map": {k: v for k, v in CN_FUND_TYPE_MAP_SEED.items()}},
            )
            assert any("CN Fund sync: 1 holdings, 2 transactions" in w for w in result.info_messages)

def test_orchestrator_skips_cn_fund_if_disabled(mock_connector, base_config):
    base_config['source_registry']['cn_fund']['enabled'] = False
    
    with patch('src.sync.orchestrator.sync_cn_fund') as mock_sync:
        # Mocks same as above
        # Phase 9: sync_pis_transactions, sync_holdings_with_cost_basis, sync_aia_holdings,
        # sync_target_allocations, sync_tier_assignments are no longer imported in the orchestrator.
        with patch('src.sync.orchestrator.create_backup'), \
             patch('src.sync.orchestrator.create_classification_tables'), \
             patch('src.sync.orchestrator.sync_asset_registry'), \
             patch('src.sync.orchestrator.sync_current_allocations'), \
             patch('src.sync.orchestrator.validate_cost_basis', return_value=[]), \
             patch('src.sync.orchestrator.validate_allocations', return_value=[]):

            run_full_sync_v3(mock_connector, base_config)
            mock_sync.assert_not_called()

def test_orchestrator_cn_fund_error_handling(mock_connector, base_config):
    # Phase 9: sync_pis_transactions, sync_holdings_with_cost_basis, sync_aia_holdings,
    # sync_target_allocations, sync_tier_assignments are no longer imported in the orchestrator.
    with patch('src.sync.orchestrator.sync_cn_fund', side_effect=Exception("Sync failed")):
        with patch('src.sync.orchestrator.create_backup'), \
             patch('src.sync.orchestrator.create_classification_tables'), \
             patch('src.sync.orchestrator.sync_asset_registry'), \
             patch('src.sync.orchestrator.sync_current_allocations'), \
             patch('src.sync.orchestrator.validate_cost_basis', return_value=[]), \
             patch('src.sync.orchestrator.validate_allocations', return_value=[]):

            result = run_full_sync_v3(mock_connector, base_config)
            assert any("CN Fund sync error: Sync failed" in w for w in result.warnings)
