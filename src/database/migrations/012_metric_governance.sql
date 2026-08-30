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
-- on $UIS_SEED_PROFILE. Safe: this migration only ever runs once per DB
-- (schema_version gate), so removing the INSERT has zero effect on an
-- already-migrated database.
