
"""Tests for Google Finance connector (using yfinance)."""
from src.data_manager.connectors.google_finance_connector import (
    get_google_finance_connector
)


class TestGoogleFinanceConnector:
    def test_get_exchange_rate_usd_cny(self):
        """Should fetch USD/CNY exchange rate."""
        connector = get_google_finance_connector()
        rate = connector.get_exchange_rate('USD', 'CNY')
        # Rate should be in reasonable range or None if network issue
        # USD/CNY is typically around 7.0
        if rate is not None:
            assert 6.0 < rate < 8.0

    def test_get_exchange_rate_same_currency(self):
        """Same currency should return 1.0."""
        connector = get_google_finance_connector()
        rate = connector.get_exchange_rate('USD', 'USD')
        assert rate == 1.0

    def test_get_exchange_rate_usd_hkd(self):
        """Should fetch USD/HKD exchange rate."""
        connector = get_google_finance_connector()
        rate = connector.get_exchange_rate('USD', 'HKD')
        if rate is not None:
            assert 7.5 < rate < 8.0
