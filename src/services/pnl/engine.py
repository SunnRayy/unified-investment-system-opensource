"""The one P&L engine (plan §B — read-only orchestration, no writes).

``compute_portfolio_pnl(db, *, scope)`` fetches the current active holdings
once, classifies each asset into a base :class:`Treatment`, computes the shared
leaf math (cost basis, unrealized, realized) and returns a :class:`PortfolioPnL`
of per-asset :class:`AssetPnL` records plus the aggregates computed once.

This is an *orchestration* refactor, not a math rewrite: every leaf calculation
is an existing, single-source helper reused unchanged —

- ``calculate_cost_basis_cny`` / ``is_cash_equivalent_asset`` /
  ``is_balance_only_holding``          (``services/currency.py``)
- ``calculate_unrealized_pl_values``   (relocated to ``pnl/pnl_math.py``)
- ``unrealized_from_holdings_row``     (``services/position_lots.py``)
- ``calculate_realized_pnl``           (``services/portfolio_helpers.py``),
  which itself preserves ``select_transaction_sources`` +
  ``is_realized_pnl_exempt`` so co-authority ledgers never double-count.

Owner-entered overrides (#7) are loaded and authority-checked in ``pnl/manual.py``
and overlaid here, *after* the base treatment — see the overlay block below.
"""
from __future__ import annotations

from typing import Optional

from src.services.currency import (
    calculate_cost_basis_cny,
    get_today_usd_cny_rate,
    is_balance_only_holding,
    is_cash_equivalent_asset,
)
from src.services.portfolio_helpers import (
    calculate_realized_pnl,
    get_display_name,
    resolve_top_class,
)
from src.services.position_lots import unrealized_from_holdings_row
from src.services.pnl.aggregate import summary_totals
from src.services.pnl.manual import (
    is_manually_loggable,
    load_manual_overrides,
    superseded_override_ids,
)
from src.services.pnl.models import AssetPnL, PortfolioPnL, Scope, Treatment
from src.services.pnl.snapshot import (
    assets_with_reader_transactions,
    assets_with_transactions,
    closed_asset_meta,
    fetch_active_holdings,
    first_buy_dates,
    latest_snapshot_date,
    sold_after_snapshot,
    total_invested_native,
    transaction_asset_ids,
    transaction_currency,
)


def _realized_map(db, asset_ids, *, start_date: Optional[str], today_fx: float) -> dict:
    """Per-asset realized P&L, memoized across the call.

    Returns ``{asset_id: (native_amount, cny_amount, native_currency)}``.

    Correctness-first (plan §B.2): rather than a naive ``GROUP BY asset_id``
    replay — which would double-count co-authority (Schwab+IBKR) ledgers and
    ignore cash-class exemptions — this delegates to the existing per-asset
    ``calculate_realized_pnl``, which already routes through
    ``select_transaction_sources`` + ``is_realized_pnl_exempt`` and does exact
    native-currency FIFO with the period pre-replay. Native currency is
    converted to CNY *after* FIFO, exactly as every legacy surface did.
    """
    out: dict[str, tuple] = {}
    for aid in asset_ids:
        if not aid or aid in out:
            continue
        amount, currency = calculate_realized_pnl(db, aid, start_date=start_date)
        native = float(amount or 0.0)
        cny = native * today_fx if currency == "USD" else native
        out[aid] = (native, cny, currency)
    return out


