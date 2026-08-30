"""Regression test for the Schwab F1 MoneyLink composite-date fix (B2 sitting #4b).

Schwab MoneyLink cash-transfer rows carry a composite date string
"MM/DD/YYYY as of MM/DD/YYYY" (posting date " as of " effective date). Before the
fix, pd.to_datetime could not parse it, so _to_date returned None and
_normalize_transactions_df silently dropped ~16 external cash-flow rows (~$141K),
corrupting XIRR. The fix takes the posting date (the part before " as of ").
"""
import datetime

import pytest

from src.sync.phases._common import _to_date


class TestToDateMoneyLinkComposite:
    def test_composite_date_takes_posting_date(self):
        # posting date is BEFORE " as of "; effective date after is discarded
        assert _to_date("04/13/2026 as of 04/10/2026") == datetime.date(2026, 4, 13)
        assert _to_date("12/11/2025 as of 12/10/2025") == datetime.date(2025, 12, 11)

    def test_simple_date_unchanged(self):
        # MoneyLink rows that already had a plain date must keep parsing identically
        assert _to_date("07/21/2025") == datetime.date(2025, 7, 21)

    def test_iso_date_unchanged(self):
        assert _to_date("2026-04-13") == datetime.date(2026, 4, 13)

    @pytest.mark.parametrize("bad", ["not a date", None, "as of"])
    def test_unparseable_still_none(self, bad):
        # the fix must not start returning bogus dates for genuine garbage.
        # ("" → NaT is a pre-existing pandas quirk, unchanged by this fix and
        #  out of scope — empty strings have no " as of " to strip.)
        assert _to_date(bad) is None
