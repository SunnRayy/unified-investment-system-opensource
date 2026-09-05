"""RSU vested & retained contributions (ADR-025 gap-fill, plan
2026-07-25-cash-flow-classification-completion.md §3.3, §5).

READ-ONLY service — no writes to any table anywhere in this module (SELECT
only). Closes a gap in ADR-025 (`src/services/investment_contributions.py`):
the 月度收支 ledger books RSU vests as INCOME (`收入_主动收入_RSU`), and only
reinvested *cash* from a sale shows up in `投资理财`. RSU shares that vest and
are KEPT (never sold, never converted to a 投资理财 row) are real portfolio
inflow but appear in neither figure. This module derives that missing
quantity directly from `transactions` (source_system='RSU_Excel'), via a
full-history lot replay — never from the FS Excel ledger.

Two public functions:

  rsu_vest_gross_ttm(db, window_start_month, window_end_month) -> float
      Gross USD value of every 'vest' row whose transaction_date falls in the
      window, converted to CNY. A raw inflow figure — NOT netted against
      sells (that would double-subtract money the 月度收支 ledger already
      counts as reinvested when the vested shares are sold and rebought
      elsewhere; see the plan §3.3 worked example).

  rsu_retained_ttm(db, window_start_month, window_end_month) -> dict
      Replays ALL RSU_Excel history (never just the window — a lot
      vested inside the window may have been partially or fully consumed by
      a LATER sell, and a lot vested OUTSIDE the window may still be the one
      currently held) to find which lots are still held today, then sums the
      value of exactly the surviving lots whose vest_date falls inside the
      window. This is the "vested in the window and still held" measure the
      plan's owner-approved resolution (§5.4) settled on — the naive
      `vests - sells` measure double-subtracts proceeds the ledger already
      recorded as reinvested.

Lot rule / sign convention (verified against production data): a 'vest' row
carries a POSITIVE `quantity` (shares received); a 'sell' row carries a
NEGATIVE `quantity` (shares given up) — `config/readers/rsu.yaml`'s
`derive_rsu_holdings` hook relies on this same sign convention
(`net_qty = quantity.sum()`, keep only `net_qty > 0`). A sell of the batch
that just vested consumes THAT vest's lot; every other sell consumes open
lots oldest-first (FIFO). The full rule, its 4-day window and the owner
decision behind it are on `replay_rsu_lots()` — it is the ONE lot rule for
both retained shares and realized gains. Over-selling (more shares sold than are open,
possible in incomplete/edge-case histories, e.g. a bad transaction date
that reorders vest/sell chronology) can never produce a negative-quantity
lot — the excess is absorbed — but it is NOT silent: it is tracked per
asset and logged (`logger.warning`), and surfaced via
`rsu_retained_ttm()["oversold_shares"]` so a caller/UI can make a data
error visible instead of quietly producing a retained figure that is
wrong by the over-sold quantity. A one-character date typo in
`RSU_transactions.xlsx` produced exactly this failure mode (session of
2026-07-25, plan §3.2/§8.5) before this tracking existed.

FX rule — mirrors `src/services/north_star_flows.py::_amount_to_cny`
exactly: currency 'USD' (case-insensitive) is converted at the fetched rate,
anything else is treated as already CNY. `get_today_usd_cny_rate()` is
called ONCE per public function (never per-row/per-lot) and the returned
rate is reused for every row/lot in that call. This means a single CURRENT
FX rate is applied to vests and lots from any historical date — the same
project-wide FX limitation documented in `docs/known-issues.md` §fx-constant
and already accepted by `north_star_flows.py` / `investment_contributions.py`
(no per-date rate history exists in this system).
"""
from __future__ import annotations

import logging
from typing import Optional

from src.services.currency import get_today_usd_cny_rate

logger = logging.getLogger(__name__)

_LOT_TOL = 1e-6
# Price equality tolerance for structural specific-lot detection (a sale at a
# vest's own price). Prices are stored DECIMAL(x,8); 1e-6 is well below any
# real price difference but absorbs float round-trip noise.
_PRICE_TOL = 1e-6

