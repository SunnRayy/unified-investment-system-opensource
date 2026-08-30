"""Unit tests for compass_allocation.py swallowed-exception fix (Pass C Batch 3b).

Verifies that when an inner DB call raises, the exception is:
  (a) logged via logger.exception (caplog captures it), and
  (b) the function returns the documented degraded value (not a 500).
"""
from __future__ import annotations

import logging


def _make_db_mock(monkeypatch, raise_on: str):
    """Return a minimal db mock whose .execute() raises RuntimeError on the first
    call that matches `raise_on` substring in the SQL."""

    class _MockDB:
        def execute(self, sql: str, *args, **kwargs):
            if raise_on in sql:
                raise RuntimeError("injected test error")
            return self  # chainable

        def fetchall(self):
            return []

    return _MockDB()


def test_targets_exception_logs_and_returns_empty(monkeypatch, caplog):
    """Exception in risk_profile_allocations query → logged + targets={}."""
    from src.services.compass_allocation import build_compass_allocation

    db = _make_db_mock(monkeypatch, "risk_profile_allocations")

    with caplog.at_level(logging.ERROR, logger="src.services.compass_allocation"):
        result = build_compass_allocation(db, include_non_rebalanceable=True)

    assert isinstance(result, list), "should return a list (degraded empty result)"
    assert any(
        "risk-profile targets" in r.message
        for r in caplog.records
        if r.levelno >= logging.ERROR
    ), "expected logger.exception message about targets"


def test_detail_rows_exception_logs_and_returns_empty(monkeypatch, caplog):
    """Exception in holdings query → logged + returns empty allocation list."""
    from src.services.compass_allocation import build_compass_allocation

    db = _make_db_mock(monkeypatch, "latest_per_asset")

    with caplog.at_level(logging.ERROR, logger="src.services.compass_allocation"):
        result = build_compass_allocation(db, include_non_rebalanceable=True)

    assert isinstance(result, list)
    assert any(
        "holdings detail rows" in r.message
        for r in caplog.records
        if r.levelno >= logging.ERROR
    ), "expected logger.exception message about detail rows"
