"""Config-engine smoke tests for CN Fund reader (B5 — legacy CNFundReader deleted).

The legacy CNFundReader and cn_fund_transformer were deleted in Workstream B5.
These tests verify the config-driven engine produces correct output on the
real fixture used in production dual-run gate.

CRITICAL SAFETY: The CN Fund config YAML declares pre_read_hook: cn_fund_raw_process
which writes back to the workbook.  Tests must set cfg.parsing.pre_read_hook = None
to prevent mutation of the fixture.
"""
import pytest
from pathlib import Path

from src.sources.config_driven_reader import ConfigDrivenReader
from src.sources.reader_config import load_reader_config

pytestmark = pytest.mark.pipeline

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "readers"
CN_FUND_FIXTURE = FIXTURE_DIR / "funding_transactions.xlsx"
CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "readers"
CN_FUND_YAML = CONFIG_DIR / "cn_fund.yaml"


@pytest.fixture(scope="module")
def cn_fund_outputs():
    cfg = load_reader_config(CN_FUND_YAML)
    # Disable pre_read_hook so the fixture workbook is never mutated
    cfg.parsing.pre_read_hook = None
    reader = ConfigDrivenReader(cfg)
    data = reader.read(CN_FUND_FIXTURE)
    holdings_df, transactions_df = reader.transform(data)
    return holdings_df, transactions_df


class TestCNFundConfigSmoke:
    def test_holdings_non_empty(self, cn_fund_outputs):
        holdings_df, _ = cn_fund_outputs
        assert not holdings_df.empty, "CN Fund holdings must be non-empty from fixture"

    def test_transactions_non_empty(self, cn_fund_outputs):
        _, transactions_df = cn_fund_outputs
        assert not transactions_df.empty, "CN Fund transactions must be non-empty from fixture"

    def test_holdings_asset_ids_start_with_cn_fund(self, cn_fund_outputs):
        holdings_df, _ = cn_fund_outputs
        bad = holdings_df[~holdings_df["asset_id"].str.startswith("CN_FUND_")]
        assert bad.empty, f"All CN Fund asset IDs must start with CN_FUND_, found: {bad['asset_id'].tolist()}"

    def test_transactions_asset_ids_start_with_cn_fund(self, cn_fund_outputs):
        _, transactions_df = cn_fund_outputs
        bad = transactions_df[~transactions_df["asset_id"].str.startswith("CN_FUND_")]
        assert bad.empty, f"All CN Fund txn asset IDs must start with CN_FUND_, found: {bad['asset_id'].tolist()}"

    def test_holdings_required_columns(self, cn_fund_outputs):
        holdings_df, _ = cn_fund_outputs
        required = {"asset_id", "snapshot_date", "market_value", "quantity", "source_system"}
        missing = required - set(holdings_df.columns)
        assert not missing, f"Missing required holdings columns: {missing}"

    def test_holdings_source_system(self, cn_fund_outputs):
        holdings_df, _ = cn_fund_outputs
        assert (holdings_df["source_system"] == "CN_Fund_Excel").all()

    def test_transactions_required_columns(self, cn_fund_outputs):
        _, transactions_df = cn_fund_outputs
        required = {"asset_id", "transaction_date", "transaction_type", "source_system"}
        missing = required - set(transactions_df.columns)
        assert not missing, f"Missing required transaction columns: {missing}"

    def test_pre_read_hook_set_in_yaml(self):
        """The on-disk YAML must declare pre_read_hook (test instance nulls it; YAML has it)."""
        cfg = load_reader_config(CN_FUND_YAML)
        assert cfg.parsing is not None
        assert cfg.parsing.pre_read_hook == "cn_fund_raw_process", (
            "cn_fund.yaml must declare pre_read_hook: cn_fund_raw_process"
        )

    def test_missing_file_returns_empty(self, tmp_path):
        cfg = load_reader_config(CN_FUND_YAML)
        cfg.parsing.pre_read_hook = None
        reader = ConfigDrivenReader(cfg)
        data = reader.read(tmp_path / "nonexistent.xlsx")
        assert data.holdings.empty
        assert data.transactions.empty