# How many CALENDAR days after a vest a sale may still be that vest's own
# batch being disposed of (see `replay_rsu_lots()`).
#
# Chosen against the real RSU_Excel history, not picked round:
#   0 days — the four same-day sell-to-cover withholdings (2023-09-15,
#            2024-09-15, 2025-03-15, 2025-09-15) and the 2026-06-25 GOOG one.
#   1 day  — the 2026-03-16 full-batch liquidation of the 2026-03-15 vest
#            (a Sunday vest settled by the broker on the Monday).
#   3 days — worst realistic case for "next business day": a Friday vest
#            (2023-09-15 was one; 2024-03-15, 2028-09-15, 2030-03-15 are too)
#            liquidated on the following Monday.
#   4 days — the same Friday case when that Monday is a US market holiday,
#            or a Thursday vest ahead of Good Friday.
#
# Deliberately NOT wider: the point of the window is that a discretionary sale
# weeks or months later which happens to trade at a price equal to some old
# vest price must fall through to ordinary FIFO. 4 days cannot reach that.
# The price predicate (exact to 1e-6 on an 8-decimal price) is the primary
# guard; this window is the secondary one, and it is one-sided — a sale BEFORE
# a vest can never be that vest's batch.
_VEST_MATCH_WINDOW_DAYS = 4


def _is_own_vest_lot(lot: dict, sale_price: float, sale_date) -> bool:
    """True when `lot` is the specific vest batch this sale is disposing of:
    same price within `_PRICE_TOL`, and 0..`_VEST_MATCH_WINDOW_DAYS` calendar
    days after the vest (one-sided). A non-date-like value fails closed —
    the sale falls through to ordinary FIFO rather than matching on price
    alone, which would let any same-priced sale claim the lot.
    """
    if abs(lot["price_unit"] - sale_price) > _PRICE_TOL:
        return False
    try:
        days = (sale_date - lot["vest_date"]).days
    except TypeError:
        return False
    return 0 <= days <= _VEST_MATCH_WINDOW_DAYS


def _amount_to_cny(amount, currency: Optional[str], fx_rate: float) -> float:
    """Convert a native-currency amount to CNY.

    Mirrors src/services/north_star_flows.py::_amount_to_cny: currency 'USD'
    (case-insensitive) is converted at fx_rate; anything else (including
    None/missing) is treated as already CNY.
    """
    if amount is None:
        return 0.0
    amt = float(amount)
    return amt * fx_rate if (currency or "CNY").upper() == "USD" else amt


def _month_str(value) -> str:
    """YYYY-MM for a transaction_date value (date object or ISO string)."""
    return value.strftime("%Y-%m") if hasattr(value, "strftime") else str(value)[:7]


def rsu_vest_gross_ttm(db, window_start_month: str, window_end_month: str) -> float:
    """Sum of amount_net for RSU_Excel 'vest' rows in [window_start_month,
    window_end_month] (inclusive, 'YYYY-MM' strings), converted to CNY.

    Read-only: SELECT on transactions only. Gross figure — never netted
    against sells (see module docstring).
    """
    rows = db.execute(
        """
        SELECT amount_net, currency
        FROM transactions
        WHERE source_system = 'RSU_Excel'
          AND LOWER(transaction_type) = 'vest'
          AND strftime(transaction_date, '%Y-%m') BETWEEN ? AND ?
        """,
        [window_start_month, window_end_month],
    ).fetchall()

    fx_rate = get_today_usd_cny_rate()
    total_cny = sum(_amount_to_cny(amount_net, currency, fx_rate) for amount_net, currency in rows)
    return round(total_cny, 2)


