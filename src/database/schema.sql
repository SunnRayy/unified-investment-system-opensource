-- src/database/schema.sql
-- Huinsight Database Schema v6 (with v3 pipeline tables)

-- 1. Holdings table
CREATE SEQUENCE IF NOT EXISTS seq_holdings_id START 1;
CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_holdings_id'),
    snapshot_date DATE NOT NULL,
    asset_id VARCHAR(50) NOT NULL,
    asset_name VARCHAR(200),
    asset_type VARCHAR(100),
    quantity DECIMAL(20,8),
    unit VARCHAR(20),
    cost_price_unit DECIMAL(20,8),
    market_price_unit DECIMAL(20,8),
    market_value DECIMAL(20,2),
    currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
    account VARCHAR(100),
    source_system VARCHAR(50),
    derived_from_transaction_id INTEGER,
    verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_shadow BOOLEAN DEFAULT FALSE,
    authority_source VARCHAR(50),
    price_updated_at TIMESTAMP,
    price_source VARCHAR,
    UNIQUE(snapshot_date, asset_id, source_system)
);

-- 2. Transactions table
CREATE SEQUENCE IF NOT EXISTS seq_transactions_id START 1;
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_transactions_id'),
    transaction_date DATE NOT NULL,
    asset_id VARCHAR(50) NOT NULL,
    asset_name VARCHAR(200),
    transaction_type VARCHAR(50) NOT NULL,
    quantity DECIMAL(20,8),
    price_unit DECIMAL(20,8),
    amount_gross DECIMAL(20,2),
    amount_net DECIMAL(20,2),
    commission_fee DECIMAL(20,4),
    currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
    account VARCHAR(100),
    memo TEXT,
    source_system VARCHAR(50),
    verified BOOLEAN DEFAULT FALSE,
    is_provisional BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Trade logs table (AI Advisor records)
CREATE SEQUENCE IF NOT EXISTS seq_trade_logs_id START 1;
CREATE TABLE IF NOT EXISTS trade_logs (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_trade_logs_id'),
    log_date DATE NOT NULL,
    asset_id VARCHAR(50) NOT NULL,
    asset_name VARCHAR(200),
    action VARCHAR(20) NOT NULL,
    price DECIMAL(20,8),
    quantity DECIMAL(20,8),
    amount DECIMAL(20,2),
    pnl_pct DECIMAL(10,4),
    pnl_amount DECIMAL(20,2),
    decision_reason TEXT,
    ai_suggestion TEXT,
    suggestion_source VARCHAR(50),
    verification_date DATE,
    verification_result VARCHAR,
    linked_transaction_id INTEGER,
    user_notes TEXT,
    vote_breakdown JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    verification_block_reason VARCHAR,
    FOREIGN KEY (linked_transaction_id) REFERENCES transactions(id)
);

-- 4. Insights table
CREATE SEQUENCE IF NOT EXISTS seq_insights_id START 1;
CREATE TABLE IF NOT EXISTS insights (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_insights_id'),
    insight_date DATE NOT NULL,
    insight_type VARCHAR(50) NOT NULL,
    category VARCHAR(100),
    content TEXT NOT NULL,
    user_notes TEXT,
    observation_source VARCHAR(100),
    verified BOOLEAN DEFAULT FALSE,
    verification_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    recommendation_id INTEGER,
    adopted BOOLEAN,
    adoption_date DATE,
    outcome_accuracy DECIMAL(5,2),
    ai_model VARCHAR(50),
    confidence_score DECIMAL(3,2),
    tags JSON,
    title VARCHAR(200)
);

-- 5. Committee Decisions — Dropped via Migration 16 (Pass F)
-- 6. Market Events — Dropped via Migration 16 (Pass F)

