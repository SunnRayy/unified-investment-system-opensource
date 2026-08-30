"""
Unit tests for src/services/ai_advisor/review_generator.py
"""

from __future__ import annotations

import json
from typing import Optional
from unittest.mock import MagicMock, patch

import duckdb
import pytest

from src.services.ai_advisor.review_generator import (
    ReviewGenerator,
    ReviewResult,
    Question,
    _normalize_review_payload,
)
from src.services.ai_advisor.prompts import REVIEW_SECTION_IDS, section_placeholder
from src.services.ai_advisor.section_ids import section_label
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_response(content_json: Optional[dict], success: bool = True) -> MagicMock:
    """Build a minimal mock LLMResponse."""
    resp = MagicMock()
    resp.success = success
    resp.content_json = content_json
    resp.content = json.dumps(content_json) if content_json else ""
    resp.model_used = "gemini/gemini-2.5-flash"
    resp.usage = {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}
    resp.error = None
    return resp


def _full_review_content() -> dict:
    """Return a valid 5-section review content_json."""
    return {
        "trade_summary": {
            "narrative": "本期共有2笔交易。",
            "trades": [
                {"date": "2026-01-10", "asset_id": "US_STK_AAPL", "action": "买入"},
            ],
            "grade_breakdown": {"A": 1, "B": 1},
        },
        "advice_accuracy": {
            "narrative": "建议执行情况良好。",
            "scorecard": [
                {"asset": "US_STK_AAPL", "result": "符合预期"},
            ],
        },
        "portfolio_performance": {
            "narrative": "组合整体上涨3%。",
        },
        "lessons_learned": {
            "narrative": "本期主要经验。",
            "lessons": ["及时止损是关键"],
            "improvements": ["应更早设置止盈位"],
        },
        "rule_updates": {
            "narrative": "建议更新如下准则。",
            "suggestions": ["单票仓位不超过15%"],
        },
    }


def _sample_questions_answers() -> list[dict]:
    return [
        {"question": "本期交易驱动力是什么？", "answer": "主要是估值回归。"},
        {"question": "哪笔交易最符合预期？", "answer": "AAPL买入时机准确。"},
    ]


def _minimal_context_config() -> dict:
    return {
        "tiers": {
            "portfolio": {"enabled": True, "detail": "summary"},
            "market": {"enabled": False, "detail": "summary"},
        }
    }


# ---------------------------------------------------------------------------
# Test 1: generate_questions() calls LLM with trade data in prompt
# ---------------------------------------------------------------------------

@patch("src.services.ai_advisor.review_generator.LLMClient")
@patch("src.services.ai_advisor.review_generator._load_trades_text")
def test_generate_questions_includes_trade_data_in_prompt(mock_load_trades, MockLLMClient):
    """generate_questions() should include trade text in the user prompt sent to LLM."""
    mock_load_trades.return_value = (
        "2026-01-10 | US_STK_AAPL | 买入 | qty=100 | price_cny=1050.0 | grade=A\n"
        "2026-01-15 | CN_FUND_900002 | 卖出 | qty=1000 | price_cny=2.5 | grade=B"
    )

    questions_json = {
        "questions": [
            {"id": 1, "question": "为什么在1月10日买入AAPL？", "context": "AAPL买入"},
            {"id": 2, "question": "基金卖出时机的判断依据？", "context": "900002卖出"},
        ]
    }
    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.return_value = _make_llm_response(questions_json)

    generator = ReviewGenerator()
    questions = generator.generate_questions("2026-01-01", "2026-01-31", db_path="data/unified.duckdb", language="en")

    # LLM must have been called
    mock_llm_instance.complete.assert_called_once()
    call_kwargs = mock_llm_instance.complete.call_args

    # Trade data must appear in the user_prompt
    user_prompt = call_kwargs[1].get("user_prompt") or call_kwargs[0][1]
    assert "US_STK_AAPL" in user_prompt
    assert "CN_FUND_900002" in user_prompt

    # Return value should have 2 questions
    assert len(questions) == 2


