"""Memo registry service: look up strategy memos by asset_id.

Provides the shared linkage-state logic used by:
- GET /reviews/value-trap/{id}/context  (context panel)
- POST /reviews/value-trap/{id}/draft   (AI draft prompt construction)
- PUT /reviews/value-trap/{id}          (ruling gate — unresolved requires linkage_ack)

Linkage states:
  'linked'         — at least one active memo_registry row maps to this asset.
  'confirmed_none' — owner explicitly confirmed no memo exists
                     (asset_memo_confirmations.confirmed_no_memo = TRUE).
  'unresolved'     — neither linked nor confirmed; show backfill warning.
"""
from __future__ import annotations

from typing import Literal

LinkageState = Literal["linked", "confirmed_none", "unresolved"]

# Exact string the PRD requires for the unresolved case.
UNRESOLVED_DISPLAY = "Memo linkage not backfilled — verify manually before ruling"


def memos_for_asset(db, asset_id: str) -> list[dict]:
    """Return all active memo_registry rows linked to asset_id.

    Each dict: {memo_id: str, title: str, falsification_summary: str | None}.
    Empty list if no linkage exists.

    Never uses global MAX(snapshot_date) — operates on memo tables only.
    """
    rows = db.execute(
        """
        SELECT m.memo_id, m.title, m.falsification_summary
        FROM memo_registry m
        JOIN memo_asset_map mam ON m.memo_id = mam.memo_id
        WHERE mam.asset_id = ?
          AND m.status = 'active'
        ORDER BY m.created_at ASC, m.memo_id ASC
        """,
        [asset_id],
    ).fetchall()
    return [
        {
            "memo_id": str(r[0]),
            "title": str(r[1]),
            "falsification_summary": str(r[2]) if r[2] is not None else None,
        }
        for r in rows
    ]


def linkage_state(db, asset_id: str) -> LinkageState:
    """Return the memo linkage state for an asset.

    'linked'         — at least one active memo is mapped to this asset.
    'confirmed_none' — owner confirmed no memo exists.
    'unresolved'     — neither linked nor confirmed; backfill warning must show.
    """
    memos = memos_for_asset(db, asset_id)
    if memos:
        return "linked"

    row = db.execute(
        "SELECT confirmed_no_memo FROM asset_memo_confirmations WHERE asset_id = ?",
        [asset_id],
    ).fetchone()
    if row and row[0]:
        return "confirmed_none"

    return "unresolved"
