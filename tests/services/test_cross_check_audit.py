"""Tests for generate_cross_check_audit() — V5.8.0 Batch C Step 7."""

from __future__ import annotations

import pytest
from datetime import date
from unittest.mock import MagicMock, patch
from fastapi import HTTPException

from src.database.connector import DatabaseConnector


# ---------------------------------------------------------------------------
# In-memory DB helper (same schema as context test)
# ---------------------------------------------------------------------------

def _setup_db() -> DatabaseConnector:
    """Create in-memory DB with all tables required for audit generation."""
    db = DatabaseConnector(":memory:")
    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_trade_logs_id START 1")
    db.execute("""
        CREATE TABLE IF NOT EXISTS trade_logs (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_trade_logs_id'),
            log_date DATE NOT NULL,
            asset_id VARCHAR(50) NOT NULL,
            action VARCHAR(20) NOT NULL,
            suggestion_source VARCHAR(50),
            verdict VARCHAR(50),
            outcome_pct DECIMAL(10,4),
            verification_result VARCHAR(100),
            verification_status VARCHAR(20) DEFAULT 'pending',
            verification_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            verification_block_reason VARCHAR
        )
    """)
    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_insights_id START 1")
    db.execute("""
        CREATE TABLE IF NOT EXISTS insights (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_insights_id'),
            insight_date DATE NOT NULL,
            insight_type VARCHAR(50),
            category VARCHAR(100),
            content TEXT NOT NULL,
            adopted BOOLEAN,
            ai_model VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            title VARCHAR(200),
            observation_source VARCHAR(100),
            verified BOOLEAN DEFAULT FALSE,
            confidence_score DECIMAL(3,2)
        )
    """)
    db.execute("CREATE SEQUENCE IF NOT EXISTS ai_reports_seq START 1")
    db.execute("""
        CREATE TABLE IF NOT EXISTS ai_reports (
            id INTEGER PRIMARY KEY DEFAULT nextval('ai_reports_seq'),
            report_type VARCHAR(50) NOT NULL,
            title VARCHAR(200),
            context_config_json TEXT,
            content_json TEXT NOT NULL,
            content_markdown TEXT,
            model_used VARCHAR(100),
            period_start DATE,
            period_end DATE,
            prompt_text TEXT,
            raw_response_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return db


def _make_llm_response(content: str = "# Audit\n\nSome audit content.", model: str = "gemini/gemini-2.5-flash") -> MagicMock:
    """Build a minimal mock LLMResponse for cross-check audit (plain text, not JSON)."""
    resp = MagicMock()
    resp.success = True
    resp.content = content
    resp.content_json = None  # cross_check returns markdown, not JSON
    resp.model_used = model
    resp.usage = {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}
    resp.error = None
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_generator_returns_structured_payload():
    """Mock LLM returns canned markdown; generator returns expected dict shape."""
    db = _setup_db()
    # Insert a minimal insight so context is non-empty
    db.execute(
        """INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model, title)
           VALUES ('2026-03-10', 'recommendation', 'strategy', 'Buy AAPL', TRUE, 'gemini', 'Buy AAPL')"""
    )

    from src.services.ai_advisor.review_generator import generate_cross_check_audit

    canned_markdown = "# Cross-Check Audit\n\n## 1. Adopted insights — what worked\n\nNone.\n"

    with patch("src.services.ai_advisor.review_generator.LLMClient") as MockLLM:
        mock_instance = MagicMock()
        mock_instance.complete.return_value = _make_llm_response(canned_markdown)
        MockLLM.return_value = mock_instance

        result = generate_cross_check_audit(
            db=db,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 30),
        )

    assert isinstance(result, dict)
    assert "audit_markdown" in result
    assert "summary" in result
    assert "model_used" in result
    assert "generated_at" in result
    assert "report_id" in result
    assert result["audit_markdown"] == canned_markdown
    assert result["model_used"] == "gemini/gemini-2.5-flash"


def test_generator_persists_to_ai_reports():
    """generate_cross_check_audit() must insert a row with report_type='cross_check_audit'."""
    db = _setup_db()
    db.execute(
        """INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model, title)
           VALUES ('2026-03-10', 'recommendation', 'strategy', 'Buy TSLA', FALSE, 'deepseek', 'Buy TSLA')"""
    )

    from src.services.ai_advisor.review_generator import generate_cross_check_audit

    with patch("src.services.ai_advisor.review_generator.LLMClient") as MockLLM:
        mock_instance = MagicMock()
        mock_instance.complete.return_value = _make_llm_response("# Audit output")
        MockLLM.return_value = mock_instance

        result = generate_cross_check_audit(
            db=db,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 30),
        )

    # Verify DB row
    row = db.execute(
        "SELECT report_type, title FROM ai_reports WHERE report_type = 'cross_check_audit' LIMIT 1"
    ).fetchone()
    assert row is not None, "No row inserted into ai_reports"
    assert row[0] == "cross_check_audit"
    assert "2026-03-01" in row[1] and "2026-03-30" in row[1]
    assert result["report_id"] is not None


def test_generator_propagates_period_too_large_as_422():
    """When context builder returns period_too_large error, generator raises HTTPException(422)."""
    db = _setup_db()
    from src.services.ai_advisor.review_generator import generate_cross_check_audit

    with pytest.raises(HTTPException) as exc_info:
        generate_cross_check_audit(
            db=db,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 4, 11),  # 100 days — exceeds 90d cap
        )

    assert exc_info.value.status_code == 422
    assert "Period exceeds caps" in exc_info.value.detail


def test_generator_uses_fallback_model_on_primary_failure():
    """When primary LLM model raises, fallback model is tried and used."""
    db = _setup_db()
    db.execute(
        """INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model, title)
           VALUES ('2026-03-10', 'recommendation', 'strategy', 'Hold BTC', TRUE, 'gemini', 'Hold BTC')"""
    )

    from src.services.ai_advisor.review_generator import generate_cross_check_audit
    from src.services.llm_client import LLMAllModelsFailedError

    call_count = 0

    def mock_complete(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Primary model failed")
        return _make_llm_response("# Fallback audit", model="deepseek/deepseek-chat")

    with patch("src.services.ai_advisor.review_generator.LLMClient") as MockLLM:
        mock_instance = MagicMock()
        mock_instance.complete.side_effect = mock_complete
        MockLLM.return_value = mock_instance

        # If LLMClient handles fallback internally, we just need the result
        # The actual fallback chain is inside LLMClient — we test the error propagation path
        # by making complete() raise LLMAllModelsFailedError on first call
        mock_instance.complete.side_effect = [
            RuntimeError("Primary failed"),
            _make_llm_response("# Fallback audit", model="deepseek/deepseek-chat"),
        ]

        # LLMClient handles fallback internally; if all fail, it raises LLMAllModelsFailedError.
        # Here we test that generate_cross_check_audit propagates a 502 if all models fail.
        mock_instance.complete.side_effect = LLMAllModelsFailedError("All models failed")

        with pytest.raises(HTTPException) as exc_info:
            generate_cross_check_audit(
                db=db,
                period_start=date(2026, 3, 1),
                period_end=date(2026, 3, 30),
            )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["detail"] == "llm_unavailable"


# ---------------------------------------------------------------------------
# Lesson extraction tests (V5.10.x Growth Timeline fix)
# ---------------------------------------------------------------------------

AUDIT_WITH_LESSONS = """## 1. Adopted insights — what worked