# ---------------------------------------------------------------------------
# Test 2: LLM returns valid questions JSON → Question objects
# ---------------------------------------------------------------------------

@patch("src.services.ai_advisor.review_generator.LLMClient")
@patch("src.services.ai_advisor.review_generator._load_trades_text")
def test_generate_questions_parses_question_objects(mock_load_trades, MockLLMClient):
    """Valid LLM questions JSON → proper Question dataclass list."""
    mock_load_trades.return_value = "2026-01-10 | US_STK_AAPL | 买入 | qty=10 | price_cny=1000 | grade=A"

    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.return_value = _make_llm_response(
        {"questions": [{"id": 1, "question": "Q1", "context": "C1"}]}
    )

    generator = ReviewGenerator()
    questions = generator.generate_questions("2026-01-01", "2026-01-31", language="en")

    assert len(questions) == 1
    q = questions[0]
    assert isinstance(q, Question)
    assert q.id == 1
    assert q.question == "Q1"
    assert q.context == "C1"


# ---------------------------------------------------------------------------
# Test 3: LLM raises exception → 3 fallback questions returned
# ---------------------------------------------------------------------------

@patch("src.services.ai_advisor.review_generator.LLMClient")
@patch("src.services.ai_advisor.review_generator._load_trades_text")
def test_generate_questions_llm_failure_returns_fallback(mock_load_trades, MockLLMClient):
    """LLM raises RuntimeError → 3 hardcoded fallback questions returned."""
    mock_load_trades.return_value = "2026-01-10 | US_STK_AAPL | 买入 | qty=10 | price_cny=1000 | grade=A"

    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.side_effect = RuntimeError("All LLM models failed")

    generator = ReviewGenerator()
    questions = generator.generate_questions("2026-01-01", "2026-01-31", language="en")

    # Must return exactly the 3 fallback questions
    assert len(questions) == 3
    assert all(isinstance(q, Question) for q in questions)
    # The fallback questions are generic, non-empty
    assert all(q.question for q in questions)


# ---------------------------------------------------------------------------
# Test 4: generate_review() returns ReviewResult with all 5 section keys
# ---------------------------------------------------------------------------

@patch("src.services.ai_advisor.review_generator._save_to_db", return_value=7)
@patch("src.services.ai_advisor.review_generator.ContextBuilder")
@patch("src.services.ai_advisor.review_generator.LLMClient")
@patch("src.services.ai_advisor.review_generator._load_trades_text")
def test_generate_review_has_all_five_sections(
    mock_load_trades, MockLLMClient, MockContextBuilder, mock_save
):
    """generate_review() with full LLM response → ReviewResult has all 5 REVIEW_SECTION_IDS."""
    mock_load_trades.return_value = None  # no trades

    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.return_value = _make_llm_response(_full_review_content())

    mock_cb = MagicMock()
    MockContextBuilder.return_value = mock_cb
    mock_cb.build_portfolio_context.return_value = "## Portfolio"
    mock_cb.build_review_trade_summary.return_value = None
    # Falsy so the real context-assembly helper skips them (a bare MagicMock is
    # truthy and would be appended, breaking the str join). valuation/technical
    # default to include=True in build_review_context.
    mock_cb.build_technical_context.return_value = ""
    mock_cb.build_valuation_context.return_value = ""

    generator = ReviewGenerator()
    result = generator.generate_review(
        questions_answers=_sample_questions_answers(),
        period_start="2026-01-01",
        period_end="2026-01-31",
        context_config=_minimal_context_config(),
        language="en",
    )

    assert isinstance(result, ReviewResult)
    assert result.report_type == "review"
    assert result.id == 7
    mock_load_trades.assert_not_called()
    mock_cb.build_review_trade_summary.assert_called_once_with("2026-01-01", "2026-01-31")

    for key in REVIEW_SECTION_IDS:
        assert key in result.content_json, f"Missing section: {key}"


