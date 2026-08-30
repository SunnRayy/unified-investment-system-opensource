import logging
from collections import defaultdict
from typing import Optional
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query
from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.api.routes._errors import api_error_response
from src.services.currency import (
    is_cash_equivalent_asset,
    is_balance_only_holding,
    # Re-exported for backward-compat: the engine now owns the FX + cost-basis
    # math, but the parity tests monkeypatch ``perf.get_today_usd_cny_rate`` and
    # external callers still import these names from this module.
    get_today_usd_cny_rate,  # noqa: F401
    calculate_cost_basis_cny,  # noqa: F401
)
# Pass G 4c-followup: display/classification/PnL helpers extracted to services layer.
# fetch_included_asset_ids and calculate_realized_pnl are also re-exported here for
# backward-compat — external callers (analytics.py, data.py, tests) still import them
# from this module.
from src.services.portfolio_helpers import (
    get_display_name,
    resolve_top_class,
    is_non_balanceable_class,
    fetch_non_balanceable_asset_ids,
    fetch_included_asset_ids,
    calculate_realized_pnl,  # noqa: F401 — re-export for analytics.py/data.py/tests
)
from src.validation.reader_validator import extract_symbol
from src.services.taxonomy_display import get_class_name_cn_map
# The pure unrealized-P&L helper was relocated to the service layer
# (src/services/pnl/pnl_math.py) so the engine never imports up from this route.
# It is re-exported here unchanged for backward-compat: data.py, position_lots.py,
# value_trap.py and tests still import calculate_unrealized_pl_values from this module.
from src.services.pnl.pnl_math import calculate_unrealized_pl_values  # noqa: F401
from src.services.pnl import Scope, compute_portfolio_pnl, summary_totals

router = APIRouter(tags=["Performance"])
logger = logging.getLogger(__name__)

# ===========================================================================
# P&L CALCULATION METHOD (read before modifying)
# ===========================================================================
# All P&L is computed in the native currency of each asset:
#   USD assets (Schwab_CSV, RSU_Excel): cost_price_unit and market_price_unit in USD
#   CNY assets (CN Fund, Gold, etc.):  all values in CNY
#
# FX conversion to CNY for display uses TODAY'S rate (constant-FX method).
# NOT the historical rate at each transaction date. This ensures:
#   1. A stable USD asset (e.g. SGOV) never shows a loss due to USD/CNY moves
#   2. XIRR reflects real investment return, not currency timing luck
#
# holdings.market_value is ALWAYS in CNY. Do NOT use
# (market_value - cost_price_unit * quantity) for USD assets — it mixes currencies.
# Use (market_price_unit - cost_price_unit) * quantity * today_fx for USD assets.
#
# Wire transfers (CNY->USD to fund Schwab) are not tracked as transactions.
# They appear only as USD cash balance changes in Schwab holdings.
# ===========================================================================

LATEST_SNAPSHOT_CTE = """
    WITH latest_per_asset AS (
        SELECT asset_id, MAX(snapshot_date) as latest_date
        FROM holdings WHERE is_shadow = FALSE
        GROUP BY asset_id
    )
"""

# SQL sub-class lists used in portfolio queries
CASH_CLASS_SQL_LIST = "'Cash', 'Cash Checking', 'Cash Deposit'"
# Bank Wealth behaves like a cash equivalent (~1% annualized return, no cost basis tracking).
CASH_EQUIV_SUBCLASS_SQL_LIST = "'Bank Wealth'"

PERIOD_ALL_TIME = "all_time"
PERIOD_LAST_36M = "last_36m"
PERIOD_LAST_12M = "last_12m"
PERIOD_LAST_1M = "last_1m"


def normalize_period(period: str) -> str:
    period_key = (period or PERIOD_ALL_TIME).strip().lower()
    aliases = {
        "all": PERIOD_ALL_TIME,
        "all_time": PERIOD_ALL_TIME,
        "36m": PERIOD_LAST_36M,
        "last_36m": PERIOD_LAST_36M,
        "12m": PERIOD_LAST_12M,
        "last_12m": PERIOD_LAST_12M,
        "1m": PERIOD_LAST_1M,
        "last_1m": PERIOD_LAST_1M,
        "30d": PERIOD_LAST_1M,
    }
    return aliases.get(period_key, PERIOD_ALL_TIME)


