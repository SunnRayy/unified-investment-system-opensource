"""Shared return-basis helpers used by both:
  - GET /analytics/projection/defaults
  - src/services/north_star_glide.py  (glide-path TWR + run-rate)
  - src/services/forecast_levers.py   (R-2, median_return only)

Keeps both consumers byte-identical — one source of truth.

Functions:
  suggested_return_basis(db) -> Optional[float]
      Annualized TWR on rebalanceable assets (Performance-page filter).
      Returns decimal (e.g. 0.1105 = 11.05%) or None.

  avg_monthly_investment(db, since) -> float
      Average monthly investment from income_expense_monthly 投资理财_*
      columns since the given ISO date. Returns 0.0 on any error.

  median_return(annual_return, annual_volatility) -> float
      Volatility-drag-adjusted (median / 50-50) compound annual growth rate.
      See docstring below — this is the R-1 engine-unification helper
      (docs/plans/2026-07-25-forecast-planning-redesign.md §2).

  crossing_time_percentiles(p0, monthly_contribution, annual_return,
  annual_volatility, target) -> dict
      Analytic crossing-TIME percentiles (W-3,
      docs/plans/2026-07-26-your-path-design-implementation.md §4.4). See
      docstring below for the exact "value at t >= target" definition —
      NOT first-passage time.
"""
from __future__ import annotations

import json
import logging
import math
from statistics import NormalDist
from typing import Optional

logger = logging.getLogger(__name__)


def median_return(annual_return: float, annual_volatility: float) -> float:
    """Median (50/50) compound annual growth rate implied by an arithmetic
    mean annual return and its volatility — i.e. volatility drag.

    A portfolio whose annual returns average `annual_return` (the arithmetic
    mean, e.g. the trailing-TWR figure this app quotes elsewhere) does NOT
    compound at that rate in the typical/most-likely case. Compounding is
    multiplicative, so variance around the mean drags the geometric/median
    outcome below the arithmetic mean — by approximately `sigma**2 / 2` for
    a lognormal-ish return distribution. The exact relationship used here is
    `g = exp(ln(1+r) - sigma**2/2) - 1`.

    THIS IS THE MEDIAN (50/50) BASIS, NOT THE MEAN PATH. Running the
    existing deterministic compounding engine
    (`src.services.north_star_glide.future_value` / `months_to_target`) at
    `g` instead of `r` reproduces the MEDIAN outcome of a full Monte Carlo
    simulation (`src.financial_analysis.monte_carlo.run_monte_carlo`) run
    with the same `annual_return`/`annual_volatility` inputs — validated to
    within ~1-1.5% in `tests/financial_analysis/test_projection_defaults_median_return.py`,
    which pins the two engines together so they cannot silently drift apart
    again (see docs/plans/2026-07-25-forecast-planning-redesign.md §2).
    Using the raw arithmetic `r` in a deterministic projection instead
    answers a different, more optimistic question — the MEAN path, which is
    only ~1-in-3 likely to be met or exceeded for typical equity volatility.

    This is the single choke point for the drag adjustment: every caller
    that wants a "realistic single-path" projection must go through this
    function rather than re-deriving the formula.

    Guard rails (never raise a math-domain error):
      - `annual_volatility <= 0` — no drag to apply; returns `annual_return`
        unchanged (also correctly reproduces the zero-volatility identity
        `g == r`).
      - `1 + annual_return <= 0` — the arithmetic return is -100% or worse,
        so `ln(1 + annual_return)` is undefined. Compounding cannot fall
        below a total loss, so this clamps to -1.0 (-100%) rather than
        raising. This is a defensive floor for a pathological input, not a
        realistic call ever expected in production.
    """
    if annual_volatility <= 0:
        return annual_return
    if 1.0 + annual_return <= 0:
        return -1.0
    return math.exp(math.log(1.0 + annual_return) - (annual_volatility ** 2) / 2.0) - 1.0


