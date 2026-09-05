import asyncio
import os
import re

import bcrypt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.api import stream_tickets

# Matches POST /settings/sources/fetch/<reader> with or without /api prefix.
# Anchored; single path segment (no further slashes) for the reader name.
_IS_SCHEDULER_FETCH_PATH = re.compile(r"^(/api)?/settings/sources/fetch/[^/]+$")


class BearerTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Local dev mode: if UIS_AUTH_TOKEN is not set, pass all requests
        if not os.getenv("UIS_AUTH_TOKEN"):
            return await call_next(request)

        path = request.url.path

        # OPTIONS always passes (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Health checks always pass (no auth required)
        if path in ("/health", "/health/deep"):
            return await call_next(request)

        # Login endpoint passes without token (bootstraps auth)
        if request.method == "POST" and path in ("/auth/login", "/api/auth/login"):
            return await call_next(request)

        # SSE endpoint: accept ?ticket=<ticket> query param (EventSource can't set headers).
        # The ticket is issued by POST /sync/stream-ticket (requires normal Bearer auth),
        # so the password never appears in the URL.  A leaked ticket is time-limited and
        # grants only this read-only log stream — not general API access.
        if request.method == "GET" and path in ("/api/sync/stream", "/sync/stream"):
            ticket = request.query_params.get("ticket")
            if ticket and stream_tickets.validate(ticket):
                return await call_next(request)
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # SPA shell and static assets (non-/api/ GETs) pass always
        if request.method == "GET" and not path.startswith("/api/"):
            return await call_next(request)

        # Cloud Scheduler OIDC carve-out: accept a Google-signed OIDC token from the
        # scheduler service account for the IBKR fetch path ONLY. Falls through to the
        # normal password-bearer check on any failure (so the owner's UI trigger still
        # works on this path).
        if request.method == "POST" and _IS_SCHEDULER_FETCH_PATH.match(path):
            sa_email = os.getenv("UIS_SCHEDULER_SA_EMAIL")
            audience = os.getenv("UIS_SCHEDULER_AUDIENCE")
            oidc_header = request.headers.get("authorization", "")
            if sa_email and audience and oidc_header.startswith("Bearer "):
                if await self._validate_oidc_async(oidc_header[7:], sa_email, audience):
                    return await call_next(request)
            # else: fall through to normal bearer validation below

        # All other requests: require valid Authorization header
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        raw_token = auth_header[7:]
        if not await self._validate_token_async(raw_token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    async def _validate_token_async(self, token: str) -> bool:
        """Run the (blocking, retrying) token validation off the event loop so the
        read-only retry never stalls other concurrent requests."""
        return await asyncio.get_event_loop().run_in_executor(None, self._validate_token, token)

    def _validate_token(self, token: str) -> bool:
        """Validate bearer token against the in-memory credential cache.

        Format: ``<password>.<version>`` (versioned) or legacy plain token.
        Reads ONLY from the cache — never opens a DB connection — so this
        method is safe to call while a sync holds a read-write DuckDB connection.
        """
        from src.api import auth_cache
        creds = auth_cache.get()
        if creds is None:
            return False  # Cache not yet populated → fail closed

        last_dot = token.rfind(".")

        if last_dot == -1:
            # Legacy format (no version separator).
            # Accept only when no credentials are configured; in that case fall
            # back to the plain UIS_AUTH_TOKEN env-var value.
            if creds.configured:
                return False  # Credentials exist → reject legacy tokens
            return token == os.getenv("UIS_AUTH_TOKEN", "")

        password, version_str = token[:last_dot], token[last_dot + 1:]
        try:
            version = int(version_str)
        except ValueError:
            return False

        if not creds.configured or creds.password_hash is None:
            return False  # Versioned token but no credentials → reject (fail-closed)

        try:
            return version == creds.token_version and bcrypt.checkpw(
                password.encode(), creds.password_hash.encode()
            )
        except (ValueError, TypeError):
            return False  # Malformed stored hash → reject, not 500

    async def _validate_oidc_async(self, token: str, sa_email: str, audience: str) -> bool:
        """Run the blocking OIDC verify off the event loop so the cert-fetch network
        call never stalls other concurrent requests."""
        return await asyncio.get_event_loop().run_in_executor(
            None, self._validate_oidc, token, sa_email, audience
        )

    def _validate_oidc(self, token: str, expected_sa_email: str, audience: str) -> bool:
        """Validate a Google-signed OIDC id_token from the Cloud Scheduler SA.
        Network-only (no DB). Returns False on any failure (fail-closed)."""
        try:
            from google.auth.transport import requests as g_requests  # noqa: PLC0415
            from google.oauth2 import id_token  # noqa: PLC0415
        except ImportError:
            return False
        try:
            claims = id_token.verify_oauth2_token(token, g_requests.Request(), audience=audience)
        except Exception:
            return False
        # The security boundary is: Google-signed token (verify_oauth2_token) + audience
        # match (enforced above) + the SA email. email_verified is belt-and-suspenders;
        # Google may emit it as a bool, the string "true", or omit it for SA tokens, so
        # reject only when it is explicitly false — never silently 401 a valid scheduler.
        verified = claims.get("email_verified", True)
        if isinstance(verified, str):
            verified = verified.strip().lower() == "true"
        return bool(verified) and claims.get("email") == expected_sa_email
