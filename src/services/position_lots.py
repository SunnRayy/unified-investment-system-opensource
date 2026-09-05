"""FIFO lot-granular view of open positions (Fix 1, 2026-07-10 fix-request).

This module is the *lot-granular* complement to holdings.cost_price_unit.
holdings.cost_price_unit is ALSO a FIFO remaining-cost basis (the Huinsight FIFO
calculator sets it at sync time); the two will agree for most assets.  The one
documented discrepancy for 110020 (+448.77-unit qty gap, likely dividend-reinvest
units that have no matching transaction rows) is documented in
docs/reports/2026-07-10-110020-cost-basis-reconciliation.md.

The critical shared function is ``unrealized_from_holdings_row``: a **pure
function** on holdings-row scalars that both value_trap.scan_value_traps and
(via the /wealthos/assets endpoint) the WealthOS holdings table use.  No
duplicate formulas.

Precision note (see AGENTS.md / V62 incident): all quantity comparisons use
a 1e-6 tolerance, never exact equality on Decimals.  Decimal→float casts are
explicit.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_TOL = 1e-6  # quantity equality tolerance


# ─────────────────────────────────────────────────────────────────────────────
# FIFO lot replay
# ─────────────────────────────────────────────────────────────────────────────

_BUY_TYPES = frozenset(
    ("buy", "vest", "rsu_vest", "transfer_in", "premium_payment", "adjustment_buy")
)
_SELL_TYPES = frozenset(("sell", "adjustment_sell"))


def _paired_transfer_in_ids(db, asset_id: str) -> set:
    """Transaction ids of ``transfer_in`` rows for *asset_id* that have a
    same-asset ``transfer_out`` counterpart (ACAT pair).

    Bug fixed here (2026-07-19, verified live: VOO 42 vs 21 held, IEF 344 vs
    172, SGOV 753.07 vs 553.07): ``_BUY_TYPES`` includes ``transfer_in`` (adds
    a lot) but ``transfer_out`` was never a sell type, so a broker-to-broker
    ACAT transfer added a *second* lot on top of the original buy lot that
    was never consumed — double-counting the position.

    ``src/financial_analysis/cost_basis.py`` (the FIFO calculator that sets
    ``holdings.cost_price_unit``) sidesteps this entirely by treating *both*
    transfer legs as a no-op ("ACAT/security transfer is NON-REALIZING: lots
    persist (cost basis carries across brokers)") — it never adds a lot for
    transfer_in at all. This module can't just copy that: unpaired
    transfer_in rows are real acquisitions with no matching same-asset sell
    anywhere (CN-fund 超级转换 conversions redeem one fund and buy a
    *different* asset_id, so the in-leg's counterpart is a same-day
    transfer_out on the OTHER asset, never this one) and must still create a
    lot for the units received.

    So the fix is pair-aware, not a blanket exclusion: only a transfer_in
    that has a *matching* transfer_out on the SAME asset_id (the ACAT
    round-trip — a broker-transfer copy of a position, not a conversion) is
    excluded, because the original buy lot on the source broker already
    represents that cost basis and persists. Matching mirrors
    ``north_star_flows.py``'s R0 ``security_transfer_pair`` heuristic:
    |qty| within 1e-6, |amount_net| < 0.005 on both legs (ACAT legs are
    always ~$0), within a 7-day window either direction (source-lag between
    brokers' report dates, not necessarily same-day).
    """
    rows = db.execute(
        """
        SELECT id, transaction_date, transaction_type, quantity, amount_net
        FROM transactions
        WHERE asset_id = ?
          AND LOWER(transaction_type) IN ('transfer_in', 'transfer_out')
          AND ABS(COALESCE(quantity, 0)) > 0.0001
          AND ABS(COALESCE(amount_net, 0)) < 0.005
        ORDER BY transaction_date ASC, id ASC
        """,
        [asset_id],
    ).fetchall()

    legs_in: list[dict] = []
    legs_out: list[dict] = []
    for tx_id, tx_date, tx_type, qty_raw, _amount in rows:
        leg = {"id": tx_id, "date": tx_date, "qty": abs(float(qty_raw or 0.0))}
        (legs_in if (tx_type or "").lower() == "transfer_in" else legs_out).append(leg)

    paired_in_ids: set = set()
    used_out: set = set()
    for leg_in in legs_in:
        if leg_in["date"] is None:
            continue
        match = next(
            (
                o for o in legs_out
                if o["id"] not in used_out
                and o["date"] is not None
                and abs(o["qty"] - leg_in["qty"]) <= _TOL
                and abs((o["date"] - leg_in["date"]).days) <= 7
            ),
            None,
        )
        if match is not None:
            used_out.add(match["id"])
            paired_in_ids.add(leg_in["id"])

    return paired_in_ids


def open_lots(db, asset_id: str) -> list[dict]:
    """FIFO replay of transactions for *asset_id*.

    Returns a list of open lot dicts: {"date": str, "quantity": float,
    "price_unit": float}, newest purchases at the end (FIFO: consumed from
    the front).  Lots with effectively-zero remaining quantity (< 1e-6 units)
    are dropped before returning.

    Guards:
    - Zero/negative quantity rows are skipped.
    - Sell quantities that exceed remaining lots are consumed without error
      (over-sold edge case from incomplete transaction history).
    - ACAT-paired transfer_in rows are excluded from lot creation — see
      ``_paired_transfer_in_ids`` for why (double-count fix). Unpaired
      transfer_in (e.g. CN-fund 超级转换 conversions) still adds a lot.
      transfer_out is, and remains, a no-op (never in ``_SELL_TYPES``).
    """
    excluded_transfer_in_ids = _paired_transfer_in_ids(db, asset_id)

    rows = db.execute(
        """
        SELECT id, transaction_date, transaction_type, quantity, price_unit
        FROM transactions
        WHERE asset_id = ?
          AND LOWER(transaction_type) IN (
              'buy', 'vest', 'rsu_vest', 'transfer_in', 'premium_payment',
              'adjustment_buy', 'sell', 'adjustment_sell'
          )
        ORDER BY transaction_date ASC, id ASC
        """,
        [asset_id],
    ).fetchall()

    lots: list[dict] = []
    for tx_id, tx_date, tx_type, qty_raw, price_raw in rows:
        qty = float(qty_raw or 0.0)
        price = float(price_raw or 0.0)
        tx_lower = (tx_type or "").lower()

        if qty <= _TOL:
            continue  # zero / negative — skip

        if tx_lower in _BUY_TYPES:
            if tx_lower == "transfer_in" and tx_id in excluded_transfer_in_ids:
                # ACAT round-trip pair — the original buy lot on the source
                # broker already exists and persists; adding a second lot
                # here would double-count the position (see
                # _paired_transfer_in_ids docstring).
                continue
            lots.append({"date": str(tx_date), "quantity": qty, "price_unit": price})

        elif tx_lower in _SELL_TYPES:
            remaining = qty
            while remaining > _TOL and lots:
                lot = lots[0]
                if lot["quantity"] <= remaining + _TOL:
                    remaining -= lot["quantity"]
                    lots.pop(0)
                else:
                    lot["quantity"] -= remaining
                    remaining = 0.0

    # Drop sub-tolerance residuals (floating-point dust)
    return [lot for lot in lots if lot["quantity"] > _TOL]


def current_lot_cost(db, asset_id: str) -> Optional[dict]:
    """Weighted average cost of FIFO open lots.

    Returns {"avg_cost": float, "open_qty": float, "lots": [...]} or None
    when there are no open lots or the total open quantity is effectively zero.
    """
    lots = open_lots(db, asset_id)
    if not lots:
        return None
    total_qty = sum(lot["quantity"] for lot in lots)
    if total_qty <= _TOL:
        return None
    avg_cost = sum(lot["quantity"] * lot["price_unit"] for lot in lots) / total_qty
    return {
        "avg_cost": float(avg_cost),
        "open_qty": float(total_qty),
        "lots": lots,
    }


def unrealized_return_current_lots(
    db, asset_id: str, current_price: float
) -> Optional[float]:
    """(current_price − avg_lot_cost) / avg_lot_cost × 100 from FIFO replay.

    Returns None when there are no open lots or avg_cost <= 0.
    For CNY-denominated assets this equals the holdings-row computation
    (avg_cost == holdings.cost_price_unit) barring dividend-reinvest gaps;
    see docs/reports/2026-07-10-110020-cost-basis-reconciliation.md.
    """
    info = current_lot_cost(db, asset_id)
    if info is None or info["avg_cost"] <= 0:
        return None
    return (current_price - info["avg_cost"]) / info["avg_cost"] * 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Shared pure function — SINGLE unrealized-return formula for the whole app
# ─────────────────────────────────────────────────────────────────────────────

def unrealized_from_holdings_row(
    *,
    market_value: float,
    quantity: float,
    cost_price_unit: float,
    market_price_unit: float,
    currency: str,
    top_class: str,
    sub_class: str,
    today_fx: float,
) -> Optional[float]:
    """Unrealized return % for one holdings row — the **single** formula used
    by both ``scan_value_traps`` (value_trap.py) and ``/wealthos/assets``
    (data.py).  No other code should duplicate this math.

    Returns (unrealized_cny / cost_basis_cny) × 100, or None when
    cost_basis_cny <= 0 (cash rows, RSU-zero-cost rows, or assets with no
    cost data — must never produce a fake −100%).

    holdings.cost_price_unit is the FIFO-remaining weighted average cost per
    unit (set by the Huinsight FIFO calculator at sync time), which IS the current-lot
    basis.  So this function computes current-lot unrealized return even though
    it reads holdings scalars rather than replaying lot history — the two are
    equivalent when all transaction history is present.
    """
    # Lazy imports: both functions live in routes/services modules that may
    # import from services at module load; lazy import avoids circular imports.
    from src.services.currency import calculate_cost_basis_cny
    from src.api.routes.performance import calculate_unrealized_pl_values

    cost_basis_cny = calculate_cost_basis_cny(
        market_value=float(market_value or 0.0),
        quantity=float(quantity or 0.0),
        cost_price_unit=float(cost_price_unit or 0.0),
        currency=str(currency or "CNY"),
        top_class=str(top_class or ""),
        sub_class=str(sub_class or ""),
        today_fx=float(today_fx),
    )
    if cost_basis_cny <= 0:
        return None

    unrealized_cny, _ = calculate_unrealized_pl_values(
        market_value=float(market_value or 0.0),
        quantity=float(quantity or 0.0),
        cost_price_unit=float(cost_price_unit or 0.0),
        market_price_unit=float(market_price_unit or 0.0),
        currency=str(currency or "CNY"),
        top_class=str(top_class or ""),
        sub_class=str(sub_class or ""),
        today_fx=float(today_fx),
    )
    return (unrealized_cny / cost_basis_cny) * 100.0
