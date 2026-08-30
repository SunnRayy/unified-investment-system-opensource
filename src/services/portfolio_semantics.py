"""AI-advisor portfolio semantics — thin formatters over the P&L engine.

Release 1 / Step 5 of the P&L unification (docs/plans/2026-08-02-pnl-unification-
and-manual-cost.md). Both public functions here are now thin formatters over the
single ``compute_portfolio_pnl`` engine; their private per-asset loops (and the
duplicated snapshot / realized-P&L SQL) were deleted. The engine already carries
the V7.8.3 balance-only rule (non-cash balance-only excluded from gain
aggregates; cash checked first), which these functions carried inline before —
so the migration is **byte-parity**, no number changes. The return shapes (dict
keys, field names, rounding) are preserved exactly for
``ai_advisor/context_builder.py`` (aggregate summary + per-asset table) and
``valuation/collector.py``.

Parity-gated by tests/services/test_portfolio_semantics_engine_parity.py.
"""
from __future__ import annotations

from typing import Any

from src.services.currency import (
    calculate_cost_basis_cny,
    is_balance_only_holding,
)
from src.services.pnl import Scope, compute_portfolio_pnl, summary_totals
from src.services.pnl.snapshot import sold_after_snapshot
from src.services.rebalanceable_filter import fetch_non_rebalanceable_asset_ids


# WealthOS reader-holding sources eligible for the "fully sold after latest
# snapshot" close (QDII-lagged / non-reader assets stay). Kept identical to the
# pre-engine set — notably it does NOT include Broker_IBKR, so this surface must
# apply its own restriction rather than the engine's registry-wide drop flag.
_WEALTHOS_SOLD_CLOSE_CANDIDATE_SOURCES = {
    "Schwab_CSV",
    "CN_Fund_Excel",
    "Gold_Excel",
    "Insurance_Excel",
    "RSU_Excel",
}
_WEALTHOS_NON_REBALANCEABLE_TYPES = {
    "Real Estate",
    "Insurance",
    "房地产",
    "保险",
    "Property (房产)",
    "Insurance (保险)",
    "Residential (住宅)",
    "Commercial (商业)",
    "REITs (信托)",
}
_WEALTHOS_CASH_EQUIV_KEYWORDS = ("Cash", "现金", "Money Market", "Bank Wealth", "货币")


def _has_column(db: Any, table_name: str, column_name: str) -> bool:
    try:
        rows = db.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    except Exception:
        return False
    return any(len(row) > 1 and row[1] == column_name for row in rows)


def _is_non_rebalanceable_type(asset_type: str | None) -> bool:
    return bool(asset_type and asset_type in _WEALTHOS_NON_REBALANCEABLE_TYPES)


def build_portfolio_summary_semantics(
    db: Any,
    include_non_rebalanceable: bool = False,
) -> dict[str, float | int | str | None]:
    """Aggregate KPI semantics for the AI advisor — a thin formatter over the
    engine's ``summary_totals`` (identical accumulation to the pre-engine loop).

    The V7.8.3 balance-only rule (a balance-only asset's value counts in net
    worth but its unknown cost is excluded from the gain figure; realized is
    excluded by the id-set only) lives in ``summary_totals`` now. The
    non-rebalanceable exclusion uses the SAME id-set + top/sub name filter as
    before; every field is rounded to 2 decimals exactly as the advisor expects.
    """
    portfolio = compute_portfolio_pnl(db, scope=Scope())
    excluded_ids = (
        fetch_non_rebalanceable_asset_ids(db) if not include_non_rebalanceable else set()
    )
    totals = summary_totals(
        portfolio.assets,
        excluded_ids=excluded_ids,
        apply_name_filter=not include_non_rebalanceable,
    )
    return {
        "net_worth": round(totals["net_worth"], 2),
        "total_cost_basis": round(totals["total_cost_basis"], 2),
        "total_unrealized_pl": round(totals["total_unrealized"], 2),
        "unrealized_pl_pct": round(totals["return_pct"], 2),
        "total_realized_pl": round(totals["total_realized"], 2),
        "total_lifetime_pl": round(totals["total_lifetime"], 2),
        "asset_count": totals["asset_count"],
        "snapshot_date": portfolio.snapshot_date,
    }


def _resolve_display_name(asset_name: Any, display_name: Any, asset_id: str) -> str:
    """COALESCE(NULLIF(TRIM(asset_name),''), NULLIF(TRIM(display_name),''), asset_id).

    Reproduces the pre-engine WealthOS name resolution exactly. The engine
    carries only ``MAX(h.asset_name)``; the ``asset_registry.display_name``
    fallback is applied here from a bulk map so the resolved name is byte-identical.
    """
    an = str(asset_name).strip() if asset_name is not None else ""
    if an:
        return an
    dn = str(display_name).strip() if display_name is not None else ""
    if dn:
        return dn
    return asset_id


