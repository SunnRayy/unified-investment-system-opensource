"""Fetcher registry — maps reader keys to callable fetch functions.

A fetcher function has the signature:
    fetch_fn(data_dir: Path) -> Path

It reads its own credentials from environment variables (never from arguments)
and writes a new timestamped file to data_dir.  It returns the path to the
newly written file.

``can_fetch(reader)`` returns True iff the reader has a registered fetcher.
``fetch(reader, data_dir)`` invokes the fetcher (raises KeyError if not found).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Dict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# IBKR fetcher wrapper
# ---------------------------------------------------------------------------


def _ibkr_fetch(data_dir: Path) -> Path:
    """Fetch the latest IBKR Flex statement and write it to data_dir.

    Reads credentials from:
        IBKR_FLEX_TOKEN    — read-only Flex Web Service token
        IBKR_FLEX_QUERY_ID — numeric Query ID from Flex Query config page

    Returns the path to the newly written file.
    Raises:
        EnvironmentError  — if required env vars are missing
        FlexFetchError    — on any IBKR Flex API error (propagated unchanged)
    """
    from src.fetchers.ibkr_flex import fetch_and_save  # noqa: PLC0415

    token = os.environ.get("IBKR_FLEX_TOKEN")
    query_id = os.environ.get("IBKR_FLEX_QUERY_ID")

    missing = [
        name
        for name, val in [("IBKR_FLEX_TOKEN", token), ("IBKR_FLEX_QUERY_ID", query_id)]
        if not val
    ]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variable(s) for IBKR fetch: "
            f"{', '.join(missing)}. "
            "Set them in the Cloud Run service environment or local .env file."
        )

    # Token is never logged — only query_id
    logger.info("Starting IBKR Flex fetch: query_id=%s dest_dir=%s", query_id, data_dir)
    return fetch_and_save(data_dir, token, query_id)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Registry map: reader_key → fetch_fn
# ---------------------------------------------------------------------------

FETCHERS: Dict[str, Callable[[Path], Path]] = {
    "ibkr": _ibkr_fetch,
}


def can_fetch(reader: str) -> bool:
    """Return True iff reader has a registered fetcher."""
    return reader in FETCHERS


def fetch(reader: str, data_dir: Path) -> Path:
    """Invoke the registered fetcher for reader.

    Raises:
        KeyError   — reader not in FETCHERS (check can_fetch first)
        EnvironmentError — missing env credentials
        FlexFetchError   — upstream IBKR error
    """
    fn = FETCHERS[reader]
    return fn(data_dir)
