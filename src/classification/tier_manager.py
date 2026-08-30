"""TierManager for CRUD operations on asset_tiers table."""
from typing import Optional, List
from src.database.connector import DatabaseConnector
from src.classification.models import AssetTier


class TierManager:
    """Manages the 3-tier portfolio construction system."""
    
    def __init__(self, connector: DatabaseConnector):
        self.connector = connector
    
    def create_tier(
        self,
        id: str,
        name: str,
        target_pct: float,
        name_en: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
        sort_order: int = 0
    ) -> None:
        """Create a new asset tier."""
        self.connector.execute("""
            INSERT INTO asset_tiers (id, name, name_en, target_pct, description, color, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [id, name, name_en, target_pct, description, color, sort_order])
    
    def get_tier(self, tier_id: str) -> Optional[AssetTier]:
        """Get a tier by ID. Returns None if not found."""
        row = self.connector.execute(
            "SELECT id, name, name_en, target_pct, description, color, sort_order FROM asset_tiers WHERE id = ?",
            [tier_id]
        ).fetchone()
        
        if row is None:
            return None
        
        return AssetTier(
            id=row[0],
            name=row[1],
            name_en=row[2],
            target_pct=float(row[3]),
            description=row[4],
            color=row[5],
            sort_order=row[6]
        )
    
    def get_all_tiers(self) -> List[AssetTier]:
        """Get all tiers sorted by sort_order."""
        rows = self.connector.execute(
            "SELECT id, name, name_en, target_pct, description, color, sort_order FROM asset_tiers ORDER BY sort_order"
        ).fetchall()
        
        return [
            AssetTier(
                id=row[0],
                name=row[1],
                name_en=row[2],
                target_pct=float(row[3]),
                description=row[4],
                color=row[5],
                sort_order=row[6]
            )
            for row in rows
        ]
    
    def update_tier(self, tier_id: str, **kwargs) -> None:
        """Update a tier with the given fields."""
        if not kwargs:
            return
        
        set_clauses = []
        values = []
        
        for key, value in kwargs.items():
            set_clauses.append(f"{key} = ?")
            values.append(value)
        
        values.append(tier_id)
        
        self.connector.execute(
            f"UPDATE asset_tiers SET {', '.join(set_clauses)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values
        )
