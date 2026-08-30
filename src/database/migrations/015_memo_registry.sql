-- Migration 015: Memo registry + asset linkage (Fix 2, 2026-07-10 fix-request).
--
-- memo_registry: one row per investment-thesis memo.
--   memo_id: human-readable identifier (e.g. '2026-Q2-007').
--   status: active | retired.
--   falsification_summary: one-liner capturing key falsification conditions.
--   doc_link: optional URL or file path to the full memo document.
-- memo_asset_map: many-to-many between memos and asset_ids.
--   Allows one memo to cover multiple assets (e.g. Q2-007 covers both
--   900013 and 900014) and one asset to be covered by multiple memos.
-- asset_memo_confirmations: per-asset owner acknowledgement that no memo
--   exists. When confirmed_no_memo = TRUE the context panel may display
--   "no memo on record" -- absent this confirmation the unresolved warning
--   is shown instead.  Separate table (not a column on value_trap_reviews)
--   so the confirmation persists across multiple review cycles for the same
--   asset.
--
-- Seeds moved to the seed-pack system (Program OSR WS-3c, 2026-08-17) —
-- src.database.seed_loader.seed_demo_content(), gated on $UIS_SEED_PROFILE.
-- This migration is DDL-only; the owner's production DB already has its real
-- memo rows from when this migration last ran with seeds inline (safe: a
-- migration body only ever runs once per DB via the schema_version gate,
-- so removing the INSERT here has zero effect on an already-migrated DB).
CREATE TABLE IF NOT EXISTS memo_registry (
    memo_id VARCHAR(50) PRIMARY KEY,
    title VARCHAR(300) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    falsification_summary TEXT,
    doc_link VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memo_asset_map (
    memo_id VARCHAR(50) NOT NULL,
    asset_id VARCHAR(50) NOT NULL,
    PRIMARY KEY (memo_id, asset_id)
);

CREATE TABLE IF NOT EXISTS asset_memo_confirmations (
    asset_id VARCHAR(50) PRIMARY KEY,
    confirmed_no_memo BOOLEAN NOT NULL DEFAULT TRUE,
    confirmed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
