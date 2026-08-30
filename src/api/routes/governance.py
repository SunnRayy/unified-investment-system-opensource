"""Metric governance API routes (PRD 2026-07-07 F4.3/F4.4/F4.6, Batch B5).

Rule 12: every route body is wrapped in try/except -> api_error_response, so
an unhandled failure never degrades to a silent [] + 200.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.dependencies import get_db
from src.api.routes._errors import api_error_response
from src.database.connector import DatabaseConnector
from src.services.metric_governance import get_metrics_overview
from src.storage.gcs_flush import mark_dirty

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/governance", tags=["Metric Governance"])

_VALID_STATUSES = ("open", "done", "wontfix")

_COLUMNS = (
    "id", "title", "description", "metric_key", "opened_at", "due_at",
    "status", "closed_at",
)


def _open_writable(db: DatabaseConnector) -> DatabaseConnector:
    if getattr(db, "read_only", False):
        db_path = db.db_path
        db.close()
        return DatabaseConnector(db_path, read_only=False)
    return db


def _row_to_dict(row: tuple) -> dict:
    d = dict(zip(_COLUMNS, row))
    for key in ("opened_at", "due_at", "closed_at"):
        if d.get(key) is not None:
            d[key] = str(d[key])
    return d


class CreateDataFixRequest(BaseModel):
    title: str
    description: Optional[str] = None
    metric_key: Optional[str] = None
    due_at: Optional[str] = None


class UpdateDataFixRequest(BaseModel):
    status: str


@router.get("/metrics")
async def get_metrics(db: DatabaseConnector = Depends(get_db)):
    """metric_catalog overview with per-metric open/overdue data_fixes counts."""
    try:
        return get_metrics_overview(db)
    except Exception as e:
        logger.exception("get_metrics failed")
        return api_error_response(e, context="governance_metrics")


@router.get("/data-fixes")
async def list_data_fixes(
    status: str = Query(default="open", pattern="^(open|overdue|done|wontfix|all)$"),
    db: DatabaseConnector = Depends(get_db),
):
    """List data_fixes. 'overdue' = status='open' AND due_at < now, sorted
    due_at ASC. Response includes {overdue_count} regardless of the filter."""
    try:
        now = datetime.now()

        where = ""
        params: list = []
        if status == "open":
            where = "WHERE status = 'open'"
        elif status == "done":
            where = "WHERE status = 'done'"
        elif status == "wontfix":
            where = "WHERE status = 'wontfix'"
        elif status == "overdue":
            where = "WHERE status = 'open' AND due_at < ?"
            params = [now]
        # 'all' -> no filter

        rows = db.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM data_fixes {where} ORDER BY due_at ASC",
            params,
        ).fetchall()

        overdue_count = db.execute(
            "SELECT COUNT(*) FROM data_fixes WHERE status = 'open' AND due_at < ?", [now]
        ).fetchone()[0]

        items = [_row_to_dict(row) for row in rows]
        return {"items": items, "overdue_count": int(overdue_count)}
    except Exception as e:
        logger.exception("list_data_fixes failed")
        return api_error_response(e, context="list_data_fixes")


def _default_due_at(db: DatabaseConnector, metric_key: Optional[str], now: datetime) -> datetime:
    """PRD F4.6: default due_at by metric freshness class — fast=7d, slow=30d
    (also the default when metric_key is missing/unknown)."""
    freshness_class = "slow"
    if metric_key:
        row = db.execute(
            "SELECT freshness_class FROM metric_catalog WHERE metric_key = ?", [metric_key]
        ).fetchone()
        if row and row[0]:
            freshness_class = str(row[0]).lower()
    return now + (timedelta(days=7) if freshness_class == "fast" else timedelta(days=30))


@router.post("/data-fixes")
async def create_data_fix(
    body: CreateDataFixRequest,
    db: DatabaseConnector = Depends(get_db),
):
    """Create a data_fix. due_at is NEVER null: an explicit due_at must parse
    as ISO-8601 (422 if not); otherwise it defaults from metric freshness
    (fast -> now+7d, else now+30d) (PRD F4.6)."""
    writable = None
    try:
        now = datetime.now()
        if body.due_at is not None:
            try:
                due_at = datetime.fromisoformat(body.due_at)
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail=f"due_at must be ISO-8601, got {body.due_at!r}",
                )
        else:
            due_at = _default_due_at(db, body.metric_key, now)

        writable = _open_writable(db)
        writable.execute(
            """
            INSERT INTO data_fixes (title, description, metric_key, opened_at, due_at, status)
            VALUES (?, ?, ?, ?, ?, 'open')
            """,
            [body.title, body.description, body.metric_key, now, due_at],
        )
        mark_dirty()

        row = writable.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM data_fixes ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _row_to_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("create_data_fix failed")
        return api_error_response(e, context="create_data_fix")
    finally:
        if writable and writable is not db:
            writable.close()


@router.put("/data-fixes/{fix_id}")
async def update_data_fix(
    fix_id: int,
    body: UpdateDataFixRequest,
    db: DatabaseConnector = Depends(get_db),
):
    """Update a data_fix's status. 404 unknown id; 422 bad status. Setting
    status to done/wontfix stamps closed_at; reopening to 'open' clears it."""
    if body.status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {_VALID_STATUSES}, got {body.status!r}",
        )

    writable = None
    try:
        writable = _open_writable(db)
        existing = writable.execute(
            "SELECT id FROM data_fixes WHERE id = ?", [fix_id]
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail=f"data_fix {fix_id} not found")

        closed_at = datetime.now() if body.status in ("done", "wontfix") else None
        writable.execute(
            "UPDATE data_fixes SET status = ?, closed_at = ? WHERE id = ?",
            [body.status, closed_at, fix_id],
        )
        mark_dirty()

        row = writable.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM data_fixes WHERE id = ?", [fix_id]
        ).fetchone()
        return _row_to_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_data_fix failed")
        return api_error_response(e, context="update_data_fix")
    finally:
        if writable and writable is not db:
            writable.close()
