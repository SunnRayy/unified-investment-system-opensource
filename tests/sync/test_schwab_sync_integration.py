"""Tests for Schwab sync integration with orchestrator.

TDD: These tests are written FIRST, before the implementation.
Run: pytest tests/sync/test_schwab_sync_integration.py -v
"""
import pytest

pytestmark = pytest.mark.pipeline

from unittest.mock import patch, MagicMock
import pandas as pd


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def mock_schwab_csv_dir(tmp_path):
    """Create temp directory with mock Schwab CSV files."""
    # Positions file
    positions_content = '''"Positions for account Individual ...XXX342 as of 10:11 PM ET, 02/06/2026"

"Symbol","Description","Qty (Quantity)","Price","Price Chng $ (Price Change $)","Price Chng % (Price Change %)","Mkt Val (Market Value)","Day Chng $ (Day Change $)","Day Chng % (Day Change %)","Cost Basis","Gain $ (Gain/Loss $)","Gain % (Gain/Loss %)","Reinvest?","Reinvest Capital Gains?","Security Type"
"QQQ","INVESCO QQQ TRUST SERIES 1","10","$529.78","$3.50","0.66%","$5,297.80","$35.00","0.66%","$4,500.00","$797.80","17.73%","No","N/A","ETF"
"Cash & Cash Investments","--","--","--","--","--","$6,440.00","$0.00","0%","--","--","--","--","--","--"
"Account Total","--","--","--","--","--","$12,714.05","$29.00","0.23%","--","--","--","--","--","--"
'''
    pos_path = tmp_path / "Individual-Positions-2026-02-06.csv"
    pos_path.write_text(positions_content)

    # Transactions file
    trans_content = '''"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"
"02/05/2026","Buy","QQQ","INVESCO QQQ TRUST SERIES 1","2","$529.78","","$-1059.56"
"02/04/2026","Sell","AAPL","APPLE INC","5","$195.25","$0.02","$976.23"
'''
    trans_path = tmp_path / "Individual_XXX342_Transactions_20260206.csv"
    trans_path.write_text(trans_content)

    return tmp_path


@pytest.fixture
def schwab_config(mock_schwab_csv_dir):
    """Config dict with source_registry for Schwab."""
    return {
        'source_registry': {
            'schwab': {
                'enabled': True,
                'data_dir': str(mock_schwab_csv_dir),
                'file_patterns': {
                    'positions': 'Individual-Positions-*.csv',
                    'transactions': 'Individual_*_Transactions_*.csv',
                }
            }
        },
        'sources': {
            'pis': {
                'excel_path': '/nonexistent/path.xlsx',
                'sqlite_path': '/nonexistent/path.db',
            }
        },
        'validation': {
            'freshness': {'enabled': False},
            'taxonomy': {'enabled': False},
        }
    }


# ============================================================================
# SYNC SCHWAB FUNCTION TESTS
# ============================================================================

