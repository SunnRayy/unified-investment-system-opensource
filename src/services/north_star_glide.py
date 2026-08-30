"""North Star F3.2/F3.3/F3.4 — time-in-market, unforced errors, glide path
(PRD 2026-07-07, Batch B6).

Split out of src/services/north_star.py to keep each file under the 400-line
guideline; north_star.py re-exports everything here and is the intended
import surface for routes/tests.

Time-in-market top-class mapping (F3.2): a holdings row counts toward
"equity + commodities + alternatives" if its resolved top-level taxonomy
class name contains 'Equity', 'Commodit', or 'Alternative' — the canonical
top-class names src/services/compass_allocation.py::DISPLAY_MAP documents
('Equity', 'Commodity', 'Alternative'), i.e. the same live allocation engine
F4.1 requires the drift metric to read.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

from src.services.goal_resolver import resolve_north_star_goal
from src.services.verification_config import load_verification_config

logger = logging.getLogger(__name__)

_MAX_HORIZON_YEARS = 60
_EQUITY_LIKE_MARKERS = ("Equity", "Commodit", "Alternative")


# ─────────────────────────────────────────────────────────────────────────
# F3.2 — time in market
# ─────────────────────────────────────────────────────────────────────────

def _equity_like_target_pct(db) -> Optional[float]:
    """Sum of active risk-profile targets for equity-like top classes, read
    from the live Compass allocation engine (see module docstring)."""
    try:
        from src.services.compass_allocation import build_compass_allocation
        alloc = build_compass_allocation(db)
        if not isinstance(alloc, list):
            alloc = alloc.get("allocation", [])
        total = 0.0
        found = False
        for row in alloc:
            if not row.get("is_top_level"):
                continue
            name = str(row.get("asset_class") or "")
            if any(k in name for k in _EQUITY_LIKE_MARKERS):
                total += float(row.get("target_pct") or 0.0)
                found = True
        return total if found else None
    except Exception:
        logger.exception("north_star: failed to resolve equity-like target from compass allocation")
        return None


def time_in_market(db) -> dict:
    """Trailing-N-month ratio of months where equity+commodities+alternatives
    weight >= (target - band_pp), per PRD F3.2. Never fabricates: returns
    {insufficient_data: True} below 3 months of holdings history."""
    cfg = load_verification_config().north_star

    rows = db.execute(
        """
        WITH monthly_latest AS (
            SELECT asset_id, strftime(snapshot_date, '%Y-%m') AS month, MAX(snapshot_date) AS latest_date
            FROM holdings
            WHERE is_shadow = FALSE
            GROUP BY asset_id, strftime(snapshot_date, '%Y-%m')
        )
        SELECT
            ml.month,
            COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified') AS top_class,
            SUM(h.market_value) AS value
        FROM holdings h
        JOIN monthly_latest ml ON h.asset_id = ml.asset_id AND h.snapshot_date = ml.latest_date
        LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
        LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
        LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
        WHERE h.is_shadow = FALSE
        GROUP BY 1, 2
        """
    ).fetchall()

    by_month: dict[str, dict[str, float]] = defaultdict(lambda: {"equity_like": 0.0, "total": 0.0})
    for month, top_class, value in rows:
        v = float(value or 0.0)
        by_month[month]["total"] += v
        if any(k in str(top_class or "") for k in _EQUITY_LIKE_MARKERS):
            by_month[month]["equity_like"] += v

    months_sorted = sorted(by_month.keys())[-cfg.tim_trailing_months:]
    if len(months_sorted) < 3:
        return {"insufficient_data": True}

    target_pct = _equity_like_target_pct(db)
    if target_pct is None:
        return {"insufficient_data": True, "reason": "no active risk-profile target"}

    band_floor = target_pct - cfg.tim_band_pp
    in_market_months = 0
    monthly_weights = []
    for month in months_sorted:
        total = by_month[month]["total"]
        weight_pct = (by_month[month]["equity_like"] / total * 100.0) if total > 0 else 0.0
        monthly_weights.append({"month": month, "weight_pct": round(weight_pct, 2)})
        if weight_pct >= band_floor:
            in_market_months += 1

    return {
        "insufficient_data": False,
        "ratio": round(in_market_months / len(months_sorted), 4),
        "in_market_months": in_market_months,
        "total_months": len(months_sorted),
        "target_pct": round(target_pct, 2),
        "band_floor_pct": round(band_floor, 2),
        "monthly_weights": monthly_weights,
    }


# ─────────────────────────────────────────────────────────────────────────
# F3.3 — unforced errors
# ─────────────────────────────────────────────────────────────────────────

def _unforced_error_row_to_dict(r) -> dict:
    """Convert an unforced_errors DB row (8 columns) to a response dict."""
    import json as _json
    history_raw = r[7] if len(r) > 7 else None
    try:
        history = _json.loads(history_raw) if isinstance(history_raw, str) and history_raw else []
    except Exception:
        history = []
    return {
        "id": r[0],
        "error_date": str(r[1]) if r[1] is not None else None,
        "description": r[2],
        "est_cost_cny": float(r[3]) if r[3] is not None else None,
        "root_cause": r[4],
        "linked_rule": r[5],
        "created_at": str(r[6]) if r[6] is not None else None,
        "cost_edit_history": history,
    }


def list_unforced_errors(db) -> list[dict]:
    rows = db.execute(
        """
        SELECT id, error_date, description, est_cost_cny, root_cause, linked_rule,
               created_at, cost_edit_history
        FROM unforced_errors
        ORDER BY error_date DESC, id DESC
        """
    ).fetchall()
    return [_unforced_error_row_to_dict(r) for r in rows]


def create_unforced_error(
    db, error_date: str, description: str, est_cost_cny: Optional[float] = None,
    root_cause: Optional[str] = None, linked_rule: Optional[str] = None,
) -> dict:
    if not description or not description.strip():
        raise ValueError("description must be non-empty")
    try:
        datetime.strptime(error_date, "%Y-%m-%d")
    except (ValueError, TypeError) as e:
        raise ValueError(f"error_date must be YYYY-MM-DD, got {error_date!r}") from e

    db.execute(
        """
        INSERT INTO unforced_errors (error_date, description, est_cost_cny, root_cause, linked_rule)
        VALUES (?, ?, ?, ?, ?)
        """,
        [error_date, description.strip(), est_cost_cny, root_cause, linked_rule],
    )
    row = db.execute(
        """
        SELECT id, error_date, description, est_cost_cny, root_cause, linked_rule,
               created_at, cost_edit_history
        FROM unforced_errors WHERE error_date = ? AND description = ?
        ORDER BY id DESC LIMIT 1
        """,
        [error_date, description.strip()],
    ).fetchone()
    return _unforced_error_row_to_dict(row)


def update_unforced_error_cost(db, error_id: int, new_cost_cny: Optional[float]) -> dict:
    """Update est_cost_cny for an unforced error and append to edit history."""
    import json as _json
    from datetime import timezone

    row = db.execute(
        "SELECT est_cost_cny, cost_edit_history FROM unforced_errors WHERE id = ?",
        [error_id],
    ).fetchone()
    if row is None:
        raise LookupError(f"unforced error {error_id} not found")

    old_cost = float(row[0]) if row[0] is not None else None
    history_raw = row[1]
    try:
        history = _json.loads(history_raw) if isinstance(history_raw, str) and history_raw else []
    except Exception:
        history = []
    if not isinstance(history, list):
        history = []

    history.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "old": old_cost,
        "new": float(new_cost_cny) if new_cost_cny is not None else None,
    })

    db.execute(
        "UPDATE unforced_errors SET est_cost_cny = ?, cost_edit_history = ? WHERE id = ?",
        [new_cost_cny, _json.dumps(history), error_id],
    )

    updated = db.execute(
        """
        SELECT id, error_date, description, est_cost_cny, root_cause, linked_rule,
               created_at, cost_edit_history
        FROM unforced_errors WHERE id = ?
        """,
        [error_id],
    ).fetchone()
    return _unforced_error_row_to_dict(updated)


# ─────────────────────────────────────────────────────────────────────────
# F3.4 — glide path (pure deterministic compounding)
# ─────────────────────────────────────────────────────────────────────────

def _monthly_rate(annual_rate: float) -> float:
    return (1.0 + annual_rate) ** (1.0 / 12.0) - 1.0


def months_to_target(nw: float, monthly_contribution: float, annual_rate: float, target: float) -> Optional[float]:
    """Iterate month-by-month; None if unreachable within _MAX_HORIZON_YEARS
    (never fabricate a number for an unreachable target).

    Returns a fractional month count, linearly interpolated within the
    crossing month, rather than always rounding up to the next whole month —
    a whole-month-only count systematically overstates years-to-target by up
    to ~1 month (e.g. 208 discrete months vs. the true ~207.1-month
    crossing point for the PRD's illustrative NW / 11.05% TWR / ¥0 contribution
    spreadsheet fixture, which needs years-to-target within ±0.2y of ~17.1).

    Public (promoted 2026-07-25, R-2 forecast-levers workstream): the
    deterministic engine is now also called from
    src.services.forecast_levers, a different module, so it must not be
    imported cross-module as a private name.
    """
    if nw >= target:
        return 0.0
    r_m = _monthly_rate(annual_rate)
    value = nw
    prev_value = value
    for month in range(1, _MAX_HORIZON_YEARS * 12 + 1):
        prev_value = value
        value = value * (1.0 + r_m) + monthly_contribution
        if value >= target:
            span = value - prev_value
            frac = (target - prev_value) / span if span > 0 else 0.0
            return (month - 1) + frac
    return None


def future_value(nw: float, monthly_contribution: float, annual_rate: float, months: int) -> float:
    """Compound nw for `months` months at `annual_rate`, adding
    `monthly_contribution` at the end of every month.

    Public (promoted 2026-07-25, R-2 forecast-levers workstream) — see
    months_to_target docstring.
    """
    r_m = _monthly_rate(annual_rate)
    value = nw
    for _ in range(months):
        value = value * (1.0 + r_m) + monthly_contribution
    return value


def _required_cagr(nw: float, monthly_contribution: float, target: float, horizon_years: int) -> Optional[float]:
    """Bisection on annual rate solving future_value(...) == target.

    None only when bisection cannot bracket a root: the target is unreachable
    even at +500%/yr (reported as None, not a fabricated number). Already
    exceeded at -99%/yr (contribution alone clears it) returns -0.99.
    """
    months = horizon_years * 12
    lo, hi = -0.99, 5.0
    if future_value(nw, monthly_contribution, lo, months) >= target:
        return lo
    if future_value(nw, monthly_contribution, hi, months) < target:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if future_value(nw, monthly_contribution, mid, months) < target:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 6)


def _default_net_worth(db) -> float:
    """Liquid (rebalanceable) net worth — same asset universe as the TWR basis.

    The ¥20M target is a LIQUID-assets target and the glide TWR basis is
    rebalanceable-only, so the starting NW must use the same universe.
    Summing total net worth (incl. real estate / insurance / pension) would
    compound non-compounding assets at the portfolio TWR and understate
    years-to-target.

    Restricts to `fetch_included_asset_ids(db, start_date=None)` — the exact
    Performance-page filter `suggested_return_basis` uses — and reuses the
    latest-per-asset CTE /performance/summary uses
    (src/api/routes/performance.py::LATEST_SNAPSHOT_CTE) rather than
    re-deriving the aggregation. Lazy route import, same precedent as
    projection_defaults.suggested_return_basis.
    """
    from src.api.routes.performance import LATEST_SNAPSHOT_CTE, fetch_included_asset_ids

    include_ids = fetch_included_asset_ids(db, start_date=None)
    if not include_ids:
        return 0.0
    placeholders = ", ".join("?" for _ in include_ids)
    row = db.execute(
        f"""
        {LATEST_SNAPSHOT_CTE}
        SELECT SUM(h.market_value)
        FROM holdings h
        JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        WHERE h.is_shadow = FALSE
          AND h.asset_id IN ({placeholders})
        """,
        list(include_ids),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def _default_trailing_twr(db) -> Optional[float]:
    """Rebalanceable-only annualized TWR — same basis as the Forecast page's
    /analytics/projection/defaults (Performance-page filter).

    Delegates to `src.financial_analysis.projection_defaults.suggested_return_basis`
    so both the Forecast projection defaults and the North Star glide path always
    use the same number.  The unbounded all-time TWR (the old implementation)
    was deposit-inflated by the FS-history boundary and produced ~35% annualized
    (same class as the V7.1.8 Calmar bug).
    """
    from src.financial_analysis.projection_defaults import suggested_return_basis

    return suggested_return_basis(db)


def _trailing_12m_gross_income(db) -> Optional[float]:
    """Trailing-12M gross income, DERIVED from the 月度收支 leaf columns.

    Used for the run-rate sanity guard: if the computed run-rate exceeds 60%
    of trailing gross income, the number is implausible.  Returns None when
    income_expense_monthly has no rows in range (guard skipped).

    Was `Σ payload['总收入合计']`; now `Σ LedgerTotals.gross_income` (income
    leaves + redemptions + the pass-through inflow) via the shared ie_column
    role mapping —
    owner ruling 2026-08-01, no Excel aggregate is a calculation input. The two
    agree to the cent on live data, so the guard's threshold is unchanged
    today; it will differ (correctly) the moment a leaf column falls outside
    the owner's SUM range.
    """
    from datetime import date, timedelta

    from src.services.ie_ledger import (  # noqa: PLC0415 — lazy, mirrors this module's other imports
        load_ie_column_mapping,
        payload_dict,
        role_totals,
    )

    trailing_start = (date.today() - timedelta(days=365)).isoformat()
    try:
        rows = db.execute(
            "SELECT payload FROM income_expense_monthly WHERE transaction_date >= ?",
            [trailing_start],
        ).fetchall()
        if not rows:
            return None
        mapping = load_ie_column_mapping(db)
        buckets = None
        total = 0.0
        for (payload_raw,) in rows:
            payload = payload_dict(payload_raw)
            if not payload:
                continue
            total += role_totals(payload, mapping, buckets=buckets).gross_income
        return total if total > 0 else None
    except Exception:
        logger.exception("north_star: failed to compute trailing_12m_gross_income")
        return None


def _contribution_run_rate(db) -> tuple[Optional[float], str]:
    """Monthly run-rate = (net_external_ttm + RSU retained-in-window) / 12.

    Rewired 2026-07-25 (ADR-025 §5.2, owner decision; plan
    2026-07-25-cash-flow-classification-completion.md §3.6). The run-rate
    used to be the trailing-12M average of cash_flow_tags rows tagged
    external_contribution — the plan found that figure to be 100% RSU-vest-
    gross (§3.4), i.e. it double-counted RSU vests already booked as income
    in 月度收支, and undercounted everything the ledger's own 投资理财 columns
    already track as real savings.

    Source is now the two ADR-025 lines, over the SAME trailing window:
    - net_external_ttm from contributions_summary_v2() (月度收支 投资理财,
      src/services/investment_contributions.py) — the ADR-025 authority for
      investment contributions.
    - retained_ttm from rsu_retained_ttm() (src/services/rsu_contributions.py)
      — RSU shares that vested inside that SAME window and are still held, a
      real portfolio inflow the ledger never captures because it books RSU
      vests as income, not investment (plan §3.3).
    Both calls are given contributions_summary_v2's own returned
    window_start_month/window_end_month — never recomputed independently —
    so the two figures can never drift onto different windows.

    NO LONGER GATED by flow_contamination_status / cash_flow_tags
    completeness: this function reads no cash_flow_tags data at all now, so
    tag-classification completeness is irrelevant to it. (cash_flow_tags-
    derived sums — ytd_sum/trailing_12m_sum/by_classification — remain a
    separate, retired-as-contributions figure; see ADR-025 §4.)

    Returns (run_rate_monthly, status) where status is one of:
    - "available"  — run_rate_monthly is the computed value
    - "no contribution data available"  — income_expense_monthly has no rows
      at all (contributions_summary_v2's window is None); run_rate_monthly
      is None
    - "run-rate implausible — check flow tagging"  — sanity guard fired;
      run_rate_monthly is None

    Sanity guard (unchanged): run_rate > 60% of trailing-12M gross income
    (income_expense_monthly 总收入合计). Guard is skipped when income data is
    unavailable.
    """
    from src.services.investment_contributions import contributions_summary_v2
    from src.services.rsu_contributions import rsu_retained_ttm

    investment = contributions_summary_v2(db)
    window_start = investment["window_start_month"]
    window_end = investment["window_end_month"]
    if window_start is None or window_end is None:
        return None, "no contribution data available"

    net_external_ttm = investment["net_external_ttm"]
    retained = rsu_retained_ttm(db, window_start, window_end)
    run_rate = (net_external_ttm + retained["retained_cny"]) / 12.0

    # Sanity guard: run-rate > 60% of trailing gross income is implausible
    gross_income = _trailing_12m_gross_income(db)
    if gross_income is not None and gross_income > 0 and run_rate > 0.6 * gross_income:
        return None, "run-rate implausible — check flow tagging"

    return round(run_rate, 2), "available"


def glide_path(
    db, monthly_contribution: float = 0.0,
    current_nw: Optional[float] = None, trailing_twr: Optional[float] = None,
) -> dict:
    """PRD F3.4 — deterministic compounding glide path to the target net
    worth resolved by src.services.goal_resolver.resolve_north_star_goal
    (the goals table's active retirement goal, falling back to
    config/verification.yaml only when no such goal exists — see that
    module's docstring). All assumptions are returned in an 'assumptions'
    block, explicitly labeled — never rendered as a forecast (Cross-Cutting
    Req 3).

    TWR basis: rebalanceable assets only, annualized (Performance-page filter).
    Same number as /analytics/projection/defaults suggested_return. Never the
    unbounded all-time TWR which is inflated by the FS-history boundary.

    NW basis: liquid (rebalanceable) assets — the SAME universe as the TWR
    basis, because the ¥20M target is a liquid-assets target. Total net worth
    (incl. real estate / insurance / pension) is never used as the default.

    Run-rate (rewired 2026-07-25, ADR-025 §5.2): (net_external_ttm + RSU
    retained-in-window) / 12 — see _contribution_run_rate docstring for the
    full derivation. No longer derived from cash_flow_tags at all (that was
    the Fix 5, 2026-07-10 mechanism; it double-counted RSU vests already
    booked as income in 月度收支). If income_expense_monthly has no data, or
    the computed value exceeds 60% of trailing gross income (implausible),
    run_rate_monthly is None and run_rate_status explains why.

    Headline binding rule (Fix 5): the headline years_to_target must come from
    the SAME scenario used in the headline text:
    - If run-rate is available → headline uses the run-rate scenario.
    - Else → headline uses ¥0/mo.
    The 'headline' sub-dict encodes this explicitly so the UI binds correctly.
    'years_to_target' remains the *scenario* (monthly_contribution parameter)
    result for the scenario-input use case.
    """
    cfg = load_verification_config().north_star
    goal = resolve_north_star_goal(db)
    target = goal["target_amount"]

    if current_nw is None:
        current_nw = _default_net_worth(db)
    if trailing_twr is None:
        trailing_twr = _default_trailing_twr(db)

    # Run-rate from tagged external_contribution flows (Fix 5)
    run_rate_monthly, run_rate_status = _contribution_run_rate(db)

    assumptions = {
        "current_nw": round(current_nw, 2),
        "trailing_twr_pct": round(trailing_twr * 100, 2) if trailing_twr is not None else None,
        "monthly_contribution": round(monthly_contribution, 2),
        "target": target,
        "goal_source": goal["source"],
        "goal_name": goal["name"],
        "goal_id": goal["goal_id"],
        "note": "deterministic compounding; all inputs are assumptions, not forecasts",
        "twr_basis": "annualized TWR, rebalanceable assets (Performance-page filter)",
        "nw_basis": "liquid (rebalanceable) assets — same universe as the TWR basis",
        "run_rate_basis": "(月度收支 net_external_ttm + RSU retained-in-window) / 12 (ADR-025 §5.2)",
        "current_run_rate_monthly": run_rate_monthly,  # None when unavailable
        "run_rate_status": run_rate_status,
    }

    if trailing_twr is None:
        return {"reachable": False, "insufficient_data": True, "assumptions": assumptions}

    # ── Compute years for each scenario ─────────────────────────────────────

    # Scenario (user-controlled input):
    months_scenario = months_to_target(current_nw, monthly_contribution, trailing_twr, target)
    reachable = months_scenario is not None
    years_scenario = round(months_scenario / 12.0, 2) if months_scenario is not None else None

    # ¥0/mo (always computable):
    months_zero = months_to_target(current_nw, 0.0, trailing_twr, target)
    years_zero = round(months_zero / 12.0, 2) if months_zero is not None else None

    # Run-rate (only when available):
    years_run_rate: Optional[float] = None
    if run_rate_monthly is not None:
        m = months_to_target(current_nw, run_rate_monthly, trailing_twr, target)
        years_run_rate = round(m / 12.0, 2) if m is not None else None

    # ── Headline binding rule ────────────────────────────────────────────────
    # "if run-rate available → headline = run-rate scenario; else headline = ¥0"
    if run_rate_monthly is not None and years_run_rate is not None:
        headline_years = years_run_rate
        headline_contribution = run_rate_monthly
        headline_scenario_used = "current_run_rate"
    else:
        headline_years = years_zero
        headline_contribution = 0.0
        headline_scenario_used = "zero"

    contribution_levels = {
        "zero": 0.0,
        "current_run_rate": run_rate_monthly if run_rate_monthly is not None else 0.0,
        "scenario": monthly_contribution,
    }

    required_cagr_grid = []
    for horizon in cfg.glide_horizons_years:
        by_level = {}
        for level_name, level_contribution in contribution_levels.items():
            rate = _required_cagr(current_nw, level_contribution, target, horizon)
            by_level[level_name] = round(rate * 100, 2) if rate is not None else None
        required_cagr_grid.append({"horizon_years": horizon, "required_cagr_pct": by_level})

    return {
        "reachable": reachable,
        "years_to_target": years_scenario,  # scenario (monthly_contribution param)
        # R2-4: per-scenario years to target (same engine as CAGR grid)
        # scenario is None when monthly_contribution == 0 (same as zero column)
        # run_rate is None when run-rate is unavailable
        "years_to_target_by_scenario": {
            "zero": years_zero,
            "run_rate": years_run_rate,
            "scenario": years_scenario if monthly_contribution > 0 else None,
        },
        # Headline sub-dict: headline number and text must come from the same scenario
        "headline": {
            "years_to_target": headline_years,
            "contribution_monthly": headline_contribution,
            "scenario_used": headline_scenario_used,
        },
        "run_rate_monthly": run_rate_monthly,
        "run_rate_status": run_rate_status,
        "required_cagr_grid": required_cagr_grid,
        "assumptions": assumptions,
    }
