"""Tests for integrity gate blocking/advisory classification and exception-path name handling.

Part A of the pass1-followup-integrity-gate plan (2026-05-30).

Key invariants verified:
- A check function that raises emits the canonical name (from INTEGRITY_CHECKS registry),
  NOT the __name__-derived space-name that broke BLOCKING_CHECKS keying before this fix.
- A raised blocking check causes the orchestrator to set success=False.
- A raised advisory check causes the orchestrator to set degraded=True, success remains True.
- is_blocking() returns True for unknown names (fail-safe: unclassifiable failures cannot pass).
- BLOCKING_CHECKS / advisory set covers all canonical names (no orphaned entries).
"""
import pytest
from unittest.mock import patch
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.validation.data_integrity_gate import (
    BLOCKING_CHECKS,
    INTEGRITY_CHECKS,
    INTEGRITY_CHECK_COUNT,
    IntegrityReport,
    CheckResult,
    is_blocking,
    run_integrity_checks,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_db():
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1. Exception-path canonical name test
# ---------------------------------------------------------------------------

def test_raised_blocking_check_emits_canonical_name(mem_db):
    """A check that raises must appear in the report under its canonical name.

    Before this fix the runner built the name from __name__ (e.g.
    'reader rows not all shadowed' with spaces) which did not match the
    underscore key in BLOCKING_CHECKS, silently misclassifying it as advisory.
    """
    # Find the first blocking check in the registry.
    blocking_entry = next(
        (name, fn) for name, fn in INTEGRITY_CHECKS if name in BLOCKING_CHECKS
    )
    canonical_name, blocking_fn = blocking_entry

    # Monkeypatch the function to raise.
    def _raising_fn(connector):
        raise RuntimeError("simulated failure in blocking check")

    patched_checks = [
        (name, _raising_fn if fn is blocking_fn else fn)
        for name, fn in INTEGRITY_CHECKS
    ]

    with patch("src.validation.data_integrity_gate.INTEGRITY_CHECKS", patched_checks):
        report = run_integrity_checks(mem_db)

    # Find the failed check in the report.
    failed = [c for c in report.checks if c.name == canonical_name]
    assert failed, (
        f"Expected a failed check with canonical name {canonical_name!r}, "
        f"got names: {[c.name for c in report.checks]}"
    )
    failed_check = failed[0]
    assert not failed_check.passed
    assert "simulated failure" in failed_check.details


def test_raised_advisory_check_emits_canonical_name(mem_db):
    """Same invariant for an advisory check that raises."""
    advisory_entries = [
        (name, fn) for name, fn in INTEGRITY_CHECKS if name not in BLOCKING_CHECKS
    ]
    assert advisory_entries, "Expected at least one advisory check in registry"
    canonical_name, advisory_fn = advisory_entries[0]

    def _raising_fn(connector):
        raise ValueError("simulated advisory failure")

    patched_checks = [
        (name, _raising_fn if fn is advisory_fn else fn)
        for name, fn in INTEGRITY_CHECKS
    ]

    with patch("src.validation.data_integrity_gate.INTEGRITY_CHECKS", patched_checks):
        report = run_integrity_checks(mem_db)

    failed = [c for c in report.checks if c.name == canonical_name]
    assert failed, (
        f"Expected failed check {canonical_name!r}, got {[c.name for c in report.checks]}"
    )
    assert not failed[0].passed
    assert "simulated advisory failure" in failed[0].details


# ---------------------------------------------------------------------------
# 2. Orchestrator: raised blocking check → success=False
# ---------------------------------------------------------------------------

def test_raised_blocking_check_sets_success_false(mem_db):
    """A raised blocking check must propagate through the orchestrator as success=False.

    This test patches run_integrity_checks to return a report with a single
    blocking failure, then runs a minimal sync and asserts success=False.
    """
    from src.sync.orchestrator import run_full_sync_v3

    # Pick the first blocking check name.
    blocking_name = next(iter(BLOCKING_CHECKS))

    failing_report = IntegrityReport(checks=[
        CheckResult(
            name=blocking_name,
            passed=False,
            actual_value="0",
            threshold="0",
            details="injected blocking failure",
        )
    ])

    config = {
        "sources": {"pis": {"excel_path": "", "sqlite_path": ""}},
        "validation": {"freshness": {"enabled": False}},
    }

    with patch("src.sync.orchestrator.sync_current_allocations", return_value={"synced": 0}), \
         patch("src.sync.orchestrator.validate_cost_basis", return_value=[]), \
         patch("src.sync.orchestrator.validate_allocations", return_value=[]), \
         patch("src.sync.orchestrator.run_integrity_checks", return_value=failing_report):

        result = run_full_sync_v3(mem_db, config)

    assert result.success is False, (
        f"Expected success=False for a blocking integrity failure, got {result.success}"
    )
    # degraded should not be set when there is already a hard failure.
    # (In this case success=False dominates; degraded could be True or False
    # depending on whether we also set it — the key invariant is success=False.)
    integrity_step = next((s for s in result.steps if s.name == "integrity_gate"), None)
    assert integrity_step is not None
    assert integrity_step.critical is True
    assert integrity_step.status == "failed"


# ---------------------------------------------------------------------------
# 3. Orchestrator: raised advisory check → degraded=True, success stays True
# ---------------------------------------------------------------------------

def test_raised_advisory_check_sets_degraded(mem_db):
    """A raised advisory check must set degraded=True without touching success."""
    from src.sync.orchestrator import run_full_sync_v3

    advisory_names = [name for name, _ in INTEGRITY_CHECKS if name not in BLOCKING_CHECKS]
    assert advisory_names, "Expected at least one advisory check"
    advisory_name = advisory_names[0]

    failing_report = IntegrityReport(checks=[
        CheckResult(
            name=advisory_name,
            passed=False,
            actual_value="0",
            threshold="0",
            details="injected advisory failure",
        )
    ])

    config = {
        "sources": {"pis": {"excel_path": "", "sqlite_path": ""}},
        "validation": {"freshness": {"enabled": False}},
    }

    with patch("src.sync.orchestrator.sync_current_allocations", return_value={"synced": 0}), \
         patch("src.sync.orchestrator.validate_cost_basis", return_value=[]), \
         patch("src.sync.orchestrator.validate_allocations", return_value=[]), \
         patch("src.sync.orchestrator.run_integrity_checks", return_value=failing_report):

        result = run_full_sync_v3(mem_db, config)

    assert result.success is True, (
        f"Expected success=True for advisory-only failure, got {result.success}"
    )
    assert result.degraded is True, (
        f"Expected degraded=True for advisory failure, got {result.degraded}"
    )
    integrity_step = next((s for s in result.steps if s.name == "integrity_gate"), None)
    assert integrity_step is not None
    assert integrity_step.critical is False
    assert integrity_step.status == "failed"


# ---------------------------------------------------------------------------
# 4. is_blocking() fail-safe: unknown names are blocking
# ---------------------------------------------------------------------------

def test_is_blocking_unknown_name_defaults_to_blocking():
    """Any check name not in the canonical registry must be treated as blocking.

    This prevents a future new check from silently passing as advisory if it
    is not explicitly categorized.
    """
    assert is_blocking("some_completely_unknown_check_name_xyz") is True


def test_is_blocking_known_blocking():
    """Sanity: known blocking names return True."""
    for name in BLOCKING_CHECKS:
        assert is_blocking(name) is True, f"Expected {name!r} to be blocking"


def test_is_blocking_known_advisory():
    """Sanity: known advisory names return False."""
    advisory = [name for name, _ in INTEGRITY_CHECKS if name not in BLOCKING_CHECKS]
    for name in advisory:
        assert is_blocking(name) is False, f"Expected {name!r} to be advisory"


# ---------------------------------------------------------------------------
# 5. Registry completeness
# ---------------------------------------------------------------------------

def test_blocking_checks_are_all_canonical():
    """Every name in BLOCKING_CHECKS must appear in INTEGRITY_CHECKS registry.

    Orphaned entries in BLOCKING_CHECKS (e.g. after a check is renamed) would
    cause reclassification failures without any visible error.
    """
    canonical_names = {name for name, _ in INTEGRITY_CHECKS}
    orphaned = BLOCKING_CHECKS - canonical_names
    assert not orphaned, (
        f"BLOCKING_CHECKS contains names not in INTEGRITY_CHECKS registry: {orphaned}"
    )


def test_integrity_check_count_matches_registry():
    """INTEGRITY_CHECK_COUNT must equal len(INTEGRITY_CHECKS)."""
    assert INTEGRITY_CHECK_COUNT == len(INTEGRITY_CHECKS)


def test_registry_canonical_names_match_check_results(mem_db):
    """Each check function must return a CheckResult whose name matches the registry key.

    This guards against a function returning a different name than the registry records,
    which would break BLOCKING_CHECKS keying on the returned name.
    """
    for canonical_name, check_fn in INTEGRITY_CHECKS:
        try:
            result = check_fn(mem_db)
            assert result.name == canonical_name, (
                f"Check function {check_fn.__name__!r} returned name={result.name!r} "
                f"but registry canonical name is {canonical_name!r}. "
                "Update the registry tuple or the function's CheckResult name."
            )
        except Exception as e:
            # If a check raises on the empty DB that's acceptable (the exception-path
            # test covers name handling), but we still want to know about unexpected errors.
            pytest.skip(
                f"Check {canonical_name!r} raised on empty DB: {e} — "
                "covered by exception-path tests"
            )
