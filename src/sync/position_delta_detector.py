"""Position delta detection between syncs.

Captures a pre-sync snapshot of holdings quantities for a given source_system,
then compares against the post-sync state to identify changes (new positions,
closed positions, quantity changes).

Detected deltas are persisted to the position_deltas table with a unique index
to prevent duplicate inserts on re-runs.
"""

import logging
from datetime import date
from typing import Optional

from src.database.connector import DatabaseConnector

logger = logging.getLogger(__name__)


def capture_pre_sync_snapshot(
    connector: DatabaseConnector,
    source_system: str,
) -> dict[str, tuple[float, date]]:
    """Return a snapshot of {asset_id: (quantity, snapshot_date)} for active holdings of the source.

    Uses latest-per-asset deduplication to ensure deterministic results when
    multiple snapshot rows exist for the same asset.

    Args:
        connector: DuckDB connector (read access sufficient)
        source_system: e.g. 'Schwab_CSV', 'CN_Fund_Excel'

    Returns:
        dict mapping asset_id to (quantity, snapshot_date) tuple
        (only non-shadow rows with qty > 0, latest snapshot_date per asset)
    """
    rows = connector.execute(
        """
        WITH latest AS (
            SELECT asset_id, MAX(snapshot_date) AS max_date
            FROM holdings
            WHERE source_system = ?
              AND is_shadow = FALSE
              AND quantity > 0
            GROUP BY asset_id
        )
        SELECT h.asset_id, h.quantity, h.snapshot_date
        FROM holdings h
        JOIN latest ON h.asset_id = latest.asset_id
          AND h.snapshot_date = latest.max_date
        WHERE h.source_system = ?
          AND h.is_shadow = FALSE
          AND h.quantity > 0
        """,
        (source_system, source_system),
    ).fetchall()

    snapshot = {asset_id: (float(qty), snap_date) for asset_id, qty, snap_date in rows}
    logger.debug(
        f"capture_pre_sync_snapshot({source_system!r}): {len(snapshot)} active holdings"
    )
    return snapshot


def detect_and_persist_deltas(
    connector: DatabaseConnector,
    source_system: str,
    pre_snapshot: dict[str, tuple[float, date]],
    new_snapshot_date: date,
) -> list[dict]:
    """Compare current holdings against pre_snapshot and persist non-zero deltas.

    Uses a Python-level FULL OUTER JOIN to handle:
    - New positions (not in pre_snapshot)
    - Closed positions (no longer in current state with qty > 0)
    - Quantity changes (qty changed between snapshots)

    Inserts are idempotent via the expression index on position_deltas
    (source_system, asset_id, old_snapshot_date, new_snapshot_date).

    Args:
        connector: DuckDB connector (write access required)
        source_system: source system identifier
        pre_snapshot: dict from capture_pre_sync_snapshot() — maps asset_id to
            (quantity, snapshot_date) tuple
        new_snapshot_date: snapshot date of the new reader data

    Returns:
        list of delta dicts (may be empty if no changes detected)
    """
    # Capture post-sync current state (latest-per-asset to avoid non-determinism)
    rows = connector.execute(
        """
        WITH latest AS (
            SELECT asset_id, MAX(snapshot_date) AS max_date
            FROM holdings
            WHERE source_system = ?
              AND is_shadow = FALSE
              AND quantity > 0
            GROUP BY asset_id
        )
        SELECT h.asset_id, h.quantity, h.snapshot_date
        FROM holdings h
        JOIN latest ON h.asset_id = latest.asset_id
          AND h.snapshot_date = latest.max_date
        WHERE h.source_system = ?
          AND h.is_shadow = FALSE
          AND h.quantity > 0
        """,
        (source_system, source_system),
    ).fetchall()

    post_state: dict[str, tuple[float, Optional[date]]] = {
        asset_id: (float(qty), snap_date) for asset_id, qty, snap_date in rows
    }

    # FULL OUTER JOIN via Python sets
    all_asset_ids = set(pre_snapshot.keys()) | set(post_state.keys())
    deltas = []

    for asset_id in all_asset_ids:
        pre_info = pre_snapshot.get(asset_id)
        old_qty = pre_info[0] if pre_info else 0.0
        new_qty_info = post_state.get(asset_id)
        new_qty = new_qty_info[0] if new_qty_info else 0.0

        delta_qty = new_qty - old_qty
        if abs(delta_qty) < 1e-8:
            continue  # no meaningful change

        # Use the pre-captured snapshot_date — do NOT query post-upsert holdings
        # (which would return the new snapshot_date, causing dedup collisions)
        old_snapshot_date: Optional[date] = pre_info[1] if pre_info else None

        delta_dict = {
            "asset_id": asset_id,
            "old_qty": old_qty,
            "new_qty": new_qty,
            "delta_qty": delta_qty,
            "source_system": source_system,
            "old_snapshot_date": old_snapshot_date,
            "new_snapshot_date": new_snapshot_date,
        }

        # Check for existing delta with same dedup key before inserting
        # (DuckDB expression indexes don't support ON CONFLICT DO NOTHING)
        coalesced_old = old_snapshot_date.isoformat() if old_snapshot_date else "1970-01-01"
        coalesced_new = new_snapshot_date.isoformat() if new_snapshot_date else "1970-01-01"
        existing = connector.execute(
            """
            SELECT COUNT(*) FROM position_deltas
            WHERE source_system = ?
              AND asset_id = ?
              AND COALESCE(old_snapshot_date, DATE '1970-01-01') = ?
              AND COALESCE(new_snapshot_date, DATE '1970-01-01') = ?
            """,
            (source_system, asset_id, coalesced_old, coalesced_new),
        ).fetchone()
        if existing and existing[0] > 0:
            logger.debug(
                f"position_delta already exists for {source_system}/{asset_id} "
                f"({coalesced_old} → {coalesced_new}), skipping"
            )
            continue

        try:
            connector.execute(
                """
                INSERT INTO position_deltas
                    (asset_id, old_qty, new_qty, delta_qty, source_system,
                     old_snapshot_date, new_snapshot_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    old_qty,
                    new_qty,
                    delta_qty,
                    source_system,
                    old_snapshot_date,
                    new_snapshot_date,
                ),
            )
            deltas.append(delta_dict)
        except Exception as e:
            logger.debug(
                f"position_delta insert failed for {source_system}/{asset_id}: {e}"
            )

    if deltas:
        logger.info(
            f"detect_and_persist_deltas({source_system!r}): "
            f"{len(deltas)} delta(s) detected and persisted"
        )
    return deltas
