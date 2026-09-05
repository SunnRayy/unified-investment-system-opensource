"""Balance Sheet API endpoints — reads from balance_sheet_monthly table.

Data is persisted by the sync orchestrator via _persist_financial_summary().
Each row has a record_key, snapshot_date, and JSON payload blob.
"""
import json
from fastapi import APIRouter, Depends, Query
from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector

router = APIRouter(prefix="/balance-sheet", tags=["Balance Sheet"])


def _parse_payload(payload_str) -> dict:
    """Parse JSON payload from balance_sheet_monthly row."""
    if isinstance(payload_str, dict):
        return payload_str
    try:
        return json.loads(payload_str)
    except (json.JSONDecodeError, TypeError):
        return {}


@router.get("/summary")
async def get_balance_sheet_summary(db: DatabaseConnector = Depends(get_db)):
    """Get the latest balance sheet snapshot with all line items."""
    try:
        # Find all available snapshot dates
        date_rows = db.execute(
            "SELECT DISTINCT snapshot_date FROM balance_sheet_monthly WHERE snapshot_date IS NOT NULL ORDER BY snapshot_date DESC"
        ).fetchall()
        snapshot_dates = [str(r[0]) for r in date_rows if r[0]]

        # Get latest snapshot rows
        latest_date = snapshot_dates[0] if snapshot_dates else None
        rows = []
        if latest_date:
            result = db.execute(
                "SELECT record_key, snapshot_date, payload FROM balance_sheet_monthly WHERE snapshot_date = ? ORDER BY record_key",
                (latest_date,),
            ).fetchall()
            rows = [
                {"record_key": r[0], "snapshot_date": str(r[1]), **_parse_payload(r[2])}
                for r in result
            ]

        # Also get all rows if no dated rows exist (some rows may have NULL snapshot_date)
        if not rows:
            result = db.execute(
                "SELECT record_key, snapshot_date, payload FROM balance_sheet_monthly ORDER BY record_key"
            ).fetchall()
            rows = [
                {"record_key": r[0], "snapshot_date": str(r[1]) if r[1] else None, **_parse_payload(r[2])}
                for r in result
            ]

        count_result = db.execute("SELECT COUNT(*) FROM balance_sheet_monthly").fetchone()
        total_rows = count_result[0] if count_result else 0

        return {
            "latest_snapshot": latest_date,
            "snapshot_count": len(snapshot_dates),
            "total_rows": total_rows,
            "rows": rows,
        }
    except Exception as e:
        return {"latest_snapshot": None, "snapshot_count": 0, "total_rows": 0, "rows": [], "error": str(e)}


@router.get("/history")
async def get_balance_sheet_history(
    limit: int = Query(default=72, ge=1, le=120),
    db: DatabaseConnector = Depends(get_db),
):
    """Get balance sheet snapshots over time for trend charts.

    Returns one row per snapshot_date with all line items for that month.
    """
    try:
        result = db.execute(
            """
            SELECT record_key, snapshot_date, payload
            FROM balance_sheet_monthly
            WHERE snapshot_date IS NOT NULL
            ORDER BY snapshot_date DESC, record_key
            """,
        ).fetchall()

        # Group by snapshot_date
        snapshots: dict = {}
        for r in result:
            dt = str(r[1])
            if dt not in snapshots:
                snapshots[dt] = []
            snapshots[dt].append({"record_key": r[0], **_parse_payload(r[2])})

        # Convert to list sorted by date desc, apply limit
        history = [
            {"snapshot_date": dt, "items": items}
            for dt, items in sorted(snapshots.items(), reverse=True)
        ][:limit]

        return {"snapshots": history}
    except Exception as e:
        return {"snapshots": [], "error": str(e)}


@router.get("/dates")
async def get_balance_sheet_dates(db: DatabaseConnector = Depends(get_db)):
    """List all available balance sheet snapshot dates."""
    try:
        result = db.execute(
            "SELECT DISTINCT snapshot_date FROM balance_sheet_monthly WHERE snapshot_date IS NOT NULL ORDER BY snapshot_date DESC"
        ).fetchall()
        return {"dates": [str(r[0]) for r in result if r[0]]}
    except Exception as e:
        return {"dates": [], "error": str(e)}
