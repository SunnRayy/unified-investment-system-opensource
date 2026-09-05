import pytest
from unittest.mock import patch, MagicMock

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.sync.orchestrator import run_full_sync_v3, SyncResult, _is_no_change_sync

@pytest.fixture
def connector():
    conn = DatabaseConnector(":memory:")
    # Initialize basic schema plus our new sync_audit_reports table
    initialize_schema(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_audit_reports (
            id VARCHAR(36) PRIMARY KEY,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            report_type VARCHAR(20) NOT NULL DEFAULT 'sync',
            net_worth_before DOUBLE,
            net_worth_after DOUBLE,
            net_worth_change_pct DOUBLE,
            asset_count_before INTEGER,
            asset_count_after INTEGER,
            by_source_before JSON,
            by_source_after JSON,
            integrity_passed INTEGER,
            integrity_total INTEGER,
            integrity_checks JSON,
            source_discrepancies JSON,
            reader_counts JSON,
            warnings JSON,
            alert BOOLEAN DEFAULT FALSE,
            is_no_change BOOLEAN DEFAULT FALSE,
            info_messages JSON
        )
    """)
    yield conn
    conn.close()

@pytest.fixture
def mock_config():
    return {
        'sources': {
            'pis': {
                'excel_path': '/mock/path/transactions.xlsx',
                'sqlite_path': '/mock/path/investment.db',
            }
        },
        'validation': {
            'freshness': {'enabled': False},
            'cost_basis': {'threshold_pct': 1.0},
            'allocations': {'drift_threshold_pct': 5.0}
        }
    }

def test_sync_audit_report_is_persisted(connector, mock_config):
    # We want to patch all the internal sync processes but let run_full_sync_v3 call persist_sync_audit
    with patch('src.sync.orchestrator.sync_current_allocations'), \
         patch('src.sync.orchestrator.validate_cost_basis'), \
         patch('src.sync.orchestrator.validate_allocations'), \
         patch('src.sync.orchestrator.run_integrity_checks') as mock_integrity, \
         patch('src.validation.sync_audit.persist_sync_audit') as mock_persist:

        mock_integrity.return_value = MagicMock(all_passed=True, passed_count=12)
        mock_integrity.return_value.checks = []

        result = run_full_sync_v3(connector, mock_config)

        if not result.success or not result.sync_audit_id:
            assert False, str(result.warnings[-1])
        assert result.success
        # Check that sync_audit_id was assigned
        assert result.sync_audit_id is not None
        
        # Check that persist_sync_audit was called once with the correct types
        assert mock_persist.call_count == 1
        call_args = mock_persist.call_args[0]
        assert call_args[0] == connector # db connection
        
        report = call_args[1]
        assert report.__class__.__name__ == "SyncAuditReport"
        assert report.sync_id == result.sync_audit_id
        assert report.integrity_passed == 12


def test_is_no_change_sync_ignores_processed_row_counts_when_portfolio_state_matches():
    """Repeated imports with identical before/after state should be no-change."""
    diff = {
        "net_worth_before": 5301255.27,
        "net_worth_after": 5301255.27,
        "net_worth_change_pct": 0.0,
        "asset_count_before": 41,
        "asset_count_after": 41,
        "by_source_before": {
            "Schwab_CSV": {"count": 8, "value": 611557.76},
            "CN_Fund_Excel": {"count": 19, "value": 1475848.25},
        },
        "by_source_after": {
            "Schwab_CSV": {"count": 8, "value": 611557.76},
            "CN_Fund_Excel": {"count": 19, "value": 1475848.25},
        },
    }
    result = SyncResult(
        success=True,
        holdings_synced=541,
        transactions_synced=2725,
        warnings=["Found 8 cost basis discrepancies"],
    )

    assert _is_no_change_sync(diff, result) is True


def test_is_no_change_sync_false_when_live_price_refresh_changed_holdings():
    """Same input rows are not enough — actual live price updates make the run meaningful."""
    diff = {
        "net_worth_before": 5301255.27,
        "net_worth_after": 5301255.27,
        "net_worth_change_pct": 0.0,
        "asset_count_before": 41,
        "asset_count_after": 41,
        "by_source_before": {"Schwab_CSV": {"count": 8, "value": 611557.76}},
        "by_source_after": {"Schwab_CSV": {"count": 8, "value": 611557.76}},
    }
    result = SyncResult(success=True, live_price_holdings_updated=3)

    assert _is_no_change_sync(diff, result) is False
