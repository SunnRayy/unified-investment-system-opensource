"""Monthly Attribution API routes (Attribution & Flows Program WS-1).

Contract: docs/api-specs/attribution.md. Rule 12: every route wrapped in
try/except -> api_error_response, no silent []-with-200.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.dependencies import get_db
from src.api.routes._errors import api_error_response
from src.database.connector import DatabaseConnector
from src.services.attribution import (
    HISTORY_FLOOR_MONTH,
    compute_range,
    get_asset_history,
    get_monthly,
    get_summary,
)
from src.storage.gcs_flush import mark_dirty

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attribution", tags=["Attribution"])

_VALID_LEVELS = ("asset", "sub_class", "top_class", "total")


def _open_writable(db: DatabaseConnector) -> DatabaseConnector:
    """Return a writable DB connection, closing the read-only one if needed."""
    if getattr(db, "read_only", False):
        db_path = db.db_path
        db.close()
        return DatabaseConnector(db_path, read_only=False)
    return db


def _parse_month(month_str: str) -> date:
    """Parse 'YYYY-MM' -> first-of-month date. Raises ValueError on bad input."""
    return datetime.strptime(month_str, "%Y-%m").date().replace(day=1)


class RecomputeRequest(BaseModel):
    months: int = 6


@router.get("/monthly")
async def get_attribution_monthly(
    month: str = Query(..., description="YYYY-MM"),
    month_to: str = Query(default=None, description="YYYY-MM, inclusive — aggregates [month, month_to]"),
    level: str = Query(default="sub_class"),
    include_non_rebalanceable: bool = Query(default=True),
    db: DatabaseConnector = Depends(get_db),
):
    try:
        if level not in _VALID_LEVELS:
            return api_error_response(
                ValueError(f"invalid level '{level}'"), context="attribution_monthly_level", status_code=400
            )
        try:
            month_date = _parse_month(month)
        except ValueError as e:
            return api_error_response(e, context="attribution_monthly_month", status_code=400)
        if month_date < HISTORY_FLOOR_MONTH:
            return api_error_response(
                ValueError(f"month before history floor {HISTORY_FLOOR_MONTH.isoformat()}"),
                context="attribution_monthly_history_floor",
                status_code=400,
            )

        month_to_date = None
        if month_to is not None:
            try:
                month_to_date = _parse_month(month_to)
            except ValueError as e:
                return api_error_response(e, context="attribution_monthly_month_to", status_code=400)
            if month_to_date < HISTORY_FLOOR_MONTH:
                return api_error_response(
                    ValueError(f"month_to before history floor {HISTORY_FLOOR_MONTH.isoformat()}"),
                    context="attribution_monthly_history_floor",
                    status_code=400,
                )
            if month_to_date < month_date:
                return api_error_response(
                    ValueError(f"month_to '{month_to}' is before month '{month}'"),
                    context="attribution_monthly_month_to_reversed",
                    status_code=400,
                )

        return get_monthly(
            db, month_date, level=level, include_non_rebalanceable=include_non_rebalanceable,
            month_to=month_to_date,
        )
    except Exception as e:
        logger.exception("get_attribution_monthly failed")
        return api_error_response(e, context="attribution_monthly")


@router.get("/asset/{asset_id}")
async def get_attribution_asset(
    asset_id: str,
    months: int = Query(default=6, ge=1, le=18),
    db: DatabaseConnector = Depends(get_db),
):
    try:
        result = get_asset_history(db, asset_id, months=months)
        if result is None:
            return api_error_response(
                ValueError(f"asset '{asset_id}' not found in attribution history"),
                context="attribution_asset_not_found",
                status_code=404,
            )
        return result
    except Exception as e:
        logger.exception("get_attribution_asset failed")
        return api_error_response(e, context="attribution_asset")


@router.get("/summary")
async def get_attribution_summary(
    months: int = Query(default=12, ge=1, le=120),
    db: DatabaseConnector = Depends(get_db),
):
    try:
        return get_summary(db, months=months)
    except Exception as e:
        logger.exception("get_attribution_summary failed")
        return api_error_response(e, context="attribution_summary")


@router.post("/recompute")
async def recompute_attribution(body: RecomputeRequest, db: DatabaseConnector = Depends(get_db)):
    writable = None
    try:
        if body.months < 1:
            return api_error_response(
                ValueError("months must be >= 1"), context="attribution_recompute_months", status_code=400
            )
        writable = _open_writable(db)
        today = date.today().replace(day=1)
        start_month = today
        for _ in range(body.months - 1):
            start_month = date(start_month.year - 1, 12, 1) if start_month.month == 1 else date(
                start_month.year, start_month.month - 1, 1
            )
        if start_month < HISTORY_FLOOR_MONTH:
            start_month = HISTORY_FLOOR_MONTH
        summaries = compute_range(writable, start_month, today)
        mark_dirty()
        return {
            "months_recomputed": len(summaries),
            "rows": summaries,
            "dq_total": sum(s["dq_count"] for s in summaries),
        }
    except Exception as e:
        logger.exception("recompute_attribution failed")
        return api_error_response(e, context="attribution_recompute", status_code=500)
    finally:
        if writable and writable is not db:
            writable.close()