def period_start_date(period: str) -> Optional[str]:
    normalized = normalize_period(period)
    today = date.today()
    if normalized == PERIOD_LAST_36M:
        return (today - timedelta(days=365 * 3)).isoformat()
    if normalized == PERIOD_LAST_12M:
        return (today - timedelta(days=365)).isoformat()
    if normalized == PERIOD_LAST_1M:
        return (today - timedelta(days=30)).isoformat()
    return None


def latest_snapshot_cte(start_date: Optional[str]) -> tuple[str, list]:
    if start_date:
        return (
            """
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) as latest_date
                FROM holdings
                WHERE is_shadow = FALSE
                  AND snapshot_date >= ?
                GROUP BY asset_id
            )
            """,
            [start_date],
        )
    return (LATEST_SNAPSHOT_CTE, [])


def _assets_with_transactions(db) -> set[str]:
    """asset_ids carrying at least one transaction — see is_balance_only_holding.

    A holding absent from this set AND without a cost basis is a reported balance
    (e.g. a Financial-Summary bond column) whose lifetime P&L is unknown. Every
    P&L surface must exclude such assets from its gain figures rather than book
    their whole market value as profit.
    """
    try:
        return {
            str(row[0])
            for row in db.execute(
                "SELECT DISTINCT asset_id FROM transactions WHERE asset_id IS NOT NULL"
            ).fetchall()
            if row and row[0]
        }
    except Exception:
        return set()


def _is_balance_only(*, top_class: str, sub_class: str, cost_price_unit, aid: str, txn_ids: set) -> bool:
    """A NON-cash holding with unknown cost (no cost basis, no transactions).

    Cash is excluded because a cash balance is its own principal (a real zero
    gain, handled by calculate_cost_basis_cny). Only balances like the FS bond
    columns land here — cost genuinely unknown, must not report a gain.
    """
    if is_cash_equivalent_asset(top_class, sub_class):
        return False
    return is_balance_only_holding(
        cost_price_unit=cost_price_unit,
        has_transactions=str(aid) in txn_ids,
    )


@router.get("/performance/summary")
async def get_performance_summary(
    period: str = Query(default=PERIOD_ALL_TIME),
    exclude_non_balanceable: bool = Query(default=False),
    include_non_rebalanceable: Optional[bool] = Query(default=None),
    db: DatabaseConnector = Depends(get_db),
):
    """Get Key Performance Indicators for the portfolio.

    Thin formatter over the single P&L engine (``compute_portfolio_pnl``): the
    engine owns the snapshot query, the cash/traded/balance-only treatment and
    the cost/unrealized/realized leaf math; this endpoint only applies the
    non-balanceable display filter and names the response fields. The result is
    byte-identical to the pre-engine V7.8.3 implementation (parity-gated by
    tests/api/test_performance_summary_engine_parity.py).
    """
    if isinstance(include_non_rebalanceable, bool):
        exclude_non_balanceable = not include_non_rebalanceable
    try:
        start_date = period_start_date(period)
        portfolio = compute_portfolio_pnl(db, scope=Scope(start_date=start_date))

        excluded_asset_ids = (
            fetch_non_balanceable_asset_ids(db) if exclude_non_balanceable else frozenset()
        )
        totals = summary_totals(
            portfolio.assets,
            excluded_ids=excluded_asset_ids,
            apply_name_filter=exclude_non_balanceable,
        )

        return {
            "net_worth": totals["net_worth"],
            "total_cost_basis": totals["total_cost_basis"],
            "total_unrealized_pl": totals["total_unrealized"],
            "unrealized_pl_pct": round(totals["return_pct"], 2),
            "total_realized_pl": totals["total_realized"],
            "total_lifetime_pl": totals["total_lifetime"],
            "asset_count": totals["asset_count"],
            "snapshot_date": portfolio.snapshot_date,
        }
    except Exception as e:
        print(f"Error in performance summary: {e}")
        return {
            "net_worth": 0.0,
            "total_cost_basis": 0.0,
            "total_unrealized_pl": 0.0,
            "unrealized_pl_pct": 0.0,
            "total_realized_pl": 0.0,
            "total_lifetime_pl": 0.0,
            "asset_count": 0,
            "snapshot_date": None
        }

