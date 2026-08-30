"""Config-engine smoke tests for RSU reader (B5 — legacy RSUReader deleted).

The legacy RSUReader and rsu_transformer were deleted in Workstream B5.
These tests verify the config-driven engine produces correct output on the
real fixture used in production dual-run gate.
"""
import pytest
from pathlib import Path

from src.sources.config_driven_reader import ConfigDrivenReader
from src.sources.reader_config import load_reader_config

pytestmark = pytest.mark.pipeline

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "readers"
RSU_FIXTURE = FIXTURE_DIR / "RSU_transactions.xlsx"
CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "readers"
RSU_YAML = CONFIG_DIR / "rsu.yaml"


@pytest.fixture(scope="module")
def rsu_outputs():
    cfg = load_reader_config(RSU_YAML)
    reader = ConfigDrivenReader(cfg)
    data = reader.read(RSU_FIXTURE)
    holdings_df, transactions_df = reader.transform(data)
    return holdings_df, transactions_df


class TestRSUConfigSmoke:
    def test_transactions_non_empty(self, rsu_outputs):
        _, transactions_df = rsu_outputs
        assert not transactions_df.empty, "RSU transactions must be non-empty from fixture"

    def test_transactions_asset_ids_start_with_rsu(self, rsu_outputs):
        _, transactions_df = rsu_outputs
        bad = transactions_df[~transactions_df["asset_id"].str.startswith("RSU_")]
        assert bad.empty, f"All RSU asset IDs must start with RSU_, found: {bad['asset_id'].tolist()}"

    def test_transactions_required_columns(self, rsu_outputs):
        _, transactions_df = rsu_outputs
        required = {"asset_id", "transaction_date", "transaction_type", "source_system"}
        missing = required - set(transactions_df.columns)
        assert not missing, f"Missing required transaction columns: {missing}"

    def test_transactions_source_system(self, rsu_outputs):
        _, transactions_df = rsu_outputs
        assert (transactions_df["source_system"] == "RSU_Excel").all()

    def test_holdings_non_empty(self, rsu_outputs):
        holdings_df, _ = rsu_outputs
        assert not holdings_df.empty, "RSU holdings must be non-empty (derive_rsu_holdings hook)"

    def test_holdings_asset_ids_start_with_rsu(self, rsu_outputs):
        holdings_df, _ = rsu_outputs
        bad = holdings_df[~holdings_df["asset_id"].str.startswith("RSU_")]
        assert bad.empty, "All RSU holdings asset IDs must start with RSU_"

    def test_missing_file_returns_empty(self, tmp_path):
        cfg = load_reader_config(RSU_YAML)
        reader = ConfigDrivenReader(cfg)
        data = reader.read(tmp_path / "nonexistent.xlsx")
        assert data.transactions.empty
