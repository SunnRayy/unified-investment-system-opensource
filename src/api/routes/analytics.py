"""Analytics API endpoints — Phase 6 Advanced Analytics."""
import logging
from fastapi import APIRouter, Depends, Query
from typing import Optional
from src.api.dependencies import get_db, get_writable_db
from src.database.connector import DatabaseConnector
from src.storage.gcs_flush import mark_dirty
from src.api.routes._errors import api_error_response

router = APIRouter(prefix="/analytics", tags=["Analytics"])
logger = logging.getLogger(__name__)


@router.get("/projection")
async def get_projection(
    years: int = Query(default=10, ge=1, le=50),
    simulations: int = Query(default=1000, ge=100, le=10000),
    annual_return: float = Query(default=0.07, ge=-0.5, le=0.5),
    annual_volatility: float = Query(default=0.15, ge=0.01, le=1.0),
    annual_contribution: float = Query(default=0.0, ge=0.0),
    goal_target: Optional[float] = Query(default=None, ge=0.0),
    include_non_rebalanceable: bool = Query(default=False),
    seed: int = Query(default=42, ge=0),
    db: DatabaseConnector = Depends(get_db),
):
    """Monte Carlo portfolio projection.

    Returns percentile bands (p10/p25/p50/p75/p90) over the projection horizon.
    """
    from src.financial_analysis.monte_carlo import calculate_portfolio_projection

    try:
        result = calculate_portfolio_projection(
            db,
            years=years,
            num_simulations=simulations,
            annual_return=annual_return,
            annual_volatility=annual_volatility,
            annual_contribution=annual_contribution,
            goal_target=goal_target,
            include_non_rebalanceable=include_non_rebalanceable,
            seed=seed,
        )
        return result
    except Exception as e:
        return api_error_response(e, context="projection")