@router.get("/performance/gains")
async def get_gains_analysis(
    period: str = Query(default=PERIOD_ALL_TIME),
    exclude_non_balanceable: bool = Query(default=False),
    include_non_rebalanceable: Optional[bool] = Query(default=None),
    db: DatabaseConnector = Depends(get_db),
):
    """Get gains analysis (P&L breakdown) per asset.

    Thin formatter over the single P&L engine (``compute_portfolio_pnl``, period
    mode): the engine owns the snapshot, the cash/traded/balance-only treatment,
    the cost basis and the co-authority-safe realized-P&L map; this endpoint only
    ranks the CURRENT holdings, applies the non-balanceable display filter and
    names the response fields. Byte-identical to the pre-engine V7.8.3 loop
    (parity-gated by tests/api/test_gains_analysis_engine_parity.py).
    """
    if isinstance(include_non_rebalanceable, bool):
        exclude_non_balanceable = not include_non_rebalanceable
    try:
        start_date = period_start_date(period)
        portfolio = compute_portfolio_pnl(db, scope=Scope(start_date=start_date))
        today_fx = portfolio.today_fx
        excluded_asset_ids = (
            fetch_non_balanceable_asset_ids(db) if exclude_non_balanceable else set()
        )
        # Additive _cn companion (Program BIL / WS-9) for a.top_class below.
        name_cn_map = get_class_name_cn_map(db)

        assets = []
        total_unrealized = 0.0
        total_realized = 0.0
        total_cost = 0.0
        total_mv = 0.0

        for a in portfolio.assets:
            if not a.is_current:
                continue  # gains ranks current holdings only (no closed/sold-only)
            aid = a.asset_id
            if exclude_non_balanceable and aid in excluded_asset_ids:
                continue
            # Gains applies the name-based non-balanceable filter on the TOP class
            # only (a documented divergence from the summary, which checks sub too).
            if exclude_non_balanceable and is_non_balanceable_class(a.top_class):
                continue

            mv = a.market_value_cny

            # Assets with an unknown cost have no measurable return — omit them
            # from the ranking rather than let them show up as top "performers"
            # on a fabricated 100% gain. Their market value still counts in the total.
            # Keyed on has_known_cost, not the treatment enum (see AssetPnL).
            if not a.has_known_cost:
                total_mv += mv
                continue

            cost = a.cost_basis_cny  # cash (== value) or traded (FIFO), from the engine
            # Price-based unrealized (+ native) — the gains/value-trap convention
            # (differs from the summary's value-based method for USD assets).
            unrealized, unrealized_native = calculate_unrealized_pl_values(
                market_value=mv,
                quantity=a.quantity,
                cost_price_unit=float(a.cost_price_unit or 0.0),
                market_price_unit=a.market_price_unit,
                currency=a.currency,
                top_class=a.top_class,
                sub_class=a.sub_class,
                today_fx=today_fx,
            )
            realized = a.realized_cny
            realized_native = a.realized_native
            pnl_currency = a.realized_currency
            period_profit = unrealized + realized
            ret_pct = (period_profit / cost * 100) if cost != 0 else 0.0

            assets.append({
                "asset_id": aid,
                "name": a.name,
                "top_class": a.top_class,
                "top_class_cn": name_cn_map.get(a.top_class),
                "currency": a.currency,
                "cost_basis": cost,
                "market_value": mv,
                "unrealized_pl": unrealized,
                "realized_pl": realized,
                "pnl_currency": pnl_currency,
                "unrealized_pl_native": unrealized_native,
                "realized_pl_native": realized_native,
                "period_pl": period_profit,
                "return_pct": round(ret_pct, 2)
            })

            total_unrealized += unrealized
            total_realized += realized
            total_cost += cost
            total_mv += mv

        # Sort by return_pct DESC
        assets.sort(key=lambda x: x["return_pct"], reverse=True)

        # Portfolio totals
        total_lifetime = total_unrealized + total_realized
        port_ret_pct = (total_lifetime / total_cost * 100) if total_cost != 0 else 0.0

        return {
            "total_unrealized_pl": total_unrealized,
            "total_realized_pl": total_realized,
            "total_lifetime_pl": total_lifetime,
            "total_cost_basis": total_cost,
            "total_market_value": total_mv,
            "unrealized_pl_pct": round(port_ret_pct, 2),
            "assets": assets
        }
    except Exception as e:
        print(f"Error in gains analysis: {e}")
        return {
             "total_unrealized_pl": 0.0,
             "total_realized_pl": 0.0,
             "total_lifetime_pl": 0.0,
             "total_cost_basis": 0.0,
             "total_market_value": 0.0,
             "unrealized_pl_pct": 0.0,
             "assets": []
        }

