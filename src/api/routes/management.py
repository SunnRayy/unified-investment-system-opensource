"""Management API endpoints — Transaction Browser & Import Preview."""
from fastapi import APIRouter, Depends, Query
from typing import Optional, Any, Iterable

from src.database.connector import DatabaseConnector
from src.api.dependencies import get_db

router = APIRouter(prefix="/management", tags=["management"])


def _rows_to_dicts(result: Any) -> list[dict]:
    """Normalize query results for both real DuckDB cursors and mocked list outputs."""
    if isinstance(result, list):
        return result
    if hasattr(result, "fetchall"):
        rows = result.fetchall()
        cols = [col[0] for col in getattr(result, "description", [])]
        return [dict(zip(cols, row)) for row in rows]
    return []


def _scalar_count(result: Any) -> int:
    if isinstance(result, list):
        if not result:
            return 0
        row = result[0]
        if isinstance(row, dict):
            return int(row.get("cnt", 0))
        if isinstance(row, Iterable):
            values = list(row)
            return int(values[0]) if values else 0
        return int(row)
    if hasattr(result, "fetchone"):
        row = result.fetchone()
        if row is None:
            return 0
        if isinstance(row, dict):
            return int(row.get("cnt", 0))
        return int(row[0])
    return 0


def _pluck(rows: list[dict], key: str) -> list[Any]:
    """Extract a column from row dicts, tolerating column-name case variance."""
    target = key.lower()
    values: list[Any] = []
    for row in rows:
        if key in row:
            values.append(row[key])
            continue
        matched = next((value for name, value in row.items() if str(name).lower() == target), None)
        if matched is not None:
            values.append(matched)
    return values


@router.get("/transactions")
async def search_transactions(
    asset_id: Optional[str] = Query(None, description="Filter by asset ID (exact or prefix)"),
    source: Optional[str] = Query(None, description="Filter by source system"),
    txn_type: Optional[str] = Query(None, description="Backward-compatible alias for normalized_type"),
    normalized_type: Optional[str] = Query(None, description="Normalized transaction type"),
    raw_type: Optional[str] = Query(None, description="Raw transaction type"),
    account: Optional[str] = Query(None, description="Filter by account"),
    verified: Optional[bool] = Query(None, description="Filter by verified flag"),
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=1000),
    db: DatabaseConnector = Depends(get_db),
):
    """Search transaction evidence with live transactions schema fields."""
    conditions = []
    params = []
    if asset_id:
        conditions.append("asset_id LIKE ?")
        params.append(f"{asset_id}%")
    if source:
        conditions.append("source_system = ?")
        params.append(source)
    effective_norm_type = normalized_type or txn_type
    if effective_norm_type:
        conditions.append("LOWER(transaction_type) = LOWER(?)")
        params.append(effective_norm_type)
    if raw_type:
        conditions.append("transaction_type = ?")
        params.append(raw_type)
    if account:
        conditions.append("account = ?")
        params.append(account)
    if verified is not None:
        conditions.append("verified = ?")
        params.append(verified)
    if date_from:
        conditions.append("transaction_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("transaction_date <= ?")
        params.append(date_to)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    count_result = db.execute(
        f"SELECT COUNT(*) as cnt FROM transactions {where}", params
    )
    total_count = _scalar_count(count_result)

    offset = (page - 1) * limit
    data_result = db.execute(
        f"""SELECT id, transaction_date, asset_id, asset_name, transaction_type,
                   quantity, price_unit, amount_net, commission_fee,
                   currency, account, memo, source_system, verified
            FROM transactions
            {where}
            ORDER BY transaction_date DESC, id DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset]
    )
    rows = _rows_to_dicts(data_result)

    return {
        "transactions": rows,
        "total": total_count,
        "page": page,
        "limit": limit,
    }


@router.get("/transactions/sources")
async def get_transaction_sources(db: DatabaseConnector = Depends(get_db)):
    """Backward-compatible endpoint for source/type dropdown data."""
    source_rows = _rows_to_dicts(db.execute(
        "SELECT DISTINCT source_system FROM transactions WHERE source_system IS NOT NULL ORDER BY source_system"
    ))
    type_rows = _rows_to_dicts(db.execute(
        "SELECT DISTINCT transaction_type FROM transactions WHERE transaction_type IS NOT NULL ORDER BY transaction_type"
    ))
    sources = _pluck(source_rows, "source_system")
    types = _pluck(type_rows, "transaction_type")
    return {
        "sources": sources,
        "types": types,
    }


@router.get("/transactions/filters")
async def get_transaction_filters(db: DatabaseConnector = Depends(get_db)):
    """Returns normalized metadata for transaction evidence filters."""
    source_rows = _rows_to_dicts(db.execute(
        "SELECT DISTINCT source_system FROM transactions WHERE source_system IS NOT NULL ORDER BY source_system"
    ))
    raw_type_rows = _rows_to_dicts(db.execute(
        "SELECT DISTINCT transaction_type FROM transactions WHERE transaction_type IS NOT NULL ORDER BY transaction_type"
    ))
    norm_type_rows = _rows_to_dicts(db.execute(
        "SELECT DISTINCT LOWER(transaction_type) AS normalized_type FROM transactions WHERE transaction_type IS NOT NULL ORDER BY normalized_type"
    ))
    account_rows = _rows_to_dicts(db.execute(
        "SELECT DISTINCT account FROM transactions WHERE account IS NOT NULL AND account <> '' ORDER BY account"
    ))
    return {
        "sources": _pluck(source_rows, "source_system"),
        "raw_types": _pluck(raw_type_rows, "transaction_type"),
        "normalized_types": _pluck(norm_type_rows, "normalized_type"),
        "accounts": _pluck(account_rows, "account"),
    }


@router.get("/import/preview")
async def preview_imports():
    """Preview what the next sync would import (dry-run of all readers).

    Uses the existing reader validation framework to show what data
    each reader would produce without writing to the database.
    """
    try:
        from src.validation.run_reader_validation import run_full_validation
        report = run_full_validation()
        readers = []
        for reader_name, result in report.reader_results.items():
            has_errors = any("error" in w.lower() for w in result.warnings)
            readers.append({
                "reader": reader_name,
                "status": "warning" if has_errors or result.warnings else "ok",
                "holdings_count": result.holdings_count,
                "transactions_count": result.transactions_count,
                "warnings": result.warnings,
                "new_assets": [],
                "conflicts": [],
            })
        return {"readers": readers}
    except Exception as e:
        return {"readers": [], "error": str(e)}
