"""Tests for integrity check #3 (`twr_in_range` — `_check_twr_in_range`).

Plan: this branch (fix/twr-in-range-valuation-v2), diagnosed in
tests/validation/test_integrity_skipped_state.py and
docs/audits/2026-07-26-two-week-retrospective.md §3.

Before this fix, the check's coverage gate required a single global
``snapshot_date`` covering >= 50% of all distinct assets — the exact
global-MAX(snapshot_date) pattern AGENTS.md Rule 3 forbids. Every reader
writes its own assets on its own date, so on the live mirror the gate
needed 0 qualifying snapshot dates ever, and the check permanently
returned ``skipped=True``.

The fix reuses the LOCKED valuation-v2 helper
(``src.services.attribution._latest_snapshot_by_asset``) at two FIXED
anchors — the latest ``snapshot_date`` in ``holdings`` and 365 days
before it — summing each source's own latest per-asset row at or before
the anchor date, instead of requiring one date to cover everything.
"""
from __future__ import annotations

from datetime import date

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.validation.data_integrity_gate import (
    TWR_CHECK_LOOKBACK_DAYS,
    _check_twr_in_range,
)


@pytest.fixture
def conn():
    db = DatabaseConnector(":memory:")
    initialize_schema(db)
    yield db
    db.close()