# ---------------------------------------------------------------------------
# Test 5: LLM returns review with one missing section → placeholder inserted
# ---------------------------------------------------------------------------

@patch("src.services.ai_advisor.review_generator._save_to_db", return_value=None)
@patch("src.services.ai_advisor.review_generator.ContextBuilder")
@patch("src.services.ai_advisor.review_generator.LLMClient")
@patch("src.services.ai_advisor.review_generator._load_trades_text")
def test_generate_review_missing_section_gets_placeholder(
    mock_load_trades, MockLLMClient, MockContextBuilder, mock_save
):
    """LLM response missing one section → placeholder inserted, all 5 keys present."""
    mock_load_trades.return_value = None

    incomplete = _full_review_content()
    del incomplete["rule_updates"]  # Remove one section

    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.return_value = _make_llm_response(incomplete)

    mock_cb = MagicMock()
    MockContextBuilder.return_value = mock_cb
    mock_cb.build_portfolio_context.return_value = ""
    mock_cb.build_review_trade_summary.return_value = None
    mock_cb.build_technical_context.return_value = ""
    mock_cb.build_valuation_context.return_value = ""

    generator = ReviewGenerator()
    result = generator.generate_review(
        questions_answers=_sample_questions_answers(),
        period_start="2026-01-01",
        period_end="2026-01-31",
        context_config=_minimal_context_config(),
        language="en",
    )

    # Missing section should have placeholder
    assert "rule_updates" in result.content_json
    assert result.content_json["rule_updates"] == section_placeholder("en")

    # All 5 sections must be present
    for key in REVIEW_SECTION_IDS:
        assert key in result.content_json


@patch("src.services.ai_advisor.review_generator._save_to_db", return_value=11)
@patch("src.services.ai_advisor.review_generator.ContextBuilder")
@patch("src.services.ai_advisor.review_generator.LLMClient")
def test_generate_review_uses_reviewed_context_text_when_provided(
    MockLLMClient, MockContextBuilder, mock_save
):
    """Explicit reviewed review-context should be sent to the LLM verbatim."""
    reviewed_context = "## Reviewed Review Context\n\n- Filtered portfolio only"

    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.return_value = _make_llm_response(_full_review_content())

    mock_cb = MagicMock()
    MockContextBuilder.return_value = mock_cb

    generator = ReviewGenerator()
    result = generator.generate_review(
        questions_answers=_sample_questions_answers(),
        period_start="2026-01-01",
        period_end="2026-01-31",
        context_config=_minimal_context_config(),
        reviewed_context_text=reviewed_context,
        language="en",
    )

    assert result.prompt_text == reviewed_context
    assert mock_llm_instance.complete.call_args.kwargs["user_prompt"] == reviewed_context
    mock_cb.build_portfolio_context.assert_not_called()


# ---------------------------------------------------------------------------
# Helpers for extract_insights tests (in-memory DuckDB)
# ---------------------------------------------------------------------------

