"""Gold price fetcher using akshare SGE AU9999 benchmark.

Code contract:
- asset_id 'ALTS_Paper_Gold' maps to raw_code 'Gold'
- asset_id 'GOLD_*' maps to raw_code 'Gold'
- market_daily stores code='Gold' (matches _update_from_dsa() regex extraction:
  REGEXP_EXTRACT('ALTS_Paper_Gold', '^[^_]+_[^_]+_(.+)$', 1) → 'Gold')
"""

from datetime import datetime, timedelta
import logging
from typing import Optional

try:
    import akshare
except ImportError as e:
    raise ImportError(
        "akshare is required for GoldPriceFetcher. "
        "Install it with: pip install akshare"
    ) from e

import pandas as pd

from src.market_data.fetchers.base import BaseFetcher, NoDataError, ProviderError, UnsupportedCodeError
from src.market_data.fetchers.types import OHLCVBar, RealtimeQuote

logger = logging.getLogger(__name__)

# Canonical raw code stored in market_daily — must match _update_from_dsa() regex extraction
GOLD_RAW_CODE = "Gold"

# All asset_ids that should route to this fetcher
GOLD_ASSET_PATTERNS = ("ALTS_Paper_Gold",)
GOLD_PREFIX = "GOLD_"


def _is_gold_code(code: str) -> bool:
    """Return True if code represents a gold asset."""
    return code in GOLD_ASSET_PATTERNS or code.startswith(GOLD_PREFIX) or code == GOLD_RAW_CODE


def _normalize_gold_code(code: str) -> str:
    """Normalize any gold-related asset ID to the canonical raw code 'Gold'.

    Examples:
        'ALTS_Paper_Gold' → 'Gold'
        'GOLD_PAPER_CMB'  → 'Gold'
        'Gold'            → 'Gold'

    Raises:
        UnsupportedCodeError: if code is not a recognized gold asset
    """
    if _is_gold_code(code):
        return GOLD_RAW_CODE
    raise UnsupportedCodeError(
        f"GoldPriceFetcher only handles gold asset codes (ALTS_Paper_Gold, GOLD_*), got: {code!r}"
    )


def _load_sge_history() -> pd.DataFrame:
    """Fetch AU9999 spot price history from SGE via akshare.

    Primary source: akshare.spot_hist_sge(symbol="Au99.99") — fresher, has same-day closes.
    Fallback source: akshare.spot_golden_benchmark_sge() — lags days, kept for resilience.

    Returns DataFrame with columns: date (date), close (float in CNY/gram).

    Raises:
        ProviderError: on network/API error from the fallback source
        NoDataError: if both sources fail or return empty data
    """
    # --- Primary source: spot_hist_sge ---
    # Verified live 2026-07-06: has same-day close 907.77.
    # Columns: date / open / close / low / high (already-parsed date column).
    _primary_error: Optional[str] = None
    try:
        primary_df = akshare.spot_hist_sge(symbol="Au99.99")
        if primary_df is not None and not primary_df.empty:
            primary_df = primary_df[["date", "close"]].copy()
            primary_df["date"] = pd.to_datetime(primary_df["date"], errors="coerce").dt.date
            primary_df["close"] = pd.to_numeric(primary_df["close"], errors="coerce")
            primary_df = primary_df.dropna(subset=["date", "close"])
            primary_df = primary_df.sort_values("date").reset_index(drop=True)
            primary_df = primary_df.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
            if not primary_df.empty:
                logger.info("GoldPriceFetcher: using primary source spot_hist_sge(Au99.99)")
                return primary_df
        _primary_error = "spot_hist_sge(Au99.99) returned empty data"
    except Exception as e:
        _primary_error = f"spot_hist_sge(Au99.99) failed: {e}"

    logger.info(f"GoldPriceFetcher: primary source unavailable ({_primary_error}), falling back to spot_golden_benchmark_sge")

    # --- Fallback source: spot_golden_benchmark_sge ---
    # Typical columns: 日期/交易时间 + 收盘/晚盘价. Normalize to date/close.
    try:
        df = akshare.spot_golden_benchmark_sge()
    except Exception as e:
        raise ProviderError(
            f"akshare.spot_golden_benchmark_sge() failed: {e}"
        ) from e

    if df is None or df.empty:
        raise NoDataError("akshare.spot_golden_benchmark_sge() returned no data")

    def _first_matching_column(matchers: tuple[str, ...]) -> Optional[object]:
        for matcher in matchers:
            for candidate in df.columns:
                candidate_str = str(candidate)
                if matcher in candidate_str or candidate_str.lower() == matcher.lower():
                    return candidate
        return None

    date_col = _first_matching_column(("交易时间", "交易日期", "日期", "date"))
    close_col = _first_matching_column(("收盘", "晚盘", "close", "price", "au9999", "价格"))

    if date_col is None or close_col is None:
        # Last-resort positional fallback
        logger.warning(
            f"GoldPriceFetcher: unexpected columns {list(df.columns)}, using positional fallback"
        )
        date_col = df.columns[0]
        close_col = df.columns[1]

    df = df[[date_col, close_col]].copy()
    df.columns = ["date", "close"]
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"])
    df = df.sort_values("date").reset_index(drop=True)
    df = df.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)

    if df.empty:
        raise NoDataError("akshare.spot_golden_benchmark_sge() returned no usable data after parsing")

    logger.info("GoldPriceFetcher: using fallback source spot_golden_benchmark_sge")
    return df


