-- Migration 014: Insight Library governance (PRD 2026-07-07 F6, Batch B7).
--
-- Promote gate: confidence >= 70% OR validated_cases >= 3 (src/services/
--   ai_advisor/insight_manager.py::_check_promotion_gate), applied at every
--   step of the raw -> recurring -> validated -> principle ladder, not just
--   the final step -- PRD intent is stopping one-click promotion of weak
--   insights anywhere in the ladder.
-- validated_cases / validated_case_links: manually incremented via
--   POST /ai-advisor/insights/{id}/validated-cases {link, note?}; each call
--   appends {link, note, added_at} to validated_case_links (JSON array) and
--   increments validated_cases by 1.
-- rule_layer: required classification for governed insights -- 'principle'
--   (candidate for the system prompt) or 'checklist_item' (exported to the
--   grouped checklist document, see GET /ai-advisor/insights/checklist-export).
--   Nullable for legacy rows written before this migration.
-- rule_citations: v1 manual tick UI -- one row per (insight, memo) citation.
--   quarter is derived from cited_at at insert time (e.g. '2026-Q3') so the
--   quarterly governance report (GET /ai-advisor/insights/governance-report)
--   can count citations per rule per quarter without re-deriving from a
--   timestamp range every time.
--
-- NOTE: no string literal in this file may contain a semicolon -- several
-- test migration-replay helpers (tests/api/test_operations_routes.py,
-- tests/verification/test_monthly_verifier.py) split the file naively on ';'.

ALTER TABLE ai_insights ADD COLUMN IF NOT EXISTS validated_cases INTEGER DEFAULT 0;
ALTER TABLE ai_insights ADD COLUMN IF NOT EXISTS validated_case_links JSON;
ALTER TABLE ai_insights ADD COLUMN IF NOT EXISTS rule_layer VARCHAR(20);
-- rule_layer values: principle | checklist_item (NULL for legacy rows)

CREATE SEQUENCE IF NOT EXISTS seq_rule_citations_id START 1;
CREATE TABLE IF NOT EXISTS rule_citations (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_rule_citations_id'),
    insight_id INTEGER NOT NULL,
    memo_id VARCHAR(50),
    cited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    quarter VARCHAR(7),
    note VARCHAR(300)
);
