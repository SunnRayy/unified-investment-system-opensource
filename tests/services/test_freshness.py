"""Unit tests for src/services/freshness.py (R2-1).

Tests use near-today dynamic dates throughout — hardcoded dates age past
staleness windows and silently flip assertions to the wrong branch.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.freshness import (
    freshness_class_for,
    freshness_verdict,
    is_cash_like,
    is_fresh,
)


def _make_db() -> DatabaseConnector:
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    return conn


def _insert_holding(
    conn: DatabaseConnector,
    asset_id: str,
    *,
    snapshot_date: Optional[str] = None,
    price_updated_at: Optional[str] = None,
    market_price_unit: float = 10.0,
    asset_class: Optional[str] = None,
) -> None:
    if snapshot_date is None:
        snapshot_date = (date.today() - timedelta(days=1)).isoformat()
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, quantity, cost_price_unit,
             market_price_unit, market_value, currency, source_system, is_shadow,
             price_updated_at)
        VALUES (?, ?, ?, 100.0, 10.0, ?, 1000.0, 'CNY', 'test', FALSE, ?)
        """,
        [snapshot_date, asset_id, asset_id, market_price_unit, price_updated_at],
    )
    if asset_class:
        # Insert minimal asset_registry row so freshness_verdict can read asset_class
        conn.execute(
            """
            INSERT INTO asset_registry (canonical_id, asset_name, asset_class, currency)
            SELECT ?, ?, ?, 'CNY'
            WHERE NOT EXISTS (SELECT 1 FROM asset_registry WHERE canonical_id = ?)
            """,
            [asset_id, asset_id, asset_class, asset_id],
        )


# ── freshness_class_for ──────────────────────────────────────────────────────

def test_cn_fund_prefix_is_fast():
    assert freshness_class_for("CN_FUND_900013", "Unknown") == "fast"


def test_us_stk_prefix_is_fast():
    assert freshness_class_for("US_STK_MSFT", "Unknown") == "fast"


def test_us_etf_prefix_is_fast():
    assert freshness_class_for("US_ETF_VOO", "Unknown") == "fast"


def test_alts_gold_prefix_is_fast():
    assert freshness_class_for("ALTS_Paper_Gold", "Unknown") == "fast"


def test_ins_prefix_is_slow():
    assert freshness_class_for("Ins_Pacific_001", "Unknown") == "slow"


def test_pension_prefix_is_slow():
    assert freshness_class_for("Pension_Personal", "Unknown") == "slow"


def test_property_prefix_is_slow():
    assert freshness_class_for("Property_BJ_Apt", "Unknown") == "slow"


def test_pension_personal_is_slow_even_with_fast_asset_class():
    """ID prefix 'Pension_' takes priority over a fast asset_class.

    Real-DB observed: Pension_Personal had asset_class='CN Equity' in
    asset_registry; the old code returned 'fast' because the fast-class
    check preceded the slow-prefix check.  The ID prefix is more
    authoritative — pension/property/insurance are never daily-feed instruments.
    """
    assert freshness_class_for("Pension_Personal", "CN Equity") == "slow"
    assert freshness_class_for("Pension_Personal", "US Equity") == "slow"
    assert freshness_class_for("Property_BJ_Apt", "CN Equity") == "slow"
    assert freshness_class_for("Ins_Pacific_001", "Alternatives") == "slow"


def test_cn_equity_class_is_fast():
    assert freshness_class_for("UNKNOWN_ASSET", "CN Equity") == "fast"


def test_insurance_products_class_is_slow():
    assert freshness_class_for("UNKNOWN_ASSET", "Insurance Products") == "slow"


def test_unknown_asset_is_none():
    assert freshness_class_for("XYZ_UNKNOWN", "Unknown") == "none"


# ── is_cash_like ─────────────────────────────────────────────────────────────

def test_cash_prefix_is_cash_like():
    assert is_cash_like("CASH_Cash_CNY", "Unknown") is True


def test_cash_deposit_prefix_is_cash_like():
    assert is_cash_like("CASH_Deposit_CMB_CNY", "Unknown") is True


def test_wealth_prefix_is_cash_like():
    assert is_cash_like("Wealth_CMB", "Unknown") is True


def test_bank_wealth_class_is_cash_like():
    assert is_cash_like("SOME_ASSET", "Bank Wealth") is True


def test_money_market_class_is_cash_like():
    assert is_cash_like("SOME_ASSET", "Money Market") is True


