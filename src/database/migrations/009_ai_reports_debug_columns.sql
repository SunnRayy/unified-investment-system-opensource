-- Migration 009: Add debug columns to ai_reports for LLM transparency
ALTER TABLE ai_reports ADD COLUMN IF NOT EXISTS prompt_text VARCHAR;
ALTER TABLE ai_reports ADD COLUMN IF NOT EXISTS raw_response_text VARCHAR;