@router.get("/performance/by-class")
async def get_performance_by_class(
    period: str = Query(default=PERIOD_ALL_TIME),
    exclude_non_balanceable: bool = Query(default=False),
    include_non_rebalanceable: Optional[bool] = Query(default=None),
    db: DatabaseConnector = Depends(get_db),
):
    """Get P&L aggregated by top-level class and sub-class, including Realized P&L.

    Thin formatter over the single P&L engine (``compute_portfolio_pnl``): the
    engine owns the snapshot query, the cash/traded/balance-only treatment, the
    cost basis, the co-authority-safe realized-P&L map AND the closed / sold-only
    asset union (with each closed asset's class resolved via the transaction-
    ledger provenance join). This endpoint only aggregates the per-asset records
    into top/sub classes, applies the non-balanceable display filter, recomputes
    filtered weights and names the response fields. Byte-identical to the
    pre-engine V7.8.3 loop (parity-gated by
    tests/api/test_by_class_engine_parity.py).
    """
    if isinstance(include_non_rebalanceable, bool):
        exclude_non_balanceable = not include_non_rebalanceable
    try:
        start_date = period_start_date(period)
        # with_transaction_provenance=True so closed/sold-only assets carry their
        # resolved top/sub class (realized P&L must land in the right class).
        portfolio = compute_portfolio_pnl(
            db,
            scope=Scope(start_date=start_date, with_transaction_provenance=True),
        )
        today_fx = portfolio.today_fx

        # Aggregation Structures
        top_agg = {} # name -> {mv, cost, unrealized, realized, count}
        sub_agg = {} # (top, sub) -> {mv, cost, unrealized, realized, count}

        total_mv = 0.0

        def _bucket(agg, key):
            if key not in agg:
                agg[key] = {"mv": 0.0, "cost": 0.0, "unrealized": 0.0, "realized": 0.0, "count": 0}
            return agg[key]

        for a in portfolio.assets:
            top = a.top_class
            sub = a.sub_class
            realized = a.realized_cny

            if not a.is_current:
                # Closed / sold-only asset: contributes realized P&L only (no
                # market value, no cost, no unrealized, and NOT counted as an
                # active holding) — booked into its resolved class.
                _bucket(top_agg, top)["realized"] += realized
                _bucket(sub_agg, (top, sub))["realized"] += realized
                continue

            mv = a.market_value_cny

            # Unknown-cost assets: their market value belongs to the class
            # value/weight, but they contribute NO cost and NO unrealized P&L —
            # otherwise the missing cost books the whole balance as class profit
            # (the ¥386K Fixed-Income phantom). Keyed on has_known_cost, not the
            # treatment enum (see AssetPnL), so a #7 manual-realized-only override
            # cannot re-open that phantom here.
            if not a.has_known_cost:
                cost = 0.0
                unrealized = 0.0
            else:
                # Cost basis (cash == value; traded == FIFO) straight from the engine.
                cost = a.cost_basis_cny
                # Price-based unrealized (the by-class/gains convention — NOT the
                # summary's value-based method), recomputed from the raw inputs.
                unrealized, _ = calculate_unrealized_pl_values(
                    market_value=mv,
                    quantity=a.quantity,
                    cost_price_unit=float(a.cost_price_unit or 0.0),
                    market_price_unit=a.market_price_unit,
                    currency=a.currency,
                    top_class=top,
                    sub_class=sub,
                    today_fx=today_fx,
                )

            top_bucket = _bucket(top_agg, top)
            top_bucket["mv"] += mv
            top_bucket["cost"] += cost
            top_bucket["unrealized"] += unrealized
            top_bucket["realized"] += realized
            top_bucket["count"] += 1

            sub_bucket = _bucket(sub_agg, (top, sub))
            sub_bucket["mv"] += mv
            sub_bucket["cost"] += cost
            sub_bucket["unrealized"] += unrealized
            sub_bucket["realized"] += realized
            sub_bucket["count"] += 1

            total_mv += mv

        # Format Output
        # Additive _cn companions (Program BIL / WS-9) — top/sub class names here
        # are taxonomy_classes.name values (or 'Unclassified', which has no cn).
        name_cn_map = get_class_name_cn_map(db)
        top_classes = []
        for top, data in top_agg.items():
            cost = data["cost"]
            unrealized = data["unrealized"]
            realized = data["realized"]
            lifetime = unrealized + realized
            
            ret_pct = (lifetime / cost * 100) if cost != 0 else 0.0
            weight_pct = (data["mv"] / total_mv * 100) if total_mv != 0 else 0.0
            
            top_classes.append({
                "class_name": top,
                "class_name_cn": name_cn_map.get(top),
                "market_value": data["mv"],
                "cost_basis": cost,
                "unrealized_pl": unrealized,
                "realized_pl": realized,
                "lifetime_pl": lifetime,
                "return_pct": round(ret_pct, 2),
                "weight_pct": round(weight_pct, 1),
                "asset_count": data["count"]
            })
        
        # Filter out empty Unclassified entries (zero assets, zero value)
        top_classes = [c for c in top_classes if not (c["class_name"] == "Unclassified" and c["asset_count"] == 0)]

        # Sort by Market Value DESC
        top_classes.sort(key=lambda x: x["market_value"], reverse=True)

        sub_classes = []
        for (top, sub), data in sub_agg.items():
            cost = data["cost"]
            unrealized = data["unrealized"]
            realized = data["realized"]
            lifetime = unrealized + realized
             
            ret_pct = (lifetime / cost * 100) if cost != 0 else 0.0
            weight_pct = (data["mv"] / total_mv * 100) if total_mv != 0 else 0.0
            
            sub_classes.append({
                "top_class": top,
                "sub_class": sub,
                "top_class_cn": name_cn_map.get(top),
                "sub_class_cn": name_cn_map.get(sub),
                "market_value": data["mv"],
                "cost_basis": cost,
                "unrealized_pl": unrealized,
                "realized_pl": realized,
                "lifetime_pl": lifetime,
                "return_pct": round(ret_pct, 2),
                "weight_pct": round(weight_pct, 1),
                "asset_count": data["count"]
            })
            
        sub_classes.sort(key=lambda x: x["market_value"], reverse=True)

        if exclude_non_balanceable:
            top_classes = [
                item for item in top_classes
                if not is_non_balanceable_class(item["class_name"])
            ]
            kept_top_classes = {item["class_name"] for item in top_classes}
            sub_classes = [
                item for item in sub_classes
                if item["top_class"] in kept_top_classes
                and not is_non_balanceable_class(item["top_class"])
            ]

            filtered_total_mv = sum(item["market_value"] for item in top_classes)
            for item in top_classes:
                item["weight_pct"] = (
                    round(item["market_value"] / filtered_total_mv * 100, 1)
                    if filtered_total_mv != 0
                    else 0.0
                )
            for item in sub_classes:
                item["weight_pct"] = (
                    round(item["market_value"] / filtered_total_mv * 100, 1)
                    if filtered_total_mv != 0
                    else 0.0
                )
            total_mv = filtered_total_mv

        # Calculate Total Cost Basis (sum of displayed classes)
        total_cost_basis = sum(c["cost_basis"] for c in top_classes)

        return {
            "total_market_value": total_mv,
            "total_cost_basis": total_cost_basis,
            "top_classes": top_classes,
            "sub_classes": sub_classes
        }
    except Exception as e:
        print(f"Error in performance by class: {e}")
        return {
            "total_market_value": 0.0,
            "total_cost_basis": 0.0,
            "top_classes": [],
            "sub_classes": []
        }


