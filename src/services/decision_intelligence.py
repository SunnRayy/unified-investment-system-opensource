"""Decision Hub intelligence helpers."""

from __future__ import annotations

import weakref
from datetime import date, datetime, timedelta
from typing import Any

GENERIC_SOURCES = {"", "unknown", "other", "system", "imported"}
MANUAL_SOURCES = {"manual", "human", "user"}
SOURCE_ALIASES = {"strategy_memo": "memo"}

# WeakKeyDictionary so entries are automatically removed when the DB object is GC'd,
# preventing the id(db) address-reuse bug between in-memory test databases.
_STRATEGY_MEMOS_HAS_CONTENT_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


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


def _extract_ticker(asset_id: str | None) -> str:
    token = (asset_id or "").split("_")[-1]
    return token.split(".")[0].upper()


def _strategy_memos_has_content_col(db: Any) -> bool:
    try:
        cached = _STRATEGY_MEMOS_HAS_CONTENT_CACHE.get(db)
    except TypeError:
        cached = None
    if cached is not None:
        return cached
    cols = db.execute("PRAGMA table_info('strategy_memos')").fetchall()
    has_content = any(str(c[1]).lower() == "content" for c in cols)
    if has_content:
        try:
            _STRATEGY_MEMOS_HAS_CONTENT_CACHE[db] = True
        except TypeError:
            pass
    return has_content


def _trade_logs_has_linked_memo_col(db: Any) -> bool:
    cols = db.execute("PRAGMA table_info('trade_logs')").fetchall()
    return any(str(c[1]).lower() == "linked_memo_id" for c in cols)


def _display_source(source_family: str | None, source_raw: str | None) -> str:
    source = normalize_source(source_family)
    if source and source not in {"unknown", "other"}:
        return source
    raw = (source_raw or "").strip()
    return raw or "system"


def normalize_source(source: str | None) -> str:
    text = (source or "").strip().lower()
    if text in SOURCE_ALIASES:
        return SOURCE_ALIASES[text]
    return text or "unknown"


def infer_source_from_evidence(ai_suggestion: str | None, decision_reason: str | None) -> str | None:
    text = " ".join(part for part in [ai_suggestion or "", decision_reason or ""] if part).lower()
    if not text:
        return None
    if any(token in text for token in ("/brief", "简报")):
        return "brief"
    if any(token in text for token in ("/committee", "投委会", "策略官")):
        return "committee"
    if any(token in text for token in ("/analyze", "分析")):
        return "analyze"
    if any(token in text for token in ("自主决策",)):
        return "self_decision"
    if any(token in text for token in ("用户干预纠错",)):
        return "user_correction"
    if any(token in text for token in ("突发应对",)):
        return "emergency_response"
    if any(token in text for token in ("memo", "备忘录", "战略 memo", "strategy memo")):
        return "memo"
    return None


def _extract_reason_excerpt(ai_suggestion: str | None, decision_reason: str | None, max_len: int = 120) -> str | None:
    raw = (ai_suggestion or decision_reason or "").strip()
    if not raw:
        return None
    compact = " ".join(raw.split())
    if len(compact) <= max_len:
        return compact
    return f"{compact[:max_len - 3]}..."


def _build_linked_ref(linked: dict[str, Any] | None) -> str | None:
    if not linked:
        return None
    linked_id = linked.get("id")
    if isinstance(linked_id, int):
        return f"insights:{linked_id}"
    if isinstance(linked_id, str):
        if linked_id.startswith("memo_"):
            return f"strategy_memos:{linked_id.split('_', 1)[1]}"
        if linked_id.isdigit():
            return f"insights:{linked_id}"
        return linked_id
    return None


