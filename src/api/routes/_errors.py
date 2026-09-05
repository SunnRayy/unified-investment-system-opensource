# src/api/routes/_errors.py
"""Shared error-contract helpers for Rule 12 compliance.

FROZEN CONTRACT (Pass 1 — agent-trust-rule12 branch):
  - Success + data             → 200 + normal payload (unchanged)
  - Success + genuinely empty  → 200 + [] (lists) / {} (objects)  — NOT an error
  - Failure (unhandled exc)    → 5xx + {"error": {"code": str, "message": str}}
    503 for upstream/feed/GCS failures; 500 for internal logic errors.

Response shape note: we raise via HTTPException so FastAPI's exception handler
runs, but we set the content-type to application/json and use a body that matches
the contract exactly — {"error": {...}} — NOT {"detail": {"error": {...}}}.
FastAPI's default behaviour wraps detail in {"detail": ...}, so api_error_response
uses a custom JSONResponse body to guarantee the spec shape.

Usage:
    from src.api.routes._errors import api_error_response
    except Exception as e:
        logger.exception("Context about what failed")
        raise api_error_response(e, context="get_insights")

See: AGENTS.md Core Doctrine + Rule 12, docs/plans/2026-05-29-pass1-*.md
"""
import logging
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Upstream exceptions that should map to 503 (service unavailable, caller should retry)
_UPSTREAM_PATTERNS = (
    "timeout", "connect", "connection", "refused", "unreachable",
    "gcs", "gcloud", "storage", "s3", "bucket",
    "feed", "yfinance", "akshare", "fred", "requests", "httpx",
)


def _classify_status(exc: Exception) -> int:
    """Return 503 for upstream/feed/connectivity failures, 500 for internal errors."""
    msg = str(exc).lower()
    if any(p in msg for p in _UPSTREAM_PATTERNS):
        return 503
    return 500


# see AGENTS.md Rule 12 (error contract — return JSONResponse, never raise JSONResponse)
class ApiErrorResponse(JSONResponse):
    """Rule-12-compliant error response.

    Returned via `raise` so FastAPI's exception handler picks it up correctly.
    Shape: {"error": {"code": str, "message": str}} — NOT {"detail": ...}.
    """


def api_error_response(
    exc: Exception,
    context: str = "",
    status_code: int = 0,
) -> "ApiErrorResponse":
    """Build a Rule-12-compliant error response for unhandled endpoint failures.

    Caller must `return` the result from the route function. FastAPI will
    send it directly to the client with the given status code.

    The response shape is exactly: {"error": {"code": ..., "message": ...}}

    The exception message is safe — no stack trace, no file paths, no internal
    secrets exposed to the client.

    Args:
        exc: the caught exception
        context: short description of what was happening (for the error code)
        status_code: override; 0 = auto-classify (recommended)
    """
    code = status_code or _classify_status(exc)
    error_code = f"ERR_{context.upper()}" if context else "ERR_INTERNAL"
    # Safe message: exception type only — str(exc) may contain paths/SQL/internals.
    safe_message = f"{type(exc).__name__} while processing {context}" if context else type(exc).__name__
    return ApiErrorResponse(
        status_code=code,
        content={"error": {"code": error_code, "message": safe_message}},
    )