def replay_rsu_lots(
    db,
    *,
    on_match=None,
    legacy_strict_fifo: bool = False,
) -> tuple[list[dict], dict[str, float]]:
    """Full-history lot replay of RSU_Excel vest/sell rows — shared core.

    Read-only: SELECT on transactions only. This is the single lot-replay
    implementation in the project; `_surviving_lots()` (retained-shares
    measure) and `src/services/rsu_realized_gains.py` (realized-gain measure)
    are both thin callers of it. Do not fork this loop.

    Returns (surviving_lots, oversold_by_asset):

    - surviving_lots: currently-held lots (qty > _LOT_TOL), one dict per lot:
        {"asset_id": str, "vest_date": date, "qty": float,
         "price_unit": float, "currency": str}
      ordered by vest_date (oldest surviving lot first).
    - oversold_by_asset: {asset_id: total_oversold_qty} for any asset where a
      sell (or sequence of sells) exceeded all open lots. Empty dict when
      the data is clean.

    A 'vest' row (positive quantity) pushes a new lot. A 'sell' row (negative
    quantity) consumes the OLDEST open lots first for that asset_id,
    partially or fully. Over-selling (sell quantity exceeds all open lots for
    that asset) is absorbed without going negative — the queue simply empties
    for that asset; the excess sell quantity is never fabricated into a
    negative lot, but IS accumulated into oversold_by_asset and logged via
    logger.warning — a data error (e.g. a mis-dated transaction reordering
    the vest/sell chronology) must be observable, not silently dropped.

    Args:
        on_match: optional callback ``on_match(sale, lot, qty)`` invoked once
            per (sale, consumed lot) pair, BEFORE the lot is mutated, so
            ``lot["price_unit"]`` / ``lot["vest_date"]`` are the cost-basis
            side of the match and ``qty`` is the number of shares matched.
            ``sale`` is
            ``{"asset_id", "date", "price_unit", "currency", "quantity",
               "is_vest_lot_match", "is_sell_to_cover"}``. Both flags describe
            THIS match, not the whole sale — a vest-lot sale larger than its
            own lot falls through to ordinary FIFO for the excess, and those
            matches arrive with both flags False so their (real) gain is not
            suppressed.
            ``lot`` is ``None`` for the over-sold excess of a sale that could
            not be matched against any open lot (``qty`` = unmatched shares) —
            a consumer computing realized gain MUST treat that as
            "gain unknown", never as zero-basis profit.
        legacy_strict_fifo: escape hatch for regression tests ONLY. Leave it
            alone in production code — see "Lot rule" below.

    Lot rule (owner decision, 2026-08-01 — 「统一用 specific-lot」)
    -----------------------------------------------------------
    A sell is matched against a SPECIFIC vest lot instead of the FIFO head
    when both hold:

      * its price equals that lot's vest price within `_PRICE_TOL`, and
      * it falls 0..`_VEST_MATCH_WINDOW_DAYS` calendar days after that lot's
        vest date (one-sided — a sale before a vest can never be that vest's
        batch).

    The newest qualifying lot wins. Any quantity beyond that lot falls through
    to ordinary FIFO. Every other sale is ordinary FIFO.

    One rule, two real shapes, no special cases:
      * partial match (consumes only part of its vest lot) = mandatory
        sell-to-cover tax withholding — those shares never reached the
        employee's account. `is_sell_to_cover=True`.
      * full match (consumes the whole vest lot) = the batch itself being
        liquidated, e.g. the 2026-03-16 sale of the entire 2026-03-15 vest
        with tax withheld inside the single row.
        `is_sell_to_cover=False`, `is_vest_lot_match=True`.
    Either way the realized gain is ~zero, because the sale price IS the lot's
    vest price — which is the point: neither event is appreciation above the
    price the ledger already booked as income.

    Detection is structural (date + price), never memo text: the `memo` column
    on these rows is owner-authored free text from the Excel source and cannot
    be relied on. A sale near a vest at a DIFFERENT price, or at the same price
    but outside the window, is discretionary and goes to FIFO.

    This is now the ONE rule for every consumer — realized gains AND retained
    shares. It used to be opt-in while `rsu_retained_ttm()` stayed on strict
    FIFO, and that split was itself the bug: strict FIFO reported the surviving
    106.8 AMZN shares as the 2026-03-15 vest, the very batch the owner had
    ruled was liquidated on 2026-03-16. One batch cannot have two answers
    (`two-sources-signature-bug`). `legacy_strict_fifo=True` exists only so
    regression tests can still assert what the old rule produced; it is
    deliberately named so that any production call site reads as wrong on
    sight, and `test_no_production_code_opts_into_legacy_strict_fifo` enforces
    that.
    """
    rows = db.execute(
        """
        SELECT asset_id, transaction_date, transaction_type, quantity, price_unit, currency
        FROM transactions
        WHERE source_system = 'RSU_Excel'
          AND LOWER(transaction_type) IN ('vest', 'sell')
        ORDER BY transaction_date ASC, id ASC
        """
    ).fetchall()

    lots_by_asset: dict[str, list[dict]] = {}
    oversold_by_asset: dict[str, float] = {}
    for asset_id, tx_date, tx_type, quantity, price_unit, currency in rows:
        tx_type_l = (tx_type or "").lower()
        qty = float(quantity) if quantity is not None else 0.0
        px = float(price_unit) if price_unit is not None else 0.0
        lots = lots_by_asset.setdefault(asset_id, [])

        if tx_type_l == "vest":
            if qty <= _LOT_TOL:
                continue  # zero/negative vest quantity — nothing to add
            lots.append({
                "asset_id": asset_id,
                "vest_date": tx_date,
                "qty": qty,
                "price_unit": px,
                "currency": currency,
            })
        elif tx_type_l == "sell":
            remaining = abs(qty)
            sale = {
                "asset_id": asset_id,
                "date": tx_date,
                "price_unit": px,
                "currency": currency,
                "quantity": remaining,
                "is_vest_lot_match": False,
                "is_sell_to_cover": False,
            }

            if not legacy_strict_fifo:
                # Newest qualifying lot wins: if two vests somehow share a
                # price inside the window, the nearer one is the batch.
                own_lot = next(
                    (lot for lot in reversed(lots) if _is_own_vest_lot(lot, px, tx_date)),
                    None,
                )
                if own_lot is not None:
                    consumed = min(own_lot["qty"], remaining)
                    if consumed > _LOT_TOL:
                        # Both flags are per-MATCH, not per-sale: any quantity
                        # beyond this vest lot falls through to ordinary FIFO
                        # below and is a real disposal of OTHER lots whose gain
                        # must not be suppressed.
                        partial = remaining < own_lot["qty"] - _LOT_TOL
                        if on_match is not None:
                            on_match(
                                {**sale,
                                 "is_vest_lot_match": True,
                                 "is_sell_to_cover": partial},
                                own_lot,
                                consumed,
                            )
                        own_lot["qty"] -= consumed
                        remaining -= consumed
                        if own_lot["qty"] <= _LOT_TOL:
                            lots.remove(own_lot)

            while remaining > _LOT_TOL and lots:
                lot = lots[0]
                fully_consumed = lot["qty"] <= remaining + _LOT_TOL
                consumed = lot["qty"] if fully_consumed else remaining
                if on_match is not None:
                    on_match(sale, lot, consumed)
                if fully_consumed:
                    remaining -= lot["qty"]
                    lots.pop(0)
                else:
                    lot["qty"] -= remaining
                    remaining = 0.0
            # remaining > 0 here means over-selling beyond all open lots for
            # this asset — never fabricated into a negative lot, but tracked
            # and logged so the data error is observable, not silent.
            if remaining > _LOT_TOL:
                oversold_by_asset[asset_id] = oversold_by_asset.get(asset_id, 0.0) + remaining
                if on_match is not None:
                    on_match(sale, None, remaining)
                logger.warning(
                    "RSU FIFO over-sell: asset_id=%s sell on %s exceeds all open "
                    "lots by %.4f shares — likely a data error (bad transaction "
                    "date/quantity) in RSU_Excel source data",
                    asset_id, tx_date, remaining,
                )

    surviving = [
        lot for lots in lots_by_asset.values() for lot in lots if lot["qty"] > _LOT_TOL
    ]
    surviving.sort(key=lambda lot: (_month_str(lot["vest_date"]), lot["asset_id"]))
    return surviving, oversold_by_asset