@router.get("/projection/defaults")
async def get_projection_defaults(
    db: DatabaseConnector = Depends(get_db),
):
    """Return historically-derived defaults for the Monte Carlo simulation.

    Mirrors the Performance page filter logic (exclude_non_balanceable=True) so
    the numbers match what the user sees on the Performance / Risk Metrics page.

    Computes:
    - suggested_return: annualized TWR on rebalanceable assets (decimal, e.g. 0.09)
    - suggested_volatility: historical annualized volatility, rebalanceable only (decimal)
    - avg_monthly_investment_12m: avg monthly new money from income_expense_monthly (CNY)
    - avg_monthly_investment_36m: same over 36 months
    - suggested_contribution_run_rate: the SAME run-rate North Star's glide path
      uses — (net_external_ttm + rsu_retained_ttm) / 12, from
      `src.services.north_star_glide._contribution_run_rate`. None when that
      function's status is not "available" (e.g. no income_expense_monthly
      data, or the sanity guard fired). Decision 3,
      docs/plans/2026-07-25-cash-flow-classification-completion.md.

    TWR and run-rate are delegated to `src.financial_analysis.projection_defaults`
    (single source of truth shared with the North Star glide-path engine).

    Window anchor (fixed 2026-07-25, owner UI review BUG 4a): both windows are
    anchored to the latest DATA month in `income_expense_monthly`, never to
    `date.today()`. ADR-025 §2 established that the FS Excel ledger lags real
    time by 1-2 months, so "today minus 12 calendar months" does not line up
    with the months actually present in the data — it was silently dropping
    the most recent, most relevant months. Reuses
    `contributions_summary_v2`'s own window derivation ("last N distinct
    months present in the series") via its returned `window_start_month`,
    rather than inventing a second windowing convention.
    """
    from src.financial_analysis.metrics import calculate_portfolio_metrics
    from src.api.routes.performance import fetch_included_asset_ids
    from src.financial_analysis.projection_defaults import (
        suggested_return_basis,
        avg_monthly_investment as _avg_monthly_investment,
    )
    from src.services.investment_contributions import contributions_summary_v2

    suggested_return = None
    try:
        val = suggested_return_basis(db)
        if val is not None:
            suggested_return = round(val, 4)
    except Exception:
        pass

    suggested_volatility = None
    try:
        # Volatility is not part of the shared helper (only used by Monte Carlo).
        include_ids = fetch_included_asset_ids(db, start_date=None)
        metrics = calculate_portfolio_metrics(
            db, include_asset_ids=include_ids, exclude_non_balanceable=True
        )
        if metrics and metrics.get("volatility_annual") is not None:
            suggested_volatility = round(float(metrics["volatility_annual"]) / 100, 4)
    except Exception:
        pass

    window_12m_start = contributions_summary_v2(db, window_months=12)["window_start_month"]
    window_36m_start = contributions_summary_v2(db, window_months=36)["window_start_month"]
    avg_12m = _avg_monthly_investment(db, f"{window_12m_start}-01") if window_12m_start else 0.0
    avg_36m = _avg_monthly_investment(db, f"{window_36m_start}-01") if window_36m_start else 0.0

    # Decision 3: reuse the glide-path's own run-rate rather than duplicating
    # its formula. Function-local import to avoid a module-load-time cycle
    # with src.services.north_star_glide (established pattern in this file —
    # see the other service imports above, all function-local).
    from src.services.north_star_glide import _contribution_run_rate

    suggested_contribution_run_rate = None
    try:
        run_rate_value, run_rate_status = _contribution_run_rate(db)
        if run_rate_status == "available":
            suggested_contribution_run_rate = run_rate_value
    except Exception:
        # Never a silent empty success — log and surface None, the same
        # "unavailable" contract callers already handle for a not-"available"
        # status. The endpoint itself must still return 200 with the other
        # (unrelated) defaults intact.
        logger.warning(
            "get_projection_defaults: _contribution_run_rate raised", exc_info=True
        )

    return {
        "suggested_return": suggested_return,
        "suggested_volatility": suggested_volatility,
        "avg_monthly_investment_12m": avg_12m,
        "avg_monthly_investment_36m": avg_36m,
        "suggested_contribution_run_rate": suggested_contribution_run_rate,
    }


@router.get("/cashflow-trends")
async def get_cashflow_trends(db: DatabaseConnector = Depends(get_db)):
    """Cash flow analysis: monthly income/expense trends."""
    from src.financial_analysis.cash_flow import get_cash_flow_analysis

    try:
        return get_cash_flow_analysis(db)
    except Exception as e:
        return api_error_response(e, context="cashflow-trends")


@router.get("/cashflow-forecast")
async def get_cashflow_forecast(
    months: int = Query(default=6, ge=1, le=60),
    db: DatabaseConnector = Depends(get_db),
):
    """Forecast future monthly income and expenses."""
    from src.financial_analysis.forecaster import get_cash_flow_forecast

    try:
        return get_cash_flow_forecast(db, months=months)
    except Exception as e:
        return api_error_response(e, context="cashflow-forecast")


from pydantic import BaseModel
from datetime import date as Date

class GoalCreate(BaseModel):
    name: str
    target_amount: float
    target_date: Date
    current_amount: Optional[float] = 0.0
    monthly_contribution: Optional[float] = 0.0
    goal_type: Optional[str] = "other"
    notes: Optional[str] = None


class GoalUpdate(BaseModel):
    """PUT /goals/{id} payload. Partial update — omitted fields unchanged.

    Deliberately excludes current_amount / monthly_contribution: those are
    now live-derived (see src/financial_analysis/goals.py Goal docstring),
    not stored user intent, so there is nothing for the owner to "edit" —
    exposing them here would recreate the two-sources-of-truth bug this
    endpoint exists to fix.
    """
    name: Optional[str] = None
    target_amount: Optional[float] = None
    target_date: Optional[Date] = None
    goal_type: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


