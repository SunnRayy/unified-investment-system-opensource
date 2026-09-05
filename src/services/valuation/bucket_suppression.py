"""Bucket-aware valuation signal suppression (PRD 2026-07-07 F4.5, Batch B5).

Serving-path transform applied to `GET /valuation/snapshot/latest` rows,
*after* the F4.2 canonical-underlying dedup step
(src/services/valuation/canonical_underlyings.py::apply_canonical_signal_dedup).

- compliance-bucket assets (e.g. RSU_AMZN sells) never display a valuation
  signal — temptation without decision value while under mandatory
  liquidation/deadline machinery. Their row instead carries
  `display_mode='execution_progress'`. No structured quota/deadline-tracking
  table exists yet in this codebase (grepped for 'quota'/'deadline' entities —
  none found beyond `trade_logs.memo_id` free text), so `execution_progress`
  is `None` rather than a fabricated value (Cross-cutting Req 3).
- ratio-bucket assets (gold, IBIT, FBTC) forbid valuation/price judgment by
  rule (same rationale as F2's exclusion in src/services/value_trap.py) and
  instead display `display_mode='band_position'` — the asset's current
  portfolio share vs its target band. `target_band` is `None`: the target
  allocation table lives in the strategy/allocation engine, not in
  valuation_snapshots, and wiring it in is out of scope for this batch (PRD
  non-goal: "no change to target allocation... logic"); `current_pct` alone
  is still real, non-fabricated data.
- everything else keeps `display_mode='signal'` and its existing fields
  unchanged.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.database.connector import DatabaseConnector
from src.services.rule_buckets import classify_asset_bucket
from src.services.verification_config import (
    VALUE_BUCKET,
    VerificationConfig,
    load_verification_config,
)

logger = logging.getLogger(__name__)

_SUPPRESSED_BUCKETS = ("compliance", "ratio")

# Fields nulled out for suppressed buckets — the same signal-classification
# fields the F4.2 dedup mirrors (canonical_underlyings.py::_SIGNAL_FIELDS),
# minus canonical_underlying/signal_source_series which are dedup-lineage
# metadata, not signal values themselves.
_SIGNAL_FIELDS_TO_NULL = (
    "valuation_signal",
    "signal_basis",
    "percentile_value",
    "percentile_metric",
    "pct_years",
)


def _resolve_bucket(row: dict[str, Any], cfg: VerificationConfig) -> str:
    """Resolve a valuation row's rule_bucket, trying asset_id then ticker.

    Production rows carry a canonical `asset_id` (e.g. 'RSU_AMZN',
    'US_STK_IBIT') written by the valuation collector from the same
    canonical_id holdings/asset_registry use — bucket_map patterns are
    substrings of that canonical form. `ticker` (the raw instrument symbol,
    e.g. 'AMZN', 'IBIT') is tried as a fallback so a row missing/mismatching
    asset_id still resolves correctly; classify_asset_bucket already does
    case-insensitive substring matching, so this is a direct reuse, not a
    new matching algorithm.
    """
    for candidate in (row.get("asset_id"), row.get("ticker")):
        if not candidate:
            continue
        bucket = classify_asset_bucket(str(candidate), cfg=cfg)
        if bucket != VALUE_BUCKET:
            return bucket
    return VALUE_BUCKET


def _latest_holdings_pct_by_asset(db: DatabaseConnector) -> dict[str, float]:
    """asset_id -> % share of total portfolio market value, per-asset latest
    non-shadow holdings snapshot (Rule 3: GROUP BY CTE, never a global MAX)."""
    rows = db.execute(
        """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_date
            FROM holdings WHERE is_shadow = FALSE
            GROUP BY asset_id
        ),
        latest_holdings AS (
            SELECT h.asset_id, SUM(h.market_value) AS market_value
            FROM holdings h
            JOIN latest_per_asset lpa
                ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            WHERE h.is_shadow = FALSE
            GROUP BY h.asset_id
        )
        SELECT asset_id, market_value, SUM(market_value) OVER () AS total_market_value
        FROM latest_holdings
        """
    ).fetchall()

    result: dict[str, float] = {}
    for asset_id, market_value, total_market_value in rows:
        if not total_market_value or total_market_value <= 0 or market_value is None:
            continue
        result[str(asset_id)] = float(market_value) / float(total_market_value) * 100.0
    return result


def apply_bucket_signal_suppression(
    db: DatabaseConnector,
    rows: list[dict[str, Any]],
    config_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return *rows* with compliance/ratio-bucket signal fields suppressed.

    Never raises — matches the canonical-dedup helper's contract (a config or
    bucket-resolution problem degrades a row to display_mode='signal'
    unchanged rather than breaking the endpoint).
    """
    cfg = load_verification_config(config_path) if config_path else load_verification_config()

    needs_holdings = any(
        _resolve_bucket(row, cfg) == "ratio" for row in rows
    )
    pct_by_asset = _latest_holdings_pct_by_asset(db) if needs_holdings else {}

    for row in rows:
        try:
            bucket = _resolve_bucket(row, cfg)
        except Exception as exc:  # pragma: no cover — defensive, mirrors dedup helper
            logger.warning("bucket_suppression: failed to resolve bucket for row: %s", exc)
            row.setdefault("display_mode", "signal")
            continue

        if bucket == "compliance":
            for field_name in _SIGNAL_FIELDS_TO_NULL:
                row[field_name] = None
            row["display_mode"] = "execution_progress"
            row["execution_progress"] = None
        elif bucket == "ratio":
            for field_name in _SIGNAL_FIELDS_TO_NULL:
                row[field_name] = None
            row["display_mode"] = "band_position"
            asset_id = row.get("asset_id")
            current_pct = pct_by_asset.get(str(asset_id)) if asset_id else None
            row["band_position"] = {"current_pct": current_pct, "target_band": None}
        else:
            row.setdefault("display_mode", "signal")

    return rows
