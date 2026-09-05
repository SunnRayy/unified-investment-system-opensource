"""Tests for integrity check #4 (`xirr_proxy_in_range` — `_check_xirr_in_range`).

Plan: docs/plans/2026-07-25-amount-net-sign-convention-sweep.md §3 (F-1).

Before the 2026-07-25 fix, this check's `total_invested` CTE matched ZERO rows
because it filtered `transaction_type IN ('BUY', 'VEST', 'DEPOSIT')` — stored
values are lowercase/mixed-case ('buy', 'vest', 'Buy'), never uppercase. That
made `total_invested` NULL on every run, so the check silently returned a false
PASS (`actual_value="insufficient_data"`) instead of ever computing a real
percentage. Two more defects were stacked on top: `AND amount_net > 0` dropped
all negative (Schwab-convention) buys, and there was no FX conversion despite
mixing USD and CNY `amount_net` in one raw SUM.

This test proves the check is now non-vacuous: a fixture with lowercase
'buy'/'vest', a capitalized 'Buy' (AIA convention), a NEGATIVE amount_net buy
row (Schwab convention), and a USD-denominated row must all be counted, with
the USD row FX-converted and the negative row's magnitude included.

A pre-fix version of `_check_xirr_in_range` (uppercase-only type filter) would
return `actual_value="insufficient_data"` against this fixture, because no row
uses an exact uppercase transaction_type — so this test fails against the old
code, by construction.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.validation.data_integrity_gate import _check_xirr_in_range


@pytest.fixture
def conn():
    db = DatabaseConnector(":memory:")
    initialize_schema(db)
    yield db
    db.close()


def _insert_holding(db, *, snapshot_date, asset_id, market_value, source_system="Schwab_CSV"):
    db.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit, market_value,
            currency, account, source_system, is_shadow
        ) VALUES (?, ?, 'Test Asset', 'ETF', 10.0, 'share', 100.0, 110.0, ?, 'CNY', 'Test', ?, FALSE)
        """,
        (snapshot_date, asset_id, market_value, source_system),
    )


def _insert_txn(db, *, transaction_type, amount_net, currency="CNY", asset_id="US_STK_TEST"):
    db.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, amount_net, commission_fee,
            currency, source_system
        ) VALUES ('2026-01-15', ?, 'Test Asset', ?, 1.0, 1.0, ?, ?, 0.0, ?, 'Test_Source')
        """,
        (asset_id, transaction_type, amount_net, amount_net, currency),
    )


def test_check4_non_vacuous_with_mixed_case_mixed_sign_and_usd(conn):
    """Lowercase 'buy'/'vest', capitalized 'Buy', a negative buy, and a USD row
    must all contribute to total_invested — the check must return a real
    percentage, not the 'insufficient_data' false-PASS."""
    _insert_holding(conn, snapshot_date="2026-06-01", asset_id="US_STK_TEST", market_value=10000.0)

    # Schwab convention: buy stored NEGATIVE, in USD.
    _insert_txn(conn, transaction_type="buy", amount_net=-500.0, currency="USD")
    # AIA convention: capitalized 'Buy', positive, CNY.
    _insert_txn(conn, transaction_type="Buy", amount_net=300.0, currency="CNY")
    # RSU vest, lowercase, positive, CNY.
    _insert_txn(conn, transaction_type="vest", amount_net=1000.0, currency="CNY")
    # A sell row must NOT be counted as "invested".
    _insert_txn(conn, transaction_type="sell", amount_net=200.0, currency="CNY")

    fake_rate = 8.0
    with patch("src.services.currency.get_today_usd_cny_rate", return_value=fake_rate):
        result = _check_xirr_in_range(conn)

    assert result.name == "xirr_proxy_in_range"
    assert result.actual_value != "insufficient_data", (
        f"Check #4 is still vacuous — got {result.actual_value!r}. "
        "This means the transaction_type case filter, the amount_net>0 filter, "
        "or both, are still dropping rows."
    )

    # Expected: USD row converted at fake_rate, negative row's magnitude included.
    expected_invested = abs(-500.0) * fake_rate + 300.0 + 1000.0  # = 5300.0
    current_value = 10000.0
    expected_proxy = (current_value - expected_invested) / expected_invested
    expected_str = f"{expected_proxy:.1%} return proxy"

    assert result.actual_value == expected_str, (
        f"Expected {expected_str!r} (USD row scaled by {fake_rate}, negative row "
        f"included), got {result.actual_value!r}."
    )


def test_check4_insufficient_data_when_truly_no_matching_rows(conn):
    """Sanity check: the 'insufficient_data' path is legitimate when there
    really are no buy/vest/deposit transactions — not a bug in itself."""
    _insert_holding(conn, snapshot_date="2026-06-01", asset_id="US_STK_TEST", market_value=10000.0)
    _insert_txn(conn, transaction_type="sell", amount_net=200.0, currency="CNY")

    result = _check_xirr_in_range(conn)
    assert result.passed is True
    assert result.actual_value == "insufficient_data"


def test_check4_fx_lookup_failure_falls_back_to_default_rate(conn):
    """An FX lookup failure must not make the check throw — falls back to 7.0."""
    _insert_holding(conn, snapshot_date="2026-06-01", asset_id="US_STK_TEST", market_value=10000.0)
    _insert_txn(conn, transaction_type="buy", amount_net=-500.0, currency="USD")

    with patch("src.services.currency.get_today_usd_cny_rate", side_effect=RuntimeError("network down")):
        result = _check_xirr_in_range(conn)  # must not raise

    expected_invested = abs(-500.0) * 7.0
    expected_proxy = (10000.0 - expected_invested) / expected_invested
    assert result.actual_value == f"{expected_proxy:.1%} return proxy"
