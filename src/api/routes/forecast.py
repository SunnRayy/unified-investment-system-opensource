"""Forecast & Planning API routes (R-2,
docs/plans/2026-07-25-forecast-planning-redesign.md).

Rule 12: the route body is wrapped in try/except -> api_error_response, so
an unhandled failure never degrades to a silent [] / {} + 200.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query

from src.api.dependencies import get_db
from src.api.routes._errors import api_error_response
from src.database.connector import DatabaseConnector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/forecast", tags=["Forecast"])


@router.get("/levers")
async def get_forecast_levers(
    db: DatabaseConnector = Depends(get_db),
    savings_pct: Optional[float] = Query(
        None, ge=0, le=60,
        description="W-2: % increase over the current run-rate (slider position, step 5).",
    ),
    return_pp: Optional[float] = Query(
        None, ge=0, le=6,
        description="W-2: percentage points added to expected return (slider position, step 0.5).",
    ),
    volatility_pp: Optional[float] = Query(
        None, ge=0, le=10,
        description="W-2: percentage points subtracted from volatility (slider position, step 0.5).",
    ),
):
    """R-2 — base case + sensitivity grid over savings / expected return /
    volatility, answering "what do I change to reach the goal sooner?".

    All three query params are OPTIONAL (W-2,
    docs/plans/2026-07-26-your-path-design-implementation.md §4.3). Omitted
    -> the response is byte-for-byte identical to the pre-W-2 shape. When
    supplied, out-of-range values 422 here (FastAPI Query validation); values
    in range are additionally clamped server-side inside compute_levers and
    echoed back in the response's "applied" block.

    Read-only. See src.services.forecast_levers.compute_levers for the full
    derivation of every input (all live, none hardcoded)."""
    from src.services.forecast_levers import compute_levers

    try:
        return compute_levers(
            db,
            savings_pct=savings_pct,
            return_pp=return_pp,
            volatility_pp=volatility_pp,
        )
    except Exception as e:
        logger.exception("get_forecast_levers failed")
        return api_error_response(e, context="forecast_levers")
