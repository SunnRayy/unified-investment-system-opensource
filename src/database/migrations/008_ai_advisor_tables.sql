-- Migration 008: AI Advisor Tables
-- Creates tables for LLM usage tracking, AI reports, insights, and behavioral logs.

-- Sequences
CREATE SEQUENCE IF NOT EXISTS llm_usage_seq START 1;
CREATE SEQUENCE IF NOT EXISTS ai_reports_seq START 1;
CREATE SEQUENCE IF NOT EXISTS ai_insights_seq START 1;
CREATE SEQUENCE IF NOT EXISTS ai_behavioral_log_seq START 1;

-- llm_usage: tracks every LLM call (including failed ones)
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY DEFAULT nextval('llm_usage_seq'),
    report_type VARCHAR,           -- 'brief', 'review', 'questions', 'unknown'
    model_used VARCHAR NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    cost_estimate_usd DECIMAL(10,6),
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_message VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ai_reports: stores brief/review outputs
CREATE TABLE IF NOT EXISTS ai_reports (
    id INTEGER PRIMARY KEY DEFAULT nextval('ai_reports_seq'),
    report_type VARCHAR NOT NULL,  -- 'brief', 'review'
    title VARCHAR,
    context_config_json VARCHAR,   -- JSON: tier settings used
    content_json VARCHAR NOT NULL, -- JSON: 5-section dict
    content_markdown VARCHAR,      -- derived markdown version
    model_used VARCHAR,
    period_start DATE,             -- for reviews: period covered
    period_end DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ai_insights: extracted learnings with lifecycle
CREATE TABLE IF NOT EXISTS ai_insights (
    id INTEGER PRIMARY KEY DEFAULT nextval('ai_insights_seq'),
    source_report_id INTEGER,      -- FK to ai_reports.id (nullable -- may be user-created)
    category VARCHAR,              -- 'risk', 'timing', 'sizing', 'strategy', 'process'
    title VARCHAR NOT NULL,
    body VARCHAR NOT NULL,
    tags VARCHAR,                  -- comma-separated tags
    confidence DECIMAL(3,2),       -- 0.0-1.0
    status VARCHAR DEFAULT 'raw',  -- 'raw', 'recurring', 'validated', 'principle', 'deprecated'
    recurrence_count INTEGER DEFAULT 1,
    entity_refs VARCHAR,           -- comma-separated asset_ids this insight references
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ai_behavioral_log: computed metric scores per dimension over time
CREATE TABLE IF NOT EXISTS ai_behavioral_log (
    id INTEGER PRIMARY KEY DEFAULT nextval('ai_behavioral_log_seq'),
    dimension VARCHAR NOT NULL,    -- 'contrarian_tendency', 'position_sizing_discipline', etc.
    score DECIMAL(5,4),            -- 0.0-1.0 normalized score
    raw_value DECIMAL(10,4),       -- raw computed value (%, days, etc.)
    computation_window_days INTEGER,
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata_json VARCHAR          -- extra context for the computation
);
