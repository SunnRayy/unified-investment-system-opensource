"""Tests for Attribution & Flows WS-3.3: tiered net-worth-move alert.

_compute_sync_diff (src/sync/phases/_common.py) already yields
net_worth_before/after/change_pct. This adds a distinctly-named
`net_worth_move_warning` field when |change_pct| exceeds
NET_WORTH_MOVE_ALERT_PCT (2%) — kept separate from the >30% `alert` field
(corrupt-sync signal) and from the perpetual allocation-drift warning so the
owner can actually see it (alert-fatigue fix).
"""
from src.sync.phases._common import _compute_sync_diff, NET_WORTH_MOVE_ALERT_PCT


def test_net_worth_move_warning_present_above_threshold():
    """A >2% single-run move produces the distinct [NET-WORTH-MOVE] warning."""
    pre = {"net_worth": 1_000_000.0, "asset_count": 10, "by_source": {}}
    post = {"net_worth": 1_030_000.0, "asset_count": 10, "by_source": {}}  # +3%

    diff = _compute_sync_diff(pre, post)

    assert diff["net_worth_change_pct"] == 3.0
    assert diff["net_worth_move_warning"] is not None
    warning = diff["net_worth_move_warning"]
    assert "[NET-WORTH-MOVE]" in warning
    assert "+3.00%" in warning
    assert "1,000,000" in warning
    assert "1,030,000" in warning
    assert "review before trusting downstream reports" in warning
    # Must not be conflated with the pre-existing >30% corrupt-sync alert.
    assert diff["alert"] is False


def test_net_worth_move_warning_absent_below_threshold():
    """A sub-2% move (ordinary market noise) must not produce the warning."""
    pre = {"net_worth": 1_000_000.0, "asset_count": 10, "by_source": {}}
    post = {"net_worth": 1_010_000.0, "asset_count": 10, "by_source": {}}  # +1%

    diff = _compute_sync_diff(pre, post)

    assert diff["net_worth_change_pct"] == 1.0
    assert diff["net_worth_move_warning"] is None


def test_net_worth_move_warning_triggers_on_negative_move():
    """A -2%+ drop must also trigger (absolute value, not just gains)."""
    pre = {"net_worth": 1_000_000.0, "asset_count": 10, "by_source": {}}
    post = {"net_worth": 950_000.0, "asset_count": 10, "by_source": {}}  # -5%

    diff = _compute_sync_diff(pre, post)

    assert diff["net_worth_move_warning"] is not None
    assert "-5.00%" in diff["net_worth_move_warning"]


def test_net_worth_move_warning_distinct_from_allocation_drift_text():
    """The warning string must be independently identifiable (not the drift warning)."""
    pre = {"net_worth": 1_000_000.0, "asset_count": 10, "by_source": {}}
    post = {"net_worth": 1_050_000.0, "asset_count": 10, "by_source": {}}

    diff = _compute_sync_diff(pre, post)
    warning = diff["net_worth_move_warning"]
    assert "allocation drift" not in warning.lower()


def test_threshold_constant_is_2_pct():
    assert NET_WORTH_MOVE_ALERT_PCT == 2.0


def test_exactly_at_threshold_does_not_trigger():
    """Boundary: exactly 2.0% must NOT trigger (strict > per spec)."""
    pre = {"net_worth": 1_000_000.0, "asset_count": 10, "by_source": {}}
    post = {"net_worth": 1_020_000.0, "asset_count": 10, "by_source": {}}  # exactly +2%

    diff = _compute_sync_diff(pre, post)
    assert diff["net_worth_change_pct"] == 2.0
    assert diff["net_worth_move_warning"] is None
