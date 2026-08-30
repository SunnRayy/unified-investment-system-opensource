"""
Tests for the market data fetcher layer (Phase 1 DSA analysis migration).
All external API calls (yfinance, akshare) are mocked — no real network access.
"""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.market_data.fetchers.types import OHLCVBar, RealtimeQuote
from src.market_data.fetchers.base import (
    BaseFetcher,
    DataFetchError,
    FetcherManager,
    NoDataError,
    ProviderError,
    UnsupportedCodeError,
)
from src.market_data.fetchers.yfinance_fetcher import YfinanceFetcher, _normalize_code
from src.market_data.fetchers.akshare_fetcher import AkshareFundFetcher, _extract_fund_code
from src.market_data.service import MarketDataService


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_bars(code: str, n: int = 5, source: str = "test") -> list:
    """Create n dummy OHLCVBar objects."""
    today = date.today()
    return [
        OHLCVBar(
            code=code,
            date=today - timedelta(days=n - i - 1),
            open=100.0 + i,
            high=105.0 + i,
            low=95.0 + i,
            close=100.0 + i,
            volume=1_000_000.0,
            pct_chg=0.5 if i > 0 else None,
            source=source,
        )
        for i in range(n)
    ]


def _make_realtime(code: str, source: str = "test") -> RealtimeQuote:
    return RealtimeQuote(
        code=code,
        price=123.45,
        change_pct=0.5,
        volume=1_000_000.0,
        timestamp=datetime.now(),
        source=source,
    )


class _GoodFetcher(BaseFetcher):
    """Always returns valid data."""
    name = "good"

    def fetch_ohlcv(self, code: str, days: int) -> list:
        return _make_bars(code, n=days, source="good")

    def fetch_realtime(self, code: str) -> RealtimeQuote:
        return _make_realtime(code, source="good")


class _ProviderErrorFetcher(BaseFetcher):
    """Always raises ProviderError."""
    name = "bad_provider"

    def fetch_ohlcv(self, code: str, days: int) -> list:
        raise ProviderError("simulated network error")

    def fetch_realtime(self, code: str) -> RealtimeQuote:
        raise ProviderError("simulated network error")


class _NoDataFetcher(BaseFetcher):
    """Always raises NoDataError."""
    name = "no_data"

    def fetch_ohlcv(self, code: str, days: int) -> list:
        raise NoDataError("symbol not found")

    def fetch_realtime(self, code: str) -> RealtimeQuote:
        raise NoDataError("symbol not found")


# ---------------------------------------------------------------------------
# 1. Dataclass creation
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_ohlcv_bar_creation(self):
        bar = OHLCVBar(
            code="AMZN",
            date=date(2025, 1, 15),
            open=185.0,
            high=190.0,
            low=183.0,
            close=188.5,
            volume=10_000_000.0,
            pct_chg=0.8,
            source="yfinance",
        )
        assert bar.code == "AMZN"
        assert bar.close == 188.5
        assert bar.source == "yfinance"

    def test_ohlcv_bar_optional_fields_none(self):
        bar = OHLCVBar(
            code="CN_FUND_900008",
            date=date(2025, 1, 15),
            open=None,
            high=None,
            low=None,
            close=1.2345,
            volume=None,
            pct_chg=None,
            source="akshare_fund",
        )
        assert bar.open is None
        assert bar.volume is None

    def test_realtime_quote_creation(self):
        ts = datetime(2025, 1, 15, 9, 30, 0)
        quote = RealtimeQuote(
            code="NVDA",
            price=850.0,
            change_pct=-1.2,
            volume=50_000_000.0,
            timestamp=ts,
            source="yfinance",
        )
        assert quote.price == 850.0
        assert quote.timestamp == ts


# ---------------------------------------------------------------------------
# 2. FetcherManager
# ---------------------------------------------------------------------------

