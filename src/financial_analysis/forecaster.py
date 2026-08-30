"""Cash flow forecasting using Holt-Winters and linear regression.

Predicts future income and expense trends based on historical monthly data.
"""
import logging
from typing import Any, Dict, List

import numpy as np

# Graceful import for statsmodels
try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False

from src.financial_analysis.cash_flow import parse_monthly_cash_flows

logger = logging.getLogger(__name__)


def linear_forecast(values: List[float], periods: int) -> List[float]:
    """Forecast using simple linear regression."""
    if not values:
        return [0.0] * periods
    if len(values) < 2:
        return [values[-1]] * periods

    try:
        x = np.arange(len(values))
        y = np.array(values)
        slope, intercept = np.polyfit(x, y, 1)
        
        forecast = []
        for i in range(1, periods + 1):
            val = slope * (len(values) + i - 1) + intercept
            forecast.append(max(0.0, float(val)))  # specific: clamp to 0
        return forecast
    except Exception as e:
        logger.error(f"Linear regression failed: {e}")
        return [np.mean(values)] * periods


def forecast_series(
    history: List[float],
    months: int = 6,
    seasonal: bool = True,
    seasonal_periods: int = 12,
) -> Dict[str, Any]:
    """Forecast a single series using best available method.
    
    Returns:
        Dict with keys: 'method', 'forecast' (List[float])
    """
    if not history:
        return {"method": "none", "forecast": [0.0] * months}

    # Try Holt-Winters if safe and requested
    use_hw = seasonal and HAS_STATSMODELS and len(history) >= (2 * seasonal_periods)
    
    if use_hw:
        try:
            model = ExponentialSmoothing(
                history,
                trend="add",
                seasonal="add",
                seasonal_periods=seasonal_periods,
                initialization_method="estimated",
            ).fit()
            forecast = model.forecast(months)
            return {
                "method": "holt_winters", 
                "forecast": [max(0.0, float(v)) for v in forecast]
            }
        except Exception as e:
            logger.warning(f"Holt-Winters failed, falling back to linear: {e}")
            # Fall through to linear

    # Fallback to linear
    return {
        "method": "linear",
        "forecast": linear_forecast(history, months)
    }


def get_cash_flow_forecast(
    db: Any,
    months: int = 6,
) -> Dict[str, Any]:
    """Fetch history and forecast income/expenses.

    Args:
        db: DatabaseConnector or mock
        months: Forecast horizon (default 6 per plan)

    Returns:
        Dict with income_forecast, expense_forecast, net_forecast, methods used.
    """
    try:
        rows = db.execute("""
            SELECT record_key, transaction_date, payload
            FROM income_expense_monthly
            ORDER BY transaction_date ASC
        """).fetchall()

        monthly = parse_monthly_cash_flows(rows)

        incomes = [m["total_income"] for m in monthly]
        expenses = [m["total_expense"] for m in monthly]

        inc_res = forecast_series(incomes, months=months)
        exp_res = forecast_series(expenses, months=months)
        
        inc_forecast = inc_res["forecast"]
        exp_forecast = exp_res["forecast"]
        net_forecast = [i - e for i, e in zip(inc_forecast, exp_forecast)]

        return {
            "income_forecast": [round(v, 2) for v in inc_forecast],
            "expense_forecast": [round(v, 2) for v in exp_forecast],
            "net_forecast": [round(v, 2) for v in net_forecast],
            "months": months,
            "historical_months": len(monthly),
            "methods": {
                "income": inc_res["method"],
                "expense": exp_res["method"]
            }
        }

    except Exception as e:
        logger.error(f"Error forecasting cash flows: {e}")
        return {
            "income_forecast": [],
            "expense_forecast": [],
            "net_forecast": [],
            "months": months,
            "error": str(e),
        }
