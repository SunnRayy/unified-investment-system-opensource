"""Tests for Brinson performance attribution."""


def test_brinson_pure_allocation_effect():
    """When selection returns are identical, only allocation effect matters."""
    from src.financial_analysis.attribution import brinson_attribution

    # Portfolio overweight equities (60% vs 50% benchmark), same returns
    portfolio = [
        {"class": "Equity", "weight": 0.60, "return": 0.10},
        {"class": "Bonds", "weight": 0.40, "return": 0.03},
    ]
    benchmark = [
        {"class": "Equity", "weight": 0.50, "return": 0.10},
        {"class": "Bonds", "weight": 0.50, "return": 0.03},
    ]
    result = brinson_attribution(portfolio, benchmark)
    assert "total_allocation_effect" in result
    assert "total_selection_effect" in result
    # Selection effect should be ~0 since returns are the same
    assert abs(result["total_selection_effect"]) < 0.001
    # Allocation effect should be positive (overweight in higher-return class)
    assert result["total_allocation_effect"] > 0


def test_brinson_pure_selection_effect():
    """When weights are identical, only selection effect matters."""
    from src.financial_analysis.attribution import brinson_attribution

    portfolio = [
        {"class": "Equity", "weight": 0.50, "return": 0.15},  # beat benchmark
        {"class": "Bonds", "weight": 0.50, "return": 0.03},
    ]
    benchmark = [
        {"class": "Equity", "weight": 0.50, "return": 0.10},
        {"class": "Bonds", "weight": 0.50, "return": 0.03},
    ]
    result = brinson_attribution(portfolio, benchmark)
    assert abs(result["total_allocation_effect"]) < 0.001
    assert result["total_selection_effect"] > 0


def test_brinson_class_detail():
    """Each class should have its own allocation/selection/interaction breakdown."""
    from src.financial_analysis.attribution import brinson_attribution

    portfolio = [
        {"class": "Equity", "weight": 0.60, "return": 0.12},
        {"class": "Bonds", "weight": 0.40, "return": 0.04},
    ]
    benchmark = [
        {"class": "Equity", "weight": 0.50, "return": 0.10},
        {"class": "Bonds", "weight": 0.50, "return": 0.03},
    ]
    result = brinson_attribution(portfolio, benchmark)
    assert "classes" in result
    assert len(result["classes"]) == 2
    equity = next(c for c in result["classes"] if c["class"] == "Equity")
    assert "allocation_effect" in equity
    assert "selection_effect" in equity
    assert "interaction_effect" in equity


def test_brinson_effects_sum_to_excess_return():
    """Allocation + Selection + Interaction should equal portfolio return - benchmark return."""
    from src.financial_analysis.attribution import brinson_attribution

    portfolio = [
        {"class": "Equity", "weight": 0.60, "return": 0.12},
        {"class": "Bonds", "weight": 0.30, "return": 0.04},
        {"class": "Cash", "weight": 0.10, "return": 0.01},
    ]
    benchmark = [
        {"class": "Equity", "weight": 0.50, "return": 0.10},
        {"class": "Bonds", "weight": 0.40, "return": 0.03},
        {"class": "Cash", "weight": 0.10, "return": 0.01},
    ]
    result = brinson_attribution(portfolio, benchmark)
    total_effect = (
        result["total_allocation_effect"]
        + result["total_selection_effect"]
        + result["total_interaction_effect"]
    )
    excess = result["portfolio_return"] - result["benchmark_return"]
    assert abs(total_effect - excess) < 0.0001
