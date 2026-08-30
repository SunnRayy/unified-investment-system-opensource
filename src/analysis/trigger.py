"""Trigger logic: should a fresh analysis be run for an asset?"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import duckdb

from src.database.connector import resolve_db_path

logger = logging.getLogger(__name__)

STALE_DAYS = 30


def should_trigger_analysis(
    asset_code: str,
    db_path: Optional[str] = None,
    db_conn: Optional[duckdb.DuckDBPyConnection] = None,
) -> tuple[bool, str]:
    """Return (should_trigger, reason).

    Triggers when:
      1. No prior analysis exists.
      2. Latest analysis is older than STALE_DAYS days.
      3. Valuation signal in valuation_snapshots differs from the one
         stored in the last analysis's portfolio_context JSON.
    """
    owned = False
    if db_conn is None:
        resolved = resolve_db_path(db_path or "data/unified.duckdb")
        db_conn = duckdb.connect(resolved, read_only=True)
        owned = True

    try:
        try:
            row = db_conn.execute(
                """
                SELECT created_at, llm_analysis, portfolio_context
                FROM asset_analyses
                WHERE UPPER(TRIM(asset_code)) = UPPER(TRIM(?))
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                [asset_code],
            ).fetchone()
        except Exception:
            return True, "no prior analysis — first run"

        if row is None:
            return True, "no prior analysis — first run"

        created_at_str, _llm_json, portfolio_ctx_str = row

        try:
            created_at = datetime.fromisoformat(str(created_at_str))
            # Compare in the same timezone space to avoid naive-vs-aware skew (UTC+8 host)
            if created_at.tzinfo is None:
                age_days = (datetime.now() - created_at).days
            else:
                age_days = (datetime.now(timezone.utc) - created_at).days
        except Exception:
            return True, "could not parse analysis timestamp — re-running"
        if age_days > STALE_DAYS:
            return True, f"analysis is {age_days} days old (stale after {STALE_DAYS} days)"

        # Check for valuation signal change (only when prior run stored a signal)
        try:
            ctx = json.loads(portfolio_ctx_str or "{}")
            last_signal = ctx.get("valuation_signal")
        except Exception:
            last_signal = None

        if last_signal:
            try:
                sig_row = db_conn.execute(
                    """
                    SELECT valuation_signal
                    FROM valuation_snapshots
                    WHERE (UPPER(TRIM(ticker)) = UPPER(TRIM(?))
                           OR UPPER(TRIM(asset_id)) = UPPER(TRIM(?)))
                      AND is_estimable = TRUE
                    ORDER BY snapshot_date DESC
                    LIMIT 1
                    """,
                    [asset_code, asset_code],
                ).fetchone()
                if sig_row and sig_row[0] and sig_row[0] != last_signal:
                    return True, f"valuation signal changed {last_signal}→{sig_row[0]}"
            except Exception as e:
                logger.debug("signal change check failed for %s: %s", asset_code, e)

        return False, f"analysis is {age_days} days old — still fresh"

    finally:
        if owned:
            try:
                db_conn.close()
            except Exception:
                pass
