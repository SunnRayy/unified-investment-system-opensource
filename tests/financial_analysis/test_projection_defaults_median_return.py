"""Tests for src.financial_analysis.projection_defaults.median_return (R-1,
docs/plans/2026-07-25-forecast-planning-redesign.md).

The pin-against-Monte-Carlo test is the whole point of this workstream: it
locks the deterministic engine (north_star_glide.future_value /
months_to_target), evaluated at median_return(r, sigma), to agree with the
MEDIAN of a real Monte Carlo simulation (run_monte_carlo) for the same
(r, sigma) inputs. If either engine's math drifts, this test must fail.
"""
from __future__ import annotations

import math

import pytest

from src.financial_analysis.projection_defaults import median_return
from src.financial_analysis.monte_carlo import run_monte_carlo
from src.services.north_star_glide import months_to_target


# ── Guard rails ─────────────────────────────────────────────────────────────

def test_sigma_zero_returns_r_exactly():
    assert median_return(0.108, 0.0) == 0.108
    assert median_return(-0.05, 0.0) == -0.05


def test_negative_sigma_returns_r_unchanged():
    """sigma <= 0 is documented as 'no drag to apply' — a defensive floor,
    not an expected input, but must not raise."""
    assert median_return(0.10, -0.01) == 0.10


def test_total_loss_domain_guard_does_not_raise():
    """1 + annual_return <= 0 (a -100%-or-worse arithmetic return) must not
    hit math.log's domain error. Clamped to -1.0 (total loss floor)."""
    assert median_return(-1.0, 0.15) == -1.0
    assert median_return(-1.5, 0.15) == -1.0


# ── Monotonicity ─────────────────────────────────────────────────────────────

def test_higher_sigma_strictly_lower_median_return():
    r = 0.108
    g_low_vol = median_return(r, 0.10)
    g_high_vol = median_return(r, 0.25)
    assert g_high_vol < g_low_vol < r


# ── Known value ──────────────────────────────────────────────────────────────

def test_known_value_matches_lead_verified_figure():
    """Lead-verified live-data figure (2026-07-25): r=0.108, sigma=0.179 ->
    g ~= 0.0904."""
    g = median_return(0.108, 0.179)
    assert g == pytest.approx(0.0904, abs=0.0002)


def test_formula_matches_closed_form_directly():
    r, s = 0.15, 0.22
    expected = math.exp(math.log(1 + r) - s ** 2 / 2.0) - 1.0
    assert median_return(r, s) == pytest.approx(expected, rel=1e-12)


# ── Pin against Monte Carlo — the critical test ──────────────────────────────

def _mc_median_crossing_year(
    p0: float, r: float, sigma: float, monthly_contribution: float,
    target: float, years: int, num_simulations: int, seed: int,
) -> float | None:
    """Year (fractional, linearly interpolated) at which run_monte_carlo's
    p50 (median) series first reaches `target`. None if it never does
    within `years`. Uses the SAME linear-interpolation-within-the-crossing-
    period convention as north_star_glide.months_to_target, so the two
    "years to cross" numbers are apples-to-apples."""
    result = run_monte_carlo(
        initial_value=p0,
        annual_return=r,
        annual_volatility=sigma,
        years=years,
        num_simulations=num_simulations,
        annual_contribution=monthly_contribution * 12.0,
        seed=seed,
        goal_target=target,
    )
    p50 = result["percentiles"]["p50"]
    for y in range(1, years + 1):
        if p50[y] >= target:
            prev_value, cur_value = p50[y - 1], p50[y]
            span = cur_value - prev_value
            frac = (target - prev_value) / span if span > 0 else 0.0
            return (y - 1) + frac
    return None


@pytest.mark.parametrize(
    "p0,r,sigma,monthly_contribution,target,years,seed",
    [
        # Lead-verified live-data case (2026-07-25 session):
        # deterministic @ median 9.04% -> 11.79y; MC median (20k sims) crosses
        # just under 12y. Agreement within ~1.5%.
        (3_269_850.0, 0.108, 0.179, 44_665.0, 20_000_000.0, 12, 42),
        # A second, independent (r, sigma, P0, pm, target) combination so
        # this isn't a single-point coincidence.
        (1_000_000.0, 0.08, 0.15, 10_000.0, 3_000_000.0, 30, 7),
    ],
)
def test_deterministic_at_median_return_agrees_with_monte_carlo_median(
    p0, r, sigma, monthly_contribution, target, years, seed,
):
    """The whole point of R-1: running the EXISTING deterministic engine at
    median_return(r, sigma) must reproduce the Monte Carlo median crossing
    year, within 5%. Uses >=20000 simulations + a fixed seed for stability.
    This test must fail if either engine's formula drifts."""
    g = median_return(r, sigma)

    det_months = months_to_target(p0, monthly_contribution, g, target)
    assert det_months is not None, "deterministic engine reports target unreachable — check test inputs"
    det_years = det_months / 12.0

    mc_years = _mc_median_crossing_year(
        p0, r, sigma, monthly_contribution, target, years,
        num_simulations=20_000, seed=seed,
    )
    assert mc_years is not None, "Monte Carlo median never reaches target within the horizon — check test inputs"

    rel_diff = abs(det_years - mc_years) / mc_years
    assert rel_diff <= 0.05, (
        f"deterministic-at-median ({det_years:.3f}y) vs Monte Carlo median "
        f"({mc_years:.3f}y) diverged by {rel_diff:.2%} (must be <=5%) for "
        f"r={r}, sigma={sigma}, P0={p0}, pm={monthly_contribution}, target={target}"
    )
