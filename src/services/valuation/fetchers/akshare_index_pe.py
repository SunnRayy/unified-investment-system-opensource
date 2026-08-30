"""Akshare CN broad index PE/PB fetcher (stock_index_pe_lg + CSIndex secondary)."""
from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# PE column name returned by stock_index_pe_lg
_PE_COL = "滚动市盈率"
# PB column name returned by stock_index_pb_lg
_PB_COL = "市净率"
_DATE_COL = "日期"

_PE_BOUNDS = (3.0, 500.0)
_PB_BOUNDS = (0.1, 50.0)
_YIELD_BOUNDS = (0.0, 30.0)  # dividend yield in %

# funddb indicator → expected DataFrame value column (primary + alias)
_FUNDDB_VALUE_COLS: dict[str, list[str]] = {
    "市盈率": ["市盈率", "pe"],
    "市净率": ["市净率", "pb"],
    "股息率": ["股息率", "dividend_yield"],
}
_FUNDDB_BOUNDS: dict[str, tuple[float, float]] = {
    "市盈率": _PE_BOUNDS,
    "市净率": _PB_BOUNDS,
    "股息率": _YIELD_BOUNDS,
}

# CSIndex numeric codes for broad indexes (secondary/fallback for history)
_CSINDEX_CODE_MAP: dict[str, str] = {
    "沪深300": "000300",
    "中证500": "000905",
    "上证50":  "000016",
    "中证800": "000906",
}


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def _in_bounds(value: float, bounds: tuple[float, float]) -> bool:
    return bounds[0] <= value <= bounds[1]


def fetch_cn_index_funddb(symbol: str, indicator: str) -> list[dict]:
    """Fetch historical PB series for a CN broad index via stock_index_pb_lg.

    symbol: Chinese index name e.g. '沪深300', '中证500', '上证50'
    indicator: only '市净率' is supported; other indicators return []
    Returns [{date: str, value: float}, ...] filtered to valid range.
    Returns [] on failure or unsupported indicator.
    """
    if indicator != "市净率":
        logger.debug("fetch_cn_index_funddb: indicator '%s' not implemented, only '市净率' supported", indicator)
        return []
    try:
        import akshare as ak
        df = ak.stock_index_pb_lg(symbol=symbol)
        if df is None or df.empty:
            return []
        bounds = _PB_BOUNDS
        result = []
        for _, row in df.iterrows():
            v = _safe_float(row.get(_PB_COL))
            if v is None or v <= 0 or not _in_bounds(v, bounds):
                continue
            d = str(row[_DATE_COL])[:10]
            if len(d) < 10:
                continue
            result.append({"date": d, "value": v})
        return result
    except Exception as exc:
        logger.warning("fetch_cn_index_funddb failed for %s/%s: %s", symbol, indicator, exc)
        return []


def fetch_cn_index_snapshot(symbol: str) -> dict[str, Any]:
    """Fetch latest PE-TTM + PB for a CN broad index (e.g. '沪深300').

    Returns {pe_ttm, pb_ratio, data_source} or {} on failure.
    """
    try:
        import akshare as ak
        df_pe = ak.stock_index_pe_lg(symbol=symbol)
        if df_pe.empty:
            return {}
        pe_raw = _safe_float(df_pe[_PE_COL].iloc[-1])
        if pe_raw is None or not _in_bounds(pe_raw, _PE_BOUNDS):
            return {}

        result: dict[str, Any] = {"pe_ttm": pe_raw, "data_source": "akshare_index_pe"}

        try:
            df_pb = ak.stock_index_pb_lg(symbol=symbol)
            if not df_pb.empty:
                pb_raw = _safe_float(df_pb[_PB_COL].iloc[-1])
                if pb_raw is not None and _in_bounds(pb_raw, _PB_BOUNDS):
                    result["pb_ratio"] = pb_raw
        except Exception as exc:
            logger.warning("stock_index_pb_lg failed for %s: %s", symbol, exc)

        return result

    except Exception as exc:
        logger.warning("fetch_cn_index_snapshot failed for %s: %s", symbol, exc)
        return {}


def fetch_cn_index_history(symbol: str) -> list[dict[str, Any]]:
    """Fetch full PE history for a CN broad index.

    Primary: ak.stock_index_pe_lg (Chinese name).
    Secondary: ak.stock_zh_index_hist_csindex (numeric code, CSIndex source).

    Returns list of {date: str, pe_ttm: float}, filtered to valid positive PE values.
    """
    # Primary: stock_index_pe_lg (accepts Chinese names like '沪深300')
    try:
        import akshare as ak
        df = ak.stock_index_pe_lg(symbol=symbol)
        result = []
        for _, row in df.iterrows():
            pe = _safe_float(row.get(_PE_COL))
            if pe is None or pe <= 0 or not _in_bounds(pe, _PE_BOUNDS):
                continue
            result.append({"date": str(row[_DATE_COL])[:10], "pe_ttm": pe})
        if result:
            return result
        logger.warning("stock_index_pe_lg returned no valid PE rows for %s", symbol)
    except Exception as exc:
        logger.warning("stock_index_pe_lg failed for %s: %s; trying CSIndex fallback", symbol, exc)

    # Secondary: stock_zh_index_hist_csindex (accepts numeric codes)
    csindex_code = _CSINDEX_CODE_MAP.get(symbol)
    if not csindex_code:
        return []
    try:
        import akshare as ak
        df = ak.stock_zh_index_hist_csindex(
            symbol=csindex_code,
            start_date="20050101",
            end_date=date.today().strftime("%Y%m%d"),
        )
        if df is None or df.empty or "滚动市盈率" not in df.columns:
            return []
        result = []
        for _, row in df.iterrows():
            pe = _safe_float(row.get("滚动市盈率"))
            if pe is None or pe <= 0 or not _in_bounds(pe, _PE_BOUNDS):
                continue
            result.append({"date": str(row[_DATE_COL])[:10], "pe_ttm": pe})
        if result:
            logger.info("CSIndex fallback history for %s: %d rows", symbol, len(result))
        return result
    except Exception as exc:
        logger.warning("CSIndex fallback failed for %s (%s): %s", symbol, csindex_code, exc)
        return []
