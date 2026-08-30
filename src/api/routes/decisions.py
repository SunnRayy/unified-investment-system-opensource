from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from src.api.dependencies import get_db
from src.config import load_config
from src.database.connector import DatabaseConnector
import json
from src.services.decision_scorer import (
    build_ai_attribution_scope_sql,
    build_trade_display_scope_sql,
    compute_insight_adoption_metrics,
)
from src.services.decision_intelligence import (
    build_scorecard_reason,
    find_linked_insight,
    get_decision_intelligence,
    resolve_trade_linkage,
)
from src.services.alert_generator import generate_alerts
from src.services.verification_config import load_verification_config
from src.services.process_scorer import (
    bucket_display_state,
    compute_process_aggregates,
    compute_quarterly_outcome_report,
    evaluate_process,
    outcome_info,
)

router = APIRouter(prefix="/decisions", tags=["Decisions"])


def _display_source(source: str | None, raw_source: str | None = None) -> str:
    text = (source or "").strip()
    if text and text not in {"unknown", "other"}:
        return text
    raw = (raw_source or "").strip()
    return raw or "system"


def _trade_logs_has_linked_memo_col(db: DatabaseConnector) -> bool:
    return _trade_logs_has_column(db, "linked_memo_id")


def _trade_logs_has_column(db: DatabaseConnector, column_name: str) -> bool:
    """Defensive column-existence check — process_verification columns (migration
    010) may be absent from an older/synthetic DB even when the flag is on."""
    try:
        cols = db.execute("PRAGMA table_info('trade_logs')").fetchall()
    except Exception:
        return False
    return any(str(c[1]).lower() == column_name.lower() for c in cols)

