-- Migration 013: North Star panel — cash_flow_tags, unforced_errors (PRD
-- 2026-07-07 F3, Batch B6).
--
-- cash_flow_tags: additive classification layer over existing transactions /
--   income_expense_monthly rows (D6 — no changes to those tables). One row
--   per (source_table, source_row_key). classification is one of
--   external_contribution | internal_transfer | income_reinvested.
--   tagged_by='heuristic' rows may be re-tagged by a later heuristic run or
--   overwritten by a manual tag; tagged_by='manual' rows are never overwritten
--   by the heuristic (src/services/north_star.py::classify_flows_heuristic).
-- unforced_errors: manual execution-failure log (F3.3), seeded with the June
--   2026 RSU Divest quota zero-fill incident. est_cost_cny left NULL when
--   unknown — never fabricated (Cross-Cutting Requirement 3).
--
-- NOTE: no string literal in this file may contain a semicolon — several test
-- migration-replay helpers (tests/verification/test_monthly_verifier.py,
-- tests/api/test_operations_routes.py) split the file naively on ';'.

CREATE SEQUENCE IF NOT EXISTS seq_cash_flow_tags_id START 1;
CREATE TABLE IF NOT EXISTS cash_flow_tags (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_cash_flow_tags_id'),
    source_table VARCHAR(50) NOT NULL,
    source_row_key VARCHAR(100) NOT NULL,
    classification VARCHAR(30) NOT NULL,
    tagged_by VARCHAR(20) NOT NULL DEFAULT 'heuristic',
    amount_cny DECIMAL(20,2),
    flow_date DATE,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Idempotency guard for upserts (one tag per source row). DuckDB supports
-- CREATE UNIQUE INDEX on a table without a UNIQUE constraint clause.
CREATE UNIQUE INDEX IF NOT EXISTS idx_cash_flow_tags_source
    ON cash_flow_tags(source_table, source_row_key);

CREATE SEQUENCE IF NOT EXISTS seq_unforced_errors_id START 1;
CREATE TABLE IF NOT EXISTS unforced_errors (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_unforced_errors_id'),
    error_date DATE NOT NULL,
    description TEXT NOT NULL,
    est_cost_cny DECIMAL(20,2),
    root_cause TEXT,
    linked_rule VARCHAR(200),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- unforced_errors seed moved to the seed-pack system (Program OSR WS-3c,
-- 2026-08-17) — src.database.seed_loader.seed_demo_content(), gated on
-- $UIS_SEED_PROFILE. Safe: this migration only ever runs once per DB
-- (schema_version gate), so removing the INSERT has zero effect on an
-- already-migrated database.
