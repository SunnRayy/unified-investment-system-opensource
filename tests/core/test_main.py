"""Tests for CLI commands."""
import pytest
from unittest.mock import patch, MagicMock
from io import StringIO
import sys


class TestSyncV3Command:
    def test_sync_v3_calls_orchestrator(self):
        """Should call run_full_sync_v3 when --sync-v3 is passed."""
        with patch('main.load_config') as mock_config, \
             patch('main.DatabaseConnector') as mock_connector, \
             patch('main.initialize_schema'), \
             patch('main.bootstrap_database'), \
             patch('main.run_full_sync_v3') as mock_sync:

            mock_config.return_value = {
                'database': {'path': ':memory:'},
                'sources': {'pis': {}},
                'validation': {}
            }
            mock_conn_instance = MagicMock()
            mock_connector.return_value = mock_conn_instance
            mock_sync.return_value = MagicMock(
                success=True,
                transactions_synced=10,
                holdings_synced=5,
                market_records_synced=100,
                allocations_synced=3,
                taxonomy_created=2,
                taxonomy_updated=1,
                cost_basis_discrepancies=0,
                allocation_drifts=0,
                warnings=[]
            )

            # Import main and call with args
            import main
            with patch.object(sys, 'argv', ['main.py', '--sync-v3']):
                main.main()

            mock_sync.assert_called_once()
            call_args = mock_sync.call_args
            assert call_args[0][0] == mock_conn_instance  # connector

    def test_sync_v3_shows_warnings(self):
        """Should display warnings from sync result."""
        with patch('main.load_config') as mock_config, \
             patch('main.DatabaseConnector') as mock_connector, \
             patch('main.initialize_schema'), \
             patch('main.bootstrap_database'), \
             patch('main.run_full_sync_v3') as mock_sync:

            mock_config.return_value = {
                'database': {'path': ':memory:'},
                'sources': {'pis': {}},
                'validation': {}
            }
            mock_connector.return_value = MagicMock()
            mock_sync.return_value = MagicMock(
                success=True,
                transactions_synced=10,
                holdings_synced=5,
                market_records_synced=100,
                allocations_synced=3,
                cost_basis_discrepancies=2,
                allocation_drifts=1,
                warnings=['Found 2 cost basis discrepancies', 'Found 1 allocation drifts']
            )

            captured_output = StringIO()
            with patch.object(sys, 'stdout', captured_output):
                import main
                with patch.object(sys, 'argv', ['main.py', '--sync-v3']):
                    main.main()

            output = captured_output.getvalue()
            # Should show warning count
            assert '2' in output or 'discrepancies' in output.lower()

    def test_sync_v3_handles_failure(self):
        """Should show error message when sync fails."""
        with patch('main.load_config') as mock_config, \
             patch('main.DatabaseConnector') as mock_connector, \
             patch('main.initialize_schema'), \
             patch('main.bootstrap_database'), \
             patch('main.run_full_sync_v3') as mock_sync:

            mock_config.return_value = {
                'database': {'path': ':memory:'},
                'sources': {'pis': {}},
                'validation': {}
            }
            mock_connector.return_value = MagicMock()
            mock_sync.return_value = MagicMock(
                success=False,
                error_message='Data too stale',
                warnings=[]
            )

            captured_output = StringIO()
            with patch.object(sys, 'stdout', captured_output):
                import main
                with patch.object(sys, 'argv', ['main.py', '--sync-v3']):
                    main.main()

            output = captured_output.getvalue()
            assert 'stale' in output.lower() or 'error' in output.lower() or 'failed' in output.lower()


# ============================================================================
# BACKUP CLI TESTS (Task 12)
# ============================================================================

