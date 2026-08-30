"""Owner-logged P&L for bank-bought assets (#7, plan §C.4).

`PUT /holdings/{asset_id}/manual-pnl`    upsert cost and/or realized profit
`DELETE /holdings/{asset_id}/manual-pnl` clear the override
`GET /holdings/manual-pnl`               list every override (UI hydration)

The readers cannot price money-market / 理财 / 债券 / 美元债 holdings — no cost, no
transactions — so the engine can only honestly report "—" for them. These
endpoints let the owner supply the figures they actually know ("I put in X, it
earned Y"). Reads stay in the P&L engine; this module only writes the override
table it reads from.

Contract notes the UI depends on:
- Both figures are **CNY** and both are optional, but an upsert with *neither*
  is a 400 — an empty override is indistinguishable from no override, so it
  would be a silent no-op.
- A logged cost on a **cash-equivalent** asset is accepted and stored, but does
  NOT produce an unrealized gain: a cash balance has no price basis (engine rule
  §C.1.1). The response says so via `cost_affects_unrealized`.
- Manual realized applies to **all-time** P&L only. One cumulative figure cannot
  yield a month delta, so period views (1m/12m/36m) ignore it by design.
- An asset that later receives authoritative *reader* transactions has its
  override **superseded, not added** — the reader ledger wins, or the owner's
  cumulative profit would double-count. `superseded: true` flags it for deletion.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.dependencies import get_db, get_writable_db
from src.api.routes._errors import api_error_response
from src.database.connector import DatabaseConnector
from src.services.pnl.manual import superseded_override_ids
from src.storage.gcs_flush import mark_dirty

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/holdings", tags=["Manual P&L"])


class ManualPnLIn(BaseModel):
    """Both figures optional, but not both absent (see module docstring)."""

    cost_basis_cny: Optional[float] = Field(
        default=None,
        description="What the owner put in, CNY. Yields unrealized = market - cost "
                    "for non-cash assets; stored but not applied to unrealized for "
                    "cash-equivalents.",
    )
    realized_pnl_cny: Optional[float] = Field(
        default=None,
        description="Cumulative realized profit to date, CNY. All-time only — "
                    "period views ignore it.",
    )
    as_of_date: Optional[str] = Field(
        default=None,
        description="Display provenance: the date the cumulative figure is 'as of'. "
                    "Never used in math.",
    )
    memo: Optional[str] = None


class ManualPnLOut(BaseModel):
    asset_id: str
    cost_basis_cny: Optional[float]
    realized_pnl_cny: Optional[float]
    as_of_date: Optional[str]
    memo: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    # Why a stored cost may not be moving the displayed unrealized figure.
    cost_affects_unrealized: bool = True
    # An authoritative reader ledger has taken this asset over; the override is
    # ignored by the engine and should be deleted.
    superseded: bool = False
    # Staleness signal for buys/sells (see STALE_VALUE_MOVE_PCT).
    market_value_at_log: Optional[float] = None
    current_market_value: Optional[float] = None
    value_move_pct: Optional[float] = None
    value_looks_stale: bool = False


# A logged cost covers the whole position, so changing the position size
# invalidates it. This threshold separates "the balance drifted" (interest
# accrual, FX) from "the owner put money in or took it out". Deliberately a
# prompt, never an automatic adjustment: inferring a new cost would be inventing
# a number, which is the V7.8.3 phantom in a different costume.
STALE_VALUE_MOVE_PCT = 10.0


def _row_to_out(
    row,
    *,
    superseded: bool = False,
    cost_affects_unrealized: bool = True,
    current_market_value: Optional[float] = None,
) -> ManualPnLOut:
    at_log = None if row[7] is None else float(row[7])
    move_pct = None
    stale = False
    # Only a logged COST goes stale on a position change; a realized figure is a
    # running total that a later buy does not invalidate.
    if at_log and current_market_value is not None and row[1] is not None:
        move_pct = (current_market_value - at_log) / at_log * 100.0
        stale = abs(move_pct) >= STALE_VALUE_MOVE_PCT
    return ManualPnLOut(
        asset_id=row[0],
        cost_basis_cny=None if row[1] is None else float(row[1]),
        realized_pnl_cny=None if row[2] is None else float(row[2]),
        as_of_date=None if row[3] is None else str(row[3]),
        memo=row[4],
        created_at=None if row[5] is None else str(row[5]),
        updated_at=None if row[6] is None else str(row[6]),
        cost_affects_unrealized=cost_affects_unrealized,
        superseded=superseded,
        market_value_at_log=at_log,
        current_market_value=current_market_value,
        value_move_pct=None if move_pct is None else round(move_pct, 2),
        value_looks_stale=stale,
    )


_SELECT = (
    "SELECT asset_id, cost_basis_cny, realized_pnl_cny, as_of_date, memo, "
    "created_at, updated_at, market_value_at_log FROM manual_asset_pnl"
)


def _current_market_value(db, asset_id: str) -> Optional[float]:
    """This asset's latest non-shadow market value (per-asset MAX, never global)."""
    row = db.execute(
        """SELECT SUM(h.market_value)
           FROM holdings h
           JOIN (SELECT MAX(snapshot_date) AS d FROM holdings
                 WHERE asset_id = ? AND is_shadow = FALSE) latest
             ON h.snapshot_date = latest.d
           WHERE h.asset_id = ? AND h.is_shadow = FALSE""",
        [asset_id, asset_id],
    ).fetchone()
    return None if not row or row[0] is None else float(row[0])


