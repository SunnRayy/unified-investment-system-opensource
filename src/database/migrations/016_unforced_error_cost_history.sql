-- Migration 016: Add cost_edit_history to unforced_errors (R2-7.5).
-- Stores a JSON array [{ts, old, new}] tracking est_cost_cny edits.
-- No semicolons inside string literals (migration convention).
-- ALTER TABLE ... ADD COLUMN IF NOT EXISTS is idempotent on DuckDB.

ALTER TABLE unforced_errors ADD COLUMN IF NOT EXISTS cost_edit_history TEXT