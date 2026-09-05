
"""Google Finance connector (via Yahoo Finance).

Note: Actual Google Finance API is deprecated/unreliable, so we use yfinance
as the backend implementation while keeping the interface consistent.
"""
import logging
import yfinance as yf
from typing import Optional

logger = logging.getLogger(__name__)

class GoogleFinanceConnector:
    def get_exchange_rate(self, from_currency: str, to_currency: str) -> Optional[float]:
        """
        Get live exchange rate.
        
        Args:
            from_currency: Source currency code (e.g. 'USD')
            to_currency: Target currency code (e.g. 'CNY')
            
        Returns:
            Exchange rate or None if failed
        """
        if from_currency == to_currency:
            return 1.0
        
        # yfinance typically uses tickers like "USDCNY=X"
        symbol = f"{from_currency}{to_currency}=X"
        try:
            ticker = yf.Ticker(symbol)
            # Fetch latest data
            hist = ticker.history(period="1d")
            if not hist.empty:
                return float(hist['Close'].iloc[-1])
            else:
                logger.warning(f"No history found for {symbol}")
                return None
        except Exception as e:
            logger.warning(f"Failed to fetch rate for {symbol}: {e}")
            return None

_connector = GoogleFinanceConnector()

def get_google_finance_connector() -> GoogleFinanceConnector:
    return _connector
