from datetime import date, datetime, timedelta, timezone
import logging
from typing import Optional

try:
    import yfinance
except ImportError as e:
    raise ImportError(
        "yfinance is required for YfinanceFetcher. "
        "Install it with: pip install yfinance"
    ) from e

import pandas as pd

from src.market_data.fetchers.base import BaseFetcher, NoDataError, ProviderError, UnsupportedCodeError
from src.market_data.fetchers.types import OHLCVBar, RealtimeQuote

logger = logging.getLogger(__name__)

# Strip these prefixes to get the raw ticker symbol
_PREFIX_MAP = {
    "US_STK_": "",
    "US_ETF_": "",
    "RSU_": "",
}


def fetch_fx_rates() -> dict:
    """Fetch live USD/CNY and HKD/CNY rates.

    Resolution chain (same chain used by CurrencyConverterService.get_latest_rate):
      1. yfinance fast_info (USDCNY=X, HKDCNY=X) — fast, no historical API call.
      2. Google Finance connector (via yfinance history) — for any currency yfinance
         did NOT return a valid (>0) price for.
      3. Config-driven / hard-coded defaults (USD via
         currency_converter.get_default_usd_cny_rate() — settings-driven,
         historical default 7.0; HKD=0.9 unchanged) — final safety net.

    Logs which source supplied each rate at INFO level.
    """
    # Lazy import: currency_converter.py lazily imports THIS module inside a
    # method for the same reason (avoids a module-load-time cycle) — see its
    # own "Lazy import avoids any potential circular-import risk" comment.
    from src.data_manager.currency_converter import get_default_usd_cny_rate
    defaults = {"USD": get_default_usd_cny_rate(), "HKD": 0.9}
    pairs = (("USDCNY=X", "USD"), ("HKDCNY=X", "HKD"))
    rates: dict = {}

    # Step 1: yfinance fast_info
    try:
        for pair, key in pairs:
            ticker = yfinance.Ticker(pair)
            fast_info = getattr(ticker, "fast_info", {}) or {}
            price = fast_info.get("lastPrice") or fast_info.get("last_price")
            if price is not None and float(price) > 0:
                rates[key] = round(float(price), 4)
                logger.info("FX %s/CNY = %.4f (source: yfinance fast_info)", key, rates[key])
    except Exception as e:
        logger.warning("yfinance fast_info FX fetch failed: %s", e)

    # Step 2: Google Finance fallback for any currency missing from yfinance
    missing = [key for (_, key) in pairs if key not in rates]
    if missing:
        try:
            from src.data_manager.connectors.google_finance_connector import (
                get_google_finance_connector,
            )
            connector = get_google_finance_connector()
            for key in missing:
                gf_rate = connector.get_exchange_rate(key, "CNY")
                if gf_rate and float(gf_rate) > 0:
                    rates[key] = round(float(gf_rate), 4)
                    logger.info(
                        "FX %s/CNY = %.4f (source: Google Finance fallback)", key, rates[key]
                    )
        except Exception as e:
            logger.warning("Google Finance FX fallback failed: %s", e)

    # Step 3: hard-coded defaults for any still-missing currency
    result = {**defaults, **rates}
    for key in defaults:
        if key not in rates:
            logger.info(
                "FX %s/CNY = %.4f (source: hard-coded default)", key, defaults[key]
            )

    return result


def _normalize_code(code: str) -> str:
    """Strip known Huinsight prefixes to obtain a Yahoo Finance ticker.

    Raises:
        UnsupportedCodeError: if the code cannot be mapped to a ticker
    """
    for prefix in _PREFIX_MAP:
        if code.startswith(prefix):
            return code[len(prefix):]

    # Bare uppercase alpha ticker (e.g., "AMZN", "NVDA") — use as-is
    if code.isalpha() and code.isupper() and len(code) <= 5:
        return code

    raise UnsupportedCodeError(
        f"YfinanceFetcher cannot map code {code!r} to a ticker symbol"
    )


def _coerce_market_date(raw_value) -> Optional[date]:
    """Convert provider metadata timestamps to a trading date.

    Returns None for weekend dates (post/pre-market UTC rollover from Friday evening)
    so the caller falls back to the last OHLCV bar date instead.
    """
    if raw_value in (None, ""):
        return None
    try:
        if isinstance(raw_value, str):
            raw_value = float(raw_value)
        if isinstance(raw_value, (int, float)):
            result = datetime.fromtimestamp(float(raw_value), tz=timezone.utc).date()
            # Reject weekend dates — post/pre-market UTC can roll Friday 8pm ET into Saturday
            if result.weekday() >= 5:
                return None
            return result
    except Exception:
        return None
    return None


