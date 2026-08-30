
import json
from datetime import date
from typing import List, Optional, Any
from src.services.rebalanceable_filter import adjust_balance_sheet_payload

def get_portfolio_value_series(
    db: Any,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    include_asset_ids: Optional[List[str]] = None,
    exclude_non_balanceable: bool = False,
) -> List[dict]:
    # 1. Fetch balance sheet monthly snapshots
    bs_clauses = []
    bs_params = []
    if start_date:
        bs_clauses.append("snapshot_date >= ?")
        bs_params.append(start_date)
    if end_date:
        bs_clauses.append("snapshot_date <= ?")
        bs_params.append(end_date)
    
    bs_filter = f"WHERE {' AND '.join(bs_clauses)}" if bs_clauses else ""
    bs_query = f"SELECT snapshot_date, payload FROM balance_sheet_monthly {bs_filter} ORDER BY snapshot_date ASC"
    bs_rows = db.execute(bs_query, bs_params or None).fetchall()
    
    snapshot_map = {} # date -> value
    for snapshot_date_raw, payload_str in bs_rows:
        dt = date.fromisoformat(snapshot_date_raw) if isinstance(snapshot_date_raw, str) else snapshot_date_raw
        if hasattr(dt, 'date'): dt = dt.date()
        
        payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
        val = payload.get("合计总资产") or payload.get("合计净资产") or 0.0
        
        if exclude_non_balanceable:
            adjustment = adjust_balance_sheet_payload(payload)
            val = float(val) - adjustment
            
        snapshot_map[dt] = float(val)
    
    # 2. Fetch current holdings (latest available data)
    # We aggregate ALL non-shadow holdings into a single terminal point.
    holdings_clauses = ["is_shadow = FALSE"]
    holdings_params = []
    
    if start_date:
        holdings_clauses.append("snapshot_date >= ?")
        holdings_params.append(start_date)
    if end_date:
        holdings_clauses.append("snapshot_date <= ?")
        holdings_params.append(end_date)
    if include_asset_ids is not None:
        if include_asset_ids:
            placeholders = ", ".join(["?"] * len(include_asset_ids))
            holdings_clauses.append(f"asset_id IN ({placeholders})")
            holdings_params.extend(include_asset_ids)
        else:
            holdings_clauses.append("1=0")
            
    holdings_filter = f"WHERE {' AND '.join(holdings_clauses)}"
    holdings_query = f"""
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS max_date
            FROM holdings
            {holdings_filter}
            GROUP BY asset_id
        )
        SELECT MAX(l.max_date), SUM(h.market_value) 
        FROM holdings h
        JOIN latest_per_asset l ON h.asset_id = l.asset_id AND h.snapshot_date = l.max_date
        WHERE h.is_shadow = FALSE
    """
    row = db.execute(holdings_query, holdings_params or None).fetchone()
    
    if row and row[0] is not None:
        snapshot_date_raw, val = row
        dt = date.fromisoformat(snapshot_date_raw) if isinstance(snapshot_date_raw, str) else snapshot_date_raw
        if hasattr(dt, 'date'): dt = dt.date()
        
        # Holdings data wins over BS monthly data for the same date
        snapshot_map[dt] = float(val or 0.0)
    
    # 3. Format into sorted list
    result = [
        {"date": dt, "value": val}
        for dt, val in snapshot_map.items()
    ]
    
    return sorted(result, key=lambda x: x["date"])
