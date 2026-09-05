"""Config-engine smoke tests for Schwab transform output (B5 — legacy schwab_transformer deleted).

The legacy schwab_transformer was deleted in Workstream B5.  These tests verify
that sync_schwab() using the config engine returns correct columns and values.
"""
import pytest
from pathlib import Path

pytestmark = pytest.mark.pipeline

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "readers"
CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "readers"


@pytest.fixture(scope="module")
def schwab_sync_outputs():
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


class TestSchwabTransformHoldings:
    def test_maps_columns(self, schwab_sync_outputs):
        df, _ = schwab_sync_outputs
        assert "asset_id" in df.columns
        assert "source_system" in df.columns
        assert (df["source_system"] == "Schwab_CSV").all()

    def test_non_empty(self, schwab_sync_outputs):
        df, _ = schwab_sync_outputs
        assert not df.empty

    def test_market_value_positive(self, schwab_sync_outputs):
        df, _ = schwab_sync_outputs
        assert (df["market_value"] > 0).all(), "All Schwab market_values must be positive"

    def test_snapshot_date_present(self, schwab_sync_outputs):
        df, _ = schwab_sync_outputs
        assert "snapshot_date" in df.columns
        assert df["snapshot_date"].notna().all()

    def test_quantity_numeric(self, schwab_sync_outputs):
        df, _ = schwab_sync_outputs
        import pandas as pd
        assert pd.api.types.is_numeric_dtype(df["quantity"])


class TestSchwabTransformTransactions:
    def test_maps_columns(self, schwab_sync_outputs):
        _, df = schwab_sync_outputs
        assert "asset_id" in df.columns
        assert "source_system" in df.columns
        assert (df["source_system"] == "Schwab_CSV").all()

    def test_non_empty(self, schwab_sync_outputs):
        _, df = schwab_sync_outputs
        assert not df.empty

    def test_transaction_type_present(self, schwab_sync_outputs):
        _, df = schwab_sync_outputs
        assert "transaction_type" in df.columns
        assert df["transaction_type"].notna().all()
