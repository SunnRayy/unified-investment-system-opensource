"""Monthly per-asset attribution engine (Attribution & Flows Program WS-1).

Decomposes Δmarket_value per (month, asset) into price / trade / transfer /
income effects + residual, per docs/api-specs/attribution.md (normative
computation model — read that file before changing formulas here).

Key rules (see CLAUDE.md / AGENTS.md):
  - Rule 3: NEVER global MAX(snapshot_date) — always per-asset.
  - Rule 7: NEVER asset_registry.is_rebalanceable — use taxonomy_classes via
    the COALESCE(tc.is_rebalanceable, r.is_rebalanceable, TRUE) pattern.
  - Attribution cash cost: cash-like assets carry cost=0 semantics; their
    delta is explained via cash_flow_tags, not price/trade effects.

FX note (TODO, updated 2026-07-20 Item D): the held-quantity + mid-month
price-reval terms now use IMPLIED prices (mv/qty, both already CNY per the
project's storage rule) instead of native market_price_unit, so they need NO
FX conversion at all — this also fixes the PIS(legacy)->reader tier-mismatch
bug (see _detect_source_transition). FX still applies to trade_effect and
income_effect (transaction-date rate) and to the mid-month reval term's
transaction-price leg (ev_price is native currency, converted via tx_fx
before comparing to the CNY-denominated price_end_implied). There is still no
historical FX-rate table (only src.services.currency.get_today_usd_cny_rate —
a single live/current rate), so those transaction-date-FX terms are exact
only for the current month and an approximation for historical months.
Flagged here rather than inventing a rates table — a real historical-FX
source is a follow-up.

compute_month() / compute_range() are the WRITE path (delete month
partition + rewrite, idempotent). compute_month_raw() is the pure READ-ONLY
computation with no side effects, kept separate so callers (tests, ad-hoc
report queries) can inspect results without touching the DB.
"""
from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date
from typing import Optional

from src.services.currency import get_today_usd_cny_rate
from src.services.north_star_flows import _compose_fs_cash_key, _is_fs_cash_asset
from src.sync.phases._common import LEGACY_HOLDING_SOURCES

logger = logging.getLogger(__name__)

HISTORY_FLOOR_MONTH = date(2026, 1, 1)

_BUY_TYPES = ("buy",)
_SELL_TYPES = ("sell",)
_TRANSFER_IN_TYPES = ("transfer_in",)
_TRANSFER_OUT_TYPES = ("transfer_out",)
_VEST_TYPES = ("vest",)


def _month_start(month: date) -> date:
    return date(month.year, month.month, 1)


def _month_bounds(month: date) -> tuple[date, date, date]:
    """Return (month_start, prev_month_end, month_end) for the calendar month
    containing `month` (any day-of-month is normalized to the 1st)."""
    m_start = _month_start(month)
    if m_start.month == 1:
        prev_end = date(m_start.year - 1, 12, 31)
    else:
        prev_end = date(m_start.year, m_start.month - 1, monthrange(m_start.year, m_start.month - 1)[1])
    m_end = date(m_start.year, m_start.month, monthrange(m_start.year, m_start.month)[1])
    return m_start, prev_end, m_end


def _fx_rate(currency: Optional[str]) -> float:
    if (currency or "CNY").upper() == "USD":
        try:
            return get_today_usd_cny_rate()
        except Exception:
            logger.warning("get_today_usd_cny_rate failed, falling back to 7.0", exc_info=True)
            return 7.0
    return 1.0


def _asset_universe(db, month_end: date) -> list[str]:
    """All asset_ids relevant for this month: those with a non-shadow holdings
    snapshot on/before month_end, or a transaction dated within the month."""
    m_start = date(month_end.year, month_end.month, 1)
    rows = db.execute(
        """
        SELECT DISTINCT asset_id FROM holdings
        WHERE snapshot_date <= ?
        UNION
        SELECT DISTINCT asset_id FROM transactions
        WHERE transaction_date >= ? AND transaction_date <= ?
        """,
        [str(month_end), str(m_start), str(month_end)],
    ).fetchall()
    return [r[0] for r in rows]


def _coauthority_sources() -> "frozenset[str]":
    """AuthorityResolver's co-authority source set (e.g. {Schwab_CSV, Broker_IBKR}).

    Wrapped in its own function (rather than imported at module scope) so tests can
    patch `src.services.attribution.AuthorityResolver` directly, matching the pattern
    in src/sync/reference_export.py. Failures degrade to an empty set (no co-authority
    exclusion) rather than breaking valuation.
    """
    from src.identity.authority_resolver import AuthorityResolver

    try:
        return AuthorityResolver().coauthority_sources()
    except Exception:
        logger.warning("AuthorityResolver().coauthority_sources() failed", exc_info=True)
        return frozenset()