def _insert_holding(db, *, snapshot_date, asset_id, market_value, source_system="Schwab_CSV"):
    db.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit, market_value,
            currency, account, source_system, is_shadow
        ) VALUES (?, ?, 'Test Asset', 'ETF', 10.0, 'share', 100.0, 110.0, ?, 'CNY', 'Test', ?, FALSE)
        """,
        (snapshot_date, asset_id, market_value, source_system),
    )


def _old_coverage_gate_qualifying_dates(db) -> int:
    """Re-implements the PRE-FIX coverage-gate SQL verbatim (global
    snapshot_date covering >= 50% of distinct assets), to empirically prove
    a given fixture really would have starved the old algorithm — rather
    than just asserting it by argument.
    """
    total_assets_row = db.execute(
        "SELECT COUNT(DISTINCT asset_id) FROM holdings WHERE is_shadow = FALSE"
    ).fetchone()
    total_assets = int(total_assets_row[0]) if total_assets_row else 0
    min_coverage = max(1, total_assets // 2)
    rows = db.execute(
        """
        SELECT snapshot_date, COUNT(DISTINCT asset_id) AS asset_count
        FROM holdings
        WHERE is_shadow = FALSE AND market_value > 0
        GROUP BY snapshot_date
        HAVING COUNT(DISTINCT asset_id) >= ?
        """,
        (min_coverage,),
    ).fetchall()
    return len(rows)


def test_check3_evaluates_normal_365_day_window(conn):
    """Happy path: a value at d_start and a value at d_end (exactly
    TWR_CHECK_LOOKBACK_DAYS apart) yields a non-skipped, in-band result."""
    assert TWR_CHECK_LOOKBACK_DAYS == 365
    d_end = date(2026, 7, 25)
    d_start = date(2025, 7, 25)  # exactly 365 days earlier

    _insert_holding(conn, snapshot_date=d_start, asset_id="US_STK_TEST", market_value=1_000_000.0)
    _insert_holding(conn, snapshot_date=d_end, asset_id="US_STK_TEST", market_value=1_200_000.0)

    result = _check_twr_in_range(conn)

    assert result.name == "twr_in_range"
    assert result.skipped is False, f"Should have evaluated, not skipped: {result.details}"
    assert result.passed is True
    # (1,200,000 / 1,000,000) ** (365/365) - 1 == 0.20
    assert "20.0% annualized" in result.actual_value
    assert "v_start=¥1,000,000@2025-07-25" in result.actual_value
    assert "v_end=¥1,200,000@2026-07-25" in result.actual_value
    assert "365d" in result.actual_value


def test_check3_detects_extreme_return(conn):
    """A 10x jump over the 365-day window must fail the -80%/+200% band —
    this is the historical +912% double-counting bug this check exists for."""
    d_end = date(2026, 7, 25)
    d_start = date(2025, 7, 25)

    _insert_holding(conn, snapshot_date=d_start, asset_id="US_STK_TEST", market_value=100_000.0)
    _insert_holding(conn, snapshot_date=d_end, asset_id="US_STK_TEST", market_value=1_000_000.0)

    result = _check_twr_in_range(conn)

    assert result.skipped is False
    assert result.passed is False, "10x in 365 days (900% annualized) must exceed the +200% ceiling"
    assert "900.0%" in result.actual_value


def test_check3_legitimate_skip_when_no_data_before_window_start(conn):
    """A fresh-ish DB whose only holding is recent (younger than the 365-day
    lookback) must SKIP honestly, not fabricate a return from zero."""
    _insert_holding(conn, snapshot_date=date(2026, 7, 25), asset_id="US_STK_TEST", market_value=500_000.0)

    result = _check_twr_in_range(conn)

    assert result.name == "twr_in_range"
    assert result.skipped is True
    assert result.passed is True, "a legitimate skip must not be reported as a failure"
    assert result.actual_value == "insufficient_data"
    assert "2025-07-25" in result.details  # honest reason names the missing anchor date


def test_check3_legitimate_skip_on_empty_database(conn):
    """No holdings at all (fresh DB) must skip, not error or fabricate."""
    result = _check_twr_in_range(conn)
    assert result.skipped is True
    assert result.passed is True
    assert result.actual_value == "no_data"


def test_check3_evaluates_per_source_valuation_where_old_gate_would_skip(conn):
    """The core regression test for the fix: a fixture where every asset
    reports on its OWN distinct snapshot_date (no single date covers more
    than one asset). The pre-fix global coverage-gate algorithm would find
    ZERO qualifying dates and skip forever. The new per-(asset, source)
    valuation (via _latest_snapshot_by_asset) must still evaluate cleanly.
    """
    # 4 assets, each with a "start" row (~365+ days back, all on distinct
    # dates) and an "end" row (recent, all on distinct dates, D's row sets
    # the global d_end). No two rows anywhere share a snapshot_date.
    _insert_holding(conn, snapshot_date=date(2025, 1, 5), asset_id="ASSET_A", market_value=100_000.0)
    _insert_holding(conn, snapshot_date=date(2025, 1, 10), asset_id="ASSET_B", market_value=150_000.0)
    _insert_holding(conn, snapshot_date=date(2025, 1, 15), asset_id="ASSET_C", market_value=200_000.0)
    _insert_holding(conn, snapshot_date=date(2025, 1, 20), asset_id="ASSET_D", market_value=250_000.0)

    _insert_holding(conn, snapshot_date=date(2026, 7, 20), asset_id="ASSET_A", market_value=130_000.0)
    _insert_holding(conn, snapshot_date=date(2026, 7, 21), asset_id="ASSET_B", market_value=180_000.0)
    _insert_holding(conn, snapshot_date=date(2026, 7, 22), asset_id="ASSET_C", market_value=210_000.0)
    _insert_holding(conn, snapshot_date=date(2026, 7, 25), asset_id="ASSET_D", market_value=260_000.0)  # global d_end

    # Prove the OLD algorithm really would have starved on this exact fixture.
    assert _old_coverage_gate_qualifying_dates(conn) == 0, (
        "fixture must reproduce the pre-fix vacuous condition (0 qualifying dates) "
        "for this test to prove anything"
    )

    result = _check_twr_in_range(conn)

    assert result.skipped is False, (
        f"new check must evaluate via per-(asset,source) valuation even though "
        f"no single date covers >1 asset: {result.details}"
    )
    # d_start = 2026-07-25 - 365d = 2025-07-25; each asset's own latest row
    # <= that date is its Jan-2025 "start" row (all before 2025-07-25).
    # v_start = 100k+150k+200k+250k = 700,000
    # v_end (<= 2026-07-25) = 130k+180k+210k+260k = 780,000
    # annualized = (780000/700000) ** (365/365) - 1 ≈ 11.43%
    assert result.passed is True
    assert "v_start=¥700,000@2025-07-25" in result.actual_value
    assert "v_end=¥780,000@2026-07-25" in result.actual_value
    assert "11.4% annualized" in result.actual_value


# ─────────────────────────────────────────────────────────────────────────────
# Like-for-like basis + coverage gate (Lead review, 2026-07-26)
#
# The first implementation of this fix summed EVERY asset present at each
# anchor. On the live mirror that read +140.7% annualized — which was not
# return at all but reader ONBOARDING: only Financial_Summary_Excel reached
# back a year, so a 10-asset start anchor was compared against 68 assets
# today. Restricted to the common set, the same window read +10.0%, agreeing
# with the authoritative trailing TWR (10.832%).
#
# These tests pin the two properties that prevent that regression.
# ─────────────────────────────────────────────────────────────────────────────


def test_check3_ignores_assets_absent_at_the_start_anchor(conn):
    """Assets onboarded DURING the window must not be counted as growth.

    The spanning asset deliberately dominates value so the coverage gate is
    satisfied and the INTERSECTION is what is under test. Without it, v_end
    would include the newcomers (¥12.0M + ¥0.9M) against a ¥10.0M start and
    read ~+29%; with it, the answer is the true +20% of the asset that
    actually spans the window.
    """
    d_end = date(2026, 7, 25)
    d_start = date(2025, 7, 25)

    # Present at BOTH anchors, and the bulk of value: +20% over the year.
    _insert_holding(conn, snapshot_date=d_start, asset_id="SPANS_WINDOW", market_value=10_000_000.0)
    _insert_holding(conn, snapshot_date=d_end, asset_id="SPANS_WINDOW", market_value=12_000_000.0)

    # Nine small assets onboarded after d_start — present only at d_end.
    for i in range(9):
        _insert_holding(
            conn, snapshot_date=d_end, asset_id=f"ONBOARDED_{i}", market_value=100_000.0
        )

    result = _check_twr_in_range(conn)

    assert result.skipped is False, f"should evaluate: {result.details}"
    assert "20.0% annualized" in result.actual_value, (
        "onboarded assets leaked into the ratio — like-for-like intersection lost. "
        f"Got: {result.actual_value}"
    )


def test_check3_skips_when_like_for_like_basis_is_too_thin(conn):
    """A like-for-like ratio over a sliver of the portfolio is arithmetically
    valid and substantively meaningless — it must SKIP with a specific reason
    rather than report a confident number about the wrong assets.

    This is the live 2026-07-26 situation at a 365-day window: 10 of 68 assets
    span it, covering 45.7% of value (property/pension/cash, not the
    investment portfolio).
    """
    d_end = date(2026, 7, 25)

    # One small asset spans every candidate window (365/270/180)...
    for d in (date(2025, 7, 25), date(2025, 10, 28), date(2026, 1, 26), d_end):
        _insert_holding(conn, snapshot_date=d, asset_id="TINY_SPANNER", market_value=100_000.0)
    # ...while the bulk of today's value has no history at all.
    _insert_holding(conn, snapshot_date=d_end, asset_id="BIG_NEW", market_value=900_000.0)

    result = _check_twr_in_range(conn)

    assert result.skipped is True, (
        f"a 10% like-for-like basis must skip, not report a number: {result.actual_value}"
    )
    assert "too thin" in result.details
    assert "self-heals" in result.details


def test_check3_prefers_the_longest_window_with_adequate_coverage(conn):
    """When 365d is too thin but a shorter window is well covered, the check
    degrades to the shorter window instead of skipping — and says which it
    used. This is what makes it evaluate today and climb back to 365d on its
    own as reader history accumulates.
    """
    d_end = date(2026, 7, 25)
    d_180 = date(2026, 1, 26)   # ~180 days before d_end

    # Only a tiny asset reaches back a full year.
    _insert_holding(conn, snapshot_date=date(2025, 7, 25), asset_id="TINY", market_value=1_000.0)
    _insert_holding(conn, snapshot_date=d_end, asset_id="TINY", market_value=1_000.0)
    # The bulk of the portfolio has ~180 days of history.
    _insert_holding(conn, snapshot_date=d_180, asset_id="MAIN", market_value=1_000_000.0)
    _insert_holding(conn, snapshot_date=d_end, asset_id="MAIN", market_value=1_100_000.0)

    result = _check_twr_in_range(conn)

    assert result.skipped is False, f"should have fallen back to a shorter window: {result.details}"
    assert "180d" in result.actual_value, (
        f"expected the 180-day fallback to be used and reported. Got: {result.actual_value}"
    )