class TestFetcherManager:
    def test_successful_fetch_returns_data(self):
        mgr = FetcherManager([_GoodFetcher()], min_bars=5)
        bars = mgr.get_ohlcv("AMZN", days=5)
        assert len(bars) == 5
        assert all(isinstance(b, OHLCVBar) for b in bars)

    def test_successful_realtime(self):
        mgr = FetcherManager([_GoodFetcher()])
        quote = mgr.get_realtime_quote("AMZN")
        assert isinstance(quote, RealtimeQuote)
        assert quote.price == 123.45

    def test_failover_to_second_fetcher(self):
        """First fetcher raises ProviderError; second should succeed."""
        mgr = FetcherManager([_ProviderErrorFetcher(), _GoodFetcher()], min_bars=5)
        bars = mgr.get_ohlcv("AMZN", days=5)
        assert len(bars) == 5
        assert bars[0].source == "good"

    def test_failover_realtime(self):
        mgr = FetcherManager([_ProviderErrorFetcher(), _GoodFetcher()])
        quote = mgr.get_realtime_quote("AMZN")
        assert quote.source == "good"

    def test_all_fail_raises_data_fetch_error(self):
        mgr = FetcherManager([_ProviderErrorFetcher(), _NoDataFetcher()])
        with pytest.raises(DataFetchError):
            mgr.get_ohlcv("AMZN", days=5)

    def test_all_fail_realtime_raises_data_fetch_error(self):
        mgr = FetcherManager([_ProviderErrorFetcher(), _NoDataFetcher()])
        with pytest.raises(DataFetchError):
            mgr.get_realtime_quote("AMZN")

    def test_circuit_breaker_opens_after_threshold(self):
        """After 3 consecutive ProviderErrors the circuit should open."""
        mgr = FetcherManager(
            [_ProviderErrorFetcher()],
            circuit_open_threshold=3,
            circuit_reset_seconds=300,
        )
        # 3 calls should open the circuit
        for _ in range(3):
            with pytest.raises(DataFetchError):
                mgr.get_ohlcv("AMZN", days=5)

        assert mgr._is_circuit_open("bad_provider", "ohlcv")

    def test_no_data_error_does_not_increment_circuit_breaker(self):
        mgr = FetcherManager(
            [_NoDataFetcher()],
            circuit_open_threshold=3,
            circuit_reset_seconds=300,
        )
        for _ in range(5):
            with pytest.raises(DataFetchError):
                mgr.get_ohlcv("AMZN", days=5)

        # Circuit must NOT have opened
        assert not mgr._is_circuit_open("no_data", "ohlcv")
        assert mgr._failures.get(("no_data", "ohlcv"), 0) == 0

    def test_ohlcv_circuit_does_not_block_realtime(self):
        """Opening the circuit for 'ohlcv' must not affect 'realtime'."""
        mgr = FetcherManager(
            [_ProviderErrorFetcher(), _GoodFetcher()],
            circuit_open_threshold=1,
            circuit_reset_seconds=300,
        )

        # Force-open the ohlcv circuit for bad_provider manually
        mgr._record_failure("bad_provider", "ohlcv")
        assert mgr._is_circuit_open("bad_provider", "ohlcv")

        # realtime circuit should still be closed
        assert not mgr._is_circuit_open("bad_provider", "realtime")

    def test_circuit_auto_resets_after_timeout(self):
        """Circuit should auto-reset after reset_seconds elapses."""
        mgr = FetcherManager(
            [_ProviderErrorFetcher()],
            circuit_open_threshold=1,
            circuit_reset_seconds=10,
        )
        # Trigger the circuit open
        with pytest.raises(DataFetchError):
            mgr.get_ohlcv("AMZN", days=5)

        assert mgr._is_circuit_open("bad_provider", "ohlcv")

        # Artificially backdate the open time to simulate timeout elapsed
        key = ("bad_provider", "ohlcv")
        with mgr._lock:
            mgr._circuit_open_at[key] = datetime.now() - timedelta(seconds=11)

        # Now the circuit should auto-reset
        assert not mgr._is_circuit_open("bad_provider", "ohlcv")

    def test_record_success_resets_failures(self):
        mgr = FetcherManager([_GoodFetcher()])
        mgr._record_failure("good", "ohlcv")
        mgr._record_failure("good", "ohlcv")
        assert mgr._failures.get(("good", "ohlcv"), 0) == 2
        mgr._record_success("good", "ohlcv")
        assert mgr._failures.get(("good", "ohlcv"), 0) == 0

    def test_data_fetch_error_when_all_circuits_open(self):
        mgr = FetcherManager(
            [_ProviderErrorFetcher()],
            circuit_open_threshold=1,
            circuit_reset_seconds=300,
        )
        # Open the circuit
        with pytest.raises(DataFetchError):
            mgr.get_ohlcv("AMZN", days=5)

        # Now the circuit is open — next call should also raise DataFetchError
        with pytest.raises(DataFetchError):
            mgr.get_ohlcv("AMZN", days=5)


