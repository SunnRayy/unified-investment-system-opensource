"""Value-trap review API routes (PRD 2026-07-07 F2, Batch B3).

Rule 12: every route body is wrapped in try/except -> api_error_response, so
an unhandled failure never degrades to a silent [] + 200.

Fix 2 (2026-07-10): memo registry + linkage; linkage_ack required for ruling
on unresolved assets; PUT /assets/{asset_id}/confirm-no-memo endpoint.

Fix 3 (2026-07-10): AI draft prompt rewritten — price decline, unrealized
loss %, and the trigger threshold are explicitly inadmissible as falsification
evidence. Reduced context passed to LLM strips the loss numbers.

Fix 4 (2026-07-10): Hold ruling requires next_review_date (422 otherwise);
frontend enforces no-default ruling selection.
"""
import json as _json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.dependencies import get_db
from src.api.routes._errors import api_error_response
from src.database.connector import DatabaseConnector
from src.services.freshness import freshness_verdict
from src.services.memo_registry import (
    UNRESOLVED_DISPLAY,
    linkage_state,
    memos_for_asset,
)
from src.services.position_lots import current_lot_cost
from src.services.value_trap import scan_value_traps
from src.services.verification_config import load_verification_config
from src.storage.gcs_flush import mark_dirty

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reviews/value-trap", tags=["Value Trap Reviews"])

_VALID_RULINGS = ("hold_with_thesis", "trim", "liquidate")

_COLUMNS = (
    "id", "asset_id", "asset_name", "status", "trigger_threshold_pct",
    "unrealized_return_pct", "memo_id", "opened_at", "refreshed_at",
    "thesis_restated", "falsification_check", "would_buy_today", "ruling",
    "adversarial_ack", "next_review_date", "last_reviewed_at", "last_ruling",
    "next_trigger_threshold_pct",
)


def _open_writable(db: DatabaseConnector) -> DatabaseConnector:
    """Return a writable DB connection, closing the read-only one if needed."""
    if getattr(db, "read_only", False):
        db_path = db.db_path
        db.close()
        return DatabaseConnector(db_path, read_only=False)
    return db


def _row_to_dict(row: tuple) -> dict:
    d = dict(zip(_COLUMNS, row))
    for key in ("opened_at", "refreshed_at", "last_reviewed_at"):
        if d.get(key) is not None:
            d[key] = str(d[key])
    if d.get("next_review_date") is not None:
        d["next_review_date"] = str(d["next_review_date"])
    return d


def _days_open(opened_at) -> Optional[int]:
    if opened_at is None:
        return None
    if isinstance(opened_at, str):
        try:
            opened_at = datetime.fromisoformat(opened_at)
        except ValueError:
            return None
    if not isinstance(opened_at, datetime):
        return None
    return max(0, (datetime.now() - opened_at).days)


class ValueTrapRulingRequest(BaseModel):
    thesis_restated: Optional[str] = None
    falsification_check: Optional[str] = None
    would_buy_today: Optional[str] = None
    ruling: str
    adversarial_ack: bool = False
    next_review_date: Optional[str] = None
    # Fix 2: required when the asset has unresolved memo linkage.
    linkage_ack: bool = False


@router.post("/scan")
async def run_value_trap_scan(db: DatabaseConnector = Depends(get_db)):
    """Run the F2.1/F2.2 trigger scan now (also runnable weekly/on-sync later)."""
    writable = None
    try:
        writable = _open_writable(db)
        summary = scan_value_traps(writable)
        mark_dirty()
        return summary
    except Exception as e:
        logger.exception("value_trap scan failed")
        return api_error_response(e, context="value_trap_scan")
    finally:
        if writable and writable is not db:
            writable.close()


