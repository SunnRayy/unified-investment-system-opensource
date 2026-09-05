"""North Star panel API routes (PRD 2026-07-07 F3, Batch B6).

Rule 12: every route body is wrapped in try/except -> api_error_response, so
an unhandled failure never degrades to a silent [] + 200.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.dependencies import get_db
from src.api.routes._errors import api_error_response
from src.database.connector import DatabaseConnector
from src.services.north_star import (
    classify_flows_heuristic,
    contributions_summary,
    create_unforced_error,
    list_classified_flows,
    list_unclassified_flows,
    list_unforced_errors,
    north_star_panel,
    tag_flow_manual,
    tag_flows_bulk,
    untag_flows,
    update_unforced_error_cost,
)
from src.storage.gcs_flush import mark_dirty

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/north-star", tags=["North Star"])

_VALID_CLASSIFICATIONS = ("external_contribution", "internal_transfer", "income_reinvested")
# 'fs_cash_delta' added 2026-07-25: tag_flow_manual had supported it since
# V7.6.0, but this allowlist omitted it, so the UI's per-row Tag action 422'd on
# every FS-cash row. Full story: test_flow_tag_accepts_fs_cash_delta_source_table.
_VALID_SOURCE_TABLES = ("transactions", "income_expense_monthly", "fs_cash_delta")

# "All Time" window for GET /contributions — deliberately larger than any
# realistic income_expense_monthly history (78 months live as of 2026-07).
# See _resolve_contributions_window() for why this is honest, not a hack.
_CONTRIBUTIONS_ALL_HISTORY_MONTHS = 100_000


def _open_writable(db: DatabaseConnector) -> DatabaseConnector:
    """Return a writable DB connection, closing the read-only one if needed."""
    if getattr(db, "read_only", False):
        db_path = db.db_path
        db.close()
        return DatabaseConnector(db_path, read_only=False)
    return db


class FlowTagRequest(BaseModel):
    source_table: str
    source_row_key: str
    classification: str
    note: Optional[str] = None


class UnforcedErrorRequest(BaseModel):
    error_date: str
    description: str
    est_cost_cny: Optional[float] = None
    root_cause: Optional[str] = None
    linked_rule: Optional[str] = None


class FlowTagBulkItem(BaseModel):
    source_table: str
    source_row_key: str


class FlowTagBulkRequest(BaseModel):
    items: list[FlowTagBulkItem]
    classification: str


class FlowUntagRequest(BaseModel):
    items: list[FlowTagBulkItem]


class RevertClassifyRequest(BaseModel):
    ids: list[int]


class UnforcedErrorCostPatch(BaseModel):
    est_cost_cny: Optional[float] = None


@router.get("/panel")
async def get_north_star_panel(
    monthly_contribution: float = Query(default=0.0),
    db: DatabaseConnector = Depends(get_db),
):
    """F3.5 — the composed North Star block (contributions, time-in-market,
    unforced errors, glide path). Also the quarterly-report data source."""
    try:
        return north_star_panel(db, monthly_contribution=monthly_contribution)
    except Exception as e:
        logger.exception("get_north_star_panel failed")
        return api_error_response(e, context="north_star_panel")


@router.post("/flows/classify")
async def run_flow_classification(
    dry_run: bool = Query(default=False),
    db: DatabaseConnector = Depends(get_db),
):
    """Run the F3.1 heuristic classification pass now. dry_run=true returns
    would_tag count without writing."""
    if dry_run:
        try:
            summary = classify_flows_heuristic(db, dry_run=True)
            return summary
        except Exception as e:
            logger.exception("run_flow_classification dry_run failed")
            return api_error_response(e, context="north_star_classify_dry_run")
    writable = None
    try:
        writable = _open_writable(db)
        summary = classify_flows_heuristic(writable, dry_run=False)
        mark_dirty()
        return summary
    except Exception as e:
        logger.exception("run_flow_classification failed")
        return api_error_response(e, context="north_star_classify")
    finally:
        if writable and writable is not db:
            writable.close()


@router.post("/flows/classify/revert")
async def revert_flow_classification(body: RevertClassifyRequest, db: DatabaseConnector = Depends(get_db)):
    """Revert specific heuristic-tagged rows (for Undo after auto-classify)."""
    if not body.ids:
        return {"deleted": 0}
    writable = None
    try:
        writable = _open_writable(db)
        placeholders = ", ".join("?" for _ in body.ids)
        rows_to_delete = writable.execute(
            f"SELECT id FROM cash_flow_tags WHERE id IN ({placeholders}) AND tagged_by = 'heuristic'",
            body.ids,
        ).fetchall()
        count = len(rows_to_delete)
        if count > 0:
            writable.execute(
                f"DELETE FROM cash_flow_tags WHERE id IN ({placeholders}) AND tagged_by = 'heuristic'",
                body.ids,
            )
            mark_dirty()
        return {"deleted": count}
    except Exception as e:
        logger.exception("revert_flow_classification failed")
        return api_error_response(e, context="north_star_revert_classify")
    finally:
        if writable and writable is not db:
            writable.close()


@router.get("/flows/unclassified")
async def get_unclassified_flows(db: DatabaseConnector = Depends(get_db)):
    """Candidate flow rows with no cash_flow_tags entry yet (tagging UI)."""
    try:
        return list_unclassified_flows(db)
    except Exception as e:
        logger.exception("get_unclassified_flows failed")
        return api_error_response(e, context="north_star_unclassified")


@router.get("/flows/classified")
async def get_classified_flows(
    classification: Optional[str] = Query(default=None),
    db: DatabaseConnector = Depends(get_db),
):
    """Already-tagged cash_flow_tags rows, newest flow_date first.

    Optional ?classification= filter (external_contribution | internal_transfer |
    income_reinvested). Returns 422 for an unrecognised classification value.
    """
    if classification is not None and classification not in _VALID_CLASSIFICATIONS:
        raise HTTPException(
            status_code=422,
            detail=f"classification must be one of {_VALID_CLASSIFICATIONS}, got {classification!r}",
        )
    try:
        return list_classified_flows(db, classification=classification)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("get_classified_flows failed")
        return api_error_response(e, context="north_star_classified")


@router.put("/flows/tag/bulk")
async def put_flow_tag_bulk(body: FlowTagBulkRequest, db: DatabaseConnector = Depends(get_db)):
    """Bulk manual tag upsert. tagged_by='manual' for every item (D6).

    Body: {"items": [{"source_table": str, "source_row_key": str}], "classification": str}
    Returns: {"tagged": n, "not_found": m} — rows whose source is missing are
    counted in not_found (not a hard error, since the UI may race with deletions).
    """
    if body.classification not in _VALID_CLASSIFICATIONS:
        raise HTTPException(
            status_code=422,
            detail=f"classification must be one of {_VALID_CLASSIFICATIONS}, got {body.classification!r}",
        )
    if not body.items:
        return {"tagged": 0, "not_found": 0}

    items_dicts = [{"source_table": it.source_table, "source_row_key": it.source_row_key} for it in body.items]
    writable = None
    try:
        writable = _open_writable(db)
        result = tag_flows_bulk(writable, items_dicts, body.classification)
        mark_dirty()
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("put_flow_tag_bulk failed")
        return api_error_response(e, context="north_star_tag_bulk")
    finally:
        if writable and writable is not db:
            writable.close()


@router.delete("/flows/tag")
async def delete_flow_tags(body: FlowUntagRequest, db: DatabaseConnector = Depends(get_db)):
    """Remove cash_flow_tags rows for the given (source_table, source_row_key) pairs.

    Scoped delete on the overlay table only — never touches transactions or
    income_expense_monthly. Returns {"deleted": n}.
    """
    if not body.items:
        return {"deleted": 0}

    items_dicts = [{"source_table": it.source_table, "source_row_key": it.source_row_key} for it in body.items]
    writable = None
    try:
        writable = _open_writable(db)
        result = untag_flows(writable, items_dicts)
        mark_dirty()
        return result
    except Exception as e:
        logger.exception("delete_flow_tags failed")
        return api_error_response(e, context="north_star_untag")
    finally:
        if writable and writable is not db:
            writable.close()


_CONTRIBUTIONS_WINDOW_VALUES = {"12": 12, "36": 36, "all": _CONTRIBUTIONS_ALL_HISTORY_MONTHS}


def _resolve_contributions_window(window_months: str) -> int:
    """Map the query-param string to an actual trailing-window month count.

    'all' maps to a window (_CONTRIBUTIONS_ALL_HISTORY_MONTHS) deliberately
    larger than any realistic income_expense_monthly history — Python list
    slicing (`series[-window_months:]`) is a no-op past the list length, so
    this covers the FULL ledger without any special-casing in
    contributions_summary_v2(), and window_start_month/window_end_month in
    the response still reflect the true first/last data month (honest, never
    hardcoded). Raises ValueError for anything else, turned into a 400 by
    the caller — never silently falls back to a different window.
    """
    key = (window_months or "12").strip().lower()
    if key not in _CONTRIBUTIONS_WINDOW_VALUES:
        raise ValueError(f"window_months must be one of '12', '36', 'all' (got {window_months!r})")
    return _CONTRIBUTIONS_WINDOW_VALUES[key]


@router.get("/contributions")
async def get_contributions(
    window_months: str = Query("12", description="Trailing window for investment.*/rsu.*: '12', '36', or 'all'"),
    db: DatabaseConnector = Depends(get_db),
):
    """Cash-flow contribution metrics for the Cash Flow tab.

    Returns ytd_sum, trailing_12m_sum, unclassified_count, and a
    by_classification breakdown (trailing-12M sums, consistent with
    trailing_12m_sum) — these are the legacy tag-based figures and are NOT
    affected by window_months (ADR-025 §4a: retired from display, fixed
    trailing-12M/YTD always). window_months controls only investment.* and
    rsu.* (which reads its window off investment.*, see
    north_star_flows.contributions_summary docstring) — the Cash Flow tab's
    Last 12m / 36m / All Time toggle. Reuses contribution_metrics() +
    per-classification SQL.
    """
    try:
        window = _resolve_contributions_window(window_months)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        return contributions_summary(db, window_months=window)
    except Exception as e:
        logger.exception("get_contributions failed")
        return api_error_response(e, context="north_star_contributions")


@router.put("/flows/tag")
async def put_flow_tag(body: FlowTagRequest, db: DatabaseConnector = Depends(get_db)):
    """Manual tag upsert. Always tagged_by='manual' — never overwritten by
    a later heuristic run (D6)."""
    if body.classification not in _VALID_CLASSIFICATIONS:
        raise HTTPException(
            status_code=422,
            detail=f"classification must be one of {_VALID_CLASSIFICATIONS}, got {body.classification!r}",
        )
    if body.source_table not in _VALID_SOURCE_TABLES:
        raise HTTPException(
            status_code=422,
            detail=f"source_table must be one of {_VALID_SOURCE_TABLES}, got {body.source_table!r}",
        )

    writable = None
    try:
        writable = _open_writable(db)
        result = tag_flow_manual(
            writable, body.source_table, body.source_row_key, body.classification, body.note,
        )
        mark_dirty()
        return result
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("put_flow_tag failed")
        return api_error_response(e, context="north_star_tag")
    finally:
        if writable and writable is not db:
            writable.close()


@router.patch("/unforced-errors/{error_id}")
async def patch_unforced_error_cost(
    error_id: int,
    body: UnforcedErrorCostPatch,
    db: DatabaseConnector = Depends(get_db),
):
    """R2-7.5 — update est_cost_cny; appends to cost_edit_history."""
    writable = None
    try:
        writable = _open_writable(db)
        result = update_unforced_error_cost(writable, error_id, body.est_cost_cny)
        mark_dirty()
        return result
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("patch_unforced_error_cost failed")
        return api_error_response(e, context="north_star_patch_unforced_error")
    finally:
        if writable and writable is not db:
            writable.close()


@router.get("/unforced-errors")
async def get_unforced_errors(db: DatabaseConnector = Depends(get_db)):
    """F3.3 — unforced-error log, newest first."""
    try:
        return list_unforced_errors(db)
    except Exception as e:
        logger.exception("get_unforced_errors failed")
        return api_error_response(e, context="north_star_unforced_errors")


@router.post("/unforced-errors")
async def post_unforced_error(body: UnforcedErrorRequest, db: DatabaseConnector = Depends(get_db)):
    """F3.3 — log a new execution failure."""
    writable = None
    try:
        writable = _open_writable(db)
        result = create_unforced_error(
            writable,
            error_date=body.error_date,
            description=body.description,
            est_cost_cny=body.est_cost_cny,
            root_cause=body.root_cause,
            linked_rule=body.linked_rule,
        )
        mark_dirty()
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("post_unforced_error failed")
        return api_error_response(e, context="north_star_unforced_error_create")
    finally:
        if writable and writable is not db:
            writable.close()
