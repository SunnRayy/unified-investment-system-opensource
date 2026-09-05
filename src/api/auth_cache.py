"""
In-memory credential cache for the auth hot path.

Why this exists
---------------
DuckDB forbids a read-only connection open while a read-write connection is
open in the same process. A cloud sync holds a long-lived read-write connection;
prior to this module, every auth check (login + bearer validation) opened a
read-only connection via ``connect_readonly_with_retry()``. While a sync was
running, DuckDB refused those read-only opens, the retry budget (~2 s) exhausted,
and the result was:

* ``POST /auth/login`` → 500 ("Can't open a connection ... different
  configuration than existing connections") — surfaced to the user as
  "wrong password".
* Every authed request → 401.

Fix: cache credentials in memory at startup. The auth hot path (login +
``_validate_token``) never touches the DB. Cache is refreshed after any
credential write (``change_password``, ``logout_all``) so the new hash/version
takes effect immediately and old tokens are invalidated.

Single-instance assumption
--------------------------
Cloud Run ``max-instances=1``. A password change (or logout-all) updates the
cache in the single process immediately. If ``max-instances`` were ever raised
above 1, a password change on one instance would not propagate to the others
until restart — acceptable and documented here, but worth noting before scaling.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class CachedCreds:
    configured: bool
    password_hash: Optional[str]
    token_version: Optional[int]


_creds: Optional[CachedCreds] = None
_lock = threading.Lock()


def refresh_from_db(db: Any) -> None:
    """Load auth_credentials into the in-memory cache.

    Call at startup (while the startup DatabaseConnector is still open) and
    after any credential write (change-password, logout-all).

    NEVER call this on the auth hot path — the whole point is a DB-free auth
    path. If the cache is unset when auth runs, ``get()`` returns ``None`` and
    auth fails closed (see ``_validate_token``).
    """
    row = db.execute(
        "SELECT password_hash, token_version FROM auth_credentials WHERE id = 1"
    ).fetchone()
    new = CachedCreds(True, row[0], row[1]) if row else CachedCreds(False, None, None)
    global _creds
    with _lock:
        _creds = new


def get() -> Optional[CachedCreds]:
    """Return the current cached creds, or None if never loaded.

    Lock-free read: Python's GIL makes a reference read atomic; only writes
    need the lock (see ``refresh_from_db``).

    NEVER lazy-loads from the DB — the whole point is a DB-free auth hot path.
    """
    return _creds


def _reset_for_tests() -> None:
    """Clear the cache. Call in test teardown to prevent cross-test leakage."""
    global _creds
    with _lock:
        _creds = None
