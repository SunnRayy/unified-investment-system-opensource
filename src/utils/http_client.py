"""Shared resilient HTTP client for all external data fetches.

Provides a module-level requests.Session with automatic retry/backoff
(2 retries, 0.5s backoff, on 429/5xx) and connection pooling.

Usage
-----
    from src.utils.http_client import http_get

    resp = http_get("https://example.com/api/data", timeout=15)
    data = resp.json()
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Browser-like User-Agent required by some APIs (CNN, goldprice.org) that
# block the default python-requests UA with 403/418 responses.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_DEFAULT_TIMEOUT = 15  # seconds — allows 2 retries within a 45s window

# Thread-local storage so each thread gets its own Session (thread-safe).
_local = threading.local()


def get_session() -> requests.Session:
    """Return the thread-local resilient Session, creating it if needed."""
    if not hasattr(_local, "session"):
        retry = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods={"GET", "POST"},
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=10,
            pool_maxsize=20,
        )
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"User-Agent": _BROWSER_UA})
        _local.session = session
    return _local.session


def http_get(
    url: str,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> requests.Response:
    """Perform a GET request using the resilient session.

    Parameters
    ----------
    url:
        The URL to fetch.
    timeout:
        Read timeout in seconds (default 15).  The session will automatically
        retry up to 2 times on transient 5xx / 429 responses.
    headers:
        Extra headers to merge with the default browser UA.
    **kwargs:
        Forwarded to ``session.get``.
    """
    session = get_session()
    merged_headers = {}
    if headers:
        merged_headers.update(headers)
    return session.get(url, timeout=timeout, headers=merged_headers or None, **kwargs)