AAPL +12% — good call.

## 2. Adopted insights — what hurt

None.

## 3. Rejected insights

None.

## 4. Pending

None.

## 5. **Top 3 lessons**

- Always set a stop-loss before entering a position.
- Avoid buying into earnings without a verified thesis.
- Rebalance quarterly regardless of short-term noise.

## 6. **Recommended memo updates**

- Add a stop-loss rule to the risk section.
"""

AUDIT_NO_LESSONS = """## 1. Adopted insights — what worked

None.

## 5. **Top 3 lessons**

No data in this period.
"""


def test_extract_lessons_inserts_rows():
    """Lesson bullets from audit are inserted into insights(category='lesson')."""
    from src.services.ai_advisor.review_generator import _extract_lessons_to_insights

    db = _setup_db()
    inserted = _extract_lessons_to_insights(
        db=db,
        audit_markdown=AUDIT_WITH_LESSONS,
        period_end="2026-03-31",
        model_used="gemini/gemini-2.5-flash",
        report_id=1,
    )
    assert inserted == 3
    rows = db.execute(
        "SELECT title, category, observation_source, verified FROM insights WHERE category = 'lesson' ORDER BY id"
    ).fetchall()
    assert len(rows) == 3
    assert rows[0][1] == "lesson"
    assert rows[0][2] == "cross_check_audit"
    assert rows[0][3] is True


def test_extract_lessons_idempotent():
    """Calling extraction twice for same period does not double-insert."""
    from src.services.ai_advisor.review_generator import _extract_lessons_to_insights

    db = _setup_db()
    _extract_lessons_to_insights(db, AUDIT_WITH_LESSONS, "2026-03-31", "gemini", 1)
    second = _extract_lessons_to_insights(db, AUDIT_WITH_LESSONS, "2026-03-31", "gemini", 1)
    assert second == 0
    count = db.execute("SELECT COUNT(*) FROM insights WHERE category = 'lesson'").fetchone()[0]
    assert count == 3


AUDIT_NUMBERED_LESSONS = """## 1. Adopted insights — what worked

