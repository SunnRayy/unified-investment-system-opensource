"""Sync current asset allocations from holdings."""

from datetime import date, datetime
from typing import Dict, Optional
from src.database.connector import DatabaseConnector


def sync_current_allocations(
    connector: DatabaseConnector,
    snapshot_date: Optional[date] = None
) -> Dict[str, int]:
    """
    Calculate and sync current asset allocations.

    Separates rebalanceable and non-rebalanceable:
    - Rebalanceable: Percentages calculated against rebalanceable total
    - Non-rebalanceable: Values stored, % shown against total portfolio

    Args:
        connector: Database connector
        snapshot_date: Date for allocation snapshot (defaults to today)

    Returns:
        Dict with {synced: count}
    """
    if snapshot_date is None:
        snapshot_date = date.today()

    # Clear existing allocations for this date
    connector.execute("""
        DELETE FROM current_allocations WHERE snapshot_date = ?
    """, (snapshot_date,))

    # Get holdings with taxonomy info via asset_registry.
    # GROUP BY (asset_class, asset_subclass) only — never include is_rebalanceable in
    # the GROUP BY, because different assets in the same class may have inconsistent
    # ar.is_rebalanceable values (known unreliable for Insurance/Property), which
    # would produce multiple rows with the same (asset_class, asset_subclass) and
    # cause a UNIQUE constraint violation on current_allocations.
    #
    # is_rebalanceable resolution order (per MEMORY / rebalanceable_filter.py):
    #   1. taxonomy_classes.is_rebalanceable (authoritative — class-level truth)
    #   2. asset_registry.is_rebalanceable   (fallback — may be TRUE for Ins/Property)
    # MIN() is used so FALSE (non-rebalanceable) wins over any rogue TRUE.
    try:
        holdings = connector.execute("""
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) as latest_date
                FROM holdings WHERE is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT
                ar.asset_class,
                ar.asset_subclass,
                COALESCE(MIN(tc.is_rebalanceable), MIN(ar.is_rebalanceable), TRUE) as is_rebalanceable,
                SUM(h.market_value) as total_value
            FROM holdings h
            JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            JOIN asset_registry ar ON h.asset_id = ar.canonical_id
            LEFT JOIN taxonomy_classes tc ON ar.asset_class = tc.name
            WHERE h.is_shadow = FALSE
            GROUP BY ar.asset_class, ar.asset_subclass
        """).fetchall()
    except Exception:
        # Fallback for environments where taxonomy_classes is not yet initialized
        holdings = connector.execute("""
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) as latest_date
                FROM holdings WHERE is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT
                ar.asset_class,
                ar.asset_subclass,
                COALESCE(MIN(ar.is_rebalanceable), TRUE) as is_rebalanceable,
                SUM(h.market_value) as total_value
            FROM holdings h
            JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            JOIN asset_registry ar ON h.asset_id = ar.canonical_id
            WHERE h.is_shadow = FALSE
            GROUP BY ar.asset_class, ar.asset_subclass
        """).fetchall()

    if not holdings:
        return {'synced': 0}

    # Calculate totals — guard against NULL market_value
    rebalanceable_total = sum(
        (h[3] or 0) for h in holdings if h[2]  # is_rebalanceable
    )
    total_portfolio = sum((h[3] or 0) for h in holdings)

    synced = 0

    for asset_class, asset_subclass, is_rebalanceable, market_value in holdings:
        # Handle missing classification (avoid NOT NULL constraint)
        if not asset_class:
            asset_class = "Unclassified"
            asset_subclass = "Unclassified"
            is_rebalanceable = False
        elif not asset_subclass:
            asset_subclass = "Unclassified"

        safe_mv = float(market_value or 0)
        if is_rebalanceable and rebalanceable_total > 0:
            current_pct = safe_mv / float(rebalanceable_total) * 100
        else:
            # Non-rebalanceable: show % of total portfolio for info
            current_pct = safe_mv / float(total_portfolio) * 100 if total_portfolio > 0 else 0

        connector.execute("""
            INSERT INTO current_allocations (
                asset_class, asset_subclass, current_pct, market_value,
                is_rebalanceable, snapshot_date, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            asset_class, asset_subclass, current_pct, safe_mv,
            is_rebalanceable, snapshot_date, datetime.now()
        ))
        synced += 1

    return {'synced': synced}
