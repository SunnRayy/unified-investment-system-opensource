
"""Currency Converter Service — unified FX resolution chain.

Resolution chain for the LATEST rate (today / None):
  0. yfinance fast_info  (USDCNY=X, HKDCNY=X) — fast, no calendar lookup
  1. Google Finance via GoogleFinanceConnector
  2. Hard-coded fallback rates in self.fallback_rates / config

For HISTORICAL dates (past) steps 0 is skipped; Google Finance is tried first,
then config fallback (unchanged behaviour).
"""

import logging
from datetime import date, datetime
from typing import Optional, Tuple, Dict, Any
import pandas as pd

logger = logging.getLogger(__name__)


def get_default_usd_cny_rate() -> float:
    """Single source of truth for the USD→CNY fallback rate (Program OSR
    WS-2 step 3). Previously six independent hardcoded ``7.0`` literals
    scattered across src/sources/hooks/{rsu,schwab,ibkr}.py and
    yfinance_fetcher.py's own local fallback dict — this is the one place
    a self-hoster changes to alter the default.

    Reads config/settings.yaml's ``currency.fallback_rates.USD_CNY`` — the
    same key CurrencyConverterService.__init__ already reads to build
    self.fallback_rates. Falls back to the historical constant 7.0 if
    config loading fails for any reason (missing file, bad YAML, no
    'currency' section), so a broken or absent config can never break FX
    conversion — this function must always return a usable float.
    """
    try:
        from src.config import load_config
        config = load_config()
        raw = (config or {}).get("currency", {}).get("fallback_rates", {}).get("USD_CNY")
        if raw is not None:
            return float(raw)
    except Exception as e:
        logger.warning(
            "get_default_usd_cny_rate: config unavailable, using historical "
            "fallback 7.0: %s", e,
        )
    return 7.0


class CurrencyConverterService:
    def __init__(self, enable_google_finance: bool = True, config: Dict[str, Any] = None):
        self.enable_google_finance = enable_google_finance
        self.fallback_rates = {
            ('USD', 'CNY'): 7.0,
            ('CNY', 'USD'): 1.0 / 7.0,
            ('HKD', 'CNY'): 0.90,
            ('CNY', 'HKD'): 1.0 / 0.90,
            ('USD', 'USD'): 1.0,
            ('CNY', 'CNY'): 1.0,
            ('HKD', 'HKD'): 1.0,
        }
        
        if config:
            fallback_config = config.get('currency', {}).get('fallback_rates', {})
            for key, rate in fallback_config.items():
                if '_' in key:
                    try:
                        from_curr, to_curr = key.split('_')
                        self.fallback_rates[(from_curr, to_curr)] = float(rate)
                        # Optional: Add inverse? No, manual config overrides specific pair.
                    except ValueError:
                        pass
        
        self.cache: Dict[Tuple[str, str, date], float] = {}

    def _is_today(self, rate_date_obj: date) -> bool:
        """Return True if rate_date_obj is today (or in the future)."""
        return rate_date_obj >= datetime.now().date()

    def get_historical_rate(
        self,
        from_currency: str,
        to_currency: str,
        rate_date: pd.Timestamp,
    ) -> Optional[float]:
        """Get exchange rate for a specific date.

        For today / latest (rate_date >= today):
          0. yfinance fast_info — fastest, same source as fetch_fx_rates()
          1. Google Finance connector
          2. Config / hard-coded fallback_rates

        For past dates (rate_date < today):
          Skips step 0; uses Google Finance then fallback (unchanged behaviour).
        """
        if from_currency == to_currency:
            return 1.0

        # Normalise to date object
        if isinstance(rate_date, pd.Timestamp):
            rate_date_obj = rate_date.date()
        else:
            rate_date_obj = rate_date  # type: ignore[assignment]

        # Check cache
        cache_key = (from_currency, to_currency, rate_date_obj)
        if cache_key in self.cache:
            return self.cache[cache_key]

        rate = None

        # Step 0: yfinance fast_info — only for "latest" (today or future timestamp)
        if self._is_today(rate_date_obj) and self.enable_google_finance:
            try:
                # Lazy import avoids any potential circular-import risk at module load time
                from src.market_data.fetchers.yfinance_fetcher import fetch_fx_rates
                _yf_rates = fetch_fx_rates()
                # fetch_fx_rates already applies its own Google + fallback chain,
                # so a valid result here means yfinance (or Google) succeeded.
                pair_key = from_currency if to_currency == "CNY" else None
                if pair_key and pair_key in _yf_rates:
                    candidate = float(_yf_rates[pair_key])
                    # Only trust if it differs from the hard-coded fallback
                    # (i.e. a live value was actually fetched)
                    default_val = self.fallback_rates.get((from_currency, to_currency))
                    if candidate != default_val or default_val is None:
                        rate = candidate
                        logger.debug(
                            "CurrencyConverter: %s->%s = %.4f via yfinance chain",
                            from_currency, to_currency, rate,
                        )
            except Exception as e:
                logger.warning("CurrencyConverter: yfinance fast_info step failed: %s", e)

        # Step 1: Google Finance (for past dates, or if yfinance step 0 found nothing)
        if rate is None and self.enable_google_finance:
            try:
                from .connectors.google_finance_connector import get_google_finance_connector
                connector = get_google_finance_connector()
                gf_rate = connector.get_exchange_rate(from_currency, to_currency)
                if gf_rate:
                    rate = gf_rate
                    logger.debug(
                        "CurrencyConverter: %s->%s = %.4f via Google Finance",
                        from_currency, to_currency, rate,
                    )
            except Exception as e:
                logger.warning("CurrencyConverter: Google Finance step failed: %s", e)

        # Step 2: config / hard-coded fallback
        if rate is None:
            rate = self.fallback_rates.get((from_currency, to_currency))
            if rate is None:
                inverse = self.fallback_rates.get((to_currency, from_currency))
                if inverse:
                    rate = 1.0 / inverse
            if rate is not None:
                logger.debug(
                    "CurrencyConverter: %s->%s = %.4f via fallback",
                    from_currency, to_currency, rate,
                )

        if rate is not None:
            self.cache[cache_key] = rate

        return rate

    def get_latest_rate(self, from_currency: str, to_currency: str) -> Optional[float]:
        """Get latest available exchange rate."""
        return self.get_historical_rate(from_currency, to_currency, pd.Timestamp.now())

    def convert_amount(
        self, 
        amount: float, 
        from_currency: str, 
        to_currency: str, 
        rate_date: pd.Timestamp
    ) -> Optional[float]:
        """Convert amount between currencies."""
        if from_currency == to_currency:
            return amount
            
        rate = self.get_historical_rate(from_currency, to_currency, rate_date)
        if rate is None:
            logger.warning(f"No rate found for {from_currency}->{to_currency} on {rate_date}")
            return None
            
        return amount * rate

# Global instance
_service = None

def get_currency_service() -> CurrencyConverterService:
    global _service
    if _service is None:
        try:
            from src.config import load_config
            config = load_config()
        except Exception as e:
            logger.warning(f"Could not load config for currency service: {e}")
            config = None
            
        _service = CurrencyConverterService(config=config)
    return _service

def convert_amount(
    amount: float, 
    from_currency: str, 
    to_currency: str, 
    rate_date: pd.Timestamp
) -> Optional[float]:
    """Convenience function for conversion."""
    return _service.convert_amount(amount, from_currency, to_currency, rate_date)