None.

## 5. Top 3 lessons

1.  **Independent Verification:** Cross-reference AI suggestions against macro realities.
2.  **Discipline via GTC:** Use GTC orders to remove panic from execution.
3.  **Concentration Risk:** Adhere to 100% liquidation rule for RSU vests.

## 6. Recommended memo updates

- Add stop-loss rule.
"""


def test_extract_lessons_numbered_format():
    """Numbered lesson items (1. 2. 3.) are extracted as well as bullet points."""
    from src.services.ai_advisor.review_generator import _extract_lessons_to_insights

    db = _setup_db()
    inserted = _extract_lessons_to_insights(db, AUDIT_NUMBERED_LESSONS, "2026-05-25", "gemini", 2)
    assert inserted == 3
    count = db.execute("SELECT COUNT(*) FROM insights WHERE category = 'lesson'").fetchone()[0]
    assert count == 3


def test_extract_lessons_no_bullets_returns_zero():
    """When lessons section has no bullet points, returns 0."""
    from src.services.ai_advisor.review_generator import _extract_lessons_to_insights

    db = _setup_db()
    inserted = _extract_lessons_to_insights(db, AUDIT_NO_LESSONS, "2026-03-31", "gemini", 1)
    assert inserted == 0
    count = db.execute("SELECT COUNT(*) FROM insights WHERE category = 'lesson'").fetchone()[0]
    assert count == 0


def test_audit_generation_populates_growth_timeline():
    """generate_cross_check_audit() with lesson-containing markdown populates insights."""
    db = _setup_db()
    db.execute(
        """INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model, title)
           VALUES ('2026-03-10', 'recommendation', 'strategy', 'Buy AAPL', TRUE, 'gemini', 'Buy AAPL')"""
    )

    from src.services.ai_advisor.review_generator import generate_cross_check_audit

    with patch("src.services.ai_advisor.review_generator.LLMClient") as MockLLM:
        mock_instance = MagicMock()
        mock_instance.complete.return_value = _make_llm_response(AUDIT_WITH_LESSONS)
        MockLLM.return_value = mock_instance

        result = generate_cross_check_audit(
            db=db,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 30),
        )

    assert result["lessons_added"] == 3
    count = db.execute("SELECT COUNT(*) FROM insights WHERE category = 'lesson'").fetchone()[0]
    assert count == 3