def _write_audit(writable, asset_id: str, action: str, old_value, new_value) -> None:
    """Append an immutable before/after record. Never updated, never deleted —
    a cleared override must stay reconstructible from the audit trail."""
    writable.execute(
        "INSERT INTO manual_asset_pnl_audit (asset_id, action, old_value, new_value) "
        "VALUES (?, ?, ?, ?)",
        [
            asset_id,
            action,
            None if old_value is None else json.dumps(old_value, ensure_ascii=False, default=str),
            None if new_value is None else json.dumps(new_value, ensure_ascii=False, default=str),
        ],
    )


def _snapshot(row) -> dict:
    return {
        "cost_basis_cny": None if row[1] is None else float(row[1]),
        "realized_pnl_cny": None if row[2] is None else float(row[2]),
        "as_of_date": None if row[3] is None else str(row[3]),
        "memo": row[4],
    }


def _cash_equivalent(db, asset_id: str) -> bool:
    """Resolve the asset's cash-equivalence the same way the engine does, so the
    `cost_affects_unrealized` hint cannot contradict the displayed P&L."""
    from src.services.currency import is_cash_equivalent_asset
    from src.services.portfolio_helpers import get_display_name, resolve_top_class

    row = db.execute(
        """SELECT COALESCE(MAX(parent_tc.name), MAX(tc.name), MAX(r.asset_class), '') AS top_class,
                  COALESCE(MAX(r.asset_class), '') AS sub_class
           FROM asset_registry r
           LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
           LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
           WHERE r.canonical_id = ?""",
        [asset_id],
    ).fetchone()
    if not row:
        return False
    return is_cash_equivalent_asset(resolve_top_class(row[0] or ""), get_display_name(row[1] or ""))


@router.get("/manual-pnl", response_model=list[ManualPnLOut])
async def list_manual_pnl(db: DatabaseConnector = Depends(get_db)):
    """Every override, for UI hydration. Flags superseded rows so the owner can
    see which ones the reader ledger has taken over."""
    try:
        rows = db.execute(f"{_SELECT} ORDER BY asset_id").fetchall()
        if not rows:
            return []
        superseded = superseded_override_ids(db, [r[0] for r in rows])
        return [
            _row_to_out(
                r,
                superseded=r[0] in superseded,
                cost_affects_unrealized=not _cash_equivalent(db, r[0]),
                current_market_value=_current_market_value(db, r[0]),
            )
            for r in rows
        ]
    except Exception as e:
        logger.exception("list_manual_pnl failed")
        return api_error_response(e, context="manual_pnl_list")


@router.put("/{asset_id}/manual-pnl", response_model=ManualPnLOut)
async def upsert_manual_pnl(
    asset_id: str,
    payload: ManualPnLIn,
    db: DatabaseConnector = Depends(get_writable_db),
):
    """Log (or re-log) the owner's cost and/or realized profit for one asset."""
    if payload.cost_basis_cny is None and payload.realized_pnl_cny is None:
        raise HTTPException(
            status_code=400,
            detail="Provide cost_basis_cny and/or realized_pnl_cny — an override with "
                   "neither figure is indistinguishable from no override. Use DELETE to clear.",
        )
    try:
        exists = db.execute(
            "SELECT 1 FROM asset_registry WHERE canonical_id = ?", [asset_id]
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Unknown asset_id: {asset_id}")

        before = db.execute(f"{_SELECT} WHERE asset_id = ?", [asset_id]).fetchone()
        old_value = _snapshot(before) if before else None

        # Stamp the balance this figure was entered against, so a later buy or
        # sell can be detected as "your cost is out of date" instead of silently
        # turning new principal into phantom profit.
        current_value = _current_market_value(db, asset_id)

        if before:
            db.execute(
                "UPDATE manual_asset_pnl SET cost_basis_cny = ?, realized_pnl_cny = ?, "
                "as_of_date = ?, memo = ?, market_value_at_log = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE asset_id = ?",
                [payload.cost_basis_cny, payload.realized_pnl_cny,
                 payload.as_of_date, payload.memo, current_value, asset_id],
            )
            action = "update"
        else:
            db.execute(
                "INSERT INTO manual_asset_pnl "
                "(asset_id, cost_basis_cny, realized_pnl_cny, as_of_date, memo, market_value_at_log) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [asset_id, payload.cost_basis_cny, payload.realized_pnl_cny,
                 payload.as_of_date, payload.memo, current_value],
            )
            action = "create"

        row = db.execute(f"{_SELECT} WHERE asset_id = ?", [asset_id]).fetchone()
        _write_audit(db, asset_id, action, old_value, _snapshot(row))
        mark_dirty()

        superseded = superseded_override_ids(db, [asset_id])
        return _row_to_out(
            row,
            superseded=asset_id in superseded,
            cost_affects_unrealized=not _cash_equivalent(db, asset_id),
            current_market_value=current_value,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("upsert_manual_pnl failed for %s", asset_id)
        return api_error_response(e, context="manual_pnl_upsert")


@router.delete("/{asset_id}/manual-pnl")
async def delete_manual_pnl(
    asset_id: str,
    db: DatabaseConnector = Depends(get_writable_db),
):
    """Clear the override — the asset returns to its base treatment ("—" for a
    balance-only holding, pl=0 for cash)."""
    try:
        before = db.execute(f"{_SELECT} WHERE asset_id = ?", [asset_id]).fetchone()
        if not before:
            raise HTTPException(
                status_code=404, detail=f"No manual P&L logged for {asset_id}"
            )

        db.execute("DELETE FROM manual_asset_pnl WHERE asset_id = ?", [asset_id])
        _write_audit(db, asset_id, "delete", _snapshot(before), None)
        mark_dirty()
        return {"asset_id": asset_id, "deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("delete_manual_pnl failed for %s", asset_id)
        return api_error_response(e, context="manual_pnl_delete")
