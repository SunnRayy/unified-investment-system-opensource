-- Migration 010: Process-based verification schema foundation (F1.1, Batch B1)
-- Additive columns on trade_logs only. No destructive changes, no scoring logic —
-- that is Batch B2 (process_scorer.py) and Batch B3 (value_trap_review).
--
-- rule_bucket: compliance | value | ratio | liquidity (D1 — see config/verification.yaml
--   bucket_map for the asset->bucket default classification used by the backfill script).
-- memo_id: authorizing memo reference, e.g. '2026-Q2-010-v2'. Nullable for backfilled rows.
-- order_origin: auto_dca | conditional_order | manual (also used by F5 contrarian split).
-- process_authorized / process_params_ok / process_data_verified: the three F1.2 process
--   checks. NULL = not yet evaluated (distinct from FALSE = evaluated and failed).
-- process_checked_at / process_notes: when the process checks were last entered, and
--   free-text context.
-- verdict_archived: archive of the pre-program `verdict` value (D2/Cross-cutting Req 1 —
--   old verdict data is archived, never destroyed). The backfill UPDATE below copies any
--   existing verdict into verdict_archived exactly once (guarded by IS NULL so re-running
--   this file is idempotent and never overwrites a later archival).

ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS rule_bucket VARCHAR(20);
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS memo_id VARCHAR(50);
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS order_origin VARCHAR(20);
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS process_authorized BOOLEAN;
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS process_params_ok BOOLEAN;
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS process_data_verified BOOLEAN;
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS process_checked_at TIMESTAMP;
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS process_notes TEXT;
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS verdict_archived VARCHAR(50);

-- Archive existing verdicts (never destroy verdict data — Cross-cutting Req 1 / D2).
UPDATE trade_logs
   SET verdict_archived = verdict
 WHERE verdict IS NOT NULL
   AND verdict_archived IS NULL;
