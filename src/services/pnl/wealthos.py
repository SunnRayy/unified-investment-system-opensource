"""WealthOS holdings view — a thin formatter over ``compute_portfolio_pnl``.

The engine owns the expensive, duplicated glue: the active-holdings snapshot
(with the WealthOS positive-position filter + sold-after-snapshot removal), the
closed / transaction-only asset union, the co-authority-safe realized-P&L map,
and the transaction-ledger provenance (first-buy dates, closed invested basis,
closed name/type/currency). This module maps the engine's per-asset records into
the WealthOS response shape, reproducing the V7.8.3 endpoint byte-for-byte:

- field names invested / cur / pl / pl_native / pnl_currency / ret /
  unrealized_current_lots_pct / open_value_trap_review / name / code / type /
  period / status; 3-decimal invested/pl/pl_native, 2-decimal cur/ret;
- keyword cash-equivalent convention (cash: invested == value, P&L 0);
- null balance-only records (FS bonds: invested/pl/ret = None);
- price-based unrealized via the shared leaf helpers, on the RAW registry class;
- active-then-closed ordering (active by |current|/|pl|, closed after), with
  input order reconstructed exactly for tie-stability;
- rebalanceable vs non-rebalanceable partition + the open-value-trap badge.
"""
from __future__ import annotations

from datetime import date, datetime

from src.services.currency import is_balance_only_holding
from src.services.pnl.engine import compute_portfolio_pnl
from src.services.pnl.models import Scope
from src.services.pnl.pnl_math import calculate_unrealized_pl_values
from src.services.pnl.snapshot import open_value_trap_asset_ids
from src.services.position_lots import unrealized_from_holdings_row
from src.services.rebalanceable_filter import fetch_non_rebalanceable_asset_ids
from src.services.taxonomy_display import get_class_name_cn_map

# Cash-equivalent by keyword on the raw registry class (WealthOS convention).
_CASH_EQUIV_KEYWORDS = ("Cash", "现金", "Money Market", "Bank Wealth", "货币")
# Non-rebalanceable by display type (fallback alongside the id-set).
_NON_REBAL_TYPES = [
    "Real Estate", "Insurance", "房地产", "保险", "Property (房产)",
    "Insurance (保险)", "Residential (住宅)", "Commercial (商业)", "REITs (信托)",
]


def _format_period(first_date, today: date) -> str:
    if first_date is None:
        return "Unknown"
    try:
        if isinstance(first_date, str):
            first_date = datetime.strptime(first_date, "%Y-%m-%d").date()
        elif hasattr(first_date, "date") and callable(first_date.date):
            first_date = first_date.date()
        elif not isinstance(first_date, date):
            return "Unknown"
        days = (today - first_date).days
        if days < 30:
            return f"{days}d"
        elif days < 365:
            return f"{days // 30}m"
        else:
            years = days // 365
            rem_months = (days % 365) // 30
            return f"{years}y {rem_months}m" if rem_months else f"{years}y"
    except Exception:
        return "Unknown"


def _ordered_asset_ids(db, active_ids: set) -> list:
    """Reconstruct the legacy ``all_asset_ids`` order for tie-stable sorting:
    transaction assets in DB order, then holdings-only active assets (sorted)."""
    txn_order = [
        r[0]
        for r in db.execute(
            "SELECT DISTINCT asset_id FROM transactions WHERE asset_id IS NOT NULL"
        ).fetchall()
    ]
    seen = set(txn_order)
    ordered = list(txn_order)
    for aid in sorted(active_ids):
        if aid not in seen:
            ordered.append(aid)
    return ordered


