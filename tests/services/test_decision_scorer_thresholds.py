"""Tests for A2: per-asset-class verdict threshold bands in derive_verdict_suggestion()."""
import pytest
from src.services.decision_scorer import (
    derive_verdict_suggestion,
    VERDICT_GOOD_CALL,
    VERDICT_REGRET,
    VERDICT_BULLET_DODGED,
    VERDICT_MISSED_OPPORTUNITY,
    VERDICT_THRESHOLDS,
    _DEFAULT_BAND,
)


# ── Default (unmapped class) still uses the default band ──────────────────────

def test_default_band_unchanged_for_unknown_class():
    """outcome_pct == _DEFAULT_BAND on a buy with unmapped class → good_call."""
    result = derive_verdict_suggestion("buy", _DEFAULT_BAND, asset_class="UnknownClass")
    assert result == VERDICT_GOOD_CALL


def test_default_band_below_threshold_returns_none():
    """outcome_pct < _DEFAULT_BAND on an unmapped class → inconclusive (None)."""
    result = derive_verdict_suggestion("buy", _DEFAULT_BAND - 0.1, asset_class="UnknownClass")
    assert result is None


def test_no_asset_class_uses_default_band():
    """Omitting asset_class should fall back to _DEFAULT_BAND (backward compat)."""
    # Above default band for buy → good_call
    assert derive_verdict_suggestion("buy", _DEFAULT_BAND + 1.0) == VERDICT_GOOD_CALL
    # Just below → None
    assert derive_verdict_suggestion("buy", _DEFAULT_BAND - 0.1) is None


# ── Per-class bands (Cash/Bond class uses lower threshold) ────────────────────

def test_cash_class_uses_lower_band():
    """Cash/Bond asset class should have a lower threshold than the default 5%."""
    cash_band = VERDICT_THRESHOLDS.get("Cash", VERDICT_THRESHOLDS.get("Bond", _DEFAULT_BAND))
    assert cash_band < _DEFAULT_BAND, "Cash/Bond band should be lower than default Equity band"

    # A move at the cash band is a good_call for Cash...
    assert derive_verdict_suggestion("buy", cash_band, asset_class="Cash") == VERDICT_GOOD_CALL
    # ...but the same move is inconclusive for Equity (doesn't reach default band)
    if cash_band < _DEFAULT_BAND:
        assert derive_verdict_suggestion("buy", cash_band, asset_class="Equity") is None


def test_alts_class_uses_higher_band():
    """Alts class should have a higher threshold than the default (more volatile)."""
    alts_band = VERDICT_THRESHOLDS.get("Alts", None)
    if alts_band is None:
        pytest.skip("No Alts band configured — skipping")
    assert alts_band > _DEFAULT_BAND, "Alts band should be higher than default Equity band"

    # A move at default band is still inconclusive for Alts
    assert derive_verdict_suggestion("buy", _DEFAULT_BAND, asset_class="Alts") is None
    # A move at the Alts band is a good_call
    assert derive_verdict_suggestion("buy", alts_band, asset_class="Alts") == VERDICT_GOOD_CALL


# ── Sell directions ────────────────────────────────────────────────────────────

def test_sell_above_band_is_bullet_dodged():
    """Sell + positive outcome above band → bullet_dodged."""
    result = derive_verdict_suggestion("卖出", _DEFAULT_BAND + 1, asset_class="Equity")
    assert result == VERDICT_BULLET_DODGED


def test_sell_below_band_is_missed_opportunity():
    """Sell + negative outcome beyond band → missed_opportunity."""
    result = derive_verdict_suggestion("sell", -(_DEFAULT_BAND + 1), asset_class="Equity")
    assert result == VERDICT_MISSED_OPPORTUNITY


def test_buy_below_band_is_regret():
    """Buy + negative outcome beyond band → regret."""
    result = derive_verdict_suggestion("买入", -(_DEFAULT_BAND + 1), asset_class="Equity")
    assert result == VERDICT_REGRET


# ── Keyword classifier still wins when both are given ────────────────────────

def test_keyword_classifier_is_authoritative_not_threshold_function():
    """derive_verdict_suggestion is a UI hint only — keyword classifier is separate.

    This test documents that derive_verdict_suggestion is the numeric fallback,
    NOT the authoritative scorer. classify_verdict_from_text is the authoritative one.
    Both are imported here to confirm they coexist independently.
    """
    from src.services.decision_scorer import classify_verdict_from_text

    # Keyword classifier finds 'good_call' in text
    assert classify_verdict_from_text("buy", "止损成功") == "good_call"
    # Numeric fallback with None outcome → None
    assert derive_verdict_suggestion("buy", None) is None


# ── None / zero edge cases ─────────────────────────────────────────────────────

def test_none_outcome_returns_none():
    assert derive_verdict_suggestion("buy", None, asset_class="Equity") is None
    assert derive_verdict_suggestion("sell", None, asset_class="Cash") is None


def test_zero_outcome_returns_none():
    assert derive_verdict_suggestion("buy", 0.0, asset_class="Equity") is None


def test_unknown_action_returns_none():
    """Non-buy/sell action → can't determine direction → None."""
    assert derive_verdict_suggestion("hold", _DEFAULT_BAND + 10, asset_class="Equity") is None
