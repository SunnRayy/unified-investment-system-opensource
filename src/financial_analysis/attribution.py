"""Brinson performance attribution model.

Decomposes portfolio excess return into:
- Allocation effect: did we overweight the right classes?
- Selection effect: did we pick good assets within each class?
- Interaction effect: cross-term (overweight in outperforming class)

Reference: Brinson, Hood, Beebower (1986)
"""
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from src.services.currency import (
    calculate_cost_basis_cny,
    get_today_usd_cny_rate,
    is_balance_only_holding,
    is_cash_equivalent_asset,
)

logger = logging.getLogger(__name__)

# Keep these values aligned with cash-equivalent semantics in
# src/services/currency.py (CASH_CLASS_DISPLAY_VALUES).
ATTRIBUTION_CASH_CLASS_SQL = "'Cash', 'Cash Checking', 'Cash Deposit'"
ATTRIBUTION_CASH_EQUIV_SUBCLASS_SQL = "'Bank Wealth'"


def brinson_attribution(
    portfolio: List[Dict[str, Any]],
    benchmark: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute Brinson attribution given portfolio and benchmark class-level data.

    Args:
        portfolio: List of {"class": str, "weight": float, "return": float}
        benchmark: List of {"class": str, "weight": float, "return": float}

    Returns:
        Dict with total effects, per-class breakdown, and portfolio/benchmark returns.
    """
    p_map = {item["class"]: item for item in portfolio}
    b_map = {item["class"]: item for item in benchmark}
    all_classes = sorted(set(p_map.keys()) | set(b_map.keys()))

    # Benchmark total return
    r_b_total = sum(b["weight"] * b["return"] for b in benchmark)
    r_p_total = sum(p["weight"] * p["return"] for p in portfolio)

    classes = []
    total_alloc = 0.0
    total_select = 0.0
    total_interact = 0.0

    for cls in all_classes:
        w_p = p_map.get(cls, {}).get("weight", 0.0)
        w_b = b_map.get(cls, {}).get("weight", 0.0)
        r_p = p_map.get(cls, {}).get("return", 0.0)
        r_b = b_map.get(cls, {}).get("return", 0.0)

        alloc = (w_p - w_b) * (r_b - r_b_total)
        select = w_b * (r_p - r_b)
        interact = (w_p - w_b) * (r_p - r_b)

        total_alloc += alloc
        total_select += select
        total_interact += interact

        classes.append({
            "class": cls,
            "portfolio_weight": round(w_p, 4),
            "benchmark_weight": round(w_b, 4),
            "portfolio_return": round(r_p, 4),
            "benchmark_return": round(r_b, 4),
            "allocation_effect": round(alloc, 6),
            "selection_effect": round(select, 6),
            "interaction_effect": round(interact, 6),
            "total_effect": round(alloc + select + interact, 6),
        })

    return {
        "portfolio_return": round(r_p_total, 6),
        "benchmark_return": round(r_b_total, 6),
        "excess_return": round(r_p_total - r_b_total, 6),
        "total_allocation_effect": round(total_alloc, 6),
        "total_selection_effect": round(total_select, 6),
        "total_interaction_effect": round(total_interact, 6),
        "classes": classes,
    }


def calculate_portfolio_attribution(
    db: Any,
    include_asset_ids: Optional[list] = None,
) -> Optional[Dict[str, Any]]:
    """Calculate Brinson attribution for the portfolio using live DB data.

    Portfolio weights/returns come from current holdings grouped by top-class.
    Benchmark weights come from active risk profile target allocations.
    Benchmark returns use portfolio class returns (pure allocation attribution).

    Args:
        db: DatabaseConnector
        include_asset_ids: If provided, only include these asset IDs

    Returns:
        Attribution result dict, or None on failure.
    """
    try:
        # 1. Portfolio: current weights and returns by top-class
        asset_filter = ""
        filter_params: list = []
        if include_asset_ids is not None and len(include_asset_ids) > 0:
            placeholders = ", ".join(["?"] * len(include_asset_ids))
            asset_filter = f"AND h.asset_id IN ({placeholders})"
            filter_params = list(include_asset_ids)
        elif include_asset_ids is not None:
            return None  # empty list = no assets

        # Return per-asset raw fields so FX conversion can be applied in Python.
        # cost_price_unit is stored in the asset's native currency (USD for Schwab/RSU,
        # CNY for everything else). market_value is always CNY.  We must NOT compute
        # cost_basis in SQL because that would mix USD cost vs CNY market_value.
        holdings_query = f"""
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) AS latest_date
                FROM holdings WHERE is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT
                COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified') AS top_class,
                COALESCE(tc.name, r.asset_class, 'Unclassified') AS sub_class,
                SUM(h.market_value) AS market_value,
                SUM(h.quantity) AS quantity,
                MAX(h.cost_price_unit) AS cost_price_unit,
                MAX(h.currency) AS currency,
                h.asset_id AS asset_id
            FROM holdings h
            JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
            LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
            LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
            WHERE h.is_shadow = FALSE {asset_filter}
            GROUP BY
                COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified'),
                COALESCE(tc.name, r.asset_class, 'Unclassified'),
                h.asset_id
        """
        rows = db.execute(holdings_query, filter_params or None).fetchall()
        if not rows:
            return None

        # Get FX rate once — used for USD cost_price_unit → CNY conversion.
        today_fx = get_today_usd_cny_rate()
        try:
            txn_asset_ids = {
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT asset_id FROM transactions WHERE asset_id IS NOT NULL"
                ).fetchall()
                if r and r[0]
            }
        except Exception:
            txn_asset_ids = set()

        # Aggregate market_value and cost_basis by top_class.
        # cost_basis is computed per-asset using FX-aware helper (mirrors context_generator.py).
        class_mv: dict = defaultdict(float)
        class_cost: dict = defaultdict(float)
        for row in rows:
            top_cls = row[0]
            sub_cls = str(row[1] or "")
            mv = float(row[2] or 0)
            qty = float(row[3] or 0)
            cpu = float(row[4] or 0)
            currency = str(row[5] or "CNY")
            aid = row[6] if len(row) > 6 else None
            # Balance-only assets (unknown cost) are charged in at market value so
            # they contribute ZERO to the class gain (mv - cost) rather than booking
            # the whole balance as return — see is_balance_only_holding.
            if not is_cash_equivalent_asset(top_cls, sub_cls) and is_balance_only_holding(
                cost_price_unit=cpu, has_transactions=str(aid) in txn_asset_ids,
            ):
                cost_cny = mv
            else:
                cost_cny = calculate_cost_basis_cny(
                    market_value=mv,
                    quantity=qty,
                    cost_price_unit=cpu,
                    currency=currency,
                    top_class=top_cls,
                    sub_class=sub_cls,
                    today_fx=today_fx,
                )
            class_mv[top_cls] += mv
            class_cost[top_cls] += cost_cny

        total_mv = sum(class_mv.values())
        if total_mv == 0:
            return None

        portfolio = []
        for cls in class_mv:
            mv = class_mv[cls]
            cost = class_cost[cls]
            weight = mv / total_mv
            ret = (mv - cost) / cost if cost > 0 else 0.0
            portfolio.append({"class": cls, "weight": weight, "return": ret})

        # 2. Benchmark: target allocations from active risk profile
        try:
            bench_rows = db.execute(
                """
                SELECT
                    COALESCE(parent_tc.name, tc.name) AS top_class,
                    SUM(rpa.target_pct) AS total_target_pct
                FROM risk_profile_allocations rpa
                JOIN risk_profiles rp ON rpa.profile_id = rp.id
                JOIN taxonomy_classes tc ON rpa.class_id = tc.id
                LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
                WHERE rp.is_active = TRUE
                GROUP BY COALESCE(parent_tc.name, tc.name)
                """
            ).fetchall()
        except Exception:
            bench_rows = []

        if bench_rows:
            # Use target allocations as benchmark weights, portfolio returns as benchmark returns
            # This measures pure allocation effect (over/underweight vs target).
            # By design benchmark class returns are set to the portfolio class return,
            # so selection effect is expected to be near zero.
            p_return_map = {p["class"]: p["return"] for p in portfolio}
            benchmark = []
            for row in bench_rows:
                cls = row[0]
                target = float(row[1] or 0) / 100.0  # stored as percentage
                # Use portfolio's own class return as benchmark return
                ret = p_return_map.get(cls, 0.0)
                benchmark.append({"class": cls, "weight": target, "return": ret})

            portfolio_classes = {p["class"] for p in portfolio}
            benchmark_classes = {b["class"] for b in benchmark}
            if not (portfolio_classes & benchmark_classes):
                logger.warning(
                    "Attribution benchmark/portfolio empty class intersection; portfolio=%s benchmark=%s",
                    sorted(portfolio_classes),
                    sorted(benchmark_classes),
                )
        else:
            # Fallback: equal-weight benchmark
            n = len(portfolio)
            benchmark = [
                {"class": p["class"], "weight": 1.0 / n, "return": p["return"]}
                for p in portfolio
            ]

        return brinson_attribution(portfolio, benchmark)

    except Exception as e:
        logger.error(f"Error calculating portfolio attribution: {e}")
        return None
