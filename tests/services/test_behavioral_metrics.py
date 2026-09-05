"""Unit tests for BehavioralMetricsComputer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services.ai_advisor.behavioral_metrics import (
    BehavioralMetricsComputer,
    MetricResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_computer(db_path: str = "data/unified.duckdb") -> BehavioralMetricsComputer:
    return BehavioralMetricsComputer(db_path=db_path)


def _mock_fetchone(return_value):
    """Return a mock connection whose execute().fetchone() returns *return_value*."""
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.fetchone.return_value = return_value
    return mock_conn


# ---------------------------------------------------------------------------
# Test 1: _contrarian_tendency returns a MetricResult
# ---------------------------------------------------------------------------

def test_contrarian_tendency_returns_metric_result():
    """Mock DB query returning known trades — verify MetricResult is returned."""
    computer = _make_computer()

    # Simulate: 3 contrarian buys out of 10 total buys
    mock_row = (3, 10)

    with patch("duckdb.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_connect.return_value = mock_conn

        result = computer._contrarian_tendency(90)

    assert isinstance(result, MetricResult)
    assert result.dimension == "contrarian_tendency"
    assert result.score == pytest.approx(0.3, abs=1e-4)
    assert result.raw_value == pytest.approx(30.0, abs=1e-4)
    assert "30.0%" in result.label
    assert result.computation_window_days == 90


# ---------------------------------------------------------------------------
# Test 2: contrarian tendency SQL uses nearest-prior-day join, not exact match
# ---------------------------------------------------------------------------

def test_contrarian_tendency_uses_nearest_prior_day_join():
    """Verify the SQL does NOT use an exact date match for market_daily lookups."""
    import inspect
    from src.services.ai_advisor import behavioral_metrics as bm_module

    source = inspect.getsource(bm_module.BehavioralMetricsComputer._contrarian_tendency)

    # Must use range-based date lookup (up to 3 days back), not exact date equality
    assert "INTERVAL '3' DAY" in source, "Must use 3-day lookback window"

    # Ensure the SQL does NOT use a direct equality like "WHERE m.date = t.log_date"
    # (it should use MAX(...) WHERE date <= t.log_date AND date >= t.log_date - INTERVAL)
    assert "date <= t.log_date" in source, "Must use <= for nearest-prior-day join"
    assert "date >= t.log_date - INTERVAL '3' DAY" in source, "Must bound lookback to 3 days"


# ---------------------------------------------------------------------------
# Test 3: compute_all returns exactly 6 MetricResult items
# ---------------------------------------------------------------------------

def test_compute_all_returns_eight_results():
    """compute_all() must always return exactly 8 MetricResult objects.

    6 original dimensions + 2 F5 contrarian-decomposition dimensions
    (systematic_contrarian, manual_contrarian) added 2026-07-07.
    """
    computer = _make_computer()

    # Patch every private method to return a known MetricResult
    dummy = MetricResult(
        dimension="test", score=0.5, raw_value=50.0,
        computation_window_days=90, label="test", description="test",
    )

    with patch.object(computer, "_contrarian_tendency", return_value=dummy), \
         patch.object(computer, "_systematic_contrarian", return_value=dummy), \
         patch.object(computer, "_manual_contrarian", return_value=dummy), \
         patch.object(computer, "_position_sizing_discipline", return_value=dummy), \
         patch.object(computer, "_decision_speed", return_value=dummy), \
         patch.object(computer, "_loss_tolerance", return_value=dummy), \
         patch.object(computer, "_strategy_compliance", return_value=dummy), \
         patch.object(computer, "_rebalance_discipline", return_value=dummy):

        results = computer.compute_all(90)

    assert len(results) == 8
    assert all(isinstance(r, MetricResult) for r in results)


# ---------------------------------------------------------------------------
# Test 4: failed metric method produces fallback MetricResult (not exception)
# ---------------------------------------------------------------------------

def test_compute_all_handles_method_failure_gracefully():
    """If a metric method raises, compute_all must return a fallback MetricResult (score=0.0)."""
    computer = _make_computer()

    dummy = MetricResult(
        dimension="test", score=0.5, raw_value=50.0,
        computation_window_days=90, label="test", description="test",
    )

    def raise_error(_window_days):
        raise RuntimeError("simulated DB error")

    # patch.object with side_effect replaces the method with a MagicMock, which
    # lacks __name__. Wrap the raiser in a named function and assign directly.
    def _failing_contrarian(window_days):
        raise RuntimeError("simulated DB error")

    with patch.object(computer, "_contrarian_tendency", new=_failing_contrarian), \
         patch.object(computer, "_systematic_contrarian", return_value=dummy), \
         patch.object(computer, "_manual_contrarian", return_value=dummy), \
         patch.object(computer, "_position_sizing_discipline", return_value=dummy), \
         patch.object(computer, "_decision_speed", return_value=dummy), \
         patch.object(computer, "_loss_tolerance", return_value=dummy), \
         patch.object(computer, "_strategy_compliance", return_value=dummy), \
         patch.object(computer, "_rebalance_discipline", return_value=dummy):

        results = computer.compute_all(90)

    assert len(results) == 8

    # The first result (contrarian_tendency) must be the fallback
    fallback = results[0]
    assert fallback.score == 0.0
    assert fallback.label == "N/A"
    assert "Insufficient data" in fallback.description


# ---------------------------------------------------------------------------
# Test 5: _position_sizing_discipline handles missing target_allocations table
# ---------------------------------------------------------------------------

def test_position_sizing_discipline_handles_missing_target_allocations():
    """If target_allocations table doesn't exist, return score=0.5 gracefully."""
    computer = _make_computer()

    with patch("duckdb.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Table target_allocations does not exist")
        mock_connect.return_value = mock_conn

        result = computer._position_sizing_discipline(90)

    assert isinstance(result, MetricResult)
    assert result.dimension == "position_sizing_discipline"
    assert result.score == 0.5
    assert "暂无目标配置数据" in result.description


