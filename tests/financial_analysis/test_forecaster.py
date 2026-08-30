"""Tests for cash flow forecasting."""
import pytest
from src.financial_analysis.forecaster import (
    linear_forecast
)

def test_linear_forecast_basic():
    """Test basic linear progression."""
    history = [100.0, 110.0, 120.0, 130.0]
    # Slope = 10, next 3 should be 140, 150, 160
    forecast = linear_forecast(history, periods=3)
    
    assert len(forecast) == 3
    assert forecast[0] == pytest.approx(140.0, abs=0.1)
    assert forecast[1] == pytest.approx(150.0, abs=0.1)
    assert forecast[2] == pytest.approx(160.0, abs=0.1)
