"""IBKR Flex Web Service client — 2-step protocol (v=3).

Public API
----------
fetch_flex_statement(token, query_id, *, timeout, max_polls, backoff_base, client)
    -> str                   raises FlexFetchError

fetch_and_save(dest_dir, token, query_id, *, now, **kw)
    -> pathlib.Path          writes IBKR_UIS_Report_<UTC-ISO-compact>.csv

Security contract
-----------------
The token is NEVER written to any log record or exception message.
Only query_id, reference_code, and byte counts are logged.
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_SEND_REQUEST_URL = (
    "https://ndcdyn.interactivebrokers.com"
    "/AccountManagement/FlexWebService/SendRequest"
)

# ---------------------------------------------------------------------------
# Typed error
# ---------------------------------------------------------------------------


class FlexFetchError(Exception):
    """Raised when the IBKR Flex Web Service returns a non-Success status.

    Attributes
    ----------
    code : str | None
        ErrorCode from the envelope (e.g. "1012", "1019").
    message : str | None
        ErrorMessage from the envelope.
    """

    def __init__(self, message: str, *, code: Optional[str] = None) -> None:
        # Guarantee the token never leaks into the message by construction.
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Envelope parser
# ---------------------------------------------------------------------------


def _parse_envelope(body: str) -> dict[str, Optional[str]]:
    """Parse a <FlexStatementResponse> XML envelope.

    Returns a dict with keys: status, reference_code, url, error_code,
    error_message.  Missing elements → None.
    Raises FlexFetchError on XML parse failure.
    """
    try:
        root = ET.fromstring(body.strip())
    except ET.ParseError as exc:
        raise FlexFetchError(f"Malformed XML envelope: {exc}") from exc

    def _text(tag: str) -> Optional[str]:
        el = root.find(tag)
        return el.text.strip() if el is not None and el.text else None

    return {
        "status": _text("Status"),
        "reference_code": _text("ReferenceCode"),
        "url": _text("Url"),
        "error_code": _text("ErrorCode"),
        "error_message": _text("ErrorMessage"),
    }


# ---------------------------------------------------------------------------
# Step 1 — SendRequest
# ---------------------------------------------------------------------------


def _send_request(token: str, query_id: str, timeout: float, client: Any) -> tuple[str, str]:
    """POST the Flex Web Service SendRequest and return (reference_code, url).

    Raises FlexFetchError on any non-Success status.
    The token is never logged.
    """
    params = {"t": token, "q": query_id, "v": "3"}
    logger.debug("FlexWebService SendRequest: query_id=%s", query_id)

    resp = client.get(_SEND_REQUEST_URL, params=params, timeout=timeout)
    resp.raise_for_status()

    envelope = _parse_envelope(resp.text)
    status = envelope["status"]

    if status != "Success":
        code = envelope.get("error_code")
        msg = envelope.get("error_message") or "Unknown error"
        logger.error(
            "FlexWebService SendRequest failed: query_id=%s code=%s",
            query_id, code,
        )
        raise FlexFetchError(f"SendRequest failed [{code}]: {msg}", code=code)

    reference_code = envelope["reference_code"]
    url = envelope["url"]

    if not reference_code or not url:
        raise FlexFetchError(
            "SendRequest succeeded but ReferenceCode or Url missing in envelope"
        )

    logger.info(
        "FlexWebService SendRequest OK: query_id=%s reference_code=%s",
        query_id, reference_code,
    )
    return reference_code, url


# ---------------------------------------------------------------------------
# Step 2 — GetStatement (with polling)
# ---------------------------------------------------------------------------

_IN_PROGRESS_CODE = "1019"


def _get_statement(
    token: str,
    reference_code: str,
    url: str,
    *,
    timeout: float,
    max_polls: int,
    backoff_base: float,
    client: Any,
) -> str:
    """Fetch the statement from the GetStatement endpoint.

    Polls up to max_polls times when ErrorCode 1019 (in progress).
    Returns statement text on success.
    Raises FlexFetchError on permanent error or exhausted polls.
    The token is never logged.
    """
    params = {"t": token, "q": reference_code, "v": "3"}

    for attempt in range(max_polls + 1):
        logger.debug(
            "FlexWebService GetStatement: reference_code=%s attempt=%d",
            reference_code, attempt,
        )
        resp = client.get(url, params=params, timeout=timeout)
        resp.raise_for_status()

        body = resp.text

        # Heuristic: if body starts with '<' it's an XML envelope (not CSV data).
        if not body.lstrip().startswith("<"):
            byte_count = len(body.encode())
            logger.info(
                "FlexWebService GetStatement OK: reference_code=%s bytes=%d",
                reference_code, byte_count,
            )
            return body

        # It's an envelope — parse it.
        envelope = _parse_envelope(body)
        status = envelope["status"]
        code = envelope.get("error_code")

        if code == _IN_PROGRESS_CODE:
            if attempt >= max_polls:
                raise FlexFetchError(
                    f"GetStatement still in progress after {max_polls} polls "
                    f"[code={code}]",
                    code=code,
                )
            delay = backoff_base ** attempt
            logger.info(
                "FlexWebService GetStatement in-progress: reference_code=%s "
                "attempt=%d/%d sleep=%.1fs",
                reference_code, attempt + 1, max_polls, delay,
            )
            time.sleep(delay)
            continue

        # Any other non-success envelope.
        msg = envelope.get("error_message") or "Unknown error"
        logger.error(
            "FlexWebService GetStatement error: reference_code=%s status=%s code=%s",
            reference_code, status, code,
        )
        raise FlexFetchError(
            f"GetStatement failed [{code}]: {msg}",
            code=code,
        )

    # Should not reach here; loop guard above raises.
    raise FlexFetchError("GetStatement exhausted all poll attempts")  # pragma: no cover


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_flex_statement(
    token: str,
    query_id: str,
    *,
    timeout: float = 30.0,
    max_polls: int = 5,
    backoff_base: float = 2.0,
    client: Any = None,
) -> str:
    """Fetch a Flex statement via the two-step IBKR Flex Web Service protocol.

    Parameters
    ----------
    token:
        Flex Web Service read-only token.  NEVER logged.
    query_id:
        Numeric Query ID from the Flex Query configuration page.
    timeout:
        Per-request HTTP timeout in seconds (default 30.0).
    max_polls:
        Maximum number of GetStatement retries when the report is still being
        generated (ErrorCode 1019).  Default 5.
    backoff_base:
        Base for exponential backoff between polls: delay = backoff_base ** attempt.
        Default 2.0 → delays of 1 s, 2 s, 4 s, 8 s, 16 s.
    client:
        Injectable httpx.Client (or any object with a .get() method).  When None,
        a fresh httpx.Client is created and closed after use.

    Returns
    -------
    str
        Statement text (CSV).

    Raises
    ------
    FlexFetchError
        On any Flex envelope error or if max_polls is exhausted.
    httpx.HTTPError
        On network-level failure (propagated unchanged).
    """
    _own_client = client is None
    if _own_client:
        import httpx  # noqa: PLC0415

        client = httpx.Client()

    try:
        reference_code, url = _send_request(token, query_id, timeout, client)
        return _get_statement(
            token,
            reference_code,
            url,
            timeout=timeout,
            max_polls=max_polls,
            backoff_base=backoff_base,
            client=client,
        )
    finally:
        if _own_client:
            client.close()


def fetch_and_save(
    dest_dir: "str | Path",
    token: str,
    query_id: str,
    *,
    now: Optional[datetime] = None,
    **kw: Any,
) -> Path:
    """Fetch a Flex statement and write it to dest_dir.

    The file is named ``IBKR_UIS_Report_<UTC-ISO-compact>.csv``
    (e.g. ``IBKR_UIS_Report_20260617T084500Z.csv``) — matching the glob pattern
    ``IBKR_UIS_Report*.csv`` that the IBKR reader uses.

    Parameters
    ----------
    dest_dir:
        Directory to write the file (must exist).
    token:
        Flex Web Service token (read-only).
    query_id:
        Flex Query ID.
    now:
        Injectable UTC datetime for deterministic filenames in tests.  When
        None, ``datetime.now(timezone.utc)`` is used.
    **kw:
        Forwarded to :func:`fetch_flex_statement` (e.g. ``timeout``,
        ``max_polls``, ``client``).

    Returns
    -------
    pathlib.Path
        Absolute path to the written file.
    """
    dest_dir = Path(dest_dir)

    if now is None:
        now = datetime.now(timezone.utc)

    ts = now.strftime("%Y%m%dT%H%M%SZ")
    filename = f"IBKR_UIS_Report_{ts}.csv"
    dest = dest_dir / filename

    text = fetch_flex_statement(token, query_id, **kw)
    dest.write_text(text, encoding="utf-8")
    logger.info("Flex statement written: %s (%d bytes)", dest, len(text.encode()))
    return dest