-- 7. Thresholds table
CREATE SEQUENCE IF NOT EXISTS seq_thresholds_id START 1;
CREATE TABLE IF NOT EXISTS thresholds (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_thresholds_id'),
    signal_type VARCHAR(50) NOT NULL,
    market VARCHAR(20) NOT NULL,
    condition_desc TEXT NOT NULL,
    threshold_value DECIMAL(20,4),
    threshold_unit VARCHAR(20),
    adjustable BOOLEAN DEFAULT TRUE,
    historical_win_rate DECIMAL(5,2),
    data_source VARCHAR(200),
    notes TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. Market daily table
CREATE SEQUENCE IF NOT EXISTS seq_market_daily_id START 1;
CREATE TABLE IF NOT EXISTS market_daily (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_market_daily_id'),
    code VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    open DECIMAL(20,4),
    high DECIMAL(20,4),
    low DECIMAL(20,4),
    close DECIMAL(20,4),
    volume DECIMAL(30,2),
    amount DECIMAL(30,2),
    pct_chg DECIMAL(10,4),
    ma5 DECIMAL(20,4),
    ma10 DECIMAL(20,4),
    ma20 DECIMAL(20,4),
    pe_ttm DECIMAL(20,4),
    pb DECIMAL(20,4),
    data_source VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(code, date)
);

-- 7. Deviation actions table
CREATE SEQUENCE IF NOT EXISTS seq_deviation_actions_id START 1;
CREATE TABLE IF NOT EXISTS deviation_actions (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_deviation_actions_id'),
    detected_date DATE NOT NULL,
    deviation_type VARCHAR(100),
    deviation_pct DECIMAL(10,4),
    planned_action TEXT,
    tolerance_period VARCHAR(20),
    target_resolve_date DATE,
    status VARCHAR(50) DEFAULT 'observing',
    resolution_notes TEXT,
    user_decision TEXT,
    -- Phase 3: Structured allocation fields
    asset_class VARCHAR(50),
    current_pct DECIMAL(5,2),
    target_pct DECIMAL(5,2),
    tolerance_pct DECIMAL(5,2),
    is_within_tolerance BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. Verification logs table (Phase 2 reconciliation + Phase 4 monthly KPI)
CREATE SEQUENCE IF NOT EXISTS seq_verification_logs_id START 1;
CREATE TABLE IF NOT EXISTS verification_logs (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_verification_logs_id'),
    verification_date DATE NOT NULL,
    -- Phase 2: Reconciliation columns
    source_a VARCHAR(50),
    source_b VARCHAR(50),
    data_type VARCHAR(50),
    discrepancy_count INTEGER,
    discrepancy_details JSON,
    user_confirmed BOOLEAN DEFAULT FALSE,
    resolution_action TEXT,
    -- Phase 4: Monthly verification KPI columns
    verification_type VARCHAR(50),
    period_start DATE,
    period_end DATE,
    ai_hit_rate DECIMAL(5,2),
    ai_hit_rate_by_model JSON,
    adoption_rate DECIMAL(5,2),
    portfolio_return DECIMAL(10,4),
    benchmark_return DECIMAL(10,4),
    alpha DECIMAL(10,4),
    max_allocation_drift DECIMAL(5,2),
    drift_details JSON,
    total_insights INTEGER,
    key_lessons JSON,
    generated_by VARCHAR(50),
    report_path VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 9. Exchange rates table — Dropped via Migration 16 (Pass F)
-- 10. Economic indicators table — Dropped via Migration 16 (Pass F)

-- Import adapter staging/control tables
CREATE SEQUENCE IF NOT EXISTS seq_import_adapter_runs_id START 1;
CREATE TABLE IF NOT EXISTS import_adapter_runs (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_import_adapter_runs_id'),
    adapter_key VARCHAR NOT NULL,
    import_type VARCHAR NOT NULL,
    filename VARCHAR NOT NULL,
    file_path VARCHAR,
    uploaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR NOT NULL DEFAULT 'uploaded',
    detected_headers JSON,
    column_mapping JSON,
    row_counts_json JSON,
    warnings_json JSON,
    errors_json JSON
);

CREATE SEQUENCE IF NOT EXISTS seq_import_adapter_staged_rows_id START 1;
CREATE TABLE IF NOT EXISTS import_adapter_staged_rows (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_import_adapter_staged_rows_id'),
    run_id INTEGER NOT NULL,
    row_index INTEGER NOT NULL,
    row_kind VARCHAR NOT NULL,
    normalized_payload_json JSON NOT NULL,
    validation_status VARCHAR NOT NULL,
    validation_messages_json JSON,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    synced_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS import_adapter_approvals (
    adapter_key VARCHAR PRIMARY KEY,
    approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_by VARCHAR,
    source_system VARCHAR NOT NULL,
    asset_prefixes_json JSON NOT NULL,
    authority_priority INTEGER NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    generated_reader_key VARCHAR
);

-- 11. Circuit breaker logs table
CREATE SEQUENCE IF NOT EXISTS seq_circuit_breaker_logs_id START 1;
CREATE TABLE IF NOT EXISTS circuit_breaker_logs (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_circuit_breaker_logs_id'),
    trigger_date TIMESTAMP NOT NULL,
    trigger_type VARCHAR(50) NOT NULL,
    trigger_value DECIMAL(10,4),
    threshold_value DECIMAL(10,4),
    action_taken VARCHAR(100),
    resolved_at TIMESTAMP,
    resolution_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 12. Target allocations table (Compass Report support)
CREATE SEQUENCE IF NOT EXISTS seq_target_allocations_id START 1;
CREATE TABLE IF NOT EXISTS target_allocations (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_target_allocations_id'),
    asset_class VARCHAR(50) NOT NULL,
    target_pct DECIMAL(5,2) NOT NULL,
    tolerance_pct DECIMAL(5,2) DEFAULT 5,
    taxonomy_type VARCHAR(20) NOT NULL DEFAULT 'Asset Class',
    priority INTEGER DEFAULT 1,
    notes TEXT,
    effective_date DATE NOT NULL,
    expired_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset_class, taxonomy_type, effective_date)
);

-- 13. Sync audit logs table (cross-system data validation)
CREATE SEQUENCE IF NOT EXISTS seq_sync_audit_logs_id START 1;
CREATE TABLE IF NOT EXISTS sync_audit_logs (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_sync_audit_logs_id'),
    sync_timestamp TIMESTAMP NOT NULL,
    source_system VARCHAR(50) NOT NULL,
    target_table VARCHAR(100) NOT NULL,
    record_key VARCHAR(200) NOT NULL,
    conflict_type VARCHAR(50),
    source_value JSON,
    target_value JSON,
    resolution VARCHAR(50),
    resolved_by VARCHAR(100),
    resolution_notes TEXT,
    is_resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 14. RSU vesting schedules table — Dropped via Migration 16 (Pass F)

-- ═══════════════════════════════════════════════════════════════════════════
-- V3 PIPELINE TABLES (Identity Layer, Taxonomy, Validation)
-- ═══════════════════════════════════════════════════════════════════════════

-- 15. Asset Registry - Canonical asset IDs
CREATE SEQUENCE IF NOT EXISTS seq_asset_registry_id START 1;
CREATE TABLE IF NOT EXISTS asset_registry (
    canonical_id VARCHAR(50) PRIMARY KEY,
    display_name VARCHAR(200) NOT NULL,
    asset_class VARCHAR(50),
    asset_subclass VARCHAR(50),
    extended_classification JSON,
    tier VARCHAR(20),
    is_rebalanceable BOOLEAN DEFAULT TRUE,
    risk_level VARCHAR(20),
    base_currency VARCHAR(10) DEFAULT 'CNY',
    is_active BOOLEAN DEFAULT TRUE,
    last_price_update TIMESTAMP,
    sync_timestamp TIMESTAMP,
    is_pending BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 16. Asset Source Mappings - Link source IDs to canonical IDs
CREATE SEQUENCE IF NOT EXISTS seq_asset_source_mappings_id START 1;
CREATE TABLE IF NOT EXISTS asset_source_mappings (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_asset_source_mappings_id'),
    canonical_id VARCHAR(50) NOT NULL,
    source_system VARCHAR(50) NOT NULL,
    source_id VARCHAR(100) NOT NULL,
    mapping_type VARCHAR(20) DEFAULT 'manual',
    confidence DECIMAL(3,2) DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_system, source_id)
);

CREATE INDEX IF NOT EXISTS idx_source_mappings_lookup
ON asset_source_mappings(source_system, source_id);

-- 17. Schema Snapshots — Dropped via Migration 16 (Pass F)
-- 18. Asset Taxonomy — Dropped via Migration 16 (Pass F)
--     No SQL table reference in src/ -- only test fixtures that build their
--     own in-memory schemas. taxonomy_classes is the live replacement.

-- 19. Current Allocations - Snapshot of allocation percentages
CREATE SEQUENCE IF NOT EXISTS seq_current_allocations_id START 1;
CREATE TABLE IF NOT EXISTS current_allocations (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_current_allocations_id'),
    asset_class VARCHAR(50) NOT NULL,
    asset_subclass VARCHAR(50),
    current_pct DECIMAL(5,2),
    market_value DECIMAL(15,2),
    is_rebalanceable BOOLEAN DEFAULT TRUE,
    snapshot_date DATE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset_class, asset_subclass, snapshot_date)
);

-- 20. Source Authority Rules — Dropped via Migration 16 (Pass F)

-- 21. Financial Summary Balance Sheet Monthly snapshots
CREATE SEQUENCE IF NOT EXISTS seq_balance_sheet_monthly_id START 1;
CREATE TABLE IF NOT EXISTS balance_sheet_monthly (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_balance_sheet_monthly_id'),
    record_key VARCHAR(120) NOT NULL,
    snapshot_date DATE,
    payload JSON NOT NULL,
    source_system VARCHAR(50) NOT NULL DEFAULT 'Financial_Summary_Excel',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(record_key, source_system)
);

-- 22. Financial Summary Income/Expense Monthly rows
CREATE SEQUENCE IF NOT EXISTS seq_income_expense_monthly_id START 1;
CREATE TABLE IF NOT EXISTS income_expense_monthly (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_income_expense_monthly_id'),
    record_key VARCHAR(120) NOT NULL,
    transaction_date DATE,
    payload JSON NOT NULL,
    source_system VARCHAR(50) NOT NULL DEFAULT 'Financial_Summary_Excel',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(record_key, source_system)
);

-- ═══════════════════════════════════════════════════════════════════════════
-- INDEXES
-- ═══════════════════════════════════════════════════════════════════════════

-- Indexes
CREATE INDEX IF NOT EXISTS idx_holdings_date ON holdings(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_holdings_asset ON holdings(asset_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_trade_logs_date ON trade_logs(log_date);
CREATE INDEX IF NOT EXISTS idx_market_code_date ON market_daily(code, date);
CREATE INDEX IF NOT EXISTS idx_thresholds_type ON thresholds(signal_type, market);
-- idx_exchange_rates_date removed — table dropped via Migration 16 (Pass F)
CREATE INDEX IF NOT EXISTS idx_sync_audit_unresolved ON sync_audit_logs(is_resolved, source_system);
CREATE INDEX IF NOT EXISTS idx_target_allocations_class ON target_allocations(asset_class);
-- idx_rsu_vesting_status removed — table dropped via Migration 16 (Pass F)

-- V3 table indexes
CREATE INDEX IF NOT EXISTS idx_asset_registry_class ON asset_registry(asset_class, asset_subclass);
-- idx_asset_taxonomy_class, idx_asset_taxonomy_rebalanceable removed — table dropped via Migration 16 (Pass F)
CREATE INDEX IF NOT EXISTS idx_current_allocations_date ON current_allocations(snapshot_date);

-- 23. Goals table (Phase 6)
CREATE SEQUENCE IF NOT EXISTS seq_goals_id START 1;
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_goals_id'),
    name VARCHAR(200) NOT NULL,
    target_amount DECIMAL(20,2) NOT NULL,
    target_date DATE NOT NULL,
    current_amount DECIMAL(20,2) DEFAULT 0,
    monthly_contribution DECIMAL(20,2) DEFAULT 0,
    goal_type VARCHAR(50),
    status VARCHAR(20) DEFAULT 'active',
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 24. Market sentiment cache table (Phase 8)
CREATE TABLE IF NOT EXISTS market_sentiment_cache (
    indicator_key VARCHAR PRIMARY KEY,
    section VARCHAR,
    indicator_name VARCHAR,
    value DOUBLE,
    display_value VARCHAR,
    zone VARCHAR,
    zone_color VARCHAR,
    description VARCHAR,
    raw_json VARCHAR,
    updated_at TIMESTAMP
);

-- Added for Operations Audit Redesign
CREATE TABLE IF NOT EXISTS sync_audit_reports (
    id VARCHAR(36) PRIMARY KEY,   -- UUID
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    report_type VARCHAR(20) NOT NULL DEFAULT 'sync',  -- 'sync' or 'on_demand'
    net_worth_before DOUBLE,
    net_worth_after DOUBLE,
    net_worth_change_pct DOUBLE,
    asset_count_before INTEGER,
    asset_count_after INTEGER,
    by_source_before JSON,
    by_source_after JSON,
    integrity_passed INTEGER,
    integrity_total INTEGER,
    integrity_checks JSON,
    source_discrepancies JSON,
    reader_counts JSON,
    warnings JSON,
    alert BOOLEAN DEFAULT FALSE
);


-- V4.2 Migration: Decision scorecard columns
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS outcome_pct DECIMAL(10,4);
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS verdict VARCHAR(50);
-- verdict values: good_call | regret | missed_opportunity | bullet_dodged
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS decision_grade VARCHAR(10);
-- grade values: A | B (from monthly review files)
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS verification_status VARCHAR(20) DEFAULT 'pending';
-- verification_status values: pending | pending_window | verified | verification_blocked

-- V5.8.0 Migration: Decision feedback loop columns
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS verification_block_reason VARCHAR;

-- Migration 010 (V67, F1.1 process-based verification foundation, Batch B1):
-- bucket-aware process verification columns. See migrations/010_process_verification_schema.sql.
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS rule_bucket VARCHAR(20);
-- rule_bucket values: compliance | value | ratio | liquidity
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS memo_id VARCHAR(50);
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS order_origin VARCHAR(20);
-- order_origin values: auto_dca | conditional_order | manual
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS process_authorized BOOLEAN;
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS process_params_ok BOOLEAN;
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS process_data_verified BOOLEAN;
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS process_checked_at TIMESTAMP;
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS process_notes TEXT;
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS verdict_archived VARCHAR(50);
-- verdict_archived: archive of pre-program verdict values (D2 — never destroyed)

-- V5.8.0: Verdict audit trail
CREATE SEQUENCE IF NOT EXISTS seq_verdict_audit_id START 1;
CREATE TABLE IF NOT EXISTS verdict_audit (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_verdict_audit_id'),
    trade_id INTEGER NOT NULL,
    suggested_from_threshold VARCHAR,
    keyword_derived VARCHAR,
    final_verdict VARCHAR,
    mismatch BOOLEAN,
    both_matched BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- V5.10.0: Persisted insight-trade attribution links (link_type: auto_source or manual)
CREATE SEQUENCE IF NOT EXISTS seq_insight_trade_links_id START 1;
CREATE TABLE IF NOT EXISTS insight_trade_links (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_insight_trade_links_id'),
    insight_id INTEGER NOT NULL,
    trade_id INTEGER NOT NULL,
    link_type VARCHAR NOT NULL,
    confidence DECIMAL(3,2),
    rationale VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(insight_id, trade_id)
);

-- V4.2 Migration: Strategic Profile targets source tracking
ALTER TABLE target_allocations ADD COLUMN IF NOT EXISTS source VARCHAR(50);

-- V4.2: Strategy intelligence tables
CREATE SEQUENCE IF NOT EXISTS seq_strategy_memos_id START 1;
CREATE TABLE IF NOT EXISTS strategy_memos (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_strategy_memos_id'),
    memo_date DATE NOT NULL,
    title VARCHAR(300),
    strategic_bias VARCHAR(20),
    key_directives JSON,
    source_file VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(memo_date, title)
);

CREATE SEQUENCE IF NOT EXISTS seq_strategy_review_reports_id START 1;
CREATE TABLE IF NOT EXISTS strategy_review_reports (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_strategy_review_reports_id'),
    review_date DATE NOT NULL,
    allocation_alignment JSON,
    trading_frequency JSON,
    contrarian_score DECIMAL(5,2),
    contrarian_details JSON,
    profile_discrepancies JSON,
    overall_alignment VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Source upload history (Batch 11)
CREATE SEQUENCE IF NOT EXISTS seq_source_upload_history_id START 1;
CREATE TABLE IF NOT EXISTS source_upload_history (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_source_upload_history_id'),
    reader VARCHAR NOT NULL,
    filename VARCHAR NOT NULL,
    file_size_bytes BIGINT,
    uploaded_at TIMESTAMP NOT NULL,
    is_valid BOOLEAN,
    warnings JSON,
    previous_filename VARCHAR
);

-- asset_analyses: stores single-asset AI analysis results (V5.0)
CREATE SEQUENCE IF NOT EXISTS asset_analyses_seq START 1;
CREATE TABLE IF NOT EXISTS asset_analyses (
    id INTEGER PRIMARY KEY DEFAULT nextval('asset_analyses_seq'),
    asset_code VARCHAR(20) NOT NULL,
    asset_name VARCHAR(200),
    analysis_type VARCHAR(20) DEFAULT 'full',
    technical_signals JSON,
    llm_analysis JSON,
    llm_analysis_markdown VARCHAR,
    portfolio_context JSON,
    model_used VARCHAR,
    data_source VARCHAR(50),
    triggered_by VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_asset_analyses_code ON asset_analyses(asset_code);
CREATE INDEX IF NOT EXISTS idx_asset_analyses_created ON asset_analyses(created_at DESC);

-- V4.8 Migration: Sync history improvements — warning/info split and no-change detection
ALTER TABLE sync_audit_reports ADD COLUMN IF NOT EXISTS is_no_change BOOLEAN DEFAULT FALSE;
ALTER TABLE sync_audit_reports ADD COLUMN IF NOT EXISTS info_messages JSON;

-- A3b Migration: per-phase pipeline step results (names P0..P8 + finer-grained steps)
ALTER TABLE sync_audit_reports ADD COLUMN IF NOT EXISTS steps JSON;

-- position_deltas: tracks quantity changes between syncs for unlogged trade detection
CREATE SEQUENCE IF NOT EXISTS seq_position_deltas_id START 1;
CREATE TABLE IF NOT EXISTS position_deltas (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_position_deltas_id'),
    asset_id VARCHAR(50) NOT NULL,
    old_qty DECIMAL(20,8) DEFAULT 0,
    new_qty DECIMAL(20,8) DEFAULT 0,
    delta_qty DECIMAL(20,8) NOT NULL,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_system VARCHAR(50),
    old_snapshot_date DATE,
    new_snapshot_date DATE,
    confirmed BOOLEAN DEFAULT FALSE
);

-- Valuation Module tables (added Phase 1)
CREATE SEQUENCE IF NOT EXISTS seq_valuation_snapshots_id START 1;
CREATE TABLE IF NOT EXISTS valuation_snapshots (
  id INTEGER PRIMARY KEY DEFAULT nextval('seq_valuation_snapshots_id'),
  snapshot_date DATE NOT NULL,
  ticker VARCHAR(20) NOT NULL,
  display_name VARCHAR(200),
  row_kind VARCHAR(20) DEFAULT 'holding',
  linked_ticker VARCHAR(20),
  asset_id VARCHAR(50),
  asset_class VARCHAR(20) NOT NULL,
  pe_ttm DOUBLE,
  pe_forward DOUBLE,
  pb_ratio DOUBLE,
  peg_ratio DOUBLE,
  fcf_yield DOUBLE,
  dividend_yield DOUBLE,
  ev_ebitda DOUBLE,
  sec_yield DOUBLE,
  pe_ttm_pct DOUBLE,
  pe_fwd_pct DOUBLE,
  pb_pct DOUBLE,
  pct_years INTEGER,
  valuation_signal VARCHAR(10),
  signal_basis VARCHAR(200),
  rate_adjustment_factor DOUBLE,
  data_source VARCHAR(50),
  is_estimable BOOLEAN DEFAULT TRUE,
  notes TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(snapshot_date, ticker)
);

CREATE TABLE IF NOT EXISTS valuation_reference (
  ticker VARCHAR(20) NOT NULL,
  metric VARCHAR(20) NOT NULL,
  low_threshold DOUBLE NOT NULL,
  high_threshold DOUBLE NOT NULL,
  historical_mean DOUBLE,
  rate_sensitive BOOLEAN DEFAULT FALSE,
  pct_low_threshold FLOAT DEFAULT 30.0,
  pct_high_threshold FLOAT DEFAULT 70.0,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  notes TEXT,
  PRIMARY KEY (ticker, metric)
);

CREATE TABLE IF NOT EXISTS valuation_history (
  ticker VARCHAR(20) NOT NULL,
  metric VARCHAR(20) NOT NULL,
  observed_date DATE NOT NULL,
  value DOUBLE NOT NULL,
  source VARCHAR(50) NOT NULL,
  PRIMARY KEY (ticker, metric, observed_date)
);

CREATE INDEX IF NOT EXISTS idx_valuation_history_lookup
  ON valuation_history (ticker, metric, observed_date);

CREATE TABLE IF NOT EXISTS valuation_watchlist (
  ticker VARCHAR(20) PRIMARY KEY,
  display_name VARCHAR(200) NOT NULL,
  asset_type VARCHAR(20) NOT NULL,
  note TEXT,
  added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Auth credentials (single-row, id=1, version bump = global logout)
CREATE TABLE IF NOT EXISTS auth_credentials (
    id INTEGER PRIMARY KEY DEFAULT 1,
    password_hash VARCHAR NOT NULL,
    token_version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User profile (single-row, id=1 — persisted to GCS via DB flush)
CREATE TABLE IF NOT EXISTS user_profile (
    id INTEGER PRIMARY KEY DEFAULT 1,
    display_name VARCHAR,
    avatar_base64 TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS philosophy TEXT;
-- Program BIL: persisted UI / AI-advisor language ('en' | 'zh-CN').
-- NULLABLE WITH NO DEFAULT ON PURPOSE. A schema DEFAULT would not reliably
-- backfill the existing row (DuckDB's ADD COLUMN ... DEFAULT backfill semantics
-- are unverified here, and `philosophy` above set the precedent), and a fresh
-- install must land on 'en' while an existing Chinese-speaking instance must
-- stay Chinese. Connector migration V89 sets the value with an explicit data
-- step and warns when it is left NULL.
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS language VARCHAR;

-- Migration: add synced_at to import_adapter_staged_rows for idempotent sync
ALTER TABLE import_adapter_staged_rows ADD COLUMN IF NOT EXISTS synced_at TIMESTAMP;

-- Pass D: Classification tables (Migration 13)
-- These were previously created only during orchestrator sync via create_classification_tables().
-- Now part of the canonical schema so fresh installs and server restarts always have them.
CREATE TABLE IF NOT EXISTS taxonomy_classes (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    name_cn VARCHAR(100),
    parent_id INTEGER,
    level INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    is_rebalanceable BOOLEAN DEFAULT TRUE,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, parent_id)
);

CREATE TABLE IF NOT EXISTS asset_tiers (
    id VARCHAR(30) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    name_en VARCHAR(100),
    target_pct DECIMAL(5,2) NOT NULL,
    description TEXT,
    color VARCHAR(20),
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS risk_profiles (
    id INTEGER PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    name_en VARCHAR(50),
    is_active BOOLEAN DEFAULT FALSE,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS risk_profile_allocations (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL,
    class_id INTEGER NOT NULL,
    target_pct DECIMAL(5,2) NOT NULL,
    UNIQUE(profile_id, class_id)
);

CREATE TABLE IF NOT EXISTS classification_rules (
    id INTEGER PRIMARY KEY,
    rule_type VARCHAR(20) NOT NULL,
    pattern VARCHAR(500) NOT NULL,
    class_id INTEGER,
    tier_id VARCHAR(30),
    priority INTEGER DEFAULT 100,
    source VARCHAR(50) DEFAULT 'seed',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(rule_type, pattern)
);

CREATE TABLE IF NOT EXISTS classification_audit_log (
    id INTEGER PRIMARY KEY,
    asset_id VARCHAR(50) NOT NULL,
    old_class_id INTEGER,
    new_class_id INTEGER,
    old_tier_id VARCHAR(30),
    new_tier_id VARCHAR(30),
    method VARCHAR(50) NOT NULL,
    changed_by VARCHAR(100) DEFAULT 'system',
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);

-- Pass D: market_sentiment_cache newer columns (Migration 14)
-- These 3 columns were previously only added by ensure_sentiment_table() at request time,
-- which broke on read_only connections. Now part of the canonical schema.
ALTER TABLE market_sentiment_cache ADD COLUMN IF NOT EXISTS is_stale BOOLEAN DEFAULT FALSE;
ALTER TABLE market_sentiment_cache ADD COLUMN IF NOT EXISTS last_refresh_attempt TIMESTAMP;
ALTER TABLE market_sentiment_cache ADD COLUMN IF NOT EXISTS error_detail VARCHAR;

-- Pass D: Hot-path indexes (Migration 15)
CREATE INDEX IF NOT EXISTS idx_holdings_source_system ON holdings(source_system);
CREATE INDEX IF NOT EXISTS idx_holdings_is_shadow ON holdings(is_shadow);
CREATE INDEX IF NOT EXISTS idx_transactions_asset_id ON transactions(asset_id);
CREATE INDEX IF NOT EXISTS idx_trade_logs_linked_transaction_id ON trade_logs(linked_transaction_id);

-- Migration 011 (V68, F2 loss-side mandatory review trigger, Batch B3):
-- see migrations/011_value_trap_review.sql for full column documentation.
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

-- Migration 012 (V69, F4.3/F4.4/F4.6 metric governance, Batch B5):
-- see migrations/012_metric_governance.sql for full column documentation.
-- Migration 012: Metric governance — metric_catalog, data_fixes,
-- ruling_deferred_events (F4.3/F4.4/F4.6, PRD 2026-07-07, Batch B5).
--
-- metric_catalog: one row per governed metric. freshness_class drives F4.4
--   staleness evaluation (src/services/metric_governance.py::evaluate_reliability).
--   methodology_sensitive=TRUE metrics reject untagged ingestion writes via
--   require_methodology() (F4.3).
-- data_fixes: F4.6 fix backlog with a mandatory due_at. An open row whose
--   due_at has passed auto-flips its metric_key to UNRELIABLE at read time
--   (evaluate_reliability) — no mutation needed, purely time-based.
-- ruling_deferred_events: F4.4 audit trail — logged whenever a trigger
--   evaluator (F2 value_trap scan, future ladder/band checks) skips an asset
--   because the backing metric/data is stale or unreliable.
--
-- All seeds are idempotent (INSERT ... SELECT ... WHERE NOT EXISTS), so this
-- file is safe to re-run (schema.sql mirrors it verbatim for fresh in-memory
-- test databases via initialize_schema()).

CREATE TABLE IF NOT EXISTS metric_catalog (
    metric_key VARCHAR(100) PRIMARY KEY,
    source VARCHAR(100),
    methodology VARCHAR(100),
    freshness_class VARCHAR(10) NOT NULL DEFAULT 'slow',
    methodology_sensitive BOOLEAN DEFAULT FALSE,
    description TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE IF NOT EXISTS seq_data_fixes_id START 1;
CREATE TABLE IF NOT EXISTS data_fixes (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_data_fixes_id'),
    title VARCHAR(200) NOT NULL,
    description TEXT,
    metric_key VARCHAR(100),
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    due_at TIMESTAMP NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    closed_at TIMESTAMP
);

CREATE SEQUENCE IF NOT EXISTS seq_ruling_deferred_events_id START 1;
CREATE TABLE IF NOT EXISTS ruling_deferred_events (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_ruling_deferred_events_id'),
    metric_key VARCHAR(100),
    context VARCHAR(300),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- F4.3 — Buffett indicator methodology tagging (see
-- src/financial_analysis/macro_analyzer.py::_fetch_buffett_us_computed /
-- _fetch_fred_indicator). market_sentiment_cache previously had no
-- methodology/source column, so the fed-adjusted computed variant (Fed Z.1
-- NCBEILQ027S / GDP, ~194.9%) and the classic World Bank stock-market-cap/GDP
-- variant (DDDM01*, ~235%) were indistinguishable at read time.
ALTER TABLE market_sentiment_cache ADD COLUMN IF NOT EXISTS methodology VARCHAR(50);
ALTER TABLE market_sentiment_cache ADD COLUMN IF NOT EXISTS data_source VARCHAR(150);

-- ── Seeds: metric_catalog (idempotent) ──────────────────────────────────────
INSERT INTO metric_catalog (metric_key, source, methodology, freshness_class, methodology_sensitive, description)
SELECT 'buffett_indicator', 'FRED / World Bank', NULL, 'slow', TRUE,
       'US equity-market-cap-to-GDP overvaluation gauge — multiple methodology variants exist (classic TMC/GDP vs Fed-Z.1-adjusted).'
WHERE NOT EXISTS (SELECT 1 FROM metric_catalog WHERE metric_key = 'buffett_indicator');

INSERT INTO metric_catalog (metric_key, source, methodology, freshness_class, methodology_sensitive, description)
SELECT 'csi500_pe', 'AKShare / third-party', NULL, 'slow', TRUE,
       'CSI500 index PE — official CSI static PE vs third-party TTM PE are different methodologies.'
WHERE NOT EXISTS (SELECT 1 FROM metric_catalog WHERE metric_key = 'csi500_pe');

INSERT INTO metric_catalog (metric_key, source, methodology, freshness_class, methodology_sensitive, description)
SELECT 'vix', 'yfinance / FRED', 'cboe_vix', 'fast', FALSE,
       'CBOE Volatility Index.'
WHERE NOT EXISTS (SELECT 1 FROM metric_catalog WHERE metric_key = 'vix');

INSERT INTO metric_catalog (metric_key, source, methodology, freshness_class, methodology_sensitive, description)
SELECT 'fx_usd_cny', 'currency service', 'spot_rate', 'fast', FALSE,
       'USD/CNY spot exchange rate used for cross-currency valuation.'
WHERE NOT EXISTS (SELECT 1 FROM metric_catalog WHERE metric_key = 'fx_usd_cny');

INSERT INTO metric_catalog (metric_key, source, methodology, freshness_class, methodology_sensitive, description)
SELECT 'sp500_pe_percentile', 'FMP / akshare', NULL, 'slow', TRUE,
       'S&P 500 PE percentile vs historical distribution.'
WHERE NOT EXISTS (SELECT 1 FROM metric_catalog WHERE metric_key = 'sp500_pe_percentile');

INSERT INTO metric_catalog (metric_key, source, methodology, freshness_class, methodology_sensitive, description)
SELECT 'rebalance_discipline', 'allocation engine', 'allocation_engine_drift', 'slow', FALSE,
       'Rebalance-discipline / drift metric (F4.1 fixed to read the live allocation engine).'
WHERE NOT EXISTS (SELECT 1 FROM metric_catalog WHERE metric_key = 'rebalance_discipline');

-- data_fixes backlog entries moved to the seed-pack system (Program OSR
-- WS-3c, 2026-08-17) — src.database.seed_loader.seed_demo_content(), gated
-- on $UIS_SEED_PROFILE. The owner's real backlog is not shipped; schema.sql
-- stays DDL-only here. Safe: this file runs unconditionally on every
-- bootstrap, but only ever INSERTed new rows via WHERE NOT EXISTS — removing
-- the INSERT does not delete rows that already exist in a migrated database.

-- Migration 013 (V70, F3 North Star panel, Batch B6):
-- see migrations/013_north_star.sql for full column documentation.
-- cash_flow_tags: additive classification layer over transactions /
--   income_expense_monthly rows. unforced_errors: manual execution-failure
--   log, seeded with the June 2026 RSU Divest quota zero-fill incident.

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
    rule_id VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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
    cost_edit_history TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- unforced_errors seed moved to the seed-pack system (Program OSR WS-3c,
-- 2026-08-17) — see seed_demo_content(), same idempotency-preserves-safety
-- reasoning as the data_fixes removal above.

-- Migration 014 (V71, F6 Insight Library governance, Batch B7):
-- see migrations/014_insight_governance.sql for full column documentation.
-- The ai_insights.validated_cases / validated_case_links / rule_layer ADD
-- COLUMN statements are NOT mirrored here — ai_insights itself is created by
-- migration 008 (V4), not schema.sql, so those ALTERs only exist in the
-- migration file (run_migrations() always applies 008 before 014 in the same
-- bootstrap_database() call, so ordering is guaranteed). rule_citations is a
-- brand-new table, so it IS mirrored here for fresh-DB init parity with the
-- 010-013 precedent.

CREATE SEQUENCE IF NOT EXISTS seq_rule_citations_id START 1;
CREATE TABLE IF NOT EXISTS rule_citations (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_rule_citations_id'),
    insight_id INTEGER NOT NULL,
    memo_id VARCHAR(50),
    cited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    quarter VARCHAR(7),
    note VARCHAR(300)
);

-- Migration 015 (V72, Fix 2, 2026-07-10 fix-request memo registry + linkage):
-- see migrations/015_memo_registry.sql for full column documentation.
-- All three tables are new; seeds are idempotent (INSERT ... SELECT ... WHERE
-- NOT EXISTS), mirrored here for fresh-DB init parity with the 010-014 precedent.

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

-- memo_registry / memo_asset_map seeds moved to the seed-pack system
-- (Program OSR WS-3c, 2026-08-17) — see seed_demo_content(), same
-- idempotency-preserves-safety reasoning as the data_fixes removal above.
