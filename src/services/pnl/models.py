"""Canonical P&L data model (plan 2026-08-02 §B.1).

``AssetPnL`` is the single per-asset record every reporting surface formats
from; ``PortfolioPnL`` carries the asset list plus the aggregates computed once.
``Treatment`` names the three base asset treatments plus the (dormant until #7)
``manual`` override; ``Scope`` selects which slice of the portfolio to compute.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class Treatment(str, Enum):
    """How an asset's P&L is derived.

    - ``cash``:        cash-equivalent (NAV≈1.0) — cost = value, unrealized = 0.
    - ``traded``:      has a cost basis / transactions — real FIFO cost + P&L.
    - ``balance_only``: a reported balance with no cost and no transactions —
      cost/unrealized are *unknown* (``None``), excluded from gain aggregates.
    - ``manual``:      an owner-entered override applied (#7; dormant in #8).
    """

    cash = "cash"
    traded = "traded"
    balance_only = "balance_only"
    manual = "manual"


@dataclass(frozen=True)
class Scope:
    """Which slice of the portfolio ``compute_portfolio_pnl`` computes.

    ``start_date is None`` selects ``mode="current"`` (all-time): per-asset
    latest snapshot with ``is_shadow=FALSE``. A non-null ``start_date`` selects
    ``mode="period"``: candidate snapshots constrained ``>= start_date`` (the
    real behavioral difference Performance's period windows rely on).

    ``today_fx`` overrides the USD→CNY rate (deterministic tests); when ``None``
    the engine resolves today's rate exactly as the legacy surfaces did.
    """

    start_date: Optional[str] = None
    today_fx: Optional[float] = None
    # WealthOS-surface snapshot refinements (default off — the performance
    # surfaces do not use them, so their behavior is unchanged):
    #   positive_positions_only   — HAVING SUM(mv)>0 AND SUM(qty)>0 on active rows
    #   drop_sold_after_snapshot  — remove reader positions fully sold *after* their
    #                               last snapshot (the ETHA case, source-restricted)
    #   with_transaction_provenance — populate first_acquired, source_system, and
    #                               closed-asset invested/name/type/currency from
    #                               the transaction ledger (WealthOS closed rows)
    positive_positions_only: bool = False
    drop_sold_after_snapshot: bool = False
    with_transaction_provenance: bool = False
    # Join taxonomy_classes to resolve top/sub display classes. The performance
    # surfaces need it; WealthOS uses only the raw registry class, so it turns
    # this OFF — its snapshot must not require a taxonomy_classes table to exist
    # (matches the legacy WealthOS query, which joined only asset_registry).
    resolve_taxonomy: bool = True

    @property
    def mode(self) -> str:
        return "period" if self.start_date else "current"


@dataclass
class ManualPnL:
    """One owner-entered override row (#7). Dormant until the table exists."""

    asset_id: str
    cost_basis_cny: Optional[float] = None
    realized_pnl_cny: Optional[float] = None
    as_of_date: Optional[date] = None
    memo: Optional[str] = None


@dataclass
class AssetPnL:
    """The canonical per-asset P&L record (plan §B.1).

    Null contract: ``cost_basis_cny``/``unrealized_cny``/``return_pct`` are
    ``None`` for ``balance_only`` (cost unknown) and for a future
    manual-realized-only override; ``realized_cny`` is *always* a number
    (0 when none). ``lifetime_cny`` is ``None`` only when nothing is measurable
    at all (a pure balance-only asset with no realized amount).
    """

    asset_id: str
    name: Optional[str]
    top_class: str
    sub_class: str
    source_system: Optional[str]
    currency: str
    market_value_cny: float
    treatment: Treatment
    cost_basis_cny: Optional[float]
    unrealized_cny: Optional[float]
    realized_cny: float
    lifetime_cny: Optional[float]
    return_pct: Optional[float]
    unrealized_current_lots_pct: Optional[float]
    first_acquired: Optional[date]
    has_manual_data: bool
    # Engine scope flag: True for a current active holding, False for a
    # closed / transaction-only asset (realized P&L only, no market value).
    is_current: bool = True
    # Raw snapshot inputs + provenance, carried so surfaces with their own
    # display/treatment conventions (WealthOS) can format without re-querying.
    asset_class_registry: Optional[str] = None   # raw MAX(r.asset_class), no display map
    quantity: float = 0.0
    cost_price_unit: Optional[float] = None
    market_price_unit: float = 0.0
    realized_native: float = 0.0
    realized_currency: str = "CNY"
    has_transactions: bool = False
    # Closed-asset invested basis from the transaction ledger (native currency):
    # SUM(quantity * price_unit) over buy-type rows. None for active assets.
    invested_native: Optional[float] = None
    # #7: may the owner log P&L here? True when no authoritative reader ledger
    # feeds this asset, so an override would actually be honoured rather than
    # superseded. Resolved by the engine, never re-derived by a surface — a UI
    # guessing from "does it show —?" misses the assets that show a fake 0.00.
    can_log_manual_pnl: bool = False

    @property
    def has_known_cost(self) -> bool:
        """Is this asset's cost basis known, and therefore chargeable to a
        cost/return denominator?

        Every surface that asks "may I put this asset in a cost or return
        denominator?" MUST ask *this*, never ``treatment is not
        Treatment.balance_only``. The two agree today — ``cost_basis_cny`` is
        ``None`` exactly for ``balance_only`` — but they answer different
        questions, and conflating them is what re-opens the V7.8.3 phantom:

        - ``treatment`` is a *classification* (how the asset was categorised,
          incl. ``manual`` once #7 overlays an owner-entered figure);
        - ``has_known_cost`` is the *math precondition*.

        A manual-realized-only override (#7 rule 4 — owner logs profit but no
        cost) sets ``treatment = manual`` while cost stays unknown. A surface
        keyed on the enum would then see "not balance_only", charge the asset
        in at cost 0, and book its entire market value as profit — exactly the
        ¥386K Fixed-Income phantom V7.8.3 fixed. Keyed on this property, the
        asset stays out of the denominators and its realized amount still
        flows through the separate realized channel.
        """
        return self.cost_basis_cny is not None


@dataclass
class PortfolioPnL:
    """Asset-level records plus the aggregates computed once (plan §B.1).

    The aggregates here are the *unfiltered* (no non-balanceable exclusion)
    totals with the V7.8.3 balance-only rule baked in: a balance-only asset's
    value counts in ``net_worth`` but its (unknown) cost is excluded from the
    gain numerator/denominator. Surfaces that apply their own display exclusion
    re-aggregate from ``assets`` via the engine's shared totals helper.
    """

    scope: Scope
    today_fx: float
    snapshot_date: Optional[str]
    assets: list[AssetPnL] = field(default_factory=list)
    net_worth: float = 0.0
    total_cost_basis: float = 0.0
    # Market value of assets whose cost is known (the gain %'s denominator base);
    # excludes balance-only value so it never dilutes the return.
    measurable_value: float = 0.0
    total_unrealized: float = 0.0
    total_realized: float = 0.0
    total_lifetime: float = 0.0
    asset_count: int = 0
    return_pct: float = 0.0
