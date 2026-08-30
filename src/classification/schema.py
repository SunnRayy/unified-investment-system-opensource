"""Schema migration for Phase 2 classification tables.

Creates 6 new tables for the taxonomy & tier classification system:
- taxonomy_classes: Class hierarchy (7 top-level, 27 sub-classes)
- asset_tiers: 3-tier portfolio construction
- risk_profiles: 4 risk profile definitions
- risk_profile_allocations: Per-class target allocations per profile
- classification_rules: Explicit and regex classification mappings
- classification_audit_log: Audit trail for classification changes
"""
from src.database.connector import DatabaseConnector


def create_classification_tables(connector: DatabaseConnector) -> None:
    """Create Phase 2 classification tables. Idempotent."""
    
    # 1. Taxonomy class hierarchy (replaces flat asset_taxonomy rows)
    connector.execute("""
        CREATE TABLE IF NOT EXISTS taxonomy_classes (
            id INTEGER PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            name_cn VARCHAR(100),
            parent_id INTEGER,
            level INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            is_rebalanceable BOOLEAN DEFAULT TRUE,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name, parent_id)
        )
    """)
    
    # 2. Asset tier system (3 tiers for portfolio construction)
    connector.execute("""
        CREATE TABLE IF NOT EXISTS asset_tiers (
            id VARCHAR(30) PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            name_en VARCHAR(100),
            target_pct DECIMAL(5,2) NOT NULL,
            description TEXT,
            color VARCHAR(20),
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 3. Risk profiles (4 investor profiles)
    connector.execute("""
        CREATE TABLE IF NOT EXISTS risk_profiles (
            id INTEGER PRIMARY KEY,
            name VARCHAR(50) NOT NULL UNIQUE,
            name_en VARCHAR(50),
            is_active BOOLEAN DEFAULT FALSE,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 4. Per-class target allocations for each risk profile
    connector.execute("""
        CREATE TABLE IF NOT EXISTS risk_profile_allocations (
            id INTEGER PRIMARY KEY,
            profile_id INTEGER NOT NULL,
            class_id INTEGER NOT NULL,
            target_pct DECIMAL(5,2) NOT NULL,
            UNIQUE(profile_id, class_id)
        )
    """)
    
    # 5. Classification rules (explicit + regex mappings)
    connector.execute("""
        CREATE TABLE IF NOT EXISTS classification_rules (
            id INTEGER PRIMARY KEY,
            rule_type VARCHAR(20) NOT NULL,
            pattern VARCHAR(500) NOT NULL,
            class_id INTEGER,
            tier_id VARCHAR(30),
            priority INTEGER DEFAULT 100,
            source VARCHAR(50) DEFAULT 'seed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(rule_type, pattern)
        )
    """)
    
    # 6. Audit log for classification changes
    connector.execute("""
        CREATE TABLE IF NOT EXISTS classification_audit_log (
            id INTEGER PRIMARY KEY,
            asset_id VARCHAR(50) NOT NULL,
            old_class_id INTEGER,
            new_class_id INTEGER,
            old_tier_id VARCHAR(30),
            new_tier_id VARCHAR(30),
            method VARCHAR(50) NOT NULL,
            changed_by VARCHAR(100) DEFAULT 'system',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        )
    """)
