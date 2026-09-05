"""Pure P&L leaf helpers — the service-layer home of the unrealized-P&L math.

Relocated from ``src/api/routes/performance.py`` (plan 2026-08-02 §B.1b): the
service-layer P&L engine must not import *upward* from an API route (that would
create a circular import the moment ``performance.py`` calls the engine). The
pure helper therefore lives here; ``performance.py`` re-exports it for
backward-compat so existing callers (``data.py``, ``position_lots.py``,
``value_trap.py``, tests) keep importing it from the old location unchanged.

Behavior is byte-identical to the previous route-level implementation.
"""
from __future__ import annotations

from src.services.currency import (
    calculate_cost_basis_cny,
    is_cash_equivalent_asset,
)


def calculate_unrealized_pl_values(
    *,
    market_value: float,
    quantity: float,
    cost_price_unit: float,
    market_price_unit: float,
    currency: str,
    top_class: str,
    sub_class: str,
    today_fx: float,
) -> tuple[float, float]:
    """Return ``(unrealized_cny, unrealized_native)`` for one holdings row.

    Cash-equivalent rows have a NAV of ~1.0 and distribute return as yield, so
    their unrealized P&L is zero by definition. For USD assets the gain is
    computed in native currency (``(market_price − cost_price) × qty``) and then
    converted at *today's* FX, so a currency move never fabricates a gain/loss.
    CNY assets use ``market_value − cost_basis_cny`` directly.
    """
    if is_cash_equivalent_asset(top_class, sub_class):
        return 0.0, 0.0

    quantity = float(quantity or 0.0)
    cost_price_unit = float(cost_price_unit or 0.0)
    market_price_unit = float(market_price_unit or 0.0)
    market_value = float(market_value or 0.0)
    currency = currency or "CNY"

    if currency == "USD":
        if market_price_unit == 0.0 and quantity > 0:
            market_price_unit = market_value / quantity / today_fx
        unrealized_native = (market_price_unit - cost_price_unit) * quantity
        return unrealized_native * today_fx, unrealized_native

    cost_basis_cny = calculate_cost_basis_cny(
        market_value=market_value,
        quantity=quantity,
        cost_price_unit=cost_price_unit,
        currency=currency,
        top_class=top_class,
        sub_class=sub_class,
        today_fx=today_fx,
    )
    unrealized_cny = market_value - cost_basis_cny
    return unrealized_cny, unrealized_cny
