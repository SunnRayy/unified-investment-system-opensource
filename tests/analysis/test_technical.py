from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis.models import (
    MACDStatus,
    RSIStatus,
    TechnicalSignals,
    TrendStatus,
    VolumeStatus,
)
from src.analysis.technical import StockTrendAnalyzer
from src.market_data.fetchers.base import InsufficientDataError


def _make_df(prices: list[float], include_volume: bool = True) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=len(prices), freq="D"),
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [max(0.01, p - 1) for p in prices],
            "close": prices,
        }
    )
    if include_volume:
        df["volume"] = 1000
    return df


def test_analyzer_instantiation() -> None:
    analyzer = StockTrendAnalyzer()
    assert analyzer is not None


def test_analyze_returns_technical_signals() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df(np.linspace(10, 100, 60).tolist())

    result = analyzer.analyze(df, "TEST")

    assert isinstance(result, TechnicalSignals)


def test_ma_values_correct() -> None:
    analyzer = StockTrendAnalyzer()
    prices = list(range(1, 61))
    df = _make_df(prices)

    result = analyzer.analyze(df, "MA")

    assert result.ma5 is not None
    assert result.ma5 == pytest.approx(np.mean(prices[-5:]), rel=1e-9)


def test_ma_alignment_full_bull() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df(np.linspace(10, 120, 60).tolist())

    result = analyzer.analyze(df, "BULL")

    assert result.ma_alignment_score == 2


def test_ma_alignment_full_bear() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df(np.linspace(120, 10, 60).tolist())

    result = analyzer.analyze(df, "BEAR")

    assert result.ma_alignment_score == 0


def test_rsi_range() -> None:
    analyzer = StockTrendAnalyzer()
    prices = (50 + np.sin(np.linspace(0, 12, 60)) * 10).tolist()
    df = _make_df(prices)

    result = analyzer.analyze(df, "RSI_RANGE")

    assert result.rsi_value is not None
    assert 0 <= result.rsi_value <= 100


def test_rsi_flat_price_neutral() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df([100.0] * 60)

    result = analyzer.analyze(df, "RSI_FLAT")

    assert result.rsi_status == RSIStatus.NEUTRAL


def test_macd_golden_cross_detected() -> None:
    analyzer = StockTrendAnalyzer()
    down = np.linspace(100, 60, 40)
    up = np.concatenate([np.linspace(60, 62, 18), [47, 200]])
    prices = np.concatenate([down, up]).tolist()
    df = _make_df(prices)

    result = analyzer.analyze(df, "MACD_GOLDEN")

    assert result.macd_status == MACDStatus.GOLDEN_CROSS


def test_macd_death_cross_detected() -> None:
    analyzer = StockTrendAnalyzer()
    up = np.linspace(60, 100, 40)
    down = np.concatenate([np.linspace(100, 98, 18), [114, 20]])
    prices = np.concatenate([up, down]).tolist()
    df = _make_df(prices)

    result = analyzer.analyze(df, "MACD_DEATH")

    assert result.macd_status == MACDStatus.DEATH_CROSS


def test_signal_score_range() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df(np.linspace(30, 90, 60).tolist())

    result = analyzer.analyze(df, "SIGNAL_SCORE")

    assert 0 <= result.signal_score <= 100


def test_trend_direction_score_range() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df(np.linspace(30, 90, 60).tolist())

    result = analyzer.analyze(df, "TREND_DIR_SCORE")

    assert 0 <= result.trend_direction_score <= 70


def test_trend_status_strong_bull() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df(np.linspace(10, 200, 60).tolist())

    result = analyzer.analyze(df, "STRONG_BULL")

    assert result.trend_status in (TrendStatus.STRONG_BULL, TrendStatus.BULL)


def test_trend_status_strong_bear() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df(np.linspace(200, 10, 60).tolist())

    result = analyzer.analyze(df, "STRONG_BEAR")

    assert result.trend_status in (TrendStatus.STRONG_BEAR, TrendStatus.BEAR)


def test_to_dict_keys() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df(np.linspace(10, 100, 60).tolist())

    result = analyzer.analyze(df, "DICT_KEYS")
    payload = result.to_dict()

    required = {
        "trend_status",
        "ma5",
        "ma10",
        "ma20",
        "rsi_value",
        "macd_line",
        "signal_score",
        "trend_direction_score",
        "support_levels",
        "resistance_levels",
    }
    assert required.issubset(payload.keys())


def test_to_compact_str_format() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df(np.linspace(10, 100, 60).tolist())

    result = analyzer.analyze(df, "COMPACT")
    text = result.to_compact_str()

    assert "Trend:" in text
    assert "RSI=" in text
    assert "MACD=" in text
    assert "Score=" in text


def test_insufficient_data_25_rows() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df(np.linspace(10, 100, 25).tolist())

    with pytest.raises(InsufficientDataError):
        analyzer.analyze(df, "SHORT25")


def test_insufficient_data_empty() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df([], include_volume=False)

    with pytest.raises(InsufficientDataError):
        analyzer.analyze(df, "EMPTY")


def test_missing_volume_defaults() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df(np.linspace(20, 120, 60).tolist(), include_volume=False)

    result = analyzer.analyze(df, "NO_VOL")

    assert result.volume_ratio is None
    assert result.volume_status == VolumeStatus.NORMAL


def test_to_compact_str_bull() -> None:
    analyzer = StockTrendAnalyzer()
    df = _make_df(np.linspace(20, 220, 60).tolist())

    result = analyzer.analyze(df, "COMPACT_BULL")
    text = result.to_compact_str()

    assert text.startswith("Trend:STRONG_BULL") or text.startswith("Trend:BULL")


def test_support_below_current_price() -> None:
    analyzer = StockTrendAnalyzer()
    prices = np.linspace(20, 220, 60).tolist()
    df = _make_df(prices)

    result = analyzer.analyze(df, "SUPPORT")
    current_close = prices[-1]

    assert all(level <= current_close for level in result.support_levels)
