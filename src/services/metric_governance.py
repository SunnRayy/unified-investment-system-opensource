"""Metric governance service (PRD 2026-07-07 F4.3/F4.4/F4.6, Batch B5).

Three governance primitives, all read/write against the additive
``metric_catalog`` / ``data_fixes`` / ``ruling_deferred_events`` tables from
migration 012 (see src/database/migrations/012_metric_governance.sql):

- ``evaluate_reliability`` — F4.4 staleness policy + F4.6 overdue-fix
  auto-flip. Purely time-based (age vs freshness class, due_at vs now); never
  mutates state.
- ``log_ruling_deferred`` — F4.4 audit trail, called by trigger evaluators
  (F2 value_trap scan today; future ladder/band-position checks) whenever a
  metric/asset is skipped for unreliability.
- ``require_methodology`` — F4.3 ingestion gate for methodology-sensitive
  metrics (Buffett indicator, CSI500 PE, index PE percentiles): a write
  without a methodology tag raises rather than silently persisting an
  ambiguous series.
- ``get_metrics_overview`` — dashboard-facing summary joining metric_catalog
  with per-metric open/overdue data_fixes counts.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from src.database.connector import DatabaseConnector
from src.services.verification_config import VerificationConfig, load_verification_config

logger = logging.getLogger(__name__)

_FRESHNESS_CLASSES = ("fast", "slow")
_DEFAULT_FRESHNESS_CLASS = "slow"


def _coerce_datetime(value: Any) -> Optional[datetime]:
    """Best-effort coercion of a DB-returned timestamp value to datetime.

    DuckDB may hand back a native ``datetime``, a ``date``, or (via some test
    fixtures) an ISO string. Never raises — returns None on anything it can't
    parse, which callers treat as "no timestamp" (unreliable / not overdue).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "isoformat") and not isinstance(value, str):
        # date (no time component) — treat as midnight.
        try:
            return datetime.fromisoformat(value.isoformat())
        except ValueError:
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _get_metric_catalog_row(db: DatabaseConnector, metric_key: str) -> Optional[tuple]:
    return db.execute(
        "SELECT metric_key, source, methodology, freshness_class, methodology_sensitive, description "
        "FROM metric_catalog WHERE metric_key = ?",
        [metric_key],
    ).fetchone()


def evaluate_reliability(
    db: DatabaseConnector,
    metric_key: str,
    as_of: Optional[Any],
    now: Optional[datetime] = None,
) -> dict:
    """F4.4 staleness policy + F4.6 overdue-fix auto-flip.

    Returns {reliable: bool, reason: str|None, freshness_class: str,
    age_hours: float|None}.

    - Missing ``as_of`` -> unreliable, reason 'no as_of timestamp'.
    - Unknown ``metric_key`` (no metric_catalog row) -> treated as the 'slow'
      freshness class; reason notes the metric is unknown when stale/unreliable.
    - Stale when age > cfg.staleness.fast_hours (fast class) or
      > cfg.staleness.slow_days*24 (slow class).
    - Any OPEN data_fixes row for this metric_key with due_at < now ->
      unreliable, reason 'overdue data_fix #<id>' (checked even when the
      as_of-based staleness check alone would pass).
    """
    now = now or datetime.now()
    cfg: VerificationConfig = load_verification_config()

    catalog_row = _get_metric_catalog_row(db, metric_key)
    if catalog_row is not None:
        freshness_class = (catalog_row[3] or _DEFAULT_FRESHNESS_CLASS).lower()
        unknown_metric = False
    else:
        freshness_class = _DEFAULT_FRESHNESS_CLASS
        unknown_metric = True
    if freshness_class not in _FRESHNESS_CLASSES:
        freshness_class = _DEFAULT_FRESHNESS_CLASS

    as_of_dt = _coerce_datetime(as_of)
    if as_of_dt is None:
        reason = "no as_of timestamp"
        if unknown_metric:
            reason += f" (unknown metric_key '{metric_key}', treated as slow)"
        return {
            "reliable": False,
            "reason": reason,
            "freshness_class": freshness_class,
            "age_hours": None,
        }

    age_hours = max(0.0, (now - as_of_dt).total_seconds() / 3600.0)
    threshold_hours = (
        cfg.staleness.fast_hours if freshness_class == "fast" else cfg.staleness.slow_days * 24
    )

    reliable = True
    reason: Optional[str] = None
    if age_hours > threshold_hours:
        reliable = False
        reason = (
            f"stale: age {age_hours:.1f}h exceeds {freshness_class} threshold {threshold_hours}h"
        )
        if unknown_metric:
            reason += f" (unknown metric_key '{metric_key}', treated as slow)"

    # F4.6: overdue open data_fix auto-flips the metric to UNRELIABLE,
    # independent of the as_of staleness check above.
    overdue_fix = db.execute(
        """
        SELECT id FROM data_fixes
        WHERE metric_key = ? AND status = 'open' AND due_at < ?
        ORDER BY due_at ASC LIMIT 1
        """,
        [metric_key, now],
    ).fetchone()
    if overdue_fix is not None:
        reliable = False
        reason = f"overdue data_fix #{overdue_fix[0]}"

    if reliable and unknown_metric:
        # Still reliable, but flag that this metric has no catalog entry so
        # callers/tests can distinguish "known-fresh" from "unknown-but-fresh".
        reason = f"unknown metric_key '{metric_key}' (treated as slow, currently fresh)"

    return {
        "reliable": reliable,
        "reason": reason,
        "freshness_class": freshness_class,
        "age_hours": age_hours,
    }


