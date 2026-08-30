"""Shared FX / cost-basis helpers.

Extracted from src/api/routes/performance.py to eliminate cross-layer imports.
Used by: performance.py, attribution.py, context_generator.py, portfolio_semantics.py.
"""
from src.data_manager.currency_converter import get_currency_service

# Display values that represent cash-equivalent asset classes.
# Authoritative source (extracted from src/api/routes/performance.py).
# performance.py now imports from here; do not re-define elsewhere.
CASH_CLASS_DISPLAY_VALUES = {
    "Cash (现金)",
    "Cash",
    "Cash Checking (活期)",
    "Cash Checking",
    "Cash Deposit (定期)",
    "Cash Deposit",
    "Bank Wealth (银行理财)",
    "Bank Wealth",
    # Money market funds (e.g. 示例流动货币B) carry a stable NAV of 1.0 and distribute
    # returns as yield, not price appreciation — they must show zero unrealized P&L,
    # matching WealthOS (_WEALTHOS_CASH_EQUIV_KEYWORDS). See issue #18.
    "Money Market (货基)",
    "Money Market",
    "货币市场",
}


def get_today_usd_cny_rate() -> float:
    return get_currency_service().get_latest_rate("USD", "CNY") or 7.0


def is_cash_equivalent_asset(top_class: str, sub_class: str) -> bool:
    return top_class in CASH_CLASS_DISPLAY_VALUES or sub_class in CASH_CLASS_DISPLAY_VALUES


def is_balance_only_holding(*, cost_price_unit, has_transactions: bool) -> bool:
    """True when a row is a reported *balance* rather than a position.

    A holding with neither a cost basis nor any transaction history came from a
    source that reports what an account is worth, not what was paid for it — the
    Financial-Summary balance columns are the canonical case.  Its lifetime P&L
    is **unknown, not zero**: treating the absent cost as ``0`` makes the whole
    balance read as profit (that is how a balance-only bond position showed
    a 100% gain), while charging the
    cost in at market value fabricates a Total Invested equal to the current
    value.  Callers must therefore emit *null* for invested / P&L / return
    (rendered "—") and exclude the asset from any gain aggregate — NOT reuse the
    cash-equivalent treatment.  A bond is not cash; its cost is simply not in the
    data.  (Owner may later record a real cost basis, at which point the row has a
    cost and stops matching this predicate.)

    Deliberately structural rather than name-based.  ``is_cash_equivalent_asset``
    keys on the asset-class *string*, so it protects a balance column only while
    somebody spells its class like cash; the two bond columns above were
    classified "CN Bonds" / "US Bonds" and fell straight through.  Every future
    Financial-Summary column would reintroduce the bug on the same terms.

    Distinct from cash-equivalents: a cash balance genuinely has zero gain (the
    balance is its own principal), whereas a balance-only bond has an unknown
    gain.  Money-market funds carry transactions, so this predicate does not
    claim them.  The two rules are complementary, not interchangeable.
    """
    return not has_transactions and not float(cost_price_unit or 0.0)


def calculate_cost_basis_cny(
    *,
    market_value: float,
    quantity: float,
    cost_price_unit: float,
    currency: str,
    top_class: str,
    sub_class: str,
    today_fx: float,
) -> float:
    if is_cash_equivalent_asset(top_class, sub_class):
        return market_value

    native_cost = float(cost_price_unit or 0.0) * float(quantity or 0.0)
    if (currency or "CNY") == "USD":
        return native_cost * today_fx
    return native_cost
