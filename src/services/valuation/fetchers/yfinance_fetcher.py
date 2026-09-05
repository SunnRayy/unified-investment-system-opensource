"""yfinance wrapper for US stock and ETF valuation metrics."""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

SANITY_BOUNDS: dict[str, tuple[float, float]] = {
    "pe_forward": (5.0, 200.0),
    "pe_ttm": (3.0, 500.0),
    "pb_ratio": (0.1, 50.0),
    "peg_ratio": (0.0, 20.0),
    "fcf_yield": (-50.0, 30.0),
    "sec_yield": (0.0, 30.0),
    "dividend_yield": (0.0, 30.0),
}


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def _check_bounds(key: str, value: float | None) -> float | None:
    if value is None:
        return None
    bounds = SANITY_BOUNDS.get(key)
    if bounds and not (bounds[0] <= value <= bounds[1]):
        logger.warning("Sanity check failed: %s=%s out of bounds %s", key, value, bounds)
        return None
    return value


def _normalize_yield(raw) -> float | None:
    """Normalize yield from decimal (0.007) or percent (0.7 or 4.5) to percent points."""
    v = _safe_float(raw)
    if v is None:
        return None
    if 0 < v < 1:  # Likely decimal form (e.g. 0.045 = 4.5%)
        v = v * 100.0
    return _check_bounds("sec_yield", v)


def fetch_yfinance_us_stock(ticker: str) -> dict[str, Any]:
    """Fetch US stock valuation from yfinance. Returns {} on failure."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
    except Exception as exc:
        logger.warning("yfinance error for %s: %s", ticker, exc)
        return {}

    result: dict[str, Any] = {"data_source": "yfinance"}
    result["pe_forward"] = _check_bounds("pe_forward", _safe_float(info.get("forwardPE")))
    result["pe_ttm"] = _check_bounds("pe_ttm", _safe_float(info.get("trailingPE")))
    result["pb_ratio"] = _check_bounds("pb_ratio", _safe_float(info.get("priceToBook")))
    result["peg_ratio"] = _check_bounds("peg_ratio", _safe_float(info.get("pegRatio")))
    # FCF yield: freeCashflow / marketCap
    fcf = _safe_float(info.get("freeCashflow"))
    cap = _safe_float(info.get("marketCap"))
    if fcf is not None and cap and cap > 0:
        result["fcf_yield"] = _check_bounds("fcf_yield", fcf / cap * 100.0)
    result["ev_ebitda"] = _check_bounds("pe_forward", _safe_float(info.get("enterpriseToEbitda")))
    # Dividend yield normalization
    raw_dy = info.get("dividendYield")
    if raw_dy is not None:
        dy = _safe_float(raw_dy)
        if dy is not None and 0 < dy < 1:
            dy = dy * 100.0
        result["dividend_yield"] = _check_bounds("dividend_yield", dy)
    return result


def fetch_yfinance_etf_yield(ticker: str) -> dict[str, Any]:
    """Fetch ETF yield (for bond/cash ETFs like SGOV, IEF). Returns {} on failure."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
    except Exception as exc:
        logger.warning("yfinance ETF error for %s: %s", ticker, exc)
        return {}

    # Key precedence: 'yield' -> 'dividendYield'
    raw = info.get("yield") or info.get("dividendYield")
    sec_yield = _normalize_yield(raw)
    if sec_yield is None:
        return {}
    return {"sec_yield": sec_yield, "data_source": "yfinance_yield"}
