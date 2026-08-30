"""Validate allocation drift for rebalanceable assets."""

from dataclasses import dataclass
from typing import List, Optional
from src.database.connector import DatabaseConnector


@dataclass
class AllocationDrift:
    """Details of allocation drift."""
    asset_class: str
    asset_subclass: Optional[str]
    current_pct: float
    target_pct: float
    drift_pct: float
    recommendation: str  # 'reduce', 'add', 'hold'


def validate_allocations(
    connector: DatabaseConnector,
    threshold_pct: float = 5.0
) -> List[AllocationDrift]:
    """
    Validate current allocations against targets for rebalanceable assets.

    Args:
        connector: Database connector
        threshold_pct: Minimum drift % to flag

    Returns:
        List of AllocationDrift for assets exceeding threshold
    """
    # Get current allocations with targets from active risk profile (rebalanceable only)
    # Joins taxonomy_classes (via ca.asset_class = tc.name) then risk_profile_allocations
    # for the active risk profile's target percentages.
    results = connector.execute("""
        SELECT
            ca.asset_class,
            ca.asset_subclass,
            ca.current_pct,
            rpa.target_pct
        FROM current_allocations ca
        JOIN taxonomy_classes tc ON ca.asset_class = tc.name
        JOIN risk_profile_allocations rpa ON rpa.class_id = tc.id
        JOIN risk_profiles rp ON rpa.profile_id = rp.id
        WHERE ca.is_rebalanceable = TRUE
        AND rpa.target_pct IS NOT NULL
        AND ca.snapshot_date = (SELECT MAX(snapshot_date) FROM current_allocations)
        AND rp.is_active = TRUE
    """).fetchall()

    drifts = []

    for asset_class, asset_subclass, current_pct, target_pct in results:
        drift_pct = current_pct - target_pct

        if abs(drift_pct) >= threshold_pct:
            recommendation = 'reduce' if drift_pct > 0 else 'add'

            drifts.append(AllocationDrift(
                asset_class=asset_class,
                asset_subclass=asset_subclass,
                current_pct=current_pct,
                target_pct=target_pct,
                drift_pct=drift_pct,
                recommendation=recommendation
            ))

    return drifts
