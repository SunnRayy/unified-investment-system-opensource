"""
Unit tests for src/services/ai_advisor/brief_generator.py
"""

from __future__ import annotations

import json
from typing import Optional
from unittest.mock import MagicMock, patch


from src.services.ai_advisor.brief_generator import BriefGenerator, BriefResult, _build_content_markdown
from src.services.ai_advisor.prompts import BRIEF_SECTION_IDS, section_placeholder
from src.services.ai_advisor.section_ids import adapt_stored_content_json, section_label


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_response(
    content_json: Optional[dict],
    success: bool = True,
    *,
    content: Optional[str] = None,
    usage: Optional[dict] = None,
) -> MagicMock:
    """Build a minimal mock LLMResponse."""
    resp = MagicMock()
    resp.success = success
    resp.content_json = content_json
    resp.content = content if content is not None else (json.dumps(content_json) if content_json else "")
    resp.model_used = "gemini/gemini-2.5-flash"
    resp.usage = usage or {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}
    resp.error = None
    return resp


def _full_content_json() -> dict:
    """Return a valid 5-section content_json keyed by the stable section IDs."""
    return {
        "macro_outlook": {
            "narrative": "全球市场稳定。",
            "key_factors": ["美联储政策", "通胀数据"],
        },
        "holdings_risk": {
            "narrative": "持仓结构合理。",
            "positions": [
                {"name": "US_STK_AAPL", "status": "hold", "comment": "表现稳健"},
            ],
        },
        "risk_alerts": {
            "narrative": "当前风险可控。",
            "items": [
                {"title": "汇率风险", "severity": "medium", "description": "美元波动较大"},
            ],
        },
        "action_items": {
            "narrative": "维持现有仓位。",
            "actions": [
                {"asset": "US_STK_AAPL", "action": "hold", "reasoning": "估值合理"},
            ],
        },
        "watchlist": {
            "narrative": "关注美联储声明。",
            "watchlist": [
                {"item": "美联储声明", "trigger": "利率决议", "level": "N/A"},
            ],
        },
    }


def _legacy_content_json() -> dict:
    """A REAL pre-BIL brief payload: Simplified Chinese keys, Chinese enum values.

    This is the shape sitting in `ai_reports.content_json` for 39 briefs on the
    owner's database. It is a fixture, not history — but it is copied from the
    real stored shape (verified against the live row) so the adapter is proved
    against what actually exists, not against what we wish existed.
    """
    return {
        "宏观形势": {
            "narrative": "全球市场稳定。",
            "key_factors": ["美联储政策", "通胀数据"],
        },
        "持仓分析与风险预警": {
            "narrative": "持仓结构合理。",
            "positions": [
                {"name": "US_STK_AAPL", "status": "持有", "comment": "表现稳健"},
            ],
        },
        "风险预警汇总": {
            "narrative": "当前风险可控。",
            "items": [
                {"title": "汇率风险", "severity": "medium", "description": "美元波动较大"},
            ],
        },
        "操作建议": {
            "narrative": "维持现有仓位。",
            "actions": [
                {"asset": "US_STK_AAPL", "action": "持有", "reasoning": "估值合理"},
            ],
        },
        "明日关注": {
            "narrative": "关注美联储声明。",
            "watchlist": [
                {"item": "美联储声明", "trigger": "利率决议", "level": "N/A"},
            ],
        },
    }


def _minimal_context_config(all_disabled: bool = False) -> dict:
    enabled = not all_disabled
    return {
        "tiers": {
            "identity":     {"enabled": enabled, "detail": "summary"},
            "portfolio":    {"enabled": enabled, "detail": "summary"},
            "market":       {"enabled": enabled, "detail": "summary"},
            "strategy":     {"enabled": False,   "detail": "summary"},
            "transactions": {"enabled": enabled, "detail": "summary", "timeframe": "14d"},
        },
        "include_realtime": False,
    }


# ---------------------------------------------------------------------------
# Test 1: Valid 5-section response → BriefResult has all 5 keys
# ---------------------------------------------------------------------------

@patch("src.services.ai_advisor.brief_generator._save_to_db", return_value=42)
@patch("src.services.ai_advisor.brief_generator.ContextBuilder")
@patch("src.services.ai_advisor.brief_generator.LLMClient")
def test_generate_all_five_sections_present(MockLLMClient, MockContextBuilder, mock_save):
    """LLM returns all 5 sections → BriefResult.content_json has all 5 keys."""
    # Arrange
    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.return_value = _make_llm_response(_full_content_json())

    mock_cb = MagicMock()
    MockContextBuilder.return_value = mock_cb
    mock_cb.build_identity_context.return_value = "## Identity"
    mock_cb.build_portfolio_context.return_value = "## Portfolio"
    mock_cb.build_market_context.return_value = "## Market"
    mock_cb.build_transactions_context.return_value = "## Transactions"

    # Act
    result = BriefGenerator().generate(_minimal_context_config(), language="en")

    # Assert
    assert isinstance(result, BriefResult)
    assert result.report_type == "brief"
    assert result.id == 42
    for key in BRIEF_SECTION_IDS:
        assert key in result.content_json, f"Missing section: {key}"


