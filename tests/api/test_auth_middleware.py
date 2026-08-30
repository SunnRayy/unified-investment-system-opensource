from unittest.mock import MagicMock, patch

import bcrypt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import stream_tickets
from src.api.auth_cache import CachedCreds
from src.api.middleware.auth import BearerTokenMiddleware


def _empty_creds() -> CachedCreds:
    """Simulate empty auth_credentials table (legacy mode — plain token allowed)."""
    return CachedCreds(configured=False, password_hash=None, token_version=None)


def _versioned_creds(password: str, version: int = 1) -> CachedCreds:
    """Simulate configured credentials with a real bcrypt hash."""
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    return CachedCreds(configured=True, password_hash=pw_hash, token_version=version)


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/sync/status")
    async def sync_status():
        return {"running": False}

    @app.get("/api/sync/stream")
    async def api_sync_stream():
        return {"stream": True}

    @app.get("/sync/stream")
    async def sync_stream():
        return {"stream": True}

    @app.post("/api/sync/stream-ticket")
    async def api_sync_stream_ticket():
        return {"ticket": stream_tickets.issue()}

    @app.get("/api/auth/validate")
    async def api_auth_validate():
        return {"ok": True}

    @app.get("/api/some/other/route")
    async def some_other_route():
        return {"data": True}

    @app.post("/api/settings/sources/fetch/{reader}")
    async def fetch_source(reader: str):
        return {"fetched": reader}

    app.add_middleware(BearerTokenMiddleware)
    return app


def test_auth_middleware_allows_all_requests_when_token_unset(monkeypatch):
    monkeypatch.delenv("UIS_AUTH_TOKEN", raising=False)
    app = _build_app()
    client = TestClient(app)

    response = client.get("/sync/status")

    assert response.status_code == 200
    assert response.json() == {"running": False}