# ---------------------------------------------------------------------------
# 3. YfinanceFetcher
# ---------------------------------------------------------------------------

class TestYfinanceFetcherCodeNormalization:
    def test_bare_ticker_unchanged(self):
        assert _normalize_code("AMZN") == "AMZN"

    def test_us_stk_prefix_stripped(self):
        assert _normalize_code("US_STK_NVDA") == "NVDA"

    def test_us_etf_prefix_stripped(self):
        assert _normalize_code("US_ETF_SPY") == "SPY"

    def test_rsu_prefix_stripped(self):
        assert _normalize_code("RSU_AMZN") == "AMZN"

    def test_cn_fund_raises_unsupported(self):
        with pytest.raises(UnsupportedCodeError):
            _normalize_code("CN_FUND_900008")

    def test_unknown_prefix_raises_unsupported(self):
        with pytest.raises(UnsupportedCodeError):
            _normalize_code("GOLD_PAPER_CMB")

    def test_mixed_case_bare_ticker_raises(self):
        # Not all-uppercase — should raise
        with pytest.raises(UnsupportedCodeError):
            _normalize_code("Amzn")


def _make_yf_dataframe(tickers=None) -> pd.DataFrame:
    """Build a minimal yfinance-style DataFrame for mocking."""
    idx = pd.to_datetime([
        "2025-01-10", "2025-01-13", "2025-01-14", "2025-01-15",
        "2025-01-16", "2025-01-17",
    ])
    data = {
        "Open":   [180.0, 181.0, 182.0, 183.0, 184.0, 185.0],
        "High":   [182.0, 183.0, 184.0, 185.0, 186.0, 187.0],
        "Low":    [178.0, 179.0, 180.0, 181.0, 182.0, 183.0],
        "Close":  [181.0, 182.0, 183.0, 184.0, 185.0, 186.0],
        "Volume": [1e7, 1.1e7, 1.2e7, 1.3e7, 1.4e7, 1.5e7],
    }
    df = pd.DataFrame(data, index=idx)
    return df