# ---------------------------------------------------------------------------
# Test 2: LLM returns JSON missing one section → placeholder inserted
# ---------------------------------------------------------------------------

@patch("src.services.ai_advisor.brief_generator._save_to_db", return_value=1)
@patch("src.services.ai_advisor.brief_generator.ContextBuilder")
@patch("src.services.ai_advisor.brief_generator.LLMClient")
def test_missing_section_gets_placeholder(MockLLMClient, MockContextBuilder, mock_save):
    """LLM response missing one key → placeholder inserted, no exception."""
    incomplete = _full_content_json()
    del incomplete["watchlist"]  # Remove one section

    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.return_value = _make_llm_response(incomplete)

    mock_cb = MagicMock()
    MockContextBuilder.return_value = mock_cb
    mock_cb.build_identity_context.return_value = ""
    mock_cb.build_portfolio_context.return_value = ""
    mock_cb.build_market_context.return_value = ""
    mock_cb.build_transactions_context.return_value = ""

    # Should NOT raise
    result = BriefGenerator().generate(_minimal_context_config(), language="en")

    assert "watchlist" in result.content_json
    assert result.content_json["watchlist"] == section_placeholder("en")
    # All other sections still present
    for key in BRIEF_SECTION_IDS:
        assert key in result.content_json


# ---------------------------------------------------------------------------
# Test 3: LLM returns invalid JSON (content_json=None) → BriefResult still returned
# ---------------------------------------------------------------------------

@patch("src.services.ai_advisor.brief_generator._save_to_db", return_value=None)
@patch("src.services.ai_advisor.brief_generator.ContextBuilder")
@patch("src.services.ai_advisor.brief_generator.LLMClient")
def test_invalid_json_handled_gracefully(MockLLMClient, MockContextBuilder, mock_save):
    """LLM returns non-parseable JSON → BriefResult returned with placeholders for all sections."""
    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.return_value = _make_llm_response(None)  # parse failed

    mock_cb = MagicMock()
    MockContextBuilder.return_value = mock_cb
    mock_cb.build_identity_context.return_value = ""
    mock_cb.build_portfolio_context.return_value = ""
    mock_cb.build_market_context.return_value = ""
    mock_cb.build_transactions_context.return_value = ""

    # Should NOT raise
    result = BriefGenerator().generate(_minimal_context_config(), language="en")

    assert isinstance(result, BriefResult)
    assert result.id is None  # DB save returned None since id was None
    for key in BRIEF_SECTION_IDS:
        assert key in result.content_json
        # All sections should be placeholders
        assert result.content_json[key] == section_placeholder("en")


# ---------------------------------------------------------------------------
# Test 4: _build_content_markdown includes all 5 section headings
# ---------------------------------------------------------------------------

def test_build_content_markdown_has_all_headings():
    """Markdown headings are the resolved LABEL for each ID, in the given language."""
    content_json = _full_content_json()

    markdown_en = _build_content_markdown(content_json, "en")
    for key in BRIEF_SECTION_IDS:
        label = section_label(key, "en")
        assert f"## {label}" in markdown_en, f"Heading '## {label}' not found in markdown"
        # The machine ID must never leak into the reader-facing artifact.
        assert f"## {key}" not in markdown_en

    markdown_zh = _build_content_markdown(content_json, "zh-CN")
    for key in BRIEF_SECTION_IDS:
        assert f"## {section_label(key, 'zh-CN')}" in markdown_zh

    # The zh-CN headings are the exact strings production used before WS-5 —
    # a legacy brief's markdown is byte-comparable to a new one's.
    assert "## 宏观形势" in markdown_zh
    assert "## 明日关注" in markdown_zh

    # Should be non-empty
    assert len(markdown_en) > 50


def test_legacy_chinese_keyed_brief_still_renders():
    """A REAL pre-BIL row (Chinese keys, Chinese enum values) must render correctly.

    This is the gate that matters: 39 briefs and 4 reviews on the owner's DB are
    stored in this shape and are never rewritten.
    """
    adapted = adapt_stored_content_json(_legacy_content_json())

    # Every section reachable under its stable ID.
    for key in BRIEF_SECTION_IDS:
        assert key in adapted, f"legacy row lost section {key}"
        assert adapted[key]["narrative"], f"legacy row lost narrative for {key}"

    # No Chinese key survives the adapter.
    assert not any(not k.isascii() for k in adapted)

    # Enum VALUES normalize too — 持有 was styled as a string match.
    assert adapted["holdings_risk"]["positions"][0]["status"] == "hold"
    assert adapted["action_items"]["actions"][0]["action"] == "hold"

    # And it renders: markdown built from the adapted payload keeps the prose.
    markdown = _build_content_markdown(adapted, "zh-CN")
    assert "## 宏观形势" in markdown
    assert "全球市场稳定。" in markdown
    assert "美联储声明" in markdown