def build_wealthos_assets(db, *, include_non_rebalanceable: bool = False) -> dict:
    """Return ``{"assets": [...], "non_rebalanceable_assets": [...]}``."""
    today = date.today()
    portfolio = compute_portfolio_pnl(
        db,
        scope=Scope(
            positive_positions_only=True,
            drop_sold_after_snapshot=True,
            with_transaction_provenance=True,
            resolve_taxonomy=False,
        ),
    )
    fx = portfolio.today_fx
    by_id = {a.asset_id: a for a in portfolio.assets}
    active_ids = {a.asset_id for a in portfolio.assets if a.is_current}
    open_review = open_value_trap_asset_ids(db)
    # Additive _cn companion for `type` below (Program BIL / WS-9). `type` here is
    # the raw asset_registry.asset_class value, which is a taxonomy_classes.name.
    name_cn_map = get_class_name_cn_map(db)

    records = []
    for aid in _ordered_asset_ids(db, active_ids):
        a = by_id.get(aid)
        if a is None:
            continue
        asset_type = a.asset_class_registry or "Unknown"
        native_currency = a.currency or "CNY"

        if a.is_current:
            name = a.name or aid
            market_value = a.market_value_cny
            quantity = a.quantity
            cost_price_unit = float(a.cost_price_unit or 0.0)
            market_price_unit = a.market_price_unit
            cost_basis = cost_price_unit * quantity
            invested_amount = cost_basis * fx if native_currency == "USD" else cost_basis
            unrealized, unrealized_native = calculate_unrealized_pl_values(
                market_value=market_value, quantity=quantity,
                cost_price_unit=cost_price_unit, market_price_unit=market_price_unit,
                currency=native_currency, top_class=asset_type, sub_class=asset_type,
                today_fx=fx,
            )
            unrealized_current_lots_pct = unrealized_from_holdings_row(
                market_value=market_value, quantity=quantity,
                cost_price_unit=cost_price_unit, market_price_unit=market_price_unit,
                currency=native_currency, top_class=asset_type, sub_class=asset_type,
                today_fx=fx,
            )
            # Faithful legacy rule: a NON-cash holding with no cost basis and no
            # transactions. Computed from raw inputs (not the engine's display-
            # based treatment) so the WealthOS keyword cash-equiv check downstream
            # remains the sole cash authority for this surface.
            is_balance_only = is_balance_only_holding(
                cost_price_unit=cost_price_unit, has_transactions=a.has_transactions
            )
            status = "ACTIVE"
        else:
            name = a.name or aid
            market_value = 0.0
            invested_native = a.invested_native or 0.0
            invested_amount = invested_native * fx if native_currency == "USD" else invested_native
            unrealized = 0.0
            unrealized_native = 0.0
            unrealized_current_lots_pct = None
            is_balance_only = False
            status = "CLOSED"

        is_cash_equiv = any(kw in (asset_type or "") for kw in _CASH_EQUIV_KEYWORDS)

        # Owner-logged P&L first (#7). Both branches below re-derive their treatment
        # from RAW inputs — `is_balance_only_holding(cost_price_unit, has_transactions)`
        # and the cash keyword check — and neither can see an override, so without
        # this branch a logged figure would be computed by the engine and then thrown
        # away here (the row would keep showing "—" while the KPI total moved).
        #
        # The engine has already applied the full precedence, including the two rules
        # this surface would otherwise break: a cash-equivalent keeps unrealized = 0
        # but lets a logged realized figure through (§C.2), and a realized-only
        # override leaves the cost genuinely unknown. So read its numbers.
        #
        # Inert until something is logged — has_manual_data is False for every asset
        # otherwise — which is what keeps this surface byte-parity.
        if a.has_manual_data:
            invested_amount = a.cost_basis_cny      # None when only profit was logged
            unrealized = a.unrealized_cny or 0.0
            unrealized_native = unrealized          # manual figures are CNY by definition
            realized = a.realized_cny
            realized_native = realized
            lifetime_pl = a.lifetime_cny
            lifetime_pl_native = lifetime_pl
            # This column means lifetime return on what was invested, so it is derived
            # the WealthOS way rather than taken from the engine's
            # unrealized/cost `return_pct` — the two conventions differ.
            ret = (
                (lifetime_pl / invested_amount * 100)
                if invested_amount and lifetime_pl is not None
                else None
            )
            unrealized_current_lots_pct = None
        # Cash next: a cash/deposit/money-market balance IS its own principal, so
        # a genuine zero gain is correct. Only a NON-cash balance-only asset has an
        # unknown cost. Otherwise the real FIFO realized P&L applies.
        elif is_cash_equiv:
            unrealized = 0.0
            realized = 0.0
            lifetime_pl = 0.0
            lifetime_pl_native = 0.0
            if a.is_current:
                invested_amount = market_value
            unrealized_current_lots_pct = None
            invested_for_ret = invested_amount if invested_amount != 0 else abs(realized)
            ret = (lifetime_pl / invested_for_ret * 100) if invested_for_ret != 0 else 0.0
        elif is_balance_only:
            invested_amount = None
            realized = 0.0
            lifetime_pl = None
            lifetime_pl_native = None
            ret = None
            unrealized_current_lots_pct = None
        else:
            realized = a.realized_cny
            realized_native = a.realized_native
            lifetime_pl = unrealized + realized
            lifetime_pl_native = (
                unrealized_native + realized_native
                if native_currency == "USD"
                else lifetime_pl
            )
            invested_for_ret = invested_amount if invested_amount != 0 else abs(realized)
            ret = (lifetime_pl / invested_for_ret * 100) if invested_for_ret != 0 else 0.0

        records.append({
            "name": name,
            "code": aid,
            "type": asset_type,
            "type_cn": name_cn_map.get(asset_type),
            "period": _format_period(a.first_acquired, today),
            "status": status,
            "invested": round(invested_amount, 3) if invested_amount is not None else None,
            "cur": round(market_value, 2),
            "pl": round(lifetime_pl, 3) if lifetime_pl is not None else None,
            "pl_native": round(lifetime_pl_native, 3) if lifetime_pl_native is not None else None,
            "pnl_currency": native_currency,
            "ret": round(ret, 2) if ret is not None else None,
            "unrealized_current_lots_pct": (
                round(unrealized_current_lots_pct, 2)
                if unrealized_current_lots_pct is not None else None
            ),
            "open_value_trap_review": aid in open_review,
            # #7: this row's P&L comes from an owner-entered override, not from a
            # reader ledger. Drives the "Logged" badge and the edit-vs-log affordance.
            "has_manual_data": a.has_manual_data,
            # #7: whether the "Log P&L" affordance applies. Backend-resolved so the
            # UI never has to infer it from whether a figure looks empty.
            "can_log_manual_pnl": a.can_log_manual_pnl,
        })

    # Active first (by current value desc), then closed (by |pl| desc). Balance-only
    # pl=None sorts last within its group rather than crashing on abs(None).
    records.sort(key=lambda x: (
        0 if x["status"] == "ACTIVE" else 1,
        -abs(x["pl"]) if x["pl"] is not None else 0.0,
    ))

    excluded_ids = set()
    if not include_non_rebalanceable:
        excluded_ids = fetch_non_rebalanceable_asset_ids(db)

    main_assets = []
    non_rebalanceable = []
    for r in records:
        if not include_non_rebalanceable and (
            r["code"] in excluded_ids or r["type"] in _NON_REBAL_TYPES
        ):
            non_rebalanceable.append(r)
        else:
            main_assets.append(r)

    return {"assets": main_assets, "non_rebalanceable_assets": non_rebalanceable}
