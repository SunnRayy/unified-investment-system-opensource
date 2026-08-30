import importlib
import sys

from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute


def _load_main_module():
    import src.api.main as api_main

    return importlib.reload(api_main)


def _restore_default_module_state(monkeypatch):
    """Reload src.api.main back to its default (local-dev, no cloud/auth env)
    state. importlib.reload() mutates the shared sys.modules entry; other test
    modules (e.g. tests/api/test_file_patterns.py) do a fresh
    `from src.api.main import app` inside per-test fixtures and pick up
    whatever the last reload left behind — under pytest-xdist, tests from many
    files interleave within one worker process. Never let a mode-switching
    test leak its route registration into unrelated tests.

    Wrapped so a restore failure prints loudly but never masks the original
    test exception.
    """
    monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)
    monkeypatch.delenv("UIS_AUTH_TOKEN", raising=False)
    try:
        _load_main_module()
    except Exception as restore_error:  # pragma: no cover — defensive
        print(
            "\n*** WARNING: failed to restore src.api.main to default state "
            f"after mode-switching test: {restore_error!r} — subsequent tests "
            "in this worker may see wrong route registration ***",
            file=sys.stderr,
        )


def _routes_by_path(app) -> dict:
    """Map route path -> set of HTTP methods for all APIRoutes on the app."""
    routes: dict = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            routes.setdefault(route.path, set()).update(route.methods)
    return routes


def test_api_prefixed_routes_registered():
    api_main = _load_main_module()
    route_paths = {route.path for route in api_main.app.routes if hasattr(route, "methods")}

    assert "/sync/status" in route_paths
    assert "/api/sync/status" in route_paths


def test_cors_allowed_origin_from_env(monkeypatch):
    monkeypatch.setenv("UIS_ALLOWED_ORIGIN", "https://example.com")
    api_main = _load_main_module()

    cors_middleware = next(
        middleware
        for middleware in api_main.app.user_middleware
        if middleware.cls is CORSMiddleware
    )
    assert cors_middleware.kwargs["allow_origins"] == ["https://example.com"]


def test_every_router_has_api_prefixed_parity():
    """Class-level regression test for #28/#29: a router registered on
    ALL_ROUTERS must be reachable under BOTH the unprefixed local-dev path
    AND the /api-prefixed Cloud Run path. This fails automatically if a
    future router is ever added to only one registration list, instead of
    relying on someone remembering to add it to both places by hand.
    """
    api_main = _load_main_module()
    routes = _routes_by_path(api_main.app)

    missing = []
    for router in api_main.ALL_ROUTERS:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            prefixed_path = "/api" + route.path
            prefixed_methods = routes.get(prefixed_path)
            if prefixed_methods is None:
                missing.append((prefixed_path, "route not found"))
                continue
            if not route.methods.issubset(prefixed_methods):
                missing.append((prefixed_path, f"methods mismatch: {route.methods} vs {prefixed_methods}"))

    assert not missing, f"Routers missing /api-prefixed parity: {missing}"


def test_north_star_routes_have_api_prefix():
    """Explicit regression case for GitHub issue #28: north_star_router was
    added only to the local (unprefixed) registration block in V7.4.0, so
    GET /api/north-star/* 404'd through to the SPA catch-all on Cloud Run."""
    api_main = _load_main_module()
    routes = _routes_by_path(api_main.app)

    assert "GET" in routes.get("/api/north-star/panel", set())
    assert "POST" in routes.get("/api/north-star/flows/classify", set())
    assert "GET" in routes.get("/api/north-star/flows/unclassified", set())
    assert "GET" in routes.get("/api/north-star/contributions", set())


def test_value_trap_routes_have_api_prefix():
    """Explicit regression case for GitHub issue #29: value_trap_router was
    unreachable on Cloud Run under /api."""
    api_main = _load_main_module()
    routes = _routes_by_path(api_main.app)

    assert "POST" in routes.get("/api/reviews/value-trap/scan", set())


def test_governance_routes_have_api_prefix():
    """Explicit regression case for GitHub issue #29: governance_router was
    unreachable on Cloud Run under /api."""
    api_main = _load_main_module()
    routes = _routes_by_path(api_main.app)

    assert "GET" in routes.get("/api/governance/metrics", set())


def _assert_no_unprefixed_api_routes(api_main):
    """Every mounted path must be /api-prefixed or in NON_API_ALLOWLIST
    (imported from src.api.main — single source of truth; includes the
    UIS_SERVE_STATIC catch-all '/{full_path:path}' entry)."""
    non_api_routes = [
        route.path
        for route in api_main.app.routes
        if hasattr(route, "path")
        and not route.path.startswith("/api/")
        and route.path not in api_main.NON_API_ALLOWLIST
    ]
    assert non_api_routes == []


def test_cloud_run_mode_mounts_no_unprefixed_api_routes(monkeypatch):
    """Guard test: with UIS_GCS_BUCKET set (Cloud Run mode), only /api-prefixed
    routes plus NON_API_ALLOWLIST should be mounted, and module import must
    not raise."""
    monkeypatch.setenv("UIS_GCS_BUCKET", "test-bucket-does-not-exist")
    try:
        api_main = _load_main_module()
        _assert_no_unprefixed_api_routes(api_main)
    finally:
        _restore_default_module_state(monkeypatch)


def test_auth_token_without_bucket_mounts_no_unprefixed_api_routes(monkeypatch):
    """Fail-closed misconfig case: UIS_AUTH_TOKEN set but UIS_GCS_BUCKET
    unset (the two are independent secretKeyRefs in cloud-run-service.yaml
    and can diverge). BearerTokenMiddleware passes ALL non-/api GETs without
    auth (SPA-shell exemption), so unprefixed API routers in this mode would
    expose portfolio data unauthenticated. The app must boot (Docker smoke
    test flow) but mount ONLY /api routes + NON_API_ALLOWLIST."""
    monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)
    monkeypatch.setenv("UIS_AUTH_TOKEN", "test-token")
    try:
        api_main = _load_main_module()  # must not raise
        _assert_no_unprefixed_api_routes(api_main)
        # The /api surface itself must still be fully mounted
        routes = _routes_by_path(api_main.app)
        assert "GET" in routes.get("/api/north-star/panel", set())
        assert "POST" in routes.get("/api/auth/login", set())
    finally:
        _restore_default_module_state(monkeypatch)