def test_adapter_fails_when_a_legacy_mapping_is_broken():
    """Anti-vacuity: prove the legacy gate can go RED.

    A green test that cannot fail is worse than no test. Remove one entry from
    the legacy map and the legacy row must stop resolving.
    """
    import src.services.ai_advisor.section_ids as sid

    original = dict(sid.LEGACY_SECTION_KEYS)
    try:
        sid.LEGACY_SECTION_KEYS.pop("宏观形势")
        broken = adapt_stored_content_json(_legacy_content_json())
        assert "macro_outlook" not in broken, (
            "removing the 宏观形势 mapping did NOT break resolution — the legacy "
            "gate is vacuous and proves nothing"
        )
        assert "宏观形势" in broken  # it survives untranslated, i.e. unrendered
    finally:
        sid.LEGACY_SECTION_KEYS.clear()
        sid.LEGACY_SECTION_KEYS.update(original)

    # …and restoring it makes the gate green again.
    assert "macro_outlook" in adapt_stored_content_json(_legacy_content_json())


# ---------------------------------------------------------------------------
# Test 5: generate() with all tiers disabled still calls LLM
# ---------------------------------------------------------------------------

@patch("src.services.ai_advisor.brief_generator._save_to_db", return_value=1)
@patch("src.services.ai_advisor.brief_generator.ContextBuilder")
@patch("src.services.ai_advisor.brief_generator.LLMClient")
def test_generate_all_tiers_disabled_still_calls_llm(MockLLMClient, MockContextBuilder, mock_save):
    """Even with all tiers disabled, the LLM must still be called."""
    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.return_value = _make_llm_response(_full_content_json())

    mock_cb = MagicMock()
    MockContextBuilder.return_value = mock_cb

    config_all_disabled = _minimal_context_config(all_disabled=True)
    result = BriefGenerator().generate(config_all_disabled, language="en")

    # LLM must have been called exactly once
    mock_llm_instance.complete.assert_called_once()

    # Result should still be a valid BriefResult
    assert isinstance(result, BriefResult)
    assert result.report_type == "brief"
    for key in BRIEF_SECTION_IDS:
        assert key in result.content_json


@patch("src.services.ai_advisor.brief_generator._save_to_db", return_value=9)
@patch("src.services.ai_advisor.brief_generator.ContextBuilder")
@patch("src.services.ai_advisor.brief_generator.LLMClient")
def test_generate_uses_reviewed_context_text_when_provided(MockLLMClient, MockContextBuilder, mock_save):
    """Explicit reviewed context should be sent to the LLM without rebuilding tiers."""
    reviewed_context = "## Reviewed Context\n\n- Equity only"

    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.return_value = _make_llm_response(_full_content_json())

    mock_cb = MagicMock()
    MockContextBuilder.return_value = mock_cb

    result = BriefGenerator().generate(
        _minimal_context_config(),
        reviewed_context_text=reviewed_context,
        language="en",
    )

    assert result.prompt_text == reviewed_context
    mock_llm_instance.complete.assert_called_once()
    assert mock_llm_instance.complete.call_args.kwargs["user_prompt"] == reviewed_context
    mock_cb.build_identity_context.assert_not_called()


@patch("src.services.ai_advisor.brief_generator._save_to_db", return_value=10)
@patch("src.services.ai_advisor.brief_generator.ContextBuilder")
@patch("src.services.ai_advisor.brief_generator.LLMClient")
def test_generate_retries_once_when_brief_json_is_truncated(MockLLMClient, MockContextBuilder, mock_save):
    """If the first brief response is clearly truncated, generator should retry once with a concise repair prompt."""
    truncated = {
        "宏观形势": {
            "narrative": "only first section",
        },
    }
    raw_truncated = '```json\n{"宏观形势":{"narrative":"only first section"'

    mock_llm_instance = MagicMock()
    MockLLMClient.return_value = mock_llm_instance
    mock_llm_instance.complete.side_effect = [
        _make_llm_response(
            truncated,
            content=raw_truncated,
            usage={"prompt_tokens": 100, "completion_tokens": 4092, "total_tokens": 4192},
        ),
        _make_llm_response(_full_content_json()),
    ]

    mock_cb = MagicMock()
    MockContextBuilder.return_value = mock_cb
    mock_cb.build_identity_context.return_value = ""
    mock_cb.build_portfolio_context.return_value = ""
    mock_cb.build_market_context.return_value = ""
    mock_cb.build_transactions_context.return_value = ""

    result = BriefGenerator().generate(_minimal_context_config(), language="en")

    assert mock_llm_instance.complete.call_count == 2
    retry_call = mock_llm_instance.complete.call_args_list[1]
    assert "Regenerate the complete JSON" in retry_call.kwargs["user_prompt"]
    assert "macro_outlook" in retry_call.kwargs["user_prompt"]
    for key in BRIEF_SECTION_IDS:
        assert key in result.content_json
        assert result.content_json[key] != section_placeholder("en")