def _bisect_crossing_year(prob_fn, target_prob: float, horizon_years: float) -> Optional[float]:
    """Solve prob_fn(t) == target_prob for t in [0, horizon_years], assuming
    prob_fn is non-decreasing in t (true for the P(t) below — see
    crossing_time_percentiles docstring). Returns None (never a fabricated
    number) when even the horizon doesn't reach target_prob — mirrors
    north_star_glide.months_to_target's own None-on-unreachable convention.
    """
    lo, hi = 0.0, horizon_years
    if prob_fn(hi) < target_prob:
        return None
    if prob_fn(lo) >= target_prob:
        return 0.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if prob_fn(mid) < target_prob:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 2)


def crossing_time_percentiles(
    p0: float,
    monthly_contribution: float,
    annual_return: Optional[float],
    annual_volatility: Optional[float],
    target: float,
    percentiles: tuple[int, ...] = (25, 50, 75),
) -> dict[str, Optional[float]]:
    """Analytic crossing-TIME percentiles (W-3,
    docs/plans/2026-07-26-your-path-design-implementation.md §4.4).

    DEFINITION — read carefully, this is NOT first-passage time. For each
    percentile p, t_p solves

        P(t_p) = p / 100,   where   P(t) = Pr[ value(t) >= target ]

    i.e. t_p is the time at which there is a p% probability that the
    portfolio's value AT THAT TIME (not before) is at or above the target.
    This is the percentile of "value at time t", NOT the percentile of the
    FIRST time the portfolio ever touches the target (first-passage time).
    For a growing process, first-passage is always <= this "value at t"
    quantity — a systematic difference, not noise. Any UI copy or test
    built on this MUST use this same "value at t" definition; pinning it
    against Monte Carlo first-passage crossings will show a spurious
    mismatch that looks like a bug but isn't (see the pinning test in
    tests/financial_analysis/test_crossing_time_percentiles.py, which
    computes the Monte Carlo side the SAME "value at t >= target" way).

    Model: value(t) is treated as approximately lognormal around the
    DETERMINISTIC median-drift path FV_mu(t) — evaluated via
    north_star_glide.future_value at g = median_return(annual_return,
    annual_volatility), the SAME median/volatility-drag-adjusted rate
    years_to_target already uses (ADR-026). The log-space drift underlying
    `g` is mu = ln(1+annual_return) - annual_volatility**2/2, i.e.
    g = e^mu - 1 — this is ONE formula expressed two ways (discrete vs.
    log-space), not a second formula. median_return is reused unmodified
    here, never swapped for the continuous `r - 0.5*sigma**2` shorthand.

        P(t) = Phi( (ln(FV_mu(t)) - ln(target)) / (annual_volatility * sqrt(t)) )

    Phi is the standard normal CDF (statistics.NormalDist().cdf). FV_mu(t)
    is evaluated continuously in t by calling future_value() at the two
    bracketing integer-month values and linearly interpolating between them
    — the SAME within-month interpolation months_to_target already uses to
    report a fractional crossing month, not a re-derived annuity formula.

    P(t) is monotonically non-decreasing in t, so t_25 < t_50 < t_75 always
    comes out in the correct (ascending) order automatically — this is what
    replaces the ADR-026 "ordering trap" the old percentile-of-VALUE-path
    frontend approximation needed a caveat for.

    Returns {"p<N>": years_or_None, ...} for each requested percentile.
    None (never a fabricated number) when:
      - annual_return or annual_volatility is unavailable (can't compute), or
      - t_p would exceed the 60-year solver horizon (mirrors
        north_star_glide.months_to_target's own None convention).
    """
    if annual_return is None or annual_volatility is None:
        return {f"p{p}": None for p in percentiles}

    from src.services.north_star_glide import _MAX_HORIZON_YEARS, future_value

    g = median_return(annual_return, annual_volatility)

    def _fv_at(years: float) -> float:
        months_float = years * 12.0
        m_floor = int(months_float)
        frac = months_float - m_floor
        fv_floor = future_value(p0, monthly_contribution, g, m_floor)
        if frac <= 0:
            return fv_floor
        fv_ceil = future_value(p0, monthly_contribution, g, m_floor + 1)
        return fv_floor + frac * (fv_ceil - fv_floor)

    def _prob_at_or_above(years: float) -> float:
        if years <= 0:
            return 1.0 if p0 >= target else 0.0
        fv = _fv_at(years)
        if fv <= 0 or target <= 0:
            return 0.0
        denom = annual_volatility * math.sqrt(years)
        if denom <= 0:
            return 1.0 if fv >= target else 0.0
        z = (math.log(fv) - math.log(target)) / denom
        return NormalDist().cdf(z)

    horizon = float(_MAX_HORIZON_YEARS)
    return {
        f"p{p}": _bisect_crossing_year(_prob_at_or_above, p / 100.0, horizon)
        for p in percentiles
    }


