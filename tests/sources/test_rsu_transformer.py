"""Config-engine smoke tests for RSU transform output (B5 — legacy rsu_transformer deleted).

The legacy rsu_transformer was deleted in Workstream B5.  These tests verify
that the config engine's derive_rsu_holdings hook produces correct holdings.
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
    return reader.transform(data)


class TestRSUTransformTransactions:
    def test_maps_columns(self, rsu_outputs):
        _, df = rsu_outputs
        assert "asset_id" in df.columns
        assert "source_system" in df.columns
        assert df["source_system"].iloc[0] == "RSU_Excel"

    def test_non_empty(self, rsu_outputs):
        _, df = rsu_outputs
        assert not df.empty

    def test_currency_usd(self, rsu_outputs):
        _, df = rsu_outputs
        assert "currency" in df.columns
        assert (df["currency"] == "USD").all()


class TestRSUTransformHoldings:
    def test_derives_holdings_from_transactions(self, rsu_outputs):
        """derive_rsu_holdings hook must produce holdings from transactions."""
        df, _ = rsu_outputs
        assert not df.empty, "Holdings must be derived from RSU transactions"
        assert "asset_id" in df.columns
        assert df["asset_id"].str.startswith("RSU_").all()

    def test_market_value_positive(self, rsu_outputs):
        df, _ = rsu_outputs
        assert "market_value" in df.columns
        assert (df["market_value"] > 0).all()
