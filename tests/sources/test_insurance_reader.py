"""Config-engine smoke tests for Insurance reader (B5 — legacy InsuranceReader deleted).

The legacy InsuranceReader and insurance_transformer were deleted in Workstream B5.
These tests verify the config-driven engine produces correct output on the
real fixture used in production dual-run gate.
"""
import pytest
from pathlib import Path

from src.sources.config_driven_reader import ConfigDrivenReader
from src.sources.reader_config import load_reader_config

pytestmark = pytest.mark.pipeline

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "readers"
INSURANCE_FIXTURE = FIXTURE_DIR / "Insurance_Portfolio.xlsx"
CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "readers"
INSURANCE_YAML = CONFIG_DIR / "insurance.yaml"


@pytest.fixture(scope="module")
def insurance_outputs():
    cfg = load_reader_config(INSURANCE_YAML)
    reader = ConfigDrivenReader(cfg)
    data = reader.read(INSURANCE_FIXTURE)
    holdings_df, transactions_df = reader.transform(data)
    return holdings_df, transactions_df


class TestInsuranceConfigSmoke:
    def test_holdings_non_empty(self, insurance_outputs):
        holdings_df, _ = insurance_outputs
        assert not holdings_df.empty, "Insurance holdings must be non-empty from fixture"

    def test_transactions_non_empty(self, insurance_outputs):
        _, transactions_df = insurance_outputs
        assert not transactions_df.empty, "Insurance transactions must be non-empty from fixture"

    def test_holdings_asset_ids_start_with_ins(self, insurance_outputs):
        holdings_df, _ = insurance_outputs
        bad = holdings_df[~holdings_df["asset_id"].str.startswith("INS_")]
        assert bad.empty, f"All insurance asset IDs must start with INS_, found: {bad['asset_id'].tolist()}"

    def test_holdings_required_columns(self, insurance_outputs):
        holdings_df, _ = insurance_outputs
        required = {"asset_id", "snapshot_date", "market_value", "source_system"}
        missing = required - set(holdings_df.columns)
        assert not missing, f"Missing required holdings columns: {missing}"

    def test_holdings_source_system(self, insurance_outputs):
        holdings_df, _ = insurance_outputs
        assert (holdings_df["source_system"] == "Insurance_Excel").all()

    def test_transactions_required_columns(self, insurance_outputs):
        _, transactions_df = insurance_outputs
        # Config insurance reader emits payment_date (id_var renamed in insurance.yaml);
        # the downstream insert phase maps it onto the transactions table.
        required = {"asset_id", "payment_date", "transaction_type", "source_system"}
        missing = required - set(transactions_df.columns)
        assert not missing, f"Missing required transaction columns: {missing}"

    def test_holdings_snapshot_date_format(self, insurance_outputs):
        holdings_df, _ = insurance_outputs
        snap = holdings_df["snapshot_date"].iloc[0]
        assert len(str(snap)) == 10, "snapshot_date must be YYYY-MM-DD"
        assert str(snap)[4] == "-" and str(snap)[7] == "-"

    def test_missing_file_returns_empty(self, tmp_path):
        cfg = load_reader_config(INSURANCE_YAML)
        reader = ConfigDrivenReader(cfg)
        data = reader.read(tmp_path / "nonexistent.xlsx")
        assert data.holdings.empty
        assert data.transactions.empty
