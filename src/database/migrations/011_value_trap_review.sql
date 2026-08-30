-- Migration 011: Loss-side mandatory review trigger (F2, Batch B3)
-- Additive new table only. No changes to holdings/transactions; F2's scan
-- (src/services/value_trap.py) reads holdings read-only (Rule 3: per-asset
-- latest snapshot via GROUP BY asset_id CTE, never global MAX(snapshot_date)).
--
-- status: open | ruled.
-- trigger_threshold_pct: the configured/escalated threshold crossed to open
--   this review row (e.g. -25.0, or -35.0 after a prior 'hold' ruling).
-- unrealized_return_pct: the loss observed at trigger/refresh time (may drift
--   further while the review stays 'open' — refreshed_at tracks each rescan).
-- thesis_restated / falsification_check / would_buy_today: the three F2.3
--   mandatory review questions, filled in on the owner's PUT ruling.
-- ruling: hold_with_thesis | trim | liquidate. NULL while status='open'.
-- adversarial_ack: must be TRUE to save a 'liquidate' ruling (F2.3/PRD gate;
--   enforced in the API route, not just here — this column just persists it).
-- next_review_date: owner-set next check-in date from the ruling form.
-- last_reviewed_at / last_ruling / next_trigger_threshold_pct: escalation
--   ladder state (F2.2) — set when a 'hold_with_thesis' ruling is saved, so a
--   later re-open (new row) can reference what the ladder produced.

CREATE SEQUENCE IF NOT EXISTS seq_value_trap_reviews_id START 1;
CREATE TABLE IF NOT EXISTS value_trap_reviews (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_value_trap_reviews_id'),
    asset_id VARCHAR(50) NOT NULL,
    asset_name VARCHAR(200),
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    trigger_threshold_pct DECIMAL(10,4) NOT NULL,
    unrealized_return_pct DECIMAL(10,4),
    memo_id VARCHAR(50),
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    refreshed_at TIMESTAMP,
    thesis_restated TEXT,
    falsification_check TEXT,
    would_buy_today TEXT,
    ruling VARCHAR(30),
    adversarial_ack BOOLEAN DEFAULT FALSE,
    next_review_date DATE,
    last_reviewed_at TIMESTAMP,
    last_ruling VARCHAR(30),
    next_trigger_threshold_pct DECIMAL(10,4)
);
