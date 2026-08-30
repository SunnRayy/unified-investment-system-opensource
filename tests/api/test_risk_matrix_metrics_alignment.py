"""Regression tests for Risk Matrix metrics alignment with Performance metrics."""

import math
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_risk_metrics_uses_historical_vol_sharpe_and_model_beta_div_score():
    """Risk Matrix should map historical volatility/sharpe while keeping model beta/div score."""
    from src.api.routes.data import get_risk_metrics

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = [
        ("CN Equity", 1000.0, "CN_STOCK_000001"),
    ]

    hist = {
        "max_drawdown": 5.0,
        "sharpe_ratio": 1.63,
        "sortino_ratio": 2.0,
        "calmar_ratio": 1.2,
        "volatility_annual": 17.32,
        "total_return": 10.0,
        "data_points": 24,
    }
    model = {
        "volatility": 12.14,
        "volatility_status": "MED",
        "sharpe": 0.46,
        "sharpe_status": "POOR",
        "var_95": 1.25,
        "var_95_status": "LOW",
        "beta": 1.05,
        "div_score": 10,
    }

    with patch("src.api.routes.data.calculate_portfolio_metrics", return_value=hist, create=True), patch(
        "src.api.routes.data.calculate_portfolio_risk", return_value=model
    ):
        result = await get_risk_metrics(include_non_rebalanceable=True, db=mock_db)

    expected_var_95 = 1.65 * (hist["volatility_annual"] / 100.0) / math.sqrt(252) * 100.0
    assert result["volatility"] == hist["volatility_annual"]
    assert result["sharpe"] == hist["sharpe_ratio"]
    assert result["var_95"] == round(expected_var_95, 2)
    assert result["volatility_status"] == "MED"
    assert result["sharpe_status"] == "EXCELLENT"
    assert result["var_95_status"] == "MED"
    assert result["beta"] == model["beta"]
    assert result["div_score"] == model["div_score"]


@pytest.mark.asyncio
async def test_risk_metrics_falls_back_to_model_when_historical_missing():
    """Risk Matrix should fall back to model metrics when historical metrics are unavailable."""
    from src.api.routes.data import get_risk_metrics

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = [
        ("CN Equity", 1000.0, "CN_STOCK_000001"),
    ]

    model = {
        "volatility": 11.11,
        "volatility_status": "MED",
        "sharpe": 0.55,
        "sharpe_status": "AVG",
        "var_95": 1.2,
        "var_95_status": "LOW",
        "beta": 0.97,
        "div_score": 8,
    }
    hist_missing = {
        "max_drawdown": None,
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "calmar_ratio": None,
        "volatility_annual": None,
        "total_return": None,
        "data_points": 0,
    }

    with patch("src.api.routes.data.calculate_portfolio_metrics", return_value=hist_missing, create=True), patch(
        "src.api.routes.data.calculate_portfolio_risk", return_value=model
    ):
        result = await get_risk_metrics(include_non_rebalanceable=True, db=mock_db)

    assert result == model