from src.financial_analysis.twr import calculate_portfolio_twr
from src.financial_analysis.xirr import calculate_portfolio_xirr
from src.financial_analysis.attribution import calculate_portfolio_attribution

@router.get("/performance/returns")
async def get_performance_returns(
    period: str = Query(default=PERIOD_ALL_TIME),
    exclude_non_balanceable: bool = Query(default=False),
    include_non_rebalanceable: Optional[bool] = Query(default=None),
    db: DatabaseConnector = Depends(get_db),
):
    """Get portfolio return metrics: TWR, MWR (XIRR), and period returns."""
    if isinstance(include_non_rebalanceable, bool):
        exclude_non_balanceable = not include_non_rebalanceable
    try:
        start_date = period_start_date(period)
        include_asset_ids = (
            fetch_included_asset_ids(db, start_date=start_date)
            if exclude_non_balanceable
            else None
        )

        twr_result = calculate_portfolio_twr(
            db,
            start_date=start_date,
            include_asset_ids=include_asset_ids,
            exclude_non_balanceable=exclude_non_balanceable
        )
        twr = twr_result["cumulative"] if twr_result else None
        twr_annualized = twr_result["annualized"] if twr_result else None
        mwr = calculate_portfolio_xirr(
            db,
            start_date=start_date,
            include_asset_ids=include_asset_ids,
        )

        # Period returns (TWR for specific windows)
        today = date.today()
        ytd_start = date(today.year, 1, 1).isoformat()
        one_year_start = (today - timedelta(days=365)).isoformat()

        ytd_filter = max(ytd_start, start_date) if start_date else ytd_start
        one_year_filter = max(one_year_start, start_date) if start_date else one_year_start

        ytd_include_asset_ids = (
            fetch_included_asset_ids(db, start_date=ytd_filter)
            if exclude_non_balanceable
            else None
        )
        one_year_include_asset_ids = (
            fetch_included_asset_ids(db, start_date=one_year_filter)
            if exclude_non_balanceable
            else None
        )

        twr_ytd_result = calculate_portfolio_twr(
            db,
            start_date=ytd_filter,
            include_asset_ids=ytd_include_asset_ids,
            exclude_non_balanceable=exclude_non_balanceable
        )
        twr_ytd = twr_ytd_result["cumulative"] if twr_ytd_result else None

        twr_1y_result = calculate_portfolio_twr(
            db,
            start_date=one_year_filter,
            include_asset_ids=one_year_include_asset_ids,
            exclude_non_balanceable=exclude_non_balanceable
        )
        twr_1y = twr_1y_result["cumulative"] if twr_1y_result else None

        return {
            "twr_cumulative": round(twr * 100, 2) if twr is not None else None,
            "twr_annualized": round(twr_annualized * 100, 2) if twr_annualized is not None else None,
            "twr_ytd": round(twr_ytd * 100, 2) if twr_ytd is not None else None,
            "twr_1y": round(twr_1y * 100, 2) if twr_1y is not None else None,
            "mwr_xirr": round(mwr * 100, 2) if mwr is not None else None,
        }
    except Exception as e:
        print(f"Error in overall returns view: {e}")
        return {"twr_cumulative": None, "twr_annualized": None, "twr_ytd": None, "twr_1y": None, "mwr_xirr": None, "error": str(e)}


