"""Portfolio rollup from per-asset records (plan §B.1) — pure, no DB.

``summary_totals`` is the one place the KPI aggregates are accumulated, so the
surfaces that apply their own display exclusion re-aggregate through it instead
of each re-deriving the balance-only / non-balanceable rules. Split out of
``engine.py`` because it is pure math over :class:`AssetPnL` while the rest of
the engine orchestrates queries.
"""
from __future__ import annotations

from src.services.portfolio_helpers import is_non_balanceable_class
from src.services.pnl.models import AssetPnL


def summary_totals(
    assets: list[AssetPnL],
    *,
    excluded_ids,
    apply_name_filter: bool,
) -> dict:
    """Aggregate the KPI figures from per-asset records (the summary contract).

    Reproduces the V7.8.3 ``get_performance_summary`` accumulation exactly:

    - ``net_worth`` and ``asset_count`` count every in-scope *current* asset
      (cash, traded AND balance-only — a balance-only value is real money).
    - ``total_cost_basis`` / ``measurable_value`` include only non-balance-only
      assets (a balance-only cost is unknown, never charged in at face value).
    - ``total_unrealized = measurable_value − total_cost_basis`` (value-based,
      not a per-asset sum — preserves the legacy float arithmetic).
    - ``total_realized`` sums the realized map over the current+closed union.

    ``excluded_ids`` drives the non-balanceable display filter: value/cost/count
    exclude an asset in the id-set OR (when ``apply_name_filter``) whose
    resolved class name is non-balanceable; realized excludes the id-set only —
    mirroring the summary's asymmetric exclusion.
    """
    net_worth = 0.0
    total_cost_basis = 0.0
    measurable_value = 0.0
    total_realized = 0.0
    asset_count = 0

    for a in assets:
        excluded_value = a.asset_id in excluded_ids or (
            apply_name_filter
            and (
                is_non_balanceable_class(a.top_class)
                or is_non_balanceable_class(a.sub_class)
            )
        )
        if a.is_current and not excluded_value:
            net_worth += a.market_value_cny
            asset_count += 1
            # Keyed on has_known_cost, NOT `treatment is not balance_only`: an
            # asset whose cost is unknown must never enter the cost/measurable
            # denominators, whatever its classification says (see the property's
            # docstring — this is what keeps a #7 manual-realized-only override
            # from re-booking a whole balance as profit).
            if a.has_known_cost:
                total_cost_basis += a.cost_basis_cny or 0.0
                measurable_value += a.market_value_cny
        if a.asset_id not in excluded_ids:
            total_realized += a.realized_cny

    total_unrealized = measurable_value - total_cost_basis
    total_lifetime = total_unrealized + total_realized
    return_pct = (
        (total_unrealized / total_cost_basis * 100.0)
        if total_cost_basis != 0
        else 0.0
    )
    return {
        "net_worth": net_worth,
        "total_cost_basis": total_cost_basis,
        "measurable_value": measurable_value,
        "total_unrealized": total_unrealized,
        "total_realized": total_realized,
        "total_lifetime": total_lifetime,
        "asset_count": asset_count,
        "return_pct": return_pct,
    }
