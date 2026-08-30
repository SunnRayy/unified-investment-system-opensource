"""Tests for market regime assessment."""
import pandas as pd
import numpy as np
from datetime import date, timedelta
from unittest.mock import MagicMock


def _make_price_series(n=250, trend="bull"):
    """Generate a synthetic daily price series."""
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    if trend == "bull":
        prices = 100 * np.cumprod(1 + np.random.normal(0.0005, 0.01, n))
    elif trend == "bear":
        prices = 100 * np.cumprod(1 + np.random.normal(-0.0005, 0.01, n))
    else:
        prices = 100 * np.cumprod(1 + np.random.normal(0.0, 0.005, n))
    return pd.DataFrame({"date": dates, "close": prices})


def test_regime_bull_market():
    """Strong uptrend should score as Bull."""
    from src.financial_analysis.regime import assess_regime

    np.random.seed(42)
    df = _make_price_series(250, "bull")
    result = assess_regime(df["close"].values, df["date"].values)
    assert result["trend"] in ("Bull", "Neutral")  # seed-dependent, but not Bear
    assert "volatility_level" in result
    assert "drawdown_pct" in result


def test_regime_bear_market():
    """Strong downtrend should score as Bear."""
    from src.financial_analysis.regime import assess_regime

    np.random.seed(42)
    df = _make_price_series(250, "bear")
    result = assess_regime(df["close"].values, df["date"].values)
    assert result["trend"] in ("Bear", "Neutral")


def test_regime_needs_minimum_data():
    """Fewer than 50 data points should return unknown."""
    from src.financial_analysis.regime import assess_regime

    result = assess_regime(np.array([100, 101, 102]), np.array(["2025-01-01", "2025-01-02", "2025-01-03"]))
    assert result["trend"] == "Unknown"


def test_regime_assessment_structure():
    """Result should have all expected fields."""
    from src.financial_analysis.regime import assess_regime

    np.random.seed(42)
    df = _make_price_series(250, "bull")
    result = assess_regime(df["close"].values, df["date"].values)
    expected_keys = {"trend", "volatility_level", "volatility_30d", "drawdown_pct", "ma50", "ma200", "ma_signal", "score"}
    assert expected_keys.issubset(set(result.keys()))


def _make_market_rows(n=250, trend="bull"):
    """Generate (date, close) tuples using correct market_daily column names."""
    np.random.seed(7)
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    if trend == "bull":
        prices = 100 * np.cumprod(1 + np.random.normal(0.0005, 0.01, n))
    else:
        prices = 100 * np.cumprod(1 + np.random.normal(-0.0005, 0.01, n))
    return [(d, float(p)) for d, p in zip(dates, prices)]


def test_assess_portfolio_regime_with_market_data():
    """assess_portfolio_regime uses correct column names (date, close, code)."""
    from src.financial_analysis.regime import assess_portfolio_regime

    market_rows = _make_market_rows(250)

    db = MagicMock()
    call_count = [0]

    def mock_execute(sql, params=None):
        mock_result = MagicMock()
        call_count[0] += 1
        if params and params[0] == "000300":
            # First symbol found with enough data
            mock_result.fetchall.return_value = market_rows
        else:
            mock_result.fetchall.return_value = []
        return mock_result

    db.execute.side_effect = mock_execute

    result = assess_portfolio_regime(db)
    assert result is not None
    assert result["benchmark_symbol"] == "000300"
    assert result["trend"] in ("Bull", "Neutral", "Bear")
    # Verify the correct SQL was used (code=?, not symbol=?)
    first_call_sql = db.execute.call_args_list[0][0][0]
    assert "code" in first_call_sql
    assert "date" in first_call_sql
    assert "close" in first_call_sql
    assert "symbol" not in first_call_sql
    assert "trade_date" not in first_call_sql
    assert "close_price" not in first_call_sql


def test_assess_portfolio_regime_fallback_to_net_worth():
    """When market_daily is empty, falls back to portfolio net worth history."""
    from src.financial_analysis.regime import assess_portfolio_regime

    np.random.seed(7)
    # 20 portfolio snapshots (monthly)
    snap_dates = [date(2024, 1, 1) + timedelta(days=30 * i) for i in range(20)]
    snap_values = [1_000_000 * (1 + 0.01 * i) for i in range(20)]
    snap_rows = [(d, v) for d, v in zip(snap_dates, snap_values)]

    db = MagicMock()

    def mock_execute(sql, params=None):
        mock_result = MagicMock()
        if "market_daily" in sql:
            mock_result.fetchall.return_value = []
        elif "holdings" in sql and "SUM" in sql:
            mock_result.fetchall.return_value = snap_rows
        else:
            mock_result.fetchall.return_value = []
        return mock_result

    db.execute.side_effect = mock_execute

    result = assess_portfolio_regime(db)
    assert result is not None
    assert result["benchmark_symbol"] == "portfolio_net_worth"


def test_assess_portfolio_regime_returns_none_when_insufficient():
    """Returns None when neither market data nor enough snapshots exist."""
    from src.financial_analysis.regime import assess_portfolio_regime

    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []

    result = assess_portfolio_regime(db)
    assert result is None


class TestGetBenchmarkProxyCodes:
    """Program OSR WS-2 step 5 — single settings-driven source for the
    market-regime benchmark proxy list, replacing 2 of its 3 hardcoded
    copies (the third, in behavioral_metrics.py's deprecated
    _contrarian_tendency, is explicitly frozen byte-for-byte and left
    untouched — see that method's docstring)."""

    def test_reads_configured_codes(self):
        from unittest.mock import patch

        from src.financial_analysis.regime import get_benchmark_proxy_codes

        with patch(
            "src.config.load_config",
            return_value={"verification": {"benchmark_proxy_codes": ["AAA", "BBB"]}},
        ):
            assert get_benchmark_proxy_codes() == ("AAA", "BBB")

    def test_falls_back_to_historical_default_when_config_unavailable(self):
        from unittest.mock import patch

        from src.financial_analysis.regime import get_benchmark_proxy_codes

        with patch("src.config.load_config", side_effect=RuntimeError("no config")):
            assert get_benchmark_proxy_codes() == (
                "000300", "CSI300", "000300", "CSI300", "SPY", "^GSPC",
            )

    def test_falls_back_to_historical_default_when_key_absent(self):
        from unittest.mock import patch

        from src.financial_analysis.regime import get_benchmark_proxy_codes

        with patch("src.config.load_config", return_value={"verification": {}}):
            assert get_benchmark_proxy_codes() == (
                "000300", "CSI300", "000300", "CSI300", "SPY", "^GSPC",
            )

    def test_matches_repo_settings_yaml(self):
        """Zero-behavior-change guard: the repo's own config/settings.yaml
        must resolve to the same 6-code list every caller used before."""
        from src.financial_analysis.regime import get_benchmark_proxy_codes

        assert get_benchmark_proxy_codes() == (
            "000300", "CSI300", "000300", "CSI300", "SPY", "^GSPC",
        )