class TestYfinanceFetcher:
    def test_fetch_ohlcv_returns_correct_bars(self):
        fetcher = YfinanceFetcher()
        mock_df = _make_yf_dataframe()

        with patch("yfinance.download", return_value=mock_df):
            bars = fetcher.fetch_ohlcv("AMZN", days=5)

        # We request 5 bars; fixture has 6 rows — tail(5) = last 5
        assert len(bars) == 5
        assert all(isinstance(b, OHLCVBar) for b in bars)
        assert bars[0].source == "yfinance"

    def test_fetch_ohlcv_date_is_python_date(self):
        fetcher = YfinanceFetcher()
        mock_df = _make_yf_dataframe()

        with patch("yfinance.download", return_value=mock_df):
            bars = fetcher.fetch_ohlcv("AMZN", days=6)

        for bar in bars:
            assert isinstance(bar.date, date)

    def test_fetch_ohlcv_timezone_aware_index(self):
        """Timezone-aware DatetimeIndex should be handled without error."""
        fetcher = YfinanceFetcher()
        mock_df = _make_yf_dataframe()
        # Make the index timezone-aware
        mock_df.index = mock_df.index.tz_localize("America/New_York")

        with patch("yfinance.download", return_value=mock_df):
            bars = fetcher.fetch_ohlcv("AMZN", days=6)

        for bar in bars:
            assert isinstance(bar.date, date)

    def test_fetch_ohlcv_pct_chg_calculated(self):
        fetcher = YfinanceFetcher()
        mock_df = _make_yf_dataframe()

        with patch("yfinance.download", return_value=mock_df):
            bars = fetcher.fetch_ohlcv("AMZN", days=6)

        # First bar has no previous price → pct_chg should be None
        assert bars[0].pct_chg is None
        # Subsequent bars should have a pct_chg
        assert bars[1].pct_chg is not None
        expected = (bars[1].close - bars[0].close) / bars[0].close * 100
        assert abs(bars[1].pct_chg - expected) < 1e-6

    def test_fetch_ohlcv_empty_result_raises_no_data(self):
        fetcher = YfinanceFetcher()
        with patch("yfinance.download", return_value=pd.DataFrame()):
            with pytest.raises(NoDataError):
                fetcher.fetch_ohlcv("AMZN", days=5)

    def test_fetch_ohlcv_network_error_raises_provider_error(self):
        fetcher = YfinanceFetcher()
        with patch("yfinance.download", side_effect=ConnectionError("timeout")):
            with pytest.raises(ProviderError):
                fetcher.fetch_ohlcv("AMZN", days=5)

    def test_fetch_ohlcv_unsupported_code_raises(self):
        fetcher = YfinanceFetcher()
        with pytest.raises(UnsupportedCodeError):
            fetcher.fetch_ohlcv("CN_FUND_900008", days=5)

    def test_fetch_ohlcv_multiindex_columns_handled(self):
        """yfinance >= 0.2 may return MultiIndex columns for single ticker."""
        fetcher = YfinanceFetcher()
        mock_df = _make_yf_dataframe()
        # Simulate MultiIndex
        mock_df.columns = pd.MultiIndex.from_tuples(
            [(c, "AMZN") for c in mock_df.columns]
        )
        with patch("yfinance.download", return_value=mock_df):
            bars = fetcher.fetch_ohlcv("AMZN", days=5)
        assert len(bars) == 5

    def test_fetch_realtime_returns_quote(self):
        fetcher = YfinanceFetcher()
        mock_fast_info = {"lastPrice": 188.5, "regularMarketChangePercent": 0.5, "regularMarketVolume": 5_000_000}
        mock_ticker = MagicMock()
        mock_ticker.fast_info = mock_fast_info

        with patch("yfinance.Ticker", return_value=mock_ticker):
            quote = fetcher.fetch_realtime("AMZN")

        assert isinstance(quote, RealtimeQuote)
        assert quote.price == 188.5
        assert quote.source == "yfinance"

    def test_fetch_realtime_no_price_raises_no_data(self):
        fetcher = YfinanceFetcher()
        mock_ticker = MagicMock()
        mock_ticker.fast_info = {}

        with patch("yfinance.Ticker", return_value=mock_ticker):
            with pytest.raises(NoDataError):
                fetcher.fetch_realtime("AMZN")

    def test_fetch_realtime_network_error_raises_provider_error(self):
        fetcher = YfinanceFetcher()
        with patch("yfinance.Ticker", side_effect=ConnectionError("timeout")):
            with pytest.raises(ProviderError):
                fetcher.fetch_realtime("AMZN")


# ---------------------------------------------------------------------------
# 4. AkshareFundFetcher
# ---------------------------------------------------------------------------

class TestAkshareFundCodeExtraction:
    def test_valid_6digit_code(self):
        assert _extract_fund_code("CN_FUND_900008") == "900008"

    def test_5_digit_raises(self):
        with pytest.raises(UnsupportedCodeError):
            _extract_fund_code("CN_FUND_12345")

    def test_7_digit_raises(self):
        with pytest.raises(UnsupportedCodeError):
            _extract_fund_code("CN_FUND_1234567")

    def test_non_cn_fund_prefix_raises(self):
        with pytest.raises(UnsupportedCodeError):
            _extract_fund_code("US_STK_AMZN")

    def test_letters_in_code_raises(self):
        with pytest.raises(UnsupportedCodeError):
            _extract_fund_code("CN_FUND_00702A")


def _make_akshare_dataframe() -> pd.DataFrame:
    """Build a minimal akshare fund NAV DataFrame with recent dates."""
    today = date.today()
    dates = [(today - timedelta(days=4 - i)).strftime("%Y-%m-%d") for i in range(5)]
    return pd.DataFrame({
        "净值日期": dates,
        "单位净值": ["1.2100", "1.2150", "1.2200", "1.2180", "1.2250"],
    })


