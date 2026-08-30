"""Tests for src.financial_analysis.projection_defaults.crossing_time_percentiles
(W-3, docs/plans/2026-07-26-your-path-design-implementation.md §4.4).

DEFINITIONAL TRAP (see the function's own docstring): crossing_time_percentiles
answers "at what time t is there a p% probability the portfolio's VALUE AT
THAT TIME is >= target" — NOT first-passage time (the first time the
portfolio ever touches the target, which for a growing process is always
earlier). The Monte Carlo pinning test below MUST compute the empirical
comparison the same "value at t" way: for each year, the fraction of
run_monte_carlo's individual simulated paths with value_t >= target, then
invert that fraction curve to get the percentile years. Pinning against
run_monte_carlo's own first-passage crossing of its percentile VALUE paths
(the pattern tests/financial_analysis/test_projection_defaults_median_return.py
uses for the p50/median case only) would show a spurious, systematic
mismatch — do not "fix" this function to match that if you see it drift.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.financial_analysis.monte_carlo import run_monte_carlo
from src.financial_analysis.projection_defaults import (
    crossing_time_percentiles,
    median_return,
)
from src.services.north_star_glide import months_to_target


# ── Never-fabricate guard rails ──────────────────────────────────────────────

def test_none_when_expected_return_unavailable():
    result = crossing_time_percentiles(100.0, 0.0, None, 0.15, 1_000.0)
    assert result == {"p25": None, "p50": None, "p75": None}


def test_none_when_volatility_unavailable():
    result = crossing_time_percentiles(100.0, 0.0, 0.1, None, 1_000.0)
    assert result == {"p25": None, "p50": None, "p75": None}


def test_none_when_unreachable_within_horizon():
    """Tiny NW, zero contribution, ~flat return -> even the 60-year horizon
    never reaches the target at any confidence level -> all None, never a
    fabricated year (mirrors months_to_target's own None convention)."""
    result = crossing_time_percentiles(100.0, 0.0, 0.0, 0.15, 20_000_000.0)
    assert result == {"p25": None, "p50": None, "p75": None}


def test_zero_when_already_at_or_above_target():
    result = crossing_time_percentiles(2_000_000.0, 0.0, 0.1, 0.15, 1_000_000.0)
    assert result == {"p25": 0.0, "p50": 0.0, "p75": 0.0}


# ── Ordering: the whole point of W-3 (kills the ADR-026 ordering trap) ──────

def test_percentiles_strictly_ascending():
    """p25 < p50 < p75 must come out in the correct order automatically —
    no frontend inversion required (the old percentile-of-VALUE-path
    approximation needed lowYear=p75CrossYear / highYear=p25CrossYear)."""
    result = crossing_time_percentiles(3_269_850.0, 44_665.0, 0.108, 0.179, 20_000_000.0)
    assert result["p25"] is not None
    assert result["p50"] is not None
    assert result["p75"] is not None
    assert result["p25"] < result["p50"] < result["p75"]


def test_p50_matches_deterministic_median_years_to_target():
    """P(t) = 0.5 exactly at the deterministic median-drift crossing time by
    construction (ln(FV_mu(t)) - ln(target) = 0 there, independent of
    sigma) — so p50 must equal months_to_target(median_return(r,sigma))
    to within the continuous-interpolation rounding."""
    p0, pm, r, sigma, target = 3_269_850.0, 44_665.0, 0.108, 0.179, 20_000_000.0
    g = median_return(r, sigma)
    months = months_to_target(p0, pm, g, target)
    assert months is not None
    det_years = months / 12.0

    result = crossing_time_percentiles(p0, pm, r, sigma, target)
    assert result["p50"] == pytest.approx(det_years, abs=0.05)


def test_zero_volatility_collapses_all_percentiles_to_the_same_year():
    """No spread -> no uncertainty -> p25 == p50 == p75 == the deterministic
    crossing year exactly."""
    p0, pm, r, sigma, target = 1_000_000.0, 10_000.0, 0.08, 0.0, 3_000_000.0
    g = median_return(r, sigma)
    months = months_to_target(p0, pm, g, target)
    det_years = round(months / 12.0, 2)

    result = crossing_time_percentiles(p0, pm, r, sigma, target)
    assert result["p25"] == result["p50"] == result["p75"] == pytest.approx(det_years, abs=0.02)


# ── Pin against Monte Carlo — the critical test ──────────────────────────────

def _mc_empirical_crossing_percentiles(
    p0: float, r: float, sigma: float, monthly_contribution: float,
    target: float, years: int, num_simulations: int, seed: int,
) -> dict:
    """Empirical crossing-time percentiles computed the SAME "value at t >=
    target" way as crossing_time_percentiles — NOT first-passage. For each
    year, the fraction of individual simulated paths with value_t >= target;
    then linearly interpolate to find the year at which that fraction curve
    crosses 25% / 50% / 75%."""
    result = run_monte_carlo(
        initial_value=p0,
        annual_return=r,
        annual_volatility=sigma,
        years=years,
        num_simulations=num_simulations,
        annual_contribution=monthly_contribution * 12.0,
        seed=seed,
        return_paths=True,
    )
    paths = np.array(result["paths"])  # shape (num_simulations, years+1)
    year_list = result["years"]
    frac_at_or_above = (paths >= target).mean(axis=0)  # per-year fraction

    def _invert(p: float):
        for i in range(1, len(year_list)):
            if frac_at_or_above[i] >= p:
                prev_f, cur_f = frac_at_or_above[i - 1], frac_at_or_above[i]
                span = cur_f - prev_f
                frac_year = (p - prev_f) / span if span > 0 else 0.0
                return year_list[i - 1] + frac_year * (year_list[i] - year_list[i - 1])
        return None

    return {p: _invert(p) for p in (0.25, 0.5, 0.75)}


@pytest.mark.parametrize(
    "p0,r,sigma,monthly_contribution,target,years,seed",
    [
        (3_269_850.0, 0.108, 0.179, 44_665.0, 20_000_000.0, 25, 42),
        (1_000_000.0, 0.08, 0.15, 10_000.0, 3_000_000.0, 30, 7),
    ],
)
def test_analytic_crossing_percentiles_agree_with_monte_carlo_empirical(
    p0, r, sigma, monthly_contribution, target, years, seed,
):
    """The analytic P(t) = Phi(...) formula must reproduce the empirical
    "fraction of Monte Carlo paths at/above target by year t" distribution,
    inverted for 25/50/75%. This is a real (small) approximation, not just
    sampling noise — verified stable across simulation counts in
    development — so tolerances widen away from the median: p50 is the
    tightest (the analytic formula is exact there by construction, see
    test_p50_matches_deterministic_median_years_to_target), p25/p75 are
    looser because the lognormal-with-fixed-sigma approximation diverges
    more away from the center of the distribution.
    """
    mc = _mc_empirical_crossing_percentiles(
        p0, r, sigma, monthly_contribution, target, years,
        num_simulations=50_000, seed=seed,
    )
    analytic = crossing_time_percentiles(p0, monthly_contribution, r, sigma, target)

    assert mc[0.5] is not None, "MC empirical median crossing never reached within horizon — check test inputs"
    assert analytic["p50"] is not None
    rel_diff_p50 = abs(analytic["p50"] - mc[0.5]) / mc[0.5]
    assert rel_diff_p50 <= 0.05, (
        f"p50 diverged {rel_diff_p50:.2%} (analytic {analytic['p50']}y vs MC {mc[0.5]:.2f}y)"
    )

    for p, key in ((0.25, "p25"), (0.75, "p75")):
        assert mc[p] is not None, f"MC empirical {key} crossing never reached within horizon"
        assert analytic[key] is not None
        rel_diff = abs(analytic[key] - mc[p]) / mc[p]
        assert rel_diff <= 0.15, (
            f"{key} diverged {rel_diff:.2%} (analytic {analytic[key]}y vs MC {mc[p]:.2f}y) "
            f"for r={r}, sigma={sigma}, P0={p0}, pm={monthly_contribution}, target={target}"
        )