def _latest_snapshot_by_asset(db, as_of_date: date, asset_ids: Optional[list[str]] = None) -> dict[str, dict]:
    """Per-asset valuation at as_of_date, using each SOURCE's own latest row.

    Rule 3: per-asset (and here, per-source) MAX, never global. CRITICAL: NO
    is_shadow filter in historical valuation — in this schema is_shadow on OLD
    reader rows means "superseded by a newer snapshot" (a current-state flag,
    not time-versioning), so filtering is_shadow=FALSE erases history and
    yields phantom mv_start=0 for long-held assets (2026-07-19 lead review).

    LOCKED VALUATION v2 (2026-07-20, fixes the v1 single-d* tombstone bug: a
    zero-qty tombstone written by source A with a snapshot_date LATER than
    source B's real holding row used to win the asset-wide MAX(snapshot_date)
    and zero out the whole asset — e.g. Schwab US_STK_VOO 2026-06-26 tombstone
    postdating Broker_IBKR's real 2026-06-25 row):
      1. Per (asset_id, source_system): take that source's own latest row with
         snapshot_date <= as_of_date. Every source contributes independently —
         one source's tombstone no longer hides another source's real row.
      2. Drop LEGACY_HOLDING_SOURCES (PIS family, ADR-003) rows from that set
         if any non-legacy source is present — existing history-floor
         semantics, now applied per-source. If ONLY legacy sources are present
         (early history), keep them — that IS the floor.
      3. If a 'Consolidated' row is in the remaining set, also drop rows from
         AuthorityResolver.coauthority_sources() (currently Schwab_CSV +
         Broker_IBKR) — Consolidated is the merged co-authority value, so its
         constituent brokers' OWN latest rows would double count (or, per the
         v1 bug, could be stale tombstones that no longer apply now that each
         source has its own d*).
      4. Sum market_value over what remains. A tombstone row only zeroes out
         its OWN source's contribution, not the asset's.
    """
    asset_filter = ""
    params: list = [str(as_of_date)]
    if asset_ids:
        placeholders = ", ".join(["?"] * len(asset_ids))
        asset_filter = f"AND asset_id IN ({placeholders})"
        params.extend(asset_ids)

    rows = db.execute(
        f"""
        WITH latest AS (
            SELECT asset_id, source_system, MAX(snapshot_date) AS max_date
            FROM holdings
            WHERE snapshot_date <= ?
            {asset_filter}
            GROUP BY asset_id, source_system
        )
        SELECT h.asset_id, h.source_system, h.quantity, h.market_value,
               h.market_price_unit, h.currency, h.snapshot_date
        FROM holdings h
        JOIN latest l ON h.asset_id = l.asset_id
            AND h.source_system = l.source_system
            AND h.snapshot_date = l.max_date
        """,
        params,
    ).fetchall()

    by_asset: dict[str, list[dict]] = {}
    for asset_id, source, qty, mv, price, currency, snap_date in rows:
        by_asset.setdefault(asset_id, []).append({
            "source_system": source,
            "quantity": float(qty or 0.0),
            "market_value": float(mv or 0.0),
            "market_price_unit": float(price) if price is not None else None,
            "currency": currency or "CNY",
            "snapshot_date": snap_date,
        })

    coauthority: Optional["frozenset[str]"] = None
    out = {}
    for asset_id, asset_rows in by_asset.items():
        non_legacy = [r for r in asset_rows if r["source_system"] not in LEGACY_HOLDING_SOURCES]
        is_legacy_floor = not non_legacy  # step 2: history floor fallback (captured before reassignment below)
        use = non_legacy if non_legacy else asset_rows

        if any(r["source_system"] == "Consolidated" for r in use):
            if coauthority is None:
                coauthority = _coauthority_sources()
            use = [r for r in use if r["source_system"] not in coauthority]  # step 3

        qty = sum(r["quantity"] for r in use)
        mv = sum(r["market_value"] for r in use)
        # Native unit price: qty-weighted average across the summed rows
        # (sources report the same instrument, so prices agree; weighting only
        # matters when one source is missing a price).
        priced = [r for r in use if r["market_price_unit"] is not None]
        if priced:
            w_qty = sum(r["quantity"] for r in priced)
            if w_qty:
                price = sum(r["market_price_unit"] * r["quantity"] for r in priced) / w_qty
            else:
                price = priced[0]["market_price_unit"]
        else:
            price = None
        out[asset_id] = {
            "quantity": qty,
            "market_value": mv,
            "market_price_unit": price,
            "currency": use[0]["currency"] if use else asset_rows[0]["currency"],
            "snapshot_date": max((r["snapshot_date"] for r in use), default=asset_rows[0]["snapshot_date"]),
            # Tier used at this boundary — needed by _detect_source_transition
            # (Item D, 2026-07-20): is_legacy=True means the history-floor
            # fallback fired (step 2, ONLY legacy/PIS sources at this d*).
            "is_legacy": is_legacy_floor,
            "sources": sorted({r["source_system"] for r in use}),
        }
    return out


def _detect_source_transition(start_info: Optional[dict], end_info: Optional[dict]) -> bool:
    """Item D (2026-07-20 owner review): detect a PIS(legacy)->reader (or
    otherwise incompatible) valuation-tier transition between mv_start's and
    mv_end's boundary rows, where qty/price conventions may differ and a
    naive qty x delta-price price_effect would be meaningless (verified case:
    Feb-2026 US_STK_AGG, price_effect -398,403.95 / residual +601,603.46 —
    purely a tier-mismatch artifact, not real price movement).

    Two independent triggers (either fires the guard):
      1. Tier mismatch: one boundary used ONLY legacy/PIS sources (history
         floor fallback) while the other used a non-legacy reader source.
      2. Implied-price convention mismatch: even within the same tier, if the
         mv/qty implied unit price jumps by more than 1 order of magnitude
         (|log10(p_end/p_start)| > 1), qty/price conventions differ (e.g. a
         per-100-shares vs per-share unit convention) and the delta is not a
         real price move.
    """
    if not start_info or not end_info:
        return False
    if bool(start_info.get("is_legacy")) != bool(end_info.get("is_legacy")):
        return True
    qty_s, mv_s = start_info.get("quantity"), start_info.get("market_value")
    qty_e, mv_e = end_info.get("quantity"), end_info.get("market_value")
    if qty_s and qty_e:
        p_s = mv_s / qty_s
        p_e = mv_e / qty_e
        if p_s > 0 and p_e > 0:
            import math
            if abs(math.log10(p_e / p_s)) > 1:
                return True
    return False


def _month_transactions(db, asset_id: str, m_start: date, m_end: date) -> list[dict]:
    rows = db.execute(
        """
        SELECT transaction_date, LOWER(transaction_type) AS ttype, quantity,
               price_unit, amount_net, currency
        FROM transactions
        WHERE asset_id = ? AND transaction_date >= ? AND transaction_date <= ?
        ORDER BY transaction_date
        """,
        [asset_id, str(m_start), str(m_end)],
    ).fetchall()
    out = []
    for tx_date, ttype, qty, price_unit, amount_net, currency in rows:
        out.append({
            "date": tx_date,
            "type": ttype,
            "quantity": float(qty) if qty is not None else 0.0,
            "price_unit": float(price_unit) if price_unit is not None else None,
            "amount_net": float(amount_net) if amount_net is not None else 0.0,
            "currency": currency or "CNY",
        })
    return out


