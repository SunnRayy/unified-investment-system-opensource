import re
from datetime import datetime, timedelta
import logging
from typing import Optional

try:
    import akshare
except ImportError as e:
    raise ImportError(
        "akshare is required for AkshareFundFetcher. "
        "Install it with: pip install akshare"
    ) from e

import pandas as pd

from src.market_data.fetchers.base import BaseFetcher, NoDataError, ProviderError, UnsupportedCodeError
from src.market_data.fetchers.types import OHLCVBar, RealtimeQuote

logger = logging.getLogger(__name__)

_CN_FUND_PREFIX = "CN_FUND_"
_FUND_CODE_PATTERN = re.compile(r"^\d{6}$")


def _extract_fund_code(code: str) -> str:
    """Strip CN_FUND_ prefix and validate the result is exactly 6 digits.

    Raises:
        UnsupportedCodeError: if code doesn't start with CN_FUND_ or result is not 6 digits
    """
    if not code.startswith(_CN_FUND_PREFIX):
        raise UnsupportedCodeError(
            f"AkshareFundFetcher only handles CN_FUND_* codes, got: {code!r}"
        )
    fund_code = code[len(_CN_FUND_PREFIX):]
    if not _FUND_CODE_PATTERN.match(fund_code):
        raise UnsupportedCodeError(
            f"CN fund code must be exactly 6 digits, got: {fund_code!r} (from {code!r})"
        )
    return fund_code


_MONEY_FUND_JS_ERRORS = ("Data_netWorthTrend", "Data_ACWorthTrend")


def _load_money_fund_nav(fund_code: str) -> pd.DataFrame:
    """Fetch money market fund data and normalize to unit-NAV format (price = 1.0).

    Money market funds have a stable NAV of 1.0 CNY per share; returns are
    distributed as daily yield, not as price appreciation.
    """
    try:
        df = akshare.fund_money_fund_info_em(symbol=fund_code)
    except Exception as e:
        raise ProviderError(
            f"akshare.fund_money_fund_info_em failed for {fund_code!r}: {e}"
        ) from e

    date_col = "净值日期"
    if df is None or df.empty or date_col not in df.columns:
        raise NoDataError(f"akshare returned no money fund data for {fund_code!r}")

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.date
    df = df.dropna(subset=[date_col])
    df = df.sort_values(date_col).reset_index(drop=True)
    df = df.drop_duplicates(subset=[date_col], keep="last").reset_index(drop=True)

    if df.empty:
        raise NoDataError(f"akshare returned no usable money fund data for {fund_code!r}")

    df["单位净值"] = 1.0  # stable NAV for money market funds
    logger.debug(f"Money fund {fund_code!r}: using stable NAV 1.0, latest={df.iloc[-1][date_col]}")
    return df[[date_col, "单位净值"]]


def _load_nav_history(fund_code: str) -> pd.DataFrame:
    try:
        df = akshare.fund_open_fund_info_em(
            symbol=fund_code, indicator="单位净值走势"
        )
    except Exception as e:
        err_str = str(e)
        if any(marker in err_str for marker in _MONEY_FUND_JS_ERRORS):
            # Money market fund: no unit NAV curve — fall back to money fund endpoint
            logger.debug(f"Fund {fund_code!r} is a money market fund; using fund_money_fund_info_em")
            return _load_money_fund_nav(fund_code)
        raise ProviderError(
            f"akshare.fund_open_fund_info_em failed for {fund_code!r}: {e}"
        ) from e

    if df is None or df.empty:
        raise NoDataError(f"akshare returned no data for fund {fund_code!r}")

    date_col = "净值日期"
    nav_col = "单位净值"
    if date_col not in df.columns or nav_col not in df.columns:
        raise NoDataError(
            f"akshare response for {fund_code!r} missing expected columns "
            f"({date_col!r}, {nav_col!r}). Got: {list(df.columns)}"
        )

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.date
    df[nav_col] = pd.to_numeric(df[nav_col], errors="coerce")
    df = df.dropna(subset=[date_col, nav_col])
    df = df.sort_values(date_col).reset_index(drop=True)
    df = df.drop_duplicates(subset=[date_col], keep="last").reset_index(drop=True)

    if df.empty:
        raise NoDataError(f"akshare returned no usable NAV data for fund {fund_code!r}")

    return df


class AkshareFundFetcher(BaseFetcher):
    """Fetches Chinese open-end fund NAV history via akshare."""

    name = "akshare_fund"

    def fetch_ohlcv(self, code: str, days: int) -> list:
        """Fetch fund NAV history from akshare.

        Returns:
            list[OHLCVBar]

        Raises:
            UnsupportedCodeError: code is not a valid CN_FUND_ code
            NoDataError: fund returned no data
            ProviderError: network / API error
        """
        fund_code = _extract_fund_code(code)  # may raise UnsupportedCodeError

        today = datetime.now().date()
        start_date = today - timedelta(days=int(days * 1.5))

        date_col = "净值日期"
        nav_col = "单位净值"
        df = _load_nav_history(fund_code)

        # Filter to start_date onwards
        df = df[df[date_col] >= start_date].reset_index(drop=True)

        # Keep only the most recent `days` bars
        df = df.tail(days).reset_index(drop=True)

        if df.empty:
            raise NoDataError(
                f"akshare returned no data within the requested window for fund {fund_code!r}"
            )

        bars: list[OHLCVBar] = []
        for i, row in df.iterrows():
            prev_nav = df.at[i - 1, nav_col] if i > 0 else None
            pct_chg: Optional[float] = None
            if prev_nav is not None and prev_nav != 0:
                pct_chg = (row[nav_col] - prev_nav) / prev_nav * 100

            bars.append(
                OHLCVBar(
                    code=code,
                    date=row[date_col],
                    open=None,
                    high=None,
                    low=None,
                    close=float(row[nav_col]),
                    volume=None,
                    pct_chg=pct_chg,
                    source="akshare_fund",
                )
            )

        return bars

    def fetch_realtime(self, code: str) -> RealtimeQuote:
        """Get the most recent NAV as a realtime quote.

        Returns:
            RealtimeQuote

        Raises:
            UnsupportedCodeError: invalid code
            NoDataError: no data available
            ProviderError: network / API error
        """
        try:
            fund_code = _extract_fund_code(code)
            df = _load_nav_history(fund_code)
            latest = df.iloc[-1]
            prev_nav = df.iloc[-2]["单位净值"] if len(df) > 1 else None
            change_pct: Optional[float] = None
            if prev_nav is not None and prev_nav != 0:
                change_pct = (latest["单位净值"] - prev_nav) / prev_nav * 100
            as_of_date = latest["净值日期"]
            price = float(latest["单位净值"])
        except (UnsupportedCodeError, NoDataError, ProviderError):
            raise
        except Exception:
            bars = self.fetch_ohlcv(code, days=2)
            if not bars:
                raise NoDataError(f"No recent NAV data available for {code!r}")
            latest_bar = bars[-1]
            price = latest_bar.close
            change_pct = latest_bar.pct_chg
            as_of_date = latest_bar.date

        return RealtimeQuote(
            code=code,
            price=price,
            change_pct=change_pct,
            volume=None,
            timestamp=datetime.now(),
            source="akshare_fund",
            as_of_date=as_of_date,
        )
