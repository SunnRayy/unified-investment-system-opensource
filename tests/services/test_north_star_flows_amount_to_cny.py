"""Unit tests for `_amount_to_cny` (src/services/north_star_flows.py).

Plan: docs/plans/2026-07-25-amount-net-sign-convention-sweep.md §4 (T2).

`_amount_to_cny` is used ONLY on the transactions path, where
`transactions.amount_net` carries three incompatible per-reader sign
conventions with no normalization layer (AGENTS.md Rule 26) — so the stored
sign carries no reliable economic direction. This helper must return the
ABSOLUTE MAGNITUDE in CNY; callers derive direction from `transaction_type`.

Do NOT add these assertions to FS-cash / income-expense tests — that path
(`info["amount_cny"]`) deliberately does not go through this helper because
its sign IS genuine economic direction. See
tests/services/test_fs_cash_flows.py::test_fs_cash_delta_stays_negative_for_balance_decrease
for that regression guard.
"""
from __future__ import annotations

from src.services.north_star_flows import _amount_to_cny


def test_negative_amount_net_returns_positive_magnitude_cny():
    """A Schwab-convention negative buy (amount_net=-500, CNY) must return +500."""
    assert _amount_to_cny(-500.0, "CNY", fx_rate=7.0) == 500.0


def test_positive_amount_net_returns_same_magnitude_cny():
    """An already-positive CNY amount is unchanged in magnitude."""
    assert _amount_to_cny(300.0, "CNY", fx_rate=7.0) == 300.0


def test_negative_usd_amount_is_abs_then_fx_converted():
    """Negative USD amount must be abs()ed AND scaled by fx_rate — order matters:
    abs(-500) * 7.0 = 3500.0, not abs(-500 * 7.0) (same result here, but this
    pins the contract so a future refactor can't silently reorder it wrong,
    e.g. converting first then taking abs of a NaN/None edge case)."""
    assert _amount_to_cny(-500.0, "USD", fx_rate=7.0) == 3500.0


def test_positive_usd_amount_is_fx_converted():
    assert _amount_to_cny(200.0, "usd", fx_rate=7.5) == 1500.0  # lower-case currency code


def test_none_amount_returns_zero():
    assert _amount_to_cny(None, "CNY", fx_rate=7.0) == 0.0


def test_none_currency_treated_as_cny():
    assert _amount_to_cny(-100.0, None, fx_rate=7.0) == 100.0
