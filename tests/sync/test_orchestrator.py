"""Tests for v3 sync orchestrator."""
import pytest

pytestmark = pytest.mark.pipeline

from unittest.mock import patch, MagicMock
import pandas as pd
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.sync.orchestrator import run_full_sync_v3


class TestSyncOrchestrator:
    @pytest.fixture
    def connector(self):
        conn = DatabaseConnector(":memory:")
        initialize_schema(conn)
        yield conn
        conn.close()

    @pytest.fixture
    def mock_config(self):
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

    def test_orchestrator_runs_all_phases(self, connector, mock_config):
        """Should run all sync phases in order.

        Phase 9 note: sync_pis_transactions and sync_holdings_with_cost_basis are no longer
        called by the orchestrator (removed Phase 9, superseded by 6 source readers).
        transactions_synced and holdings_synced remain on SyncResult but are populated
        by reader insertion counts, not PIS legacy functions.
        """
        with patch('src.sync.orchestrator.sync_current_allocations') as mock_alloc, \
             patch('src.sync.orchestrator.validate_cost_basis') as mock_cost, \
             patch('src.sync.orchestrator.validate_allocations') as mock_val_alloc:

            # Setup mocks
            mock_alloc.return_value = {'synced': 3}
            mock_cost.return_value = []
            mock_val_alloc.return_value = []

            result = run_full_sync_v3(connector, mock_config)

            assert result.success is True
            assert result.market_records_synced == 0  # DSA ingest removed (Phase A2)

    def test_orchestrator_does_not_expose_freshness_gate(self):
        """Freshness gate was removed with the PIS-specific validation layer."""
        import src.sync.orchestrator as orch

        assert not hasattr(orch, "check_freshness")

    def test_orchestrator_collects_warnings(self, connector, mock_config):
        """Should collect warnings from validation steps.

        Phase 9 note: sync_pis_transactions and sync_holdings_with_cost_basis are no longer
        called by the orchestrator (removed Phase 9, superseded by 6 source readers).
        """
        with patch('src.sync.orchestrator.sync_current_allocations') as mock_alloc, \
             patch('src.sync.orchestrator.validate_cost_basis') as mock_cost, \
             patch('src.sync.orchestrator.validate_allocations') as mock_val_alloc:

            # Setup mocks with some discrepancies
            mock_alloc.return_value = {'synced': 3}
            mock_cost.return_value = [{'asset_id': 'TEST', 'diff_pct': 5.0}]  # 1 discrepancy
            mock_val_alloc.return_value = [MagicMock()]  # 1 drift

            result = run_full_sync_v3(connector, mock_config)

            assert result.success is True
            assert result.cost_basis_discrepancies == 1
            assert result.allocation_drifts == 1
            assert len(result.warnings) >= 2

    def test_warns_when_enabled_reader_ingests_zero_data(self, connector, mock_config):
        """Should warn loudly when enabled readers ingest 0 holdings and 0 transactions."""
        mock_config["source_registry"] = {"schwab": {"enabled": True, "data_dir": "/no/such/path"}}

        with patch("src.sync.orchestrator.sync_current_allocations") as mock_alloc, \
             patch("src.sync.orchestrator.validate_cost_basis") as mock_cost, \
             patch("src.sync.orchestrator.validate_allocations") as mock_val_alloc, \
             patch("src.sync.orchestrator.sync_schwab") as mock_sync_schwab:

            mock_alloc.return_value = {"synced": 0}
            mock_cost.return_value = []
            mock_val_alloc.return_value = []
            mock_sync_schwab.return_value = {
                "holdings": pd.DataFrame(),
                "transactions": pd.DataFrame(),
            }

            result = run_full_sync_v3(connector, mock_config)

            assert result.success is True
            assert any("All readers enabled but 0 holdings and 0 transactions synced" in w for w in result.warnings)

    def test_no_zero_ingest_alert_when_reader_inserted_rows(self, connector, mock_config):
        """Should not emit zero-ingest alert when at least one enabled reader inserts data."""
        mock_config["source_registry"] = {"schwab": {"enabled": True, "data_dir": "/mock/path"}}

        with patch("src.sync.orchestrator.sync_current_allocations") as mock_alloc, \
             patch("src.sync.orchestrator.validate_cost_basis") as mock_cost, \
             patch("src.sync.orchestrator.validate_allocations") as mock_val_alloc, \
             patch("src.sync.orchestrator.sync_schwab") as mock_sync_schwab:

            mock_alloc.return_value = {"synced": 0}
            mock_cost.return_value = []
            mock_val_alloc.return_value = []
            mock_sync_schwab.return_value = {
                "holdings": pd.DataFrame(
                    [
                        {
                            "asset_id": "US_STK_AAPL",
                            "asset_name": "AAPL",
                            "quantity": 1.0,
                            "market_value": 1000.0,
                            "cost_price_unit": 900.0,
                            "market_price_unit": 1000.0,
                            "snapshot_date": "2026-03-14",
                            "source_system": "Schwab_CSV",
                        }
                    ]
                ),
                "transactions": pd.DataFrame(),
            }

            result = run_full_sync_v3(connector, mock_config)

            assert result.success is True
            assert not any("All readers enabled but 0 holdings and 0 transactions synced" in w for w in result.warnings)

    def test_orchestrator_runs_decision_sync_chain(self, connector, mock_config):
        """Should run trade-log link + backfill + scoring chain in sync-v3."""
        with patch("src.sync.orchestrator.sync_current_allocations") as mock_alloc, \
             patch("src.sync.orchestrator.validate_cost_basis") as mock_cost, \
             patch("src.sync.orchestrator.validate_allocations") as mock_val_alloc, \
             patch("src.sync.orchestrator.link_trade_logs_to_transactions") as mock_link_trades, \
             patch("src.sync.orchestrator.backfill_trade_logs_from_transactions") as mock_backfill_trades, \
             patch("src.sync.orchestrator.score_all_trades") as mock_score_all:

            mock_alloc.return_value = {"synced": 0}
            mock_cost.return_value = []
            mock_val_alloc.return_value = []
            mock_link_trades.return_value = {"verified": 1, "ambiguous": 0, "unmatched": 0}
            mock_backfill_trades.return_value = {"inserted": 1, "skipped_existing": 0, "skipped_type": 0, "attributed": 0}
            mock_score_all.return_value = 6

            result = run_full_sync_v3(connector, mock_config)

            assert result.success is True
            assert mock_link_trades.called
            assert mock_backfill_trades.called
            assert mock_score_all.called
            assert any("Decision sync:" in msg for msg in result.info_messages)