def _surviving_lots(db) -> tuple[list[dict], dict[str, float]]:
    """Currently-held lots — thin wrapper over `replay_rsu_lots()`.

    Uses the project's one lot rule (specific-lot matching; see
    `replay_rsu_lots()`). Renamed from `_surviving_lots` on 2026-08-01
    when that became the rule: a name promising FIFO while the body matched
    specific lots is exactly the convention-contract trap this codebase keeps
    paying for.
    """
    return replay_rsu_lots(db)


def rsu_retained_ttm(db, window_start_month: str, window_end_month: str) -> dict:
    """RSU shares vested inside [window_start_month, window_end_month] and
    still held today, valued at their own vest price and converted to CNY.

    Read-only: delegates to _surviving_lots(db) for the full-history
    replay, no writes anywhere in this module.

    Returns:
        {
          "retained_cny": float,     # CNY value of in-window surviving lots
          "retained_shares": float,  # total shares across those lots
          "lots": [
              {"asset_id": str, "vest_date": "YYYY-MM-DD", "qty": float,
               "price_unit": float},
              ...
          ],  # explainability detail, oldest vest first
          "oversold_shares": float,  # total over-sold qty across ALL RSU_Excel
              # history (not window-scoped — a data-health signal, 0.0 when
              # clean). > 0 means the FIFO replay could not consume all sold
              # shares against open lots (see _surviving_lots) — retained
              # figures above may be understated by this amount. NOT fatal;
              # a caller/UI should surface it, never raise on it.
        }

    A surviving lot whose vest_date falls BEFORE window_start_month is
    excluded — it belongs to an earlier window's retained figure, not this
    one (see module docstring: this is why the FULL history must be walked,
    not just the window).
    """
    surviving, oversold_by_asset = _surviving_lots(db)

    in_window = [
        lot for lot in surviving
        if window_start_month <= _month_str(lot["vest_date"]) <= window_end_month
    ]

    fx_rate = get_today_usd_cny_rate()
    retained_cny = sum(
        _amount_to_cny(lot["qty"] * lot["price_unit"], lot["currency"], fx_rate)
        for lot in in_window
    )
    retained_shares = sum(lot["qty"] for lot in in_window)
    oversold_shares = round(sum(oversold_by_asset.values()), 2)

    return {
        "retained_cny": round(retained_cny, 2),
        "retained_shares": round(retained_shares, 2),
        "lots": [
            {
                "asset_id": lot["asset_id"],
                "vest_date": str(lot["vest_date"]),
                "qty": round(lot["qty"], 2),
                "price_unit": lot["price_unit"],
            }
            for lot in in_window
        ],
        "oversold_shares": oversold_shares,
    }
