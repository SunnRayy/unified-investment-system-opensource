"""Tests for Monte Carlo simulation engine."""
import pytest
from unittest.mock import MagicMock


def test_monte_carlo_basic():
    """Simulate 100 paths over 10 years, verify shape and percentiles."""
    from src.financial_analysis.monte_carlo import run_monte_carlo

    result = run_monte_carlo(
        initial_value=1000000.0,
        annual_return=0.07,
        annual_volatility=0.15,
        years=10,
        num_simulations=100,
        annual_contribution=0.0,
        seed=42,
    )

    assert result["years"] == list(range(0, 11))
    assert len(result["percentiles"]["p10"]) == 11
    assert len(result["percentiles"]["p50"]) == 11
    assert len(result["percentiles"]["p90"]) == 11
    # Year 0 should be initial value for all percentiles
    assert result["percentiles"]["p10"][0] == 1000000.0
    assert result["percentiles"]["p50"][0] == 1000000.0
    assert result["percentiles"]["p90"][0] == 1000000.0
    # p90 should be > p50 > p10 at year 10
    assert result["percentiles"]["p90"][10] > result["percentiles"]["p50"][10]
    assert result["percentiles"]["p50"][10] > result["percentiles"]["p10"][10]


def test_monte_carlo_with_contributions():
    """Annual contributions should increase median outcome."""
    from src.financial_analysis.monte_carlo import run_monte_carlo

    no_contrib = run_monte_carlo(
        initial_value=1000000.0, annual_return=0.07, annual_volatility=0.15,
        years=10, num_simulations=500, annual_contribution=0.0, seed=42,
    )
    with_contrib = run_monte_carlo(
        initial_value=1000000.0, annual_return=0.07, annual_volatility=0.15,
        years=10, num_simulations=500, annual_contribution=100000.0, seed=42,
    )

    assert with_contrib["percentiles"]["p50"][10] > no_contrib["percentiles"]["p50"][10]


def test_monte_carlo_deterministic_with_seed():
    """Same seed produces same results."""
    from src.financial_analysis.monte_carlo import run_monte_carlo

    r1 = run_monte_carlo(1000000, 0.07, 0.15, 10, 100, seed=123)
    r2 = run_monte_carlo(1000000, 0.07, 0.15, 10, 100, seed=123)

    assert r1["percentiles"]["p50"] == r2["percentiles"]["p50"]


def test_monte_carlo_goal_probability():
    """Goal probability = fraction of simulations reaching target."""
    from src.financial_analysis.monte_carlo import run_monte_carlo

    result = run_monte_carlo(
        initial_value=1000000.0, annual_return=0.07, annual_volatility=0.15,
        years=10, num_simulations=1000, seed=42,
        goal_target=1500000.0,
    )

    assert "goal_probability" in result
    assert 0.0 <= result["goal_probability"] <= 1.0
    # With 7% return and 15% vol over 10 years from 1M, reaching 1.5M is very likely
    assert result["goal_probability"] > 0.5


def test_monte_carlo_zero_vol_zero_contribution_matches_closed_form():
    """Zero volatility + zero contribution must match P0*(1+r)**years exactly
    (within floating point tolerance) — pins the monthly drift calibration so
    the EXPECTED annual growth factor still equals (1 + annual_return)."""
    from src.financial_analysis.monte_carlo import run_monte_carlo

    p0 = 3_269_850.0
    r = 0.108
    years = 10

    result = run_monte_carlo(
        initial_value=p0, annual_return=r, annual_volatility=0.0,
        years=years, num_simulations=5, annual_contribution=0.0, seed=1,
    )

    expected = p0 * (1 + r) ** years
    actual = result["percentiles"]["p50"][years]
    assert actual == pytest.approx(expected, rel=1e-6)
    # All simulations are identical (zero vol) so mean == median == expected
    assert result["final_value_stats"]["mean"] == pytest.approx(expected, rel=1e-6)


def test_monte_carlo_zero_vol_with_contribution_matches_monthly_annuity_closed_form():
    """Zero volatility WITH contribution must match the monthly-compounding
    ordinary-annuity closed form implied by the glide path:
    P0*(1+rm)**N + PMT*(((1+rm)**N - 1)/rm), rm=(1+r)**(1/12)-1, N=years*12,
    PMT=annual_contribution/12. This is the convention fix under test —
    contributions must earn a partial year's return, not zero."""
    from src.financial_analysis.monte_carlo import run_monte_carlo

    p0 = 3_269_850.0
    r = 0.108
    years = 10
    annual_contribution = 44_665.0 * 12

    result = run_monte_carlo(
        initial_value=p0, annual_return=r, annual_volatility=0.0,
        years=years, num_simulations=5, annual_contribution=annual_contribution, seed=1,
    )

    rm = (1 + r) ** (1.0 / 12.0) - 1.0
    n = years * 12
    pmt = annual_contribution / 12.0
    expected = p0 * (1 + rm) ** n + pmt * (((1 + rm) ** n - 1) / rm)

    actual = result["percentiles"]["p50"][years]
    assert actual == pytest.approx(expected, rel=1e-3)  # within 0.1%


def test_monte_carlo_matches_glide_path_closed_form_cross_engine():
    """Cross-engine consistency: with zero volatility, the MC final value at
    N years must agree with north_star_glide's own deterministic projection
    (_future_value) for the same inputs, within 0.5% — this is the actual
    bug the owner reported (MC 10.82y vs glide 10.63y to 20M at the same
    inputs). Calls the glide module's real code path rather than
    re-deriving the formula independently."""
    from src.financial_analysis.monte_carlo import run_monte_carlo
    from src.services.north_star_glide import future_value

    p0 = 3_269_850.0
    r = 0.108
    years = 10
    monthly_contribution = 44_665.0
    annual_contribution = monthly_contribution * 12

    mc_result = run_monte_carlo(
        initial_value=p0, annual_return=r, annual_volatility=0.0,
        years=years, num_simulations=5, annual_contribution=annual_contribution, seed=1,
    )
    mc_final = mc_result["percentiles"]["p50"][years]

    glide_final = future_value(p0, monthly_contribution, r, years * 12)

    assert mc_final == pytest.approx(glide_final, rel=0.005)


def test_monte_carlo_return_shape_is_annual():
    """Public contract: years is 0..years and every percentile list has
    length years+1, regardless of the monthly internal step count."""
    from src.financial_analysis.monte_carlo import run_monte_carlo

    years = 7
    result = run_monte_carlo(
        initial_value=1_000_000.0, annual_return=0.09, annual_volatility=0.12,
        years=years, num_simulations=50, annual_contribution=60_000.0, seed=7,
    )

    assert result["years"] == list(range(0, years + 1))
    for label in ("p10", "p25", "p50", "p75", "p90"):
        assert len(result["percentiles"][label]) == years + 1


def test_monte_carlo_portfolio_from_db():
    """calculate_portfolio_projection reads current portfolio value from DB."""
    from src.financial_analysis.monte_carlo import calculate_portfolio_projection

    db = MagicMock()
    # Mock: net worth query
    db.execute.return_value.fetchone.return_value = (5000000.0,)

    result = calculate_portfolio_projection(
        db, years=5, num_simulations=100, seed=42
    )

    assert result["percentiles"]["p50"][0] == 5000000.0
    assert len(result["years"]) == 6  # 0 through 5
