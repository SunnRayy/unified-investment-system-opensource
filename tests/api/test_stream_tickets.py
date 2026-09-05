"""Tests for src/api/stream_tickets.py — in-memory SSE ticket store."""
import time

import pytest

from src.api import stream_tickets


@pytest.fixture(autouse=True)
def _reset():
    """Clear the ticket store before and after every test."""
    stream_tickets._reset_for_tests()
    yield
    stream_tickets._reset_for_tests()


# ── issue() ──────────────────────────────────────────────────────────────────


def test_issue_returns_nonempty_string():
    ticket = stream_tickets.issue()
    assert isinstance(ticket, str) and len(ticket) > 0


def test_issue_returns_unique_tickets():
    t1 = stream_tickets.issue()
    t2 = stream_tickets.issue()
    assert t1 != t2


# ── validate() ───────────────────────────────────────────────────────────────


def test_validate_true_for_fresh_ticket():
    ticket = stream_tickets.issue()
    assert stream_tickets.validate(ticket) is True


def test_validate_true_again_immediately_reusable():
    """Ticket must remain valid across multiple calls (EventSource reconnects)."""
    ticket = stream_tickets.issue()
    assert stream_tickets.validate(ticket) is True
    assert stream_tickets.validate(ticket) is True


def test_validate_false_for_unknown_ticket():
    assert stream_tickets.validate("not-a-real-ticket") is False


def test_validate_false_for_empty_string():
    assert stream_tickets.validate("") is False


def test_validate_false_after_expiry(monkeypatch):
    """Simulate expiry by patching time.monotonic to return a future time."""
    ticket = stream_tickets.issue()

    # Advance the clock beyond the TTL.
    real_monotonic = time.monotonic
    monkeypatch.setattr(
        stream_tickets.time,
        "monotonic",
        lambda: real_monotonic() + stream_tickets._TTL + 1,
    )

    assert stream_tickets.validate(ticket) is False


def test_validate_purges_expired_entries(monkeypatch):
    """Expired tickets are removed from the internal dict on validate()."""
    ticket = stream_tickets.issue()

    real_monotonic = time.monotonic
    monkeypatch.setattr(
        stream_tickets.time,
        "monotonic",
        lambda: real_monotonic() + stream_tickets._TTL + 1,
    )

    # After validation, the expired ticket should be gone from the internal dict.
    stream_tickets.validate(ticket)
    with stream_tickets._lock:
        assert ticket not in stream_tickets._tickets
