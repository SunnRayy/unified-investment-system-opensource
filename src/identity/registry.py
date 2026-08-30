"""Asset registry manager for canonical ID management."""

from datetime import datetime
from typing import Optional, Dict, Any
from src.database.connector import DatabaseConnector
from src.identity.normalizer import get_canonical_id


class AssetRegistry:
    """Manages the canonical asset registry and source mappings."""

    def __init__(self, connector: DatabaseConnector):
        self.connector = connector

    def register_asset(
        self,
        source_id: str,
        source_system: str,
        display_name: str,
        asset_class: Optional[str] = None,
        asset_subclass: Optional[str] = None,
        tier: Optional[str] = None,
        is_rebalanceable: bool = True,
        base_currency: str = 'CNY'
    ) -> str:
        """
        Register an asset and create source mapping.

        If asset already exists (by source mapping), returns existing canonical_id.
        If asset doesn't exist, creates new registry entry and mapping.

        Returns:
            Canonical ID for the asset
        """
        # Check if mapping already exists
        existing = self.connector.execute("""
            SELECT canonical_id FROM asset_source_mappings
            WHERE source_system = ? AND source_id = ?
        """, (source_system, source_id)).fetchone()

        if existing:
            return existing[0]

        # Generate canonical ID
        canonical_id = get_canonical_id(source_id, source_system)

        # Check if canonical_id already exists in registry
        registry_exists = self.connector.execute("""
            SELECT canonical_id FROM asset_registry WHERE canonical_id = ?
        """, (canonical_id,)).fetchone()

        if not registry_exists:
            # Create new registry entry
            self.connector.execute("""
                INSERT INTO asset_registry (
                    canonical_id, display_name, asset_class, asset_subclass,
                    tier, is_rebalanceable, base_currency, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                canonical_id, display_name, asset_class, asset_subclass,
                tier, is_rebalanceable, base_currency,
                datetime.now(), datetime.now()
            ))

        # Create source mapping
        self.connector.execute("""
            INSERT INTO asset_source_mappings (
                canonical_id, source_system, source_id, mapping_type, created_at
            ) VALUES (?, ?, ?, 'auto', ?)
        """, (canonical_id, source_system, source_id, datetime.now()))

        return canonical_id

    def resolve(self, source_id: str, source_system: str) -> Optional[str]:
        """
        Resolve a source ID to canonical ID.

        Returns:
            Canonical ID if found, None otherwise
        """
        result = self.connector.execute("""
            SELECT canonical_id FROM asset_source_mappings
            WHERE source_system = ? AND source_id = ?
        """, (source_system, source_id)).fetchone()

        return result[0] if result else None

    def get_asset(self, canonical_id: str) -> Optional[Dict[str, Any]]:
        """
        Get full asset info by canonical ID.

        Returns:
            Dict with asset info or None if not found
        """
        result = self.connector.execute("""
            SELECT canonical_id, display_name, asset_class, asset_subclass,
                   tier, is_rebalanceable, base_currency, is_active
            FROM asset_registry
            WHERE canonical_id = ?
        """, (canonical_id,)).fetchone()

        if not result:
            return None

        return {
            'canonical_id': result[0],
            'display_name': result[1],
            'asset_class': result[2],
            'asset_subclass': result[3],
            'tier': result[4],
            'is_rebalanceable': bool(result[5]),
            'base_currency': result[6],
            'is_active': bool(result[7])
        }