class TestSyncSchwab:
    """Tests for the sync_schwab() function."""

    def test_sync_schwab_reads_positions(self, mock_schwab_csv_dir):
        """sync_schwab reads positions CSV and returns holdings DataFrame."""
        from src.sync.schwab_sync import sync_schwab

        config = {
            'source_registry': {
                'schwab': {
                    'enabled': True,
                    'data_dir': str(mock_schwab_csv_dir),
                    'file_patterns': {
                        'positions': 'Individual-Positions-*.csv',
                        'transactions': 'Individual_*_Transactions_*.csv',
                    }
                }
            }
        }

        result = sync_schwab(config)

        # Should have holdings data
        assert 'holdings' in result
        assert not result['holdings'].empty
        
        # Should include QQQ
        assert 'US_ETF_QQQ' in result['holdings']['asset_id'].values

    def test_sync_schwab_includes_cash(self, mock_schwab_csv_dir):
        """Cash balance appears as CASH_USD holding."""
        from src.sync.schwab_sync import sync_schwab

        config = {
            'source_registry': {
                'schwab': {
                    'enabled': True,
                    'data_dir': str(mock_schwab_csv_dir),
                    'file_patterns': {
                        'positions': 'Individual-Positions-*.csv',
                        'transactions': 'Individual_*_Transactions_*.csv',
                    }
                }
            }
        }

        # CASH_USD market_value now uses the live USD/CNY rate (not the legacy
        # hardcoded 7.0). Pin the FX source so the assertion is deterministic.
        with patch(
            "src.market_data.fetchers.yfinance_fetcher.fetch_fx_rates",
            return_value={"USD": 7.0, "HKD": 0.9},
        ):
            result = sync_schwab(config)

        # Should have CASH_USD
        cash = result['holdings'][result['holdings']['asset_id'] == 'CASH_USD']
        assert len(cash) == 1
        assert cash.iloc[0]['market_value'] == 6440.00 * 7.0

    def test_sync_schwab_reads_transactions(self, mock_schwab_csv_dir):
        """sync_schwab reads transactions CSV."""
        from src.sync.schwab_sync import sync_schwab

        config = {
            'source_registry': {
                'schwab': {
                    'enabled': True,
                    'data_dir': str(mock_schwab_csv_dir),
                    'file_patterns': {
                        'positions': 'Individual-Positions-*.csv',
                        'transactions': 'Individual_*_Transactions_*.csv',
                    }
                }
            }
        }

        result = sync_schwab(config)

        # Should have transactions data
        assert 'transactions' in result
        assert not result['transactions'].empty
        
        # Should have buy and sell types
        types = set(result['transactions']['transaction_type'])
        assert 'buy' in types
        assert 'sell' in types

    def test_sync_schwab_disabled(self):
        """When disabled, sync_schwab returns empty result."""
        from src.sync.schwab_sync import sync_schwab

        config = {
            'source_registry': {
                'schwab': {
                    'enabled': False,
                }
            }
        }

        result = sync_schwab(config)

        assert result['holdings'].empty
        assert result['transactions'].empty

    def test_sync_schwab_accumulates_multiple_files(self, mock_schwab_csv_dir):
        """sync_schwab reads all matching CSVs and concatenates their holdings and transactions."""
        from src.sync.schwab_sync import sync_schwab

        # Add a second positions file for a different date
        pos2_content = '''"Positions for account Individual ...XXX342 as of 10:11 PM ET, 02/07/2026"

"Symbol","Description","Qty (Quantity)","Price","Price Chng $ (Price Change $)","Price Chng % (Price Change %)","Mkt Val (Market Value)","Day Chng $ (Day Change $)","Day Chng % (Day Change %)","Cost Basis","Gain $ (Gain/Loss $)","Gain % (Gain/Loss %)","Reinvest?","Reinvest Capital Gains?","Security Type"
"AAPL","APPLE INC","5","$200.00","$0.00","0.00%","$1000.00","$0.00","0.00%","$800.00","$200.00","25.0%","No","N/A","Common Stock"
"Account Total","--","--","--","--","--","$1000.00","$0.00","0.00%","--","--","--","--","--","--"
'''
        pos2_path = mock_schwab_csv_dir / "Individual-Positions-2026-02-07.csv"
        pos2_path.write_text(pos2_content)

        # Add a second transactions file
        trans2_content = '''"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"
"02/07/2026","Cash Dividend","AAPL","APPLE INC","0","$0.00","$0.00","$5.00"
'''
        trans2_path = mock_schwab_csv_dir / "Individual_XXX342_Transactions_20260207.csv"
        trans2_path.write_text(trans2_content)

        config = {
            'source_registry': {
                'schwab': {
                    'enabled': True,
                    'data_dir': str(mock_schwab_csv_dir),
                    'file_patterns': {
                        'positions': 'Individual-Positions-*.csv',
                        'transactions': 'Individual_*_Transactions_*.csv',
                    }
                }
            }
        }

        result = sync_schwab(config)

        # Holdings use only the latest positions file (2026-02-07)
        holdings = result['holdings']
        dates = set(holdings['snapshot_date'].tolist())
        assert '2026-02-07' in dates
        assert len(holdings) >= 1  # AAPL from latest file

        # Transactions are accumulated from all transaction files
        transactions = result['transactions']
        tx_dates = set(transactions['transaction_date'].tolist())
        assert '2026-02-07' in tx_dates  # From second file
        assert len(transactions) >= 3  # 2 from first, 1 from second

    def test_sync_schwab_source_system_set(self, mock_schwab_csv_dir):
        """All holdings/transactions have source_system='Schwab_CSV'."""
        from src.sync.schwab_sync import sync_schwab

        config = {
            'source_registry': {
                'schwab': {
                    'enabled': True,
                    'data_dir': str(mock_schwab_csv_dir),
                    'file_patterns': {
                        'positions': 'Individual-Positions-*.csv',
                        'transactions': 'Individual_*_Transactions_*.csv',
                    }
                }
            }
        }

        result = sync_schwab(config)

        assert (result['holdings']['source_system'] == 'Schwab_CSV').all()
        assert (result['transactions']['source_system'] == 'Schwab_CSV').all()

    def test_uses_finance_dir_when_data_dir_null(self, tmp_path):
        """When data_dir is null, falls back to finance_dir (iCloud), not PIS path."""
        # Create a fake finance_dir with a positions CSV using the real Schwab column format.
        # SchwabReader skips 2 rows (metadata + blank), then reads header on row 3.
        finance_dir = tmp_path / "Finance"
        finance_dir.mkdir()
        positions_csv = finance_dir / "Individual-Positions-2026-01-01.csv"
        positions_csv.write_text(
            '"Positions for account Individual ...XXXX-1234 as of 10:11 PM ET, 01/01/2026"\n'
            "\n"
            '"Symbol","Description","Qty (Quantity)","Price",'
            '"Price Chng $ (Price Change $)","Price Chng % (Price Change %)",'
            '"Mkt Val (Market Value)","Day Chng $ (Day Change $)","Day Chng % (Day Change %)",'
            '"Cost Basis","Gain $ (Gain/Loss $)","Gain % (Gain/Loss %)","Reinvest?","Reinvest Capital Gains?","Security Type"\n'
            '"AAPL","APPLE INC","10","$175.00","$0.87","0.50%","$1,750.00","$8.75","0.50%","$1,500.00","$250.00","16.67%","No","N/A","Common Stock"\n'
            '"Account Total","--","--","--","--","--","$1,750.00","$8.75","0.50%","--","--","--","--","--","--"\n'
        )

        config = {
            'source_registry': {
                'schwab': {
                    'enabled': True,
                    'data_dir': None,  # null — should fall back to finance_dir
                    'file_patterns': {
                        'positions': 'Individual-Positions-*.csv',
                        'transactions': 'Individual_*_Transactions_*.csv',
                    },
                }
            },
            'finance_dir': str(finance_dir),  # iCloud path
            # No sources.pis fallback config
            # No subsystems.pis.path
        }

        from src.sync.schwab_sync import sync_schwab
        result = sync_schwab(config)
        assert result['holdings'] is not None
        assert len(result['holdings']) > 0, "Should have read from finance_dir"


