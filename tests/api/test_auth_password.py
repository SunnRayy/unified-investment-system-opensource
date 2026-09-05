"""Tests for bcrypt-based auth endpoints and versioned token middleware.

Auth hot path (login + middleware token validation) now reads from the
in-memory cache (src.api.auth_cache) — no DB connection opened. Tests seed the
cache via ``patch("src.api.auth_cache.get", return_value=CachedCreds(...))``.

Write paths (change-password, logout-all) still use DatabaseConnector directly.
The cache refresh after writes is verified in the dedicated behavioral tests at
the bottom of this file.
"""

from unittest.mock import MagicMock, patch

import bcrypt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth_cache import CachedCreds
from src.api.middleware.auth import BearerTokenMiddleware
from src.api.routes.auth import router as auth_router


def _make_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router)
    app.add_middleware(BearerTokenMiddleware)
    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_mock(password: str, version: int):
    """Return a mock connection that returns (hash, version) for SELECT."""
    mock = MagicMock()
    pw_hash = _make_hash(password)
    mock.execute.return_value.fetchone.return_value = (pw_hash, version)
    return mock


def _db_empty():
    """Return a mock connection with no credentials row."""
    mock = MagicMock()
    mock.execute.return_value.fetchone.return_value = None
    return mock


def _creds(password: str, version: int) -> CachedCreds:
    """Build a CachedCreds for a known password / version."""
    return CachedCreds(configured=True, password_hash=_make_hash(password), token_version=version)


# ---------------------------------------------------------------------------
# Login tests  (login reads from auth_cache, not DB)
# ---------------------------------------------------------------------------

def test_login_success(monkeypatch):
    pw = "testpassword"
    monkeypatch.setenv("UIS_AUTH_TOKEN", "dummy")
    with patch("src.api.auth_cache.get", return_value=_creds(pw, 1)):
        client = TestClient(_build_app())
        resp = client.post("/auth/login", json={"password": pw})
    assert resp.status_code == 200
    assert resp.json()["token"] == f"{pw}.1"


def test_login_wrong_password(monkeypatch):
    monkeypatch.setenv("UIS_AUTH_TOKEN", "dummy")
    with patch("src.api.auth_cache.get", return_value=_creds("correct", 1)):
        client = TestClient(_build_app())
        resp = client.post("/auth/login", json={"password": "wrong"})
    assert resp.status_code == 401


def test_login_db_empty(monkeypatch):
    monkeypatch.setenv("UIS_AUTH_TOKEN", "dummy")
    # configured=False → login returns 503
    with patch("src.api.auth_cache.get", return_value=CachedCreds(False, None, None)):
        client = TestClient(_build_app())
        resp = client.post("/auth/login", json={"password": "any"})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Change-password tests  (route WRITES via DatabaseConnector)
# ---------------------------------------------------------------------------

def test_change_password_success(monkeypatch):
    """Version bumps; returned token uses new password + new version."""
    pw = "oldpass"
    monkeypatch.setenv("UIS_AUTH_TOKEN", "dummy")

    pw_hash = _make_hash(pw)
    mock_db = MagicMock()
    # Call 1: _get_credentials SELECT → (hash, 1)
    # Call 2: UPDATE RETURNING → (2,)
    mock_db.execute.return_value.fetchone.side_effect = [(pw_hash, 1), (2,)]

    with (
        patch("src.api.routes.auth.DatabaseConnector", return_value=mock_db),
        # Middleware reads from cache (versioned token pw.1 must validate)
        patch("src.api.auth_cache.get", return_value=_creds(pw, 1)),
        # refresh_from_db is a no-op here; behavioral verification is in
        # test_cache_refreshed_after_change_password below.
        patch("src.api.auth_cache.refresh_from_db"),
    ):
        client = TestClient(_build_app())
        resp = client.post(
            "/auth/change-password",
            json={"current_password": pw, "new_password": "newpass"},
            headers={"Authorization": f"Bearer {pw}.1"},
        )
    assert resp.status_code == 200
    assert resp.json()["token"] == "newpass.2"


def test_change_password_wrong_current(monkeypatch):
    pw = "correct"
    monkeypatch.setenv("UIS_AUTH_TOKEN", "dummy")
    with (
        patch("src.api.routes.auth.DatabaseConnector", return_value=_db_mock(pw, 1)),
        patch("src.api.auth_cache.get", return_value=_creds(pw, 1)),
        patch("src.api.auth_cache.refresh_from_db"),
    ):
        client = TestClient(_build_app())
        resp = client.post(
            "/auth/change-password",
            json={"current_password": "wrong", "new_password": "newpass"},
            headers={"Authorization": f"Bearer {pw}.1"},
        )
    assert resp.status_code == 401


def test_change_password_period_rejected(monkeypatch):
    pw = "mypassword"
    monkeypatch.setenv("UIS_AUTH_TOKEN", "dummy")
    with (
        patch("src.api.routes.auth.DatabaseConnector", return_value=_db_mock(pw, 1)),
        patch("src.api.auth_cache.get", return_value=_creds(pw, 1)),
        patch("src.api.auth_cache.refresh_from_db"),
    ):
        client = TestClient(_build_app())
        resp = client.post(
            "/auth/change-password",
            json={"current_password": pw, "new_password": "has.period"},
            headers={"Authorization": f"Bearer {pw}.1"},
        )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Logout-all tests
