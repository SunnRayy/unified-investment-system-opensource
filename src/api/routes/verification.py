"""Verification API routes for monthly verification reports."""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query
from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.storage.gcs_flush import mark_dirty

router = APIRouter(prefix="/verification", tags=["Verification"])

_ONE_DAY = timedelta(hours=24)


def _open_writable(db: DatabaseConnector) -> DatabaseConnector:
    """Return a writable DB connection, closing the read-only one if needed."""
    if getattr(db, "read_only", False):
        db_path = db.db_path
        db.close()
        return DatabaseConnector(db_path, read_only=False)
    return db


def _is_recent(created_at_value) -> bool:
    """Return True if the log row was created within the last 24 hours."""
    if created_at_value is None:
        return False
    try:
        if isinstance(created_at_value, str):
            ts = datetime.fromisoformat(created_at_value.replace("Z", "+00:00"))
        elif isinstance(created_at_value, datetime):
            ts = created_at_value
        else:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts) < _ONE_DAY
    except Exception:
        return False


@router.get("/latest")
async def get_latest_verification(db: DatabaseConnector = Depends(get_db)):
    """Get the most recent verification report, computing fresh if none exists within 24h."""
    row = db.execute(
        """
        SELECT created_at, adoption_rate, max_allocation_drift, total_insights,
               period_start, period_end, portfolio_return, benchmark_return, alpha
        FROM verification_logs
        WHERE verification_type = 'monthly'
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()

    if row and _is_recent(row[0]):
        # Cached KPIs + freshly computed history/breakdown arrays (fast read-only queries)
        from src.services.verification_service import compute_verification_report
        return compute_verification_report(db)

    # No recent log — compute fresh (needs write access to persist)
    writable = None
    try:
        writable = _open_writable(db)
        from src.services.verification_service import compute_verification_report
        result = compute_verification_report(writable)
        mark_dirty()
        return result
    except Exception:
        # Write failed (concurrent); compute without persisting using read-only db
        from src.services.verification_service import compute_verification_report
        return compute_verification_report(db)
    finally:
        if writable and writable is not db:
            writable.close()


@router.post("/run")
async def run_verification(db: DatabaseConnector = Depends(get_db)):
    """Trigger a fresh verification computation and store the result."""
    writable = None
    try:
        writable = _open_writable(db)
        from src.services.verification_service import compute_verification_report
        result = compute_verification_report(writable)
        mark_dirty()
        return result
    finally:
        if writable and writable is not db:
            writable.close()


@router.get("/trends")
async def get_verification_trends(db: DatabaseConnector = Depends(get_db)):
    """Get trend data for verification dashboard — sourced from adoption_history in the service."""
    # Return monthly adoption history computed from insights (richer than verification_logs alone)
    monthly_rows = db.execute(
        """
        SELECT
            DATE_TRUNC('month', created_at)::DATE AS month,
            COUNT(*)                              AS total,
            SUM(CASE WHEN adopted = 1 THEN 1 ELSE 0 END) AS adopted
        FROM insights
        WHERE created_at IS NOT NULL
          AND COALESCE(category, '') != 'lesson'
        GROUP BY DATE_TRUNC('month', created_at)::DATE
        ORDER BY month ASC
        """
    ).fetchall()

    periods = [
        {
            "period_start": str(r[0]) if r[0] else None,
            "period_end": str(r[0]) if r[0] else None,
            "adoption_rate": round(int(r[2] or 0) / int(r[1]) * 100, 1) if r[1] else 0.0,
            "portfolio_return": None,
            "benchmark_return": None,
            "alpha": None,
            "max_drift": None,
            "total_insights": int(r[1]),
        }
        for r in monthly_rows
    ]

    return {"periods": periods}


@router.get("/history")
async def get_verification_history(
    limit: int = Query(default=12, ge=1, le=100),
    db: DatabaseConnector = Depends(get_db)
):
    """Get list of past verification reports."""
    rows = db.execute(
        """
        SELECT
            verification_date, verification_type, period_start, period_end,
            adoption_rate, max_allocation_drift, total_insights,
            portfolio_return, benchmark_return, alpha
        FROM verification_logs
        WHERE verification_type = 'monthly'
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    return [
        {
            "verification_date": str(row[0]) if row[0] else None,
            "verification_type": row[1],
            "period_start": str(row[2]) if row[2] else None,
            "period_end": str(row[3]) if row[3] else None,
            "adoption_rate": float(row[4]) if row[4] is not None else None,
            "max_allocation_drift": float(row[5]) if row[5] is not None else None,
            "total_insights": row[6],
            "portfolio_return": float(row[7]) if row[7] is not None else None,
            "benchmark_return": float(row[8]) if row[8] is not None else None,
            "alpha": float(row[9]) if row[9] is not None else None,
        }
        for row in rows
    ]
