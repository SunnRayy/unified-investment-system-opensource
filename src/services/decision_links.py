"""Insight↔trade attribution link management for the decision feedback loop.

This module owns the insight_trade_links table — persisted links that replace
the brittle runtime LOWER(suggestion_source)=LOWER(ai_model) ±3-day join that
previously had to be re-derived on every cross-check audit.

Auto links (link_type='auto_source') mirror the existing source-match join logic.
Manual links (link_type='manual') allow user correction with confidence=1.0.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_MAX_WINDOW_DAYS = 3


def recompute_auto_links(
    db: Any,
    insight_id: int | None = None,
    trade_id: int | None = None,
) -> int:
    """Upsert auto_source links from the ±3-day/source-match heuristic.

    Idempotent: uses ON CONFLICT DO NOTHING so running twice is safe.
    Returns the number of rows inserted (0 on pure idempotent re-run).
    """
    insight_filter = "AND i.id = ?" if insight_id is not None else ""
    trade_filter = "AND tl.id = ?" if trade_id is not None else ""
    params: list[Any] = []
    if insight_id is not None:
        params.append(insight_id)
    if trade_id is not None:
        params.append(trade_id)

    try:
        before = db.execute("SELECT COUNT(*) FROM insight_trade_links").fetchone()[0]
        db.execute(
            f"""
            INSERT INTO insight_trade_links (insight_id, trade_id, link_type, confidence)
            SELECT
                i.id AS insight_id,
                tl.id AS trade_id,
                'auto_source' AS link_type,
                CAST(
                    GREATEST(0.0, 1.0 - CAST(ABS(date_diff('day', i.insight_date, tl.log_date)) AS DOUBLE) / 4.0)
                    AS DECIMAL(3,2)
                ) AS confidence
            FROM insights i
            JOIN trade_logs tl
                ON tl.suggestion_source IS NOT NULL
                AND LOWER(TRIM(tl.suggestion_source)) = LOWER(TRIM(i.ai_model))
                AND ABS(date_diff('day', i.insight_date, tl.log_date)) <= {_MAX_WINDOW_DAYS}
            WHERE TRUE
              {insight_filter}
              {trade_filter}
            ON CONFLICT (insight_id, trade_id) DO NOTHING
            """,
            params,
        )
        after = db.execute("SELECT COUNT(*) FROM insight_trade_links").fetchone()[0]
        inserted = after - before
        if inserted > 0:
            logger.info("recompute_auto_links: inserted %s new links", inserted)
        return inserted
    except Exception as e:
        logger.error("recompute_auto_links failed: %s", e)
        return 0


def add_manual_link(
    db: Any,
    insight_id: int,
    trade_id: int,
    rationale: str = "",
) -> int | None:
    """Insert a manual insight↔trade link with confidence=1.0.

    Idempotent: if the (insight_id, trade_id) pair already exists the row is
    left unchanged and the existing id is returned.
    Returns the link id, or None on error.
    """
    try:
        db.execute(
            """
            INSERT INTO insight_trade_links (insight_id, trade_id, link_type, confidence, rationale)
            VALUES (?, ?, 'manual', 1.0, ?)
            ON CONFLICT (insight_id, trade_id) DO NOTHING
            """,
            [insight_id, trade_id, rationale or None],
        )
        row = db.execute(
            "SELECT id FROM insight_trade_links WHERE insight_id = ? AND trade_id = ? LIMIT 1",
            [insight_id, trade_id],
        ).fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error("add_manual_link(%s, %s) failed: %s", insight_id, trade_id, e)
        return None


def remove_link(db: Any, link_id: int) -> None:
    """Delete an insight_trade_links row by id. Silent if not found."""
    try:
        db.execute("DELETE FROM insight_trade_links WHERE id = ?", [link_id])
    except Exception as e:
        logger.warning("remove_link(%s) failed: %s", link_id, e)