def log_ruling_deferred(db: DatabaseConnector, metric_key: str, context: str) -> None:
    """F4.4 audit trail: record that a trigger evaluator deferred a ruling
    because the backing metric/data was unreliable."""
    db.execute(
        "INSERT INTO ruling_deferred_events (metric_key, context) VALUES (?, ?)",
        [metric_key, context],
    )


def require_methodology(db: DatabaseConnector, metric_key: str, methodology: Optional[str]) -> None:
    """F4.3 ingestion gate: raise ValueError if *metric_key* is methodology_sensitive
    in metric_catalog and *methodology* is falsy (None or empty string).

    A metric absent from metric_catalog, or present with
    methodology_sensitive=FALSE, is not gated — callers may write freely.
    """
    row = _get_metric_catalog_row(db, metric_key)
    if row is None:
        return
    methodology_sensitive = bool(row[4])
    if methodology_sensitive and not methodology:
        raise ValueError(
            f"metric '{metric_key}' is methodology_sensitive — a write without a "
            f"methodology tag is rejected (PRD F4.3)"
        )


def get_metrics_overview(db: DatabaseConnector) -> list[dict]:
    """metric_catalog rows enriched with per-metric open/overdue data_fixes counts."""
    rows = db.execute(
        "SELECT metric_key, source, methodology, freshness_class, "
        "methodology_sensitive, description, updated_at FROM metric_catalog "
        "ORDER BY metric_key"
    ).fetchall()

    now = datetime.now()
    overview = []
    for row in rows:
        metric_key = row[0]
        fix_rows = db.execute(
            "SELECT status, due_at FROM data_fixes WHERE metric_key = ?", [metric_key]
        ).fetchall()
        open_count = sum(1 for r in fix_rows if r[0] == "open")
        overdue_count = 0
        for status, due_at in fix_rows:
            if status != "open":
                continue
            due_dt = _coerce_datetime(due_at)
            if due_dt is not None and due_dt < now:
                overdue_count += 1

        overview.append({
            "metric_key": metric_key,
            "source": row[1],
            "methodology": row[2],
            "freshness_class": row[3],
            "methodology_sensitive": bool(row[4]),
            "description": row[5],
            "updated_at": str(row[6]) if row[6] is not None else None,
            "open_fix_count": open_count,
            "overdue_fix_count": overdue_count,
        })
    return overview