def compute_month_raw(db, month: date) -> list[dict]:
    """Pure READ-ONLY computation of the month's per-asset attribution rows.
    No DB writes. Returns a list of dicts (one per asset_id), including
    assets with all-zero effects (caller may filter).
    """
    m_start, prev_end, m_end = _month_bounds(month)

    mv_start_map = _latest_snapshot_by_asset(db, prev_end)
    mv_end_map = _latest_snapshot_by_asset(db, m_end)

    asset_ids = sorted(set(_asset_universe(db, m_end)))

    # WS-2 (plan 2026-07-20-fs-cash-flows-attribution.md): resolve this
    # month's cash_flow_tags for in-scope FS-cash assets (CASH_* /
    # Wealth_CMB) in ONE query, keyed by the stable fscash: natural key
    # (asset_id + YYYY-MM) — never a per-asset point query in the loop below.
    # A tagged month's residual is absorbed into transfer_effect/income_effect
    # (see the per-asset adjustment below); an untagged FS-cash month is left
    # exactly as computed (residual + dq_flag), same as any other asset.
    month_str = m_start.strftime("%Y-%m")
    fs_cash_asset_ids = [aid for aid in asset_ids if _is_fs_cash_asset(aid)]
    fs_cash_tag_by_asset: dict[str, str] = {}
    if fs_cash_asset_ids:
        key_to_asset = {_compose_fs_cash_key(aid, month_str): aid for aid in fs_cash_asset_ids}
        placeholders = ", ".join(["?"] * len(key_to_asset))
        tag_rows = db.execute(
            f"""
            SELECT source_row_key, classification FROM cash_flow_tags
            WHERE source_table = 'fs_cash_delta' AND source_row_key IN ({placeholders})
            """,
            list(key_to_asset.keys()),
        ).fetchall()
        for row_key, classification in tag_rows:
            aid = key_to_asset.get(row_key)
            if aid is not None:
                fs_cash_tag_by_asset[aid] = classification

    results = []
    for asset_id in asset_ids:
        start = mv_start_map.get(asset_id)
        end = mv_end_map.get(asset_id)

        mv_start = start["market_value"] if start else 0.0
        mv_end = end["market_value"] if end else 0.0
        qty_start = start["quantity"] if start else 0.0
        qty_end = end["quantity"] if end else 0.0
        # Native market_price_unit + FX — kept ONLY for transfer_effect's
        # quantity valuation (a lookup, not a delta), which survives a
        # qty_end=0 ACAT tombstone (mv/qty is undefined at 0/0, but the
        # source still reports a last-known native price in that row). This
        # is NOT used for any qty x delta(price) term — see price_*_implied
        # below for why that mixes conventions across a tier transition.
        price_start_native = start["market_price_unit"] if start else None
        price_end_native = end["market_price_unit"] if end else None
        currency = (end or start or {}).get("currency", "CNY")
        fx = _fx_rate(currency)

        # IMPLIED prices (Item D, 2026-07-20 owner review): mv is ALWAYS
        # stored in CNY (project rule), so mv/qty is a CNY-denominated unit
        # price with no FX ambiguity — unlike market_price_unit, which is
        # native currency (USD for US assets) and, worse, can carry a
        # different qty/price CONVENTION across a PIS(legacy)->reader
        # valuation-tier transition, making qty x delta(market_price_unit) a
        # meaningless mixed-unit computation (verified: Feb-2026 US_STK_AGG,
        # price_effect -398,403.95 / residual +601,603.46 from exactly this).
        price_start_implied = (mv_start / qty_start) if (start and qty_start) else None
        price_end_implied = (mv_end / qty_end) if (end and qty_end) else None

        # source_transition guard: if the start/end boundary rows come from
        # incompatible valuation tiers (legacy-only vs reader) or their
        # implied prices disagree by >1 order of magnitude even within the
        # same tier, do NOT compute a price term — it would be noise, not
        # signal. Fold the whole (now-unexplained) delta into residual and
        # force dq_flag so it surfaces for review; _derive_dq_reason()
        # (read-time, Item A) independently re-detects this same condition
        # to label it 'source_transition'.
        source_transition = _detect_source_transition(start, end)

        events = _month_transactions(db, asset_id, m_start, m_end)

        price_effect = 0.0
        trade_effect = 0.0
        transfer_effect = 0.0
        income_effect = 0.0

        # Held-quantity revaluation to month-end price (only if we have both
        # a start price and an end price — otherwise there's nothing to
        # revalue, e.g. asset first seen mid-month). Both terms are already
        # CNY — no FX multiply here (that was the market_price_unit-era bug).
        if (
            not source_transition
            and price_start_implied is not None
            and price_end_implied is not None
            and qty_start
        ):
            price_effect += qty_start * (price_end_implied - price_start_implied)

        for ev in events:
            ttype = ev["type"]
            qty = ev["quantity"]
            amt = ev["amount_net"]
            tx_fx = _fx_rate(ev["currency"])
            ev_price = ev["price_unit"]

            if ttype in _BUY_TYPES or ttype == "dividend_reinvest":
                trade_effect += abs(amt) * tx_fx
                signed_qty = abs(qty)
            elif ttype in _SELL_TYPES:
                trade_effect -= abs(amt) * tx_fx
                signed_qty = -abs(qty)
            elif ttype in _TRANSFER_IN_TYPES:
                xfer_price = price_end_native if price_end_native is not None else price_start_native
                if xfer_price is not None:
                    transfer_effect += abs(qty) * xfer_price * fx
                signed_qty = abs(qty)
            elif ttype in _TRANSFER_OUT_TYPES:
                xfer_price = price_end_native if price_end_native is not None else price_start_native
                if xfer_price is not None:
                    transfer_effect -= abs(qty) * xfer_price * fx
                signed_qty = -abs(qty)
            elif ttype in _VEST_TYPES:
                vest_price = ev_price if ev_price is not None else 0.0
                income_effect += abs(qty) * vest_price * tx_fx
                signed_qty = abs(qty)
            else:
                signed_qty = 0.0

            # Mid-month qty-event revaluation to month-end price. Transfer legs
            # are excluded — transfer_effect above already values the full
            # quantity at month-end price directly, so an extra reval term
            # would double count. Buy/sell/vest are valued at their own
            # transaction price, so they need this term to bring them up to
            # month-end price (spec: "qty_event × (p_end − p_event)").
            # ev_price is native-currency (converted to CNY via tx_fx) so it
            # can be compared against price_end_implied (already CNY).
            if signed_qty and not source_transition and price_end_implied is not None and ev_price is not None and (
                ttype in _BUY_TYPES + _SELL_TYPES + _VEST_TYPES
            ):
                price_effect += signed_qty * (price_end_implied - ev_price * tx_fx)

        delta = mv_end - mv_start
        explained = price_effect + trade_effect + transfer_effect + income_effect
        residual = delta - explained
        threshold = max(0.01 * max(abs(mv_start), abs(mv_end)), 500.0)
        dq_flag = source_transition or (abs(residual) > threshold)

        # WS-2: a tagged FS-cash month absorbs its residual into the
        # classification-appropriate effect bucket instead of leaving it
        # unexplained. Moving residual INTO an effect (rather than
        # re-deriving delta from the candidate map) keeps the waterfall
        # identity delta == price+trade+transfer+income+residual intact by
        # construction — the sum of buckets is unchanged, only its
        # attribution to a specific bucket changes. FX (deposit vs FX drift
        # for USD-denominated FS balances) is NOT separated — documented v1
        # limitation (plan, "Non-goals").
        fs_cash_classification = fs_cash_tag_by_asset.get(asset_id)
        if fs_cash_classification is not None:
            if fs_cash_classification in ("external_contribution", "internal_transfer"):
                transfer_effect += residual
                residual = 0.0
            elif fs_cash_classification == "income_reinvested":
                income_effect += residual
                residual = 0.0
            dq_flag = False
            source_transition = False

        results.append({
            "month": m_start,
            "asset_id": asset_id,
            "mv_start": round(mv_start, 2),
            "mv_end": round(mv_end, 2),
            "price_effect": round(price_effect, 2),
            "trade_effect": round(trade_effect, 2),
            "transfer_effect": round(transfer_effect, 2),
            "income_effect": round(income_effect, 2),
            "residual": round(residual, 2),
            "dq_flag": bool(dq_flag),
            "source_transition": bool(source_transition),  # diagnostics only — not persisted
            "events": events,
        })
    return results


