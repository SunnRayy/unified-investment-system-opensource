from __future__ import annotations

import json
import logging
import re
from datetime import date as _date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.storage.gcs_flush import mark_dirty

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strategy", tags=["Strategy"])

_REPORT_MAX_AGE_HOURS = 4
_STRATEGY_REPORT_VERSION = "2026-05-21-scope-v3"


def _extract_memo_title(content: str) -> Optional[str]:
    h1_match = re.search(r"^#\s+(.+)", content, re.MULTILINE)
    if h1_match:
        candidate = h1_match.group(1).strip()
        if len(re.sub(r"[^\w\u4e00-\u9fff]", "", candidate)) >= 2:
            return candidate[:60].strip()

    _SECTION_MARKERS = (
        r"(?:"
        r"制定日期|目标资产|核心思想|会议时间|审查触发|讨论时间|决策背景|执行背景"
        r"|[一二三四五六七八九十]、"
        r"|\n|。"
        r")"
    )
    meta_parts = re.split(_SECTION_MARKERS, content[:400], maxsplit=1)
    candidate = meta_parts[0].strip() if meta_parts else ""
    if 5 < len(candidate) <= 80:
        return candidate.strip()

    for line in content.splitlines():
        line = line.strip()
        if not line or line == "---" or not re.sub(r"[^\w\u4e00-\u9fff]", "", line):
            continue
        return line[:60].strip()

    fallback = content[:60].strip()
    return fallback or None


def _extract_bias(content: str) -> str:
    if any(kw in content for kw in ["\u9632\u5fa1", "\u4fdd\u5b88", "\u964d\u4ed3", "\u964d\u4f4e\u98ce\u9669", "\u907f\u9669"]):
        if any(kw in content for kw in ["\u8fdb\u653b", "\u52a0\u4ed3", "\u6269\u5f20"]):
            return "neutral"
        return "defensive"
    if any(kw in content for kw in ["\u8fdb\u653b", "\u52a0\u4ed3", "\u4e70\u5165", "\u6269\u5f20"]):
        return "offensive"
    return "neutral"


def _extract_directives(content: str) -> list[str]:
    directives: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        match = re.match(r"^[0-9]+\.\s+(.+)", line) or re.match(r"^[*-]\s+(.+)", line)
        if not match:
            continue
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", match.group(1)).strip()
        if len(text) < 6:
            continue
        directives.append(text[:200])
        if len(directives) >= 5:
            break
    return directives


def _row_to_report(row: tuple) -> dict:
    allocation_payload = json.loads(row[1]) if row[1] else {}
    return {
        "review_date": str(row[0]),
        "target_scope_alignment": allocation_payload.get("target_scope_alignment", {}),
        "target_scope_summary": allocation_payload.get("target_scope_summary", {}),
        "target_scope_alignment_status": allocation_payload.get("target_scope_alignment_status"),
        "uis_scope_alignment": allocation_payload.get("uis_scope_alignment", {}),
        "uis_scope_summary": allocation_payload.get("uis_scope_summary", {}),
        "uis_scope_alignment_status": allocation_payload.get("uis_scope_alignment_status"),
        "trading_frequency": json.loads(row[2]) if row[2] else {},
        "contrarian_score": float(row[3]) if row[3] is not None else None,
        "contrarian_details": json.loads(row[4]) if row[4] else {},
        "profile_discrepancies": json.loads(row[5]) if row[5] else {},
    }