class YfinanceFetcher(BaseFetcher):
    """Fetches US equity / ETF / RSU data via the yfinance library."""

    name = "yfinance"

    def fetch_ohlcv(self, code: str, days: int) -> list:
        """Fetch historical OHLCV bars from Yahoo Finance.

        Returns:
            list[OHLCVBar]

        Raises:
            UnsupportedCodeError: code cannot be mapped to a ticker
            NoDataError: ticker exists but returned no data
            ProviderError: network / API error
        """
        ticker = _normalize_code(code)  # may raise UnsupportedCodeError

        today = datetime.now().date()
        start_date = today - timedelta(days=int(days * 1.5))

        try:
            df = yfinance.download(
                ticker,
                start=start_date,
                end=today,
                auto_adjust=True,
                progress=False,
            )
        except Exception as e:
            raise ProviderError(
                f"yfinance.download failed for {ticker}: {e}"
            ) from e

        if df is None or df.empty:
            raise NoDataError(f"yfinance returned no data for ticker {ticker!r}")

        # Flatten MultiIndex columns produced by yfinance >= 0.2 when downloading a single ticker
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Normalise column names
        col_map = {
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
        df = df.rename(columns=col_map)

        # Ensure we have a 'close' column
        if "close" not in df.columns:
            raise NoDataError(
                f"yfinance result for {ticker!r} is missing a 'Close' column"
            )

        # Convert timezone-aware index to plain date objects
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        df.index = pd.to_datetime(df.index)
        df["_date"] = df.index.map(lambda ts: ts.date())

        # Keep only the most recent `days` bars
        df = df.tail(days).reset_index(drop=True)

        bars: list[OHLCVBar] = []
        for i, row in df.iterrows():
            prev_close = df.at[i - 1, "close"] if i > 0 else None
            pct_chg: Optional[float] = None
            if prev_close is not None and prev_close != 0:
                pct_chg = (row["close"] - prev_close) / prev_close * 100

            bars.append(
                OHLCVBar(
                    code=code,
                    date=row["_date"],
                    open=float(row["open"]) if "open" in df.columns and pd.notna(row.get("open")) else None,
                    high=float(row["high"]) if "high" in df.columns and pd.notna(row.get("high")) else None,
                    low=float(row["low"]) if "low" in df.columns and pd.notna(row.get("low")) else None,
                    close=float(row["close"]),
                    volume=float(row["volume"]) if "volume" in df.columns and pd.notna(row.get("volume")) else None,
                    pct_chg=pct_chg,
                    source="yfinance",
                )
            )

        return bars

    def fetch_realtime(self, code: str) -> RealtimeQuote:
        """Fetch realtime quote via yfinance fast_info.

        Returns:
            RealtimeQuote

        Raises:
            UnsupportedCodeError: code cannot be mapped
            NoDataError: price unavailable
            ProviderError: network / API error
        """
        ticker = _normalize_code(code)  # may raise UnsupportedCodeError

        try:
            ticker_obj = yfinance.Ticker(ticker)
            fast_info = ticker_obj.fast_info
            price = fast_info.get("lastPrice") or fast_info.get("last_price")
        except Exception as e:
            raise ProviderError(
                f"yfinance.Ticker({ticker!r}).fast_info failed: {e}"
            ) from e

        if price is None or (isinstance(price, float) and price != price):  # NaN check
            raise NoDataError(f"yfinance has no realtime price for {ticker!r}")

        # Attempt to get change_pct and volume — these may not always be present
        try:
            change_pct = fast_info.get("regularMarketChangePercent") or fast_info.get("regular_market_change_percent")
            volume = fast_info.get("regularMarketVolume") or fast_info.get("regular_market_volume")
        except Exception:
            change_pct = None
            volume = None

        try:
            info = ticker_obj.info or {}
        except Exception:
            info = {}

        as_of_date = None
        for metadata in (fast_info, info if isinstance(info, dict) else None):
            if metadata is None:
                continue
            for key in ("regularMarketTime", "postMarketTime", "preMarketTime"):
                as_of_date = _coerce_market_date(metadata.get(key))
                if as_of_date is not None:
                    break
            if as_of_date is not None:
                break
        if as_of_date is None:
            # Metadata missing — avoid a second network call just to stamp the quote.
            logger.debug(f"No market date in metadata for {ticker!r}; falling back to local date")
            as_of_date = datetime.now().date()

        return RealtimeQuote(
            code=code,
            price=float(price),
            change_pct=float(change_pct) if change_pct is not None else None,
            volume=float(volume) if volume is not None else None,
            timestamp=datetime.now(),
            source="yfinance",
            as_of_date=as_of_date,
        )
