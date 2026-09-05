"""yfinance HK index/ETF PE fetcher (proxy via 3033.HK for HSTECH)."""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

_PE_BOUNDS = (3.0, 500.0)


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def fetch_hk_index_snapshot(ticker: str) -> dict[str, Any]:
    """Fetch PE for a HK-listed ETF (e.g. '3033.HK' for HSTECH).

    Returns {pe_ttm, data_source} or {} when PE is unavailable.
    Note: ^HSTECH via yfinance returns 404 — use ETF proxy instead.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
    except Exception as exc:
        logger.warning("yfinance HK error for %s: %s", ticker, exc)
        return {}

    pe = _safe_float(info.get("trailingPE"))
    if pe is None or not (_PE_BOUNDS[0] <= pe <= _PE_BOUNDS[1]):
        return {}

    return {"pe_ttm": pe, "data_source": "yfinance_hk_proxy"}