def _serialize_goal(g) -> dict:
    return {
        "id": g.id,
        "name": g.name,
        "target_amount": float(g.target_amount),
        "target_date": str(g.target_date),
        "current_amount": float(g.current_amount),
        "monthly_contribution": float(g.monthly_contribution),
        "goal_type": g.goal_type.value if hasattr(g.goal_type, "value") else str(g.goal_type),
        "status": g.status.value if hasattr(g.status, "value") else str(g.status),
        "notes": g.notes,
        "created_at": str(g.created_at) if g.created_at else None,
        "months_remaining": g.months_remaining,
    }


@router.get("/goals")
async def get_goals(db: DatabaseConnector = Depends(get_db)):
    """List all financial goals.

    Each goal's `current_amount` / `monthly_contribution` (top-level, on the
    Goal row) are LEGACY — static columns written once at creation, kept
    only for backward compatibility. They are NOT authoritative: do not use
    them for display, PROGRESS, or probability.

    The `live` block is the single source of truth, reusing the exact
    functions the "Your Path" tab uses, so the two tabs can never disagree
    (owner-reported defect, 2026-07-26 — the Goals card and Your Path
    previously showed different current/monthly numbers for the same
    portfolio):
      - live.current_amount        = src.services.north_star_glide._default_net_worth(db)
      - live.monthly_contribution  = src.services.north_star_glide._contribution_run_rate(db)
      - live.run_rate_status       = "available" | "no contribution data available"
                                      | "run-rate implausible — check flow tagging"
    `live.monthly_contribution` is None whenever run_rate_status != "available"
    — never fabricated as 0. These are portfolio-level facts (not per-goal),
    so every goal in the list shares the same `live` block.
    """
    from src.financial_analysis.goals import list_goals
    from src.services.north_star_glide import _contribution_run_rate, _default_net_worth
    try:
        goals = list_goals(db)

        live_current = _default_net_worth(db)
        live_monthly, run_rate_status = _contribution_run_rate(db)
        live_block = {
            "current_amount": round(live_current, 2),
            "monthly_contribution": round(live_monthly, 2) if live_monthly is not None else None,
            "run_rate_status": run_rate_status,
        }

        # Serialize explicitly so the @property months_remaining is included
        # (Python dataclass @properties are not auto-serialized by FastAPI/Pydantic)
        return [
            {**_serialize_goal(g), "live": live_block}
            for g in goals
        ]
    except Exception as e:
        return api_error_response(e, context="goals")


@router.post("/goals")
async def create_new_goal(goal: GoalCreate, db: DatabaseConnector = Depends(get_writable_db)):
    """Create a new financial goal."""
    from src.financial_analysis.goals import create_goal
    try:
        result = create_goal(db, goal.model_dump())
        mark_dirty()
        return result
    except Exception as e:
        return api_error_response(e, context="create-goal")


@router.put("/goals/{goal_id}")
async def update_existing_goal(
    goal_id: int, goal: GoalUpdate, db: DatabaseConnector = Depends(get_writable_db)
):
    """Update a goal's editable fields.

    Editable: name, target_amount, target_date, goal_type, status, notes.
    NOT editable: current_amount, monthly_contribution — those are now
    live-derived (see GET /analytics/goals `live` block); there is no
    stored value for the owner to edit. See GoalUpdate docstring.
    """
    from src.financial_analysis.goals import get_goal, update_goal
    try:
        existing = get_goal(db, goal_id)
        if not existing:
            # Rule 12: a missing resource is a 404, never a 200 carrying an
            # {"error": ...} body the frontend would render as success.
            return api_error_response(
                ValueError(f"goal {goal_id} not found"),
                context="update-goal-not-found",
                status_code=404,
            )

        updated = update_goal(db, goal_id, goal.model_dump(exclude_unset=True))
        mark_dirty()
        return _serialize_goal(updated)
    except ValueError as e:
        return api_error_response(e, context="update-goal", status_code=422)
    except Exception as e:
        return api_error_response(e, context="update-goal")


