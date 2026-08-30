"""Tests for per-asset-class analysis prompt dispatch."""
from __future__ import annotations
from src.analysis.prompts import get_analysis_system_prompt

def test_us_stock_prompt_contains_forward_pe_guidance():
    prompt = get_analysis_system_prompt(asset_class="US_STOCK")
    assert "forward PE" in prompt.lower() or "Fwd PE" in prompt or "远期市盈率" in prompt

def test_us_etf_prompt_same_as_us_stock():
    assert get_analysis_system_prompt("US_STOCK") == get_analysis_system_prompt("US_ETF")

def test_cn_fund_prompt_contains_pb_guidance():
    prompt = get_analysis_system_prompt(asset_class="CN_INDEX")
    assert "PB" in prompt or "市净率" in prompt or "pb" in prompt.lower()

def test_none_asset_class_returns_cn_hk_prompt():
    # Default (no valuation data) falls back to CN/HK template
    assert get_analysis_system_prompt(None) == get_analysis_system_prompt("CN_INDEX")

def test_us_and_cn_prompts_are_different():
    us = get_analysis_system_prompt("US_STOCK")
    cn = get_analysis_system_prompt("CN_INDEX")
    assert us != cn

def test_prompt_contains_valuation_schema_keys():
    """All prompts must still have the required JSON schema keys."""
    for cls in ("US_STOCK", "CN_INDEX", None):
        prompt = get_analysis_system_prompt(cls)
        for key in ("valuation_judgment", "rule_bucket", "operation_signal",
                    "falsification_conditions", "validity_period"):
            assert key in prompt, f"{key} missing from prompt for asset_class={cls}"

def test_rsi_not_primary_signal_in_prompt():
    """RSI guardrail must be explicitly stated in all prompts."""
    for cls in ("US_STOCK", "CN_INDEX", None):
        prompt = get_analysis_system_prompt(cls)
        assert "技术指标仅供参考" in prompt or "仅供参考，不得作为唯一买卖依据" in prompt, \
            f"RSI-as-reference guardrail missing from prompt for asset_class={cls}"


def test_us_index_routes_to_us_prompt():
    """US_INDEX (S&P500/Nasdaq100 proxies) must use the US prompt, not CN/HK."""
    assert get_analysis_system_prompt("US_INDEX") == get_analysis_system_prompt("US_STOCK")
