"""Tests for src/services/rule_buckets.py (F1.1 bucket classification, Batch B1).

Fixtures use an explicit VerificationConfig built from the same defaults as
config/verification.yaml — never the real on-disk file — so these tests are
independent of any future edit to the committed YAML.
"""
from src.services.rule_buckets import classify_asset_bucket, classify_trade_bucket
from src.services.verification_config import VerificationConfig


def _cfg() -> VerificationConfig:
    return VerificationConfig()  # dataclass defaults mirror config/verification.yaml


# ── classify_trade_bucket ───────────────────────────────────────────────────

def test_rsu_amzn_sell_is_compliance():
    assert classify_trade_bucket("RSU_AMZN", "sell", _cfg()) == "compliance"


def test_rsu_amzn_sell_case_insensitive_action():
    assert classify_trade_bucket("RSU_AMZN", "Sell", _cfg()) == "compliance"


def test_rsu_amzn_buy_is_value_not_compliance():
    """RSU_AMZN vests (buys) are not compliance-forced trades — only sells are."""
    assert classify_trade_bucket("RSU_AMZN", "buy", _cfg()) == "value"


def test_900009_sell_is_compliance():
    assert classify_trade_bucket("CN_FUND_900009", "sell", _cfg()) == "compliance"


def test_900009_buy_is_value():
    assert classify_trade_bucket("CN_FUND_900009", "buy", _cfg()) == "value"


def test_gold_any_action_is_ratio():
    assert classify_trade_bucket("ALTS_Paper_Gold", "buy", _cfg()) == "ratio"
    assert classify_trade_bucket("ALTS_Paper_Gold", "sell", _cfg()) == "ratio"
    assert classify_trade_bucket("GOLD_CMB_123", "buy", _cfg()) == "ratio"


def test_ibit_any_action_is_ratio():
    assert classify_trade_bucket("US_STK_IBIT", "buy", _cfg()) == "ratio"
    assert classify_trade_bucket("US_STK_IBIT", "sell", _cfg()) == "ratio"


def test_fbtc_any_action_is_ratio():
    assert classify_trade_bucket("US_STK_FBTC", "buy", _cfg()) == "ratio"
    assert classify_trade_bucket("US_STK_FBTC", "sell", _cfg()) == "ratio"


def test_sgov_any_action_is_liquidity():
    assert classify_trade_bucket("US_STK_SGOV", "buy", _cfg()) == "liquidity"
    assert classify_trade_bucket("US_STK_SGOV", "sell", _cfg()) == "liquidity"


def test_msft_buy_is_value():
    assert classify_trade_bucket("US_STK_MSFT", "buy", _cfg()) == "value"


def test_unmatched_action_falls_through_to_value():
    """An asset that matches a pattern but not the entry's action list is 'value'."""
    assert classify_trade_bucket("RSU_AMZN", "dividend", _cfg()) == "value"


def test_missing_asset_id_or_action_is_value():
    assert classify_trade_bucket("", "sell", _cfg()) == "value"
    assert classify_trade_bucket("RSU_AMZN", "", _cfg()) == "value"


def test_default_cfg_arg_loads_from_disk():
    """Calling without an explicit cfg loads the real config/verification.yaml."""
    result = classify_trade_bucket("RSU_AMZN", "sell", None)
    assert result == "compliance"


# ── classify_asset_bucket ────────────────────────────────────────────────────

def test_asset_bucket_rsu_amzn_is_compliance_regardless_of_action():
    """Asset-level lookup ignores the actions list — RSU_AMZN matches compliance
    even though only 'sell' is in the trade-level actions list."""
    assert classify_asset_bucket("RSU_AMZN", _cfg()) == "compliance"


def test_asset_bucket_gold_is_ratio():
    assert classify_asset_bucket("ALTS_Paper_Gold", _cfg()) == "ratio"


def test_asset_bucket_sgov_is_liquidity():
    assert classify_asset_bucket("US_STK_SGOV", _cfg()) == "liquidity"


def test_asset_bucket_unmatched_is_value():
    assert classify_asset_bucket("US_STK_MSFT", _cfg()) == "value"
