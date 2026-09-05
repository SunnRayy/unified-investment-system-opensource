"""Tests for B2: LLM memo-update proposal (propose_memo_updates)."""
import json
from unittest.mock import MagicMock, patch

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path, name="memo_proposal.duckdb"):
    db_path = tmp_path / name
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    conn.run_migrations()
    return conn


def _seed_memo(conn, memo_id: int = 1, content: str = "投资策略备忘：坚持价值投资，避免追涨杀跌。") -> int:
    conn.execute(
        """INSERT INTO strategy_memos (id, memo_date, title, strategic_bias, key_directives, content)
           VALUES (?, '2026-01-01', '2026 Strategy', 'defensive', '[]', ?)""",
        [memo_id, content],
    )
    return memo_id


def _seed_audit_report(conn, content_markdown: str = "## Top 3 lessons\n1. Avoid panic sells.") -> int:
    conn.execute(
        """INSERT INTO ai_reports (report_type, title, context_config_json, content_json,
                                   content_markdown, model_used)
           VALUES ('cross_check_audit', 'Audit', '{}', '{}', ?, 'gemini-2.5-flash')""",
        [content_markdown],
    )
    row = conn.execute("SELECT id FROM ai_reports ORDER BY created_at DESC LIMIT 1").fetchone()
    return row[0]


def _make_llm_response(diffs: list) -> MagicMock:
    """Build a mock LLMResponse with a JSON array of proposal diffs."""
    resp = MagicMock()
    resp.content = json.dumps(diffs)
    resp.model_used = "gemini-2.5-flash"
    return resp


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def test_memo_update_proposal_prompt_contains_anti_speculation_rules():
    """MEMO_UPDATE_PROPOSAL_PROMPT must forbid speculation and require grounding in lessons."""
    from src.services.ai_advisor.prompts import MEMO_UPDATE_PROPOSAL_PROMPT

    prompt_lower = MEMO_UPDATE_PROPOSAL_PROMPT.lower()
    assert "lesson" in prompt_lower or "audit" in prompt_lower, \
        "Prompt must reference the audit/lessons as the grounding source"
    assert "do not" in prompt_lower or "only" in prompt_lower or "must" in prompt_lower, \
        "Prompt must include explicit constraints"


def test_memo_update_proposal_prompt_specifies_json_output():
    """MEMO_UPDATE_PROPOSAL_PROMPT must request JSON array output with defined fields."""
    from src.services.ai_advisor.prompts import MEMO_UPDATE_PROPOSAL_PROMPT

    assert "json" in MEMO_UPDATE_PROPOSAL_PROMPT.lower(), "Prompt must ask for JSON output"
    # Check for the expected field names
    for field in ["proposed_text", "rationale"]:
        assert field in MEMO_UPDATE_PROPOSAL_PROMPT, f"Prompt must reference field '{field}'"


# ---------------------------------------------------------------------------
# propose_memo_updates() — happy path
# ---------------------------------------------------------------------------

@patch("src.services.ai_advisor.review_generator.LLMClient")
def test_propose_memo_updates_returns_diff_list(MockLLM, tmp_path):
    """propose_memo_updates returns a list of diffs without mutating strategy_memos."""
    from src.services.ai_advisor.review_generator import propose_memo_updates

    conn = _make_db(tmp_path, "proposal_happy.duckdb")
    _seed_memo(conn, 1)
    _seed_audit_report(conn)

    diffs = [
        {"section": "philosophy", "current_text": "追涨杀跌", "proposed_text": "避免情绪化操作", "rationale": "Lesson 1"}
    ]
    mock_llm = MagicMock()
    MockLLM.return_value = mock_llm
    mock_llm.complete.return_value = _make_llm_response(diffs)

    result = propose_memo_updates(conn, memo_id=1)

    assert "proposals" in result
    assert len(result["proposals"]) == 1
    assert result["proposals"][0]["proposed_text"] == "避免情绪化操作"
    assert "report_id" in result
    assert result["report_id"] is not None


@patch("src.services.ai_advisor.review_generator.LLMClient")
def test_propose_memo_updates_persists_to_ai_reports(MockLLM, tmp_path):
    """propose_memo_updates must persist to ai_reports with report_type='memo_update_proposal'."""
    from src.services.ai_advisor.review_generator import propose_memo_updates

    conn = _make_db(tmp_path, "proposal_persist.duckdb")
    _seed_memo(conn, 1)
    _seed_audit_report(conn)

    mock_llm = MagicMock()
    MockLLM.return_value = mock_llm
    mock_llm.complete.return_value = _make_llm_response([])

    result = propose_memo_updates(conn, memo_id=1)

    row = conn.execute(
        "SELECT report_type FROM ai_reports WHERE id = ?", [result["report_id"]]
    ).fetchone()
    assert row is not None, "Report should be persisted"
    assert row[0] == "memo_update_proposal"


@patch("src.services.ai_advisor.review_generator.LLMClient")
def test_propose_memo_updates_does_not_mutate_strategy_memos(MockLLM, tmp_path):
    """propose_memo_updates must NOT write to strategy_memos — user accepts manually."""
    from src.services.ai_advisor.review_generator import propose_memo_updates

    original_content = "原始策略内容"
    conn = _make_db(tmp_path, "proposal_nomutate.duckdb")
    _seed_memo(conn, 1, content=original_content)
    _seed_audit_report(conn)

    mock_llm = MagicMock()
    MockLLM.return_value = mock_llm
    mock_llm.complete.return_value = _make_llm_response(
        [{"section": "x", "current_text": "原始策略内容", "proposed_text": "NEW", "rationale": "r"}]
    )

    propose_memo_updates(conn, memo_id=1)

    row = conn.execute("SELECT content FROM strategy_memos WHERE id = 1").fetchone()
    assert row[0] == original_content, "strategy_memos must NOT be mutated by propose_memo_updates"


# ---------------------------------------------------------------------------
# propose_memo_updates() — error cases
# ---------------------------------------------------------------------------

def test_propose_memo_updates_returns_error_for_missing_memo(tmp_path):
    """propose_memo_updates should return an error dict (not raise) when memo doesn't exist."""
    from src.services.ai_advisor.review_generator import propose_memo_updates

    conn = _make_db(tmp_path, "proposal_nomemo.duckdb")
    # No memo seeded

    result = propose_memo_updates(conn, memo_id=999)

    assert "error" in result
    assert "not found" in result["error"].lower() or "memo" in result["error"].lower()


@patch("src.services.ai_advisor.review_generator.LLMClient")
def test_propose_memo_updates_uses_latest_audit_when_report_id_omitted(MockLLM, tmp_path):
    """When audit_report_id is omitted, the most recent cross_check_audit is used."""
    from src.services.ai_advisor.review_generator import propose_memo_updates

    conn = _make_db(tmp_path, "proposal_latest.duckdb")
    _seed_memo(conn, 1)
    _seed_audit_report(conn, "first audit")
    _seed_audit_report(conn, "second audit — latest")

    mock_llm = MagicMock()
    MockLLM.return_value = mock_llm
    mock_llm.complete.return_value = _make_llm_response([])

    propose_memo_updates(conn, memo_id=1)

    # The prompt passed to the LLM should contain the latest audit's content
    call_kwargs = mock_llm.complete.call_args.kwargs
    prompt_text = call_kwargs.get("user_prompt", "")
    assert "second audit" in prompt_text or "latest" in prompt_text, \
        f"Prompt should use the most recent audit; got: {prompt_text[:200]!r}"