# ---------------------------------------------------------------------------

def test_logout_all_bumps_version(monkeypatch):
    pw = "mypassword"
    monkeypatch.setenv("UIS_AUTH_TOKEN", "dummy")
    mock_db = MagicMock()
    # refresh_from_db inside logout_all will call fetchone; return a valid row
    mock_db.execute.return_value.fetchone.return_value = (_make_hash(pw), 2)

    with (
        patch("src.api.routes.auth.DatabaseConnector", return_value=mock_db),
        patch("src.api.auth_cache.get", return_value=_creds(pw, 1)),
        # refresh_from_db will be called with mock_db; fixed fetchone return is fine
        # for this test (we just verify the UPDATE happened, not the cache state)
        patch("src.api.auth_cache.refresh_from_db"),
    ):
        client = TestClient(_build_app())
        resp = client.post(
            "/auth/logout-all",
            headers={"Authorization": f"Bearer {pw}.1"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    update_calls = [
        str(call) for call in mock_db.execute.call_args_list
        if "token_version" in str(call) and "UPDATE" in str(call)
    ]
    assert len(update_calls) >= 1


def test_old_version_rejected_after_version_bump(monkeypatch):
    """After version is bumped to 2, token with version 1 must be rejected."""
    pw = "mypassword"
    monkeypatch.setenv("UIS_AUTH_TOKEN", "dummy")
    # Cache reports version 2; token carries version 1 → mismatch → 401
    with patch("src.api.auth_cache.get", return_value=_creds(pw, 2)):
        client = TestClient(_build_app())
        resp = client.get(
            "/api/auth/validate",
            headers={"Authorization": f"Bearer {pw}.1"},
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Behavioral tests: cache refresh after credential writes
# ---------------------------------------------------------------------------

class _NoClose:
    """Proxy that forwards DB calls to a real DuckDB connection but ignores
    close() so the test retains ownership of the connection lifetime."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def close(self):
        pass  # caller controls the real connection's lifecycle


def _setup_auth_db():
    """Return an in-memory DuckDB connection seeded with auth_credentials."""
    import duckdb
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE auth_credentials ("
        "  id INTEGER PRIMARY KEY,"
        "  password_hash VARCHAR,"
        "  token_version INTEGER,"
        "  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    return conn


def test_cache_refreshed_after_change_password(monkeypatch):
    """After change_password, old password fails login and new one works via cache."""
    import src.api.auth_cache as _cache

    old_pw = "oldpass"
    new_pw = "newpass"
    monkeypatch.setenv("UIS_AUTH_TOKEN", "dummy")

    _db = _setup_auth_db()
    old_hash = _make_hash(old_pw)
    _db.execute("INSERT INTO auth_credentials VALUES (1, ?, 1, CURRENT_TIMESTAMP)", [old_hash])

    # Seed cache from real DB
    _cache.refresh_from_db(_db)
    assert _cache.get().token_version == 1

    try:
        with patch("src.api.routes.auth.DatabaseConnector", return_value=_NoClose(_db)):
            client = TestClient(_build_app())
            resp = client.post(
                "/auth/change-password",
                json={"current_password": old_pw, "new_password": new_pw},
                headers={"Authorization": f"Bearer {old_pw}.1"},
            )

        assert resp.status_code == 200
        assert resp.json()["token"] == f"{new_pw}.2"

        # Cache must have been refreshed with new version and new hash
        creds = _cache.get()
        assert creds is not None
        assert creds.token_version == 2
        # New password validates against the cached hash
        assert bcrypt.checkpw(new_pw.encode(), creds.password_hash.encode())
        # Old password no longer validates against the cached hash
        assert not bcrypt.checkpw(old_pw.encode(), creds.password_hash.encode())
    finally:
        _db.close()
        _cache._reset_for_tests()


def test_old_token_rejected_after_logout_all(monkeypatch):
    """After logout_all, pre-existing versioned token (version 1) is rejected."""
    import src.api.auth_cache as _cache

    pw = "mypassword"
    monkeypatch.setenv("UIS_AUTH_TOKEN", "dummy")

    _db = _setup_auth_db()
    pw_hash = _make_hash(pw)
    _db.execute("INSERT INTO auth_credentials VALUES (1, ?, 1, CURRENT_TIMESTAMP)", [pw_hash])

    # Seed cache from real DB (version 1)
    _cache.refresh_from_db(_db)
    assert _cache.get().token_version == 1

    try:
        with patch("src.api.routes.auth.DatabaseConnector", return_value=_NoClose(_db)):
            client = TestClient(_build_app())
            resp = client.post(
                "/auth/logout-all",
                headers={"Authorization": f"Bearer {pw}.1"},
            )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # Cache must reflect bumped version
        creds = _cache.get()
        assert creds is not None
        assert creds.token_version == 2

        # Validate directly via middleware: old token (v1) rejected, same-pw v2 accepted
        mw = BearerTokenMiddleware(app=MagicMock())
        assert not mw._validate_token(f"{pw}.1")   # version 1 → rejected
        assert mw._validate_token(f"{pw}.2")       # version 2 → accepted
    finally:
        _db.close()
        _cache._reset_for_tests()
