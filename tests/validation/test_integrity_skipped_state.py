"""Integrity checks have THREE states, not two: verified / skipped / failed.

Why this file exists (2026-07-26, from
docs/audits/2026-07-26-two-week-retrospective.md §3):

Every skip path in the integrity gate used to return ``passed=True``, so a
check that could not evaluate its invariant was indistinguishable from one
that evaluated it and found nothing wrong — and it counted toward the
headline "N/16 passed" score that every session and every deploy trusts.

That is not hypothetical. TWO checks were vacuous:

  * ``xirr_proxy_in_range`` (#4) — its ``IN ('BUY','VEST','DEPOSIT')`` filter
    never matched a row, so it reported a false PASS from inception until it
    was fixed in V7.7.0.
  * ``twr_in_range`` (#3) — was vacuous the same way: its coverage gate
    demanded a single ``snapshot_date`` covering >=50% of all distinct
    assets, which assumes a global cross-asset snapshot date that this
    codebase explicitly forbids. Measured live: 59 assets => gate needed
    >=29, best single date covered 5, qualifying snapshots 0 — permanently
    SKIPPED. Fixed on branch fix/twr-in-range-valuation-v2 (2026-07-26) by
    reusing the LOCKED valuation-v2 helper
    (``src.services.attribution._latest_snapshot_by_asset``) at two fixed
    365-day-apart anchors instead of a global snapshot pair — see
    ``tests/validation/test_integrity_check_3_twr_in_range.py``. The
    ``skipped`` flag documented below is still exercised: a DB with no
    valuation data at/before the 365-day-ago anchor legitimately skips.

These tests pin the semantics so a future edit cannot quietly fold "skipped"
back into "passed".
"""
from __future__ import annotations

from src.validation.data_integrity_gate import CheckResult, IntegrityReport


def _r(name: str, *, passed: bool, skipped: bool = False) -> CheckResult:
    return CheckResult(
        name=name,
        passed=passed,
        actual_value="x",
        threshold="y",
        details="d",
        skipped=skipped,
    )


def test_skipped_defaults_to_false():
    """Existing call sites that don't pass `skipped` must keep old behaviour."""
    c = CheckResult(name="n", passed=True, actual_value=1, threshold=1, details="d")
    assert c.skipped is False


def test_passed_count_excludes_skipped():
    """The headline number must count what was VERIFIED, not what merely
    failed to complain. This is the whole point of the third state."""
    report = IntegrityReport(checks=[
        _r("verified_a", passed=True),
        _r("verified_b", passed=True),
        _r("vacuous", passed=True, skipped=True),
        _r("broken", passed=False),
    ])
    assert report.passed_count == 2, "a skipped check must not count as verified"
    assert report.skipped_count == 1
    assert len(report.failed_checks) == 1
    assert report.verified_count == report.passed_count


def test_three_state_partition_is_exhaustive():
    report = IntegrityReport(checks=[
        _r("a", passed=True),
        _r("b", passed=True, skipped=True),
        _r("c", passed=False),
    ])
    assert (
        report.passed_count + report.skipped_count + len(report.failed_checks)
        == len(report.checks)
    )


def test_skipped_is_not_a_failure():
    """Deliberate: several skips are legitimate guards (pre-V5.8.0 schema,
    too few snapshots to annualize safely). Making them blocking would break
    deploys for correct reasons. The defect was invisibility, not leniency —
    so gating semantics are unchanged."""
    report = IntegrityReport(checks=[_r("a", passed=True), _r("b", passed=True, skipped=True)])
    assert report.all_passed is True
    assert report.skipped_count == 1


def test_to_dict_exposes_skipped_at_both_levels():
    report = IntegrityReport(checks=[
        _r("verified", passed=True),
        _r("vacuous", passed=True, skipped=True),
    ])
    d = report.to_dict()
    assert d["passed"] == 1, "summary must report verified-only"
    assert d["skipped"] == 1
    by_name = {c["name"]: c for c in d["checks"]}
    assert by_name["vacuous"]["skipped"] is True
    assert by_name["verified"]["skipped"] is False


def test_to_text_marks_skipped_distinctly_and_warns():
    report = IntegrityReport(checks=[
        _r("verified", passed=True),
        _r("vacuous", passed=True, skipped=True),
    ])
    text = report.to_text()
    assert "[SKIP] vacuous" in text
    assert "[PASS] verified" in text
    assert "verified NOTHING" in text, (
        "a reader skimming the report must be told the skipped check is not coverage"
    )


def test_a_wholly_vacuous_report_does_not_look_like_success():
    """The regression that motivated all of this: if EVERY check skipped, the
    old code reported a perfect score. It must now report zero verified."""
    report = IntegrityReport(checks=[
        _r("a", passed=True, skipped=True),
        _r("b", passed=True, skipped=True),
    ])
    assert report.passed_count == 0
    assert report.skipped_count == 2
    assert report.to_dict()["passed"] == 0
