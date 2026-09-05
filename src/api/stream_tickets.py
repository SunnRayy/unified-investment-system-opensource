"""
Short-lived, stream-scoped ticket store for SSE authentication.

Why this exists
---------------
Native browser EventSource cannot set custom headers, so the SSE stream
endpoint historically accepted the full login token (?token=<password.version>)
as a query parameter.  Cloud Run (and uvicorn) log request URLs, so the
password leaked into structured logs on every stream connection.

Fix: the frontend first POSTs (with the normal Authorization header) to
``POST /api/sync/stream-ticket`` to obtain a random, time-limited ticket,
then opens the EventSource with ``?ticket=<ticket>``.  A leaked ticket is
far less dangerous than the password: it is random, single-endpoint, and
expires in 10 minutes.

Design notes
------------
- **Single-process assumption**: Cloud Run ``max-instances=1``.  The dict is
  in-process; tickets issued on one instance are not known to another.  This
  is acceptable and documented; do not raise max-instances without revisiting.
- **Reusable within TTL**: EventSource auto-reconnects and reuses the same URL.
  Consuming (deleting) the ticket on first validate() would break reconnects
  during a multi-minute sync.  Tickets are valid until expiry.
- **Lazy expiry purge**: expired entries are removed on each validate() call.
  The dict is bounded in practice (one ticket per sync session, TTL 10 min).
"""

from __future__ import annotations

import secrets
import threading
import time

# Ticket time-to-live in seconds (10 minutes — long enough for a full sync +
# all EventSource reconnect attempts).
_TTL: float = 600.0

_tickets: dict[str, float] = {}  # ticket -> expires_at (monotonic)
_lock = threading.Lock()


def issue() -> str:
    """Generate a new stream ticket and store it with a 10-minute expiry.

    Returns the ticket string.  The caller (POST /sync/stream-ticket) returns
    it to the authenticated frontend.
    """
    ticket = secrets.token_urlsafe(32)
    expires_at = time.monotonic() + _TTL
    with _lock:
        _tickets[ticket] = expires_at
    return ticket


def validate(ticket: str) -> bool:
    """Return True iff the ticket exists and has not expired.

    Reusable within the TTL — do NOT delete on validate (EventSource reconnects
    reuse the same URL/ticket).

    Lazily purges expired entries on each call.
    """
    now = time.monotonic()
    with _lock:
        # Lazy purge expired entries to keep the dict bounded.
        expired_keys = [k for k, exp in _tickets.items() if exp <= now]
        for k in expired_keys:
            del _tickets[k]

        return _tickets.get(ticket, 0.0) > now


def _reset_for_tests() -> None:
    """Clear the ticket store.  Call in test teardown to prevent cross-test leakage."""
    with _lock:
        _tickets.clear()
