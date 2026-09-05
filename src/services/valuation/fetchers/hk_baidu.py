"""Baidu Finance fetcher for HK ETF historical PE and PB ratios.

Uses requests (with redirect following) rather than http.client, because the
Baidu endpoint redirects from gushitong.baidu.com → finance.baidu.com and
akshare's built-in http.client call silently reads the 301 body as empty JSON.

Used for one-time history seed; daily snapshot comes from yfinance.
Symbol format: 5-digit HK stock code (e.g. "06969" for CSOP HSTECH ETF).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime

logger = logging.getLogger(__name__)

_PE_BOUNDS = (1.0, 500.0)
_PB_BOUNDS = (0.1, 500.0)
_BAIDU_URL = "https://gushitong.baidu.com/opendata"
_HEADERS = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15"}


def _baidu_fetch(symbol: str, indicator: str, period: str = "全部") -> list[list]:
    from src.utils.http_client import http_get

    params = {
        "openapi": "1",
        "dspName": "iphone",
        "tn": "tangram",
        "client": "app",
        "query": indicator,
        "code": symbol,
        "word": "",
        "resource_id": "51171",
        "market": "hk",
        "tag": indicator,
        "chart_select": period,
        "industry_select": "",
        "skip_industry": "1",
        "finClientType": "pc",
    }
    # ^TNX-style redirects: allow_redirects forwarded via **kwargs
    resp = http_get(_BAIDU_URL, timeout=20, headers=_HEADERS, params=params, allow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    return data["Result"][0]["DisplayData"]["resultData"]["tplData"]["result"]["chartInfo"][0]["body"]


def _parse_as_pe_ttm(raw: list[list]) -> list[dict]:
    result: list[dict] = []
    for item in raw:
        if len(item) < 2:
            continue
        try:
            d = datetime.strptime(str(item[0])[:10], "%Y-%m-%d")
            v = float(item[1])
            if not math.isfinite(v) or not (_PE_BOUNDS[0] <= v <= _PE_BOUNDS[1]):
                continue
            result.append({"date": d.strftime("%Y-%m-%d"), "pe_ttm": v})
        except (ValueError, TypeError):
            continue
    result.sort(key=lambda x: x["date"])
    return result


def _parse_as_value(raw: list[list], bounds: tuple[float, float]) -> list[dict]:
    result: list[dict] = []
    for item in raw:
        if len(item) < 2:
            continue
        try:
            d = datetime.strptime(str(item[0])[:10], "%Y-%m-%d")
            v = float(item[1])
            if not math.isfinite(v) or not (bounds[0] <= v <= bounds[1]):
                continue
            result.append({"date": d.strftime("%Y-%m-%d"), "value": v})
        except (ValueError, TypeError):
            continue
    result.sort(key=lambda x: x["date"])
    return result


def fetch_hk_index_pe_history(symbol: str) -> list[dict]:
    """Fetch daily PE TTM history for a HK ETF from Baidu Finance.

    Returns [{date: YYYY-MM-DD, pe_ttm: float}] sorted oldest-first.
    Returns [] on any failure.
    """
    try:
        raw = _baidu_fetch(symbol, "市盈率(TTM)")
        result = _parse_as_pe_ttm(raw)
        logger.info("Baidu HK PE history (%s): %d rows", symbol, len(result))
        return result
    except Exception as exc:
        logger.warning("fetch_hk_index_pe_history(%s) failed: %s", symbol, exc)
        return []


def fetch_hk_index_pb_history(symbol: str) -> list[dict]:
    """Fetch daily PB ratio history for a HK ETF from Baidu Finance.

    Returns [{date: YYYY-MM-DD, value: float}] sorted oldest-first, compatible
    with _bulk_upsert_series.
    Returns [] on any failure.
    """
    try:
        raw = _baidu_fetch(symbol, "市净率")
        result = _parse_as_value(raw, _PB_BOUNDS)
        logger.info("Baidu HK PB history (%s): %d rows", symbol, len(result))
        return result
    except Exception as exc:
        logger.warning("fetch_hk_index_pb_history(%s) failed: %s", symbol, exc)
        return []