def suggested_return_basis(db) -> Optional[float]:
    """Annualized TWR on rebalanceable assets only (Performance-page filter).

    Mirrors the `suggested_return` computed by GET /analytics/projection/defaults.
    Uses `fetch_included_asset_ids` + `exclude_non_balanceable=True` so the
    number is never inflated by insurance/property onboarding jumps or by the
    FS-history boundary (root cause of the V7.1.8 / Calmar class of bugs).

    Import of `fetch_included_asset_ids` is intentionally lazy to avoid a
    routes→services circular import — the same pattern used by value_trap.py.
    """
    try:
        from src.api.routes.performance import fetch_included_asset_ids
        from src.financial_analysis.twr import calculate_portfolio_twr

        include_ids = fetch_included_asset_ids(db, start_date=None)
        twr_result = calculate_portfolio_twr(
            db, include_asset_ids=include_ids, exclude_non_balanceable=True
        )
        if twr_result and twr_result.get("annualized") is not None:
            return round(float(twr_result["annualized"]), 6)
        return None
    except Exception:
        logger.exception("projection_defaults: failed to compute suggested_return_basis")
        return None


def avg_monthly_investment(db, since: str) -> float:
    """Average monthly investment from income_expense_monthly 投资理财_* columns.

    `since` — ISO date string (YYYY-MM-DD); only rows at or after this date
    are included in the average.

    Mirrors the local `avg_monthly_investment` helper in
    `src/api/routes/analytics.py::get_projection_defaults`.  Returns the
    average rounded to the nearest integer (same rounding as the analytics
    route), or 0.0 on any error / empty table.

    ⚠️ GROSS, and CNY-only. This sums the 投资理财_* columns as recorded, so it
    includes recycled/reallocated capital — it is NOT the ADR-025 net-new
    figure (`investment_contributions.contributions_summary_v2`) and the two
    must never be summed or compared as if equivalent.

    ⚠️ `_USD`-suffixed columns are EXCLUDED. `投资理财_股票基金_Schawab_USD` is
    the SAME money as `投资理财_股票基金_Schawab` recorded in dollars
    (ADR-025 §3: `Schawab == Schawab_USD × 参考_美元汇率`, verified every
    month). The old `startswith("投资理财_")` predicate added raw USD into a
    CNY total — e.g. a case measured as ¥95,400/mo reported vs ¥85,400/mo
    actual, a ¥10,000/mo (¥120K/yr) overstatement on the Projections default.
    `investment_contributions.py` has always guarded this; this path had not.
    """
    try:
        rows = db.execute(
            "SELECT payload FROM income_expense_monthly WHERE transaction_date >= ? ORDER BY transaction_date",
            [since],
        ).fetchall()
        if not rows:
            return 0.0
        total = 0.0
        for (payload_raw,) in rows:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
            total += sum(
                float(v or 0)
                for k, v in payload.items()
                if k.startswith("投资理财_") and not k.endswith("_USD")
            )
        return float(round(total / len(rows)))
    except Exception:
        logger.exception("projection_defaults: failed to compute avg_monthly_investment")
        return 0.0
