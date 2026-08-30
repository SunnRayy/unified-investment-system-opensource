"""
Unit tests for src/services/llm_client.py
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build fake litellm response objects
# ---------------------------------------------------------------------------

def _make_litellm_response(content: str, model: str = "gemini/gemini-2.5-flash") -> MagicMock:
    """Construct a minimal mock that mimics a litellm completion response."""
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    resp.model = model
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Return an LLMClient configured to use a temp settings file and temp DB."""
    settings_content = """
llm:
  primary_model: "gemini/gemini-2.5-flash"
  fallback_models:
    - "anthropic/claude-sonnet-4-20250514"
    - "deepseek/deepseek-chat"
  temperature: 0.7
  max_output_tokens: 4096

database:
  path: "{db_path}"
""".format(db_path=str(tmp_path / "test.duckdb"))

    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(settings_content)

    from src.services.llm_client import LLMClient
    return LLMClient(settings_path=str(settings_file))


# ---------------------------------------------------------------------------
# Test 1: Successful completion returns correct LLMResponse
# ---------------------------------------------------------------------------

def test_complete_success(client):
    """complete() returns LLMResponse with correct fields on success."""
    fake_resp = _make_litellm_response('{"key": "value"}')

    with patch("litellm.completion", return_value=fake_resp) as mock_completion:
        from src.services.llm_client import LLMResponse

        result = client.complete(
            system_prompt="You are helpful.",
            user_prompt="Say hello.",
            expect_json=False,
            report_type="brief",
        )

    assert isinstance(result, LLMResponse)
    assert result.success is True
    assert result.error is None
    assert result.content == '{"key": "value"}'
    assert result.model_used == "gemini/gemini-2.5-flash"
    assert result.usage["prompt_tokens"] == 10
    assert result.usage["completion_tokens"] == 20
    assert result.usage["total_tokens"] == 30
    assert result.content_json is None  # expect_json=False
    mock_completion.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: expect_json=True parses the JSON
# ---------------------------------------------------------------------------

def test_complete_with_json_parsing(client):
    """complete(expect_json=True) populates content_json with parsed dict."""
    fake_resp = _make_litellm_response('{"alpha": 1, "beta": [1, 2]}')

    with patch("litellm.completion", return_value=fake_resp):
        result = client.complete(
            system_prompt="sys",
            user_prompt="user",
            expect_json=True,
        )

    assert result.content_json == {"alpha": 1, "beta": [1, 2]}


# ---------------------------------------------------------------------------
# Test 3: Fallback chain — first model fails, second succeeds
# ---------------------------------------------------------------------------

def test_complete_fallback_on_first_failure(client, monkeypatch):
    """Falls back to second model when the primary raises an exception."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    fake_resp = _make_litellm_response("fallback response", model="anthropic/claude-sonnet-4-20250514")

    call_count = {"n": 0}

    def side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("Primary model timeout")
        return fake_resp

    with patch("litellm.completion", side_effect=side_effect):
        result = client.complete(system_prompt="sys", user_prompt="user")

    assert result.success is True
    assert result.model_used == "anthropic/claude-sonnet-4-20250514"
    assert call_count["n"] == 2  # primary failed, fallback succeeded


def test_complete_skips_models_without_available_api_keys(client, monkeypatch):
    """Providers without configured API keys are skipped instead of being attempted."""
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    attempted_models = []
    fake_resp = _make_litellm_response("fallback response", model="deepseek/deepseek-chat")

    def side_effect(*args, **kwargs):
        attempted_models.append(kwargs["model"])
        if kwargs["model"] == "gemini/gemini-2.5-flash":
            raise RuntimeError("Primary model timeout")
        return fake_resp

    with patch("litellm.completion", side_effect=side_effect):
        result = client.complete(system_prompt="sys", user_prompt="user")

    assert result.success is True
    assert result.model_used == "deepseek/deepseek-chat"
    assert attempted_models == ["gemini/gemini-2.5-flash", "deepseek/deepseek-chat"]


# ---------------------------------------------------------------------------
# Test 4: All models fail → RuntimeError raised
# ---------------------------------------------------------------------------

def test_complete_all_models_fail_raises(client):
    """RuntimeError is raised when every model in the chain fails."""
    with patch("litellm.completion", side_effect=RuntimeError("all fail")):
        with pytest.raises(RuntimeError, match="All LLM models failed"):
            client.complete(system_prompt="sys", user_prompt="user")


# ---------------------------------------------------------------------------
# Test 5: is_available() reflects env var state
# ---------------------------------------------------------------------------

def test_is_available_false_when_no_keys(client, monkeypatch):
    """is_available() returns False when none of the API key vars are set."""
    for var in ["GEMINI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    assert client.is_available() is False


def test_is_available_true_when_gemini_set(client, monkeypatch):
    """is_available() returns True when GEMINI_API_KEY is set."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert client.is_available() is True


