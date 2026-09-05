-- Migration 007: Add title to insights (Phase 5B)
-- STATUS: RETIRED — this file is NEVER applied by run_migrations().
--   insights.title is now V3 in the versioned migration system
--   (Pass F Batch 3, 2026-06-04).  Do not re-apply or reference this file.
ALTER TABLE insights ADD COLUMN IF NOT EXISTS title VARCHAR(200);