def test_cn_fund_is_not_cash_like():
    assert is_cash_like("CN_FUND_900013", "CN Equity") is False


def test_us_stk_is_not_cash_like():
    assert is_cash_like("US_STK_MSFT", "US Equity") is False


# ── is_fresh ─────────────────────────────────────────────────────────────────

def test_is_fresh_none_class_always_fresh():
    """'none' freshness class → always fresh regardless of date."""
    stale_date = date.today() - timedelta(days=365)
    assert is_fresh(stale_date, "none") is True


def test_is_fresh_none_price_date_is_stale():
    """Unknown price date → always stale for 'fast'/'slow'."""
    assert is_fresh(None, "fast") is False
    assert is_fresh(None, "slow") is False


def test_is_fresh_fast_within_3_days():
    fresh_date = date.today() - timedelta(days=2)
    assert is_fresh(fresh_date, "fast") is True


def test_is_fresh_fast_14_days_is_stale():
    stale_date = date.today() - timedelta(days=14)
    assert is_fresh(stale_date, "fast") is False


def test_is_fresh_slow_within_7_days():
    fresh_date = date.today() - timedelta(days=5)
    assert is_fresh(fresh_date, "slow") is True


def test_is_fresh_slow_8_days_is_stale():
    stale_date = date.today() - timedelta(days=8)
    assert is_fresh(stale_date, "slow") is False


# ── freshness_verdict ────────────────────────────────────────────────────────

def test_freshness_verdict_fresh_cn_fund():
    """CN fund with today's snapshot → fast class, fresh."""
    conn = _make_db()
    try:
        _insert_holding(conn, "CN_FUND_900013", snapshot_date=date.today().isoformat())
        vd = freshness_verdict(conn, "CN_FUND_900013")
        assert vd["freshness_class"] == "fast"
        assert vd["fresh"] is True
        assert vd["price_date"] is not None
        assert vd["price"] == pytest.approx(10.0)
    finally:
        conn.close()


def test_freshness_verdict_stale_cn_fund_14_days():
    """CN fund with 14-day-old snapshot → fast class, stale."""
    conn = _make_db()
    try:
        stale = (date.today() - timedelta(days=14)).isoformat()
        _insert_holding(conn, "CN_FUND_900014", snapshot_date=stale)
        vd = freshness_verdict(conn, "CN_FUND_900014")
        assert vd["freshness_class"] == "fast"
        assert vd["fresh"] is False
    finally:
        conn.close()


def test_freshness_verdict_price_updated_at_overrides_stale_snapshot():
    """Fresh price_updated_at overrides a stale snapshot_date (F4.4 semantics)."""
    conn = _make_db()
    try:
        stale_snap = (date.today() - timedelta(days=20)).isoformat()
        fresh_ts = datetime.now().isoformat()
        _insert_holding(
            conn, "CN_FUND_900013",
            snapshot_date=stale_snap,
            price_updated_at=fresh_ts,
        )
        vd = freshness_verdict(conn, "CN_FUND_900013")
        assert vd["fresh"] is True  # price_updated_at = today → fresh
    finally:
        conn.close()


def test_freshness_verdict_missing_asset_returns_none_class():
    """No holdings row → 'none' class.
    'none' freshness class → no defined feed → fresh=True (no staleness check applies).
    The ruling gate must not block an asset for which we simply have no holdings data.
    """
    conn = _make_db()
    try:
        vd = freshness_verdict(conn, "NONEXISTENT_ASSET")
        assert vd["freshness_class"] == "none"
        assert vd["fresh"] is True  # 'none' class → always fresh (no feed defined)
        assert vd["price_date"] is None
    finally:
        conn.close()


def test_freshness_verdict_cash_like_asset_none_class():
    """CASH_* assets may not have asset_class in registry → none class → fresh=True."""
    conn = _make_db()
    try:
        stale = (date.today() - timedelta(days=30)).isoformat()
        _insert_holding(conn, "CASH_Deposit_CMB_CNY", snapshot_date=stale)
        # CASH_* gets 'none' class (not fast/slow by prefix — doesn't match _FAST or _SLOW)
        # Actually CASH_ doesn't start with CN_FUND_, US_STK_, etc. → none
        # and is_cash_like() returns True → scan would exempt it before reaching here
        vd = freshness_verdict(conn, "CASH_Deposit_CMB_CNY")
        # class is 'none' (no fast/slow prefix) → fresh=True regardless of date
        assert vd["freshness_class"] == "none"
        assert vd["fresh"] is True
    finally:
        conn.close()
