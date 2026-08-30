"""Tests for cross-check audit and diagnostics endpoints — V5.8.0 Batch C Steps 8, 9.

Uses the same monkeypatch + tmp_path + duckdb pattern as test_ai_advisor_verify.py.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

import duckdb

from src.api.routes import ai_advisor as ai_advisor_routes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(ai_advisor_routes.router)
    return app


def _setup_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Minimal tables needed for cross-check and diagnostics tests."""
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_trade_logs_id START 1;
        CREATE TABLE IF NOT EXISTS trade_logs (
            id              INTEGER PRIMARY KEY DEFAULT nextval('seq_trade_logs_id'),
            log_date        DATE NOT NULL,
            asset_id        VARCHAR(50) NOT NULL,
            action          VARCHAR(20) NOT NULL,
            suggestion_source VARCHAR(50),
            verdict         VARCHAR(50),
            outcome_pct     DECIMAL(10,4),
            verification_result TEXT,
            verification_status VARCHAR(20) DEFAULT 'pending',
            verification_date   DATE,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            verification_block_reason VARCHAR
        )
    """)
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_insights_id START 1;
        CREATE TABLE IF NOT EXISTS insights (
            id           INTEGER PRIMARY KEY DEFAULT nextval('seq_insights_id'),
            insight_date DATE NOT NULL,
            insight_type VARCHAR(50),
            category     VARCHAR(100),
            content      TEXT NOT NULL,
            adopted      BOOLEAN,
            ai_model     VARCHAR(50),
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            title        VARCHAR(200)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_reports (
            id             INTEGER PRIMARY KEY,
            report_type    VARCHAR(50),
            title          VARCHAR(200),
            context_config_json TEXT,
            content_json   TEXT,
            content_markdown TEXT,
            model_used     VARCHAR(100),
            period_start   DATE,
            period_end     DATE,
            prompt_text    TEXT,
            raw_response_text TEXT,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_verdict_audit_id START 1;
        CREATE TABLE IF NOT EXISTS verdict_audit (
            id                       INTEGER PRIMARY KEY DEFAULT nextval('seq_verdict_audit_id'),
            trade_id                 INTEGER NOT NULL,
            suggested_from_threshold VARCHAR,
            keyword_derived          VARCHAR,
            final_verdict            VARCHAR,
            mismatch                 BOOLEAN,
            created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


@pytest.fixture
def cross_check_client(tmp_path, monkeypatch):
    """Test client with isolated DuckDB and _DB_PATH monkeypatched."""
    db_path = tmp_path / "cross_check_test.duckdb"
    conn = duckdb.connect(str(db_path))
    _setup_schema(conn)
    conn.close()

    monkeypatch.setattr(ai_advisor_routes, "_DB_PATH", Path(db_path))
    app = _make_app()
    return TestClient(app), str(db_path)


# ---------------------------------------------------------------------------
# POST /ai-advisor/review/cross-check
# ---------------------------------------------------------------------------

class TestCrossCheckEndpoint:

    def test_cross_check_endpoint_returns_audit(self, cross_check_client):
        """Happy path: mocked generate_cross_check_audit returns structured payload."""
        client, db_path = cross_check_client
        conn = duckdb.connect(db_path)
        conn.execute(
            """INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model, title)
               VALUES ('2026-03-10', 'recommendation', 'strategy', 'Buy SPY', TRUE, 'gemini', 'Buy SPY')"""
        )
        conn.close()

        canned_markdown = "# Cross-Check Audit\n\nSome analysis here."

        with patch("src.api.routes.ai_advisor.generate_cross_check_audit") as mock_gen:
            mock_gen.return_value = {
                "audit_markdown": canned_markdown,
                "summary": {"total_insights": 1},
                "model_used": "gemini/gemini-2.5-flash",
                "generated_at": "2026-03-30T10:00:00",
                "report_id": 42,
            }
            resp = client.post(
                "/ai-advisor/review/cross-check",
                json={"period_start": "2026-03-01", "period_end": "2026-03-30"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["audit_markdown"] == canned_markdown
        assert data["report_id"] == 42
        assert "model_used" in data
        assert "generated_at" in data

    def test_cross_check_422_on_oversized_period(self, cross_check_client):
        """Period > 90 days: generator raises 422, endpoint forwards it."""
        client, _ = cross_check_client
        from fastapi import HTTPException

        with patch("src.api.routes.ai_advisor.generate_cross_check_audit") as mock_gen:
            mock_gen.side_effect = HTTPException(
                status_code=422,
                detail={"error": "period_too_large", "max_days": 90, "current_days": 100},
            )
            resp = client.post(
                "/ai-advisor/review/cross-check",
                json={"period_start": "2026-01-01", "period_end": "2026-04-11"},
            )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "period_too_large"

    def test_cross_check_400_on_invalid_date_format(self, cross_check_client):
        """Invalid date string returns 400."""
        client, _ = cross_check_client
        resp = client.post(
            "/ai-advisor/review/cross-check",
            json={"period_start": "not-a-date", "period_end": "2026-03-30"},
        )
        assert resp.status_code == 400

    def test_cross_check_502_on_llm_failure(self, cross_check_client):
        """LLM failure: generator raises 502, endpoint forwards it."""
        client, _ = cross_check_client
        from fastapi import HTTPException

        with patch("src.api.routes.ai_advisor.generate_cross_check_audit") as mock_gen:
            mock_gen.side_effect = HTTPException(
                status_code=502,
                detail={"detail": "llm_unavailable", "error": "All models failed"},
            )
            resp = client.post(
                "/ai-advisor/review/cross-check",
                json={"period_start": "2026-03-01", "period_end": "2026-03-30"},
            )

        assert resp.status_code == 502
        assert resp.json()["detail"]["detail"] == "llm_unavailable"


# ---------------------------------------------------------------------------
# GET /ai-advisor/diagnostics/verdict-mismatch-rate
# ---------------------------------------------------------------------------

class TestDiagnosticsMismatchRate:

    def test_diagnostics_returns_mismatch_rate(self, cross_check_client):
        """4 audit rows (2 mismatches) → mismatch_rate_pct = 50.0."""
        client, db_path = cross_check_client
        conn = duckdb.connect(db_path)
        conn.execute(
            """INSERT INTO verdict_audit
               (trade_id, suggested_from_threshold, keyword_derived, final_verdict, mismatch, created_at)
               VALUES
               (1, 'good_call', 'good_call', 'good_call', FALSE, '2026-03-15 10:00:00'),
               (2, 'regret', 'good_call', 'good_call', TRUE, '2026-03-16 10:00:00'),
               (3, 'good_call', 'good_call', 'good_call', FALSE, '2026-03-17 10:00:00'),
               (4, 'regret', 'bullet_dodged', 'bullet_dodged', TRUE, '2026-03-18 10:00:00')
            """
        )
        conn.close()

        resp = client.get("/ai-advisor/diagnostics/verdict-mismatch-rate?since=2026-03-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["since"] == "2026-03-01"
        assert data["total_scored"] == 4
        assert data["threshold_keyword_mismatch_count"] == 2
        assert abs(data["mismatch_rate_pct"] - 50.0) < 0.01

    def test_diagnostics_returns_zero_when_no_audits(self, cross_check_client):
        """Returns 0/0/0.0 when no verdict_audit rows exist within window."""
        client, _ = cross_check_client
        resp = client.get("/ai-advisor/diagnostics/verdict-mismatch-rate?since=2026-01-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_scored"] == 0
        assert data["threshold_keyword_mismatch_count"] == 0
        assert data["mismatch_rate_pct"] == 0.0

    def test_diagnostics_missing_since_param_returns_422(self, cross_check_client):
        """`since` is required — omitting it returns 422."""
        client, _ = cross_check_client
        resp = client.get("/ai-advisor/diagnostics/verdict-mismatch-rate")
        assert resp.status_code == 422
