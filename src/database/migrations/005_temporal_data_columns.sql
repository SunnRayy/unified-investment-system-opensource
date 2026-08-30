-- Migration 005: Add temporal data columns
-- STATUS: RETIRED — this file is NEVER applied by run_migrations().
--   holdings.price_updated_at is now V1 and transactions.is_provisional is V2
--   in the versioned migration system (Pass F Batch 3, 2026-06-04).
--   Do not re-apply or reference this file.
--
-- 1. Add price_updated_at to holdings for Layer 3 (DSA) price freshness tracking
ALTER TABLE holdings ADD COLUMN IF NOT EXISTS price_updated_at TIMESTAMP;

-- 2. Add is_provisional to transactions for Layer 2 (AIA) provisional trades
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS is_provisional BOOLEAN DEFAULT FALSE;
