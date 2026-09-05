
-- Migration: Add Authority Rules (AIA/PIS Conflict Resolution)
-- Date: 2026-01-25
-- STATUS: RETIRED — this file is NEVER applied by run_migrations().
--   The holdings columns (is_shadow, authority_source) are in schema.sql.
--   source_authority_rules table was dropped via Migration 16 (Pass F, 2026-06-04).
--   Do not re-apply or reference this file.

-- 1. Add columns to holdings table
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS is_shadow BOOLEAN DEFAULT FALSE;
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS authority_source VARCHAR(50);

-- 2. Create source_authority_rules table
CREATE SEQUENCE IF NOT EXISTS seq_source_authority_rules_id START 1;
CREATE TABLE IF NOT EXISTS source_authority_rules (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_source_authority_rules_id'),
    canonical_id_pattern VARCHAR(100) NOT NULL,
    authoritative_source VARCHAR(50) NOT NULL,
    priority INTEGER DEFAULT 100,
    effective_date DATE DEFAULT CURRENT_DATE,
    notes VARCHAR(500),
    UNIQUE(canonical_id_pattern, authoritative_source)
);