def _fetch_behavioral_summary(db: DatabaseConnector) -> dict[str, dict]:
    try:
        rows = db.execute(
            """
            SELECT dimension, score, raw_value, computation_window_days, metadata_json, computed_at
            FROM ai_behavioral_log
            WHERE (dimension, computed_at) IN (
                SELECT dimension, MAX(computed_at)
                FROM ai_behavioral_log
                GROUP BY dimension
            )
            ORDER BY dimension
            """
        ).fetchall()
    except Exception:
        return {}

    summary: dict[str, dict] = {}
    for row in rows:
        metadata = {}
        try:
            metadata = json.loads(row[4]) if row[4] else {}
        except Exception:
            metadata = {}
        dimension = str(row[0])
        summary[dimension] = {
            "dimension": dimension,
            "score": float(row[1]) if row[1] is not None else None,
            "raw_value": float(row[2]) if row[2] is not None else None,
            "window_days": int(row[3]) if row[3] is not None else None,
            "label": metadata.get("label", ""),
            "description": metadata.get("description", ""),
            "computed_at": str(row[5]) if row[5] is not None else None,
        }
    return summary


def _attach_behavioral_summary(report: dict, db: DatabaseConnector) -> dict:
    summary = _fetch_behavioral_summary(db)
    if summary:
        report["behavioral_summary"] = summary
    return report


@router.get("/alignment")
async def get_strategy_alignment(db: DatabaseConnector = Depends(get_db)):
    """Return latest strategy review report, auto-computing if stale or missing."""
    row = db.execute(
        """
        SELECT review_date, allocation_alignment, trading_frequency,
               contrarian_score, contrarian_details, profile_discrepancies, overall_alignment,
               created_at
        FROM strategy_review_reports
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()

    # Auto-compute if no report or report is older than threshold
    needs_refresh = False
    if not row:
        needs_refresh = True
    else:
        try:
            payload = json.loads(row[1]) if row[1] else {}
            if (
                "target_scope_alignment" not in payload
                or "uis_scope_alignment" not in payload
                or payload.get("report_version") != _STRATEGY_REPORT_VERSION
            ):
                needs_refresh = True
        except Exception:
            needs_refresh = True
        created_at = row[7]
        if created_at is not None:
            try:
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - created_at
                if age > timedelta(hours=_REPORT_MAX_AGE_HOURS):
                    needs_refresh = True
            except Exception:
                pass  # If we can't parse the timestamp, use existing report

    if needs_refresh:
        from src.services.strategy_reviewer import generate_strategy_report

        writable_db = None
        db_for_write = db
        if getattr(db, "read_only", False):
            db.close()
            writable_db = DatabaseConnector(db.db_path, read_only=False)
            db_for_write = writable_db
        try:
            report = generate_strategy_report(db_for_write)
        except Exception as exc:
            logger.warning("Auto-compute strategy report failed: %s", exc)
            report = None

        if report is not None:
            enriched_report = _attach_behavioral_summary(report, db_for_write)
            mark_dirty()
            if writable_db:
                writable_db.close()
            return {"report": enriched_report}

        if writable_db:
            writable_db.close()

        # Fallback: return whatever we have (possibly None if brand-new DB)
        if not row:
            return {
                "message": "No strategy review available. Run POST /strategy/review first.",
                "report": None,
            }

    return {"report": _attach_behavioral_summary(_row_to_report(row), db)}


@router.post("/review")
def trigger_strategy_review(db: DatabaseConnector = Depends(get_db)):
    """Generate and persist one strategy review report."""
    from src.services.strategy_reviewer import generate_strategy_report

    # API dependency opens read-only by default; use a writable connection for report insertion.
    writable_db = None
    db_for_write = db
    if getattr(db, "read_only", False):
        db.close()
        writable_db = DatabaseConnector(db.db_path, read_only=False)
        db_for_write = writable_db

    report = generate_strategy_report(db_for_write)
    mark_dirty()
    if writable_db:
        writable_db.close()

    return {
        "status": "ok",
        "report": report,
    }


@router.get("/memos")
async def get_strategy_memos(
    include_content: bool = False,
    db: DatabaseConnector = Depends(get_db),
):
    """List recent strategy memos."""
    rows = db.execute(
        """
        SELECT id, memo_date, title, strategic_bias, key_directives, source_file, content
        FROM strategy_memos
        ORDER BY memo_date DESC
        LIMIT 50
        """
    ).fetchall()

    return {
        "memos": [
            {
                "id": r[0],
                "date": str(r[1]),
                "title": r[2],
                "bias": r[3],
                "directives": json.loads(r[4]) if r[4] else [],
                "source_file": r[5],
                "content": r[6] if include_content else (r[6][:500] if r[6] else None),
            }
            for r in rows
        ]
    }


class CreateMemoRequest(BaseModel):
    content: str
    memo_date: Optional[str] = None


@router.post("/memos", status_code=201)
async def create_strategy_memo(request: CreateMemoRequest):
    """Create a new strategy memo from pasted text. Auto-extracts title, date, bias, directives."""
    import duckdb as _duckdb
    from src.database.connector import DatabaseConnector, resolve_db_path

    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="content must not be empty")

    # Date: explicit param wins; else extract from first 500 chars; else today
    if request.memo_date:
        try:
            memo_date = str(_date.fromisoformat(request.memo_date))
        except ValueError:
            raise HTTPException(status_code=422, detail="memo_date must be YYYY-MM-DD")
    else:
        date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", content[:500])
        if date_match:
            try:
                _date.fromisoformat(date_match.group(1))
                memo_date = date_match.group(1)
            except ValueError:
                memo_date = str(_date.today())
        else:
            memo_date = str(_date.today())

    title = _extract_memo_title(content)
    if not title:
        raise HTTPException(status_code=422, detail="Could not extract title from content")

    bias = _extract_bias(content)
    directives = _extract_directives(content)

    db_path = resolve_db_path()
    try:
        with DatabaseConnector(db_path, read_only=False) as db:
            db.execute(
                """
                INSERT INTO strategy_memos
                    (memo_date, title, strategic_bias, key_directives, content, source_file)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (
                    memo_date,
                    title,
                    bias,
                    json.dumps(directives, ensure_ascii=False),
                    content,
                ),
            )
            row = db.execute(
                "SELECT id FROM strategy_memos WHERE memo_date = ? AND title = ? ORDER BY id DESC LIMIT 1",
                (memo_date, title),
            ).fetchone()
            memo_id = row[0] if row else None
        mark_dirty()
    except _duckdb.ConstraintException as e:
        if "UNIQUE constraint" in str(e) or "Constraint Error" in str(e):
            # Return existing memo id
            with DatabaseConnector(db_path, read_only=True) as rdb:
                existing = rdb.execute(
                    "SELECT id FROM strategy_memos WHERE memo_date = ? AND title = ?",
                    (memo_date, title),
                ).fetchone()
            existing_id = existing[0] if existing else None
            raise HTTPException(
                status_code=409,
                detail={"message": "Memo already exists", "existing_id": existing_id},
            )
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "id": memo_id,
        "date": memo_date,
        "title": title,
        "bias": bias,
        "directives": directives,
    }