def _fetch_insight_candidates(db: Any, trade_date: Any, asset_id: str | None) -> list[tuple]:
    dt = _to_date(trade_date)
    if not dt:
        return []
    ticker = _extract_ticker(asset_id)
    cols = db.execute("PRAGMA table_info('insights')").fetchall()
    has_title = any(str(col[1]).lower() == "title" for col in cols)
    if has_title:
        candidates = db.execute(
            """
            SELECT id, insight_date, title, content, ai_model, observation_source
            FROM insights
            WHERE COALESCE(category, '') != 'lesson'
              AND insight_date BETWEEN ? AND ?
            ORDER BY insight_date DESC, id DESC
            """,
            (dt - timedelta(days=3), dt + timedelta(days=3)),
        ).fetchall()
    else:
        candidates = db.execute(
            """
            SELECT id, insight_date, '' AS title, content, ai_model, observation_source
            FROM insights
            WHERE COALESCE(category, '') != 'lesson'
              AND insight_date BETWEEN ? AND ?
            ORDER BY insight_date DESC, id DESC
            """,
            (dt - timedelta(days=3), dt + timedelta(days=3)),
        ).fetchall()
    if not ticker:
        return candidates
    ranked: list[tuple[int, tuple]] = []
    for row in candidates:
        title = row[2] or ""
        content = row[3] or ""
        haystack = f"{title} {content}".upper()
        if ticker in haystack:
            ranked.append((2, row))
        else:
            ranked.append((1, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in ranked]


def _find_linked_memo(
    db: Any,
    asset_id: str | None,
    trade_date: Any,
) -> dict[str, Any] | None:
    """Fallback: find a strategy memo that directed this trade."""
    dt = _to_date(trade_date)
    if not dt:
        return None
    ticker = _extract_ticker(asset_id or "")
    if not ticker:
        return None

    has_content = _strategy_memos_has_content_col(db)
    select_content = ", content" if has_content else ", NULL AS content"
    memos = db.execute(
        f"""
        SELECT id, memo_date, title, key_directives, strategic_bias{select_content}
        FROM strategy_memos
        WHERE memo_date BETWEEN ? AND ?
        ORDER BY memo_date DESC
        """,
        (dt - timedelta(days=90), dt),
    ).fetchall()

    for row in memos:
        memo_id, memo_date, title, key_directives, strategic_bias, content = row
        haystack = f"{title or ''} {key_directives or ''} {content or ''}".upper()
        if ticker in haystack:
            return {
                "id": f"memo_{memo_id}",
                "title": title or f"Strategy Memo {memo_date}",
                "source": "strategy_memo",
                "display_source": "Strategy Memo",
                "match_status": "memo_linked",
            }
    return None


def find_linked_insight(
    db: Any,
    asset_id: str | None,
    trade_date: Any,
    ai_suggestion: str | None = None,
    decision_reason: str | None = None,
    suggestion_source: str | None = None,
) -> dict[str, Any] | None:
    candidates = _fetch_insight_candidates(db, trade_date, asset_id)
    if not candidates:
        return _find_linked_memo(db, asset_id, trade_date)

    evidence = " ".join(part for part in [ai_suggestion or "", decision_reason or ""] if part).upper()
    for row in candidates:
        title = row[2] or ""
        content = row[3] or ""
        if evidence and ((title and title.upper() in evidence) or (content and content.upper() in evidence)):
            return {
                "id": row[0],
                "title": title or content,
                "source": row[4],
                "display_source": _display_source(row[4], row[5]),
                "match_status": "matched",
            }

    first = candidates[0]
    source = normalize_source(suggestion_source)
    inferred_status = "inferred"
    if source and first[4] and source == normalize_source(first[4]):
        inferred_status = "matched"
    return {
        "id": first[0],
        "title": first[2] or first[3],
        "source": normalize_source(first[4]),
        "display_source": _display_source(first[4], first[5]),
        "match_status": inferred_status,
    }


def resolve_trade_linkage(
    db: Any,
    asset_id: str | None,
    trade_date: Any,
    suggestion_source: str | None = None,
    ai_suggestion: str | None = None,
    decision_reason: str | None = None,
) -> dict[str, Any]:
    linked = find_linked_insight(
        db,
        asset_id,
        trade_date,
        ai_suggestion=ai_suggestion,
        decision_reason=decision_reason,
        suggestion_source=suggestion_source,
    )
    explicit_source = normalize_source(suggestion_source)
    if explicit_source in GENERIC_SOURCES or explicit_source in MANUAL_SOURCES:
        explicit_source = None
    evidence_source = infer_source_from_evidence(ai_suggestion, decision_reason)
    linked_source = normalize_source(linked["source"]) if linked else None
    if linked_source in GENERIC_SOURCES or linked_source in MANUAL_SOURCES:
        linked_source = None

    effective_source = explicit_source or evidence_source or linked_source or normalize_source(suggestion_source)
    match_status = "unmatched"
    if linked:
        match_status = linked.get("match_status") or "matched"
    elif effective_source not in GENERIC_SOURCES and effective_source not in MANUAL_SOURCES:
        match_status = "source_only"

    linked_ref = _build_linked_ref(linked)
    linked_title = linked.get("title") if linked else None
    linked_insight_id = linked.get("id") if linked and isinstance(linked.get("id"), int) else None

    return {
        "effective_source": effective_source,
        "display_source": _display_source(effective_source, suggestion_source),
        "match_status": match_status,
        "linked_ref": linked_ref,
        "linked_title": linked_title,
        "linked_insight_id": linked_insight_id,
        "reason_excerpt": _extract_reason_excerpt(ai_suggestion, decision_reason),
    }


def build_scorecard_reason(
    verification_date: Any,
    verification_result: str | None,
    linked_insight: dict[str, Any] | None,
    ai_suggestion: str | None,
) -> str | None:
    if verification_result:
        return None
    verify_on = _to_date(verification_date)
    today = date.today()
    if verify_on and verify_on > today:
        return "awaiting_verification_window"
    if linked_insight is None:
        return "trade_without_insight_link"
    if ai_suggestion:
        return "verification_pending"
    return "source_only"


def get_decision_intelligence(db: Any, config: dict[str, Any]) -> dict[str, Any]:
    from src.services.decision_scorer import (
        build_ai_attribution_scope_sql,
        compute_adoption_funnel,
        compute_leaderboard,
    )

    source_rows = db.execute(
        """
        SELECT
            COALESCE(ai_model, 'other') AS source,
            COUNT(*) AS total,
            SUM(CASE WHEN adopted = 1 THEN 1 ELSE 0 END) AS adopted,
            SUM(CASE WHEN adopted = 0 THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN adopted IS NULL THEN 1 ELSE 0 END) AS pending
        FROM insights
        WHERE COALESCE(category, '') != 'lesson'
        GROUP BY COALESCE(ai_model, 'other')
        ORDER BY total DESC, source ASC
        """
    ).fetchall()

    growth_rows = db.execute(
        """
        SELECT id, insight_date, title, content, ai_model, observation_source
        FROM insights
        WHERE category = 'lesson'
        ORDER BY insight_date DESC, id DESC
        LIMIT 20
        """
    ).fetchall()

    ai_scope = build_ai_attribution_scope_sql(
        "tl",
        include_linked_memo=_trade_logs_has_linked_memo_col(db),
    )
    trade_rows = db.execute(
        f"""
        SELECT
            tl.log_date, tl.asset_id, tl.suggestion_source,
            tl.ai_suggestion, tl.decision_reason
        FROM trade_logs tl
        WHERE {ai_scope}
        """
    ).fetchall()
    linked_trades_by_source: dict[str, int] = {}
    for log_date, asset_id, suggestion_source, ai_suggestion, decision_reason in trade_rows:
        linkage = resolve_trade_linkage(
            db,
            asset_id,
            log_date,
            suggestion_source=suggestion_source,
            ai_suggestion=ai_suggestion,
            decision_reason=decision_reason,
        )
        src = normalize_source(linkage["effective_source"])
        if src in GENERIC_SOURCES or src in MANUAL_SOURCES:
            continue
        linked_trades_by_source[src] = linked_trades_by_source.get(src, 0) + 1
    linked_adopted_trades = sum(linked_trades_by_source.values())

    source_stats: dict[str, dict[str, Any]] = {}
    for source, total, adopted, rejected, pending in source_rows:
        normalized = normalize_source(source)
        # Collapse all generic keys ('', 'other', 'unknown', …) into ONE 'system'
        # bucket BEFORE inserting into the dict, so the API never returns two
        # separate rows both labelled "system".
        bucket_key = "system" if normalized in GENERIC_SOURCES else normalized
        display = _display_source(normalized, None)
        if bucket_key in source_stats:
            entry = source_stats[bucket_key]
            entry["total"] += total or 0
            entry["adopted"] += adopted or 0
            entry["rejected"] += rejected or 0
            entry["pending"] += pending or 0
        else:
            source_stats[bucket_key] = {
                "source": display,
                "total": total or 0,
                "adopted": adopted or 0,
                "rejected": rejected or 0,
                "pending": pending or 0,
                "linked_trades": linked_trades_by_source.get(bucket_key, 0),
            }
    for source, linked_count in linked_trades_by_source.items():
        if source in source_stats:
            continue
        source_stats[source] = {
            "source": _display_source(source, None),
            "total": 0,
            "adopted": 0,
            "rejected": 0,
            "pending": 0,
            "linked_trades": linked_count,
        }

    funnel = compute_adoption_funnel(db)
    funnel["linked_adopted_trades"] = linked_adopted_trades

    return {
        "decision_patterns": {
            "funnel": funnel,
            "leaderboard": compute_leaderboard(db),
            "sources": sorted(
                source_stats.values(),
                key=lambda item: (item["total"], item["linked_trades"], item["source"]),
                reverse=True,
            ),
        },
        "growth_timeline": [
            {
                "id": f"lesson_{row[0]}",
                "date": str(row[1]),
                "title": row[2] or "Untitled Lesson",
                "content": row[3],
                "source": _display_source(row[4], row[5]),
                "origin_ref": f"insights:{row[0]}",
            }
            for row in growth_rows
        ],
        "raw_sections": [],
    }


