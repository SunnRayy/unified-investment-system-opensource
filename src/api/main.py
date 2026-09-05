import mimetypes
import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()  # reads .env file into os.environ before any env-var reads
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from src.api.routes import sync, data, compass, performance, verification, decisions, analytics
from src.api.routes.balance_sheet import router as balance_sheet_router
from src.api.routes.income_expense import router as income_expense_router
from src.api.routes.market import router as market_router
from src.api.routes.taxonomy import router as taxonomy_router
from src.api.routes.risk_profiles import router as risk_profile_router
from src.api.routes.management import router as management_router
from src.api.routes.sentiment import router as sentiment_router
from src.api.routes.export import router as export_router
from src.api.routes.integrity import router as integrity_router
from src.api.routes.audit_v2 import router as audit_v2_router
from src.api.routes.operations import router as operations_router
from src.api.routes.strategy import router as strategy_router
from src.api.routes.ai_advisor import router as ai_advisor_router
from src.api.routes.market_data import router as market_data_router
from src.api.routes.settings import router as settings_router
from src.api.routes.auth import router as auth_router
from src.api.middleware.auth import BearerTokenMiddleware
from src.api.routes.valuation import router as valuation_router
from src.api.routes.value_trap import router as value_trap_router
from src.api.routes.governance import router as governance_router
from src.api.routes.north_star import router as north_star_router
from src.api.routes.reader_mappings import router as reader_mappings_router
from src.api.routes.manual_pnl import router as manual_pnl_router
from src.api.routes.attribution import router as attribution_router
from src.api.routes.forecast import router as forecast_router
from src.api.dependencies import DatabaseConfigurationError, validate_operational_database
from src.database.connector import DatabaseConnector
from src.database.schema import bootstrap_database
from src.market_data.scheduler import MarketDataScheduler
from src.storage.gcs import (
    download_db_from_gcs, download_settings_from_gcs, download_sources_from_gcs,
    download_seed_pack_from_gcs, download_reference_sheet_from_gcs,
    download_verification_from_gcs,
)

logger = logging.getLogger(__name__)

def _skip_startup_validation() -> bool:
    return os.getenv("UIS_SKIP_DB_STARTUP_VALIDATION") == "1" or "pytest" in sys.modules