@router.get("")
async def list_value_trap_reviews(
    status: str = Query(default="open", pattern="^(open|ruled|all)$"),
    db: DatabaseConnector = Depends(get_db),
):
    """List value-trap reviews. Each row includes days_open + overdue (F2.4)."""
    try:
        cfg = load_verification_config()
        overdue_days = cfg.value_trap.overdue_alert_days

        where = ""
        params: list = []
        if status in ("open", "ruled"):
            where = "WHERE status = ?"
            params = [status]

        rows = db.execute(
            f"""
            SELECT {', '.join(_COLUMNS)}
            FROM value_trap_reviews
            {where}
            ORDER BY opened_at DESC, id DESC
            """,
            params,
        ).fetchall()

        results = []
        for row in rows:
            item = _row_to_dict(row)
            days_open = _days_open(row[_COLUMNS.index("opened_at")])
            item["days_open"] = days_open
            item["overdue"] = bool(
                item["status"] == "open" and days_open is not None and days_open > overdue_days
            )
            results.append(item)
        return results
    except Exception as e:
        logger.exception("list_value_trap_reviews failed")
        return api_error_response(e, context="list_value_trap_reviews")


@router.get("/pending-count")
async def get_value_trap_pending_count(db: DatabaseConnector = Depends(get_db)):
    """{open, overdue} counts for a dashboard badge (F2.4)."""
    try:
        cfg = load_verification_config()
        overdue_days = cfg.value_trap.overdue_alert_days

        rows = db.execute(
            "SELECT opened_at FROM value_trap_reviews WHERE status = 'open'"
        ).fetchall()

        open_count = len(rows)
        overdue_count = sum(
            1 for r in rows
            if (days := _days_open(r[0])) is not None and days > overdue_days
        )
        return {"open": open_count, "overdue": overdue_count}
    except Exception as e:
        logger.exception("get_value_trap_pending_count failed")
        return api_error_response(e, context="value_trap_pending_count")


@router.put("/{review_id}")
async def submit_value_trap_ruling(
    review_id: int,
    body: ValueTrapRulingRequest,
    db: DatabaseConnector = Depends(get_db),
):
    """Submit the F2.3 review ruling: three mandatory questions + ruling.

    'liquidate' cannot be saved without adversarial_ack=true (PRD F2.3 process
    gate) -> 422.

    'hold_with_thesis' requires next_review_date (Fix 4) -> 422 otherwise.

    When the asset has unresolved memo linkage, the body must include
    linkage_ack=true (Fix 2) -> 422 otherwise.

    'hold_with_thesis' re-arms the escalation ladder (F2.2):
    next_trigger_threshold_pct = trigger_threshold_pct - escalation_step_pp.
    """
    if body.ruling not in _VALID_RULINGS:
        raise HTTPException(
            status_code=422,
            detail=f"ruling must be one of {_VALID_RULINGS}, got {body.ruling!r}",
        )
    if body.ruling == "liquidate" and not body.adversarial_ack:
        raise HTTPException(
            status_code=422,
            detail="liquidate ruling requires adversarial_ack=true (adversarial review required, PRD F2.3)",
        )
    # Fix 4: Hold requires a next_review_date.
    if body.ruling == "hold_with_thesis" and not body.next_review_date:
        raise HTTPException(
            status_code=422,
            detail="hold_with_thesis ruling requires next_review_date",
        )

    writable = None
    try:
        writable = _open_writable(db)
        existing = writable.execute(
            "SELECT trigger_threshold_pct, asset_id FROM value_trap_reviews WHERE id = ?",
            [review_id],
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail=f"value_trap review {review_id} not found")

        asset_id = existing[1]

        # Fix 2: linkage gate — unresolved linkage requires explicit acknowledgement.
        state = linkage_state(writable, asset_id)
        if state == "unresolved" and not body.linkage_ack:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This asset has unresolved memo linkage. "
                    + UNRESOLVED_DISPLAY
                    + " — set linkage_ack=true to proceed after manual verification."
                ),
            )

        # R2-1: Stale price gate — a ruling on stale-price data is a process
        # violation (the trigger and the ruling must share the same price epoch).
        fv = freshness_verdict(writable, asset_id)
        if not fv["fresh"]:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"price data stale as of {fv['price_date']} — refresh before ruling"
                ),
            )

        next_trigger_threshold_pct = None
        if body.ruling == "hold_with_thesis":
            cfg = load_verification_config()
            trigger_threshold_pct = float(existing[0])
            next_trigger_threshold_pct = trigger_threshold_pct - cfg.value_trap.escalation_step_pp

        writable.execute(
            """
            UPDATE value_trap_reviews
            SET status = 'ruled',
                thesis_restated = ?,
                falsification_check = ?,
                would_buy_today = ?,
                ruling = ?,
                adversarial_ack = ?,
                next_review_date = ?,
                last_reviewed_at = CURRENT_TIMESTAMP,
                last_ruling = ?,
                next_trigger_threshold_pct = ?
            WHERE id = ?
            """,
            [
                body.thesis_restated,
                body.falsification_check,
                body.would_buy_today,
                body.ruling,
                body.adversarial_ack,
                body.next_review_date,
                body.ruling,
                next_trigger_threshold_pct,
                review_id,
            ],
        )
        mark_dirty()

        row = writable.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM value_trap_reviews WHERE id = ?",
            [review_id],
        ).fetchone()
        return _row_to_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("submit_value_trap_ruling failed")
        return api_error_response(e, context="value_trap_ruling")
    finally:
        if writable and writable is not db:
            writable.close()


