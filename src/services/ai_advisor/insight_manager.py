"""Insight lifecycle management for AI Advisor.

Manages ai_insights table: list, get, update, merge, promote.
Status progression: raw → recurring → validated → principle → (deprecated).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date as _date
from typing import Optional

import duckdb

logger = logging.getLogger(__name__)

# ai_model written on auto-bridged rows — normalizes to 'review' via normalize_source(),
# which is distinct from GENERIC_SOURCES {"", "unknown", "other", "system", "imported"}.
_BRIDGE_AI_MODEL = "review"

STATUS_ORDER = ["raw", "recurring", "validated", "principle"]

_ALLOWED_UPDATE_FIELDS = {"status", "tags", "confidence", "body", "title"}

# PRD 2026-07-07 F6 — Insight Library governance ("rule budget"). Promote is
# denied unless EITHER threshold is met. Applied at EVERY step of the
# raw -> recurring -> validated -> principle ladder (not just the final
# promotion to 'principle') — the PRD problem statement is "Promote is one
# click at 30% confidence" for 37 pending insights, i.e. the one-click-ness at
# ANY stage is the thing being fixed, not just the last hop.
PROMOTE_MIN_CONFIDENCE = 0.70
PROMOTE_MIN_VALIDATED_CASES = 3

RULE_LAYER_VALUES = ("principle", "checklist_item")


def _upsert_bridge_row(
    conn,
    *,
    observation_source: str,
    category: str,
    insight_type: str,
    insight_date: str,
    title: str,
    content: str,
    ai_model: Optional[str] = None,
    confidence_score: Optional[float] = None,
) -> None:
    """Upsert one Decision Hub row keyed on observation_source (idempotent).

    Shared writer used by both the Promote path and the auto-bridge reconciler.
    When ai_model/confidence_score are None the UPDATE preserves any value already
    stored (COALESCE semantics), so a Promote call never clobbers an auto-bridge value.
    """
    conn.execute(
        """
        UPDATE insights
        SET
            insight_date = ?,
            insight_type = ?,
            category = ?,
            title = ?,
            content = ?,
            ai_model = COALESCE(?, ai_model),
            confidence_score = COALESCE(?, confidence_score)
        WHERE observation_source = ?
        """,
        [
            insight_date,
            insight_type,
            category,
            title,
            content,
            ai_model,
            confidence_score,
            observation_source,
        ],
    )
    conn.execute(
        """
        INSERT INTO insights (
            insight_date, insight_type, category, title, content,
            observation_source, ai_model, confidence_score, created_at
        )
        SELECT ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (
            SELECT 1 FROM insights WHERE observation_source = ?
        )
        """,
        [
            insight_date,
            insight_type,
            category,
            title,
            content,
            observation_source,
            ai_model,
            confidence_score,
            observation_source,
        ],
    )
    # Defensive dedup: keep the earliest row, drop later duplicates for this key.
    conn.execute(
        """
        DELETE FROM insights
        WHERE id IN (
            SELECT id
            FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY observation_source
                        ORDER BY created_at ASC, id ASC
                    ) AS rn
                FROM insights
                WHERE observation_source = ?
            ) ranked
            WHERE rn > 1
        )
        """,
        [observation_source],
    )


@dataclass
class Insight:
    id: int
    category: str
    title: str
    body: str
    tags: str
    confidence: float
    status: str
    recurrence_count: int
    entity_refs: str
    source_report_id: Optional[int]
    created_at: str
    updated_at: str
    # F6 governance fields (migration 014 / V71) — additive, defaulted so the
    # two existing positional-keyword construction sites (bridge helper tests,
    # _row_to_insight below) do not need updating unless they care about these.
    validated_cases: int = 0
    validated_case_links: str = "[]"
    rule_layer: Optional[str] = None


def check_promotion_gate(confidence: Optional[float], validated_cases: Optional[int]) -> None:
    """Raise ValueError with the unmet-criteria text unless the F6 promote gate passes.

    Gate: confidence >= PROMOTE_MIN_CONFIDENCE (70%) OR
          validated_cases >= PROMOTE_MIN_VALIDATED_CASES (3).
    Message format matches the PRD example exactly, e.g.:
        "confidence 30% < 70% and validated_cases 0 < 3"
    so the API route can surface it verbatim as a 422 detail.
    """
    conf = float(confidence) if confidence is not None else 0.0
    cases = int(validated_cases) if validated_cases is not None else 0
    if conf >= PROMOTE_MIN_CONFIDENCE or cases >= PROMOTE_MIN_VALIDATED_CASES:
        return
    raise ValueError(
        f"confidence {conf * 100:.0f}% < 70% and validated_cases {cases} < 3"
    )


class InsightManager:
    def __init__(self, db_path: str = "data/unified.duckdb"):
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_insights(
        self,
        status: str = None,
        category: str = None,
        limit: int = 50,
    ) -> list[Insight]:
        """List insights, optionally filtered by status/category.

        Excludes deprecated insights.
        """
        sql = """
        SELECT id, category, title, body, tags, confidence, status, recurrence_count,
               entity_refs, source_report_id, created_at, updated_at,
                          validated_cases, validated_case_links, rule_layer
        FROM ai_insights
        WHERE (? IS NULL OR status = ?) AND (? IS NULL OR category = ?)
          AND status != 'deprecated'
        ORDER BY updated_at DESC, recurrence_count DESC
        LIMIT ?
        """
        conn = duckdb.connect(self._db_path, read_only=True)
        try:
            rows = conn.execute(sql, [status, status, category, category, limit]).fetchall()
        finally:
            conn.close()

        return [self._row_to_insight(r) for r in rows]

    def get_insight(self, insight_id: int) -> Optional[Insight]:
        """Return a single insight by ID, or None if not found."""
        sql = """
        SELECT id, category, title, body, tags, confidence, status, recurrence_count,
               entity_refs, source_report_id, created_at, updated_at,
                          validated_cases, validated_case_links, rule_layer
        FROM ai_insights
        WHERE id = ?
        """
        conn = duckdb.connect(self._db_path, read_only=True)
        try:
            row = conn.execute(sql, [insight_id]).fetchone()
        finally:
            conn.close()

        if row is None:
            return None
        return self._row_to_insight(row)

    def update_insight(self, insight_id: int, updates: dict) -> Optional[Insight]:
        """Update allowed fields on an insight.

        Allowed fields: status, tags, confidence, body, title.
        Unknown fields are silently rejected (not applied).
        Returns updated Insight or None if not found.
        """
        safe_updates = {k: v for k, v in updates.items() if k in _ALLOWED_UPDATE_FIELDS}
        if not safe_updates:
            # No valid fields — still return the current insight if it exists
            return self.get_insight(insight_id)

        set_clauses = ", ".join(f"{field} = ?" for field in safe_updates)
        values = list(safe_updates.values()) + [insight_id]

        sql = f"""
        UPDATE ai_insights
        SET {set_clauses}, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """
        conn = duckdb.connect(self._db_path)
        try:
            conn.execute(sql, values)
            # Fetch updated row
            row = conn.execute(
                """SELECT id, category, title, body, tags, confidence, status, recurrence_count,
                          entity_refs, source_report_id, created_at, updated_at,
                          validated_cases, validated_case_links, rule_layer
                   FROM ai_insights WHERE id = ?""",
                [insight_id],
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None
        return self._row_to_insight(row)

    def merge_insights(self, primary_id: int, duplicate_id: int) -> Optional[Insight]:
        """Mark duplicate as 'deprecated', increment primary's recurrence_count.

        Returns updated primary Insight, or None if primary not found.
        """
        conn = duckdb.connect(self._db_path)
        try:
            conn.execute(
                "UPDATE ai_insights SET status='deprecated', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                [duplicate_id],
            )
            conn.execute(
                "UPDATE ai_insights SET recurrence_count=recurrence_count+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                [primary_id],
            )
            row = conn.execute(
                """SELECT id, category, title, body, tags, confidence, status, recurrence_count,
                          entity_refs, source_report_id, created_at, updated_at,
                          validated_cases, validated_case_links, rule_layer
                   FROM ai_insights WHERE id = ?""",
                [primary_id],
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None
        return self._row_to_insight(row)

    def promote_insight(self, insight_id: int) -> Optional[Insight]:
        """Advance status along the progression: raw → recurring → validated → principle.

        If already at 'principle', ensures the bridge row exists and returns current insight.
        If deprecated, no-ops and returns current insight.
        Returns None if insight not found.

        PRD 2026-07-07 F6 promote gate: raises ValueError (caller — the API
        route — maps this to HTTP 422) unless confidence >= 70% OR
        validated_cases >= 3. Checked on EVERY real advance (raw->recurring,
        recurring->validated, validated->principle) — not just the final hop
        to 'principle' — per the PRD's stated intent of stopping one-click
        promotion of weak insights anywhere on the ladder. The gate is NOT
        applied to the "already at principle" no-op branch below, since that
        path does not advance anything — it only (re)ensures the Decision Hub
        bridge row exists.
        """
        insight = self.get_insight(insight_id)
        if insight is None:
            return None

        current_status = insight.status
        if current_status not in STATUS_ORDER:
            # Already deprecated or unknown — no-op
            return insight

        current_idx = STATUS_ORDER.index(current_status)
        conn = duckdb.connect(self._db_path)
        try:
            conn.execute("BEGIN TRANSACTION")
            if current_idx >= len(STATUS_ORDER) - 1:
                # Already at 'principle' — make sure the bridge row exists, then no-op.
                self._ensure_decision_hub_insight(conn, insight)
                conn.execute("COMMIT")
                return insight

            check_promotion_gate(insight.confidence, insight.validated_cases)

            next_status = STATUS_ORDER[current_idx + 1]
            conn.execute(
                "UPDATE ai_insights SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                [next_status, insight_id],
            )
            if next_status == "principle":
                self._ensure_decision_hub_insight(conn, insight)
            row = conn.execute(
                """SELECT id, category, title, body, tags, confidence, status, recurrence_count,
                          entity_refs, source_report_id, created_at, updated_at,
                          validated_cases, validated_case_links, rule_layer
                   FROM ai_insights WHERE id = ?""",
                [insight_id],
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

        if row is None:
            return None
        return self._row_to_insight(row)

    def _ensure_decision_hub_insight(self, conn, insight: Insight) -> None:
        """Create or refresh the corresponding Decision Hub insight for a principle insight.

        Delegates to _upsert_bridge_row so the Promote path and the auto-bridge reconciler
        share one writer.  ai_model is passed explicitly: if Promote runs BEFORE the
        reconciler, the bridge row would otherwise be created with ai_model=NULL and the
        reconciler's NOT EXISTS check would skip it forever — bucketing the insight under
        the generic 'system' source instead of 'review'.
        """
        content = insight.body if insight.body else insight.title
        _upsert_bridge_row(
            conn,
            observation_source=f"ai_insights:{insight.id}",
            category=insight.category,
            insight_type="AI_Advisor",
            insight_date=str(_date.today()),
            title=insight.title,
            content=content,
            ai_model=_BRIDGE_AI_MODEL,
            confidence_score=float(insight.confidence) if insight.confidence is not None else None,
        )

    def deduplicate_all(self, conn) -> dict:
        """
        Groups insights by (title, category), keeps oldest non-deprecated,
        merges recurrence counts, deprecates the rest.
        Returns summary dict with counts.
        """
        # Find groups with duplicates
        duplicates = conn.execute("""
            SELECT title, category,
                   COUNT(*) as cnt,
                   MIN(id) as keep_id,
                   SUM(recurrence_count) as total_recurrences
            FROM ai_insights
            WHERE status != 'deprecated'
            GROUP BY title, category
            HAVING COUNT(*) > 1
        """).fetchall()

        deprecated_count = 0
        merged_count = 0

        for row in duplicates:
            title, category, cnt, keep_id, total_recurrences = row
            merged_count += 1

            # Update the keeper with merged recurrence count
            merged_recurrences = max(int(total_recurrences or 0), 1)
            conn.execute(
                """UPDATE ai_insights
                   SET recurrence_count = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                [merged_recurrences, keep_id],
            )

            # Count duplicates to deprecate
            dup_rows = conn.execute(
                """SELECT COUNT(*) FROM ai_insights
                   WHERE title = ? AND category = ? AND id != ? AND status != 'deprecated'""",
                [title, category, keep_id],
            ).fetchone()
            deprecated_count += dup_rows[0] if dup_rows else 0

            # Deprecate all others in this group
            conn.execute(
                """UPDATE ai_insights
                   SET status = 'deprecated', updated_at = CURRENT_TIMESTAMP
                   WHERE title = ? AND category = ? AND id != ? AND status != 'deprecated'""",
                [title, category, keep_id],
            )

        return {
            "groups_merged": merged_count,
            "duplicates_deprecated": deprecated_count,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_insight(self, row) -> Insight:
        """Convert DB row tuple to Insight dataclass."""
        (
            id_,
            category,
            title,
            body,
            tags,
            confidence,
            status,
            recurrence_count,
            entity_refs,
            source_report_id,
            created_at,
            updated_at,
            validated_cases,
            validated_case_links,
            rule_layer,
        ) = row
        return Insight(
            id=id_,
            category=category or "",
            title=title or "",
            body=body or "",
            tags=tags or "",
            confidence=float(confidence) if confidence is not None else 0.3,
            status=status or "raw",
            recurrence_count=int(recurrence_count) if recurrence_count is not None else 1,
            entity_refs=entity_refs or "",
            source_report_id=source_report_id,
            created_at=str(created_at),
            updated_at=str(updated_at),
            validated_cases=int(validated_cases) if validated_cases is not None else 0,
            validated_case_links=(
                validated_case_links if isinstance(validated_case_links, str)
                else (json.dumps(validated_case_links) if validated_case_links is not None else "[]")
            ),
            rule_layer=rule_layer,
        )


# ---------------------------------------------------------------------------
# Module-level reconciler (importable by call sites, e.g. extract_insights)
# ---------------------------------------------------------------------------


def bridge_ai_insights_to_decision_hub(conn) -> int:
    """Idempotent reconciler: bridge qualifying ai_insights rows into the Decision Hub.

    Qualifying rows
    ---------------
    * status != 'deprecated'
    * AND (category = 'recommendation' OR status IN ('recurring', 'principle'))
    * AND no existing insights row with observation_source = 'ai_insights:<id>'

    Category mapping for bridged rows
    ----------------------------------
    * category = 'recommendation'  →  kept as 'recommendation' (enters funnel scope)
    * all others (recurring/principle non-recommendations)  →  'lesson' (growth timeline)

    The ai_model is set to _BRIDGE_AI_MODEL ('review'), which normalizes to 'review'
    via normalize_source() — distinct from GENERIC_SOURCES.

    Returns the number of rows newly inserted.  Raises on DB errors so the caller
    can wrap in try/except and log a warning without breaking its surrounding work.
    """
    qualifying = conn.execute(
        """
        SELECT
            ai.id,
            ai.category,
            ai.title,
            ai.body,
            ai.confidence,
            ai.status,
            CAST(ai.created_at AS VARCHAR)
        FROM ai_insights ai
        WHERE ai.status != 'deprecated'
          AND (ai.category = 'recommendation' OR ai.status IN ('recurring', 'principle'))
          AND NOT EXISTS (
              SELECT 1 FROM insights i
              WHERE i.observation_source = 'ai_insights:' || CAST(ai.id AS VARCHAR)
          )
        """
    ).fetchall()

    bridged = 0
    for row in qualifying:
        insight_id = row[0]
        # Per-row isolation: one malformed row (e.g. unconvertible confidence)
        # must not abort the bridge for every remaining qualifying insight.
        try:
            _insight_id, category, title, body, confidence, _status, created_at_str = row
            observation_source = f"ai_insights:{insight_id}"
            # Recommendations → keep category (funnel scope, not 'lesson').
            # Everything else that qualified (recurring/principle) → 'lesson' (growth timeline).
            bridge_category = category if category == "recommendation" else "lesson"
            content = body if body else title
            insight_date = str(created_at_str)[:10]
            conf = float(confidence) if confidence is not None else None
            _upsert_bridge_row(
                conn,
                observation_source=observation_source,
                category=bridge_category,
                insight_type="AI_Advisor",
                insight_date=insight_date,
                title=title,
                content=content,
                ai_model=_BRIDGE_AI_MODEL,
                confidence_score=conf,
            )
            bridged += 1
        except Exception as exc:
            logger.warning(
                "bridge_ai_insights_to_decision_hub: skipping ai_insights id=%s: %s",
                insight_id, exc,
            )

    if bridged:
        logger.info("bridge_ai_insights_to_decision_hub: bridged %d new row(s)", bridged)
    return bridged
