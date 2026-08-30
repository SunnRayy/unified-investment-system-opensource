"""Config-engine smoke tests for Insurance transform output (B5 — legacy insurance_transformer deleted).

The legacy insurance_transformer was deleted in Workstream B5.  These tests verify
that the config engine's transform output has the correct columns, source_system,
and that market_value is present.
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
    return reader.transform(data)


class TestInsuranceTransformHoldings:
    def test_maps_columns(self, insurance_outputs):
        df, _ = insurance_outputs
        assert "asset_id" in df.columns
        assert "source_system" in df.columns
        assert df["source_system"].iloc[0] == "Insurance_Excel"

    def test_non_empty(self, insurance_outputs):
        df, _ = insurance_outputs
        assert not df.empty

    def test_market_value_present(self, insurance_outputs):
        df, _ = insurance_outputs
        assert "market_value" in df.columns


class TestInsuranceTransformTransactions:
    def test_maps_columns(self, insurance_outputs):
        _, df = insurance_outputs
        assert "asset_id" in df.columns
        assert "source_system" in df.columns
        assert df["source_system"].iloc[0] == "Insurance_Excel"

    def test_non_empty(self, insurance_outputs):
        _, df = insurance_outputs
        assert not df.empty