# ---------------------------------------------------------------------------
# Fix 2: confirm-no-memo endpoint
# ---------------------------------------------------------------------------

@router.put("/assets/{asset_id}/confirm-no-memo")
async def confirm_no_memo_for_asset(
    asset_id: str,
    db: DatabaseConnector = Depends(get_db),
):
    """Owner confirms no memo exists for this asset.

    Sets asset_memo_confirmations.confirmed_no_memo = TRUE, allowing the
    context panel to display 'no memo on record' (Fix 2 gate).
    The string 'no memo on record' may only appear after this confirmation.
    """
    writable = None
    try:
        writable = _open_writable(db)
        existing = writable.execute(
            "SELECT 1 FROM asset_memo_confirmations WHERE asset_id = ?",
            [asset_id],
        ).fetchone()
        if existing:
            writable.execute(
                """
                UPDATE asset_memo_confirmations
                SET confirmed_no_memo = TRUE, confirmed_at = CURRENT_TIMESTAMP
                WHERE asset_id = ?
                """,
                [asset_id],
            )
        else:
            writable.execute(
                """
                INSERT INTO asset_memo_confirmations (asset_id, confirmed_no_memo, confirmed_at)
                VALUES (?, TRUE, CURRENT_TIMESTAMP)
                """,
                [asset_id],
            )
        mark_dirty()
        return {"asset_id": asset_id, "confirmed_no_memo": True}
    except Exception as e:
        logger.exception("confirm_no_memo_for_asset failed")
        return api_error_response(e, context="confirm_no_memo")
    finally:
        if writable and writable is not db:
            writable.close()


# ---------------------------------------------------------------------------
# Context + AI draft helpers (WS2 — value-trap Huinsight integration)
# ---------------------------------------------------------------------------

