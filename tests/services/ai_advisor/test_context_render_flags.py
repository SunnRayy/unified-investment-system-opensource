"""Tests for render_context flags: include_technical_context and valuation-before-technical order."""
from __future__ import annotations
from unittest.mock import MagicMock


def _make_stub(valuation_block="## 估值仪表盘\n沪深300 | FAIR",
               technical_block="## 技术分析\nRSI=55"):
    cb = MagicMock()
    cb.build_identity_context.return_value = ""
    cb.build_portfolio_context.return_value = ""
    cb.build_market_context.return_value = ""
    cb.build_strategy_context.return_value = ""
    cb.build_transactions_context.return_value = ""
    cb.build_realtime_context.return_value = ""
    cb.build_valuation_context.return_value = valuation_block
    cb.build_technical_context.return_value = technical_block
    return cb


def _cfg(**overrides) -> dict:
    base = {
        "tiers": {},
        "include_realtime": False,
        "include_valuation_context": True,
        "include_technical_context": True,
    }
    base.update(overrides)
    return base


def test_technical_block_included_by_default():
    from src.services.ai_advisor.context_builder import render_context
    result = render_context(_make_stub(), _cfg())
    assert "RSI=55" in result


def test_technical_block_excluded_when_flag_false():
    from src.services.ai_advisor.context_builder import render_context
    result = render_context(_make_stub(), _cfg(include_technical_context=False))
    assert "RSI=55" not in result


def test_valuation_appears_before_technical():
    """Valuation block must come before technical block in rendered output."""
    from src.services.ai_advisor.context_builder import render_context
    result = render_context(_make_stub(), _cfg())
    val_pos = result.find("估值仪表盘")
    tech_pos = result.find("RSI=55")
    assert val_pos != -1
    assert tech_pos != -1
    assert val_pos < tech_pos, "valuation block must precede technical block"


def test_valuation_excluded_when_flag_false():
    from src.services.ai_advisor.context_builder import render_context
    result = render_context(_make_stub(), _cfg(include_valuation_context=False))
    assert "估值仪表盘" not in result


def test_both_excluded():
    from src.services.ai_advisor.context_builder import render_context
    result = render_context(_make_stub(), _cfg(include_valuation_context=False, include_technical_context=False))
    assert "估值仪表盘" not in result
    assert "RSI=55" not in result
