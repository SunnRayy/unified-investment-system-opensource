"""Single resolver for the AI advisor's output language (Program BIL / WS-5).

Modelled on ``src/services/goal_resolver.py`` (V7.7.0), including its
explicit ``config_fallback`` labelling: the fallback is always a *labelled*
state with a reason, never a silent substitution (AGENTS.md Rule 12). This
function never returns None, never raises, and never blanks the page.

WHY A PERSISTED VALUE EXISTS
----------------------------
Interactive generation can send the frontend's current locale. Scheduled
server-side generation cannot — a cron job has no request and no browser
locale. Without a persisted preference the nightly brief would silently be
generated in whatever the code's default happened to be. That is the single
worst outcome of this workstream for the owner, whose briefs are Chinese.

PRECEDENCE (highest first)
--------------------------
1. ``request_language``      — explicit per-request override from the UI
2. ``user_profile.language`` — the persisted preference (V89)
3. ``settings.yaml: language`` — deployment default
4. ``'en'``                  — public/fresh-install default

``user_profile.language`` is **nullable with no schema DEFAULT**. DuckDB's
``ALTER TABLE ADD COLUMN ... DEFAULT`` backfill semantics are not verified in
this repo, and the sibling column (``philosophy``) was added without one, so
V89 sets the owner's value with an explicit data step instead of trusting a
DEFAULT to reach existing rows. When the column is NULL the resolver logs a
WARNING (once) so the state is visible rather than silently English.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.services.ai_advisor.section_ids import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

#: Tolerated spellings for a supported language. i18next may hand us a bare
#: ``zh``, a region variant, or an underscore form depending on the browser.
_LANGUAGE_ALIASES: dict[str, str] = {
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh_cn": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-hans-cn": "zh-CN",
}

# Module-level latch so the "language is NULL" warning is loud once per process
# rather than once per brief — a warning printed on every request is a warning
# nobody reads.
_null_language_warned = False


def canonical_language(raw: Any) -> Optional[str]:
    """Return a supported language code for ``raw``, or None if unrecognised."""
    if not isinstance(raw, str):
        return None
    key = raw.strip().replace("_", "-").lower()
    if not key:
        return None
    resolved = _LANGUAGE_ALIASES.get(key)
    if resolved:
        return resolved
    # Bare-prefix match: 'zh-Hant-TW' → zh-CN is deliberately NOT done here.
    # Only exact aliases resolve; anything else is unrecognised on purpose.
    return raw if raw in SUPPORTED_LANGUAGES else None


def resolve_language(db=None, request_language: Optional[str] = None) -> dict:
    """Resolve the single authoritative AI-advisor output language.

    Args:
        db: an open DB handle exposing ``.execute()``. When None, a read-only
            connection is opened and closed here. A failure to read is a
            labelled fallback, never an exception out of this function.
        request_language: per-request override (the UI's current locale).

    Returns a dict with keys::

        language        str  — always a supported code, never None
        source          str  — "request" | "user_profile" | "config_fallback" | "default"
        fallback_reason str | None — None when source is "request"/"user_profile"
    """
    # 1 ── explicit request override
    if request_language is not None:
        canonical = canonical_language(request_language)
        if canonical:
            return {"language": canonical, "source": "request", "fallback_reason": None}
        logger.warning(
            "resolve_language: unsupported request language %r — falling through to profile",
            request_language,
        )

    # 2 ── persisted preference
    stored, read_error = _read_profile_language(db)
    if stored is not None:
        canonical = canonical_language(stored)
        if canonical:
            return {"language": canonical, "source": "user_profile", "fallback_reason": None}
        logger.warning(
            "resolve_language: user_profile.language holds unsupported value %r — ignoring",
            stored,
        )
        read_error = f"user_profile.language unsupported value {stored!r}"
    elif read_error is None:
        read_error = "user_profile.language is NULL"
        _warn_null_language_once()

    # 3 ── settings.yaml deployment default
    configured = _read_settings_language()
    if configured is not None:
        return {
            "language": configured,
            "source": "config_fallback",
            "fallback_reason": read_error,
        }

    # 4 ── public/fresh-install default
    return {
        "language": DEFAULT_LANGUAGE,
        "source": "default",
        "fallback_reason": read_error or "no language configured",
    }


def resolve_language_code(db=None, request_language: Optional[str] = None) -> str:
    """Convenience wrapper for callers that only need the code."""
    return resolve_language(db, request_language)["language"]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _warn_null_language_once() -> None:
    global _null_language_warned
    if _null_language_warned:
        return
    _null_language_warned = True
    logger.warning(
        "user_profile.language is NULL — AI advisor output language falls back to "
        "settings.yaml, then to %r. Set it via the profile settings to pin the "
        "language for scheduled (non-interactive) generation.",
        DEFAULT_LANGUAGE,
    )


def _read_profile_language(db) -> tuple[Optional[str], Optional[str]]:
    """Return ``(value, error_reason)``. Both None means "row present, column NULL"."""
    owns_connection = db is None
    try:
        if owns_connection:
            from src.database.connector import connect_readonly_with_retry  # noqa: PLC0415

            db = connect_readonly_with_retry()
        try:
            row = db.execute(
                "SELECT language FROM user_profile WHERE id = 1"
            ).fetchone()
        finally:
            if owns_connection:
                db.close()
    except Exception as e:
        logger.warning("resolve_language: user_profile read failed (%s)", e)
        return None, f"user_profile read failed: {type(e).__name__}"

    if row is None:
        return None, "no user_profile row"
    return (row[0] if row[0] else None), None


def _read_settings_language() -> Optional[str]:
    try:
        from src.services.settings_manager import get_configured_language  # noqa: PLC0415

        return get_configured_language()
    except Exception as e:
        logger.warning("resolve_language: settings.yaml read failed (%s)", e)
        return None