class GoldPriceFetcher(BaseFetcher):
    """Fetches paper gold (AU9999) CNY/gram prices from Shanghai Gold Exchange via akshare.

    Code contract: stores code='Gold' in market_daily so that _update_from_dsa()
    regex extraction of 'ALTS_Paper_Gold' (→ 'Gold') joins correctly.
    """

    name = "gold_sge"

    def fetch_ohlcv(self, code: str, days: int) -> list:
        """Fetch AU9999 daily price history.

        Returns:
            list[OHLCVBar] with close = CNY/gram price

        Raises:
            UnsupportedCodeError: code is not a gold asset
            NoDataError: no data returned
            ProviderError: network/API error
        """
        _normalize_gold_code(code)  # raises UnsupportedCodeError for non-gold

        df = _load_sge_history()

        # Filter to requested window
        today = datetime.now().date()
        start_date = today - timedelta(days=int(days * 1.5))
        df = df[df["date"] >= start_date].reset_index(drop=True)
        df = df.tail(days).reset_index(drop=True)

        if df.empty:
            raise NoDataError(
                f"No AU9999 data within the requested {days}-day window"
            )

        bars: list[OHLCVBar] = []
        for i, row in df.iterrows():
            prev_close = df.at[i - 1, "close"] if i > 0 else None
            pct_chg: Optional[float] = None
            if prev_close is not None and prev_close != 0:
                pct_chg = (row["close"] - prev_close) / prev_close * 100

            bars.append(
                OHLCVBar(
                    code=GOLD_RAW_CODE,
                    date=row["date"],
                    open=None,
                    high=None,
                    low=None,
                    close=float(row["close"]),
                    volume=None,
                    pct_chg=pct_chg,
                    source=self.name,
                )
            )

        return bars

    def fetch_realtime(self, code: str) -> RealtimeQuote:
        """Get the most recent AU9999 spot price.

        Returns:
            RealtimeQuote with price in CNY/gram

        Raises:
            UnsupportedCodeError: code is not a gold asset
            NoDataError: no data available
            ProviderError: network/API error
        """
        _normalize_gold_code(code)  # raises UnsupportedCodeError for non-gold

        df = _load_sge_history()
        latest = df.iloc[-1]
        prev_close = df.iloc[-2]["close"] if len(df) > 1 else None
        change_pct: Optional[float] = None
        if prev_close is not None and prev_close != 0:
            change_pct = (latest["close"] - prev_close) / prev_close * 100

        return RealtimeQuote(
            code=GOLD_RAW_CODE,
            price=float(latest["close"]),
            change_pct=change_pct,
            volume=None,
            timestamp=datetime.now(),
            source=self.name,
            as_of_date=latest["date"],
        )