class TestOrchestratorPhase9Cleanup:
    """Phase 9 regression tests: PIS legacy sync functions removed from orchestrator."""

    def test_orchestrator_does_not_import_pis_legacy_functions(self):
        """After Phase 9, orchestrator must not import PIS legacy sync functions.

        sync_holdings_with_cost_basis, sync_pis_transactions, and sync_aia_holdings
        must not be accessible as attributes of the orchestrator module. These were
        superseded by the 6 source readers in Phase 8.
        """
        import importlib
        import src.sync.orchestrator as orch
        importlib.reload(orch)

        assert not hasattr(orch, 'sync_holdings_with_cost_basis'), (
            "sync_holdings_with_cost_basis should not be importable from orchestrator "
            "(removed Phase 9: superseded by 6 source readers)"
        )
        assert not hasattr(orch, 'sync_pis_transactions'), (
            "sync_pis_transactions should not be importable from orchestrator "
            "(removed Phase 9: superseded by 6 source readers)"
        )
        assert not hasattr(orch, 'sync_aia_holdings'), (
            "sync_aia_holdings should not be importable from orchestrator "
            "(removed Phase 9: Schwab CSV reader is authoritative for US positions)"
        )

    def test_reconcile_aia_trades_is_removed(self):
        """AIA provisional trade reconciliation was removed with Phase 5 reconciliation."""
        import src.sync.orchestrator as orch

        assert not hasattr(orch, 'reconcile_aia_trades')


