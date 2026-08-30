"""TaxonomyManager for CRUD operations on taxonomy_classes table."""
from typing import Optional, List
from src.database.connector import DatabaseConnector
from src.classification.models import TaxonomyClass


class TaxonomyManager:
    """Manages taxonomy class hierarchy (7 top-level classes, 27 sub-classes)."""
    
    def __init__(self, connector: DatabaseConnector):
        self.connector = connector
    
    def create_class(
        self,
        name: str,
        name_cn: Optional[str] = None,
        parent_id: Optional[int] = None,
        level: int = 0,
        sort_order: int = 0,
        is_rebalanceable: bool = True,
        description: Optional[str] = None
    ) -> int:
        """Create a new taxonomy class and return its ID."""
        # Get next ID
        result = self.connector.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM taxonomy_classes"
        ).fetchone()
        new_id = result[0]
        
        self.connector.execute("""
            INSERT INTO taxonomy_classes 
            (id, name, name_cn, parent_id, level, sort_order, is_rebalanceable, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [new_id, name, name_cn, parent_id, level, sort_order, is_rebalanceable, description])
        
        return new_id
    
    def get_class_by_name(self, name: str) -> Optional[TaxonomyClass]:
        """Get a taxonomy class by name. Returns None if not found."""
        row = self.connector.execute(
            "SELECT id, name, name_cn, parent_id, level, sort_order, is_rebalanceable, description FROM taxonomy_classes WHERE name = ?",
            [name]
        ).fetchone()
        
        if row is None:
            return None
        
        return TaxonomyClass(
            id=row[0],
            name=row[1],
            name_cn=row[2],
            parent_id=row[3],
            level=row[4],
            sort_order=row[5],
            is_rebalanceable=row[6],
            description=row[7]
        )
    
    def get_class_by_id(self, class_id: int) -> Optional[TaxonomyClass]:
        """Get a taxonomy class by ID. Returns None if not found."""
        row = self.connector.execute(
            "SELECT id, name, name_cn, parent_id, level, sort_order, is_rebalanceable, description FROM taxonomy_classes WHERE id = ?",
            [class_id]
        ).fetchone()
        
        if row is None:
            return None
        
        return TaxonomyClass(
            id=row[0],
            name=row[1],
            name_cn=row[2],
            parent_id=row[3],
            level=row[4],
            sort_order=row[5],
            is_rebalanceable=row[6],
            description=row[7]
        )
    
    def get_hierarchy(self) -> List[TaxonomyClass]:
        """Get all top-level classes (parent_id IS NULL)."""
        return self.get_top_level_classes()
    
    def get_top_level_classes(self) -> List[TaxonomyClass]:
        """Get all top-level classes (level=0, parent_id IS NULL)."""
        rows = self.connector.execute(
            "SELECT id, name, name_cn, parent_id, level, sort_order, is_rebalanceable, description FROM taxonomy_classes WHERE parent_id IS NULL ORDER BY sort_order"
        ).fetchall()
        
        return [
            TaxonomyClass(
                id=row[0],
                name=row[1],
                name_cn=row[2],
                parent_id=row[3],
                level=row[4],
                sort_order=row[5],
                is_rebalanceable=row[6],
                description=row[7]
            )
            for row in rows
        ]
    
    def get_children(self, parent_id: int) -> List[TaxonomyClass]:
        """Get all sub-classes of a parent class."""
        rows = self.connector.execute(
            "SELECT id, name, name_cn, parent_id, level, sort_order, is_rebalanceable, description FROM taxonomy_classes WHERE parent_id = ? ORDER BY sort_order",
            [parent_id]
        ).fetchall()
        
        return [
            TaxonomyClass(
                id=row[0],
                name=row[1],
                name_cn=row[2],
                parent_id=row[3],
                level=row[4],
                sort_order=row[5],
                is_rebalanceable=row[6],
                description=row[7]
            )
            for row in rows
        ]
    
    def get_parent(self, class_id: int) -> Optional[TaxonomyClass]:
        """Get the parent class of a sub-class."""
        cls = self.get_class_by_id(class_id)
        if cls is None or cls.parent_id is None:
            return None
        return self.get_class_by_id(cls.parent_id)
    
    def update_class(self, class_id: int, **kwargs) -> None:
        """Update a taxonomy class with the given fields."""
        if not kwargs:
            return
        
        set_clauses = []
        values = []
        
        for key, value in kwargs.items():
            set_clauses.append(f"{key} = ?")
            values.append(value)
        
        values.append(class_id)
        
        self.connector.execute(
            f"UPDATE taxonomy_classes SET {', '.join(set_clauses)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values
        )