@router.get("/memos/{memo_id}")
async def get_strategy_memo(memo_id: int, db: DatabaseConnector = Depends(get_db)):
    """Return a single strategy memo with full content."""
    row = db.execute(
        """
        SELECT id, memo_date, title, strategic_bias, key_directives, source_file, content
        FROM strategy_memos
        WHERE id = ?
        """,
        (memo_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Memo {memo_id} not found")
    return {
        "id": row[0],
        "date": str(row[1]),
        "title": row[2],
        "bias": row[3],
        "directives": json.loads(row[4]) if row[4] else [],
        "source_file": row[5],
        "content": row[6],
    }


class UpdateMemoRequest(BaseModel):
    content: Optional[str] = None
    memo_date: Optional[str] = None
    title: Optional[str] = None


@router.put("/memos/{memo_id}")
async def update_strategy_memo(memo_id: int, request: UpdateMemoRequest):
    """Update a memo. Explicit title overrides extracted. Re-extracts bias/directives on content change."""
    import duckdb as _duckdb
    from src.database.connector import DatabaseConnector, resolve_db_path

    db_path = resolve_db_path()

    with DatabaseConnector(db_path, read_only=True) as rdb:
        row = rdb.execute(
            "SELECT id, memo_date, title, strategic_bias, key_directives, content FROM strategy_memos WHERE id = ?",
            (memo_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Memo {memo_id} not found")

    current_content = row[5] or ""
    new_content = request.content.strip() if request.content else current_content

    # Title: explicit wins; else re-extract if content changed; else keep current
    if request.title:
        new_title = request.title.strip()[:120]
    elif request.content:
        new_title = _extract_memo_title(new_content) or row[2]
    else:
        new_title = row[2]

    # Date
    if request.memo_date:
        try:
            new_date = str(_date.fromisoformat(request.memo_date))
        except ValueError:
            raise HTTPException(status_code=422, detail="memo_date must be YYYY-MM-DD")
    else:
        new_date = str(row[1])

    bias = _extract_bias(new_content) if request.content else row[3]
    directives = _extract_directives(new_content) if request.content else (json.loads(row[4]) if row[4] else [])

    try:
        with DatabaseConnector(db_path, read_only=False) as db:
            db.execute(
                """
                UPDATE strategy_memos
                SET content = ?, memo_date = ?, title = ?, strategic_bias = ?, key_directives = ?
                WHERE id = ?
                """,
                (
                    new_content,
                    new_date,
                    new_title,
                    bias,
                    json.dumps(directives, ensure_ascii=False),
                    memo_id,
                ),
            )
        mark_dirty()
    except _duckdb.ConstraintException as e:
        if "UNIQUE constraint" in str(e) or "Constraint Error" in str(e):
            raise HTTPException(status_code=409, detail="Memo with same date/title already exists")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "id": memo_id,
        "date": new_date,
        "title": new_title,
        "bias": bias,
        "directives": directives,
    }


@router.delete("/memos/{memo_id}", status_code=204)
async def delete_strategy_memo(memo_id: int):
    """Delete a strategy memo."""
    from src.database.connector import DatabaseConnector, resolve_db_path

    db_path = resolve_db_path()
    with DatabaseConnector(db_path, read_only=True) as rdb:
        row = rdb.execute("SELECT id FROM strategy_memos WHERE id = ?", (memo_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Memo {memo_id} not found")

    with DatabaseConnector(db_path, read_only=False) as db:
        db.execute("DELETE FROM strategy_memos WHERE id = ?", (memo_id,))
    mark_dirty()
    return None


@router.get("/targets")
async def get_strategy_targets(db: DatabaseConnector = Depends(get_db)):
    """Show strategic profile targets and Huinsight profile targets side-by-side."""
    strategic = db.execute(
        """
        WITH ranked AS (
            SELECT asset_class, target_pct,
                   ROW_NUMBER() OVER (PARTITION BY asset_class ORDER BY effective_date DESC, id DESC) AS rn
            FROM target_allocations
            WHERE source = 'Strategic_Profile'
        )
        SELECT asset_class, target_pct FROM ranked WHERE rn = 1
        ORDER BY target_pct DESC
        """
    ).fetchall()

    uis = db.execute(
        """
        WITH ranked AS (
            SELECT asset_class, target_pct,
                   ROW_NUMBER() OVER (PARTITION BY asset_class ORDER BY effective_date DESC, id DESC) AS rn
            FROM target_allocations
            WHERE source IS NULL OR source != 'Strategic_Profile'
        )
        SELECT asset_class, target_pct FROM ranked WHERE rn = 1
        ORDER BY target_pct DESC
        """
    ).fetchall()

    return {
        "strategic_profile": [{"asset_class": r[0], "target_pct": float(r[1])} for r in strategic],
        "uis_profile": [{"asset_class": r[0], "target_pct": float(r[1])} for r in uis],
    }