def test_auth_middleware_requires_bearer_token_for_api_paths(monkeypatch):
    """Non-/api/ GETs pass as SPA shell. /api/ routes require auth."""
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    # /api/ prefix routes require auth
    response = client.get("/api/auth/validate")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_auth_middleware_accepts_matching_bearer_token(monkeypatch):
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    # Legacy plain token (no dot): accepted when cache says no credentials configured
    with patch("src.api.auth_cache.get", return_value=_empty_creds()):
        response = client.get("/sync/status", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    assert response.json() == {"running": False}


def test_auth_middleware_exempts_options_health_and_non_api_gets(monkeypatch):
    """OPTIONS, /health, and non-/api/ GETs always pass (SPA shell rule)."""
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    health_response = client.get("/health")
    options_response = client.options("/sync/status")
    # non-/api/ GETs pass as SPA shell
    spa_shell_response = client.get("/sync/status")

    assert health_response.status_code == 200
    assert options_response.status_code != 401
    assert spa_shell_response.status_code == 200


def test_sse_endpoint_accepts_valid_ticket_query_param(monkeypatch):
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    ticket = stream_tickets.issue()
    response = client.get(f"/api/sync/stream?ticket={ticket}")

    assert response.status_code == 200
    stream_tickets._reset_for_tests()


def test_sse_endpoint_rejects_invalid_ticket_query_param(monkeypatch):
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    response = client.get("/api/sync/stream?ticket=not-a-valid-ticket")

    assert response.status_code == 401


def test_sse_endpoint_rejects_missing_ticket(monkeypatch):
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    response = client.get("/api/sync/stream")

    assert response.status_code == 401


def test_sse_endpoint_rejects_old_password_in_url(monkeypatch):
    """Password-in-URL (?token=) is no longer accepted — the whole point of the fix."""
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    # Even with a valid legacy token value, ?token= must be rejected.
    with patch("src.api.auth_cache.get", return_value=_empty_creds()):
        response = client.get("/api/sync/stream?token=secret")

    assert response.status_code == 401


def test_spa_shell_get_passes_without_token(monkeypatch):
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    # non-/api/ GET passes as SPA shell
    response = client.get("/health")

    assert response.status_code == 200


def test_api_auth_validate_with_correct_bearer_returns_200(monkeypatch):
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    # Legacy plain token accepted when cache says no credentials configured
    with patch("src.api.auth_cache.get", return_value=_empty_creds()):
        response = client.get("/api/auth/validate", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_api_auth_validate_without_token_returns_401(monkeypatch):
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    response = client.get("/api/auth/validate")

    assert response.status_code == 401


def test_sse_token_query_param_rejected_for_non_sse_routes(monkeypatch):
    """Token query param bypass only works for SSE stream endpoints, not other routes."""
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    # ?token=secret should NOT bypass auth for non-SSE /api/ routes
    response = client.get("/api/some/other/route?token=secret")

    assert response.status_code == 401


def test_unprefixed_sse_endpoint_accepts_valid_ticket_query_param(monkeypatch):
    """Also test the /sync/stream (unprefixed) path for ?ticket= param."""
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    ticket = stream_tickets.issue()
    response = client.get(f"/sync/stream?ticket={ticket}")

    assert response.status_code == 200
    stream_tickets._reset_for_tests()


def test_stream_ticket_endpoint_requires_auth(monkeypatch):
    """POST /api/sync/stream-ticket without Authorization → 401."""
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    response = client.post("/api/sync/stream-ticket")

    assert response.status_code == 401


def test_stream_ticket_endpoint_issues_ticket_with_valid_auth(monkeypatch):
    """POST /api/sync/stream-ticket with valid Bearer → 200 + {ticket}."""
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    app = _build_app()
    client = TestClient(app)

    with patch("src.api.auth_cache.get", return_value=_empty_creds()):
        response = client.post(
            "/api/sync/stream-ticket",
            headers={"Authorization": "Bearer secret"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "ticket" in body
    assert isinstance(body["ticket"], str) and len(body["ticket"]) > 0
    stream_tickets._reset_for_tests()


# ── OIDC carve-out tests ──────────────────────────────────────────────────────

_SCHED_SA = "sched@proj.iam.gserviceaccount.com"
_SCHED_AUD = "https://svc.run.app"


def test_oidc_valid_token_on_fetch_path_returns_200(monkeypatch):
    """Valid OIDC token (patched _validate_oidc → True) on fetch path → 200."""
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    monkeypatch.setenv("UIS_SCHEDULER_SA_EMAIL", _SCHED_SA)
    monkeypatch.setenv("UIS_SCHEDULER_AUDIENCE", _SCHED_AUD)
    app = _build_app()
    client = TestClient(app)

    with patch.object(BearerTokenMiddleware, "_validate_oidc", return_value=True):
        response = client.post(
            "/api/settings/sources/fetch/ibkr",
            headers={"Authorization": "Bearer google-oidc-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"fetched": "ibkr"}


def test_oidc_fails_falls_through_to_401_without_password_token(monkeypatch):
    """OIDC validation fails, no password token → falls through → 401."""
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    monkeypatch.setenv("UIS_SCHEDULER_SA_EMAIL", _SCHED_SA)
    monkeypatch.setenv("UIS_SCHEDULER_AUDIENCE", _SCHED_AUD)
    app = _build_app()
    client = TestClient(app)

    with patch.object(BearerTokenMiddleware, "_validate_oidc", return_value=False):
        response = client.post(
            "/api/settings/sources/fetch/ibkr",
            headers={"Authorization": "Bearer not-a-google-token"},
        )

    assert response.status_code == 401


def test_oidc_token_on_non_fetch_path_returns_401(monkeypatch):
    """OIDC carve-out is POST+fetch-path only — other paths are untouched."""
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    monkeypatch.setenv("UIS_SCHEDULER_SA_EMAIL", _SCHED_SA)
    monkeypatch.setenv("UIS_SCHEDULER_AUDIENCE", _SCHED_AUD)
    app = _build_app()
    client = TestClient(app)

    with patch.object(BearerTokenMiddleware, "_validate_oidc", return_value=True):
        response = client.get(
            "/api/some/other/route",
            headers={"Authorization": "Bearer google-oidc-token"},
        )

    assert response.status_code == 401


def test_oidc_carve_out_disabled_when_env_vars_unset(monkeypatch):
    """When SA env vars are not set, OIDC carve-out is inactive → 401."""
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    monkeypatch.delenv("UIS_SCHEDULER_SA_EMAIL", raising=False)
    monkeypatch.delenv("UIS_SCHEDULER_AUDIENCE", raising=False)
    app = _build_app()
    client = TestClient(app)

    # Send a Bearer token that _looks_ like an OIDC token — must still 401
    response = client.post(
        "/api/settings/sources/fetch/ibkr",
        headers={"Authorization": "Bearer google-oidc-token"},
    )

    assert response.status_code == 401


def test_password_bearer_still_works_on_fetch_path(monkeypatch):
    """Owner's password-bearer token falls through OIDC carve-out and still auths."""
    monkeypatch.setenv("UIS_AUTH_TOKEN", "secret")
    monkeypatch.setenv("UIS_SCHEDULER_SA_EMAIL", _SCHED_SA)
    monkeypatch.setenv("UIS_SCHEDULER_AUDIENCE", _SCHED_AUD)
    app = _build_app()
    client = TestClient(app)

    # OIDC fails; the plain "secret" token (no dot) is accepted via legacy path
    with (
        patch.object(BearerTokenMiddleware, "_validate_oidc", return_value=False),
        patch("src.api.auth_cache.get", return_value=_empty_creds()),
    ):
        response = client.post(
            "/api/settings/sources/fetch/ibkr",
            headers={"Authorization": "Bearer secret"},
        )

    assert response.status_code == 200


def test_validate_oidc_returns_false_on_import_error():
    """If google-auth is unavailable, _validate_oidc returns False (fail-closed)."""
    middleware = BearerTokenMiddleware(app=MagicMock())
    # Simulate ImportError by patching builtins.__import__
    import builtins

    real_import = builtins.__import__

    def _block_google(name, *args, **kwargs):
        if name.startswith("google"):
            raise ImportError(f"mocked ImportError for {name}")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_block_google):
        result = middleware._validate_oidc("tok", _SCHED_SA, _SCHED_AUD)

    assert result is False


def test_validate_oidc_returns_true_on_valid_claims(monkeypatch):
    """_validate_oidc returns True when id_token.verify_oauth2_token returns correct claims."""
    middleware = BearerTokenMiddleware(app=MagicMock())
    valid_claims = {"email_verified": True, "email": _SCHED_SA}

    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=valid_claims):
        result = middleware._validate_oidc("tok", _SCHED_SA, _SCHED_AUD)

    assert result is True


def test_validate_oidc_returns_false_on_email_mismatch(monkeypatch):
    """_validate_oidc returns False when email does not match the expected SA."""
    middleware = BearerTokenMiddleware(app=MagicMock())
    wrong_email_claims = {"email_verified": True, "email": "wrong@other.iam.gserviceaccount.com"}

    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=wrong_email_claims):
        result = middleware._validate_oidc("tok", _SCHED_SA, _SCHED_AUD)

    assert result is False


def test_validate_oidc_returns_false_on_verify_exception():
    """_validate_oidc returns False when verify_oauth2_token raises (e.g. bad sig / aud)."""
    middleware = BearerTokenMiddleware(app=MagicMock())

    with patch(
        "google.oauth2.id_token.verify_oauth2_token",
        side_effect=ValueError("invalid token"),
    ):
        result = middleware._validate_oidc("tok", _SCHED_SA, _SCHED_AUD)

    assert result is False


def test_validate_oidc_rejects_explicit_email_verified_false():
    """Explicit email_verified=False is rejected even if the email matches."""
    middleware = BearerTokenMiddleware(app=MagicMock())
    claims = {"email_verified": False, "email": _SCHED_SA}

    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=claims):
        result = middleware._validate_oidc("tok", _SCHED_SA, _SCHED_AUD)

    assert result is False


def test_validate_oidc_accepts_string_true_and_absent_email_verified():
    """email_verified may arrive as the string 'true' or be absent for SA tokens —
    both must be accepted when signature/audience/email already validate."""
    middleware = BearerTokenMiddleware(app=MagicMock())

    with patch(
        "google.oauth2.id_token.verify_oauth2_token",
        return_value={"email_verified": "true", "email": _SCHED_SA},
    ):
        assert middleware._validate_oidc("tok", _SCHED_SA, _SCHED_AUD) is True

    with patch(
        "google.oauth2.id_token.verify_oauth2_token",
        return_value={"email": _SCHED_SA},  # email_verified omitted
    ):
        assert middleware._validate_oidc("tok", _SCHED_SA, _SCHED_AUD) is True