@router.get("/timeline")
async def get_decisions_timeline(
    limit: int = 50,
    type: str = Query(default="all"),
    db: DatabaseConnector = Depends(get_db)
):
    """Get merged timeline of insights, trades, and drift alerts."""
    
    items = []
    display_scope = build_trade_display_scope_sql("tl")
    
    # 1. Fetch Insights
    if type in ["all", "insight"]:
        insights = db.execute("""
            SELECT 
                id, insight_date, title, content, category,
                adopted, ai_model, tags, observation_source
            FROM insights
            WHERE COALESCE(category, '') != 'lesson'
            ORDER BY insight_date DESC
            LIMIT ?
        """, (limit,)).fetchall()
        
        for r in insights:
            status = "pending"
            if r[5] == 1:
                status = "adopted"
            elif r[5] == 0:
                status = "rejected"
            
            items.append({
                "id": f"insight_{r[0]}",
                "type": "insight",
                "date": str(r[1]),
                "title": r[2] or "Untitled Insight",
                "content": r[3],
                "source": _display_source(r[6], r[8]),
                "status": status,
                "subtype": r[4] or "insight",
                "display_source": _display_source(r[6], r[8]),
                "display_status": status,
                "match_status": None,
                "origin_ref": f"insights:{r[0]}",
                "metadata": {
                    "category": r[4],
                    "tags": json.loads(r[7]) if r[7] else []
                }
            })

    # 2. Fetch Drift Alerts
    if type in ["all", "drift"]:
        drifts = db.execute("""
            SELECT 
                id, created_at, asset_class, deviation_pct, 
                tolerance_pct, status
            FROM deviation_actions
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        
        for r in drifts:
            items.append({
                "id": f"drift_{r[0]}",
                "type": "drift",
                "date": str(r[1]),
                "title": f"{r[2]} deviation {r[3]}%",
                "content": f"Deviation: {r[3]}% (Tolerance: {r[4]}%)",
                "source": "system",
                "status": r[5],
                "metadata": {
                    "asset_class": r[2],
                    "deviation_pct": r[3]
                }
            })

    # 3. Fetch Trades
    if type in ["all", "trade"]:
        trades = db.execute(f"""
            SELECT 
                tl.id, tl.log_date, tl.asset_id, tl.action, 
                tl.quantity, tl.price, tl.amount,
                tl.suggestion_source, tl.decision_reason, tl.ai_suggestion,
                tl.verification_status
            FROM trade_logs tl
            WHERE {display_scope}
            ORDER BY tl.log_date DESC
            LIMIT ?
        """, (limit,)).fetchall()
        
        for r in trades:
            linkage = resolve_trade_linkage(
                db,
                r[2],
                r[1],
                suggestion_source=r[7],
                decision_reason=r[8],
                ai_suggestion=r[9],
            )
            items.append({
                "id": f"trade_{r[0]}",
                "type": "trade",
                "date": str(r[1]),
                "title": f"Order Executed: {r[3]} {r[2]}",
                "content": f"{r[3]} {float(r[4]) if r[4] else 0} units @ {float(r[5]) if r[5] else 0}",
                "source": linkage["effective_source"],
                "status": "executed",
                "subtype": "trade",
                "display_source": linkage["display_source"],
                "display_status": "executed",
                "match_status": linkage["match_status"],
                "verification_status": r[10] or "pending",
                "origin_ref": f"trade_logs:{r[0]}",
                "metadata": {
                    "asset_id": r[2],
                    "action": r[3],
                    "amount": float(r[6]) if r[6] else 0,
                    "linked_title": linkage["linked_title"],
                    "linked_ref": linkage["linked_ref"],
                    "effective_source": linkage["effective_source"],
                    "reason_excerpt": linkage["reason_excerpt"],
                }
            })
            
    # Sort by date DESC and slice
    items.sort(key=lambda x: x["date"], reverse=True)
    return {
        "items": items[:limit],
        "summary": {
            "total": len(items),
            "adopted": sum(1 for i in items if i["status"] == "adopted"),
            "pending": sum(1 for i in items if i["status"] == "pending")
        }
    }

@router.get("/stats")
async def get_decisions_stats(db: DatabaseConnector = Depends(get_db)):
    """Get high-level decision statistics."""
    
    # Insight Stats — shared function keeps Decision Hub in sync with Review Center snapshots
    _adoption = compute_insight_adoption_metrics(db)
    total_insights = _adoption["total_insights"]
    adopted_count = _adoption["adopted_count"]
    pending_count = _adoption["pending_count"]
    adoption_rate = _adoption["adoption_rate"]
    alerts = generate_alerts(db)
    pending_actions_count = len(alerts)
    active_drift_alerts = sum(1 for alert in alerts if alert.get("category") == "drift")
    
    ai_scope = build_ai_attribution_scope_sql(
        "tl",
        include_linked_memo=_trade_logs_has_linked_memo_col(db),
    )
    ai_stats = db.execute(
        f"""
        SELECT
            COUNT(*) AS ai_trades_total,
            SUM(CASE WHEN tl.verdict IS NOT NULL THEN 1 ELSE 0 END) AS ai_scored_total,
            MAX(tl.log_date) AS ai_last_sync_date
        FROM trade_logs tl
        WHERE {ai_scope}
        """
    ).fetchone()

    stats = {
        "total_insights": total_insights,
        "adopted_count": adopted_count,
        "pending_count": pending_count,
        "pending_actions_count": pending_actions_count,
        "adoption_rate": round(adoption_rate, 1),
        "total_trades": db.execute("SELECT COUNT(*) FROM trade_logs").fetchone()[0],
        "active_drift_alerts": active_drift_alerts,
        "ai_trades_total": int(ai_stats[0] or 0),
        "ai_scored_total": int(ai_stats[1] or 0),
        "ai_last_sync_date": str(ai_stats[2]) if ai_stats[2] is not None else None,
    }

    # F1.2 (flag-gated): once process_verification is enabled, add bucket-aware
    # process aggregates. Additive only — every flag-off field above is unchanged.
    cfg = load_verification_config()
    if cfg.process_verification.enabled and _trade_logs_has_column(db, "rule_bucket"):
        stats["process_verification"] = compute_process_aggregates(
            db, build_trade_display_scope_sql("tl"), cfg
        )

    return stats


@router.get("/scorecard")
async def get_decisions_scorecard(
    limit: int = 50,
    db: DatabaseConnector = Depends(get_db),
):
    """Scored trades with verdict, grade, and outcome."""
    from src.services.decision_scorer import score_all_trades
    display_scope = build_trade_display_scope_sql("tl")

    cfg = load_verification_config()
    process_flag_on = cfg.process_verification.enabled

    writable_db = None
    db_for_query = db
    if getattr(db, "read_only", False):
        db_path = db.db_path
        db.close()
        try:
            writable_db = DatabaseConnector(db_path, read_only=False)
            db_for_query = writable_db
        except Exception:
            # DB busy (concurrent requests hold read-only locks); reopen read-only
            db_for_query = DatabaseConnector(db_path, read_only=True)
            writable_db = db_for_query

    try:
        try:
            score_all_trades(db_for_query)
        except Exception:
            pass  # best-effort: return already-scored data if write lock unavailable

        # F1.2/F1.3 (flag-gated): pull the process-check columns too, defensively —
        # a DB predating migration 010 simply doesn't get the extra columns/fields.
        has_process_cols = process_flag_on and _trade_logs_has_column(db_for_query, "rule_bucket")
        process_select = (
            ", tl.rule_bucket, tl.memo_id, tl.process_authorized, tl.process_params_ok,"
            " tl.process_data_verified"
            if has_process_cols
            else ""
        )
        rows = db_for_query.execute(
            f"""
            SELECT
                tl.id, tl.log_date, tl.asset_id, tl.asset_name, tl.action,
                tl.price, tl.quantity, tl.amount,
                tl.suggestion_source, tl.verification_date, tl.verification_result,
                tl.verdict, tl.outcome_pct, tl.decision_grade,
                tl.ai_suggestion, tl.decision_reason,
                tl.verification_status{process_select}
            FROM trade_logs tl
            WHERE ({display_scope})
            ORDER BY tl.log_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        response = {
            "items": []
        }
        for r in rows:
            linked = find_linked_insight(
                db_for_query,
                r[2],
                r[1],
                ai_suggestion=r[14],
                decision_reason=r[15],
                suggestion_source=r[8],
            )
            item = {
                "id": r[0],
                "date": str(r[1]),
                "asset_id": r[2],
                "asset_name": r[3],
                "action": r[4],
                "price": float(r[5]) if r[5] is not None else None,
                "quantity": float(r[6]) if r[6] is not None else None,
                "amount": float(r[7]) if r[7] is not None else None,
                "source": linked["display_source"] if linked and (not r[8] or str(r[8]).strip().lower() in {"unknown", "other", "system"}) else r[8],
                "verification_date": str(r[9]) if r[9] else None,
                "verification_result": r[10],
                "verdict": r[11],
                "outcome_pct": float(r[12]) if r[12] is not None else None,
                "grade": r[13],
                "linked_insight_id": linked["id"] if linked else None,
                "linked_insight_title": linked["title"] if linked else None,
                "match_status": linked["match_status"] if linked else "unmatched",
                "verification_status": r[16] or "pending",
                "why_unscored": build_scorecard_reason(r[9], r[10], linked, r[14]),
            }

            # F1.2/F1.3: once the flag is on, the legacy emotive verdict is NEVER
            # serialized (replaced by null) and is superseded by rule_bucket +
            # process (PASS/FAIL/UNSCORED + Compliant/Violation/unreviewed display
            # state) + outcome_info (value-bucket-only, no verdict word — PRD F1.3).
            if process_flag_on:
                rule_bucket = r[17] if has_process_cols else None
                memo_id = r[18] if has_process_cols else None
                process_row = {
                    "process_authorized": r[19] if has_process_cols else None,
                    "process_params_ok": r[20] if has_process_cols else None,
                    "process_data_verified": r[21] if has_process_cols else None,
                }
                process_result = evaluate_process(process_row)
                display = bucket_display_state(rule_bucket, process_result)
                item["verdict"] = None
                item["rule_bucket"] = display["rule_bucket"]
                item["memo_id"] = memo_id
                item["process"] = {
                    "status": process_result["status"],
                    "failing_checks": process_result["failing_checks"],
                    "checked": process_result["checked"],
                    "state": display["state"],
                }
                item["outcome_info"] = outcome_info(
                    {
                        "rule_bucket": rule_bucket,
                        "log_date": r[1],
                        "outcome_pct": r[12],
                    },
                    date.today(),
                    cfg.process_verification.outcome_window_days,
                )

            response["items"].append(item)
        return response
    finally:
        if writable_db:
            writable_db.close()


@router.get("/funnel")
async def get_decisions_funnel(db: DatabaseConnector = Depends(get_db)):
    """Adoption funnel: total -> adopted -> scored outcomes."""
    from src.services.decision_scorer import compute_adoption_funnel

    return compute_adoption_funnel(db)


@router.get("/leaderboard")
async def get_decisions_leaderboard(db: DatabaseConnector = Depends(get_db)):
    """Per-source hit rates."""
    from src.services.decision_scorer import compute_leaderboard

    return {"sources": compute_leaderboard(db)}


@router.get("/intelligence")
async def get_decisions_intelligence(db: DatabaseConnector = Depends(get_db)):
    """Structured intelligence view for lessons, source mix, and raw excerpts."""
    return get_decision_intelligence(db, load_config())


@router.get("/alerts")
async def get_decision_alerts(db: DatabaseConnector = Depends(get_db)):
    """Strategy-aware action alerts for the Action Inbox."""
    from src.services.alert_generator import generate_alerts
    alerts = generate_alerts(db)
    return {
        "alerts": alerts,
        "counts": {
            "high": sum(1 for a in alerts if a["priority"] == "high"),
            "medium": sum(1 for a in alerts if a["priority"] == "medium"),
            "low": sum(1 for a in alerts if a["priority"] == "low"),
        }
    }


@router.get("/quarterly-outcome-report")
async def get_quarterly_outcome_report(
    year: int = Query(...),
    quarter: int = Query(...),
    db: DatabaseConnector = Depends(get_db),
):
    """PRD F1.3 — value-bucket entry/exit decisions vs. subsequent price outcome,
    grouped by memo_id, for one quarter. Works regardless of the process_verification
    flag (it exposes no emotive verdicts by construction — informational only)."""
    if quarter not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="quarter must be between 1 and 4")
    return compute_quarterly_outcome_report(db, year, quarter)
