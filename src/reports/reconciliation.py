
"""
Reconciliation Report Module
Generates reports comparing Authoritative vs Shadow records to identify data divergence.
"""

from typing import List, Dict, Any, Optional
from datetime import date
from src.database.connector import DatabaseConnector

def generate_reconciliation_report(
    connector: DatabaseConnector,
    snapshot_date: Optional[date] = None
) -> List[Dict[str, Any]]:
    """
    Generate reconciliation report comparing Authoritative vs Shadow records for a given date.
    
    Args:
        connector: Database connection
        snapshot_date: Date to report (default: today)
    
    Returns:
        List of dicts containing comparison data:
        {
            'canonical_id': str,
            'auth_source': str,
            'auth_value': float,
            'shadow_source': str,
            'shadow_value': float,
            'diverence_pct': float
        }
    """
    if snapshot_date is None:
        snapshot_date = date.today()

    query = """
        SELECT
            h1.asset_id,
            h1.source_system as auth_source,
            h1.market_value as auth_value,
            h2.source_system as shadow_source,
            h2.market_value as shadow_value
        FROM holdings h1
        JOIN holdings h2
            ON h1.asset_id = h2.asset_id
            AND h1.snapshot_date = h2.snapshot_date
        WHERE h1.snapshot_date = ?
          AND h1.is_shadow = FALSE
          AND h2.is_shadow = TRUE
    """
    rows = connector.execute(query, (snapshot_date,)).fetchall()
    
    report = []
    for row in rows:
        auth_val = float(row[2] or 0)
        shadow_val = float(row[4] or 0)
        
        diff = abs(auth_val - shadow_val)
        pct = (diff / auth_val * 100) if auth_val > 0 else 0.0
        
        report.append({
            'canonical_id': row[0],
            'auth_source': row[1],
            'auth_value': auth_val,
            'shadow_source': row[3],
            'shadow_value': shadow_val,
            'diverence_pct': round(pct, 2)
        })
        
    return report