def _build_context_payload(review_id: int, db: DatabaseConnector) -> dict:
    """Assemble context data for a value-trap review.

    Raises HTTPException(404) if review_id does not exist.
    NEVER uses global MAX(snapshot_date) — always per-asset CTE.

    Fix 2: originating_memo logic replaced by memo_linkage with three states:
      'linked'         — memos list populated, show ids + falsification_summary
      'confirmed_none' — owner confirmed no memo (safe to display "no memo on record")
      'unresolved'     — backfill warning must show; never use "no memo on record"
    """
    review_row = db.execute(
        "SELECT asset_id, unrealized_return_pct, trigger_threshold_pct, opened_at "
        "FROM value_trap_reviews WHERE id = ?",
        [review_id],
    ).fetchone()
    if review_row is None:
        raise HTTPException(status_code=404, detail=f"value_trap review {review_id} not found")

    asset_id = review_row[0]
    unrealized_return_pct = float(review_row[1]) if review_row[1] is not None else None
    trigger_threshold_pct = float(review_row[2]) if review_row[2] is not None else 0.0
    opened_at = review_row[3]

    # Latest non-shadow holding per-asset CTE (never global MAX)
    holding_row = db.execute(
        """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_date
            FROM holdings
            WHERE is_shadow = FALSE AND asset_id = ?
            GROUP BY asset_id
        )
        SELECT
            SUM(h.quantity)           AS qty,
            MAX(h.cost_price_unit)    AS cost_price_unit,
            MAX(h.market_price_unit)  AS market_price_unit,
            SUM(h.market_value)       AS market_value,
            MAX(h.snapshot_date)      AS snapshot_date,
            MAX(h.currency)           AS currency
        FROM holdings h
        JOIN latest_per_asset lpa
          ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        WHERE h.is_shadow = FALSE AND h.asset_id = ?
        """,
        [asset_id, asset_id],
    ).fetchone()

    position = None
    if holding_row and holding_row[0] is not None:
        # R2-1: freshness verdict supplies price_date (GREATEST of snapshot_date
        # and price_updated_at) and the freshness classification.
        fv = freshness_verdict(db, asset_id)
        position = {
            "qty": float(holding_row[0]),
            "cost_price_unit": float(holding_row[1] or 0),
            "market_price_unit": float(holding_row[2] or 0),
            "price": float(holding_row[2] or 0),       # R2-1: alias for market_price_unit
            "market_value": float(holding_row[3] or 0),
            "snapshot_date": str(holding_row[4]) if holding_row[4] else None,
            "currency": str(holding_row[5] or "CNY"),
            "price_date": str(fv["price_date"]) if fv["price_date"] else None,  # R2-1
            "freshness": fv,                            # R2-1: full verdict dict
        }

    loss = {
        "unrealized_return_pct": unrealized_return_pct,
        "trigger_threshold_pct": trigger_threshold_pct,
        "days_open": _days_open(opened_at),
    }

    # Fix 2: memo linkage state replaces the single originating_memo lookup.
    state = linkage_state(db, asset_id)
    memos = memos_for_asset(db, asset_id)

    if state == "linked":
        display_text = None
    elif state == "confirmed_none":
        display_text = "No memo on record (confirmed by owner)"
    else:  # unresolved
        display_text = UNRESOLVED_DISPLAY

    memo_linkage = {
        "state": state,
        "memos": memos,
        "display_text": display_text,
    }

    # Total trade_logs count for the asset (for "showing 5 of N" footer)
    total_row = db.execute(
        "SELECT COUNT(*) FROM trade_logs WHERE asset_id = ?",
        [asset_id],
    ).fetchone()
    decision_history_total = int(total_row[0]) if total_row else 0

    # Last 5 decision_history entries for the asset
    history_rows = db.execute(
        """
        SELECT log_date, action, quantity, price, rule_bucket, verification_status
        FROM trade_logs
        WHERE asset_id = ?
        ORDER BY log_date DESC, id DESC
        LIMIT 5
        """,
        [asset_id],
    ).fetchall()
    decision_history = [
        {
            "log_date": str(r[0]) if r[0] else None,
            "action": r[1],
            "quantity": float(r[2]) if r[2] is not None else None,
            "price": float(r[3]) if r[3] is not None else None,
            "rule_bucket": r[4],
            "verification_status": r[5],
        }
        for r in history_rows
    ]

    # R2-3: Lot detail from FIFO replay (cap at 200 entries).
    # current_lot_cost() returns {"avg_cost", "open_qty", "lots"} or None.
    _LOT_CAP = 200
    raw_lot_info = current_lot_cost(db, asset_id)
    if raw_lot_info is not None:
        all_lots = raw_lot_info["lots"]
        lot_detail: Optional[dict] = {
            "open_lot_count": len(all_lots),
            "open_qty": raw_lot_info["open_qty"],
            "avg_cost": raw_lot_info["avg_cost"],
            "lots": all_lots[:_LOT_CAP],
            "truncated": len(all_lots) > _LOT_CAP,
        }
    else:
        lot_detail = None

    # case_file: asset_id so the frontend can build /asset-case-file?asset_id=<id>
    case_file = {"asset_id": asset_id}

    return {
        "review_id": review_id,
        "asset_id": asset_id,
        "position": position,
        "loss": loss,
        "memo_linkage": memo_linkage,
        "decision_history": decision_history,
        "decision_history_total": decision_history_total,
        "lot_detail": lot_detail,
        "case_file": case_file,
    }


@router.get("/{review_id}/context")
async def get_value_trap_context(
    review_id: int,
    db: DatabaseConnector = Depends(get_db),
):
    """Return Huinsight context for a value-trap review (WS2 — F2.3 context panel).

    Includes: position (latest non-shadow holding), loss stats, memo_linkage
    (Fix 2 — three-state: linked/confirmed_none/unresolved), last-5 decision
    history rows, and case_file asset_id for deep-link.
    """
    try:
        return _build_context_payload(review_id, db)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_value_trap_context failed")
        return api_error_response(e, context="value_trap_context")


