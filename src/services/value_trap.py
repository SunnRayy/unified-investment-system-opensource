"""Loss-side mandatory review trigger scan (PRD 2026-07-07 F2.1/F2.2, Batch B3).

``scan_value_traps`` walks the *current* holdings (per-asset latest snapshot —
Rule 3: a GROUP BY CTE, never a single global ``MAX(snapshot_date)``) and opens
or refreshes a ``value_trap_reviews`` row for every asset whose lifetime
unrealized return has crossed the configured loss threshold, unless the asset
is compliance- or ratio-bucketed (those buckets forbid valuation/price
judgment by rule — PRD F2.1).

Unrealized-return formula: this module reuses the exact functions the
holdings/WealthOS P&L view uses, rather than re-deriving the math —
``calculate_cost_basis_cny`` (src/services/currency.py) for the CNY-normalized
cost basis and ``calculate_unrealized_pl_values`` (src/api/routes/performance.py)
for the CNY unrealized P&L, both already used by
``GET /wealthos/assets`` (src/api/routes/data.py). unrealized_return_pct is
then ``unrealized_cny / cost_basis_cny * 100`` — the same denominator/
numerator pair the WealthOS "Active" branch computes, just expressed as a
percentage instead of a currency amount. Assets with cost_basis_cny <= 0
(cash/RSU-zero-cost rows, or anything filtered out upstream) are skipped, not
divided-by-zero or reported as a fake -100%.

Staleness (F4.4, R2-1): asset freshness is now class-dependent via
src/services/freshness.py.  Three ordered gates apply before evaluation:
  1. Cash-like exempt gate  — is_cash_like() → counted as exempt_cash_like;
     skipped BEFORE the bucket or freshness check.
  2. Bucket exclusion gate  — compliance/ratio buckets forbid price judgment.
  3. Freshness gate          — freshness_class_for() + is_fresh() per asset.
     Stale 'fast'-class assets (market-priced instruments) upsert an open
     data_fix row (idempotent) and appear in deferred_assets for the frontend
     Deferred tab (R2-1 §4).  Stale 'slow' or 'none' assets are deferred
     without a data_fix (no automated feed to repair).

Escalation ladder (F2.2): after a 'hold_with_thesis' ruling, the API route
(src/api/routes/value_trap.py) stores next_trigger_threshold_pct = ruling
threshold - cfg.value_trap.escalation_step_pp (e.g. -25 -> -35). The next scan
re-arms at that stored threshold instead of the config default, until a fresh
hit re-opens a new review row (history is preserved as separate rows, never
overwritten — F2.3 "re-open semantics = new row").
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from src.database.connector import DatabaseConnector
from src.services.currency import get_today_usd_cny_rate
from src.services.freshness import freshness_class_for, is_cash_like
from src.services.metric_governance import log_ruling_deferred
from src.services.rule_buckets import classify_asset_bucket
from src.services.verification_config import VerificationConfig, load_verification_config

logger = logging.getLogger(__name__)

# Buckets that forbid price/valuation judgment by rule (PRD F2.1) — never
# evaluated for a value-trap trigger.
_EXCLUDED_BUCKETS = ("compliance", "ratio")


def _latest_holdings_rows(db: DatabaseConnector) -> list:
    """Per-asset latest non-shadow holdings snapshot.

    MANDATORY Rule 3: per-asset latest via a GROUP BY CTE — never a single
    global MAX(snapshot_date), which would silently drop assets whose most
    recent reader sync landed on an earlier calendar date than others.
    """
    return db.execute(
        """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_date
            FROM holdings WHERE is_shadow = FALSE
            GROUP BY asset_id
        )
        SELECT
            h.asset_id,
            MAX(h.asset_name)                          AS asset_name,
            COALESCE(MAX(r.asset_class), 'Unknown')    AS asset_class,
            SUM(h.market_value)                        AS market_value,
            SUM(h.quantity)                             AS quantity,
            MAX(h.cost_price_unit)                      AS cost_price_unit,
            MAX(h.market_price_unit)                    AS market_price_unit,
            MAX(h.currency)                             AS currency,
            MAX(lpa.latest_date)                        AS latest_snapshot_date,
            MAX(h.price_updated_at)                     AS price_updated_at
        FROM holdings h
        JOIN latest_per_asset lpa
            ON h.asset_id = lpa.asset_id
           AND h.snapshot_date = lpa.latest_date
        LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
        WHERE h.is_shadow = FALSE
        GROUP BY h.asset_id
        HAVING SUM(h.market_value) > 0 AND SUM(h.quantity) > 0
        """
    ).fetchall()


def _unrealized_return_pct(row: tuple, today_fx: float) -> Optional[float]:
    """Unrealized return % for one holdings row, or None if cost basis <= 0.

    Delegates to ``position_lots.unrealized_from_holdings_row`` — the single
    shared formula used by both this scan and the /wealthos/assets endpoint.
    No duplicated math across modules.  See position_lots.py docstring for the
    basis explanation (holdings.cost_price_unit == FIFO weighted avg cost).
    """
    from src.services.position_lots import unrealized_from_holdings_row

    (_asset_id, _asset_name, asset_class, market_value, quantity,
     cost_price_unit, market_price_unit, currency, _latest_snapshot_date,
     _price_updated_at) = row

    return unrealized_from_holdings_row(
        market_value=float(market_value or 0.0),
        quantity=float(quantity or 0.0),
        cost_price_unit=float(cost_price_unit or 0.0),
        market_price_unit=float(market_price_unit or 0.0),
        currency=str(currency or "CNY"),
        top_class=str(asset_class or ""),
        sub_class=str(asset_class or ""),
        today_fx=today_fx,
    )


def _latest_review(db: DatabaseConnector, asset_id: str) -> Optional[tuple]:
    """Most recent value_trap_reviews row for an asset (any status), or None."""
    return db.execute(
        """
        SELECT id, status, trigger_threshold_pct, next_trigger_threshold_pct
        FROM value_trap_reviews
        WHERE asset_id = ?
        ORDER BY opened_at DESC, id DESC
        LIMIT 1
        """,
        [asset_id],
    ).fetchone()


def _close_stale_data_fix_if_open(db: DatabaseConnector, asset_id: str) -> None:
    """Auto-close an open 'stale price feed: {asset_id}' data_fix on freshness recovery (R2-1).

    When a previously-deferred 'fast'-class asset PASSES the freshness gate on a
    later scan, its open stale-price data_fix is resolved automatically (status →
    'done', closed_at = now).  This prevents the deferred queue from accumulating
    phantom entries after the underlying feed is repaired.

    Only 'fast'-class assets generate data_fix rows (slow/none have no automated
    feed to repair), so this function is only called for 'fast'-class assets.
    """
    title = f"stale price feed: {asset_id}"
    existing = db.execute(
        "SELECT id FROM data_fixes WHERE title = ? AND status = 'open' LIMIT 1",
        [title],
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE data_fixes SET status = 'done', closed_at = CURRENT_TIMESTAMP WHERE id = ?",
            [existing[0]],
        )
        logger.info("auto-closed stale data_fix id=%s for %s (freshness recovered)", existing[0], asset_id)


def _upsert_stale_data_fix(
    db: DatabaseConnector,
    asset_id: str,
    price_date: Optional[date],
    freshness_class: str,
) -> Optional[int]:
    """Idempotent upsert of a data_fix row for a stale-price asset (R2-1).

    Returns the data_fix id (existing open row or newly inserted).
    Only called for 'fast'-class assets — 'slow'/'none' have no automated
    feed to repair, so no data_fix is warranted.
    """
    title = f"stale price feed: {asset_id}"
    existing = db.execute(
        "SELECT id FROM data_fixes WHERE title = ? AND status = 'open' LIMIT 1",
        [title],
    ).fetchone()
    if existing:
        return int(existing[0])

    # due_at: fast=7d, slow=30d (mirrors the F4.6 _default_due_at logic in governance.py)
    due_days = 7 if freshness_class == "fast" else 30
    due_at = datetime.now() + timedelta(days=due_days)
    price_str = str(price_date) if price_date else "unknown"
    db.execute(
        """
        INSERT INTO data_fixes (title, description, metric_key, due_at)
        VALUES (?, ?, NULL, ?)
        """,
        [title, f"stale price feed: {asset_id} last {price_str}", due_at],
    )
    row = db.execute(
        "SELECT id FROM data_fixes WHERE title = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
        [title],
    ).fetchone()
    return int(row[0]) if row else None


def scan_value_traps(db: DatabaseConnector, cfg: Optional[VerificationConfig] = None) -> dict:
    """Scan current holdings for loss-side mandatory review triggers (F2.1/F2.2).

    Writes/refreshes rows in value_trap_reviews. Caller is responsible for
    calling mark_dirty() after this returns (API route concern, not this
    module's — see src/api/routes/value_trap.py).

    Returns:
        {
            "scanned": int,
            "hits": int,
            "opened": int,
            "refreshed": int,
            "skipped_bucket": int,
            "exempt_cash_like": int,          # R2-1: cash-like exempt (not deferred)
            "skipped_no_cost": int,
            "deferred_unreliable": int,
            "evaluated": int,
            "deferred_assets": [              # R2-1: per-deferred-asset detail
                {
                    "asset_id": str,
                    "price_date": str | None,
                    "freshness_class": str,
                    "data_fix_id": int | None,     # only for 'fast'-class assets
                    "data_fix_due_at": str | None, # ISO date (YYYY-MM-DD); None if no data_fix
                }
            ],
        }
    """
    if cfg is None:
        cfg = load_verification_config()

    try:
        today_fx = get_today_usd_cny_rate()
    except Exception as e:  # pragma: no cover — network/service failure fallback
        logger.warning("scan_value_traps: FX rate lookup failed, using 7.0 fallback: %s", e)
        today_fx = 7.0

    default_threshold = cfg.value_trap.trigger_threshold_pct

    summary: dict = {
        "scanned": 0,
        "hits": 0,
        "opened": 0,
        "refreshed": 0,
        "skipped_bucket": 0,
        "exempt_cash_like": 0,
        "skipped_no_cost": 0,
        "deferred_unreliable": 0,
        "evaluated": 0,
        "deferred_assets": [],
    }

    def _as_date(v: object) -> Optional[date]:
        """Cast datetime/date/None to date or None."""
        if v is None:
            return None
        return v.date() if isinstance(v, datetime) else v  # type: ignore[return-value]

    today = date.today()

    for row in _latest_holdings_rows(db):
        asset_id = row[0]
        asset_name = row[1]
        asset_class = row[2]
        latest_snapshot_date = row[8]
        price_updated_at_raw = row[9]
        summary["scanned"] += 1

        # ── Gate 1: Cash-like exemption (R2-1) ───────────────────────────
        # Cash-like assets (deposits, money-market, bank wealth) are exempt
        # BEFORE the bucket check and BEFORE the freshness check.  They are
        # never deferred; they are simply not part of the loss-trigger universe.
        if is_cash_like(asset_id, str(asset_class), cfg=cfg):
            summary["exempt_cash_like"] += 1
            continue

        # ── Gate 2: Bucket exclusion (PRD F2.1) ──────────────────────────
        bucket = classify_asset_bucket(asset_id, cfg=cfg)
        if bucket in _EXCLUDED_BUCKETS:
            summary["skipped_bucket"] += 1
            continue

        # ── Gate 3: Freshness gate (F4.4, R2-1) ──────────────────────────
        # price_date = GREATEST(snapshot_date, price_updated_at).
        # Uses per-asset freshness_class (fast/slow/none) to determine the
        # staleness threshold; 'none'-class assets always pass (no feed defined).
        candidates = [
            d for d in (_as_date(latest_snapshot_date), _as_date(price_updated_at_raw))
            if d is not None
        ]
        price_date = max(candidates) if candidates else None

        fc = freshness_class_for(asset_id, str(asset_class), cfg=cfg)
        # Replicate is_fresh() logic inline so we can pass the already-computed today
        if fc == "none":
            fresh = True
        elif price_date is None:
            fresh = False
        elif fc == "fast":
            fresh = (today - price_date).days <= cfg.staleness.fast_days
        else:  # slow
            fresh = (today - price_date).days <= cfg.staleness.slow_days

        if not fresh:
            summary["deferred_unreliable"] += 1
            log_ruling_deferred(db, "holdings_snapshot", f"value_trap:{asset_id}")

            # Auto data_fix for genuinely evaluable assets with a broken feed (R2-1).
            # Only 'fast'-class assets have an automated price feed to repair.
            # 'slow' and 'none' assets have no automated feed — no data_fix warranted.
            data_fix_id: Optional[int] = None
            data_fix_due_at: Optional[str] = None
            if fc == "fast":
                data_fix_id = _upsert_stale_data_fix(db, asset_id, price_date, fc)
                if data_fix_id:
                    df_row = db.execute(
                        "SELECT due_at FROM data_fixes WHERE id = ? LIMIT 1",
                        [data_fix_id],
                    ).fetchone()
                    if df_row and df_row[0]:
                        # Normalise to YYYY-MM-DD string for the frontend
                        raw = df_row[0]
                        data_fix_due_at = str(raw)[:10]

            summary["deferred_assets"].append({
                "asset_id": asset_id,
                "price_date": str(price_date) if price_date else None,
                "freshness_class": fc,
                "data_fix_id": data_fix_id,
                "data_fix_due_at": data_fix_due_at,  # R2-1: for Deferred tab violation styling
            })
            continue

        # Asset passed the freshness gate — auto-close any previously opened
        # stale-price data_fix so resolved feeds don't linger in the backlog.
        # Only 'fast'-class assets can have such a data_fix.
        if fc == "fast":
            _close_stale_data_fix_if_open(db, asset_id)

        return_pct = _unrealized_return_pct(row, today_fx)
        if return_pct is None:
            summary["skipped_no_cost"] += 1
            continue

        summary["evaluated"] += 1

        latest = _latest_review(db, asset_id)
        if latest is not None and latest[1] == "open":
            # Already open — re-check against the threshold that opened it.
            effective_threshold = float(latest[2])
        elif latest is not None and latest[1] == "ruled" and latest[3] is not None:
            # Ruled with an escalation-ladder re-arm threshold (F2.2).
            effective_threshold = float(latest[3])
        else:
            effective_threshold = default_threshold

        if return_pct > effective_threshold:
            continue  # not a hit

        summary["hits"] += 1

        if latest is not None and latest[1] == "open":
            db.execute(
                """
                UPDATE value_trap_reviews
                SET unrealized_return_pct = ?, refreshed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                [return_pct, latest[0]],
            )
            summary["refreshed"] += 1
        else:
            # First-ever trigger, or a re-open (new row) after a prior 'ruled'
            # review whose next_trigger_threshold_pct was just crossed —
            # both cases preserve history as a new row (F2.3 re-open semantics).
            db.execute(
                """
                INSERT INTO value_trap_reviews
                    (asset_id, asset_name, status, trigger_threshold_pct,
                     unrealized_return_pct, opened_at)
                VALUES (?, ?, 'open', ?, ?, CURRENT_TIMESTAMP)
                """,
                [asset_id, asset_name, effective_threshold, return_pct],
            )
            summary["opened"] += 1

    return summary