def _seed_auth_credentials(db) -> None:
    """Seed auth_credentials on first boot. Idempotent — skips if row exists."""
    import secrets
    import bcrypt as _bcrypt
    try:
        row = db.execute("SELECT COUNT(*) FROM auth_credentials").fetchone()
        if row and row[0] > 0:
            return  # already seeded
        env_token = os.getenv("UIS_AUTH_TOKEN")
        if env_token:
            pw = env_token
        else:
            pw = secrets.token_hex(16)
            logger.warning("FIRST_BOOT_CREDENTIAL: one-time password = %s (store this securely, it will not be shown again)", pw)
        pw_hash = _bcrypt.hashpw(pw.encode(), _bcrypt.gensalt()).decode()
        db.execute(
            "INSERT INTO auth_credentials (id, password_hash, token_version) VALUES (1, ?, 1) ON CONFLICT(id) DO NOTHING",
            [pw_hash]
        )
    except Exception as e:
        logger.warning("Could not seed auth_credentials: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run database migrations on startup and start optional auto-refresh scheduler."""
    db = None
    scheduler = None

    bucket = os.getenv("UIS_GCS_BUCKET")
    if bucket:
        os.makedirs("/tmp/data", exist_ok=True)
        os.makedirs("/tmp/sources", exist_ok=True)
        os.environ.setdefault("UIS_DB_PATH", "/tmp/data/unified.duckdb")
        local_db_path = os.environ["UIS_DB_PATH"]

        has_db = download_db_from_gcs(bucket, local_db_path)
        if not has_db and os.getenv("UIS_FIRST_DEPLOY") != "1":
            raise RuntimeError(
                f"No database found at gs://{bucket}/db/unified.duckdb. "
                "Set UIS_FIRST_DEPLOY=1 for initial deployment."
            )

        # Restore user-saved settings (model config, prompts) from GCS if present
        settings_local = str(Path(__file__).parents[2] / "config" / "settings.yaml")
        try:
            download_settings_from_gcs(bucket, settings_local)
        except Exception as e:
            logger.warning("Could not restore settings.yaml from GCS: %s", e)

        # Restore the other two real (gitignored) configs the same way — Program
        # OSR WS-4b. Neither has an in-app edit path, so this is restore-at-boot
        # only; if GCS has no copy yet, src.config._resolve_config_file's
        # .example fallback keeps the app booting on the committed template.
        config_root = Path(__file__).parents[2] / "config"
        try:
            download_reference_sheet_from_gcs(bucket, str(config_root / "reference_sheet.yaml"))
        except Exception as e:
            logger.warning("Could not restore reference_sheet.yaml from GCS: %s", e)
        try:
            download_verification_from_gcs(bucket, str(config_root / "verification.yaml"))
        except Exception as e:
            logger.warning("Could not restore verification.yaml from GCS: %s", e)

        try:
            download_sources_from_gcs(bucket, "/tmp/sources")
        except Exception as e:
            logger.warning("Failed to download source files from GCS: %s", e)

        # Restore a PRIVATE seed pack from GCS if $UIS_SEED_PROFILE names one.
        # Program OSR WS-3b: build-up only — this restore path exists so
        # activation (setting the env var + deploying) is possible later, but
        # activating it is a separate, explicitly-authorized step. Public
        # profiles (example/empty) ship inside the image via the Dockerfile
        # instead and never need this restore.
        seed_profile = os.getenv("UIS_SEED_PROFILE")
        if seed_profile and seed_profile not in ("example", "empty"):
            seeds_local_dir = str(Path(__file__).parents[2] / "seeds" / seed_profile)
            try:
                download_seed_pack_from_gcs(bucket, seed_profile, seeds_local_dir)
            except Exception as e:
                logger.warning("Could not restore seed pack %r from GCS: %s", seed_profile, e)

    # Seed any reader keys that the app knows about but are absent from the
    # persisted source_registry (e.g. 'ibkr' missing from a pre-Workstream-C
    # GCS snapshot).  Must run AFTER download_settings_from_gcs so it operates
    # on the GCS-restored file.  Wrapped in try/except — never crashes startup.
    try:
        from src.services.settings_manager import seed_missing_readers  # noqa: PLC0415
        _seeded = seed_missing_readers()
        if _seeded:
            logger.info("startup: seeded missing reader(s) into settings.yaml: %s", _seeded)
    except Exception as e:
        logger.warning("startup: seed_missing_readers failed (non-fatal): %s", e)

    try:
        db = DatabaseConnector()
        bootstrap_database(db)
        _seed_auth_credentials(db)
        if not _skip_startup_validation():
            validate_operational_database(db)
        # Populate auth cache while the startup connection is still open so
        # the hot path (login + _validate_token) never needs to open its own
        # DB connection — critical when a sync holds a read-write connection.
        from src.api import auth_cache
        auth_cache.refresh_from_db(db)
    except DatabaseConfigurationError as e:
        raise RuntimeError(f"Database startup validation failed: {e}") from e
    except Exception as e:
        # In pytest-xdist parallel mode multiple workers each start a TestClient(app).
        # Every lifespan tries to open the DB in read-write mode; DuckDB allows only
        # one writer at a time.  Rather than crashing the whole worker (which makes
        # unrelated tests fail), log a warning and let the app start without DB init.
        # The DB is already fully migrated on developer machines; tests that need live
        # data create their own DatabaseConnector() calls inside the request handler.
        import sys as _sys
        if "pytest" in _sys.modules and "Could not set lock" in str(e):
            logger.warning(
                "startup: DB write-lock held by another test worker — "
                "skipping DB init in pytest/parallel mode (non-fatal)."
            )
        else:
            raise RuntimeError(f"Database startup failed: {e}") from e
    finally:
        if db:
            db.close()

    # Start market data auto-refresh scheduler if configured
    try:
        import yaml
        settings_path = Path(__file__).parents[2] / "config" / "settings.yaml"
        with open(settings_path) as f:
            settings = yaml.safe_load(f) or {}
        auto_refresh = settings.get("market_data", {}).get("auto_refresh", {})
        if auto_refresh.get("enabled", False):
            interval = int(auto_refresh.get("interval_minutes", 30))
            scheduler = MarketDataScheduler()
            await scheduler.start(interval_minutes=interval)
    except Exception as e:
        logger.warning(f"Could not start market data scheduler: {e}")

    from src.storage.gcs_flush import start_flush_task
    await start_flush_task()

    yield

    # Stop scheduler on shutdown
    if scheduler is not None:
        await scheduler.stop()

    from src.storage.gcs_flush import stop_flush_task
    await stop_flush_task()

app = FastAPI(title="Huinsight Command Center API", lifespan=lifespan)

# Configure CORS
_allowed_origin = os.getenv("UIS_ALLOWED_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_allowed_origin] if _allowed_origin != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(BearerTokenMiddleware)

# Single canonical router list — order matches the historical local-dev
# registration order. Both the unprefixed (local dev) and /api-prefixed
# (Cloud Run) mount points MUST loop over this SAME list so a router can
# never be added to one path without the other (root cause of #28/#29:
# value_trap_router, governance_router, north_star_router were added only
# to the local block during V7.4.0 and were unreachable on Cloud Run).
ALL_ROUTERS = [
    sync.router,
    data.router,
    performance.router,
    compass.router,
    verification.router,
    decisions.router,
    balance_sheet_router,
    income_expense_router,
    market_router,
    analytics.router,
    taxonomy_router,
    risk_profile_router,
    management_router,
    sentiment_router,
    export_router,
    integrity_router,
    audit_v2_router,
    operations_router,
    strategy_router,
    ai_advisor_router,
    market_data_router,
    settings_router,
    valuation_router,
    value_trap_router,
    governance_router,
    north_star_router,
    reader_mappings_router,
    manual_pnl_router,
    attribution_router,
    forecast_router,
    auth_router,
]

# Two independent deployment signals (independent secretKeyRefs in
# deploy/cloud-run-service.yaml — they CAN diverge on a misconfigured deploy):
#   _is_cloud_run  — GCS bucket configured; the app is serving the public URL.
#   _auth_enabled  — bearer-token auth is on. BearerTokenMiddleware exempts
#                    ALL non-/api GET requests (SPA shell/static assets), so
#                    whenever auth is enabled the ONLY safe API surface is
#                    /api/*; mounting unprefixed routers alongside the token
#                    would serve portfolio data unauthenticated (fail-open).
# Unprefixed (local-dev convenience) routers are therefore mounted only when
# BOTH signals are off. The Docker smoke test (token set, no bucket) still
# boots fine: it exercises /health + /api/auth/login, both on the /api surface.
_is_cloud_run = bool(os.getenv("UIS_GCS_BUCKET"))
_auth_enabled = bool(os.getenv("UIS_AUTH_TOKEN"))
if not _is_cloud_run and not _auth_enabled:
    for _router in ALL_ROUTERS:
        app.include_router(_router)

# /api prefix registration (for production Cloud Run where Vite proxy is absent)
_api_router = APIRouter(prefix="/api")
for _router in ALL_ROUTERS:
    _api_router.include_router(_router)
app.include_router(_api_router)

# Paths that may legitimately exist without the /api prefix in cloud /
# auth-enabled mode. Module-level so the structural-guard test can import it.
# "/{full_path:path}" is the UIS_SERVE_STATIC SPA catch-all registered at the
# bottom of this module (harmless in the allowlist when static serving is off;
# in auth-enabled mode the middleware's non-/api GET exemption intentionally
# lets it serve the SPA shell + assets).
# The structural guard itself runs at the very END of this module — after
# /health, /health/deep, and the static catch-all are registered — so the
# allowlist is checked against the app's complete, final route table.
NON_API_ALLOWLIST = frozenset({
    "/health",
    "/health/deep",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/{full_path:path}",
})

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "version": os.getenv("APP_VERSION", "dev"),
        "sha": os.getenv("APP_SHA", "local"),
    }


# see ADR-006 (GCS health probe discipline — blob.exists() only, no writes from health checks)
@app.get("/health/deep")
async def health_deep():
    """Per-subsystem health check — read-only, no shared-state writes.

    Returns safe status information (booleans/enums/timestamps).
    Intentionally excludes: secrets, tokens, full filesystem paths, bucket
    internals. Agents and CI can poll this to verify system state without
    triggering a sync or integrity run.

    GCS check: config + metadata reachability only (no write→read round-trip
    in Pass 1; full round-trip probe deferred to Pass C/D).
    """
    result: dict = {
        "status": "ok",
        "version": os.getenv("APP_VERSION", "dev"),
        "subsystems": {},
    }
    has_degraded = False

    # ── DB ──────────────────────────────────────────────────────────────────
    try:
        # read_only=True: health check must never acquire a write lock
        conn = DatabaseConnector(read_only=True)
        try:
            row = conn.execute("SELECT COUNT(*) FROM holdings WHERE is_shadow = FALSE").fetchone()
            holdings_count = int(row[0]) if row else 0
            result["subsystems"]["db"] = {
                "ok": True,
                "tables_present": holdings_count > 0,
                "active_holdings_count": holdings_count,
            }
        finally:
            conn.close()
    except Exception as e:
        # Safe error: report type only, no paths or internal details
        result["subsystems"]["db"] = {"ok": False, "error": type(e).__name__}
        has_degraded = True

    # ── Readers (file presence check, no parsing) ──────────────────────────
    try:
        from src.services.settings_manager import load_settings, load_source_registry
        settings = load_settings()
        finance_dir = settings.get("finance_dir", "")
        source_registry = load_source_registry()
        enabled_sources = [k for k, v in source_registry.items() if v.get("enabled", False)]
        result["subsystems"]["readers"] = {
            "ok": bool(finance_dir),
            "finance_dir_configured": bool(finance_dir),
            "enabled_source_count": len(enabled_sources),
        }
    except Exception as e:
        result["subsystems"]["readers"] = {"ok": False, "error": type(e).__name__}
        has_degraded = True

    # ── Feeds (freshness from cached/audit data — read-only) ────────────────
    try:
        conn2 = DatabaseConnector(read_only=True)
        feed_status: dict = {}
        try:
            try:
                sentiment_row = conn2.execute(
                    "SELECT is_stale, updated_at FROM market_sentiment_cache ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
                if sentiment_row:
                    feed_status["sentiment"] = "stale" if sentiment_row[0] else "fresh"
                    feed_status["sentiment_updated_at"] = str(sentiment_row[1]) if sentiment_row[1] else None
                else:
                    feed_status["sentiment"] = "no_data"
            except Exception:
                feed_status["sentiment"] = "unknown"
            try:
                audit_row = conn2.execute(
                    "SELECT created_at FROM sync_audit_reports ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                feed_status["last_sync"] = str(audit_row[0]) if audit_row else None
            except Exception:
                feed_status["last_sync"] = "unknown"
            result["subsystems"]["feeds"] = {"ok": True, **feed_status}
        finally:
            conn2.close()
    except Exception as e:
        result["subsystems"]["feeds"] = {"ok": False, "error": type(e).__name__}
        has_degraded = True

    # ── GCS (config presence + metadata reachability only, no round-trip) ──
    gcs_bucket = os.getenv("UIS_GCS_BUCKET", "")
    if not gcs_bucket:
        result["subsystems"]["gcs"] = {"ok": True, "configured": False, "note": "local mode"}
    else:
        try:
            from google.cloud import storage as _gcs
            _client = _gcs.Client()
            _bucket = _client.bucket(gcs_bucket)
            # Lightweight metadata check — does not download or upload
            # Path must match download_db_from_gcs() which uses "db/unified.duckdb"
            _blob = _bucket.blob("db/unified.duckdb")
            _exists = _blob.exists()
            result["subsystems"]["gcs"] = {
                "ok": True,
                "configured": True,
                "db_blob_present": _exists,
            }
        except Exception as e:
            result["subsystems"]["gcs"] = {"ok": False, "configured": True, "error": type(e).__name__}
            has_degraded = True

    # ── GCS persistence state (from flush tracker — zero-cost, no I/O) ────
    try:
        from src.storage.gcs_flush import get_flush_status
        flush_status = get_flush_status()
        result["subsystems"]["gcs_persistence"] = {
            "ok": True,
            "write_seq": flush_status["write_seq"],
            "flushed_seq": flush_status["flushed_seq"],
            "dirty": flush_status["dirty"],
            "last_flush_time": flush_status["last_flush_time"],
            "last_flush_generation": flush_status["last_flush_generation"],
            "last_flush_error": flush_status["last_flush_error"],
        }
        # Mark as degraded if there is a recorded flush error (last flush failed)
        if flush_status["last_flush_error"] is not None:
            result["subsystems"]["gcs_persistence"]["ok"] = False
            has_degraded = True
    except Exception as e:
        result["subsystems"]["gcs_persistence"] = {"ok": False, "error": type(e).__name__}
        has_degraded = True

    if has_degraded:
        result["status"] = "degraded"

    return result


# Static file serving (production only).
# A single catch-all route replaces StaticFiles mount: serve real files by path,
# fall back to index.html for React Router paths. This prevents the catch-all from
# returning index.html for JS/CSS assets (MIME type mismatch crash).
if os.getenv("UIS_SERVE_STATIC") == "1":
    _static_dir = Path(__file__).parents[2] / "output" / "ux-command-center"
    if _static_dir.exists():
        from fastapi.responses import FileResponse as _FileResponse

        _index_html = _static_dir / "index.html"

        # Vite emits content-hashed filenames (index-upMfM0NR.js), so an asset's
        # URL changes whenever its bytes do. Those are safe to cache forever.
        # index.html must NOT be: it is the document that names the current
        # hashes, so caching it would pin a browser to a stale deployment.
        _IMMUTABLE = "public, max-age=31536000, immutable"
        _REVALIDATE = "no-cache"

        def _static_response(path: Path, accept_encoding: str) -> _FileResponse:
            """Serve `path`, preferring the build-time .gz sibling when accepted.

            Responses previously carried no Cache-Control at all. With an ETag
            but no freshness lifetime a browser must revalidate every asset on
            every load — a round trip each, which on a high-RTT link costs more
            than the bytes do.
            """
            immutable = "/assets/" in path.as_posix()
            headers = {"Cache-Control": _IMMUTABLE if immutable else _REVALIDATE}

            gz = path.with_suffix(path.suffix + ".gz")
            if "gzip" in accept_encoding.lower() and gz.is_file():
                # media_type comes from the *uncompressed* name: `.js.gz` would
                # otherwise be sniffed as application/gzip and downloaded
                # instead of executed.
                media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                headers["Content-Encoding"] = "gzip"
                # The response varies by request header, so shared caches must
                # not serve a gzipped body to a client that cannot read it.
                headers["Vary"] = "Accept-Encoding"
                return _FileResponse(str(gz), media_type=media_type, headers=headers)
            return _FileResponse(str(path), headers=headers)

        @app.get("/{full_path:path}", include_in_schema=False)
        async def _spa_fallback(full_path: str, request: Request):
            accept_encoding = request.headers.get("accept-encoding", "")
            if full_path:
                candidate = (_static_dir / full_path).resolve()
                # Security: ensure resolved path stays within static dir
                try:
                    candidate.relative_to(_static_dir.resolve())
                except ValueError:
                    raise HTTPException(status_code=404)
                # A request for foo.js.gz must not be served directly — the .gz
                # files exist only as encodings of their siblings, never as
                # resources in their own right.
                if candidate.is_file() and candidate.suffix != ".gz":
                    return _static_response(candidate, accept_encoding)
            if _index_html.exists():
                return _static_response(_index_html, accept_encoding)
            raise HTTPException(status_code=404)


# Structural invariant (fail-closed): in cloud mode OR whenever bearer auth is
# enabled, every mounted route must be /api-prefixed or in NON_API_ALLOWLIST.
# _auth_enabled matters independently of _is_cloud_run because the middleware
# passes ALL non-/api GETs without auth (SPA-shell exemption) — an unprefixed
# API router in that mode would be publicly readable. This block is at the END
# of the module, after ALL route registration (health endpoints + the
# UIS_SERVE_STATIC catch-all), so the allowlist is enforced against the real,
# complete route table rather than a partial one.
if _is_cloud_run or _auth_enabled:
    _all_paths = [r.path for r in app.routes if hasattr(r, "path")]
    _unprefixed = [
        p for p in _all_paths
        if not p.startswith("/api/") and p not in NON_API_ALLOWLIST
    ]
    if _unprefixed:
        raise RuntimeError(
            f"Cloud/auth mode but unprefixed API routes mounted: {_unprefixed}"
        )