@router.get("/performance/attribution")
async def get_performance_attribution(
    period: str = Query(default=PERIOD_ALL_TIME),
    include_non_rebalanceable: Optional[bool] = Query(default=None),
    db: DatabaseConnector = Depends(get_db),
):
    """Get Brinson performance attribution by asset class."""
    try:
        exclude = False
        if isinstance(include_non_rebalanceable, bool):
            exclude = not include_non_rebalanceable
        include_asset_ids = (
            fetch_included_asset_ids(db) if exclude else None
        )
        # TODO: Period-aware attribution is not implemented yet; this endpoint is all-time.
        result = calculate_portfolio_attribution(db, include_asset_ids=include_asset_ids)
        if result is None:
            return {"error": "Insufficient data for attribution analysis"}

        # Convert decimal returns/effects to percentage points for frontend contract.
        # Weights remain in 0-1 range (frontend handles weight percentage formatting).
        pctpt_fields_top = (
            "portfolio_return",
            "benchmark_return",
            "excess_return",
            "total_allocation_effect",
            "total_selection_effect",
            "total_interaction_effect",
        )
        pctpt_fields_class = (
            "portfolio_return",
            "benchmark_return",
            "allocation_effect",
            "selection_effect",
            "interaction_effect",
            "total_effect",
        )
        for field in pctpt_fields_top:
            if field in result and result[field] is not None:
                result[field] = round(float(result[field]) * 100, 4)
        for cls in result.get("classes", []):
            for field in pctpt_fields_class:
                if field in cls and cls[field] is not None:
                    cls[field] = round(float(cls[field]) * 100, 4)
        return result
    except Exception as e:
        logger.exception("attribution failed")
        return api_error_response(e, context="attribution")


from src.financial_analysis.metrics import calculate_portfolio_metrics

