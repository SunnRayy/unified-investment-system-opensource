-- Phase 3: Decision Recording schema migration
-- Adds adoption tracking to insights, structured fields to deviation_actions
-- STATUS: RETIRED — this file is NEVER applied by run_migrations().
--   The insights columns (ai_model, adopted, etc.) are superseded by later
--   inline migrations.  deviation_actions table no longer exists in the schema.
--   Folded into the versioned migration system (Pass F Batch 3, 2026-06-04).
--   Do not re-apply or reference this file.

-- === insights table: add 7 PRD v5 fields ===
ALTER TABLE insights ADD COLUMN IF NOT EXISTS recommendation_id INTEGER;
ALTER TABLE insights ADD COLUMN IF NOT EXISTS adopted BOOLEAN;
ALTER TABLE insights ADD COLUMN IF NOT EXISTS adoption_date DATE;
ALTER TABLE insights ADD COLUMN IF NOT EXISTS outcome_accuracy DECIMAL(5,2);
ALTER TABLE insights ADD COLUMN IF NOT EXISTS ai_model VARCHAR(50);
ALTER TABLE insights ADD COLUMN IF NOT EXISTS confidence_score DECIMAL(3,2);
ALTER TABLE insights ADD COLUMN IF NOT EXISTS tags JSON;

-- === deviation_actions table: add structured allocation fields ===
ALTER TABLE deviation_actions ADD COLUMN IF NOT EXISTS asset_class VARCHAR(50);
ALTER TABLE deviation_actions ADD COLUMN IF NOT EXISTS current_pct DECIMAL(5,2);
ALTER TABLE deviation_actions ADD COLUMN IF NOT EXISTS target_pct DECIMAL(5,2);
ALTER TABLE deviation_actions ADD COLUMN IF NOT EXISTS tolerance_pct DECIMAL(5,2);
ALTER TABLE deviation_actions ADD COLUMN IF NOT EXISTS is_within_tolerance BOOLEAN;
