
from abc import ABC, abstractmethod
import pandas as pd
from datetime import date
from typing import Optional

class BaseScraper(ABC):
    """Abstract base class for market data scrapers."""
    
    @abstractmethod
    def fetch_history(self, asset_id: str, start_date: date, end_date: Optional[date] = None) -> pd.DataFrame:
        """
        Fetch historical market data for an asset.
        
        Args:
            asset_id: The asset identifier (e.g. CN_FUND_110020)
            start_date: Start date for data fetch
            end_date: End date (optional, typically defaults to today)
            
        Returns:
            pd.DataFrame with columns: date, close, currency
        """
        pass
