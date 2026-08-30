
import pytest
from unittest.mock import MagicMock
from datetime import date
import pandas as pd
from src.market_data.service import MarketDataService
from src.market_data.scrapers.cn_fund_scraper import CNFundMarketDataScraper

class TestCNFundIntegration:
    """Integration style tests for MarketDataService with CN Fund scraper."""

    @pytest.fixture
    def service(self):
        return MarketDataService()

    def test_service_routes_to_cn_fund_scraper(self, service):
        """Service should route CN_FUND_* requests to CNFundMarketDataScraper."""
        # Setup mock scraper
        mock_scraper = MagicMock(spec=CNFundMarketDataScraper)
        mock_df = pd.DataFrame([
            {"date": date(2026, 1, 29), "close": 2.762, "currency": "CNY"}
        ])
        mock_scraper.fetch_history.return_value = mock_df
        
        # Override the registered scraper for testing isolation
        # Assuming service has a way to register or we patch the internal registry
        service.register_scraper("CN_FUND_", mock_scraper)
        
        result = service.get_market_data("CN_FUND_900001", start_date=date(2026, 1, 1))
        
        mock_scraper.fetch_history.assert_called_once()
        args, _ = mock_scraper.fetch_history.call_args
        assert args[0] == "CN_FUND_900001"
        assert args[1] == date(2026, 1, 1)
        
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1
        assert result.iloc[0]["close"] == 2.762

    def test_service_initializes_with_cn_fund_scraper(self):
        """Service should have CNFundMarketDataScraper registered by default."""
        service = MarketDataService()
        # access internal registry to verify
        # assuming service._scrapers is a list or dict
        # or verify get_scraper method
        scraper = service.get_scraper("CN_FUND_900001")
        assert isinstance(scraper, CNFundMarketDataScraper)

    def test_service_handles_scraper_error(self, service):
        """Service should propagate or handle scraper errors nicely."""
        mock_scraper = MagicMock()
        mock_scraper.fetch_history.side_effect = ValueError("API Error")
        service.register_scraper("CN_FUND_", mock_scraper)
        
        with pytest.raises(ValueError, match="API Error"):
            service.get_market_data("CN_FUND_ERROR", start_date=date(2026, 1, 1))

    def test_service_returns_empty_on_no_match(self, service):
        """If no scraper matches, should maybe return empty or raise."""
        # Assuming raising ValueError for unknown asset type is safer
        with pytest.raises(ValueError, match="No scraper found"):
            service.get_market_data("UNKNOWN_ASSET_123", start_date=date(2026, 1, 1))
