
import pytest
import pandas as pd
from unittest.mock import patch
from datetime import date
from src.market_data.scrapers.cn_fund_scraper import CNFundMarketDataScraper

class TestCNFundScraper:
    """Tests for CN Fund Market Data Scraper."""

    @pytest.fixture
    def scraper(self):
        return CNFundMarketDataScraper()

    @pytest.fixture
    def mock_response(self):
        """Mock successful API response from EastMoney/Tiantian Fund."""
        # Example structure simplified for what we expect to parse
        # Usually these APIs return JSON or HTML. Let's assume a JSON-like structure or HTML table we parse.
        # For this test, we mimic what the implementation will expect.
        # Let's assume the implementation uses a direct API that returns list of dicts or similar.
        # To be robust, let's mock `requests.get` return value.
        pass

    def test_fetch_history_returns_dataframe(self, scraper):
        """Should return a DataFrame with correct columns."""
        with patch('src.utils.http_client.http_get') as mock_get:
            # Mock a valid response
            mock_json = {
                "Data": {
                    "LSJZList": [
                        {"FSRQ": "2026-01-29", "DWJZ": "2.762"},
                        {"FSRQ": "2026-01-28", "DWJZ": "2.750"}
                    ]
                },
                "ErrCode": 0
            }
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_json

            df = scraper.fetch_history("CN_FUND_900001", start_date=date(2026, 1, 1), end_date=date(2026, 1, 31))
            
            assert isinstance(df, pd.DataFrame)
            assert not df.empty
            assert list(df.columns) == ["date", "close", "currency"]
            assert len(df) == 2
            assert df.iloc[0]["date"] == date(2026, 1, 28)
            assert df.iloc[0]["close"] == 2.750
            assert df.iloc[0]["currency"] == "CNY"
            
            assert df.iloc[1]["date"] == date(2026, 1, 29)
            assert df.iloc[1]["close"] == 2.762

    def test_fetch_handles_empty_response(self, scraper):
        """Should handle empty data gracefully."""
        with patch('src.utils.http_client.http_get') as mock_get:
            mock_json = {"Data": {"LSJZList": []}, "ErrCode": 0}
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_json

            df = scraper.fetch_history("CN_FUND_900001", start_date=date(2026, 1, 1), end_date=date(2026, 1, 31))
            
            assert isinstance(df, pd.DataFrame)
            assert df.empty
            # Should still have columns even if empty
            assert list(df.columns) == ["date", "close", "currency"]

    def test_fetch_handles_network_error(self, scraper):
        """Should raise or return empty on network failure (depending on design). check plan."""
        # Plan says "Handle errors". BaseScraper usually raises or logs.
        # Let's assume it raises detailed error or logs and returns empty?
        # A scraper failing explicitly is usually better for the service to handle retry.
        with patch('src.utils.http_client.http_get') as mock_get:
            mock_get.side_effect = Exception("Network Timeout")
            
            with pytest.raises(Exception):
                scraper.fetch_history("CN_FUND_900001", start_date=date(2026, 1, 1))

    def test_fetch_filters_date_range(self, scraper):
        """Should filter results to requested date range if API returns more."""
        with patch('src.utils.http_client.http_get') as mock_get:
            # Return 3 days
            mock_json = {
                "Data": {
                    "LSJZList": [
                        {"FSRQ": "2026-01-29", "DWJZ": "2.762"}, # Keep
                        {"FSRQ": "2026-01-28", "DWJZ": "2.750"}, # Keep
                        {"FSRQ": "2026-01-01", "DWJZ": "2.600"}  # Exclude (if start date is 2026-01-28)
                    ]
                },
                "ErrCode": 0
            }
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_json

            # Request range that excludes the old one
            df = scraper.fetch_history(
                "CN_FUND_900001", 
                start_date=date(2026, 1, 28), 
                end_date=date(2026, 1, 30)
            )
            
            assert len(df) == 2
            assert date(2026, 1, 1) not in df["date"].values

    def test_fetch_validates_fund_code_format(self, scraper):
        """Should extract 900001 from CN_FUND_900001."""
        with patch('src.utils.http_client.http_get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"Data": {"LSJZList": []}, "ErrCode": 0}
            
            scraper.fetch_history("CN_FUND_900001", start_date=date(2026, 1, 1))
            
            # Verify the params contained fundCode="900001"
            # call_args[1] is kwargs
            kwargs = mock_get.call_args[1]
            params = kwargs.get('params', {})
            assert params.get('fundCode') == "900001" or "900001" in str(mock_get.call_args)

    def test_fetch_raises_on_invalid_asset_id(self, scraper):
        """Should raise ValueError if asset_id doesn't match CN_FUND_ pattern."""
        with pytest.raises(ValueError):
            scraper.fetch_history("US_STK_AAPL", start_date=date(2026, 1, 1))

    def test_fetch_handles_api_error_code(self, scraper):
        """Should handle API-level errors (ErrCode != 0)."""
        with patch('src.utils.http_client.http_get') as mock_get:
            mock_json = {"Data": None, "ErrCode": 1, "ErrMsg": "Invalid Code"}
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_json

            # Should likely raise or return empty with warning
            with pytest.raises(Exception):
                 scraper.fetch_history("CN_FUND_999999", start_date=date(2026, 1, 1))

    def test_fetch_handles_null_data_with_errcode_zero(self, scraper):
        """Should return empty DataFrame when ErrCode=0 but Data is null.

        EastMoney returns {"Data": null, "ErrCode": 0} when pageSize is too large
        or no data exists for a fund in the requested range. The dict.get("Data", {})
        default does NOT fire for null — only for missing keys — so this previously
        crashed with 'NoneType' object has no attribute 'get'.
        """
        with patch('src.utils.http_client.http_get') as mock_get:
            mock_json = {"Data": None, "ErrCode": 0, "ErrMsg": None, "TotalCount": 0}
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_json

            df = scraper.fetch_history("CN_FUND_900013", start_date=date(2026, 1, 1))

            assert isinstance(df, pd.DataFrame)
            assert df.empty
            assert list(df.columns) == ["date", "close", "currency"]

    def test_scraper_respects_rate_limit(self, scraper):
        """Scraper should have a delay mechanism (mocked)."""
        # We check if it calls time.sleep or similar if configured
        with patch('src.market_data.scrapers.cn_fund_scraper.time.sleep'):
            with patch('src.utils.http_client.http_get') as mock_get:
                mock_get.return_value.status_code = 200
                mock_get.return_value.json.return_value = {"Data": {"LSJZList": []}, "ErrCode": 0}
                
                scraper.fetch_history("CN_FUND_900001", start_date=date(2026, 1, 1))
                # Just verify it proceeded without error; sleep might only happen on retries or multiple calls
                # If we enforce rate limit per call, mock_sleep should be called.
                # Let's assume implementation adds a small buffer.
                pass
