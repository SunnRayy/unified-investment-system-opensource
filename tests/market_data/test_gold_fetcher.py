"""Tests for GoldPriceFetcher.

All akshare calls are mocked — no real network access.
"""

from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

from src.market_data.fetchers.base import (
    NoDataError,
    ProviderError,
    UnsupportedCodeError,
)
from src.market_data.fetchers.gold_fetcher import (
    GoldPriceFetcher,
    _is_gold_code,
    _load_sge_history,
    _normalize_gold_code,
)
from src.market_data.service import MarketDataService
from src.validation.reader_validator import extract_symbol


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sge_df(n: int = 30) -> pd.DataFrame:
    """Create a fake akshare SGE DataFrame with n rows."""
    today = date.today()
    rows = [
        {
            "日期": (today - timedelta(days=n - i - 1)).isoformat(),
            "收盘": 450.0 + i,
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# _is_gold_code
# ---------------------------------------------------------------------------

def test_is_gold_code_alts_paper_gold():
    assert _is_gold_code("ALTS_Paper_Gold") is True


def test_is_gold_code_gold_prefix():
    assert _is_gold_code("GOLD_PAPER_CMB") is True


def test_is_gold_code_raw_gold():
    assert _is_gold_code("Gold") is True


def test_is_gold_code_non_gold():
    assert _is_gold_code("US_STK_AAPL") is False
    assert _is_gold_code("CN_FUND_900008") is False


# ---------------------------------------------------------------------------
# _normalize_gold_code
# ---------------------------------------------------------------------------

def test_normalize_alts_paper_gold():
    assert _normalize_gold_code("ALTS_Paper_Gold") == "Gold"


def test_normalize_gold_prefix():
    assert _normalize_gold_code("GOLD_PAPER_CMB") == "Gold"


def test_normalize_raw_gold():
    assert _normalize_gold_code("Gold") == "Gold"


def test_normalize_unsupported_raises():
    with pytest.raises(UnsupportedCodeError):
        _normalize_gold_code("US_STK_AAPL")


def test_normalize_cn_fund_raises():
    with pytest.raises(UnsupportedCodeError):
        _normalize_gold_code("CN_FUND_900008")


# ---------------------------------------------------------------------------
# extract_symbol integration (reader_validator)
# ---------------------------------------------------------------------------

def test_extract_symbol_alts_paper_gold():
    assert extract_symbol("ALTS_Paper_Gold") == "Gold"


def test_extract_symbol_gold_prefix():
    assert extract_symbol("GOLD_PAPER_CMB") == "Gold"


def test_extract_symbol_us_stk_unchanged():
    assert extract_symbol("US_STK_AAPL") == "AAPL"


def test_extract_symbol_cn_fund_unchanged():
    assert extract_symbol("CN_FUND_900008") == "900008"


# ---------------------------------------------------------------------------
# GoldPriceFetcher.fetch_ohlcv
# ---------------------------------------------------------------------------

@patch("src.market_data.fetchers.gold_fetcher.akshare")
def test_fetch_ohlcv_returns_bars(mock_akshare):
    mock_akshare.spot_golden_benchmark_sge.return_value = _make_sge_df(30)
    fetcher = GoldPriceFetcher()
    bars = fetcher.fetch_ohlcv("ALTS_Paper_Gold", days=20)
    assert len(bars) <= 20
    assert all(b.source == "gold_sge" for b in bars)
    assert all(b.code == "Gold" for b in bars)
    assert all(b.close > 0 for b in bars)


@patch("src.market_data.fetchers.gold_fetcher.akshare")
def test_fetch_ohlcv_gold_prefix_code(mock_akshare):
    mock_akshare.spot_golden_benchmark_sge.return_value = _make_sge_df(30)
    fetcher = GoldPriceFetcher()
    bars = fetcher.fetch_ohlcv("GOLD_PAPER_CMB", days=10)
    assert len(bars) > 0
    assert all(b.code == "Gold" for b in bars)


def test_fetch_ohlcv_unsupported_code_raises():
    fetcher = GoldPriceFetcher()
    with pytest.raises(UnsupportedCodeError):
        fetcher.fetch_ohlcv("US_STK_AAPL", days=20)


@patch("src.market_data.fetchers.gold_fetcher.akshare")
def test_fetch_ohlcv_provider_error(mock_akshare):
    mock_akshare.spot_golden_benchmark_sge.side_effect = Exception("network timeout")
    fetcher = GoldPriceFetcher()
    with pytest.raises(ProviderError):
        fetcher.fetch_ohlcv("ALTS_Paper_Gold", days=20)


@patch("src.market_data.fetchers.gold_fetcher.akshare")
def test_fetch_ohlcv_no_data_raises(mock_akshare):
    mock_akshare.spot_golden_benchmark_sge.return_value = pd.DataFrame()
    fetcher = GoldPriceFetcher()
    with pytest.raises(NoDataError):
        fetcher.fetch_ohlcv("ALTS_Paper_Gold", days=20)


# ---------------------------------------------------------------------------
# GoldPriceFetcher.fetch_realtime
# ---------------------------------------------------------------------------

@patch("src.market_data.fetchers.gold_fetcher.akshare")
def test_fetch_realtime_returns_quote(mock_akshare):
    mock_akshare.spot_golden_benchmark_sge.return_value = _make_sge_df(5)
    fetcher = GoldPriceFetcher()
    quote = fetcher.fetch_realtime("ALTS_Paper_Gold")
    assert quote.code == "Gold"
    assert quote.price > 0
    assert quote.source == "gold_sge"
    assert quote.as_of_date != date.min


def test_fetch_realtime_unsupported_code_raises():
    fetcher = GoldPriceFetcher()
    with pytest.raises(UnsupportedCodeError):
        fetcher.fetch_realtime("CN_FUND_900008")


@patch("src.market_data.fetchers.gold_fetcher.akshare")
def test_fetch_realtime_pct_chg_computed(mock_akshare):
    mock_akshare.spot_golden_benchmark_sge.return_value = _make_sge_df(5)
    fetcher = GoldPriceFetcher()
    quote = fetcher.fetch_realtime("ALTS_Paper_Gold")
    # pct_chg should be computed from last two bars
    assert quote.change_pct is not None


@patch("src.market_data.fetchers.gold_fetcher.akshare")
def test_fetch_realtime_handles_current_akshare_columns_without_warning(mock_akshare, caplog):
    mock_akshare.spot_golden_benchmark_sge.return_value = pd.DataFrame(
        {
            "交易时间": ["2026-04-03", "2026-04-04"],
            "晚盘价": [761.2, 762.5],
            "早盘价": [759.9, 761.0],
        }
    )
    fetcher = GoldPriceFetcher()

    with caplog.at_level("WARNING"):
        quote = fetcher.fetch_realtime("ALTS_Paper_Gold")

    assert quote.price == pytest.approx(762.5)
    assert "unexpected columns" not in caplog.text


# ---------------------------------------------------------------------------
# MarketDataService routing
# ---------------------------------------------------------------------------

def test_detect_market_alts_paper_gold():
    svc = MarketDataService()
    assert svc._detect_market("ALTS_Paper_Gold") == "gold"


def test_detect_market_gold_prefix():
    svc = MarketDataService()
    assert svc._detect_market("GOLD_PAPER_CMB") == "gold"


def test_detect_market_cn_fund():
    svc = MarketDataService()
    assert svc._detect_market("CN_FUND_900008") == "cn_fund"


def test_detect_market_us_stk():
    svc = MarketDataService()
    assert svc._detect_market("US_STK_AAPL") == "us"


@patch("src.market_data.fetchers.gold_fetcher.akshare")
def test_service_get_realtime_quote_gold(mock_akshare):
    mock_akshare.spot_golden_benchmark_sge.return_value = _make_sge_df(5)
    svc = MarketDataService()
    quote = svc.get_realtime_quote("ALTS_Paper_Gold")
    assert quote is not None
    assert quote.code == "Gold"
    assert quote.source == "gold_sge"


# ---------------------------------------------------------------------------
# _load_sge_history — primary/fallback source selection
# (spot_hist_sge primary, spot_golden_benchmark_sge fallback)
# ---------------------------------------------------------------------------

def _make_sge_hist_df(n: int = 30) -> pd.DataFrame:
    """Create a fake akshare spot_hist_sge DataFrame with n rows.

    Columns match the real API: date / open / close / low / high.
    """
    today = date.today()
    rows = [
        {
            "date": (today - timedelta(days=n - i - 1)).isoformat(),
            "open": 900.0 + i,
            "close": 905.0 + i,
            "low": 898.0 + i,
            "high": 910.0 + i,
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows)


@patch("src.market_data.fetchers.gold_fetcher.akshare")
def test_load_sge_history_uses_primary_source_when_available(mock_akshare, caplog):
    """spot_hist_sge returns real column shape → primary path is taken;
    spot_golden_benchmark_sge must NOT be called."""
    mock_akshare.spot_hist_sge.return_value = _make_sge_hist_df(5)
    mock_akshare.spot_golden_benchmark_sge.side_effect = RuntimeError("should not be called")

    with caplog.at_level("INFO"):
        df = _load_sge_history()

    assert not df.empty
    assert list(df.columns) == ["date", "close"]
    assert df["close"].iloc[-1] == pytest.approx(905.0 + 4)
    assert "primary source" in caplog.text
    mock_akshare.spot_golden_benchmark_sge.assert_not_called()


@patch("src.market_data.fetchers.gold_fetcher.akshare")
def test_load_sge_history_falls_back_when_primary_raises(mock_akshare, caplog):
    """spot_hist_sge raises → falls back to spot_golden_benchmark_sge."""
    mock_akshare.spot_hist_sge.side_effect = Exception("connection refused")
    mock_akshare.spot_golden_benchmark_sge.return_value = pd.DataFrame(
        {
            "交易时间": ["2026-04-03", "2026-04-04"],
            "晚盘价": [761.2, 762.5],
            "早盘价": [759.9, 761.0],
        }
    )

    with caplog.at_level("INFO"):
        df = _load_sge_history()

    assert not df.empty
    assert list(df.columns) == ["date", "close"]
    assert df["close"].iloc[-1] == pytest.approx(762.5)
    assert "fallback" in caplog.text.lower()


@patch("src.market_data.fetchers.gold_fetcher.akshare")
def test_load_sge_history_both_sources_fail_raises(mock_akshare):
    """Both sources fail → ProviderError is raised."""
    mock_akshare.spot_hist_sge.side_effect = Exception("network error")
    mock_akshare.spot_golden_benchmark_sge.side_effect = Exception("also down")

    with pytest.raises(ProviderError):
        _load_sge_history()
