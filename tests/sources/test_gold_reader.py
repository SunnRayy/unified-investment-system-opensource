"""Config-engine smoke tests for Gold reader (B5 — legacy GoldReader deleted).

The legacy GoldReader and gold_transformer were deleted in Workstream B5.
These tests verify the config-driven engine produces correct output on the
real fixture used in production dual-run gate.
"""
import pytest
from pathlib import Path

from src.sources.config_driven_reader import ConfigDrivenReader
from src.sources.reader_config import load_reader_config

pytestmark = pytest.mark.pipeline

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "readers"
GOLD_FIXTURE = FIXTURE_DIR / "Gold_transactions.xlsx"
CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "readers"
GOLD_YAML = CONFIG_DIR / "gold.yaml"


@pytest.fixture(scope="module")
def gold_outputs():
    cfg = load_reader_config(GOLD_YAML)
    reader = ConfigDrivenReader(cfg)
    data = reader.read(GOLD_FIXTURE)
    holdings_df, transactions_df = reader.transform(data)
    return holdings_df, transactions_df


class TestGoldConfigSmoke:
    def test_holdings_non_empty(self, gold_outputs):
        holdings_df, _ = gold_outputs
        assert not holdings_df.empty, "Gold holdings must be non-empty from fixture"

    def test_transactions_non_empty(self, gold_outputs):
        _, transactions_df = gold_outputs
        assert not transactions_df.empty, "Gold transactions must be non-empty from fixture"

    def test_holdings_asset_ids_start_with_gold(self, gold_outputs):
        holdings_df, _ = gold_outputs
        bad = holdings_df[~holdings_df["asset_id"].str.startswith("GOLD_")]
        assert bad.empty, f"All gold asset IDs must start with GOLD_, found: {bad['asset_id'].tolist()}"

    def test_no_alts_ids(self, gold_outputs):
        holdings_df, _ = gold_outputs
        alts = holdings_df[holdings_df["asset_id"].str.startswith("ALTS_")]
        assert alts.empty, "ALTS_ IDs must not appear in reader output"

    def test_holdings_required_columns(self, gold_outputs):
        holdings_df, _ = gold_outputs
        required = {"asset_id", "snapshot_date", "market_value", "quantity", "source_system"}
        missing = required - set(holdings_df.columns)
        assert not missing, f"Missing required holdings columns: {missing}"

    def test_holdings_source_system(self, gold_outputs):
        holdings_df, _ = gold_outputs
        assert (holdings_df["source_system"] == "Gold_Excel").all()

    def test_transactions_required_columns(self, gold_outputs):
        _, transactions_df = gold_outputs
        required = {"asset_id", "transaction_date", "transaction_type", "source_system"}
        missing = required - set(transactions_df.columns)
        assert not missing, f"Missing required transaction columns: {missing}"

    def test_holdings_snapshot_date_format(self, gold_outputs):
        holdings_df, _ = gold_outputs
        snap = holdings_df["snapshot_date"].iloc[0]
        assert len(str(snap)) == 10, "snapshot_date must be YYYY-MM-DD"
        assert str(snap)[4] == "-" and str(snap)[7] == "-"

    def test_missing_file_returns_empty(self, tmp_path):
        cfg = load_reader_config(GOLD_YAML)
        reader = ConfigDrivenReader(cfg)
        data = reader.read(tmp_path / "nonexistent.xlsx")
        assert data.holdings.empty
        assert data.transactions.empty
