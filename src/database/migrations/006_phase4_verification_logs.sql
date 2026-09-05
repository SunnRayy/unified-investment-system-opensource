-- Phase 4: Monthly Verification KPI columns for verification_logs
-- STATUS: RETIRED — this file is NEVER applied by run_migrations().
--   verification_logs table no longer exists in the production schema.
--   Folded into the versioned migration system (Pass F Batch 3, 2026-06-04).
--   Do not re-apply or reference this file.
-- Adds monthly verification reports with adoption rate, drift, alpha metrics

ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS verification_type VARCHAR(50);
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS period_start DATE;
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS period_end DATE;
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS ai_hit_rate DECIMAL(5,2);
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS ai_hit_rate_by_model JSON;
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS adoption_rate DECIMAL(5,2);
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS portfolio_return DECIMAL(10,4);
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS benchmark_return DECIMAL(10,4);
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS alpha DECIMAL(10,4);
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS max_allocation_drift DECIMAL(5,2);
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS drift_details JSON;
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS total_insights INTEGER;
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS key_lessons JSON;
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS generated_by VARCHAR(50);
ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS report_path VARCHAR(500);
