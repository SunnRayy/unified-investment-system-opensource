"""Akshare market PE fetcher for 科创50 / 创业板 (stock_market_pe_lg + CSIndex history)."""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# 科创50 column; 创业板 uses a different average column
_PE_COLUMN_MAP: dict[str, str] = {
    "科创50": "市盈率",
    "创业板": "平均市盈率",
}
_DEFAULT_PE_COL = "市盈率"
_DATE_COL = "日期"
_PE_BOUNDS = (3.0, 500.0)

# CSIndex numeric codes for sub-indexes that have history via stock_zh_index_hist_csindex
# 创业板 (399006) is a SZSE index and is NOT supported by CSIndex — use stock_market_pe_lg only
_CSINDEX_CODE_MAP: dict[str, str] = {
    "科创50": "000688",
}


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def fetch_cn_market_snapshot(symbol: str) -> dict[str, Any]:
    """Fetch latest PE for a CN market segment (科创50 or 创业板).

    Returns {pe_ttm, data_source} or {} on failure.
    """
    try:
        import akshare as ak
        df = ak.stock_market_pe_lg(symbol=symbol)
        if df.empty:
            return {}

        col = _PE_COLUMN_MAP.get(symbol, _DEFAULT_PE_COL)
        pe = _safe_float(df[col].iloc[-1])
        if pe is None or pe <= 0 or not (_PE_BOUNDS[0] <= pe <= _PE_BOUNDS[1]):
            return {}

        return {"pe_ttm": pe, "data_source": "akshare_market_pe"}

    except Exception as exc:
        logger.warning("fetch_cn_market_snapshot failed for %s: %s", symbol, exc)
        return {}


def fetch_cn_market_history(symbol: str) -> list[dict[str, Any]]:
    """Fetch full PE history for a CN market-segment index (科创50 / 创业板).

    For 科创50: uses ak.stock_zh_index_hist_csindex (CSIndex source, has 滚动市盈率).
    For 创业板: not supported by CSIndex; falls back to stock_market_pe_lg full df.

    Returns list of {date: str, pe_ttm: float}.
    """
    # Try CSIndex history first (preferred — has rolling PE TTM per day)
    csindex_code = _CSINDEX_CODE_MAP.get(symbol)
    if csindex_code:
        try:
            import akshare as ak
            from datetime import date
            df = ak.stock_zh_index_hist_csindex(
                symbol=csindex_code,
                start_date="20100101",
                end_date=date.today().strftime("%Y%m%d"),
            )
            if df is not None and not df.empty and "滚动市盈率" in df.columns:
                result = []
                for _, row in df.iterrows():
                    pe = _safe_float(row.get("滚动市盈率"))
                    if pe is None or pe <= 0 or not (_PE_BOUNDS[0] <= pe <= _PE_BOUNDS[1]):
                        continue
                    result.append({"date": str(row["日期"])[:10], "pe_ttm": pe})
                if result:
                    logger.debug("CSIndex history for %s: %d rows", symbol, len(result))
                    return result
        except Exception as exc:
            logger.warning("CSIndex history failed for %s (%s): %s", symbol, csindex_code, exc)

    # Fallback: stock_market_pe_lg full dataframe
    try:
        import akshare as ak
        df = ak.stock_market_pe_lg(symbol=symbol)
        if df is None or df.empty:
            return []
        col = _PE_COLUMN_MAP.get(symbol, _DEFAULT_PE_COL)
        if col not in df.columns:
            logger.warning("PE column %r not found in stock_market_pe_lg for %s", col, symbol)
            return []
        result = []
        for _, row in df.iterrows():
            pe = _safe_float(row.get(col))
            if pe is None or pe <= 0 or not (_PE_BOUNDS[0] <= pe <= _PE_BOUNDS[1]):
                continue
            result.append({"date": str(row[_DATE_COL])[:10], "pe_ttm": pe})
        return result
    except Exception as exc:
        logger.warning("fetch_cn_market_history failed for %s: %s", symbol, exc)
        return []