def compute_month(db, month: date) -> dict:
    """WRITE path: recompute one month, deleting + rewriting its partition
    in attribution_monthly (idempotent)."""
    m_start, _, _ = _month_bounds(month)
    rows = compute_month_raw(db, m_start)

    db.execute("DELETE FROM attribution_monthly WHERE month = ?", [str(m_start)])
    for row in rows:
        db.execute(
            """
            INSERT INTO attribution_monthly
                (month, asset_id, mv_start, mv_end, price_effect, trade_effect,
                 transfer_effect, income_effect, residual, dq_flag, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [
                str(m_start), row["asset_id"], row["mv_start"], row["mv_end"],
                row["price_effect"], row["trade_effect"], row["transfer_effect"],
                row["income_effect"], row["residual"], row["dq_flag"],
            ],
        )

    dq_count = sum(1 for r in rows if r["dq_flag"])
    return {"month": m_start.isoformat(), "rows": len(rows), "dq_count": dq_count}


def compute_range(db, start_month: date, end_month: date) -> list[dict]:
    """WRITE path: recompute every calendar month in [start_month, end_month]."""
    out = []
    cur = _month_start(start_month)
    end = _month_start(end_month)
    while cur <= end:
        out.append(compute_month(db, cur))
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return out


# ── Read / roll-up helpers (API layer) ──────────────────────────────────────

def _as_date(v) -> Optional[date]:
    if v is None:
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def _derive_dq_reason(db, asset_id: str, month: date) -> dict:
    """READ-time explainability for a dq-flagged asset-month (Item A + Item D,
    2026-07-20 owner review). No schema change — recomputes the same tiered
    valuation + a live transactions lookup, so this always agrees with
    whatever compute_month_raw() would produce right now (even if the stored
    attribution_monthly row is from an older recompute).

    Checked in order (first match wins):
      -1. fs_cash_untagged — an in-scope FS-cash asset (CASH_* / Wealth_CMB)
          reaching this function is, by construction, untagged for this month
          (compute_month_raw() clears dq_flag whenever a cash_flow_tags row
          exists for it — see the fs_cash_tag_by_asset adjustment there), so
          this is always the right, actionable message for these assets.
          Checked FIRST — before source_transition — so an FS-cash asset that
          also happens to trip the tier-mismatch guard (e.g. a PIS->reader
          boundary) still gets pointed at the tagging page instead of the
          generic "估值来源变更" message, which the owner cannot act on.
      0. source_transition — same tier-mismatch guard as compute_month_raw's
         price_effect suppression (Item D). Checked next: a legacy->reader
         boundary transition is the root cause even when post-boundary
         transactions also exist, so it must win over reason 1.
      1. snapshot_lag — transactions exist after mv_end's own d* within the
         month (the classic "Excel/reader data is a few days stale" case).
      2. first_seen — no snapshot <= month start (mv_start is a true absence,
         not a zero-value snapshot).
      3. stale_end_snapshot — d* exists but is >7 days before month-end, with
         no explaining post-snapshot transactions (would otherwise be case 1).
      4. unexplained — keep the flag, generic reason (no known root cause).
    """
    if _is_fs_cash_asset(asset_id):
        _, _, m_end_fs = _month_bounds(month)
        end_info_fs = _latest_snapshot_by_asset(db, m_end_fs, asset_ids=[asset_id]).get(asset_id)
        d_end_fs = _as_date(end_info_fs["snapshot_date"]) if end_info_fs else None
        return {
            "dq_reason": "现金余额变动未分类 — 请在现金流分类页标记 (存入/支出/内部转账)",
            "dq_detail": {
                "kind": "fs_cash_untagged",
                "asset_id": asset_id,
                "snapshot_end_date": d_end_fs.isoformat() if d_end_fs else None,
                "post_snapshot_tx_count": 0,
                "post_snapshot_tx_sum": 0.0,
            },
        }

    _, prev_end, m_end = _month_bounds(month)

    start_info = _latest_snapshot_by_asset(db, prev_end, asset_ids=[asset_id]).get(asset_id)
    end_info = _latest_snapshot_by_asset(db, m_end, asset_ids=[asset_id]).get(asset_id)
    d_end = _as_date(end_info["snapshot_date"]) if end_info else None

    if _detect_source_transition(start_info, end_info):
        return {
            "dq_reason": "估值来源变更 (PIS→reader) — 本月无法分解价格效应",
            "dq_detail": {
                "kind": "source_transition",
                "snapshot_end_date": d_end.isoformat() if d_end else None,
                "post_snapshot_tx_count": 0,
                "post_snapshot_tx_sum": 0.0,
            },
        }

    post_tx_count = 0
    post_tx_sum = 0.0
    if d_end is not None:
        row = db.execute(
            """
            SELECT COUNT(*), SUM(ABS(amount_net))
            FROM transactions
            WHERE asset_id = ? AND transaction_date > ? AND transaction_date <= ?
            """,
            [asset_id, str(d_end), str(m_end)],
        ).fetchone()
        post_tx_count = int(row[0] or 0)
        post_tx_sum = float(row[1] or 0.0)

    if post_tx_count > 0:
        return {
            "dq_reason": (
                f"月末快照 {d_end.isoformat()} 早于 {post_tx_count} 笔交易 "
                f"(共 ¥{post_tx_sum:,.0f}) — Excel/reader 数据滞后"
            ),
            "dq_detail": {
                "kind": "snapshot_lag",
                "snapshot_end_date": d_end.isoformat(),
                "post_snapshot_tx_count": post_tx_count,
                "post_snapshot_tx_sum": round(post_tx_sum, 2),
            },
        }

    if start_info is None:
        return {
            "dq_reason": "本月首次出现 — 无期初快照 (mv_start=0)",
            "dq_detail": {
                "kind": "first_seen",
                "snapshot_end_date": d_end.isoformat() if d_end else None,
                "post_snapshot_tx_count": 0,
                "post_snapshot_tx_sum": 0.0,
            },
        }

    if d_end is not None and (m_end - d_end).days > 7:
        return {
            "dq_reason": f"月末快照日期 {d_end.isoformat()} 距月末 >7 天",
            "dq_detail": {
                "kind": "stale_end_snapshot",
                "snapshot_end_date": d_end.isoformat(),
                "post_snapshot_tx_count": 0,
                "post_snapshot_tx_sum": 0.0,
            },
        }

    return {
        "dq_reason": "unexplained residual",
        "dq_detail": {
            "kind": "unexplained",
            "snapshot_end_date": d_end.isoformat() if d_end else None,
            "post_snapshot_tx_count": 0,
            "post_snapshot_tx_sum": 0.0,
        },
    }


_TAXONOMY_JOIN = """
    FROM attribution_monthly a
    JOIN asset_registry r ON a.asset_id = r.canonical_id
    LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
    LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
"""

# Additive _cn companions (Program BIL / WS-9) — mirror the English
# COALESCE precedence exactly so a _cn value is only ever attached to the
# taxonomy row that actually produced the English name next to it. `tc`/
# `parent_tc` are LEFT JOINs from _TAXONOMY_JOIN; name_cn is NULL when the
# row has none set or when the join didn't match — the frontend resolver
# falls back to the English name in both cases.
_SUB_CLASS_CN_EXPR = "tc.name_cn"
_TOP_CLASS_CN_EXPR = (
    "CASE WHEN parent_tc.name IS NOT NULL THEN parent_tc.name_cn "
    "WHEN tc.name IS NOT NULL THEN tc.name_cn ELSE NULL END"
)


def get_monthly(
    db,
    month: date,
    level: str = "sub_class",
    include_non_rebalanceable: bool = True,
    month_to: Optional[date] = None,
) -> dict:
    """Read the stored attribution_monthly rows for one month (or, when
    `month_to` is given, a month range [month, month_to] inclusive — Item B,
    2026-07-20 owner review), rolled up to the requested level.

    Single-month path (month_to=None) is unchanged from WS-1. Range path
    aggregates per asset across months: mv_start = first month's mv_start,
    mv_end = last month's mv_end, all effects summed, dq_flag = OR across
    months. Response `month` becomes "YYYY-MM..YYYY-MM" only when month_to
    is explicitly passed (even if it equals `month`).
    """
    m_start = _month_start(month)
    where_reb = "" if include_non_rebalanceable else "AND COALESCE(tc.is_rebalanceable, r.is_rebalanceable, TRUE) = TRUE"

    if month_to is None:
        return _get_monthly_single(db, m_start, level, where_reb)
    return _get_monthly_range(db, m_start, _month_start(month_to), level, where_reb)


def _get_monthly_single(db, m_start: date, level: str, where_reb: str) -> dict:
    if level == "asset":
        sql = f"""
            SELECT a.asset_id, r.display_name AS asset_name,
                   COALESCE(tc.name, r.asset_class, 'Unclassified') AS sub_class,
                   COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified') AS top_class,
                   {_SUB_CLASS_CN_EXPR} AS sub_class_cn,
                   {_TOP_CLASS_CN_EXPR} AS top_class_cn,
                   a.mv_start, a.mv_end, a.price_effect, a.trade_effect,
                   a.transfer_effect, a.income_effect, a.residual, a.dq_flag
            {_TAXONOMY_JOIN}
            WHERE a.month = ? {where_reb}
            ORDER BY a.mv_end DESC
        """
        rows = db.execute(sql, [str(m_start)]).fetchall()
        out_rows = []
        for (asset_id, asset_name, sub_class, top_class, sub_class_cn, top_class_cn, mv_start, mv_end, price_e, trade_e,
             transfer_e, income_e, residual, dq_flag) in rows:
            dq_flag = bool(dq_flag)
            dq_reason = None
            dq_detail = None
            if dq_flag:
                reason = _derive_dq_reason(db, asset_id, m_start)
                dq_reason = reason["dq_reason"]
                dq_detail = reason["dq_detail"]
            out_rows.append({
                "key": asset_id, "asset_id": asset_id, "asset_name": asset_name,
                "top_class": top_class, "sub_class": sub_class,
                "top_class_cn": top_class_cn, "sub_class_cn": sub_class_cn,
                "mv_start": float(mv_start), "mv_end": float(mv_end),
                "delta": float(mv_end) - float(mv_start),
                "price_effect": float(price_e), "trade_effect": float(trade_e),
                "transfer_effect": float(transfer_e), "income_effect": float(income_e),
                "residual": float(residual), "dq_flag": dq_flag,
                "dq_reason": dq_reason, "dq_detail": dq_detail, "asset_count": 1,
            })
    else:
        if level == "top_class":
            key_expr = "COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified')"
            top_class_expr = key_expr
            key_cn_expr = _TOP_CLASS_CN_EXPR
            top_class_cn_expr = key_cn_expr
        elif level == "total":
            key_expr = "'Total'"
            top_class_expr = "NULL"
            # 'Total' is a literal, not a taxonomy_classes value — no cn companion.
            key_cn_expr = "NULL"
            top_class_cn_expr = "NULL"
        else:  # sub_class (default)
            key_expr = "COALESCE(tc.name, r.asset_class, 'Unclassified')"
            top_class_expr = "COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified')"
            key_cn_expr = _SUB_CLASS_CN_EXPR
            top_class_cn_expr = _TOP_CLASS_CN_EXPR

        sql = f"""
            SELECT {key_expr} AS key, {top_class_expr} AS top_class,
                   MAX({key_cn_expr}) AS key_cn, MAX({top_class_cn_expr}) AS top_class_cn,
                   SUM(a.mv_start) AS mv_start, SUM(a.mv_end) AS mv_end,
                   SUM(a.price_effect) AS price_effect, SUM(a.trade_effect) AS trade_effect,
                   SUM(a.transfer_effect) AS transfer_effect, SUM(a.income_effect) AS income_effect,
                   SUM(a.residual) AS residual, BOOL_OR(a.dq_flag) AS dq_flag,
                   COUNT(*) AS asset_count
            {_TAXONOMY_JOIN}
            WHERE a.month = ? {where_reb}
            GROUP BY key, top_class
            ORDER BY mv_end DESC
        """
        rows = db.execute(sql, [str(m_start)]).fetchall()
        out_rows = []
        for row in rows:
            (key, top_class, key_cn, top_class_cn, mv_start, mv_end, price_e, trade_e,
             transfer_e, income_e, residual, dq_flag, asset_count) = row
            out_rows.append({
                "key": key, "top_class": top_class if level != "top_class" else key,
                "key_cn": key_cn, "top_class_cn": top_class_cn if level != "top_class" else key_cn,
                "mv_start": float(mv_start or 0), "mv_end": float(mv_end or 0),
                "delta": float(mv_end or 0) - float(mv_start or 0),
                "price_effect": float(price_e or 0), "trade_effect": float(trade_e or 0),
                "transfer_effect": float(transfer_e or 0), "income_effect": float(income_e or 0),
                "residual": float(residual or 0), "dq_flag": bool(dq_flag),
                "dq_reason": None, "dq_detail": None,  # rollup rows aggregate multiple assets — no single reason
                "asset_count": int(asset_count),
            })

    totals_row = db.execute(
        f"""
        SELECT SUM(a.mv_end) - SUM(a.mv_start) AS delta, SUM(a.price_effect), SUM(a.trade_effect),
               SUM(a.transfer_effect), SUM(a.income_effect), SUM(a.residual)
        {_TAXONOMY_JOIN}
        WHERE a.month = ? {where_reb}
        """,
        [str(m_start)],
    ).fetchone()
    totals = {
        "delta": float(totals_row[0] or 0), "price_effect": float(totals_row[1] or 0),
        "trade_effect": float(totals_row[2] or 0), "transfer_effect": float(totals_row[3] or 0),
        "income_effect": float(totals_row[4] or 0), "residual": float(totals_row[5] or 0),
    }

    dq_rows = db.execute(
        "SELECT asset_id FROM attribution_monthly WHERE month = ? AND dq_flag = TRUE",
        [str(m_start)],
    ).fetchall()
    dq_flagged_assets = [r[0] for r in dq_rows]

    computed_at_row = db.execute(
        "SELECT MAX(computed_at) FROM attribution_monthly WHERE month = ?", [str(m_start)]
    ).fetchone()
    computed_at = computed_at_row[0].isoformat() if computed_at_row and computed_at_row[0] else None

    return {
        "month": m_start.strftime("%Y-%m"),
        "level": level,
        "rows": out_rows,
        "totals": totals,
        "dq_flagged_assets": dq_flagged_assets,
        "computed_at": computed_at,
    }


def _get_monthly_range(db, m_start: date, m_end_range: date, level: str, where_reb: str) -> dict:
    """Item B (2026-07-20): aggregate stored attribution_monthly rows across
    [m_start, m_end_range] inclusive. Always builds the per-asset aggregate
    first (mv_start/mv_end need first/last-month semantics, not a flat SUM),
    then rolls that up in Python for sub_class/top_class/total levels — SUMs
    of already-additive fields (price/trade/transfer/income/residual) are
    associative either way, but mv_start/mv_end are NOT, so they must come
    from the per-asset first/last computation, never a flat SQL SUM."""
    sql = f"""
        SELECT a.asset_id, r.display_name AS asset_name,
               COALESCE(tc.name, r.asset_class, 'Unclassified') AS sub_class,
               COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified') AS top_class,
               {_SUB_CLASS_CN_EXPR} AS sub_class_cn,
               {_TOP_CLASS_CN_EXPR} AS top_class_cn,
               a.month, a.mv_start, a.mv_end, a.price_effect, a.trade_effect,
               a.transfer_effect, a.income_effect, a.residual, a.dq_flag
        {_TAXONOMY_JOIN}
        WHERE a.month >= ? AND a.month <= ? {where_reb}
        ORDER BY a.asset_id, a.month
    """
    rows = db.execute(sql, [str(m_start), str(m_end_range)]).fetchall()

    by_asset: dict[str, dict] = {}
    for (asset_id, asset_name, sub_class, top_class, sub_class_cn, top_class_cn, mo, mv_start, mv_end, price_e, trade_e,
         transfer_e, income_e, residual, dq_flag) in rows:
        mo_date = _as_date(mo)
        entry = by_asset.setdefault(asset_id, {
            "asset_id": asset_id, "asset_name": asset_name,
            "sub_class": sub_class, "top_class": top_class,
            "sub_class_cn": sub_class_cn, "top_class_cn": top_class_cn,
            "months": [],
        })
        entry["months"].append({
            "month": mo_date, "mv_start": float(mv_start), "mv_end": float(mv_end),
            "price_effect": float(price_e), "trade_effect": float(trade_e),
            "transfer_effect": float(transfer_e), "income_effect": float(income_e),
            "residual": float(residual), "dq_flag": bool(dq_flag),
        })

    asset_rows = []
    for asset_id, entry in by_asset.items():
        months = entry["months"]  # already ORDER BY month asc within this asset_id group
        first, last = months[0], months[-1]
        mv_start = first["mv_start"]
        mv_end = last["mv_end"]
        price_effect = sum(m["price_effect"] for m in months)
        trade_effect = sum(m["trade_effect"] for m in months)
        transfer_effect = sum(m["transfer_effect"] for m in months)
        income_effect = sum(m["income_effect"] for m in months)
        residual = sum(m["residual"] for m in months)
        dq_flag = any(m["dq_flag"] for m in months)

        dq_reason = None
        dq_detail = None
        if dq_flag:
            flagged = [m for m in months if m["dq_flag"]]
            worst = max(flagged, key=lambda m: abs(m["residual"]))
            reason = _derive_dq_reason(db, asset_id, worst["month"])
            dq_reason = reason["dq_reason"]
            dq_detail = reason["dq_detail"]

        asset_rows.append({
            "key": asset_id, "asset_id": asset_id, "asset_name": entry["asset_name"],
            "top_class": entry["top_class"], "sub_class": entry["sub_class"],
            "top_class_cn": entry["top_class_cn"], "sub_class_cn": entry["sub_class_cn"],
            "mv_start": round(mv_start, 2), "mv_end": round(mv_end, 2),
            "delta": round(mv_end - mv_start, 2),
            "price_effect": round(price_effect, 2), "trade_effect": round(trade_effect, 2),
            "transfer_effect": round(transfer_effect, 2), "income_effect": round(income_effect, 2),
            "residual": round(residual, 2), "dq_flag": dq_flag,
            "dq_reason": dq_reason, "dq_detail": dq_detail, "asset_count": 1,
        })

    if level == "asset":
        out_rows = sorted(asset_rows, key=lambda r: r["mv_end"], reverse=True)
    else:
        if level == "top_class":
            key_fn = lambda r: r["top_class"] or "Unclassified"  # noqa: E731
            top_class_fn = key_fn
            key_cn_fn = lambda r: r["top_class_cn"]  # noqa: E731
            top_class_cn_fn = key_cn_fn
        elif level == "total":
            key_fn = lambda r: "Total"  # noqa: E731
            top_class_fn = lambda r: None  # noqa: E731
            # 'Total' is a literal, not a taxonomy_classes value — no cn companion.
            key_cn_fn = lambda r: None  # noqa: E731
            top_class_cn_fn = lambda r: None  # noqa: E731
        else:  # sub_class (default)
            key_fn = lambda r: r["sub_class"] or "Unclassified"  # noqa: E731
            top_class_fn = lambda r: r["top_class"] or "Unclassified"  # noqa: E731
            key_cn_fn = lambda r: r["sub_class_cn"]  # noqa: E731
            top_class_cn_fn = lambda r: r["top_class_cn"]  # noqa: E731

        groups: dict = {}
        for r in asset_rows:
            key = key_fn(r)
            top_class = top_class_fn(r)
            g = groups.setdefault((key, top_class), {
                "key": key, "top_class": top_class,
                "key_cn": key_cn_fn(r), "top_class_cn": top_class_cn_fn(r),
                "mv_start": 0.0, "mv_end": 0.0, "price_effect": 0.0, "trade_effect": 0.0,
                "transfer_effect": 0.0, "income_effect": 0.0, "residual": 0.0,
                "dq_flag": False, "dq_reason": None, "dq_detail": None, "asset_count": 0,
            })
            g["mv_start"] += r["mv_start"]
            g["mv_end"] += r["mv_end"]
            g["price_effect"] += r["price_effect"]
            g["trade_effect"] += r["trade_effect"]
            g["transfer_effect"] += r["transfer_effect"]
            g["income_effect"] += r["income_effect"]
            g["residual"] += r["residual"]
            g["dq_flag"] = g["dq_flag"] or r["dq_flag"]
            g["asset_count"] += 1

        out_rows = []
        for g in groups.values():
            g["delta"] = g["mv_end"] - g["mv_start"]
            for f in ("mv_start", "mv_end", "delta", "price_effect", "trade_effect",
                      "transfer_effect", "income_effect", "residual"):
                g[f] = round(g[f], 2)
            out_rows.append(g)
        out_rows.sort(key=lambda r: r["mv_end"], reverse=True)

    totals = {
        "delta": round(sum(r["delta"] for r in asset_rows), 2),
        "price_effect": round(sum(r["price_effect"] for r in asset_rows), 2),
        "trade_effect": round(sum(r["trade_effect"] for r in asset_rows), 2),
        "transfer_effect": round(sum(r["transfer_effect"] for r in asset_rows), 2),
        "income_effect": round(sum(r["income_effect"] for r in asset_rows), 2),
        "residual": round(sum(r["residual"] for r in asset_rows), 2),
    }

    dq_flagged_assets = [r["asset_id"] for r in asset_rows if r["dq_flag"]]

    computed_at_row = db.execute(
        "SELECT MAX(computed_at) FROM attribution_monthly WHERE month >= ? AND month <= ?",
        [str(m_start), str(m_end_range)],
    ).fetchone()
    computed_at = computed_at_row[0].isoformat() if computed_at_row and computed_at_row[0] else None

    return {
        "month": f"{m_start.strftime('%Y-%m')}..{m_end_range.strftime('%Y-%m')}",
        "level": level,
        "rows": out_rows,
        "totals": totals,
        "dq_flagged_assets": dq_flagged_assets,
        "computed_at": computed_at,
    }


def get_asset_history(db, asset_id: str, months: int = 6) -> Optional[dict]:
    """Per-asset attribution history, newest month first. Returns None if the
    asset has never appeared in attribution_monthly (caller maps to 404)."""
    exists = db.execute(
        "SELECT 1 FROM attribution_monthly WHERE asset_id = ? LIMIT 1", [asset_id]
    ).fetchone()
    if not exists:
        return None

    rows = db.execute(
        """
        SELECT a.month, a.mv_start, a.mv_end, a.price_effect, a.trade_effect,
               a.transfer_effect, a.income_effect, a.residual, a.dq_flag, r.display_name
        FROM attribution_monthly a
        LEFT JOIN asset_registry r ON a.asset_id = r.canonical_id
        WHERE a.asset_id = ? AND a.month >= ?
        ORDER BY a.month DESC
        LIMIT ?
        """,
        [asset_id, str(HISTORY_FLOOR_MONTH), months],
    ).fetchall()

    out_months = []
    asset_name = None
    for idx, row in enumerate(rows):
        (mo, mv_start, mv_end, price_e, trade_e, transfer_e, income_e, residual, dq_flag, name) = row
        asset_name = asset_name or name
        month_date = mo if isinstance(mo, date) else date.fromisoformat(str(mo))
        dq_flag = bool(dq_flag)
        dq_reason = None
        dq_detail = None
        if dq_flag:
            reason = _derive_dq_reason(db, asset_id, month_date)
            dq_reason = reason["dq_reason"]
            dq_detail = reason["dq_detail"]
        item = {
            "key": asset_id, "asset_id": asset_id, "asset_name": name,
            "mv_start": float(mv_start), "mv_end": float(mv_end),
            "delta": float(mv_end) - float(mv_start),
            "price_effect": float(price_e), "trade_effect": float(trade_e),
            "transfer_effect": float(transfer_e), "income_effect": float(income_e),
            "residual": float(residual), "dq_flag": dq_flag,
            "dq_reason": dq_reason, "dq_detail": dq_detail,
        }
        if idx == 0:
            # Expanded month — include underlying qty events.
            raw = [r for r in compute_month_raw(db, month_date) if r["asset_id"] == asset_id]
            events = raw[0]["events"] if raw else []
            item["events"] = [
                {
                    "date": e["date"].isoformat() if hasattr(e["date"], "isoformat") else str(e["date"]),
                    "type": e["type"], "qty": e["quantity"], "amount_cny": e["amount_net"],
                    "price": e["price_unit"],
                }
                for e in events
            ]
        out_months.append(item)

    return {"asset_id": asset_id, "asset_name": asset_name, "months": out_months}


def get_summary(db, months: int = 12) -> dict:
    """Multi-month totals series + savings/invest-ratio metrics from
    cash_flow_tags (Rule: savings metrics derived ONLY from cash_flow_tags).

    Top-level (not per-month) trailing-12m contribution/savings fields —
    `savings_rate_ttm`, `net_external_ttm`, `internal_realloc_ttm`,
    `gross_invested_ttm`, `income_ttm`, `window_start_month`,
    `window_end_month` — are sourced from
    `src.services.investment_contributions.contributions_summary_v2`, the
    月度收支-derived authority for portfolio contributions/savings (plan
    2026-07-20-investment-contributions-savings.md §Reconciliation). This is
    a DIFFERENT source than the per-month `flows`/`invest_ratio` fields below
    (still cash_flow_tags-derived, unchanged) — the two must never be summed
    together (§Reconciliation). Each month's `savings_rate` stays None
    (per-month savings rate is proven meaningless on this data — see the
    module docstring on investment_contributions.py); only the trailing
    aggregate is filled in.
    """
    rows = db.execute(
        """
        SELECT month, SUM(mv_end) - SUM(mv_start) AS delta, SUM(price_effect), SUM(trade_effect),
               SUM(transfer_effect), SUM(income_effect), SUM(residual),
               COUNT(*) FILTER (WHERE dq_flag) AS dq_count
        FROM attribution_monthly
        WHERE month >= ?
        GROUP BY month
        ORDER BY month DESC
        LIMIT ?
        """,
        [str(HISTORY_FLOOR_MONTH), months],
    ).fetchall()

    out = []
    for (mo, delta, price_e, trade_e, transfer_e, income_e, residual, dq_count) in rows:
        mo_date = mo if isinstance(mo, date) else date.fromisoformat(str(mo))
        mo_start = _month_start(mo_date)
        next_month = date(mo_start.year + 1, 1, 1) if mo_start.month == 12 else date(mo_start.year, mo_start.month + 1, 1)

        # n counts tags of ANY classification: a month whose only tags are
        # internal transfers HAS been classified — its external flows are a
        # genuine ¥0, not "unknown" (owner review 2026-07-20: Apr/May showed
        # "no classified flows yet" despite classified internal transfers).
        flow_row = db.execute(
            """
            SELECT
                SUM(amount_cny) FILTER (WHERE classification = 'external_contribution' AND amount_cny > 0) AS ext_in,
                SUM(amount_cny) FILTER (WHERE classification = 'external_contribution' AND amount_cny < 0) AS ext_out,
                COUNT(*) AS n
            FROM cash_flow_tags
            WHERE flow_date >= ? AND flow_date < ?
            """,
            [str(mo_start), str(next_month)],
        ).fetchone()
        ext_in = float(flow_row[0] or 0.0)
        ext_out = float(flow_row[1] or 0.0)
        n_classified = int(flow_row[2] or 0)

        if n_classified == 0:
            # Item E (2026-07-20 owner review): NO classified cash_flow_tags
            # rows this month means "nothing classified yet", not "zero
            # flows" — {external_in: 0, ...} was misleading (reads as "no
            # money moved" when it actually means "we don't know"). null
            # lets the frontend render "—" instead of a false ¥0.
            flows = None
            invest_ratio = None
        else:
            flows = {"external_in": ext_in, "external_out": ext_out, "net_external": ext_in + ext_out}
            # invest_ratio: net external flow into rebalanceable assets / total
            # external inflows. Tags are nk:-keyed post-V81 (a raw id JOIN
            # silently drops them — same orphan bug class V81 fixed), so
            # resolve each tag's asset via the nk key itself (it embeds
            # asset_id) or, for legacy id-keyed tags, a transactions lookup.
            # WS-2 (plan 2026-07-20-fs-cash-flows-attribution.md): this
            # numerator query is scoped to source_table='transactions' ON
            # PURPOSE — FS-cash contributions (CASH_*/Wealth_CMB) are cash
            # sitting in a bank/deposit account, never a rebalanceable asset,
            # so they belong in ext_in (the denominator, via flow_row above,
            # which is NOT source_table-scoped) but must NOT count toward
            # reb_in. An uninvested FS-cash deposit correctly LOWERS
            # invest_ratio — that is the intended semantics, not a bug.
            from src.services.north_star_flows import parse_natural_key

            tag_rows = db.execute(
                """
                SELECT source_row_key, amount_cny FROM cash_flow_tags
                WHERE source_table = 'transactions'
                  AND classification = 'external_contribution'
                  AND flow_date >= ? AND flow_date < ? AND amount_cny > 0
                """,
                [str(mo_start), str(next_month)],
            ).fetchall()
            amounts_by_asset: dict = {}
            for row_key, amount_cny in tag_rows:
                parsed = parse_natural_key(row_key) if isinstance(row_key, str) else None
                if parsed is not None:
                    asset_id = parsed["asset_id"]
                else:
                    hit = db.execute(
                        "SELECT asset_id FROM transactions WHERE CAST(id AS VARCHAR) = ?",
                        [str(row_key)],
                    ).fetchone()
                    if hit is None:
                        continue  # orphaned legacy tag — unresolvable, excluded
                    asset_id = hit[0]
                amounts_by_asset[asset_id] = amounts_by_asset.get(asset_id, 0.0) + float(amount_cny or 0.0)
            reb_in = 0.0
            if amounts_by_asset:
                placeholders = ", ".join("?" for _ in amounts_by_asset)
                reb_assets = {
                    r[0] for r in db.execute(
                        f"""
                        SELECT r.canonical_id FROM asset_registry r
                        LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
                        WHERE r.canonical_id IN ({placeholders})
                          AND COALESCE(tc.is_rebalanceable, r.is_rebalanceable, TRUE) = TRUE
                        """,
                        list(amounts_by_asset.keys()),
                    ).fetchall()
                }
                reb_in = sum(v for k, v in amounts_by_asset.items() if k in reb_assets)
            invest_ratio = (reb_in / ext_in) if ext_in > 0 else None

        out.append({
            "month": mo_start.strftime("%Y-%m"),
            "delta": float(delta or 0), "price_effect": float(price_e or 0),
            "trade_effect": float(trade_e or 0), "transfer_effect": float(transfer_e or 0),
            "income_effect": float(income_e or 0), "residual": float(residual or 0),
            "flows": flows, "savings_rate": None,  # TODO: no income-basis source wired yet
            "invest_ratio": invest_ratio, "dq_count": int(dq_count or 0),
        })

    # Trailing-12m contribution/savings authority (§Reconciliation): sourced
    # from income_expense_monthly via contributions_summary_v2, NOT from the
    # cash_flow_tags flows/invest_ratio above. Guard: an empty
    # income_expense_monthly table (e.g. a fresh/test DB) must still let
    # get_summary return cleanly with defaulted fields; a genuine exception
    # from a non-empty table is logged and re-raised, never swallowed.
    # WS-G (2026-08-01): savings_rate_ttm and investment_rate_ttm are DIFFERENT
    # metrics and both ship — `savings_rate` is everything not spent
    # ((income_basis - expense_basis)/income_basis), `investment_rate` is the
    # share that actually reached an investment account. Their difference is
    # undeployed_cash_ttm. A consumer that shows one under the other's label is
    # wrong by ~19pp on live data; see docs/decisions/ADR-025 Amendment 2.
    # Keys here MUST stay identical to the ttm_fields block below — a default
    # set that is missing a key changes the response shape on empty data.
    ttm_defaults = {
        "savings_rate_ttm": None,
        "investment_rate_ttm": None,
        "income_basis_ttm": 0.0,
        "expense_basis_ttm": 0.0,
        "undeployed_cash_ttm": 0.0,
        "net_external_ttm": 0.0,
        "rsu_retained_ttm": 0.0,
        "internal_realloc_ttm": 0.0,
        "gross_invested_ttm": 0.0,
        "income_ttm": 0.0,
        "window_start_month": None,
        "window_end_month": None,
    }
    try:
        from src.services.investment_contributions import contributions_summary_v2

        inv_summary = contributions_summary_v2(db)
        ttm_fields = {
            "savings_rate_ttm": inv_summary["savings_rate_ttm"],
            "investment_rate_ttm": inv_summary["investment_rate_ttm"],
            "income_basis_ttm": inv_summary["income_basis_ttm"],
            "expense_basis_ttm": inv_summary["expense_basis_ttm"],
            "undeployed_cash_ttm": inv_summary["undeployed_cash_ttm"],
            "net_external_ttm": inv_summary["net_external_ttm"],
            "rsu_retained_ttm": inv_summary["rsu_retained_ttm"],
            "internal_realloc_ttm": inv_summary["internal_realloc_ttm"],
            "gross_invested_ttm": inv_summary["gross_invested_ttm"],
            "income_ttm": inv_summary["income_ttm"],
            "window_start_month": inv_summary["window_start_month"],
            "window_end_month": inv_summary["window_end_month"],
        }
    except Exception:
        logger.exception("contributions_summary_v2 failed in get_summary")
        row_count = db.execute("SELECT COUNT(*) FROM income_expense_monthly").fetchone()[0]
        if row_count == 0:
            ttm_fields = ttm_defaults
        else:
            raise

    return {"months": out, **ttm_fields}
