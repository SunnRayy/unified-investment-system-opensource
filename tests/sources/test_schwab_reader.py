"""Config-engine smoke tests for Schwab CSV reader (B5 — legacy SchwabReader deleted).

The legacy SchwabReader and schwab_transformer were deleted in Workstream B5.
These tests verify the config-driven engine produces correct output on the
real fixtures used in production dual-run gate.
"""
import pytest
from pathlib import Path

from src.sources.reader_config import load_reader_config

pytestmark = pytest.mark.pipeline

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "readers"
CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "readers"
SCHWAB_YAML = CONFIG_DIR / "schwab.yaml"


@pytest.fixture(scope="module")
def schwab_sync_outputs():
    """Run full sync_schwab via config engine on real fixtures."""
    from src.sync.schwab_sync import sync_schwab
    config = {
        "source_registry": {
            "schwab": {
                "enabled": True,
                "data_dir": str(FIXTURE_DIR),
                "file_patterns": {
                    "positions": "Individual-Positions-*.csv",
                    "transactions": "Individual_*_Transactions_*.csv",
                },
            }
        }
    }
    result = sync_schwab(config)
    return result["holdings"], result["transactions"]


class TestSchwabConfigSmoke:
    def test_holdings_non_empty(self, schwab_sync_outputs):
        holdings_df, _ = schwab_sync_outputs
        assert not holdings_df.empty, "Schwab holdings must be non-empty from fixture"

    def test_transactions_non_empty(self, schwab_sync_outputs):
        _, transactions_df = schwab_sync_outputs
        assert not transactions_df.empty, "Schwab transactions must be non-empty from fixture"

    def test_holdings_required_columns(self, schwab_sync_outputs):
        holdings_df, _ = schwab_sync_outputs
        required = {"asset_id", "snapshot_date", "market_value", "quantity", "source_system"}
        missing = required - set(holdings_df.columns)
        assert not missing, f"Missing required holdings columns: {missing}"

    def test_holdings_source_system(self, schwab_sync_outputs):
        holdings_df, _ = schwab_sync_outputs
        assert (holdings_df["source_system"] == "Schwab_CSV").all()

    def test_holdings_asset_ids_prefixed(self, schwab_sync_outputs):
        """Asset IDs must have US_STK_, US_ETF_, US_BND_, CASH_ or similar prefix."""
        holdings_df, _ = schwab_sync_outputs
        valid_prefixes = ("US_STK_", "US_ETF_", "US_BND_", "US_FUND_", "US_OPT_", "CASH_")
        bad = holdings_df[~holdings_df["asset_id"].apply(
            lambda x: any(x.startswith(p) for p in valid_prefixes)
        )]
        assert bad.empty, f"Unexpected asset ID prefixes: {bad['asset_id'].tolist()}"

    def test_transactions_required_columns(self, schwab_sync_outputs):
        _, transactions_df = schwab_sync_outputs
        required = {"asset_id", "transaction_date", "transaction_type", "source_system"}
        missing = required - set(transactions_df.columns)
        assert not missing, f"Missing required transaction columns: {missing}"

    def test_schwab_config_format_is_csv(self):
        cfg = load_reader_config(SCHWAB_YAML)
        assert cfg.parsing is not None
        assert cfg.parsing.format == "csv"

    def test_holdings_hook_active(self):
        cfg = load_reader_config(SCHWAB_YAML)
        assert cfg.parsing is not None
        assert cfg.parsing.holdings_from_sheet_hook == "schwab_holdings_from_csv"

    def test_transactions_hook_active(self):
        cfg = load_reader_config(SCHWAB_YAML)
        assert cfg.parsing is not None
        assert cfg.parsing.transactions_from_sheet_hook == "schwab_transactions_from_csv"

    def test_missing_file_returns_empty(self, tmp_path):
        from src.sync.schwab_sync import sync_schwab
        config = {
            "source_registry": {
                "schwab": {
                    "enabled": True,
                    "data_dir": str(tmp_path),
                    "file_patterns": {
                        "positions": "Individual-Positions-*.csv",
                        "transactions": "Individual_*_Transactions_*.csv",
                    },
                }
            }
        }
        result = sync_schwab(config)
        assert result["holdings"].empty
        assert result["transactions"].empty

    def test_disabled_returns_empty(self):
        from src.sync.schwab_sync import sync_schwab
        config = {"source_registry": {"schwab": {"enabled": False}}}
        result = sync_schwab(config)
        assert result["holdings"].empty
        assert result["transactions"].empty
