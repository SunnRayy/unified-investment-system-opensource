"""yfinance US index ETF PE fetcher (VOO, QQQ, DIA, IWM)."""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

_PE_BOUNDS = (3.0, 200.0)
_YIELD_BOUNDS = (0.0, 30.0)


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def _normalize_yield(raw) -> float | None:
    v = _safe_float(raw)
    if v is None:
        return None
    if 0 < v < 1:
        v = v * 100.0
    if not (_YIELD_BOUNDS[0] <= v <= _YIELD_BOUNDS[1]):
        return None
    return v


def fetch_us_index_snapshot(ticker: str) -> dict[str, Any]:
    """Fetch PE-TTM + dividend yield for a US index ETF (e.g. 'VOO').

    Returns {pe_ttm, dividend_yield, data_source} or {} when PE unavailable.
    History accumulates daily — no long-form history available via yfinance.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
    except Exception as exc:
        logger.warning("yfinance US index error for %s: %s", ticker, exc)
        return {}

    pe = _safe_float(info.get("trailingPE"))
    if pe is None or not (_PE_BOUNDS[0] <= pe <= _PE_BOUNDS[1]):
        return {}

    result: dict[str, Any] = {"pe_ttm": pe, "data_source": "yfinance_index_proxy"}

    dy = _normalize_yield(info.get("dividendYield"))
    if dy is not None:
        result["dividend_yield"] = dy

    return result
