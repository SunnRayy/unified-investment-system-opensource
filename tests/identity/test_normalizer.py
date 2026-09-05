"""Tests for asset ID normalizer."""
import pytest

pytestmark = pytest.mark.pipeline

from src.identity.normalizer import normalize_asset_id, get_canonical_id


class TestNormalizer:
    def test_normalizes_cn_fund_6_digits(self):
        """Should normalize 6-digit fund code."""
        result = normalize_asset_id("900002", "PIS")
        assert result == "900002"

        canonical = get_canonical_id("900002", "PIS")
        assert canonical == "CN_FUND_900002"

    def test_normalizes_cn_fund_short_code(self):
        """Should pad short fund codes to 6 digits."""
        result = normalize_asset_id("198", "PIS")
        assert result == "000198"

        canonical = get_canonical_id("198", "PIS")
        assert canonical == "CN_FUND_000198"

    def test_normalizes_shanghai_stock(self):
        """Should handle Shanghai stock codes with suffix."""
        # With suffix
        canonical = get_canonical_id("600519.SH", "DSA")
        assert canonical == "CN_STK_600519.SH"

        # Without suffix (6xxxxx pattern)
        canonical = get_canonical_id("600519", "PIS")
        assert canonical == "CN_STK_600519.SH"

    def test_normalizes_shenzhen_stock(self):
        """Should handle Shenzhen stock codes."""
        # 0xxxxx pattern - low numbers are stocks IF suffixed
        canonical = get_canonical_id("000001.SZ", "DSA")
        assert canonical == "CN_STK_000001.SZ"
        
        # 0xxxxx pattern - Ambiguous without suffix, defaults to Fund in new logic
        canonical = get_canonical_id("000001", "DSA")
        assert canonical == "CN_FUND_000001"

        # 3xxxxx pattern (ChiNext)
        canonical = get_canonical_id("300750", "PIS")
        assert canonical == "CN_STK_300750.SZ"

    def test_normalizes_insurance_code(self):
        """Should normalize Ins_ prefix to canonical INS_ form."""
        assert get_canonical_id("Ins_PingAn", "PIS") == "INS_PingAn"
        assert get_canonical_id("INS_PingAn", "PIS") == "INS_PingAn"
        assert get_canonical_id("Insurance_PingAn", "PIS") == "INS_PingAn"

    def test_preserves_property_code(self):
        """Should preserve property codes as-is."""
        canonical = get_canonical_id("Property_BlueCounty", "PIS")
        assert canonical == "Property_BlueCounty"

    def test_handles_us_stock(self):
        """Should handle US stock ticker symbols."""
        canonical = get_canonical_id("AAPL", "PIS")
        assert canonical == "US_STK_AAPL"

        canonical = get_canonical_id("NVDA", "DSA")
        assert canonical == "US_STK_NVDA"