@router.delete("/goals/{goal_id}")
async def delete_existing_goal(goal_id: int, db: DatabaseConnector = Depends(get_writable_db)):
    """Delete a goal."""
    from src.financial_analysis.goals import delete_goal
    try:
        success = delete_goal(db, goal_id)
        if success:
            mark_dirty()
        return {"success": success}
    except Exception as e:
        return api_error_response(e, context="delete-goal")


@router.get("/goals/{goal_id}/probability")
async def get_goal_probability(
    goal_id: int,
    annual_return: Optional[float] = Query(default=None),
    annual_volatility: Optional[float] = Query(default=None),
    db: DatabaseConnector = Depends(get_db),
):
    """Calculate probability of reaching a specific goal.

    Fixed 2026-07-26 (owner-reported defect): this used to default
    annual_return/annual_volatility to hardcoded 0.07/0.15 and read the
    goal row's frozen current_amount/monthly_contribution columns — four
    inputs independent of what "Your Path" (the same page) showed for the
    same portfolio. All four now come from the SAME live functions/derivation
    Your Path and forecast_levers.compute_levers use — see that module's
    docstring, which this reuses rather than re-deriving:
      - current_amount        -> north_star_glide._default_net_worth(db)
      - monthly_contribution  -> north_star_glide._contribution_run_rate(db)
      - annual_return default -> projection_defaults.suggested_return_basis(db)
      - annual_volatility default -> calculate_portfolio_metrics(db, ...)
        ["volatility_annual"] / 100.0 (same call + unit convention as
        GET /analytics/projection/defaults and compute_levers)
    Explicit query params still override the live defaults.

    Never fabricates: if the live return, volatility, or run-rate is
    unavailable, returns {"probability": None, "status": "unavailable",
    "reason": ...} instead of silently falling back to a made-up constant.
    """
    from src.api.routes.performance import fetch_included_asset_ids
    from src.financial_analysis.goals import get_goal, calculate_goal_probability
    from src.financial_analysis.metrics import calculate_portfolio_metrics
    from src.financial_analysis.projection_defaults import suggested_return_basis
    from src.services.north_star_glide import _contribution_run_rate, _default_net_worth

    try:
        goal = get_goal(db, goal_id)
        if not goal:
            # Rule 12 (pre-existing 200-with-error-body, fixed while here).
            return api_error_response(
                ValueError(f"goal {goal_id} not found"),
                context="goal-probability-not-found",
                status_code=404,
            )

        live_current = _default_net_worth(db)
        run_rate_monthly, run_rate_status = _contribution_run_rate(db)

        if annual_return is None:
            annual_return = suggested_return_basis(db)

        if annual_volatility is None:
            try:
                include_ids = fetch_included_asset_ids(db, start_date=None)
                metrics = calculate_portfolio_metrics(
                    db, include_asset_ids=include_ids, exclude_non_balanceable=True
                )
                if metrics and metrics.get("volatility_annual") is not None:
                    annual_volatility = float(metrics["volatility_annual"]) / 100.0
            except Exception:
                annual_volatility = None

        if annual_return is None or annual_volatility is None or run_rate_monthly is None:
            reason = (
                "expected return unavailable" if annual_return is None else
                "volatility unavailable" if annual_volatility is None else
                run_rate_status
            )
            return {"goal_id": goal_id, "probability": None, "status": "unavailable", "reason": reason}

        prob = calculate_goal_probability(
            current_amount=live_current,
            target_amount=goal.target_amount,
            years=goal.months_remaining / 12,
            monthly_contribution=run_rate_monthly,
            annual_return=annual_return,
            annual_volatility=annual_volatility,
        )
        return {"goal_id": goal_id, "probability": prob}
    except Exception as e:
        return api_error_response(e, context="goal-probability")