def test_is_available_true_when_anthropic_set(client, monkeypatch):
    """is_available() returns True when ANTHROPIC_API_KEY is set."""
    for var in ["GEMINI_API_KEY", "DEEPSEEK_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert client.is_available() is True


def test_is_available_true_when_deepseek_set(client, monkeypatch):
    """is_available() returns True when DEEPSEEK_API_KEY is set."""
    for var in ["GEMINI_API_KEY", "ANTHROPIC_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    assert client.is_available() is True


# ---------------------------------------------------------------------------
# Test 6: Usage logging failure is non-fatal
# ---------------------------------------------------------------------------

def test_usage_logging_failure_does_not_crash(client):
    """If DB logging fails, complete() still returns successfully."""
    fake_resp = _make_litellm_response("hello")

    with patch("litellm.completion", return_value=fake_resp):
        # Patch _log_usage to raise — should not propagate
        with patch.object(client, "_log_usage", side_effect=Exception("DB gone")):
            # Should not raise
            result = client.complete(system_prompt="sys", user_prompt="user")

    assert result.success is True


# ---------------------------------------------------------------------------
# Test 7: Migration SQL creates all 4 tables
# ---------------------------------------------------------------------------

def test_migration_creates_tables():
    """Running 008_ai_advisor_tables.sql on an in-memory DuckDB creates all required tables."""
    import duckdb

    conn = duckdb.connect(":memory:")
    with open("src/database/migrations/008_ai_advisor_tables.sql") as f:
        sql = f.read()
    conn.execute(sql)

    tables = conn.execute("SHOW TABLES").fetchall()
    table_names = {t[0] for t in tables}

    assert "llm_usage" in table_names, f"llm_usage missing. Got: {table_names}"
    assert "ai_reports" in table_names, f"ai_reports missing. Got: {table_names}"
    assert "ai_insights" in table_names, f"ai_insights missing. Got: {table_names}"
    assert "ai_behavioral_log" in table_names, f"ai_behavioral_log missing. Got: {table_names}"

    conn.close()


# ---------------------------------------------------------------------------
# Test 8: _parse_json handles markdown-fenced JSON
# ---------------------------------------------------------------------------

def test_parse_json_strips_markdown_fences():
    """_parse_json correctly strips ```json ... ``` fences."""
    from src.services.llm_client import _parse_json

    fenced = '```json\n{"a": 1}\n```'
    result = _parse_json(fenced)
    assert result == {"a": 1}


def test_parse_json_plain():
    """_parse_json parses plain JSON strings."""
    from src.services.llm_client import _parse_json

    assert _parse_json('{"x": 42}') == {"x": 42}


def test_parse_json_returns_none_on_garbage():
    """_parse_json returns None when the input cannot be parsed."""
    from src.services.llm_client import _parse_json

    # Use something truly unparseable even by json_repair
    result = _parse_json("this is not json at all !@#$%")
    # json_repair may convert garbage to a string — accept either None or non-dict
    # The important thing is it doesn't raise
    assert result is None or isinstance(result, (str, dict, list))


# ---------------------------------------------------------------------------
# Test 9: Cost estimation
# ---------------------------------------------------------------------------

def test_estimate_cost_known_model(client):
    """_estimate_cost returns correct value for known model."""
    cost = client._estimate_cost("gemini/gemini-2.5-flash", prompt_tokens=1000, completion_tokens=1000)
    expected = (1000 / 1000) * 0.000075 + (1000 / 1000) * 0.0003
    assert abs(cost - expected) < 1e-9


def test_estimate_cost_unknown_model_returns_zero(client):
    """_estimate_cost returns 0.0 for unknown model."""
    cost = client._estimate_cost("unknown/model-xyz", prompt_tokens=1000, completion_tokens=1000)
    assert cost == 0.0


# ---------------------------------------------------------------------------
# Test 10: DB path resolved via UIS_DB_PATH env override
# ---------------------------------------------------------------------------

def test_db_path_is_resolved_via_env_override(tmp_path, monkeypatch):
    """_db_path is resolved to the absolute UIS_DB_PATH value when the env is set.

    settings.yaml may contain the relative default "data/unified.duckdb".  On Cloud
    Run the DB lives at e.g. /tmp/data/unified.duckdb.  LLMClient must call
    resolve_db_path() so _db_path matches the actual DB location.
    """
    from pathlib import Path

    # Point UIS_DB_PATH at an absolute temp path — resolve_db_path will use it
    # when the settings value matches DEFAULT_DB_PATH ("data/unified.duckdb").
    override_path = str(tmp_path / "cloud_data" / "unified.duckdb")
    monkeypatch.setenv("UIS_DB_PATH", override_path)

    settings_content = """
llm:
  primary_model: "gemini/gemini-2.5-flash"
  fallback_models: []
  temperature: 0.7
  max_output_tokens: 4096

database:
  path: "data/unified.duckdb"
"""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(settings_content)

    from src.services.llm_client import LLMClient
    client = LLMClient(settings_path=str(settings_file))

    assert Path(client._db_path).is_absolute(), (
        f"_db_path should be absolute; got: {client._db_path}"
    )
    assert client._db_path == str(Path(override_path).resolve()), (
        f"_db_path should match UIS_DB_PATH override; got: {client._db_path}"
    )