@router.get("/performance/risk-metrics")
async def get_risk_metrics(
    period: str = Query(default=PERIOD_ALL_TIME),
    exclude_non_balanceable: bool = Query(default=False),
    include_non_rebalanceable: Optional[bool] = Query(default=None),
    db: DatabaseConnector = Depends(get_db),
):
    """Get historical risk metrics: Sharpe, Sortino, max drawdown, Calmar, volatility."""
    try:
        if isinstance(include_non_rebalanceable, bool):
            exclude_non_balanceable = not include_non_rebalanceable

        include_asset_ids = None
        start_date = period_start_date(period)
        if exclude_non_balanceable:
            include_asset_ids = fetch_included_asset_ids(db, start_date=start_date)

        result = calculate_portfolio_metrics(
            db,
            include_asset_ids=include_asset_ids,
            start_date=start_date,
            exclude_non_balanceable=exclude_non_balanceable,
        )
        return result
    except Exception as e:
        print(f"Error in risk metrics view: {e}")
        return {
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "max_drawdown": None,
            "calmar_ratio": None,
            "volatility_annual": None,
            "total_return": None,
            "data_points": 0,
        }


# ---------------------------------------------------------------------------
# Top Movers — price-ratio method (GitHub #27)
# ---------------------------------------------------------------------------

_WINDOW_DAYS: dict[str, int] = {
    "7d": 7,
    "30d": 30,
    "3m": 91,
    "6m": 182,
    "12m": 365,
}
_VALID_LEVELS = frozenset({"asset", "sub_class", "top_class"})


