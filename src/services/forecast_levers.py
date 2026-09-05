"""R-2 — GET /forecast/levers sensitivity engine
(docs/plans/2026-07-25-forecast-planning-redesign.md §5, workstreams R-1/R-2).

Answers the owner's actual question: "what do I change to reach the goal
sooner?" A base case plus a sensitivity grid over three levers (savings,
expected return, volatility), using the SAME deterministic engine as North
Star's glide path (src.services.north_star_glide.future_value /
months_to_target), evaluated at the volatility-drag-adjusted median_return
(R-1, src.financial_analysis.projection_defaults.median_return) instead of
the raw arithmetic return. No Monte Carlo in this module — the whole point
of R-1 is that this stays cheap enough to recompute per request.

READ-ONLY — no writes to any table anywhere in this module.

§4b HARD REQUIREMENT (plan): every number here is derived from live data.
No year count, percentage, or currency amount from the plan document's
worked example may appear as a literal in this file. The lever STEP SIZES
(25/50/100% of run-rate, +-1/2pp return, -5/-8pp volatility) are themselves
plan-specified configuration constants (see "Lever presets" in the plan/
task spec — explicitly authorized as constants, unlike the RESULT figures
they are computed against), not results copied from the worked example.

W-2 (docs/plans/2026-07-26-your-path-design-implementation.md §4.3): three
OPTIONAL slider params — savings_pct, return_pp, volatility_pp — let a
caller ask for one extra row per lever at an arbitrary slider position
instead of only the fixed presets above. Omitted (all three None, the
default) -> the response is byte-for-byte identical to before this
workstream (see tests/services/test_forecast_levers.py
test_no_slider_params_matches_pre_w2_response). This is deliberately
still server-side, closed-form math — NOT a client-side reimplementation
of years_to_target — see this module's own docstring above and ADR-026.
"""
from __future__ import annotations

from typing import Optional

# Lever step sizes — plan-specified sensitivity-grid configuration, NOT
# result literals (see module docstring).
_SAVINGS_STEPS_PCT: tuple[int, ...] = (25, 50, 100)   # +25% / +50% / +100% of current run-rate
_RETURN_STEPS_PP: tuple[int, ...] = (1, 2)            # +1pp / +2pp
_VOLATILITY_STEPS_PP: tuple[int, ...] = (5, 8)        # -5pp / -8pp
_VOLATILITY_FLOOR = 1e-6                              # volatility must never reach <= 0

# W-2 slider ranges — plan-specified UI configuration (§4.3 table), NOT
# result literals. Steps (5 / 0.5 / 0.5) are enforced by the frontend
# slider control, not here; the backend only clamps to the min/max bound.
_SAVINGS_PCT_RANGE: tuple[float, float] = (0.0, 60.0)
_RETURN_PP_RANGE: tuple[float, float] = (0.0, 6.0)
_VOLATILITY_PP_RANGE: tuple[float, float] = (0.0, 10.0)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _years_to_target(
    p0: float,
    monthly_contribution: float,
    annual_return: Optional[float],
    annual_volatility: Optional[float],
    target: float,
) -> Optional[float]:
    """years_to_target for one scenario, evaluated at median_return(r, sigma)
    (R-1) rather than the raw arithmetic return.

    None when annual_return/annual_volatility are unavailable (can't
    compute — never fabricated), or when months_to_target itself reports
    the target unreachable within its 60-year solver horizon.
    """
    if annual_return is None or annual_volatility is None:
        return None
    from src.financial_analysis.projection_defaults import median_return
    from src.services.north_star_glide import months_to_target

    g = median_return(annual_return, annual_volatility)
    months = months_to_target(p0, monthly_contribution, g, target)
    return round(months / 12.0, 2) if months is not None else None


def _delta_years(lever_years: Optional[float], base_years: Optional[float]) -> Optional[float]:
    """Negative when the lever gets you there SOONER."""
    if lever_years is None or base_years is None:
        return None
    return round(lever_years - base_years, 2)


