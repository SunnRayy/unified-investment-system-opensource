-- Migration 017: Add rule_id provenance to cash_flow_tags (WS3).
-- Records which rule tagged each row (e.g. same_day_transfer_pair, rsu_vest).
-- NULL for manual tags. IF NOT EXISTS makes this idempotent on DuckDB.

ALTER TABLE cash_flow_tags ADD COLUMN IF NOT EXISTS rule_id VARCHAR