@router.get("/performance/movers")
async def get_movers(
    window: str = Query(..., description="Lookback window: 7d|30d|3m|6m|12m"),
    level: str = Query(default="asset", description="Aggregation level: asset|sub_class|top_class"),
    limit: int = Query(default=10, ge=1, le=50),
    db: DatabaseConnector = Depends(get_db),
):
    """Get top movers by price-driven P&L impact over a selectable time window.

    Uses price-ratio method (FX-free): mv_now × (1 − p_then/p_now).
    Unpriced assets (no market_daily rows) are excluded and counted.
    """
    # 1 — Validate params (Rule 12 — 422 for bad inputs)
    if window not in _WINDOW_DAYS:
        return api_error_response(
            ValueError(f"invalid window: {window!r}"),
            context="movers",
            status_code=422,
        )
    if level not in _VALID_LEVELS:
        return api_error_response(
            ValueError(f"invalid level: {level!r}"),
            context="movers",
            status_code=422,
        )

    try:
        today = date.today()
        window_days = _WINDOW_DAYS[window]
        window_start = today - timedelta(days=window_days)

        # 2 — Latest non-shadow holdings per asset (per-asset MAX — never global)
        holdings_rows = db.execute(
            """
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) AS latest_date
                FROM holdings
                WHERE is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT
                h.asset_id,
                MAX(h.asset_name)                                                     AS name,
                SUM(h.market_value)                                                   AS market_value,
                COALESCE(MAX(parent_tc.name), MAX(tc.name), MAX(r.asset_class), 'Unclassified') AS top_class,
                COALESCE(MAX(r.asset_class), 'Unclassified')                          AS sub_class
            FROM holdings h
            JOIN latest_per_asset lpa
                ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
            LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
            LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
            WHERE h.is_shadow = FALSE
            GROUP BY h.asset_id
            """
        ).fetchall()

        if not holdings_rows:
            return {
                "window": window,
                "window_start": window_start.isoformat(),
                "level": level,
                "movers": [],
                "excluded_unpriced_count": 0,
            }

        # 3 — Map asset_id → market_daily code via extract_symbol (same helper as price-refresh)
        asset_info = []
        for asset_id, name, mv, top_class_raw, sub_class_raw in holdings_rows:
            if not asset_id:
                continue
            try:
                code = extract_symbol(asset_id)
            except Exception:
                continue
            asset_info.append({
                "asset_id": asset_id,
                "name": name or asset_id,
                "market_value": float(mv or 0.0),
                "top_class": resolve_top_class(top_class_raw or ""),
                "sub_class": get_display_name(sub_class_raw or ""),
                "code": code,
            })

        if not asset_info:
            return {
                "window": window,
                "window_start": window_start.isoformat(),
                "level": level,
                "movers": [],
                "excluded_unpriced_count": 0,
            }

        # 4 — Batch-query market_daily for all codes in one round-trip
        all_codes = list({a["code"] for a in asset_info})
        placeholders = ", ".join(["?"] * len(all_codes))
        price_rows = db.execute(
            f"""
            SELECT code, date, close
            FROM market_daily
            WHERE code IN ({placeholders})
              AND close IS NOT NULL
            ORDER BY code, date
            """,
            all_codes,
        ).fetchall()

        # Build per-code sorted close list: code → [(date, close), ...]
        prices_by_code: dict[str, list] = defaultdict(list)
        for code, dt_val, close_price in price_rows:
            prices_by_code[code].append((dt_val, float(close_price)))
        # Already ordered by (code, date) from SQL ORDER BY

        # 5 — Compute per-asset price-ratio metrics
        priced_assets: list[dict] = []
        excluded_unpriced_count = 0

        for a in asset_info:
            closes = prices_by_code.get(a["code"], [])

            if len(closes) < 2:
                excluded_unpriced_count += 1
                continue

            # p_now = latest close (last element, list is sorted by date asc)
            _, p_now = closes[-1]

            # p_then = latest close on or before window_start
            then_candidates = [(d, p) for d, p in closes if d <= window_start]
            if then_candidates:
                _, p_then = then_candidates[-1]
                window_covered = True
            else:
                # Partial coverage: use earliest available close
                _, p_then = closes[0]
                window_covered = False

            if p_now == 0 or p_then == 0:
                excluded_unpriced_count += 1
                continue

            pct_change = (p_now / p_then - 1.0) * 100.0
            pl_impact_cny = a["market_value"] * (1.0 - p_then / p_now)

            priced_assets.append({
                "key": a["asset_id"],
                "name": a["name"],
                "top_class": a["top_class"],
                "sub_class": a["sub_class"],
                "pct_change": round(pct_change, 4),
                "pl_impact_cny": round(pl_impact_cny, 2),
                "market_value": a["market_value"],
                "window_covered": window_covered,
                "asset_count": 1,
            })

        # 6 — Aggregate by level (sub_class or top_class)
        if level == "asset":
            movers: list[dict] = priced_assets
        else:
            agg: dict[str, dict] = {}
            for asset in priced_assets:
                grp_key = asset["top_class"] if level == "top_class" else asset["sub_class"]
                if grp_key not in agg:
                    agg[grp_key] = {
                        "key": grp_key,
                        "name": grp_key,
                        "top_class": asset["top_class"],
                        "sub_class": grp_key if level == "sub_class" else None,
                        "sum_pl_impact": 0.0,
                        # Σ(mv_now × p_then/p_now) = mv_now − pl_impact for each asset
                        "sum_mv_then": 0.0,
                        "sum_mv_now": 0.0,
                        "window_covered": True,
                        "asset_count": 0,
                    }
                agg[grp_key]["sum_pl_impact"] += asset["pl_impact_cny"]
                agg[grp_key]["sum_mv_then"] += asset["market_value"] - asset["pl_impact_cny"]
                agg[grp_key]["sum_mv_now"] += asset["market_value"]
                agg[grp_key]["window_covered"] = (
                    agg[grp_key]["window_covered"] and asset["window_covered"]
                )
                agg[grp_key]["asset_count"] += 1

            movers = []
            for g in agg.values():
                # pct_change = Σ impact / Σ(mv_now × p_then/p_now) × 100
                sum_mv_then = g["sum_mv_then"]
                pct = (g["sum_pl_impact"] / sum_mv_then * 100.0) if sum_mv_then != 0 else 0.0
                row: dict = {
                    "key": g["key"],
                    "name": g["name"],
                    "pct_change": round(pct, 4),
                    "pl_impact_cny": round(g["sum_pl_impact"], 2),
                    "market_value": round(g["sum_mv_now"], 2),
                    "window_covered": g["window_covered"],
                    "asset_count": g["asset_count"],
                }
                if level == "sub_class":
                    row["top_class"] = g["top_class"]
                    row["sub_class"] = g["sub_class"]
                movers.append(row)

        # 7 — Sort by |pl_impact_cny| DESC, apply limit
        movers.sort(key=lambda x: abs(x["pl_impact_cny"]), reverse=True)
        movers = movers[:limit]

        return {
            "window": window,
            "window_start": window_start.isoformat(),
            "level": level,
            "movers": movers,
            "excluded_unpriced_count": excluded_unpriced_count,
        }

    except Exception as e:
        logger.exception("movers endpoint failed")
        return api_error_response(e, context="movers")