class TestAkshareFundFetcher:
    def test_fetch_ohlcv_returns_correct_bars(self):
        fetcher = AkshareFundFetcher()
        mock_df = _make_akshare_dataframe()

        with patch("akshare.fund_open_fund_info_em", return_value=mock_df) as mock_fund_info:
            bars = fetcher.fetch_ohlcv("CN_FUND_900008", days=5)

        mock_fund_info.assert_called_once_with(symbol="900008", indicator="单位净值走势")
        assert len(bars) == 5
        assert all(isinstance(b, OHLCVBar) for b in bars)
        assert bars[0].source == "akshare_fund"

    def test_fetch_ohlcv_none_fields(self):
        """CN funds have no open/high/low/volume."""
        fetcher = AkshareFundFetcher()
        mock_df = _make_akshare_dataframe()

        with patch("akshare.fund_open_fund_info_em", return_value=mock_df):
            bars = fetcher.fetch_ohlcv("CN_FUND_900008", days=5)

        for bar in bars:
            assert bar.open is None
            assert bar.high is None
            assert bar.low is None
            assert bar.volume is None

    def test_fetch_ohlcv_sorted_ascending(self):
        """Bars must be in ascending date order."""
        fetcher = AkshareFundFetcher()
        # Provide data in reverse order
        mock_df = _make_akshare_dataframe().iloc[::-1].reset_index(drop=True)

        with patch("akshare.fund_open_fund_info_em", return_value=mock_df):
            bars = fetcher.fetch_ohlcv("CN_FUND_900008", days=5)

        dates = [b.date for b in bars]
        assert dates == sorted(dates)

    def test_fetch_ohlcv_deduplication(self):
        """Duplicate dates should be dropped (keep last)."""
        fetcher = AkshareFundFetcher()
        today = date.today()
        d0 = (today - timedelta(days=2)).strftime("%Y-%m-%d")
        d1 = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        df_dup = pd.DataFrame({
            "净值日期": [d0, d0, d1],
            "单位净值": ["1.2100", "1.2200", "1.2300"],
        })

        with patch("akshare.fund_open_fund_info_em", return_value=df_dup):
            bars = fetcher.fetch_ohlcv("CN_FUND_900008", days=30)

        dates = [b.date for b in bars]
        assert len(dates) == len(set(dates)), "Duplicate dates were not removed"
        # The last occurrence (1.2200) should be kept for d0
        dup_bar = next(b for b in bars if str(b.date) == d0)
        assert dup_bar.close == pytest.approx(1.2200)

    def test_fetch_ohlcv_null_nav_filtered(self):
        """Rows with null NAV should be dropped."""
        fetcher = AkshareFundFetcher()
        today = date.today()
        dates = [(today - timedelta(days=2 - i)).strftime("%Y-%m-%d") for i in range(3)]
        df_with_null = pd.DataFrame({
            "净值日期": dates,
            "单位净值": ["1.2100", None, "1.2300"],
        })

        with patch("akshare.fund_open_fund_info_em", return_value=df_with_null):
            bars = fetcher.fetch_ohlcv("CN_FUND_900008", days=30)

        assert all(b.close is not None for b in bars)
        assert len(bars) == 2

    def test_fetch_ohlcv_empty_result_raises_no_data(self):
        fetcher = AkshareFundFetcher()
        with patch("akshare.fund_open_fund_info_em", return_value=pd.DataFrame()):
            with pytest.raises(NoDataError):
                fetcher.fetch_ohlcv("CN_FUND_900008", days=5)

    def test_fetch_ohlcv_network_error_raises_provider_error(self):
        fetcher = AkshareFundFetcher()
        with patch("akshare.fund_open_fund_info_em", side_effect=ConnectionError("timeout")):
            with pytest.raises(ProviderError):
                fetcher.fetch_ohlcv("CN_FUND_900008", days=5)

    def test_fetch_ohlcv_unsupported_code_raises(self):
        fetcher = AkshareFundFetcher()
        with pytest.raises(UnsupportedCodeError):
            fetcher.fetch_ohlcv("US_STK_AMZN", days=5)

    def test_fetch_realtime_returns_quote(self):
        fetcher = AkshareFundFetcher()
        mock_df = _make_akshare_dataframe()

        with patch("akshare.fund_open_fund_info_em", return_value=mock_df):
            quote = fetcher.fetch_realtime("CN_FUND_900008")

        assert isinstance(quote, RealtimeQuote)
        assert quote.source == "akshare_fund"
        assert quote.price == pytest.approx(1.2250)

    def test_fetch_realtime_no_data_raises(self):
        fetcher = AkshareFundFetcher()
        with patch("akshare.fund_open_fund_info_em", return_value=pd.DataFrame()):
            with pytest.raises((NoDataError, DataFetchError)):
                fetcher.fetch_realtime("CN_FUND_900008")