# ---------------------------------------------------------------------------
# Test 6: _ai_active_since returns earliest date as a string
# ---------------------------------------------------------------------------

def test_ai_active_since_returns_earliest_date():
    """_ai_active_since() returns the MIN date from ai_briefs/strategy_memos as 'YYYY-MM-DD'."""
    computer = _make_computer()

    with patch("duckdb.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = ("2025-10-01",)
        mock_connect.return_value = mock_conn

        result = computer._ai_active_since()

    assert result == "2025-10-01"


# ---------------------------------------------------------------------------
# Test 7: _strategy_compliance uses AI-active window and includes date in label
# ---------------------------------------------------------------------------

def test_strategy_compliance_uses_ai_active_window():
    """_strategy_compliance scopes the denominator to AI-active trades only."""
    computer = _make_computer()

    with patch.object(computer, "_ai_active_since", return_value="2025-10-01"), \
         patch("duckdb.connect") as mock_connect:
        mock_conn = MagicMock()
        # 1 strategy-matched trade out of 2 total AI-era trades (low count is valid post-fix)
        mock_conn.execute.return_value.fetchone.return_value = (1, 2)
        mock_connect.return_value = mock_conn

        result = computer._strategy_compliance(90)

    assert isinstance(result, MetricResult)
    assert result.dimension == "strategy_compliance"
    assert result.score == pytest.approx(0.5, abs=1e-4)
    assert result.raw_value == pytest.approx(50.0, abs=1e-4)
    assert "since 2025-10-01" in result.label
    assert "2025-10-01" in result.description


# ---------------------------------------------------------------------------
# Test 8: _strategy_compliance returns graceful fallback when no AI data exists
# ---------------------------------------------------------------------------

def test_strategy_compliance_no_ai_data_fallback():
    """When _ai_active_since returns None, strategy_compliance returns score=0.5 with clear label."""
    computer = _make_computer()

    with patch.object(computer, "_ai_active_since", return_value=None):
        result = computer._strategy_compliance(90)

    assert isinstance(result, MetricResult)
    assert result.dimension == "strategy_compliance"
    assert result.score == 0.5
    assert result.label == "No AI advisory data yet"


# ---------------------------------------------------------------------------
# Test 9: _strategy_compliance ai_floor uses ai_since directly, not GREATEST
# ---------------------------------------------------------------------------

def test_strategy_compliance_ai_floor_uses_ai_since_not_window_days():
    """ai_floor CTE must use ai_since directly — not GREATEST(window_days, ai_since)."""
    import inspect
    from src.services.ai_advisor import behavioral_metrics as bm_module

    source = inspect.getsource(bm_module.BehavioralMetricsComputer._strategy_compliance)

    assert "GREATEST" not in source, \
        "ai_floor CTE must NOT use GREATEST — it should use ai_since directly"
    assert "'{ai_since}'::DATE AS floor_date" in source or \
        "ai_since" in source, \
        "ai_floor CTE must reference ai_since directly"
    # window_days must not appear inside the ai_floor CTE (i.e., not used as a floor)
    # The sql string is built with f-string; window_days should not appear in the SQL template
    # after the ai_floor CTE line
    ai_floor_idx = source.find("ai_floor AS")
    assert ai_floor_idx != -1, "ai_floor CTE must exist in source"
    ai_floor_section = source[ai_floor_idx: ai_floor_idx + 200]
    assert "window_days" not in ai_floor_section, \
        "window_days must NOT appear in the ai_floor CTE definition"


# ---------------------------------------------------------------------------
# Test 10: _strategy_compliance uses REGEXP_EXTRACT_ALL (not bare REGEXP_EXTRACT)
# ---------------------------------------------------------------------------

def test_strategy_compliance_uses_regexp_extract_all_for_multi_ticker():
    """strategy_tickers CTE must use REGEXP_EXTRACT_ALL, not the single-match REGEXP_EXTRACT.
    Also verifies that content field is included in the extraction text."""
    import inspect
    from src.services.ai_advisor import behavioral_metrics as bm_module

    source = inspect.getsource(bm_module.BehavioralMetricsComputer._strategy_compliance)

    assert "REGEXP_EXTRACT_ALL" in source, \
        "strategy_tickers CTE must use REGEXP_EXTRACT_ALL for multi-ticker extraction"
    # REGEXP_EXTRACT_ALL contains REGEXP_EXTRACT — check for the bare call with opening paren
    # by checking that "REGEXP_EXTRACT(" (without _ALL) does NOT appear
    import re
    bare_extract_calls = re.findall(r'REGEXP_EXTRACT\(', source)
    assert len(bare_extract_calls) == 0, \
        "source must not contain bare REGEXP_EXTRACT( — only REGEXP_EXTRACT_ALL is allowed"
    # content field must be included in the extraction text
    assert ("COALESCE(content" in source or "content," in source or "content)" in source), \
        "strategy_tickers extraction must include the content field, not just title + key_directives"


# ---------------------------------------------------------------------------
# Test 11: _strategy_compliance regex pattern captures 3-10 char tickers
# ---------------------------------------------------------------------------

def test_strategy_compliance_regex_matches_three_letter_tickers():
    """The ticker regex must capture tickers as short as 3 characters (e.g. ETF symbols).

    The blueprint pattern is '[A-Za-z][A-Za-z0-9]{2,9}' — one required leading alpha
    plus 2-9 more alphanumeric chars = 3-10 total characters. This is semantically
    equivalent to {3,10} on the whole token and captures 3-letter tickers like SPY, QQQ.
    The f-string encodes the quantifier as {{2,9}} which renders to {2,9} in SQL.
    """
    import inspect
    from src.services.ai_advisor import behavioral_metrics as bm_module

    source = inspect.getsource(bm_module.BehavioralMetricsComputer._strategy_compliance)

    # Blueprint uses [A-Za-z][A-Za-z0-9]{2,9} — 1 required + 2-9 more = 3-10 total chars
    # In the f-string source this appears as {{2,9}} or in the rendered SQL as {2,9}
    assert ("{{2,9}}" in source or "{2,9}" in source), \
        "Ticker regex must use {2,9} quantifier on the tail group (= 3-10 chars total)"
    assert "{{4,10}}" not in source and "{4,10}" not in source, \
        "Ticker regex must NOT use {4,10} — that would miss 3-letter tickers"


# ---------------------------------------------------------------------------
# Test 12: _strategy_compliance uses equality matching (IN) in final aggregation
# ---------------------------------------------------------------------------

def test_strategy_compliance_uses_equality_matching_not_like():
    """Final aggregation FILTER must use symbol IN (strategy_tickers), not LIKE.
    LIKE is acceptable inside cn_fund_matches for keyword matching, but the
    aggregation WHERE clause must use IN for ticker matching."""
    import inspect
    from src.services.ai_advisor import behavioral_metrics as bm_module

    source = inspect.getsource(bm_module.BehavioralMetricsComputer._strategy_compliance)

    # The aggregation must use 'symbol IN' for ticker-based matching
    assert ("rts.symbol IN" in source or "symbol IN (SELECT ticker" in source), \
        "Aggregation FILTER must use equality matching: 'rts.symbol IN (SELECT ticker ...)'"
    # LIKE may appear in cn_fund_matches for keyword matching, but must NOT appear in the
    # final SELECT aggregation — confirm 'rts.symbol LIKE' is not used
    assert "rts.symbol LIKE" not in source, \
        "Final aggregation must NOT use 'rts.symbol LIKE' — only IN is allowed for ticker matching"


# ---------------------------------------------------------------------------
# Test 13: _strategy_compliance has CN fund class-keyword matching CTEs
# ---------------------------------------------------------------------------

def test_strategy_compliance_has_cn_fund_class_keyword_matching():
    """_strategy_compliance must contain cn_fund_matches and class_keywords CTEs
    for two-tier matching that covers CN funds identified by numeric codes."""
    import inspect
    from src.services.ai_advisor import behavioral_metrics as bm_module

    source = inspect.getsource(bm_module.BehavioralMetricsComputer._strategy_compliance)

    assert "cn_fund_matches" in source, \
        "_strategy_compliance must contain a 'cn_fund_matches' CTE for CN fund class-keyword matching"
    assert "class_keywords" in source, \
        "_strategy_compliance must contain a 'class_keywords' CTE defining asset class → keyword mappings"
    # Verify at least one Chinese keyword is present (confirms it's real CN fund matching)
    assert ("A股" in source or "QDII" in source or "货币" in source), \
        "class_keywords CTE must contain Chinese market keywords (A股, QDII, 货币, etc.)"


# ---------------------------------------------------------------------------
# NOTE: _rebalance_discipline regression tests (F4.1 — PRD 2026-07-07 §F4.1)
# live in tests/services/test_rebalance_discipline_drift.py (split out to
# keep this file under the 400-line house limit).
# ---------------------------------------------------------------------------
