
"""Tests for currency converter service."""
import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from src.data_manager.currency_converter import (
    CurrencyConverterService,
    get_default_usd_cny_rate,
)


class TestCurrencyConverter:
    def test_same_currency_returns_one(self):
        """Same currency conversion should return 1.0."""
        service = CurrencyConverterService()
        rate = service.get_historical_rate('USD', 'USD', pd.Timestamp('2026-01-15'))
        assert rate == 1.0

    def test_usd_cny_uses_fallback_rate(self):
        """USD to CNY should use fallback rate if no API available."""
        service = CurrencyConverterService(enable_google_finance=False)
        rate = service.get_historical_rate('USD', 'CNY', pd.Timestamp('2026-01-15'))
        assert rate is not None
        assert abs(rate - 7.0) < 0.001  # Updated to expect exactly 7.0 fallback

    def test_convert_amount_applies_rate(self):
        """convert_amount should multiply by exchange rate."""
        service = CurrencyConverterService(enable_google_finance=False)
        result = service.convert_amount(100.0, 'USD', 'CNY', pd.Timestamp('2026-01-15'))
        assert result is not None
        assert 650 < result < 800  # 100 USD * ~7 = ~700 CNY

    def test_cny_to_usd_inverts_rate(self):
        """CNY to USD should invert the USD/CNY rate."""
        service = CurrencyConverterService(enable_google_finance=False)
        usd_cny = service.get_historical_rate('USD', 'CNY', pd.Timestamp('2026-01-15'))
        cny_usd = service.get_historical_rate('CNY', 'USD', pd.Timestamp('2026-01-15'))
        assert abs(usd_cny * cny_usd - 1.0) < 0.001


class TestCurrencyConverterUnifiedChain:
    """Tests for the unified FX resolution chain introduced in B3.

    Chain: yfinance (today only) → Google Finance → config fallback.

    NOTE: fetch_fx_rates is imported lazily inside get_historical_rate() via
    ``from src.market_data.fetchers.yfinance_fetcher import fetch_fx_rates``.
    We must patch it at its source: "src.market_data.fetchers.yfinance_fetcher.fetch_fx_rates".
    Similarly, GoogleFinanceConnector is patched at its source module.
    """

    def test_latest_rate_uses_yfinance_first(self):
        """get_latest_rate() for a today timestamp uses yfinance fast_info as step 0."""
        service = CurrencyConverterService(enable_google_finance=True)
        mock_yf_rates = {"USD": 7.3456, "HKD": 0.9123}

        with patch(
            "src.market_data.fetchers.yfinance_fetcher.fetch_fx_rates",
            return_value=mock_yf_rates,
        ):
            rate = service.get_latest_rate("USD", "CNY")

        assert rate == pytest.approx(7.3456)

    def test_latest_rate_falls_back_to_google_when_yfinance_returns_default(self):
        """When yfinance returns the hard-coded default (7.0), fall through to Google Finance."""
        service = CurrencyConverterService(enable_google_finance=True)
        # yfinance chain returns the exact hard-coded default → no live data
        mock_yf_rates = {"USD": 7.0, "HKD": 0.9}

        mock_connector = MagicMock()
        mock_connector.get_exchange_rate.return_value = 7.4567

        with patch(
            "src.market_data.fetchers.yfinance_fetcher.fetch_fx_rates",
            return_value=mock_yf_rates,
        ), patch(
            "src.data_manager.connectors.google_finance_connector.get_google_finance_connector",
            return_value=mock_connector,
        ):
            rate = service.get_latest_rate("USD", "CNY")

        # Falls through to Google Finance since yfinance matched the hard-coded default
        assert rate == pytest.approx(7.4567)

    def test_historical_rate_skips_yfinance(self):
        """get_historical_rate() for a past date must NOT call fetch_fx_rates (step 0 skipped)."""
        service = CurrencyConverterService(enable_google_finance=True)

        mock_connector = MagicMock()
        mock_connector.get_exchange_rate.return_value = 6.9000

        with patch(
            "src.market_data.fetchers.yfinance_fetcher.fetch_fx_rates",
        ) as mock_yf, patch(
            "src.data_manager.connectors.google_finance_connector.get_google_finance_connector",
            return_value=mock_connector,
        ):
            rate = service.get_historical_rate("USD", "CNY", pd.Timestamp("2025-01-15"))

        mock_yf.assert_not_called()
        assert rate == pytest.approx(6.9000)

    def test_all_sources_fail_uses_fallback(self):
        """When yfinance and Google both fail, the hard-coded fallback 7.0 is returned."""
        service = CurrencyConverterService(enable_google_finance=True)

        with patch(
            "src.market_data.fetchers.yfinance_fetcher.fetch_fx_rates",
            side_effect=RuntimeError("yf dead"),
        ), patch(
            "src.data_manager.connectors.google_finance_connector.get_google_finance_connector",
            side_effect=RuntimeError("gf dead"),
        ):
            rate = service.get_latest_rate("USD", "CNY")

        assert rate == pytest.approx(7.0)

    def test_google_finance_disabled_uses_fallback(self):
        """With enable_google_finance=False, no network is hit; fallback is returned."""
        service = CurrencyConverterService(enable_google_finance=False)
        rate = service.get_latest_rate("USD", "CNY")
        assert rate == pytest.approx(7.0)

    def test_cache_prevents_redundant_calls(self):
        """A second call for the same (currency, date) should not re-invoke fetch_fx_rates."""
        service = CurrencyConverterService(enable_google_finance=True)
        mock_yf_rates = {"USD": 7.5000, "HKD": 0.95}

        with patch(
            "src.market_data.fetchers.yfinance_fetcher.fetch_fx_rates",
            return_value=mock_yf_rates,
        ) as mock_yf:
            ts = pd.Timestamp.now()
            rate1 = service.get_historical_rate("USD", "CNY", ts)
            rate2 = service.get_historical_rate("USD", "CNY", ts)

        assert rate1 == pytest.approx(7.5000)
        assert rate2 == pytest.approx(7.5000)
        # fetch_fx_rates called at most once (cache hit on second call)
        assert mock_yf.call_count <= 1


class TestGetDefaultUsdCnyRate:
    """Program OSR WS-2 step 3 — the single settings-driven accessor that
    replaced six independent hardcoded 7.0 literals."""

    def test_reads_configured_rate(self):
        with patch(
            "src.config.load_config",
            return_value={"currency": {"fallback_rates": {"USD_CNY": 7.25}}},
        ):
            assert get_default_usd_cny_rate() == pytest.approx(7.25)

    def test_falls_back_to_7_0_when_key_absent(self):
        with patch("src.config.load_config", return_value={"currency": {}}):
            assert get_default_usd_cny_rate() == pytest.approx(7.0)

    def test_falls_back_to_7_0_when_config_missing_currency_section(self):
        with patch("src.config.load_config", return_value={}):
            assert get_default_usd_cny_rate() == pytest.approx(7.0)

    def test_falls_back_to_7_0_when_config_loading_raises(self):
        with patch("src.config.load_config", side_effect=RuntimeError("no config file")):
            assert get_default_usd_cny_rate() == pytest.approx(7.0)

    def test_matches_repo_settings_yaml_default(self):
        """Zero-behavior-change guard: the repo's own config/settings.yaml
        must still resolve to the historical 7.0 default (WS-2 step 3 must
        not silently change Ray's live sync)."""
        assert get_default_usd_cny_rate() == pytest.approx(7.0)
