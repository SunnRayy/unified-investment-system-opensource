"""Income/Expense API endpoints — reads from income_expense_monthly table.

Data persisted by sync orchestrator. Each row: record_key, transaction_date, JSON payload.
"""
import json
from fastapi import APIRouter, Depends, Query
from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector

router = APIRouter(prefix="/income-expense", tags=["Income Expense"])


def _parse_payload(payload_str) -> dict:
    if isinstance(payload_str, dict):
        return payload_str
    try:
        return json.loads(payload_str)
    except (json.JSONDecodeError, TypeError):
        return {}


@router.get("/summary")
async def get_income_expense_summary(db: DatabaseConnector = Depends(get_db)):
    """Get the latest month's income/expense line items."""
    try:
        date_rows = db.execute(
            "SELECT DISTINCT transaction_date FROM income_expense_monthly WHERE transaction_date IS NOT NULL ORDER BY transaction_date DESC"
        ).fetchall()
        dates = [str(r[0]) for r in date_rows if r[0]]
        latest = dates[0] if dates else None

        rows = []
        if latest:
            result = db.execute(
                "SELECT record_key, transaction_date, payload FROM income_expense_monthly WHERE transaction_date = ? ORDER BY record_key",
                (latest,),
            ).fetchall()
            rows = [{"record_key": r[0], "transaction_date": str(r[1]), **_parse_payload(r[2])} for r in result]

        if not rows:
            result = db.execute(
                "SELECT record_key, transaction_date, payload FROM income_expense_monthly ORDER BY record_key"
            ).fetchall()
            rows = [{"record_key": r[0], "transaction_date": str(r[1]) if r[1] else None, **_parse_payload(r[2])} for r in result]

        count = db.execute("SELECT COUNT(*) FROM income_expense_monthly").fetchone()

        return {
            "latest_month": latest,
            "month_count": len(dates),
            "total_rows": count[0] if count else 0,
            "rows": rows,
        }
    except Exception as e:
        return {"latest_month": None, "month_count": 0, "total_rows": 0, "rows": [], "error": str(e)}


@router.get("/history")
async def get_income_expense_history(
    limit: int = Query(default=24, ge=1, le=120),
    db: DatabaseConnector = Depends(get_db),
):
    """Get monthly income/expense data for trend charts."""
    try:
        result = db.execute(
            "SELECT record_key, transaction_date, payload FROM income_expense_monthly WHERE transaction_date IS NOT NULL ORDER BY transaction_date DESC, record_key"
        ).fetchall()

        months: dict = {}
        for r in result:
            dt = str(r[1])
            if dt not in months:
                months[dt] = []
            months[dt].append({"record_key": r[0], **_parse_payload(r[2])})

        history = [
            {"month": dt, "items": items}
            for dt, items in sorted(months.items(), reverse=True)
        ][:limit]

        return {"months": history}
    except Exception as e:
        return {"months": [], "error": str(e)}


@router.get("/dates")
async def get_income_expense_dates(db: DatabaseConnector = Depends(get_db)):
    """List all available income/expense months."""
    try:
        result = db.execute(
            "SELECT DISTINCT transaction_date FROM income_expense_monthly WHERE transaction_date IS NOT NULL ORDER BY transaction_date DESC"
        ).fetchall()
        return {"dates": [str(r[0]) for r in result if r[0]]}
    except Exception as e:
        return {"dates": [], "error": str(e)}
