"""Bidirectional linking between decision trade logs and authoritative transactions."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from src.services.decision_scorer import match_trades_to_insights

logger = logging.getLogger(__name__)

_BUY_SELL_TYPES = {"buy", "sell"}

_LEDGER_EXCLUDED_SOURCES = frozenset({"RSU_Excel"})  # non-discretionary sources (RSU vest + auto tax-cover sells) must never enter the decision ledger

# Single definition of "reader/backfill provenance" for the linker.
# SQL fragment and Python helper must stay in sync with each other.
# Reader/backfill rows (suggestion_source IS NULL or 'imported') never need
# a human-authored verdict and are promoted straight to 'verified'.
_READER_PROVENANCE_SQL = "(suggestion_source IS NULL OR suggestion_source = 'imported')"


def _is_reader_provenance(suggestion_source: Any) -> bool:
    """Return True for reader/backfill rows (NULL or 'imported' suggestion_source)."""
    return suggestion_source is None or suggestion_source == "imported"


def _table_has_column(db: Any, table_name: str, column_name: str) -> bool:
    cols = db.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    return any(str(col[1]).lower() == column_name.lower() for col in cols)


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _normalize_direction(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in _BUY_SELL_TYPES:
        return text
    if "buy" in text or "买" in text:
        return "buy"
    if "sell" in text or "卖" in text:
        return "sell"
    return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_diff(actual: float | None, target: float | None) -> float | None:
    if actual is None or target is None:
        return None
    # Use absolute values: trade_logs amounts may be negative (outflow convention)
    # while transactions.amount_gross is always positive.
    denom = max(abs(target), 1.0)
    return abs(abs(actual) - abs(target)) / denom


def _candidate_amount(amount_gross: Any, amount_net: Any) -> float | None:
    gross = _safe_float(amount_gross)
    net = _safe_float(amount_net)
    if gross is not None:
        return gross
    if net is not None:
        return net
    return None


def _score_candidate(
    trade_date: date,
    trade_amount: float | None,
    trade_quantity: float | None,
    trade_price: float | None,
    candidate: tuple,
) -> dict[str, Any]:
    tx_id, tx_date_raw, tx_qty_raw, tx_price_raw, tx_amount_gross, tx_amount_net = candidate
    tx_date = _to_date(tx_date_raw) or trade_date
    tx_qty = _safe_float(tx_qty_raw)
    tx_price = _safe_float(tx_price_raw)
    tx_amount = _candidate_amount(tx_amount_gross, tx_amount_net)

    amount_diff = _normalized_diff(tx_amount, trade_amount)
    if amount_diff is not None:
        mode = "amount"
        base_score = amount_diff
    else:
        mode = "fallback"
        qty_diff = _normalized_diff(tx_qty, trade_quantity)
        px_diff = _normalized_diff(tx_price, trade_price)
        base_score = (qty_diff if qty_diff is not None else 1.0) + (px_diff if px_diff is not None else 1.0)

    day_penalty = abs((tx_date - trade_date).days) * 0.02
    return {
        "tx_id": int(tx_id),
        "mode": mode,
        "score": base_score + day_penalty,
        "tx_date": tx_date,
    }


def _is_ambiguous(best: dict[str, Any], runner_up: dict[str, Any] | None) -> bool:
    if runner_up is None:
        return False
    delta = runner_up["score"] - best["score"]
    if delta < 0:
        return True
    if best["mode"] == "amount" and runner_up["mode"] == "amount":
        return delta <= 0.03
    return delta <= 0.20


def _parse_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback


def _has_successful_sync_since(db: Any, created_at: datetime) -> bool:
    try:
        rows = db.execute(
            """
            SELECT created_at, reader_counts, warnings, info_messages,
                   integrity_passed, integrity_total, integrity_checks
            FROM sync_audit_reports
            WHERE report_type = 'sync'
              AND created_at > ?
            ORDER BY created_at ASC
            """,
            [created_at],
        ).fetchall()
    except Exception:
        return False

    for _, reader_counts_raw, warnings_raw, _info_raw, integrity_passed, integrity_total, integrity_checks_raw in rows:
        reader_counts = _parse_json(reader_counts_raw, {})
        warnings = _parse_json(warnings_raw, [])

        tx_synced = int(reader_counts.get("transactions_synced", 0) or 0) > 0
        decision_failed = any("Decision sync error" in str(msg) for msg in warnings)

        # Integrity is OK when there are no *blocking* failures.
        # Advisory-only failures (e.g. trade_log_verdict_consistency) must not
        # mark a mechanically-successful sync as failed.
        integrity_ok = True
        integrity_checks = _parse_json(integrity_checks_raw, [])
        if integrity_checks:
            # Prefer per-check blocking flag written by the orchestrator.
            blocking_failed = sum(
                1 for c in integrity_checks
                if not c.get("passed", True) and c.get("blocking", True)
            )
            integrity_ok = blocking_failed == 0
        elif integrity_total is not None and int(integrity_total) > 0:
            # Fallback for rows persisted before the blocking flag was added:
            # treat any failure as blocking (conservative / fail-safe).
            integrity_ok = int(integrity_passed or 0) >= int(integrity_total)

        if tx_synced and not decision_failed and integrity_ok:
            return True
    return False


# Trades that existed before this date predate the verification feature.
# The unmatched timeout must not fire on them during bootstrap.
_VERIFICATION_FEATURE_DATE = datetime(2026, 4, 11)


def _mark_stale_pending_as_unmatched(db: Any, has_verification_status: bool) -> int:
    if not has_verification_status:
        return 0

    rows = db.execute(
        """
        SELECT id, created_at
        FROM trade_logs
        WHERE COALESCE(verification_status, 'pending') = 'pending'
          AND linked_transaction_id IS NULL
        """
    ).fetchall()

    now = datetime.now()
    updated = 0
    for trade_id, created_at_raw in rows:
        created_at = _to_datetime(created_at_raw)
        if created_at is None:
            continue
        if created_at.tzinfo is not None:
            created_at = created_at.astimezone().replace(tzinfo=None)
        # Skip trades that predate the verification feature — they should be matched
        # or left pending, not immediately timed out during bootstrap.
        if created_at < _VERIFICATION_FEATURE_DATE:
            continue
        if (now - created_at) < timedelta(days=15):
            continue
        if not _has_successful_sync_since(db, created_at):
            continue
        db.execute(
            "UPDATE trade_logs SET verification_status = 'unmatched' WHERE id = ?",
            [int(trade_id)],
        )
        updated += 1
    return updated


def link_trade_logs_to_transactions(db: Any) -> dict[str, int]:
    """Match pending (and previously unmatched) trade logs to authoritative transactions.

    Lifecycle after this function runs:
      - Rows that already carry a verdict (e.g. re-linked after a full-replace
          transaction reset via _reset_trade_log_links) go straight back to
          'verified' — their lifecycle is already complete.
      - Owner-recorded rows (suggestion_source IS NOT NULL AND != 'imported')
          without a verdict: matched → 'pending_window' (linked, awaiting outcome
          maturity). score_all_trades (P9b) later matures them to verified+verdict.
      - Reader/backfill rows (suggestion_source IS NULL OR == 'imported'):
          matched → 'verified' (deliberate KPI-protection; these rows never need
          a human-authored verdict).
      - Previously 'unmatched' rows are retried on every run — if a matching
        transaction now exists they are re-linked with the same owner/reader split.
      - Rows that remain unmatched past the 15-day window are written to
        'unmatched' by _mark_stale_pending_as_unmatched (targets 'pending' only).

    Summary key 'verified' counts ALL promotions regardless of the target status
    (both 'verified' and 'pending_window' increments are tallied here).  The key
    name is kept stable so callers (orchestrator, tests) need no changes.
    """
    has_verification_status = _table_has_column(db, "trade_logs", "verification_status")
    # Include 'unmatched' so previously-timed-out rows are retried when matching
    # transactions arrive later.  _mark_stale_pending_as_unmatched targets only
    # literal 'pending' rows (its own independent query — not affected here).
    status_filter = "COALESCE(verification_status, 'pending') IN ('pending', 'unmatched')" if has_verification_status else "1 = 1"

    # First pass: trades that already have linked_transaction_id set (by AIA sync or prior
    # linker runs) just need their verification_status promoted.
    # Owner-recorded → pending_window (awaiting scorer maturation).
    # Reader/backfill (NULL or 'imported') → verified (KPI-protection).
    if has_verification_status:
        pre_linked = db.execute(
            f"""
            UPDATE trade_logs
            SET verification_status = CASE
                WHEN verdict IS NOT NULL THEN 'verified'
                WHEN {_READER_PROVENANCE_SQL} THEN 'verified'
                ELSE 'pending_window'
            END
            WHERE linked_transaction_id IS NOT NULL
              AND {status_filter}
            RETURNING id
            """
        ).fetchall()
        pre_linked_count = len(pre_linked)
    else:
        pre_linked_count = 0

    rows = db.execute(
        f"""
        SELECT id, asset_id, action, log_date, quantity, price, amount, suggestion_source, verdict
        FROM trade_logs
        WHERE {status_filter}
          AND linked_transaction_id IS NULL
        ORDER BY log_date DESC, id DESC
        """
    ).fetchall()

    # 'verified' counts ALL promotions (both 'verified' and 'pending_window' targets).
    summary = {"verified": pre_linked_count, "ambiguous": 0, "no_candidate": 0, "unmatched": 0}

    for trade_id, asset_id, action, log_date_raw, quantity_raw, price_raw, amount_raw, suggestion_source, verdict in rows:
        direction = _normalize_direction(action)
        trade_date = _to_date(log_date_raw)
        if not direction or not trade_date or not asset_id:
            continue

        candidates = db.execute(
            """
            SELECT id, transaction_date, quantity, price_unit, amount_gross, amount_net
            FROM transactions
            WHERE asset_id = ?
              AND LOWER(COALESCE(transaction_type, '')) = ?
              AND transaction_date BETWEEN ? - INTERVAL 3 DAY AND ? + INTERVAL 3 DAY
              AND COALESCE(is_provisional, FALSE) = FALSE
            ORDER BY transaction_date ASC, id ASC
            """,
            [asset_id, direction, trade_date, trade_date],
        ).fetchall()

        if not candidates:
            summary["no_candidate"] += 1
            continue

        scored = [
            _score_candidate(
                trade_date=trade_date,
                trade_amount=_safe_float(amount_raw),
                trade_quantity=_safe_float(quantity_raw),
                trade_price=_safe_float(price_raw),
                candidate=row,
            )
            for row in candidates
        ]
        scored.sort(key=lambda item: item["score"])
        best = scored[0]
        runner_up = scored[1] if len(scored) > 1 else None

        if _is_ambiguous(best, runner_up):
            summary["ambiguous"] += 1
            continue

        if has_verification_status:
            # Rows with an existing verdict have already completed their lifecycle
            # (e.g. re-linked after a full-replace transaction reset) → verified.
            # Reader/backfill rows → verified (KPI-protection).
            # Owner-recorded rows without a verdict → pending_window (awaiting scorer).
            if verdict is not None:
                new_status = "verified"
            elif _is_reader_provenance(suggestion_source):
                new_status = "verified"
            else:
                new_status = "pending_window"
            db.execute(
                """
                UPDATE trade_logs
                SET linked_transaction_id = ?, verification_status = ?
                WHERE id = ?
                """,
                [best["tx_id"], new_status, int(trade_id)],
            )
        else:
            db.execute(
                "UPDATE trade_logs SET linked_transaction_id = ? WHERE id = ?",
                [best["tx_id"], int(trade_id)],
            )
        # Increment 'verified' regardless of target status (pending_window or verified).
        summary["verified"] += 1

    summary["unmatched"] = _mark_stale_pending_as_unmatched(db, has_verification_status)
    logger.info(
        "Trade linker: verified=%s ambiguous=%s no_candidate=%s unmatched=%s",
        summary["verified"],
        summary["ambiguous"],
        summary["no_candidate"],
        summary["unmatched"],
    )
    return summary


def backfill_trade_logs_from_transactions(db: Any) -> dict[str, int]:
    """Create decision-ledger trade logs for eligible buy/sell transactions missing a trade log."""
    has_verification_status = _table_has_column(db, "trade_logs", "verification_status")

    # Self-heal: remove any previously auto-imported RSU entries so the ledger heals itself.
    # Clean child rows in insight_trade_links first (trade_id references trade_logs.id).
    db.execute(
        """
        DELETE FROM insight_trade_links
        WHERE trade_id IN (
            SELECT id FROM trade_logs
            WHERE STARTS_WITH(asset_id, 'RSU_')
              AND COALESCE(suggestion_source, '') <> 'manual'
        )
        """
    )
    rsu_deleted = db.execute(
        """
        DELETE FROM trade_logs
        WHERE STARTS_WITH(asset_id, 'RSU_')
          AND COALESCE(suggestion_source, '') <> 'manual'
        RETURNING id
        """
    ).fetchall()
    rsu_ledger_removed = len(rsu_deleted)

    # Build NOT IN clause from the module-level exclusion constant.
    excluded_placeholders = ", ".join("?" for _ in _LEDGER_EXCLUDED_SOURCES)
    excluded_params = list(_LEDGER_EXCLUDED_SOURCES)

    rows = db.execute(
        f"""
        SELECT
            id, transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, amount_net, currency, memo
        FROM transactions
        WHERE LOWER(COALESCE(transaction_type, '')) IN ('buy', 'sell')
          AND COALESCE(is_provisional, FALSE) = FALSE
          AND COALESCE(source_system, '') NOT IN ({excluded_placeholders})
        ORDER BY transaction_date DESC, id DESC
        """,
        excluded_params,
    ).fetchall()

    summary = {"inserted": 0, "skipped_existing": 0, "skipped_type": 0, "attributed": 0, "rsu_ledger_removed": rsu_ledger_removed}

    for (
        tx_id,
        tx_date_raw,
        asset_id,
        asset_name,
        tx_type_raw,
        quantity_raw,
        price_raw,
        amount_gross_raw,
        amount_net_raw,
        currency,
        memo,
    ) in rows:
        tx_type = _normalize_direction(tx_type_raw)
        tx_date = _to_date(tx_date_raw)
        if tx_type not in _BUY_SELL_TYPES or not tx_date or not asset_id:
            summary["skipped_type"] += 1
            continue

        action = "Buy" if tx_type == "buy" else "Sell"
        existing = db.execute(
            """
            SELECT id
            FROM trade_logs
            WHERE linked_transaction_id = ?
               OR (
                    asset_id = ?
                AND LOWER(action) = LOWER(?)
                AND log_date BETWEEN ? - INTERVAL 1 DAY AND ? + INTERVAL 1 DAY
               )
            LIMIT 1
            """,
            [int(tx_id), asset_id, action, tx_date, tx_date],
        ).fetchone()
        if existing is not None:
            summary["skipped_existing"] += 1
            continue

        quantity = _safe_float(quantity_raw)
        price = _safe_float(price_raw)
        amount = _candidate_amount(amount_gross_raw, amount_net_raw)
        if amount is None and quantity is not None and price is not None:
            amount = round(quantity * price, 4)
        decision_reason = (str(memo).strip() if memo is not None else "") or None

        columns = [
            "log_date",
            "asset_id",
            "asset_name",
            "action",
            "price",
            "quantity",
            "amount",
            "decision_reason",
            "suggestion_source",
            "linked_transaction_id",
        ]
        values = [
            tx_date,
            asset_id,
            asset_name or asset_id,
            action,
            price,
            quantity,
            amount,
            decision_reason,
            "imported",
            int(tx_id),
        ]
        if has_verification_status:
            columns.append("verification_status")
            values.append("verified")

        placeholders = ", ".join("?" for _ in values)
        inserted = db.execute(
            f"""
            INSERT INTO trade_logs ({", ".join(columns)})
            VALUES ({placeholders})
            RETURNING id
            """,
            values,
        ).fetchone()
        if inserted is None:
            continue

        summary["inserted"] += 1
        if match_trades_to_insights(db, trade_id=int(inserted[0])) > 0:
            summary["attributed"] += 1

    logger.info(
        "Trade linker backfill: inserted=%s skipped_existing=%s skipped_type=%s attributed=%s rsu_ledger_removed=%s",
        summary["inserted"],
        summary["skipped_existing"],
        summary["skipped_type"],
        summary["attributed"],
        summary["rsu_ledger_removed"],
    )
    return summary