class TestBackupCLI:
    """Tests for backup-related CLI commands."""

    def test_cli_backup_flag_creates_backup(self, tmp_path):
        """--backup flag should create a database backup."""
        with patch('main.load_config') as mock_config, \
             patch('main.DatabaseConnector') as mock_connector, \
             patch('main.create_backup') as mock_backup:

            # Setup mocks
            mock_config.return_value = {
                'database': {'path': str(tmp_path / 'test.duckdb')},
            }
            mock_connector.return_value = MagicMock()
            mock_backup.return_value = tmp_path / "backup.duckdb"

            import main
            with patch.object(sys, 'argv', ['main.py', '--backup']):
                main.main()

            # Backup should have been called
            mock_backup.assert_called_once()
            call_kwargs = mock_backup.call_args.kwargs if mock_backup.call_args.kwargs else {}
            # Should have a reason
            assert 'reason' in call_kwargs or len(mock_backup.call_args.args) >= 1

    def test_cli_list_backups_shows_backups(self, tmp_path):
        """--list-backups should display available backups."""
        from datetime import datetime
        from src.database.backup import BackupInfo
        
        with patch('main.load_config') as mock_config, \
             patch('main.DatabaseConnector') as mock_connector, \
             patch('main.list_backups') as mock_list:

            mock_config.return_value = {
                'database': {'path': str(tmp_path / 'test.duckdb')},
            }
            mock_connector.return_value = MagicMock()
            # Mock list_backups to return BackupInfo objects (matching real return type)
            mock_list.return_value = [
                BackupInfo(path=tmp_path / 'backup1.duckdb', timestamp=datetime(2026, 2, 8, 10, 0), reason='pre-sync', size_bytes=1024),
                BackupInfo(path=tmp_path / 'backup2.duckdb', timestamp=datetime(2026, 2, 8, 11, 0), reason='manual', size_bytes=2048),
            ]

            captured_output = StringIO()
            with patch.object(sys, 'stdout', captured_output):
                import main
                with patch.object(sys, 'argv', ['main.py', '--list-backups']):
                    main.main()

            output = captured_output.getvalue()
            # Should show backup info with proper formatting
            assert 'backup' in output.lower() or '2' in output

    def test_cli_sync_v3_includes_schwab_when_enabled(self):
        """--sync-v3 should include Schwab sync when enabled in config."""
        with patch('main.load_config') as mock_config, \
             patch('main.DatabaseConnector') as mock_connector, \
             patch('main.initialize_schema'), \
             patch('main.bootstrap_database'), \
             patch('main.run_full_sync_v3') as mock_sync:

            # Config with Schwab enabled
            mock_config.return_value = {
                'database': {'path': ':memory:'},
                'sources': {'pis': {}},
                'validation': {},
                'source_registry': {
                    'schwab': {
                        'enabled': True,
                        'data_dir': '/tmp/schwab',
                    }
                }
            }
            mock_connector.return_value = MagicMock()
            mock_sync.return_value = MagicMock(
                success=True,
                transactions_synced=10,
                holdings_synced=5,
                market_records_synced=100,
                allocations_synced=3,
                taxonomy_created=0,
                taxonomy_updated=0,
                cost_basis_discrepancies=0,
                allocation_drifts=0,
                warnings=['Schwab sync: 2 holdings, 3 transactions']  # Logged by orchestrator
            )

            captured_output = StringIO()
            with patch.object(sys, 'stdout', captured_output):
                import main
                with patch.object(sys, 'argv', ['main.py', '--sync-v3']):
                    main.main()

            # Orchestrator should have been called with config containing Schwab
            mock_sync.assert_called_once()
            config_passed = mock_sync.call_args[0][1]
            assert config_passed.get('source_registry', {}).get('schwab', {}).get('enabled') is True


class TestRefreshPricesCLI:
    def test_refresh_prices_uses_dsa(self, tmp_path):
        """--refresh-prices should call update_holdings_prices and print DSA count."""
        with patch("main.load_config") as mock_config, \
             patch("main.DatabaseConnector") as mock_connector, \
             patch("src.sync.dsa_sync.update_holdings_prices") as mock_update_prices:

            mock_config.return_value = {
                "database": {"path": str(tmp_path / "test.duckdb")},
                "currency": {"fallback_rates": {"USD_CNY": 7.0, "HKD_CNY": 0.9}},
            }
            mock_conn = MagicMock()
            mock_connector.return_value = mock_conn
            mock_update_prices.return_value = {"dsa": 2}

            captured_output = StringIO()
            with patch.object(sys, "stdout", captured_output):
                import main
                with patch.object(sys, "argv", ["main.py", "--refresh-prices"]):
                    main.main()

            mock_update_prices.assert_called_once_with(
                mock_conn,
                fx_rates={"USD": 7.0, "HKD": 0.9},
            )
            output = captured_output.getvalue()
            assert "Refreshing market prices from DSA" in output
            assert "DSA: 2 rows" in output


class TestRemovedLegacyCLI:
    @pytest.mark.parametrize(
        "legacy_flag",
        ["--sync-all", "--sync", "--sync-transactions", "--sync-holdings", "--backfill-history", "--force-legacy"],
    )
    def test_legacy_flags_are_removed(self, legacy_flag):
        import main

        with patch.object(sys, "argv", ["main.py", legacy_flag]), pytest.raises(SystemExit) as exc:
            main.main()

        assert exc.value.code == 2