def compute_portfolio_pnl(db, *, scope: Scope) -> PortfolioPnL:
    """Compute the whole-portfolio P&L for ``scope`` — the single entry point."""
    today_fx = scope.today_fx if scope.today_fx is not None else get_today_usd_cny_rate()
    start_date = scope.start_date

    rows = fetch_active_holdings(
        db,
        start_date=start_date,
        positive_only=scope.positive_positions_only,
        resolve_taxonomy=scope.resolve_taxonomy,
    )
    active_ids = {row[0] for row in rows if row[0]}

    # WealthOS: drop reader positions fully sold after their last snapshot (ETHA
    # case), restricted to reader holding-sources (QDII-lagged assets stay).
    if scope.drop_sold_after_snapshot:
        source_by_id = {row[0]: row[10] for row in rows if row[0]}
        sold = sold_after_snapshot(db)
        from src.sources.registry import get_registry

        candidate_sources = frozenset(get_registry().holding_source_systems())
        drop = {
            aid for aid in sold
            if source_by_id.get(aid) in candidate_sources
        }
        if drop:
            rows = [row for row in rows if row[0] not in drop]
            active_ids -= drop

    txn_ids_all = assets_with_transactions(db)          # balance-only discriminator
    reader_txn_ids = assets_with_reader_transactions(db)  # #7 loggability
    overrides = load_manual_overrides(db)              # #7 owner-entered figures
    superseded = (
        superseded_override_ids(db, overrides.keys()) if overrides else frozenset()
    )

    # Realized P&L covers current holdings UNION period-scoped transaction assets
    # (so a fully-sold "closed" asset still books its realized gain).
    union_ids = set(active_ids) | transaction_asset_ids(db, start_date)
    realized_map = _realized_map(db, union_ids, start_date=start_date, today_fx=today_fx)

    # Transaction-ledger provenance (WealthOS): first-buy dates, closed invested
    # basis, closed name/type/currency. Loaded once, only when requested.
    first_buy: dict = {}
    invested_native_map: dict = {}
    txn_currency: dict = {}
    closed_meta: dict = {}
    if scope.with_transaction_provenance:
        first_buy = first_buy_dates(db)
        invested_native_map = total_invested_native(db)
        txn_currency = transaction_currency(db)
        closed_ids = [aid for aid in union_ids if aid not in active_ids]
        closed_meta = closed_asset_meta(db, closed_ids)

    def _realized_parts(aid):
        native, cny, cur = realized_map.get(aid, (0.0, 0.0, "CNY"))
        return native, cny, cur

    assets: list[AssetPnL] = []
    seen: set[str] = set()

    for row in rows:
        aid = row[0]
        if not aid:
            continue
        name = row[1]
        top = resolve_top_class(row[2] or "")
        sub = get_display_name(row[3] or "")
        market_value = float(row[4] or 0.0)
        quantity = float(row[5] or 0.0)
        cost_price_unit = row[6]
        market_price_unit = float(row[7] or 0.0)
        currency = str(row[8] or "CNY")
        asset_class_registry = row[9]
        source_system = row[10]
        realized_native, realized, realized_currency = _realized_parts(aid)

        # Base treatment (cash wins over balance-only; balance-only wins over
        # traded) — identical ordering to the V7.8.3 per-site logic.
        if is_cash_equivalent_asset(top, sub):
            treatment = Treatment.cash
        elif is_balance_only_holding(
            cost_price_unit=cost_price_unit, has_transactions=str(aid) in txn_ids_all
        ):
            treatment = Treatment.balance_only
        else:
            treatment = Treatment.traded

        if treatment is Treatment.balance_only:
            # Cost is unknown, not zero: value counts in net worth, but cost /
            # unrealized / return are None and excluded from gain aggregates.
            cost_basis: Optional[float] = None
            unrealized: Optional[float] = None
            return_pct: Optional[float] = None
            lots_pct: Optional[float] = None
        else:
            cost_basis = calculate_cost_basis_cny(
                market_value=market_value,
                quantity=quantity,
                cost_price_unit=float(cost_price_unit or 0.0),
                currency=currency,
                top_class=top,
                sub_class=sub,
                today_fx=today_fx,
            )
            # Value-based unrealized (market_value − cost_basis_cny): the summary
            # semantics. For cash, cost == value so this is 0.
            unrealized = market_value - cost_basis
            return_pct = (unrealized / cost_basis * 100.0) if cost_basis else None
            lots_pct = (
                unrealized_from_holdings_row(
                    market_value=market_value,
                    quantity=quantity,
                    cost_price_unit=float(cost_price_unit or 0.0),
                    market_price_unit=market_price_unit,
                    currency=currency,
                    top_class=top,
                    sub_class=sub,
                    today_fx=today_fx,
                )
                if treatment is Treatment.traded
                else None
            )

        # ── #7 manual overlay (plan §C.1) — base treatment FIRST, then overlay ──
        # The base cash/traded/balance_only classification above is computed
        # exactly as it was before #7; this only adjusts specific fields on top.
        manual = None if aid in superseded else overrides.get(aid)
        has_manual = False
        if manual is not None:
            # Rule 2 — a logged COST makes an unknown-cost asset measurable.
            # Rule 1 — but a cash-equivalent keeps unrealized = 0 regardless: a
            # cash balance has no price basis, so "cost" cannot mean market−cost
            # there. The base cash classification survives the overlay.
            if manual.cost_basis_cny is not None and treatment is not Treatment.cash:
                cost_basis = float(manual.cost_basis_cny)
                unrealized = market_value - cost_basis
                return_pct = (unrealized / cost_basis * 100.0) if cost_basis else None
                has_manual = True

            # Rule 3 — a logged REALIZED figure is overlaid AFTER the base
            # suppression, so it survives both the cash realized=0 path and the
            # balance-only exclusion.
            #
            # Rule: ALL-TIME only. The table holds ONE cumulative figure, which
            # cannot yield a month/quarter delta, so period-scoped scopes ignore
            # it rather than leak a lifetime number into a 1m view (plan §C.1).
            if manual.realized_pnl_cny is not None and scope.mode == "current":
                realized = float(manual.realized_pnl_cny)
                # The manual figure is CNY by definition (no currency column).
                realized_native = realized
                realized_currency = "CNY"
                has_manual = True

            if has_manual:
                treatment = Treatment.manual

        # Lifetime, stated once for every treatment: measurable when the cost is
        # known, otherwise the realized amount alone carries it. This is what
        # makes plan §C.1 rule 4 (manual-realized-only) fall out rather than need
        # a special case — cost stays None, but the logged profit still shows.
        if cost_basis is not None:
            lifetime: Optional[float] = (unrealized or 0.0) + realized
        else:
            lifetime = realized if realized else None

        assets.append(
            AssetPnL(
                asset_id=aid,
                name=name,
                top_class=top,
                sub_class=sub,
                source_system=source_system,
                currency=currency,
                market_value_cny=market_value,
                treatment=treatment,
                cost_basis_cny=cost_basis,
                unrealized_cny=unrealized,
                realized_cny=realized,
                lifetime_cny=lifetime,
                return_pct=return_pct,
                unrealized_current_lots_pct=lots_pct,
                first_acquired=first_buy.get(aid),
                has_manual_data=has_manual,
                is_current=True,
                asset_class_registry=asset_class_registry,
                quantity=quantity,
                cost_price_unit=cost_price_unit,
                market_price_unit=market_price_unit,
                realized_native=realized_native,
                realized_currency=realized_currency,
                has_transactions=str(aid) in txn_ids_all,
                invested_native=None,
                can_log_manual_pnl=is_manually_loggable(
                    aid, has_reader_transactions=str(aid) in reader_txn_ids
                ),
            )
        )
        seen.add(aid)

    # Closed / transaction-only assets: realized P&L only, no current value.
    for aid in union_ids:
        if aid in seen:
            continue
        realized_native, realized, realized_currency = _realized_parts(aid)
        meta = closed_meta.get(aid, {})
        # Resolved top/sub class for closed assets (from the transaction-ledger
        # provenance join). Left empty when provenance is off — gains skips closed
        # rows and the summary never reads a closed row's class, so their parity is
        # unaffected; by-class turns provenance ON to land realized in the class.
        closed_top_raw = meta.get("top_class")
        closed_sub_raw = meta.get("sub_class")
        closed_top = resolve_top_class(closed_top_raw) if closed_top_raw else ""
        closed_sub = get_display_name(closed_sub_raw) if closed_sub_raw else ""
        assets.append(
            AssetPnL(
                asset_id=aid,
                name=meta.get("name"),
                top_class=closed_top,
                sub_class=closed_sub,
                source_system=None,
                currency=txn_currency.get(aid, "CNY"),
                market_value_cny=0.0,
                treatment=Treatment.traded,
                cost_basis_cny=None,
                unrealized_cny=None,
                realized_cny=realized,
                lifetime_cny=realized if realized else None,
                return_pct=None,
                unrealized_current_lots_pct=None,
                first_acquired=first_buy.get(aid),
                has_manual_data=False,
                is_current=False,
                asset_class_registry=meta.get("type"),
                quantity=0.0,
                cost_price_unit=None,
                market_price_unit=0.0,
                realized_native=realized_native,
                realized_currency=realized_currency,
                has_transactions=str(aid) in txn_ids_all,
                invested_native=invested_native_map.get(aid, 0.0),
            )
        )

    totals = summary_totals(assets, excluded_ids=frozenset(), apply_name_filter=False)
    return PortfolioPnL(
        scope=scope,
        today_fx=today_fx,
        snapshot_date=latest_snapshot_date(db, start_date),
        assets=assets,
        net_worth=totals["net_worth"],
        total_cost_basis=totals["total_cost_basis"],
        measurable_value=totals["measurable_value"],
        total_unrealized=totals["total_unrealized"],
        total_realized=totals["total_realized"],
        total_lifetime=totals["total_lifetime"],
        asset_count=totals["asset_count"],
        return_pct=totals["return_pct"],
    )