def compute_levers(
    db,
    *,
    savings_pct: Optional[float] = None,
    return_pp: Optional[float] = None,
    volatility_pp: Optional[float] = None,
) -> dict:
    """Base case + sensitivity grid over savings / return / volatility.

    W-2 optional params (all default None): when supplied, each ADDS one
    row to the corresponding levers.<name> list at that exact slider
    position (clamped server-side to _SAVINGS_PCT_RANGE / _RETURN_PP_RANGE
    / _VOLATILITY_PP_RANGE), and "combined" is recomputed at the JOINT
    position of whichever params were supplied — any lever whose param was
    NOT supplied still uses the existing first-preset step for combined,
    which is exactly why calling with all three omitted reproduces
    "combined" unchanged. The applied (post-clamp) values are echoed back
    in a top-level "applied" dict — present ONLY when at least one slider
    param was supplied, so the no-params response has no new key at all
    (byte-for-byte backward compatible).

    All 5 inputs are live, derived, and reused from existing single-source-
    of-truth functions — never re-derived, never a plan-document literal:

      P0 (current_nw):
        src.services.north_star_glide._default_net_worth(db) — the SAME
        liquid (rebalanceable) NW north_star_glide.glide_path uses.

      r (expected_return):
        src.financial_analysis.projection_defaults.suggested_return_basis(db)
        — trailing annualized TWR, rebalanceable-only basis.

      sigma (volatility):
        src.financial_analysis.metrics.calculate_portfolio_metrics(db,
        include_asset_ids=fetch_included_asset_ids(db), exclude_non_balanceable=True)
        ["volatility_annual"] / 100.0 — the exact same call + /100 unit
        convention GET /analytics/projection/defaults already uses.

      pm (monthly_contribution):
        src.services.north_star_glide._contribution_run_rate(db) — the SAME
        (net_external_ttm + rsu_retained_ttm) / 12 run-rate glide_path uses.
        0.0 when unavailable (status != "available"), matching glide_path's
        own zero-fallback convention — never an exception.

      target:
        src.services.goal_resolver.resolve_north_star_goal(db) — the SAME
        single resolver north_star_glide.glide_path targets (goals table,
        retirement type, config fallback). See that module's docstring for
        the full resolution rule; NEVER read target_net_worth_cny directly
        here (structural guard: tests/services/test_goal_resolver_structural_guard.py).

    Engine: north_star_glide.future_value / months_to_target (via
    _years_to_target above), evaluated at median_return(r, sigma). Never a
    re-invented projection formula, never Monte Carlo in this path.
    """
    from src.api.routes.performance import fetch_included_asset_ids
    from src.financial_analysis.metrics import calculate_portfolio_metrics
    from src.financial_analysis.projection_defaults import (
        crossing_time_percentiles,
        median_return,
        suggested_return_basis,
    )
    from src.services.goal_resolver import resolve_north_star_goal
    from src.services.north_star_glide import _contribution_run_rate, _default_net_worth

    goal = resolve_north_star_goal(db)
    target = goal["target_amount"]

    current_nw = _default_net_worth(db)
    expected_return = suggested_return_basis(db)

    volatility: Optional[float] = None
    try:
        include_ids = fetch_included_asset_ids(db, start_date=None)
        metrics = calculate_portfolio_metrics(
            db, include_asset_ids=include_ids, exclude_non_balanceable=True
        )
        if metrics and metrics.get("volatility_annual") is not None:
            volatility = float(metrics["volatility_annual"]) / 100.0
    except Exception:
        volatility = None

    run_rate_monthly, run_rate_status = _contribution_run_rate(db)
    monthly_contribution = (
        run_rate_monthly if run_rate_status == "available" and run_rate_monthly is not None else 0.0
    )

    base_median_return = (
        median_return(expected_return, volatility)
        if (expected_return is not None and volatility is not None)
        else None
    )
    base_years = _years_to_target(current_nw, monthly_contribution, expected_return, volatility, target)

    # W-3 (docs/plans/2026-07-26-your-path-design-implementation.md §4.4):
    # analytic crossing-TIME percentiles for the SAME base scenario (current
    # NW, run-rate, expected return, volatility) — never the lever-adjusted
    # scenario. Ordering (p25 <= p50 <= p75) is guaranteed by construction;
    # see crossing_time_percentiles' own docstring for the exact definition
    # ("value at t >= target", not first-passage) — the UI must not
    # re-derive this in JS (that duplication is exactly what this
    # workstream removes; see ADR-026 and AnswerSection.tsx).
    crossing_years = crossing_time_percentiles(
        current_nw, monthly_contribution, expected_return, volatility, target
    )

    base = {
        "current_nw": round(current_nw, 2),
        "expected_return": round(expected_return, 6) if expected_return is not None else None,
        "volatility": round(volatility, 6) if volatility is not None else None,
        "median_return": round(base_median_return, 6) if base_median_return is not None else None,
        "monthly_contribution": round(monthly_contribution, 2),
        "target": target,
        "years_to_target": base_years,
        "crossing_years": crossing_years,
    }

    # ── Savings lever: fractions of the CURRENT run-rate ────────────────────
    savings_rows = []
    for pct in _SAVINGS_STEPS_PCT:
        lever_pm = round(monthly_contribution * (1.0 + pct / 100.0), 2)
        lever_years = _years_to_target(current_nw, lever_pm, expected_return, volatility, target)
        savings_rows.append({
            "label": f"+{pct}% (¥{lever_pm:,.0f}/mo)",
            "monthly_contribution": lever_pm,
            "years_to_target": lever_years,
            "delta_years": _delta_years(lever_years, base_years),
        })

    # ── Return lever: +1pp / +2pp ────────────────────────────────────────────
    return_rows = []
    for pp in _RETURN_STEPS_PP:
        lever_r = (expected_return + pp / 100.0) if expected_return is not None else None
        lever_years = _years_to_target(current_nw, monthly_contribution, lever_r, volatility, target)
        return_rows.append({
            "label": f"+{pp}pp",
            "expected_return": round(lever_r, 6) if lever_r is not None else None,
            "years_to_target": lever_years,
            "delta_years": _delta_years(lever_years, base_years),
        })

    # ── Volatility lever: -5pp / -8pp, floored above zero ───────────────────
    volatility_rows = []
    for pp in _VOLATILITY_STEPS_PP:
        lever_sigma = max(volatility - pp / 100.0, _VOLATILITY_FLOOR) if volatility is not None else None
        lever_years = _years_to_target(current_nw, monthly_contribution, expected_return, lever_sigma, target)
        volatility_rows.append({
            "label": f"-{pp}pp",
            "volatility": round(lever_sigma, 6) if lever_sigma is not None else None,
            "years_to_target": lever_years,
            "delta_years": _delta_years(lever_years, base_years),
        })

    # ── W-2: optional slider-position rows ───────────────────────────────────
    # Each supplied param adds exactly one row to its lever list, at the
    # slider position (clamped server-side), and records what was actually
    # used in `applied` so a clamped request is visible to the caller. All
    # three None (the default) -> neither savings_rows/return_rows/
    # volatility_rows nor `applied` change at all — this IS the "no params
    # -> byte-for-byte identical to before W-2" guarantee.
    applied: Optional[dict] = None
    queried_pm: Optional[float] = None
    queried_r: Optional[float] = None
    queried_sigma: Optional[float] = None

    if savings_pct is not None or return_pp is not None or volatility_pp is not None:
        applied = {"savings_pct": None, "return_pp": None, "volatility_pp": None}

    if savings_pct is not None:
        clamped_pct = _clamp(savings_pct, *_SAVINGS_PCT_RANGE)
        applied["savings_pct"] = clamped_pct
        queried_pm = round(monthly_contribution * (1.0 + clamped_pct / 100.0), 2)
        queried_pm_years = _years_to_target(current_nw, queried_pm, expected_return, volatility, target)
        savings_rows.append({
            "label": f"+{clamped_pct:g}% (¥{queried_pm:,.0f}/mo)",
            "monthly_contribution": queried_pm,
            "years_to_target": queried_pm_years,
            "delta_years": _delta_years(queried_pm_years, base_years),
        })

    if return_pp is not None:
        clamped_pp = _clamp(return_pp, *_RETURN_PP_RANGE)
        applied["return_pp"] = clamped_pp
        queried_r = (expected_return + clamped_pp / 100.0) if expected_return is not None else None
        queried_r_years = _years_to_target(current_nw, monthly_contribution, queried_r, volatility, target)
        return_rows.append({
            "label": f"+{clamped_pp:g}pp",
            "expected_return": round(queried_r, 6) if queried_r is not None else None,
            "years_to_target": queried_r_years,
            "delta_years": _delta_years(queried_r_years, base_years),
        })

    if volatility_pp is not None:
        clamped_pp = _clamp(volatility_pp, *_VOLATILITY_PP_RANGE)
        applied["volatility_pp"] = clamped_pp
        queried_sigma = (
            max(volatility - clamped_pp / 100.0, _VOLATILITY_FLOOR) if volatility is not None else None
        )
        queried_sigma_years = _years_to_target(
            current_nw, monthly_contribution, expected_return, queried_sigma, target
        )
        volatility_rows.append({
            "label": f"-{clamped_pp:g}pp",
            "volatility": round(queried_sigma, 6) if queried_sigma is not None else None,
            "years_to_target": queried_sigma_years,
            "delta_years": _delta_years(queried_sigma_years, base_years),
        })

    # ── Combined: JOINT position of whichever params were supplied, else the
    # existing first preset step of that lever (unchanged default) ─────────
    combined_pm = queried_pm if savings_pct is not None else savings_rows[0]["monthly_contribution"]
    combined_r = queried_r if return_pp is not None else return_rows[0]["expected_return"]
    combined_sigma = queried_sigma if volatility_pp is not None else volatility_rows[0]["volatility"]
    combined_years = _years_to_target(current_nw, combined_pm, combined_r, combined_sigma, target)

    combined_savings_label = f"+{applied['savings_pct']:g}%" if savings_pct is not None else f"+{_SAVINGS_STEPS_PCT[0]}%"
    combined_return_label = f"+{applied['return_pp']:g}pp" if return_pp is not None else f"+{_RETURN_STEPS_PP[0]}pp"
    combined_vol_label = f"-{applied['volatility_pp']:g}pp" if volatility_pp is not None else f"-{_VOLATILITY_STEPS_PP[0]}pp"

    combined = {
        "label": f"{combined_savings_label} savings, {combined_return_label} return, {combined_vol_label} volatility",
        "years_to_target": combined_years,
        "delta_years": _delta_years(combined_years, base_years),
    }

    result = {
        "base": base,
        "levers": {
            "savings": savings_rows,
            "return": return_rows,
            "volatility": volatility_rows,
        },
        "combined": combined,
        "goal": goal,
    }
    if applied is not None:
        result["applied"] = applied
    return result
