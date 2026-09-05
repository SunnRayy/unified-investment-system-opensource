"""Regression tests for AI Advisor system prompts."""

import logging

from src.services.ai_advisor.prompts import (
    BRIEF_SYSTEM_PROMPT,
    REVIEW_QUESTIONS_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT,
)


def test_brief_prompt_embeds_strategy_officer_persona_and_context_guardrails():
    assert "私人投资策略官" in BRIEF_SYSTEM_PROMPT
    assert "本杰明·格雷厄姆" in BRIEF_SYSTEM_PROMPT
    assert "霍华德·马克斯" in BRIEF_SYSTEM_PROMPT
    assert "风险控制放在首位" in BRIEF_SYSTEM_PROMPT
    assert "严格基于提供的context" in BRIEF_SYSTEM_PROMPT
    assert "不要假装搜索" in BRIEF_SYSTEM_PROMPT
    assert "战略行动计划" in BRIEF_SYSTEM_PROMPT
    assert "再平衡需求" in BRIEF_SYSTEM_PROMPT


def test_review_prompt_emphasizes_discipline_and_principle_updates():
    assert "私人投资策略官" in REVIEW_SYSTEM_PROMPT
    assert "交易纪律" in REVIEW_SYSTEM_PROMPT
    assert "决策质量" in REVIEW_SYSTEM_PROMPT
    assert "行为偏差" in REVIEW_SYSTEM_PROMPT
    # The section contract is stable ASCII IDs now, not Chinese literals —
    # the Chinese "准则更新建议" is a DISPLAY LABEL resolved from the catalog.
    assert "rule_updates" in REVIEW_SYSTEM_PROMPT
    # (the words still appear as PROSE in the reminders — what must not
    #  appear is a Chinese JSON key, asserted structurally in
    #  tests/services/test_ai_advisor_prompt_parity.py)
    assert "少做宏观预测" in REVIEW_SYSTEM_PROMPT
    assert "严格基于提供的context" in REVIEW_SYSTEM_PROMPT


def test_review_questions_prompt_stays_grounded_in_trade_records():
    assert "投资教练" in REVIEW_QUESTIONS_SYSTEM_PROMPT
    assert "交易记录" in REVIEW_QUESTIONS_SYSTEM_PROMPT
    assert "不要泛泛而谈" in REVIEW_QUESTIONS_SYSTEM_PROMPT
    assert "不要把问题写成宏观预测" in REVIEW_QUESTIONS_SYSTEM_PROMPT


def test_get_review_system_prompt_does_not_warn_when_prompts_section_is_absent(monkeypatch, caplog):
    from src.services.ai_advisor import prompts as prompts_module

    monkeypatch.setattr(prompts_module, "_get_prompts_config", lambda: {})

    with caplog.at_level(logging.WARNING, logger=prompts_module.__name__):
        prompt = prompts_module.get_review_system_prompt("zh-CN")

    assert "交易纪律" in prompt
    assert "prompts config not found in settings.yaml, using defaults" not in caplog.text


def test_get_review_system_prompt_warns_when_prompt_config_load_fails(monkeypatch, caplog):
    from src.services.ai_advisor import prompts as prompts_module

    monkeypatch.setattr(prompts_module, "_get_prompts_config", lambda: None)

    with caplog.at_level(logging.WARNING, logger=prompts_module.__name__):
        prompt = prompts_module.get_review_system_prompt("zh-CN")

    assert "交易纪律" in prompt
    assert "prompts config not found in settings.yaml, using defaults" in caplog.text