_AI_INSIGHTS_DDL = """
CREATE SEQUENCE IF NOT EXISTS ai_insights_seq START 1;
CREATE TABLE IF NOT EXISTS ai_insights (
    id              INTEGER PRIMARY KEY DEFAULT nextval('ai_insights_seq'),
    source_report_id INTEGER,
    category        VARCHAR,
    title           VARCHAR NOT NULL,
    body            VARCHAR NOT NULL,
    tags            VARCHAR,
    confidence      DECIMAL(3,2),
    status          VARCHAR DEFAULT 'raw',
    recurrence_count INTEGER DEFAULT 1,
    entity_refs     VARCHAR,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Full schema — both tables with ai_model / confidence_score for bridge tests
_FULL_BRIDGE_DDL = _AI_INSIGHTS_DDL + """
CREATE SEQUENCE IF NOT EXISTS seq_insights_id START 1;
CREATE TABLE IF NOT EXISTS insights (
    id               INTEGER PRIMARY KEY DEFAULT nextval('seq_insights_id'),
    insight_date     DATE NOT NULL,
    insight_type     VARCHAR(50) NOT NULL,
    category         VARCHAR(100),
    title            VARCHAR,
    content          TEXT NOT NULL,
    observation_source VARCHAR(100),
    ai_model         VARCHAR(100),
    confidence_score DECIMAL(3,2),
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def insights_db(tmp_path):
    """Temp DuckDB with ai_insights table for extract_insights tests."""
    path = str(tmp_path / "insights_test.duckdb")
    conn = duckdb.connect(path)
    conn.execute(_AI_INSIGHTS_DDL)
    conn.close()
    return path


# ---------------------------------------------------------------------------
# Test: same report processed twice → recurrence unchanged, no duplicate rows
# ---------------------------------------------------------------------------

def test_extract_insights_idempotent_same_report(insights_db):
    """Processing the same report_id twice must not bump recurrence_count."""
    generator = ReviewGenerator()
    content = {
        "lessons_learned": {
            "lessons": ["止损纪律是控制亏损的关键"],
        },
        "rule_updates": {},
    }

    generator.extract_insights(content, report_id=42, db_path=insights_db)
    generator.extract_insights(content, report_id=42, db_path=insights_db)

    conn = duckdb.connect(insights_db, read_only=True)
    rows = conn.execute(
        "SELECT recurrence_count, status FROM ai_insights ORDER BY id"
    ).fetchall()
    conn.close()

    # Only one active row (the original insert); no second insert or increment.
    active = [r for r in rows if r[1] != "deprecated"]
    assert len(active) == 1
    assert active[0][0] == 1  # recurrence_count stays at 1


# ---------------------------------------------------------------------------
# Test: same title from a second report → recurrence +1 exactly once
# ---------------------------------------------------------------------------

def test_extract_insights_increments_once_for_new_report(insights_db):
    """A second distinct report_id with the same title increments recurrence by 1."""
    generator = ReviewGenerator()
    content = {
        "lessons_learned": {
            "lessons": ["分散持仓降低集中度风险"],
        },
        "rule_updates": {},
    }

    # First report creates the insight
    generator.extract_insights(content, report_id=1, db_path=insights_db)
    # Second distinct report bumps recurrence
    generator.extract_insights(content, report_id=2, db_path=insights_db)
    # Second report re-processed — must NOT bump again
    generator.extract_insights(content, report_id=2, db_path=insights_db)
    # Another re-process of report 1 — must NOT bump
    generator.extract_insights(content, report_id=1, db_path=insights_db)

    conn = duckdb.connect(insights_db, read_only=True)
    row = conn.execute(
        "SELECT recurrence_count FROM ai_insights WHERE status != 'deprecated' LIMIT 1"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 2  # report 1 (initial=1) + report 2 (+1)


# ---------------------------------------------------------------------------
# Test: guard survives deduplicate_all (deprecated duplicates present)
# ---------------------------------------------------------------------------

def test_extract_insights_guard_survives_dedup(insights_db):
    """Guard must still block re-processing when deprecated contribution markers exist."""
    from src.services.ai_advisor.insight_manager import InsightManager

    generator = ReviewGenerator()
    content = {
        "lessons_learned": {
            "lessons": ["仓位管理是投资成功的基础"],
        },
        "rule_updates": {},
    }

    # Three different reports contribute
    generator.extract_insights(content, report_id=10, db_path=insights_db)
    generator.extract_insights(content, report_id=11, db_path=insights_db)
    generator.extract_insights(content, report_id=12, db_path=insights_db)

    # Run deduplicate_all — consolidates duplicates
    conn = duckdb.connect(insights_db)
    mgr = InsightManager(db_path=insights_db)
    mgr.deduplicate_all(conn)
    conn.close()

    # After dedup, re-process all three report_ids — nothing should change
    generator.extract_insights(content, report_id=10, db_path=insights_db)
    generator.extract_insights(content, report_id=11, db_path=insights_db)
    generator.extract_insights(content, report_id=12, db_path=insights_db)

    conn = duckdb.connect(insights_db, read_only=True)
    row = conn.execute(
        "SELECT recurrence_count FROM ai_insights WHERE status != 'deprecated' LIMIT 1"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 3  # 1 (first) + 1 (report 11) + 1 (report 12)


# ---------------------------------------------------------------------------
# Test: long text → title is 80-char prefix, body = full text
# ---------------------------------------------------------------------------

def test_extract_insights_title_body_long_text(insights_db):
    """Text longer than 80 chars: title = first 80 chars, body = full text."""
    long_text = "A" * 81 + " trailing content to push beyond 80 characters for sure"
    generator = ReviewGenerator()
    content = {
        "lessons_learned": {"lessons": [long_text]},
        "rule_updates": {},
    }
    generator.extract_insights(content, report_id=99, db_path=insights_db)

    conn = duckdb.connect(insights_db, read_only=True)
    row = conn.execute(
        "SELECT title, body FROM ai_insights WHERE status != 'deprecated' LIMIT 1"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == long_text[:80]
    assert row[1] == long_text


# ---------------------------------------------------------------------------
# Test: short text → body == '', title = full text
# ---------------------------------------------------------------------------

def test_extract_insights_title_body_short_text(insights_db):
    """Text 80 chars or fewer: title = full text, body = '' (not duplicated)."""
    short_text = "Short insight under 80 chars"
    generator = ReviewGenerator()
    content = {
        "lessons_learned": {"lessons": [short_text]},
        "rule_updates": {},
    }
    generator.extract_insights(content, report_id=88, db_path=insights_db)

    conn = duckdb.connect(insights_db, read_only=True)
    row = conn.execute(
        "SELECT title, body FROM ai_insights WHERE status != 'deprecated' LIMIT 1"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == short_text  # title = full text
    assert row[1] == ""          # body is empty, not a duplicate


# ---------------------------------------------------------------------------
# Fixture: both ai_insights + insights tables with full schema for bridge tests
# ---------------------------------------------------------------------------


@pytest.fixture
def full_bridge_db(tmp_path):
    """Temp DuckDB with ai_insights + insights (including ai_model/confidence_score)."""
    path = str(tmp_path / "bridge_test.duckdb")
    conn = duckdb.connect(path)
    conn.execute(_FULL_BRIDGE_DDL)
    conn.close()
    return path


# ---------------------------------------------------------------------------
# Test: status auto-upgrade raw → recurring in extract_insights
# ---------------------------------------------------------------------------


def test_extract_insights_status_auto_upgrade_raw_to_recurring(full_bridge_db):
    """Second distinct report with same title must upgrade status 'raw' → 'recurring'."""
    generator = ReviewGenerator()
    content = {
        "lessons_learned": {"lessons": ["持仓集中度管理是风控的核心"]},
        "rule_updates": {},
    }

    # First report — creates 'raw' row with recurrence_count=1
    generator.extract_insights(content, report_id=100, db_path=full_bridge_db)
    # Second distinct report — increments to 2 and upgrades to 'recurring'
    generator.extract_insights(content, report_id=101, db_path=full_bridge_db)

    conn = duckdb.connect(full_bridge_db, read_only=True)
    row = conn.execute(
        "SELECT recurrence_count, status FROM ai_insights WHERE status != 'deprecated' ORDER BY id LIMIT 1"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 2
    assert row[1] == "recurring"


# ---------------------------------------------------------------------------
# Test: bridge failure inside extract_insights is non-fatal
# ---------------------------------------------------------------------------


def test_extract_insights_bridge_failure_does_not_raise(insights_db):
    """Bridge failure (insights table missing) must not propagate out of extract_insights."""
    generator = ReviewGenerator()
    content = {
        "lessons_learned": {"lessons": ["止损是核心纪律"]},
        "rule_updates": {},
    }

    # insights_db has only ai_insights — no insights table → bridge will fail internally
    # extract_insights must still complete without raising
    generator.extract_insights(content, report_id=200, db_path=insights_db)

    conn = duckdb.connect(insights_db, read_only=True)
    count = conn.execute(
        "SELECT COUNT(*) FROM ai_insights WHERE status != 'deprecated'"
    ).fetchone()[0]
    conn.close()

    assert count == 1  # ai_insights row was still written despite bridge failure


def test_extract_insights_same_title_two_categories_same_report(insights_db):
    """Same text in two categories from ONE report = two distinct insights —
    the (title, category, report) guard must not suppress the second."""
    generator = ReviewGenerator()
    content = {
        "lessons_learned": {"lessons": ["单票仓位不超过15%"]},
        "rule_updates": {"suggestions": ["单票仓位不超过15%"]},
    }

    generator.extract_insights(content, report_id=42, db_path=insights_db)

    conn = duckdb.connect(insights_db, read_only=True)
    cats = sorted(
        r[0] for r in conn.execute(
            "SELECT category FROM ai_insights WHERE status != 'deprecated'"
        ).fetchall()
    )
    conn.close()
    assert cats == ["process", "strategy"]


# ---------------------------------------------------------------------------
# T2: _normalize_review_payload — schema drift normalizer
# ---------------------------------------------------------------------------


def test_normalize_review_payload_deepseek_nested_shape():
    """Deepseek nested grade_breakdown → flat grades lifted; total_trades + notes as siblings."""
    d = {
        "trade_summary": {
            "narrative": "本期12笔交易。",
            "trades": [],
            "grade_breakdown": {
                "total_trades": 12,
                "grades": {"N/A": 12},
                "notes": "所有交易均为N/A等级，未能形成有效评价。",
            },
        }
    }
    result = _normalize_review_payload(d)
    section = result["trade_summary"]

    # grade_breakdown is now the flat count map lifted from inner grades
    assert section["grade_breakdown"] == {"N/A": 12}

    # total_trades is a sibling int key
    assert section["total_trades"] == 12
    assert isinstance(section["total_trades"], int)

    # notes is a sibling str key
    assert section["notes"] == "所有交易均为N/A等级，未能形成有效评价。"

    # narrative + trades unchanged
    assert section["narrative"] == "本期12笔交易。"
    assert section["trades"] == []


def test_normalize_review_payload_gemini_flat_shape():
    """Gemini flat grade_breakdown (already a count map) passes through unchanged."""
    d = {
        "trade_summary": {
            "narrative": "本期交易汇总。",
            "grade_breakdown": {"A": 2, "B": 2, "C": 2},
        }
    }
    original = json.loads(json.dumps(d))  # deep copy baseline
    result = _normalize_review_payload(d)

    assert result["trade_summary"]["grade_breakdown"] == {"A": 2, "B": 2, "C": 2}
    assert "total_trades" not in result["trade_summary"]
    assert "notes" not in result["trade_summary"]
    assert result == original


def test_normalize_review_payload_no_grade_breakdown():
    """Section without grade_breakdown passes through unchanged."""
    d = {"trade_summary": {"narrative": "无评级分布字段。"}}
    result = _normalize_review_payload(d)
    assert result == d


def test_normalize_review_payload_missing_section():
    """Missing 交易汇总 section passes through unchanged."""
    d = {"advice_accuracy": {"scorecard": []}}
    result = _normalize_review_payload(d)
    assert result == d


def test_normalize_review_payload_idempotent():
    """Calling twice gives same result as calling once."""
    d = {
        "trade_summary": {
            "grade_breakdown": {
                "total_trades": 5,
                "grades": {"A": 3, "B": 2},
                "notes": "良好执行。",
            }
        }
    }
    once = _normalize_review_payload(d)
    twice = _normalize_review_payload(once)

    # After first call grade_breakdown is flat → second call is a no-op
    assert once["trade_summary"]["grade_breakdown"] == twice["trade_summary"]["grade_breakdown"]
    assert once["trade_summary"].get("total_trades") == twice["trade_summary"].get("total_trades")
    assert once["trade_summary"].get("notes") == twice["trade_summary"].get("notes")


# ---------------------------------------------------------------------------
# F3.5 (PRD 2026-07-07, Batch B6): North Star panel prepended to the review
# ---------------------------------------------------------------------------

@pytest.fixture
def full_schema_db(tmp_path):
    """Temp DuckDB with the full schema.sql (has transactions/holdings/
    cash_flow_tags/unforced_errors) so north_star_panel() can run for real."""
    path = str(tmp_path / "north_star_review_test.duckdb")
    conn = DatabaseConnector(path)
    initialize_schema(conn)
    conn.close()
    return path


@patch("src.services.ai_advisor.review_generator._save_to_db", return_value=42)
@patch("src.services.ai_advisor.review_generator.ContextBuilder")
@patch("src.services.ai_advisor.review_generator.LLMClient")
@patch("src.services.ai_advisor.review_generator._load_trades_text")
def test_generate_review_attaches_north_star_panel(
    mock_load_trades, MockLLMClient, MockContextBuilder, mock_save, full_schema_db
):
    """F3.5: content_json gains a 'north_star' key and content_markdown leads
    with the North Star block, ahead of the LLM-authored sections."""
    mock_load_trades.return_value = None

    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.return_value = _make_llm_response(_full_review_content())

    mock_cb = MagicMock()
    MockContextBuilder.return_value = mock_cb
    mock_cb.build_portfolio_context.return_value = "## Portfolio"
    mock_cb.build_review_trade_summary.return_value = None
    mock_cb.build_technical_context.return_value = ""
    mock_cb.build_valuation_context.return_value = ""

    generator = ReviewGenerator()
    result = generator.generate_review(
        questions_answers=_sample_questions_answers(),
        period_start="2026-01-01",
        period_end="2026-01-31",
        context_config=_minimal_context_config(),
        db_path=full_schema_db,
        language="en",
    )

    assert "north_star" in result.content_json
    assert set(result.content_json["north_star"].keys()) == {
        "contributions", "time_in_market", "unforced_errors", "glide_path",
    }
    assert result.content_markdown.startswith("## North Star")
    # LLM sections still follow the North Star block.
    assert f"## {section_label('trade_summary', 'en')}" in result.content_markdown


@patch("src.services.ai_advisor.review_generator._save_to_db", return_value=43)
@patch("src.services.ai_advisor.review_generator.ContextBuilder")
@patch("src.services.ai_advisor.review_generator.LLMClient")
@patch("src.services.ai_advisor.review_generator._load_trades_text")
def test_generate_review_north_star_attachment_failure_is_non_fatal(
    mock_load_trades, MockLLMClient, MockContextBuilder, mock_save, tmp_path
):
    """A missing/unreadable db_path must not break review generation — the
    North Star attachment failure is caught and logged, not raised."""
    mock_load_trades.return_value = None

    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.return_value = _make_llm_response(_full_review_content())

    mock_cb = MagicMock()
    MockContextBuilder.return_value = mock_cb
    mock_cb.build_portfolio_context.return_value = "## Portfolio"
    mock_cb.build_review_trade_summary.return_value = None
    mock_cb.build_technical_context.return_value = ""
    mock_cb.build_valuation_context.return_value = ""

    generator = ReviewGenerator()
    result = generator.generate_review(
        questions_answers=_sample_questions_answers(),
        period_start="2026-01-01",
        period_end="2026-01-31",
        context_config=_minimal_context_config(),
        db_path=str(tmp_path / "does_not_exist.duckdb"),
        language="en",
    )

    assert "north_star" not in result.content_json
    for key in REVIEW_SECTION_IDS:
        assert key in result.content_json
