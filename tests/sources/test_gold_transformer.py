"""Config-engine smoke tests for Gold transform output (B5 — legacy gold_transformer deleted).

The legacy gold_transformer was deleted in Workstream B5.  These tests verify
that the config engine's transform output has the correct columns, source_system,
and that market_value / quantity are positive numbers.
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
    return reader.transform(data)


class TestGoldTransformHoldings:
    def test_maps_columns(self, gold_outputs):
        df, _ = gold_outputs
        assert "asset_id" in df.columns
        assert "source_system" in df.columns
        assert df["source_system"].iloc[0] == "Gold_Excel"

    def test_non_empty(self, gold_outputs):
        df, _ = gold_outputs
        assert not df.empty

    def test_market_value_positive(self, gold_outputs):
        df, _ = gold_outputs
        assert (df["market_value"] > 0).all(), "All gold market_values must be positive"

    def test_quantity_positive(self, gold_outputs):
        df, _ = gold_outputs
        assert (df["quantity"] > 0).all(), "All gold quantities must be positive"


class TestGoldTransformTransactions:
    def test_maps_columns(self, gold_outputs):
        _, df = gold_outputs
        assert "asset_id" in df.columns
        assert "source_system" in df.columns
        assert df["source_system"].iloc[0] == "Gold_Excel"

    def test_non_empty(self, gold_outputs):
        _, df = gold_outputs
        assert not df.empty
