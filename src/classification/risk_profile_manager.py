"""RiskProfileManager for CRUD operations on risk_profiles and risk_profile_allocations tables."""
from typing import Optional, List, Dict
from src.database.connector import DatabaseConnector
from src.classification.models import RiskProfile


class RiskProfileManager:
    """Manages risk profiles and their per-class target allocations."""
    
    def __init__(self, connector: DatabaseConnector):
        self.connector = connector
    
    def create_profile(
        self,
        name: str,
        name_en: Optional[str] = None,
        is_active: bool = False,
        description: Optional[str] = None
    ) -> int:
        """Create a new risk profile and return its ID."""
        # Get next ID
        result = self.connector.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM risk_profiles"
        ).fetchone()
        new_id = result[0]
        
        self.connector.execute("""
            INSERT INTO risk_profiles (id, name, name_en, is_active, description)
            VALUES (?, ?, ?, ?, ?)
        """, [new_id, name, name_en, is_active, description])
        
        return new_id
    
    def get_active_profile(self) -> Optional[RiskProfile]:
        """Get the currently active risk profile. Returns None if none is active."""
        row = self.connector.execute(
            "SELECT id, name, name_en, is_active, description FROM risk_profiles WHERE is_active = TRUE"
        ).fetchone()
        
        if row is None:
            return None
        
        return RiskProfile(
            id=row[0],
            name=row[1],
            name_en=row[2],
            is_active=row[3],
            description=row[4]
        )
    
    def get_all_profiles(self) -> List[RiskProfile]:
        """Get all risk profiles."""
        rows = self.connector.execute(
            "SELECT id, name, name_en, is_active, description FROM risk_profiles ORDER BY id"
        ).fetchall()
        
        return [
            RiskProfile(
                id=row[0],
                name=row[1],
                name_en=row[2],
                is_active=row[3],
                description=row[4]
            )
            for row in rows
        ]
    
    def activate_profile(self, profile_id: int) -> None:
        """Set the specified profile as active, deactivating all others.
        
        IMPORTANT: This must first deactivate ALL profiles, then activate the target.
        Two SQL statements, not one.
        """
        # First, deactivate all profiles
        self.connector.execute(
            "UPDATE risk_profiles SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP"
        )
        
        # Then, activate the target profile
        self.connector.execute(
            "UPDATE risk_profiles SET is_active = TRUE, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [profile_id]
        )
    
    def set_allocations(self, profile_id: int, allocations: Dict[int, float]) -> None:
        """Set allocations for a profile using replace semantics.
        
        Deletes all existing allocations for this profile, then inserts new ones.
        """
        # Delete existing allocations
        self.connector.execute(
            "DELETE FROM risk_profile_allocations WHERE profile_id = ?",
            [profile_id]
        )
        
        # Insert new allocations
        for class_id, target_pct in allocations.items():
            # Get next ID
            result = self.connector.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM risk_profile_allocations"
            ).fetchone()
            new_id = result[0]
            
            self.connector.execute("""
                INSERT INTO risk_profile_allocations (id, profile_id, class_id, target_pct)
                VALUES (?, ?, ?, ?)
            """, [new_id, profile_id, class_id, target_pct])
    
    def get_allocations(self, profile_id: int) -> Dict[int, float]:
        """Get allocations for a profile. Returns {class_id: target_pct}."""
        rows = self.connector.execute(
            "SELECT class_id, target_pct FROM risk_profile_allocations WHERE profile_id = ?",
            [profile_id]
        ).fetchall()
        
        return {row[0]: float(row[1]) for row in rows}
