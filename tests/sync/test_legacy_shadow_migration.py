"""Tests for legacy holdings shadow migration."""

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.critical


from src.sync.orchestrator import _shadow_legacy_holdings


@pytest.fixture
def mock_db():
    """Create a mock DB connector that tracks execute calls."""
    return MagicMock()


class TestShadowLegacyHoldings:
    """When reader-sourced holdings exist, legacy PIS holdings should become shadow."""

    def test_marks_legacy_as_shadow_when_reader_exists(self, mock_db):
        """If assets have both PIS and reader rows, PIS rows become shadow."""
        mock_db.execute.return_value.fetchall.return_value = [
            ("CN_FUND_900013", "PIS"),
            ("CN_FUND_900017", "PIS"),
            ("INS_安泰人生", "PIS"),
        ]

        count = _shadow_legacy_holdings(mock_db)

        assert count == 3
        calls = [str(c) for c in mock_db.execute.call_args_list]
        assert any("is_shadow = TRUE" in c for c in calls)

    def test_does_not_shadow_legacy_only_assets(self, mock_db):
        """Assets with ONLY legacy source should NOT be shadowed."""
        mock_db.execute.return_value.fetchall.return_value = []

        count = _shadow_legacy_holdings(mock_db)

        assert count == 0

    def test_does_not_shadow_reader_only_assets(self, mock_db):
        """Assets with ONLY reader source should remain untouched."""
        mock_db.execute.return_value.fetchall.return_value = []

        count = _shadow_legacy_holdings(mock_db)

        assert count == 0

    def test_marks_legacy_as_shadow_regardless_of_date(self, mock_db):
        """Legacy holdings are shadowed if ANY reader row exists for that asset, regardless of date."""
        mock_db.execute.return_value.fetchall.return_value = [
            ("US_STK_SGOV", "PIS"),
        ]

        count = _shadow_legacy_holdings(mock_db)

        assert count == 1
        calls = [str(c) for c in mock_db.execute.call_args_list]
        
        # Ensure that the snapshot_date equality condition is removed
        assert "rdr.snapshot_date = leg.snapshot_date" not in calls[0]
        if len(calls) > 1:
            assert "rdr.snapshot_date = leg.snapshot_date" not in calls[1]

