"""Market assessment API endpoints."""
from fastapi import APIRouter, Depends
from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.financial_analysis.regime import assess_portfolio_regime
from src.api.routes._errors import api_error_response
from src.services.currency import get_today_usd_cny_rate

router = APIRouter(prefix="/market", tags=["Market"])


@router.get("/regime")
async def get_market_regime(db: DatabaseConnector = Depends(get_db)):
    """Get current market regime assessment (Bull/Neutral/Bear + volatility)."""
    try:
        result = assess_portfolio_regime(db)
        if result is None:
            return {
                "trend": "Unknown",
                "volatility_level": "Unknown",
                "error": "Insufficient market data for regime assessment",
            }
        return result
    except Exception as e:
        return api_error_response(e, context="market-regime")


@router.get("/fx-rate")
async def get_fx_rate():
    """Return the latest USD/CNY exchange rate for display-only conversion.

    DISPLAY ONLY — all stored values are in CNY; this rate is used by the
    frontend to convert displayed numbers to USD. No stored values are mutated.
    Conversion formula: usd = cny_value / rate.

    Returns:
        pair:  Always "USD/CNY".
        rate:  Latest USD→CNY float (fallback 7.0 if service unavailable).
        as_of: ISO 8601 timestamp or null (currency service does not expose a timestamp).
    """
    try:
        rate = get_today_usd_cny_rate()
        return {"pair": "USD/CNY", "rate": rate, "as_of": None}
    except Exception as e:
        return api_error_response(e, context="fx-rate")