class TestPhase0BackupCloudSkip:
    """Phase-0 backup skips create_backup in cloud mode (UIS_GCS_BUCKET set)."""

    @pytest.fixture
    def connector(self):
        conn = DatabaseConnector(":memory:")
        initialize_schema(conn)
        yield conn
        conn.close()

    def test_cloud_mode_skips_create_backup(self, connector, monkeypatch):
        """When UIS_GCS_BUCKET is set, create_backup is NOT called and info message is appended."""
        monkeypatch.setenv("UIS_GCS_BUCKET", "my-test-bucket")

        from src.sync.orchestrator import _run_phase0_backup_and_setup, SyncResult

        result = SyncResult(success=True)
        with patch("src.sync.orchestrator.create_backup") as mock_backup, \
             patch("src.sync.orchestrator.create_classification_tables"):
            _run_phase0_backup_and_setup(connector, dry_run=False, result=result)

        mock_backup.assert_not_called()
        assert any(
            "Cloud mode" in m and "backup skipped" in m
            for m in result.info_messages
        ), f"Expected cloud-skip message in info_messages; got: {result.info_messages}"

    def test_local_mode_calls_create_backup(self, connector, monkeypatch):
        """When UIS_GCS_BUCKET is unset, create_backup IS called as before."""
        monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)

        from src.sync.orchestrator import _run_phase0_backup_and_setup, SyncResult

        result = SyncResult(success=True)
        with patch("src.sync.orchestrator.create_backup", return_value="/tmp/fake.duckdb") as mock_backup, \
             patch("src.sync.orchestrator.create_classification_tables"):
            _run_phase0_backup_and_setup(connector, dry_run=False, result=result)

        mock_backup.assert_called_once_with(reason="pre-sync-v3")
        assert any("Backup created" in m for m in result.info_messages)

    def test_dry_run_skips_backup_entirely(self, connector, monkeypatch):
        """dry_run=True suppresses the backup regardless of cloud mode."""
        monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)

        from src.sync.orchestrator import _run_phase0_backup_and_setup, SyncResult

        result = SyncResult(success=True)
        with patch("src.sync.orchestrator.create_backup") as mock_backup, \
             patch("src.sync.orchestrator.create_classification_tables"):
            _run_phase0_backup_and_setup(connector, dry_run=True, result=result)

        mock_backup.assert_not_called()


class TestPhase2BackupCloudSkip:
    """Pre-reader-insertion backup skips create_backup in cloud mode."""

    @pytest.fixture
    def connector(self):
        conn = DatabaseConnector(":memory:")
        initialize_schema(conn)
        yield conn
        conn.close()

    @pytest.fixture
    def cloud_config(self):
        """Config with schwab reader enabled so reader_enabled=True."""
        return {
            "sources": {"pis": {}},
            "validation": {
                "freshness": {"enabled": False},
                "cost_basis": {"threshold_pct": 1.0},
                "allocations": {"drift_threshold_pct": 5.0},
            },
            "source_registry": {
                "schwab": {"enabled": True, "data_dir": "/no/such/path"},
            },
        }

    def test_cloud_mode_skips_pre_reader_backup(self, connector, cloud_config, monkeypatch):
        """When UIS_GCS_BUCKET is set, create_backup is NOT called for pre-reader-insertion."""
        monkeypatch.setenv("UIS_GCS_BUCKET", "my-test-bucket")

        from src.sync.orchestrator import _run_phase2_ingest, SyncResult

        result = SyncResult(success=True)
        with patch("src.sync.orchestrator.create_backup") as mock_backup, \
             patch("src.sync.orchestrator._dispatch_phase2_readers", return_value=(0, 0)), \
             patch("src.sync.orchestrator.sync_approved_import_adapters", return_value={}), \
             patch("src.sync.orchestrator._auto_register_new_assets", return_value=0), \
             patch("src.sync.orchestrator._normalize_legacy_prefixes", return_value=0), \
             patch("src.sync.orchestrator._run_reader_id_migration_once", return_value=False):
            _run_phase2_ingest(connector, cloud_config, dry_run=False, result=result)

        mock_backup.assert_not_called()
        assert any(
            "Cloud mode" in m and "backup skipped" in m
            for m in result.info_messages
        ), f"Expected cloud-skip message in info_messages; got: {result.info_messages}"

    def test_local_mode_calls_pre_reader_backup(self, connector, cloud_config, monkeypatch):
        """When UIS_GCS_BUCKET is unset, create_backup IS called for pre-reader-insertion."""
        monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)

        from src.sync.orchestrator import _run_phase2_ingest, SyncResult

        result = SyncResult(success=True)
        with patch("src.sync.orchestrator.create_backup", return_value="/tmp/fake.duckdb") as mock_backup, \
             patch("src.sync.orchestrator._dispatch_phase2_readers", return_value=(0, 0)), \
             patch("src.sync.orchestrator.sync_approved_import_adapters", return_value={}), \
             patch("src.sync.orchestrator._auto_register_new_assets", return_value=0), \
             patch("src.sync.orchestrator._normalize_legacy_prefixes", return_value=0), \
             patch("src.sync.orchestrator._run_reader_id_migration_once", return_value=False):
            _run_phase2_ingest(connector, cloud_config, dry_run=False, result=result)

        mock_backup.assert_called_once_with(reason="pre-reader-insertion")