# ---------------------------------------------------------------------------
# 5. MarketDataService
# ---------------------------------------------------------------------------

class TestMarketDataServiceDetectMarket:
    def setup_method(self):
        # Prevent real akshare/yfinance calls during instantiation
        self.svc = MarketDataService.__new__(MarketDataService)
        self.svc._scrapers = {}
        self.svc._fetchers = {}
        self.svc._register_default_fetchers()

    def test_cn_fund_detected(self):
        assert self.svc._detect_market("CN_FUND_900008") == "cn_fund"

    def test_bare_ticker_detected_as_us(self):
        assert self.svc._detect_market("AMZN") == "us"

    def test_us_stk_detected(self):
        assert self.svc._detect_market("US_STK_NVDA") == "us"

    def test_us_etf_detected(self):
        assert self.svc._detect_market("US_ETF_SPY") == "us"

    def test_rsu_detected(self):
        assert self.svc._detect_market("RSU_AMZN") == "us"

    def test_unknown_raises_unsupported(self):
        with pytest.raises(UnsupportedCodeError):
            self.svc._detect_market("UNKNOWN_CODE")

    def test_lowercase_ticker_raises_unsupported(self):
        with pytest.raises(UnsupportedCodeError):
            self.svc._detect_market("amzn")


class TestMarketDataServiceGetOhlcv:
    def test_get_ohlcv_returns_dataframe_with_correct_columns(self):
        svc = MarketDataService.__new__(MarketDataService)
        svc._scrapers = {}
        svc._fetchers = {}
        # Use min_bars=5 so the 5-bar mock fixture is accepted
        svc._fetchers["us"] = FetcherManager([YfinanceFetcher()], min_bars=5)
        svc._fetchers["cn_fund"] = FetcherManager([AkshareFundFetcher()], min_bars=5)

        mock_df = _make_yf_dataframe()
        with patch("yfinance.download", return_value=mock_df):
            df = svc.get_ohlcv("AMZN", days=5)

        assert isinstance(df, pd.DataFrame)
        required_cols = {"date", "open", "high", "low", "close", "volume", "pct_chg", "source"}
        assert required_cols.issubset(set(df.columns))
        assert len(df) == 5


class TestMarketDataServiceGetRealtimeQuote:
    def test_returns_none_when_all_fetchers_fail(self):
        svc = MarketDataService.__new__(MarketDataService)
        svc._scrapers = {}
        svc._fetchers = {}
        svc._register_default_fetchers()

        with patch("yfinance.Ticker", side_effect=ConnectionError("timeout")):
            result = svc.get_realtime_quote("AMZN")

        # Should return None, not raise
        assert result is None

    def test_raises_for_unsupported_code(self):
        # get_realtime_quote now propagates UnsupportedCodeError (contract change)
        # so callers can distinguish skip (unsupported) from error (transient)
        svc = MarketDataService.__new__(MarketDataService)
        svc._scrapers = {}
        svc._fetchers = {}
        svc._register_default_fetchers()

        with pytest.raises(UnsupportedCodeError):
            svc.get_realtime_quote("UNKNOWN_CODE_XYZ")

    def test_returns_quote_on_success(self):
        svc = MarketDataService.__new__(MarketDataService)
        svc._scrapers = {}
        svc._fetchers = {}
        svc._register_default_fetchers()

        mock_fast_info = {"lastPrice": 188.5}
        mock_ticker = MagicMock()
        mock_ticker.fast_info = mock_fast_info

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = svc.get_realtime_quote("AMZN")

        assert result is not None
        assert result.price == 188.5
