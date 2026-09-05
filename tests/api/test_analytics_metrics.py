"""Tests for analytics API endpoints."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import date


@pytest.mark.asyncio
async def test_risk_metrics_endpoint():
    """GET /performance/risk-metrics returns historical risk metrics."""
    from src.api.routes.performance import get_risk_metrics

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = [
        (date(2025, 1, 1), 1000000.0),
        (date(2025, 2, 1), 1050000.0),
        (date(2025, 3, 1), 1020000.0),
        (date(2025, 4, 1), 1080000.0),
    ]

    with patch("src.api.routes.performance.calculate_portfolio_metrics") as mock_calc:
        mock_calc.return_value = {
            "max_drawdown": 2.86,
            "sharpe_ratio": 1.05,
            "sortino_ratio": 1.42,
            "calmar_ratio": 0.85,
            "volatility_annual": 8.5,
            "total_return": 8.0,
            "data_points": 4,
        }
        result = await get_risk_metrics(period='all_time', db=mock_db)

    assert "max_drawdown" in result
    assert "sharpe_ratio" in result
    assert result["data_points"] == 4


@pytest.mark.asyncio
async def test_projection_endpoint():
    """GET /api/analytics/projection returns Monte Carlo result."""
    from src.api.routes.analytics import get_projection

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = (5000000.0,)

    result = await get_projection(
        years=5, simulations=100, annual_return=0.07,
        annual_volatility=0.15, annual_contribution=0.0,
        goal_target=None, include_non_rebalanceable=False, seed=42, db=mock_db,
    )

    assert "percentiles" in result
    assert "p50" in result["percentiles"]
    assert len(result["years"]) == 6


@pytest.mark.asyncio
async def test_cashflow_trends_endpoint():
    """GET /api/analytics/cashflow-trends returns monthly cash flow + trends."""
    from src.api.routes.analytics import get_cashflow_trends

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = [
        ("income_salary", date(2025, 1, 1), '{"category": "Salary", "amount": 25000, "type": "income"}'),
        ("expense_rent", date(2025, 1, 1), '{"category": "Rent", "amount": 8000, "type": "expense"}'),
    ]

    result = await get_cashflow_trends(db=mock_db)
    assert "monthly" in result
    assert "trends" in result


@pytest.mark.asyncio
async def test_cashflow_forecast_endpoint():
    """GET /api/analytics/cashflow-forecast returns future cash flow."""
    from src.api.routes.analytics import get_cashflow_forecast

    mock_db = MagicMock()
    # Mock some data for forecasting
    mock_db.execute.return_value.fetchall.return_value = [
        ("income_salary", date(2025, 1, 1), '{"amount": 1000, "type": "income"}'),
        ("income_salary", date(2025, 2, 1), '{"amount": 1000, "type": "income"}'),
    ]

    result = await get_cashflow_forecast(months=3, db=mock_db)
    assert "income_forecast" in result
    assert "expense_forecast" in result
    assert len(result["income_forecast"]) == 3


@pytest.mark.asyncio
async def test_goal_crud_endpoints():
    """Verify Goal CRUD: GET list and POST create."""
    from src.api.routes.analytics import get_goals, create_new_goal

    mock_db = MagicMock()
    # Mock list goals
    mock_db.execute.return_value.fetchall.return_value = []
    
    # Mock create goal
    # Test GET list
    goals = await get_goals(db=mock_db)
    assert isinstance(goals, list)

    # Mock insert returning ID and created_at
    from datetime import datetime as dt
    # The new SQL returns (id, created_at)
    mock_db.execute.return_value.fetchone.return_value = (1, dt.now())

    # Test POST create
    from src.api.routes.analytics import GoalCreate
    new_goal_data = {
        "name": "Test Goal",
        "target_amount": 10000.0,
        "target_date": "2030-01-01",
    }
    new_goal = GoalCreate(**new_goal_data)
    created = await create_new_goal(goal=new_goal, db=mock_db)
    assert created.id == 1
    assert created.name == "Test Goal"