@router.post("/{review_id}/draft")
async def draft_value_trap_review(
    review_id: int,
    db: DatabaseConnector = Depends(get_db),
):
    """LLM pre-draft of the three F2.3 answers (WS2).

    Returns draft text for thesis_restated, falsification_check,
    would_buy_today. Draft is NOT persisted — it fills the form only.
    HTTP 503 if no LLM API key is configured.

    Fix 3 constraints (2026-07-10):
    - Price decline, unrealized loss %, and the trigger threshold are
      INADMISSIBLE as falsification evidence — the trigger opens the review,
      it is not the verdict. The system prompt asserts this explicitly.
    - The reduced context payload passed to the LLM omits loss numbers so
      the model cannot reason from them even if it tries.
    - Linked memo falsification clauses are included in the prompt.
    - Unresolved linkage → prompt mandates "memo linkage not backfilled;
      confirm whether a memo exists before ruling" in the thesis section.
    - No ruling recommendation or value-trap conclusion in the draft.
    """
    try:
        from src.services.llm_client import LLMClient

        client = LLMClient()
        if not client.is_available():
            raise HTTPException(
                status_code=503,
                detail=(
                    "No LLM API key configured — set GEMINI_API_KEY, "
                    "ANTHROPIC_API_KEY, or DEEPSEEK_API_KEY to enable AI drafts"
                ),
            )

        # Load context (raises 404 if review not found)
        ctx = _build_context_payload(review_id, db)
        asset_id = ctx["asset_id"]

        # Fix 3: memos for context; linkage state determines thesis-section instruction.
        memos = memos_for_asset(db, asset_id)
        state = ctx["memo_linkage"]["state"]

        # ── Reduced context (Fix 3 + R2-6 belt-and-braces) ──────────────
        # Strip loss %, threshold, and trigger info — those numbers must not
        # be used as falsification evidence. Pass only position facts, memo
        # content, and decision history.
        #
        # R2-6: additionally strip cost_price_unit (avg cost) from position and
        # individual entry prices from decision_history — both are inadmissible
        # for would-buy-today reasoning. Consistent with how Fix 3 stripped the
        # loss object: exclude from context so the model cannot reason from them.
        reduced_position = None
        if ctx["position"] is not None:
            reduced_position = {
                k: v for k, v in ctx["position"].items()
                if k != "cost_price_unit"
            }

        reduced_history = [
            {k: v for k, v in entry.items() if k != "price"}
            for entry in ctx["decision_history"]
        ]

        reduced_ctx = {
            "review_id": ctx["review_id"],
            "asset_id": asset_id,
            "position": reduced_position,       # cost_price_unit stripped (R2-6)
            "memo_linkage": {                   # memos with falsification_summary
                "state": state,
                "memos": memos,
            },
            "decision_history": reduced_history,  # entry prices stripped (R2-6)
        }
        # The loss object (loss %, threshold, days_open) is intentionally
        # excluded from the LLM context to prevent circular reasoning.
        # cost_price_unit and individual trade prices are excluded for the same
        # reason: they are inadmissible for would-buy-today reasoning (R2-6).

        ctx_str = _json.dumps(reduced_ctx, ensure_ascii=False, default=str)

        # ── Memo / thesis-section instruction ────────────────────────────
        if state == "linked" and memos:
            memo_ids = ", ".join(m["memo_id"] for m in memos)
            falsification_clauses = "; ".join(
                f"{m['memo_id']}: {m['falsification_summary']}"
                for m in memos
                if m.get("falsification_summary")
            )
            thesis_instruction = (
                f"Linked memos ({memo_ids}) are included in the context. "
                "For the thesis section, draw on those memo(s) to restate the original "
                "investment case and assess whether it still holds. "
                "For the falsification section, enumerate the memo's conditions "
                f"({falsification_clauses}) and assess whether current data triggers any of them."
            )
        elif state == "confirmed_none":
            thesis_instruction = (
                "The owner has confirmed no formal memo exists for this asset. "
                "For the thesis section, state that no formal memo is recorded; "
                "do not infer a thesis from buying patterns as a substitute."
            )
        else:  # unresolved
            thesis_instruction = (
                "IMPORTANT: memo linkage is unresolved — it has not been confirmed whether "
                "a memo exists or not. For the thesis section, you MUST output exactly: "
                "'memo linkage not backfilled; confirm whether a memo exists before ruling'. "
                "Never claim the thesis is undocumented. Never infer a thesis from buying patterns."
            )

        system_prompt = (
            "You are an investment discipline assistant helping an investor conduct "
            "a structured value-trap review. Write concise, honest drafts for three "
            "required questions (2-4 sentences each). "
            "HARD CONSTRAINTS — violation of any of these is a framework error:\n"
            "1. INADMISSIBLE evidence: price decline, unrealized loss percentage, and "
            "the trigger threshold that opened this review are NOT admissible as "
            "falsification evidence. The trigger opens the review; it is not the verdict. "
            "Using price action as falsification reasoning is circular and explicitly banned.\n"
            "2. ADMISSIBLE falsification evidence: only fundamental conditions from the "
            "linked memo's falsification clauses checked against current data (e.g. "
            "valuation percentile, strategy drift, operating metrics). If the required "
            "fundamental data is unavailable, the draft MUST state: "
            "'falsification check requires data: [list what is missing]' — "
            "do NOT substitute price action.\n"
            "3. NO ruling recommendation: do not include conclusions such as "
            "'this is a value trap', 'consider liquidating', or similar. "
            "The ruling is the owner's decision, not yours.\n"
            "4. WOULD-BUY-TODAY (cost-basis inadmissibility): The would-buy-today "
            "section MUST evaluate current price against valuation evidence only "
            "(sector/fund valuation percentile, anchors from the linked memo's "
            "valuation framework). The owner's historical purchase prices, average "
            "cost, and entry points are INADMISSIBLE for reasoning in this section — "
            "do NOT compare the current price to the owner's cost basis or prior entry "
            "prices. Margin-of-safety analysis must use intrinsic-value or valuation-"
            "percentile anchors, never the owner's own purchase history. If valuation "
            "data is unavailable, the draft MUST state: 'would-buy-today assessment "
            "requires data: [list what is missing]'.\n"
            "5. Thesis section: follow the memo-linkage instruction below.\n\n"
            f"Memo-linkage instruction: {thesis_instruction}\n\n"
            "Return JSON only with keys: thesis_draft, falsification_draft, buy_today_draft."
        )
        user_prompt = (
            f"Review context (JSON) — NOTE: loss percentages, trigger threshold, "
            f"average cost (cost_price_unit), and individual trade entry prices are "
            f"intentionally excluded from this context; do not request or reference "
            f"them:\n{ctx_str}\n\n"
            "Draft honest answers to these three questions:\n"
            "1. Thesis restated: What was the original investment case (from the linked "
            "memo if available), and does it still hold based on fundamental data?\n"
            "2. Falsification check: What conditions in the memo would prove the thesis "
            "wrong? Have any of those fundamental signals appeared? "
            "(price decline is not admissible — cite only the memo clauses and "
            "current fundamental data or state what data is needed)\n"
            "3. Would you buy today: Based on current valuation evidence only "
            "(sector/fund valuation percentile, memo valuation anchors) — NOT the "
            "owner's purchase prices, average cost, or entry points — would a rational "
            "investor initiate a fresh position today? If valuation data is unavailable, "
            "state what is needed.\n\n"
            'Return JSON: {"thesis_draft": "...", '
            '"falsification_draft": "...", "buy_today_draft": "..."}'
        )

        response = client.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            expect_json=True,
            report_type="value_trap_draft",
        )

        parsed = response.content_json or {}
        return {
            "thesis_draft": str(parsed.get("thesis_draft", "")),
            "falsification_draft": str(parsed.get("falsification_draft", "")),
            "buy_today_draft": str(parsed.get("buy_today_draft", "")),
            "model": response.model_used,
        }
    except HTTPException:
        raise
    except Exception as e:
        # Re-map LLMAllModelsFailedError to 503 (lazy import: the module-level
        # path never imports llm_client; isinstance beats class-name string-matching)
        from src.services.llm_client import LLMAllModelsFailedError

        if isinstance(e, LLMAllModelsFailedError):
            raise HTTPException(
                status_code=503,
                detail=f"LLM unavailable — all configured models failed: {e}",
            )
        logger.exception("draft_value_trap_review failed")
        return api_error_response(e, context="value_trap_draft")
