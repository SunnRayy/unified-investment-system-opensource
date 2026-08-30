"""FMP (Financial Modeling Prep) API client for US stock valuation.

NOTE (2025-08-31+): FMP deprecated all v3 endpoints for new plan users.
The FMP_API_KEY is required but ALL endpoints now return 403 unless the
account has a paid subscription predating Aug 31 2025.  As of 2026-05,
the free/starter tier gives 403 on every call.  yfinance is used as the
primary spot-data fallback automatically (collector._collect_one).
This module is retained so the collector can still attempt FMP and fall
through gracefully.
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

from src.utils.http_client import http_get

logger = logging.getLogger(__name__)
FMP_BASE = "https://financialmodelingprep.com/api/v3"

# Tracks whether we've already warned about the 403/plan issue in this process.
_fmp_403_warned: bool = False

# Per-day in-process call counter. Resets on new date key.
_FMP_QUOTA: dict[str, int] = {}
FMP_DAILY_LIMIT = 250
FMP_WARN_AT = 200


def _check_fmp_quota() -> bool:
    """Decrement daily quota; return False (and log) when limit reached."""
    today = date.today().isoformat()
    count = _FMP_QUOTA.get(today, 0)
    if count >= FMP_DAILY_LIMIT:
        logger.warning("fmp_quota: daily limit reached (%d/%d)", count, FMP_DAILY_LIMIT)
        return False
    _FMP_QUOTA[today] = count + 1
    used = _FMP_QUOTA[today]
    if used >= FMP_WARN_AT:
        logger.warning("fmp_quota: used=%d/%d — approaching daily limit", used, FMP_DAILY_LIMIT)
    else:
        logger.debug("fmp_quota: used=%d/%d", used, FMP_DAILY_LIMIT)
    return True


def _safe_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def _log_fmp_403(ticker: str, endpoint: str) -> None:
    """Log FMP plan-restriction warning once per process to avoid log spam."""
    global _fmp_403_warned
    if not _fmp_403_warned:
        logger.warning(
            "FMP v3 API returned 403 for %s on %s. "
            "FMP deprecated free v3 endpoints (Aug 31 2025). "
            "yfinance will be used as fallback. "
            "To enable FMP: upgrade to a paid FMP plan.",
            ticker, endpoint,
        )
        _fmp_403_warned = True


def fetch_fmp_us_stock(ticker: str, api_key: str | None = None, timeout: int = 10) -> dict[str, Any]:
    """
    Fetch US stock valuation metrics from FMP.
    Returns dict with keys: pe_ttm, pe_forward, pb_ratio, peg_ratio, fcf_yield,
    ev_ebitda, dividend_yield, data_source.
    Returns {} on any failure (including 403 plan restriction) so caller can
    fall back to yfinance.

    NOTE: FMP v3 deprecated free access Aug 31 2025.  As of 2026-05 all
    calls return 403 on starter plans.  {} is returned transparently so
    collector._collect_one falls through to yfinance automatically.
    """
    key = api_key or os.environ.get("FMP_API_KEY", "")
    if not key:
        logger.debug("FMP_API_KEY not set, skipping FMP for %s", ticker)
        return {}

    result: dict[str, Any] = {"data_source": "fmp"}

    # Endpoint 1: key-metrics for forwardPE
    try:
        resp = http_get(f"{FMP_BASE}/key-metrics/{ticker}?limit=1&apikey={key}", timeout=timeout)
        if resp.status_code == 403:
            _log_fmp_403(ticker, "key-metrics")
            return {}
        if resp.status_code == 200:
            data = resp.json()
            record = data[0] if isinstance(data, list) and data else {}
            if record.get("forwardPE"):
                result["pe_forward"] = _safe_float(record["forwardPE"])
            result["pb_ratio"] = _safe_float(record.get("pbRatio"))
            result["peg_ratio"] = _safe_float(record.get("pegRatio"))
            result["ev_ebitda"] = _safe_float(record.get("enterpriseValueOverEBITDA"))
    except Exception as exc:
        logger.warning("FMP key-metrics error for %s: %s", ticker, exc)

    # Endpoint 2: key-metrics-ttm for TTM PE and FCF yield
    try:
        resp = http_get(f"{FMP_BASE}/key-metrics-ttm/{ticker}?apikey={key}", timeout=timeout)
        if resp.status_code == 403:
            _log_fmp_403(ticker, "key-metrics-ttm")
            return {}
        if resp.status_code == 200:
            data = resp.json()
            record = data[0] if isinstance(data, list) and data else {}
            result["pe_ttm"] = _safe_float(record.get("peRatioTTM"))
            result["fcf_yield"] = _safe_float(record.get("freeCashFlowYieldTTM"))
            result["dividend_yield"] = _safe_float(record.get("dividendYieldTTM"))
            if not result.get("pe_forward"):
                result["pe_forward"] = result.get("pe_ttm")
    except Exception as exc:
        logger.warning("FMP key-metrics-ttm error for %s: %s", ticker, exc)

    return result


def fetch_fmp_us_history(
    ticker: str, api_key: str | None = None, timeout: int = 15
) -> dict[str, list[dict]]:
    """Fetch quarterly historical key metrics for a US stock/ETF from FMP.

    Calls /v3/historical-key-metrics/{ticker}?period=quarter&limit=80 (~20y quarterly).
    Returns {metric: [{date: str, value: float}, ...]} for non-zero values.
    Keys: pe_ttm, pb_ratio, ps_ratio, ev_ebitda, dividend_yield.
    Returns {} on failure, 403 (plan tier), or quota exceeded.
    """
    key = api_key or os.environ.get("FMP_API_KEY", "")
    if not key:
        return {}
    if not _check_fmp_quota():
        return {}

    try:
        resp = http_get(
            f"{FMP_BASE}/historical-key-metrics/{ticker}?period=quarter&limit=80&apikey={key}",
            timeout=timeout,
        )
        if resp.status_code == 403:
            _log_fmp_403(ticker, "historical-key-metrics")
            return {}
        if resp.status_code != 200:
            logger.warning("FMP /historical-key-metrics HTTP %d for %s", resp.status_code, ticker)
            return {}
        data = resp.json()
        if not isinstance(data, list) or not data:
            return {}
    except Exception as exc:
        logger.warning("FMP historical-key-metrics error for %s: %s", ticker, exc)
        return {}

    _FIELD_MAP = [
        ("pe_ttm", "peRatio"),
        ("pb_ratio", "pbRatio"),
        ("ps_ratio", "priceToSalesRatio"),
        ("ev_ebitda", "enterpriseValueOverEBITDA"),
        ("dividend_yield", "dividendYield"),
    ]
    out: dict[str, list[dict]] = {m: [] for m, _ in _FIELD_MAP}
    for record in data:
        d = str(record.get("date", ""))[:10]
        if len(d) < 10:
            continue
        for metric, field in _FIELD_MAP:
            v = _safe_float(record.get(field))
            if v is not None and v > 0:
                out[metric].append({"date": d, "value": v})

    return {k: v for k, v in out.items() if v}
