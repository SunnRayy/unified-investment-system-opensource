from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import pandas as pd

from src.database.connector import DatabaseConnector

logger = logging.getLogger(__name__)


def get_approved_adapter_source_systems(connector: DatabaseConnector) -> set[str]:
    rows = connector.execute(
        "SELECT source_system FROM import_adapter_approvals WHERE enabled = TRUE"
    ).fetchall()
    return {r[0] for r in rows if r and r[0]}


def sync_approved_import_adapters(connector: DatabaseConnector, config: dict[str, Any]) -> dict[str, int]:
    """Sync approved adapter rows into holdings/transactions using the standard normalization pipeline.

    Idempotency: staged rows that have already been synced (synced_at IS NOT NULL)
    are skipped.  After successful insertion the staged row is marked with the current
    timestamp so a subsequent sync run will not re-insert.
    """
    # ADR-018 Phase 3: AND a.generated_reader_key IS NULL ensures mutual
    # exclusion — adapters with a config-driven reader are ingested via the
    # reader pipeline only, not DB-staging, preventing double-count.
    rows = connector.execute(
        """
        SELECT s.rowid, a.source_system, s.row_kind, s.normalized_payload_json
        FROM import_adapter_staged_rows s
        JOIN import_adapter_runs r ON r.id = s.run_id
        JOIN import_adapter_approvals a ON a.adapter_key = r.adapter_key
        WHERE a.enabled = TRUE AND s.validation_status = 'valid'
          AND s.synced_at IS NULL
          AND a.generated_reader_key IS NULL
        """
    ).fetchall()

    # Group payloads by (source_system, row_kind) so we can build DataFrames
    from collections import defaultdict
    groups: dict[tuple[str, str], list[tuple[int, dict]]] = defaultdict(list)
    for row_id, source_system, row_kind, payload_json in rows:
        payload = json.loads(payload_json)
        groups[(source_system, row_kind)].append((row_id, payload))

    holdings_count = 0
    transactions_count = 0
    synced_rowids: list[int] = []

    for (source_system, row_kind), items in groups.items():
        payloads = [p for _, p in items]
        rowids = [rid for rid, _ in items]

        if row_kind == "holding":
            df = pd.DataFrame(payloads)
            if df.empty:
                continue
            # Ensure required columns exist
            for col in ("snapshot_date", "asset_id", "asset_name", "quantity",
                        "market_price_unit", "market_value", "currency", "account",
                        "cost_price_unit"):
                if col not in df.columns:
                    df[col] = None
            df["source_system"] = source_system
            df["is_shadow"] = False
            # Fill currency default
            df["currency"] = df["currency"].fillna("CNY")

            try:
                from src.sync.orchestrator import _normalize_holdings_df, _upsert_holdings  # noqa: PLC0415
                normalized = _normalize_holdings_df(df, source_system)
                inserted = _upsert_holdings(connector, normalized)
                holdings_count += inserted
                synced_rowids.extend(rowids)
            except Exception as e:
                logger.error("Import adapter holdings sync failed for %s: %s", source_system, e)
        else:
            df = pd.DataFrame(payloads)
            if df.empty:
                continue
            for col in ("transaction_date", "asset_id", "asset_name", "transaction_type",
                        "quantity", "price_unit", "amount_gross", "commission_fee",
                        "currency", "account", "memo"):
                if col not in df.columns:
                    df[col] = None
            df["source_system"] = source_system
            df["currency"] = df["currency"].fillna("CNY")

            try:
                from src.sync.orchestrator import _normalize_transactions_df, _replace_transactions  # noqa: PLC0415
                normalized = _normalize_transactions_df(df, source_system)
                inserted = _replace_transactions(connector, normalized)
                transactions_count += inserted
                synced_rowids.extend(rowids)
            except Exception as e:
                logger.error("Import adapter transactions sync failed for %s: %s", source_system, e)

    # Mark synced rows so they won't be re-inserted on next run
    if synced_rowids:
        now = datetime.now().isoformat()
        for rid in synced_rowids:
            connector.execute(
                "UPDATE import_adapter_staged_rows SET synced_at = ? WHERE rowid = ?",
                (now, rid),
            )

    return {"holdings": holdings_count, "transactions": transactions_count}