def fetch_wealthos_active_holdings(
    db: Any,
    include_non_rebalanceable: bool = False,
) -> list[dict[str, Any]]:
    """Per-asset active-holdings semantics for the AI advisor — a thin formatter
    over ``compute_portfolio_pnl`` (WealthOS active-holdings scope).

    The engine owns the positive-position snapshot and the co-authority-safe
    realized-P&L map. This formatter reproduces the pre-engine per-asset math
    byte-for-byte: the keyword cash-equivalent convention (cash: invested ==
    value, zero gain), null balance-only records (unknown cost → invested / pl /
    ret = None), value-based lifetime P&L on the RAW registry class, the
    source-restricted sold-after-snapshot close, and the non-rebalanceable
    filter — sorted by market value desc.
    """
    portfolio = compute_portfolio_pnl(
        db,
        scope=Scope(positive_positions_only=True, resolve_taxonomy=False),
    )
    today_fx = portfolio.today_fx
    active = [a for a in portfolio.assets if a.is_current]

    # Sold-after-snapshot close, restricted to the WealthOS candidate sources
    # (source map built from the active records — MAX(h.source_system) per asset).
    active_source_map = {str(a.asset_id): a.source_system for a in active}
    sold_after_snapshot_ids = {
        aid
        for aid in sold_after_snapshot(db)
        if active_source_map.get(str(aid)) in _WEALTHOS_SOLD_CLOSE_CANDIDATE_SOURCES
    }

    excluded_ids = (
        fetch_non_rebalanceable_asset_ids(db) if not include_non_rebalanceable else set()
    )

    # display_name fallback map (guarded — the column may be absent).
    display_names: dict[str, Any] = {}
    if _has_column(db, "asset_registry", "display_name"):
        try:
            for r in db.execute(
                "SELECT canonical_id, display_name FROM asset_registry"
            ).fetchall():
                if r and r[0] is not None:
                    display_names[str(r[0])] = r[1]
        except Exception:
            display_names = {}

    results: list[dict[str, Any]] = []
    for a in active:
        asset_id = str(a.asset_id)
        asset_type = str(a.asset_class_registry or "Unknown")
        if asset_id in sold_after_snapshot_ids:
            continue
        if not include_non_rebalanceable and (
            asset_id in excluded_ids or _is_non_rebalanceable_type(asset_type)
        ):
            continue

        market_value_num = float(a.market_value_cny or 0.0)
        total_qty = float(a.quantity or 0.0)
        # Cost basis on the RAW registry class (top == sub == asset_type),
        # exactly as the pre-engine loop computed it (NOT the engine's
        # taxonomy-resolved cost basis).
        cost_basis_num = calculate_cost_basis_cny(
            market_value=market_value_num,
            quantity=total_qty,
            cost_price_unit=float(a.cost_price_unit or 0.0),
            currency=str(a.currency or "CNY"),
            top_class=asset_type,
            sub_class=asset_type,
            today_fx=today_fx,
        )
        is_cash_equiv = any(keyword in asset_type for keyword in _WEALTHOS_CASH_EQUIV_KEYWORDS)
        is_balance_only = is_balance_only_holding(
            cost_price_unit=a.cost_price_unit,
            has_transactions=a.has_transactions,
        )
        if is_cash_equiv:
            # A cash balance IS its own principal — a genuine zero gain. Checked
            # before is_balance_only: a cash deposit is also cost-less + txn-less,
            # but it is cash, not an unknown-cost investment.
            cost_basis_out: float | None = cost_basis_num
            lifetime_pl_out: float | None = 0.0
            return_pct_out: float | None = 0.0
        elif is_balance_only:
            # Cost unknown, not zero — emit null so the P&L reads "—" rather than a
            # fabricated gain (missing cost -> 100%) or a fabricated break-even
            # (cost = value -> 0%). Its market value still counts elsewhere.
            cost_basis_out = None
            lifetime_pl_out = None
            return_pct_out = None
        else:
            realized_pl = a.realized_cny
            lifetime_pl = (market_value_num - cost_basis_num) + realized_pl
            denominator = cost_basis_num if cost_basis_num != 0 else abs(realized_pl)
            cost_basis_out = cost_basis_num
            lifetime_pl_out = round(lifetime_pl, 3)
            return_pct_out = round((lifetime_pl / denominator * 100.0) if denominator else 0.0, 2)

        results.append(
            {
                "asset_id": asset_id,
                "name": _resolve_display_name(a.name, display_names.get(asset_id), asset_id),
                "asset_class": asset_type,
                "source_system": a.source_system,
                "market_value": market_value_num,
                "cost_basis": cost_basis_out,
                "total_quantity": total_qty,
                "lifetime_pl": lifetime_pl_out,
                "return_pct": return_pct_out,
            }
        )

    results.sort(key=lambda row: row["market_value"], reverse=True)
    return results
