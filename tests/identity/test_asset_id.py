# tests/test_asset_id.py
import pytest

pytestmark = pytest.mark.pipeline

from src.utils.asset_id import normalize_asset_id, AssetIdNormalizer

class TestNormalizeAssetId:
    """Test asset ID normalization for different formats."""

    def test_short_numeric_id_padded_to_6_digits(self):
        """198 should become 000198"""
        assert normalize_asset_id("198") == "000198"
        assert normalize_asset_id("1") == "000001"
        assert normalize_asset_id("2") == "000002"

    def test_already_6_digit_id_unchanged(self):
        """900013 should stay 900013"""
        assert normalize_asset_id("900013") == "900013"
        assert normalize_asset_id("900001") == "900001"

    def test_longer_numeric_id_unchanged(self):
        """7-digit or longer IDs should not be padded"""
        assert normalize_asset_id("1234567") == "1234567"

    def test_non_numeric_id_unchanged(self):
        """Non-numeric IDs like insurance should not be modified"""
        assert normalize_asset_id("Ins_支付宝保险") == "Ins_支付宝保险"
        assert normalize_asset_id("CASH_CNY") == "CASH_CNY"
        assert normalize_asset_id("RSU_COMPANY") == "RSU_COMPANY"

    def test_mixed_alphanumeric_unchanged(self):
        """Mixed alphanumeric IDs should not be modified"""
        assert normalize_asset_id("CN0001") == "CN0001"
        assert normalize_asset_id("US123") == "US123"

    def test_handles_int_input(self):
        """Should handle integer input"""
        assert normalize_asset_id(198) == "000198"
        assert normalize_asset_id(900013) == "900013"

    def test_handles_none_and_empty(self):
        """Should handle None and empty strings gracefully"""
        assert normalize_asset_id(None) == ""
        assert normalize_asset_id("") == ""


class TestAssetIdNormalizer:
    """Test the normalizer class with DataFrame operations."""

    def test_normalize_dataframe_column(self):
        """Should normalize a pandas DataFrame column."""
        import pandas as pd
        normalizer = AssetIdNormalizer()

        df = pd.DataFrame({
            'asset_id': ['198', '900013', '1', 'Ins_Test'],
            'name': ['A', 'B', 'C', 'D']
        })

        result = normalizer.normalize_column(df, 'asset_id')

        assert list(result['asset_id']) == ['000198', '900013', '000001', 'Ins_Test']
