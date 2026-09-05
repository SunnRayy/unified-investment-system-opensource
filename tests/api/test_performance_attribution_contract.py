"""Tests for performance attribution API contract."""

import pytest

from src.api.routes import performance as performance_routes


@pytest.mark.asyncio
async def test_attribution_route_returns_percentage_points_and_keeps_weights(monkeypatch):
    """Route should scale decimal returns/effects to percentage points only."""

    def fake_calc(_db, include_asset_ids=None):
        return {
            "portfolio_return": 0.165498,
            "benchmark_return": 0.101234,
            "excess_return": 0.064264,
            "total_allocation_effect": 0.010001,
            "total_selection_effect": 0.020002,
            "total_interaction_effect": 0.034261,
            "classes": [
                {
                    "class": "Cash",
                    "portfolio_weight": 0.0482,
                    "benchmark_weight": 0.02,
                    "portfolio_return": 2.2393,
                    "benchmark_return": 0.0,
                    "allocation_effect": 0.0,
                    "selection_effect": 0.0,
                    "interaction_effect": 0.107871,
                    "total_effect": 0.107871,
                }
            ],
        }

    monkeypatch.setattr(performance_routes, "calculate_portfolio_attribution", fake_calc)

    result = await performance_routes.get_performance_attribution(
        period="all_time",
        include_non_rebalanceable=True,
        db=None,
    )

    assert result["portfolio_return"] == 16.5498
    assert result["benchmark_return"] == 10.1234
    assert result["excess_return"] == 6.4264
    assert result["classes"][0]["total_effect"] == 10.7871
    assert result["classes"][0]["portfolio_weight"] == 0.0482
    assert result["classes"][0]["benchmark_weight"] == 0.02


@pytest.mark.asyncio
async def test_attribution_period_parameter_is_currently_ignored(monkeypatch):
    """Regression: until period-aware attribution is implemented, outputs are identical."""

    def fake_calc(_db, include_asset_ids=None):
        return {
            "portfolio_return": 0.02,
            "benchmark_return": 0.01,
            "excess_return": 0.01,
            "total_allocation_effect": 0.0,
            "total_selection_effect": 0.0,
            "total_interaction_effect": 0.01,
            "classes": [],
        }

    monkeypatch.setattr(performance_routes, "calculate_portfolio_attribution", fake_calc)

    all_time = await performance_routes.get_performance_attribution(
        period="all_time",
        include_non_rebalanceable=True,
        db=None,
    )
    last_12m = await performance_routes.get_performance_attribution(
        period="last_12m",
        include_non_rebalanceable=True,
        db=None,
    )

    assert all_time == last_12m
