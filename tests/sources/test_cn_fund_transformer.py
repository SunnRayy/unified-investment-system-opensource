"""Config-engine smoke tests for CN Fund transform output (B5 — legacy cn_fund_transformer deleted).

The legacy cn_fund_transformer was deleted in Workstream B5.  These tests verify
that the config engine's hooks produce correct holdings and transactions output.

CRITICAL SAFETY: The CN Fund config YAML declares pre_read_hook: cn_fund_raw_process
which writes back to the workbook.  Tests must set cfg.parsing.pre_read_hook = None.
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
    cfg.parsing.pre_read_hook = None  # CRITICAL: do not mutate the fixture
    reader = ConfigDrivenReader(cfg)
    data = reader.read(CN_FUND_FIXTURE)
    return reader.transform(data)


class TestCNFundHoldingsTransformer:
    def test_transform_holdings_schema(self, cn_fund_outputs):
        result, _ = cn_fund_outputs
        expected_columns = {
            "asset_id", "quantity", "market_price_unit", "market_value",
            "snapshot_date", "source_system"
        }
        assert expected_columns.issubset(set(result.columns))

    def test_transform_holdings_source_system(self, cn_fund_outputs):
        result, _ = cn_fund_outputs
        assert (result["source_system"] == "CN_Fund_Excel").all()

    def test_transform_holdings_asset_ids(self, cn_fund_outputs):
        result, _ = cn_fund_outputs
        assert not result.empty
        assert result["asset_id"].str.startswith("CN_FUND_").all()

    def test_transform_holdings_non_empty(self, cn_fund_outputs):
        result, _ = cn_fund_outputs
        assert not result.empty


class TestCNFundTransactionsTransformer:
    def test_transform_transactions_schema(self, cn_fund_outputs):
        _, result = cn_fund_outputs
        expected_columns = {
            "asset_id", "transaction_date", "transaction_type", "source_system"
        }
        assert expected_columns.issubset(set(result.columns))

    def test_transform_transactions_source_system(self, cn_fund_outputs):
        _, result = cn_fund_outputs
        assert (result["source_system"] == "CN_Fund_Excel").all()

    def test_transform_transactions_non_empty(self, cn_fund_outputs):
        _, result = cn_fund_outputs
        assert not result.empty

    def test_transform_transactions_asset_ids(self, cn_fund_outputs):
        _, result = cn_fund_outputs
        assert result["asset_id"].str.startswith("CN_FUND_").all()