# ============================================================================
# ORCHESTRATOR INTEGRATION TESTS
# ============================================================================

class TestOrchestratorSchwabIntegration:
    """Tests for orchestrator integration with Schwab sync."""

    def test_orchestrator_creates_backup_before_sync(self, tmp_path, mock_schwab_csv_dir):
        """Orchestrator creates backup before running sync."""
        from src.sync.orchestrator import run_full_sync_v3

        # Mock the connector
        mock_connector = MagicMock()
        mock_connector.execute.return_value = None
        mock_connector.query.return_value = pd.DataFrame()

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        config = {
            'source_registry': {
                'schwab': {
                    'enabled': True,
                    'data_dir': str(mock_schwab_csv_dir),
                    'file_patterns': {
                        'positions': 'Individual-Positions-*.csv',
                        'transactions': 'Individual_*_Transactions_*.csv',
                    }
                }
            },
            'sources': {'pis': {}},
            'validation': {'freshness': {'enabled': False}, 'taxonomy': {'enabled': False}},
            'database': {'path': str(tmp_path / 'test.duckdb')},
        }

        # Patch create_backup to track if it was called
        with patch('src.sync.orchestrator.create_backup') as mock_backup:
            mock_backup.return_value = backup_dir / "unified_backup.duckdb"
            
            # Run sync (will fail on various things but should call backup first)
            try:
                run_full_sync_v3(mock_connector, config)
            except Exception:
                pass  # We only care that backup was attempted

            # Backup should have been called for pre-sync, and for reader insertion.
            assert mock_backup.call_count >= 1
            reasons = [call.kwargs.get("reason") for call in mock_backup.call_args_list]
            assert "pre-sync-v3" in reasons

    def test_schwab_sync_idempotent(self, mock_schwab_csv_dir):
        """Running sync twice produces same count (no duplicates)."""
        from src.sync.schwab_sync import sync_schwab

        config = {
            'source_registry': {
                'schwab': {
                    'enabled': True,
                    'data_dir': str(mock_schwab_csv_dir),
                    'file_patterns': {
                        'positions': 'Individual-Positions-*.csv',
                        'transactions': 'Individual_*_Transactions_*.csv',
                    }
                }
            }
        }

        # Run sync twice
        result1 = sync_schwab(config)
        result2 = sync_schwab(config)

        # Same counts each time (no accumulation)
        assert len(result1['holdings']) == len(result2['holdings'])
        assert len(result1['transactions']) == len(result2['transactions'])

    def test_schwab_coexists_with_pis_sources(self, mock_schwab_csv_dir):
        """Schwab holdings have different source_system than PIS holdings would."""
        from src.sync.schwab_sync import sync_schwab

        config = {
            'source_registry': {
                'schwab': {
                    'enabled': True,
                    'data_dir': str(mock_schwab_csv_dir),
                    'file_patterns': {
                        'positions': 'Individual-Positions-*.csv',
                        'transactions': 'Individual_*_Transactions_*.csv',
                    }
                }
            }
        }

        result = sync_schwab(config)

        # Schwab holdings have source_system='Schwab_CSV'
        # This is distinct from PIS which uses 'PIS_Excel' or 'PIS_SQLite'
        assert (result['holdings']['source_system'] == 'Schwab_CSV').all()
        
        # Asset IDs use US prefixes (distinct from CN_FUND_* used by PIS)
        asset_ids = result['holdings']['asset_id'].tolist()
        us_prefixes = ['US_ETF_', 'US_STK_', 'CASH_USD']
        for aid in asset_ids:
            assert any(aid.startswith(p) or aid == p for p in us_prefixes), f"Unexpected asset_id: {aid}"
