"""ReviewGenerator: two-phase guided review (questions → answers → summary)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.database.connector import DatabaseConnector

import duckdb

from datetime import date as date_type

from src.services.ai_advisor.context_builder import ContextBuilder, render_context, build_cross_check_context
from src.services.ai_advisor.insight_manager import bridge_ai_insights_to_decision_hub
from src.services.ai_advisor.language_resolver import resolve_language_code
from src.services.ai_advisor.prompts import (
    get_review_questions_system_prompt,
    get_review_system_prompt,
    REVIEW_SECTION_IDS,
    section_placeholder,
    CROSS_CHECK_AUDIT_PROMPT,
    MEMO_UPDATE_PROPOSAL_PROMPT,
)
from src.services.ai_advisor.section_ids import (
    DEFAULT_LANGUAGE,
    normalize_section_keys,
    section_label,
)
from src.services.llm_client import LLMClient, LLMAllModelsFailedError

REVIEW_SECTION_KEYS = REVIEW_SECTION_IDS  # backwards-compatible alias

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Question:
    id: int
    question: str
    context: str  # trade background


@dataclass
class ReviewResult:
    id: Optional[int]
    report_type: str  # always "review"
    content_json: dict
    content_markdown: str
    model_used: str
    created_at: str
    period_start: Optional[str]
    period_end: Optional[str]
    usage: dict
    prompt_text: Optional[str] = None
    raw_response_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Fallback questions used when LLM is unavailable or parse fails
# ---------------------------------------------------------------------------

# User-visible review scaffolding. EN and zh-CN are sibling values in one
# literal (Program BIL / WS-5) so a one-sided edit shows up in the diff;
# `tests/services/test_ai_advisor_prompt_parity.py` asserts the key sets match.
_FALLBACK_QUESTIONS_BY_LANG: dict[str, list[Question]] = {
    "en": [
        Question(id=1, question="What mainly drove this period's trades?", context="General review question"),
        Question(id=2, question="Which trade best matched your strategy's expectation, and why?", context="General review question"),
        Question(id=3, question="Looking back at this period, what would you decide differently?", context="General review question"),
    ],
    "zh-CN": [
        Question(id=1, question="本期交易的主要驱动力是什么？", context="通用复盘问题"),
        Question(id=2, question="哪笔交易最符合你的策略预期？为什么？", context="通用复盘问题"),
        Question(id=3, question="回看本期，有什么你会做不同的决定？", context="通用复盘问题"),
    ],
}

#: Backwards-compatible alias (zh-CN), kept for any external importer.
_FALLBACK_QUESTIONS = _FALLBACK_QUESTIONS_BY_LANG["zh-CN"]

_QUESTION_SCAFFOLD: dict[str, dict[str, str]] = {
    "en": {
        "no_trades_question": "There were no trades this period. Review how your holdings moved and what you concluded about the market.",
        "no_trades_context": "No trades",
        "user_prompt": (
            "Trade records for {period_start} to {period_end}:\n\n"
            "{trades_text}\n\n"
            "From these trade records, generate 3-7 pointed review questions."
        ),
    },
    "zh-CN": {
        "no_trades_question": "本期无交易记录，请回顾你的持仓变化和市场判断。",
        "no_trades_context": "无交易",
        "user_prompt": (
            "以下是{period_start}至{period_end}期间的交易记录：\n\n"
            "{trades_text}\n\n"
            "请根据以上交易记录，生成3-7个有针对性的复盘问题。"
        ),
    },
}

_REVIEW_SCAFFOLD: dict[str, dict[str, str]] = {
    "en": {
        "title": "Investment review {period_start} ~ {period_end}",
        "trades_section": "Trade records ({period_start} to {period_end}):\n{trades_text}",
        "no_trades_section": "No trades this period ({period_start} to {period_end}).",
        "qa_heading": "Investor review Q&A:",
        "closing": "From the information above, produce a structured investment review report containing all 5 specified sections.",
    },
    "zh-CN": {
        "title": "投资复盘 {period_start} ~ {period_end}",
        "trades_section": "交易记录（{period_start}至{period_end}）：\n{trades_text}",
        "no_trades_section": "本期（{period_start}至{period_end}）无交易记录。",
        "qa_heading": "投资者复盘问答：",
        "closing": "请根据以上信息生成结构化的投资复盘报告，包含全部5个指定章节。",
    },
}


def _resolve_generation_language(language: Optional[str]) -> str:
    """Resolve the output language, never raising out of a generation call."""
    if language:
        return language
    try:
        return resolve_language_code()
    except Exception as e:  # pragma: no cover — resolver is already defensive
        logger.warning("Language resolution failed (%s); using %s", e, DEFAULT_LANGUAGE)
        return DEFAULT_LANGUAGE


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class ReviewGenerator:
    """Two-phase investment review: generate questions, then produce final review."""

    # ------------------------------------------------------------------
    # Phase 1: Generate questions grounded in actual trades
    # ------------------------------------------------------------------

    def generate_questions(
        self,
        period_start: str,
        period_end: str,
        db_path: str = "data/unified.duckdb",
        language: Optional[str] = None,
    ) -> list[Question]:
        """
        Query trade_logs for the period and ask the LLM to generate
        3-7 targeted review questions.

        Returns a list of Question objects. Falls back to 3 generic questions
        if the DB has no trades or the LLM call fails.
        """
        lang = _resolve_generation_language(language)

        # ------------------------------------------------------------------
        # 1. Load trades from DB
        # ------------------------------------------------------------------
        trades_text = _load_trades_text(period_start, period_end, db_path)

        if trades_text is None:
            # No trades found
            no_trades = _QUESTION_SCAFFOLD.get(lang, _QUESTION_SCAFFOLD[DEFAULT_LANGUAGE])
            return [
                Question(
                    id=1,
                    question=no_trades["no_trades_question"],
                    context=no_trades["no_trades_context"],
                )
            ]

        # ------------------------------------------------------------------
        # 2. Call LLM
        # ------------------------------------------------------------------
        scaffold = _QUESTION_SCAFFOLD.get(lang, _QUESTION_SCAFFOLD[DEFAULT_LANGUAGE])
        user_prompt = scaffold["user_prompt"].format(
            period_start=period_start, period_end=period_end, trades_text=trades_text
        )

        try:
            client = LLMClient()
            response = client.complete(
                system_prompt=get_review_questions_system_prompt(lang),
                user_prompt=user_prompt,
                expect_json=True,
                report_type="review",
            )

            parsed = response.content_json or {}
            raw_questions = parsed.get("questions", [])
            if not raw_questions or not isinstance(raw_questions, list):
                raise ValueError("No 'questions' list in LLM response")

            questions = [
                Question(
                    id=int(q.get("id", i + 1)),
                    question=str(q.get("question", "")),
                    context=str(q.get("context", "")),
                )
                for i, q in enumerate(raw_questions)
                if isinstance(q, dict)
            ]

            if not questions:
                raise ValueError("Empty questions list after parsing")

            return questions

        except Exception as e:
            logger.warning(
                "generate_questions LLM call failed, using fallback: %s", e
            )
            return list(
                _FALLBACK_QUESTIONS_BY_LANG.get(lang, _FALLBACK_QUESTIONS_BY_LANG[DEFAULT_LANGUAGE])
            )

    # ------------------------------------------------------------------
    # Phase 2: Generate full review given Q&A
    # ------------------------------------------------------------------

    def generate_review(
        self,
        questions_answers: list[dict],
        period_start: str,
        period_end: str,
        context_config: dict,
        db_path: str = "data/unified.duckdb",
        reviewed_context_text: Optional[str] = None,
        language: Optional[str] = None,
    ) -> ReviewResult:
        """
        Generate a structured 5-section review report from Q&A + portfolio context.

        questions_answers: list of {"question": str, "answer": str}
        language: narrative output language; None resolves it (see
            :mod:`src.services.ai_advisor.language_resolver`).
        """
        lang = _resolve_generation_language(language)

        # ------------------------------------------------------------------
        # 1. Build or accept the final user prompt
        # ------------------------------------------------------------------
        if reviewed_context_text is not None:
            user_prompt = reviewed_context_text
        else:
            cb = ContextBuilder()
            context_section = render_context(cb, context_config)
            trades_text = cb.build_review_trade_summary(period_start, period_end)
            user_prompt = build_review_prompt_text(
                context_section=context_section,
                period_start=period_start,
                period_end=period_end,
                questions_answers=questions_answers,
                trades_text=trades_text,
                language=lang,
            )

        # ------------------------------------------------------------------
        # 2. Call LLM
        # ------------------------------------------------------------------
        client = LLMClient()
        response = client.complete(
            system_prompt=get_review_system_prompt(lang),
            user_prompt=user_prompt,
            expect_json=True,
            report_type="review",
        )

        # ------------------------------------------------------------------
        # 3. Normalize section keys (legacy Chinese / Traditional → stable IDs),
        #    then validate sections
        # ------------------------------------------------------------------
        content_json: dict = normalize_section_keys(response.content_json or {})
        content_json = _normalize_review_payload(content_json)
        for key in REVIEW_SECTION_IDS:
            if key not in content_json:
                logger.warning(
                    "Review response missing section '%s'; inserting placeholder", key
                )
                content_json[key] = section_placeholder(lang)

        # ------------------------------------------------------------------
        # 3b. F3.5 — prepend the North Star panel (PRD 2026-07-07, Batch B6).
        # Deterministic, non-LLM data attached ahead of the LLM-authored
        # sections so it renders first in both the JSON payload and the
        # markdown. Additive only: content_json gains one extra 'north_star'
        # key (REVIEW_SECTION_KEYS / _build_content_markdown's fixed-section
        # loop is untouched); failure here is non-fatal to review generation.
        # ------------------------------------------------------------------
        north_star_markdown = ""
        try:
            from src.services.north_star import north_star_panel
            from src.database.connector import DatabaseConnector as _DBConnector

            ns_db = _DBConnector(db_path, read_only=True)
            try:
                north_star_section = north_star_panel(ns_db)
            finally:
                ns_db.close()
            content_json["north_star"] = north_star_section
            north_star_markdown = _render_north_star_markdown(north_star_section)
        except Exception as e:
            logger.warning("North Star panel attachment failed (non-fatal): %s", e)

        # ------------------------------------------------------------------
        # 4. Generate markdown
        # ------------------------------------------------------------------
        content_markdown = _build_content_markdown(content_json, lang)
        if north_star_markdown:
            content_markdown = f"{north_star_markdown}\n\n{content_markdown}"

        # ------------------------------------------------------------------
        # 5. Save to DB
        # ------------------------------------------------------------------
        now_str = datetime.now().isoformat()
        record_id = _save_to_db(
            record={
                "report_type": "review",
                "title": _REVIEW_SCAFFOLD.get(lang, _REVIEW_SCAFFOLD[DEFAULT_LANGUAGE])[
                    "title"
                ].format(period_start=period_start, period_end=period_end),
                "context_config_json": json.dumps(
                    {**context_config, "language": lang}, ensure_ascii=False
                ),
                "content_json": json.dumps(content_json, ensure_ascii=False),
                "content_markdown": content_markdown,
                "model_used": response.model_used,
                "period_start": period_start,
                "period_end": period_end,
                "prompt_text": user_prompt,
                "raw_response_text": response.content,
            },
            db_path=db_path,
        )

        result = ReviewResult(
            id=record_id,
            report_type="review",
            content_json=content_json,
            content_markdown=content_markdown,
            model_used=response.model_used,
            created_at=now_str,
            period_start=period_start,
            period_end=period_end,
            usage=response.usage,
            prompt_text=user_prompt,
            raw_response_text=response.content,
        )

        # ------------------------------------------------------------------
        # 6. Extract insights (fire-and-forget)
        # ------------------------------------------------------------------
        try:
            self.extract_insights(content_json, record_id, db_path)
        except Exception as e:
            logger.warning("extract_insights failed (non-fatal): %s", e)

        return result

    # ------------------------------------------------------------------
    # Insight extraction
    # ------------------------------------------------------------------

    def extract_insights(
        self,
        review_content: dict,
        report_id: Optional[int],
        db_path: str = "data/unified.duckdb",
    ) -> None:
        """
        Extract lessons, improvements, and strategy suggestions from the review
        and persist them to ai_insights.
        """
        insights: list[tuple[str, str]] = []  # (category, text)

        # Normalize first: this method is called with content_json that may come
        # straight off a stored row, which for anything written before Program
        # BIL is keyed in Chinese. Without this the extraction silently finds
        # nothing and no insight is ever created — a quiet, total no-op.
        review_content = normalize_section_keys(review_content)

        experience_section = review_content.get("lessons_learned", {})
        if isinstance(experience_section, dict):
            for text in experience_section.get("lessons", []):
                if isinstance(text, str) and text.strip():
                    insights.append(("process", text.strip()))
            for text in experience_section.get("improvements", []):
                if isinstance(text, str) and text.strip():
                    insights.append(("process", text.strip()))

        principle_section = review_content.get("rule_updates", {})
        if isinstance(principle_section, dict):
            for text in principle_section.get("suggestions", []):
                if isinstance(text, str) and text.strip():
                    insights.append(("strategy", text.strip()))

        if not insights:
            return

        try:
            conn = duckdb.connect(db_path)
            try:
                for category, text in insights:
                    # A5: title/body separation — body only populated when text exceeds 80 chars
                    if len(text) > 80:
                        title = text[:80]
                        body = text
                    else:
                        title = text
                        body = ""

                    # A4a: Idempotent guard — skip if this (title, category,
                    # source_report_id) triple has already contributed (catches the same
                    # report re-processed). Category is part of the key: the same text in
                    # two categories from one report is two distinct insights.
                    # Checks ALL rows including deprecated so the guard survives deduplicate_all().
                    if report_id is not None:
                        already_seen = conn.execute(
                            """SELECT 1 FROM ai_insights
                               WHERE title = ? AND category = ? AND source_report_id = ?""",
                            [title, category, report_id],
                        ).fetchone()
                        if already_seen:
                            continue

                    # Check for existing active insight with same title+category
                    existing = conn.execute(
                        """SELECT id, recurrence_count, status FROM ai_insights
                           WHERE title = ? AND category = ? AND status != 'deprecated'""",
                        [title, category],
                    ).fetchone()

                    if existing:
                        if report_id is None:
                            # No provenance → the (title, report) guard above could not
                            # run, so an increment here would re-open the unbounded
                            # recurrence-inflation path (the 173x bug). Create-only mode:
                            # existing title without provenance never increments.
                            continue
                        # Different report, same title → increment recurrence once
                        new_count = int(existing[1] or 0) + 1
                        conn.execute(
                            """UPDATE ai_insights
                               SET recurrence_count = recurrence_count + 1,
                                   updated_at = CURRENT_TIMESTAMP
                               WHERE id = ?""",
                            [existing[0]],
                        )
                        # Auto-upgrade: when recurrence_count first reaches 2, promote
                        # status from 'raw' → 'recurring' (never touch 'principle'/'deprecated').
                        if new_count >= 2 and existing[2] == "raw":
                            conn.execute(
                                """UPDATE ai_insights
                                   SET status = 'recurring', updated_at = CURRENT_TIMESTAMP
                                   WHERE id = ?""",
                                [existing[0]],
                            )
                        # Insert a deprecated contribution marker so future re-processing of
                        # this report_id is caught by the guard above.
                        if report_id is not None:
                            conn.execute(
                                """INSERT INTO ai_insights
                                       (source_report_id, category, title, body, status,
                                        confidence, created_at, updated_at)
                                   VALUES (?, ?, ?, ?, 'deprecated', 0.3,
                                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                                [report_id, category, title, body],
                            )
                    else:
                        conn.execute(
                            """INSERT INTO ai_insights
                                   (source_report_id, category, title, body, status,
                                    confidence, created_at, updated_at)
                               VALUES (?, ?, ?, ?, 'raw', 0.3, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                            [report_id, category, title, body],
                        )

                # Auto-bridge qualifying ai_insights rows into the Decision Hub.
                # Failures are non-fatal and must never break review generation.
                try:
                    _n = bridge_ai_insights_to_decision_hub(conn)
                    if _n:
                        logger.info("extract_insights: bridged %d insight(s) to Decision Hub", _n)
                except Exception as _bridge_err:
                    logger.warning(
                        "bridge_ai_insights_to_decision_hub failed (non-fatal): %s", _bridge_err
                    )
            finally:
                conn.close()
        except Exception as e:
            # Table may not exist yet (migration not run)
            logger.warning("Could not save insights to ai_insights: %s", e)


    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Module-level helpers (mirrors brief_generator pattern)
# ---------------------------------------------------------------------------

def _normalize_review_payload(d: dict) -> dict:
    """Normalize LLM schema drift in the review payload (idempotent, pass-through for unknowns).

    Handles deepseek-style nested grade_breakdown::

        "grade_breakdown": {
            "total_trades": 12,
            "grades": {"N/A": 12},
            "notes": "..."
        }

    Runs AFTER `normalize_section_keys`, so it keys off the stable section ID.

    After normalization the ``trade_summary`` section has:
        - grade_breakdown → flat count map lifted from inner ``grades``
        - total_trades   → int sibling key
        - notes          → str sibling key

    Flat count maps (gemini shape) pass through unchanged.
    Any shape that does not match either pattern passes through unchanged.
    """
    import copy as _copy

    section = d.get("trade_summary")
    if not isinstance(section, dict):
        return d

    gb = section.get("grade_breakdown")
    if not isinstance(gb, dict) or len(gb) == 0:
        return d

    # If all values are numbers or digit strings → already a flat count map → pass through
    gb_vals = list(gb.values())
    if all(
        isinstance(v, (int, float))
        or (isinstance(v, str) and str(v).strip().isdigit())
        for v in gb_vals
    ):
        return d

    # Nested shape check: expects a 'grades' key whose values are a count map
    inner_grades = gb.get("grades")
    if not isinstance(inner_grades, dict) or len(inner_grades) == 0:
        return d  # unknown shape — pass through

    inner_vals = list(inner_grades.values())
    if not all(
        isinstance(v, (int, float))
        or (isinstance(v, str) and str(v).strip().isdigit())
        for v in inner_vals
    ):
        return d  # inner grades not a count map — pass through

    # Perform the normalization (deep copy to avoid mutating callers)
    d = _copy.deepcopy(d)
    section = d["trade_summary"]

    total_trades = gb.get("total_trades")
    notes = gb.get("notes")

    # Lift inner grades → flat grade_breakdown
    section["grade_breakdown"] = dict(inner_grades)

    # Promote total_trades and notes to siblings on trade_summary
    if total_trades is not None:
        try:
            section["total_trades"] = int(total_trades)
        except (ValueError, TypeError):
            section["total_trades"] = total_trades

    if notes is not None:
        section["notes"] = str(notes)

    return d


def _render_north_star_markdown(panel: dict) -> str:
    """Deterministic markdown for the F3.5 North Star block (PRD 2026-07-07,
    Batch B6) — contributions, time-in-market, unforced errors, glide path.
    Rendered ahead of the LLM-authored review sections (F3.5: "before the
    valuation dashboard content")."""
    lines: list[str] = ["## North Star (contributions, time-in-market, glide path)"]

    contrib = panel.get("contributions", {})
    lines.append(
        f"- Contributions — YTD: ¥{contrib.get('ytd_sum', 0):,.0f}, "
        f"trailing 12m: ¥{contrib.get('trailing_12m_sum', 0):,.0f} "
        f"({contrib.get('unclassified_count', 0)} unclassified flow(s))"
    )

    tim = panel.get("time_in_market", {})
    if tim.get("insufficient_data"):
        lines.append("- Time in market: insufficient data")
    else:
        band_floor = tim.get("band_floor_pct")
        floor_text = (
            f"{band_floor:.1f}% weight floor" if band_floor is not None else "band floor"
        )
        lines.append(
            f"- Time in market: {tim.get('in_market_months', 0)}/{tim.get('total_months', 0)} months "
            f"(ratio {tim.get('ratio', 0):.2f}) vs {floor_text}"
        )

    errors = panel.get("unforced_errors", [])
    lines.append(f"- Unforced errors logged: {len(errors)}")

    gp = panel.get("glide_path", {})
    if gp.get("insufficient_data"):
        lines.append("- Glide path: insufficient data (assumptions are labeled, never forecasts)")
    else:
        lines.append(
            f"- Glide path: {'reachable' if gp.get('reachable') else 'not reachable within 60y'} — "
            f"years to target: {gp.get('years_to_target')} "
            f"(assumptions: current_nw=¥{gp.get('assumptions', {}).get('current_nw', 0):,.0f}, "
            f"trailing_twr={gp.get('assumptions', {}).get('trailing_twr_pct')}%)"
        )

    return "\n".join(lines)


# Markdown scaffolding words for the review artifact — sibling values, one literal.
_REVIEW_MARKDOWN_LABELS: dict[str, dict[str, str]] = {
    "en": {"lessons": "lesson", "improvements": "improvement"},
    "zh-CN": {"lessons": "经验", "improvements": "改进"},
}


def _build_content_markdown(content_json: dict, language: str = DEFAULT_LANGUAGE) -> str:
    """Convert structured 5-section review content_json to a markdown string.

    Headings are the resolved display LABEL for each stable section ID.
    """
    md_labels = _REVIEW_MARKDOWN_LABELS.get(language) or _REVIEW_MARKDOWN_LABELS[DEFAULT_LANGUAGE]
    lines: list[str] = []

    for key in REVIEW_SECTION_IDS:
        section = content_json.get(key, {})
        narrative = (
            section.get("narrative", "") if isinstance(section, dict) else str(section)
        )
        lines.append(f"## {section_label(key, language)}")
        lines.append(narrative)

        if not isinstance(section, dict):
            lines.append("")
            continue

        # trade_summary — trades list
        trades = section.get("trades", [])
        if trades:
            for trade in trades:
                if isinstance(trade, dict):
                    asset_name = trade.get("asset", trade.get("asset_id", ""))
                    action = trade.get("action", "")
                    logic = trade.get("logic", trade.get("reasoning", ""))
                    lines.append(
                        f"- {trade.get('date', '')} {asset_name} {action}".rstrip()
                    )
                    if logic:
                        lines.append(f"  - {logic}")
                else:
                    lines.append(f"- {trade}")

        # advice_accuracy — scorecard
        scorecard = section.get("scorecard", [])
        if scorecard:
            for item in scorecard:
                if isinstance(item, dict):
                    target = item.get("target", item.get("asset", item.get("name", "")))
                    status = item.get("status", item.get("result", item.get("score", "")))
                    comment = item.get("comment", item.get("note", ""))
                    lines.append(
                        f"- {target}: {status}".rstrip(": ")
                    )
                    if comment:
                        lines.append(f"  - {comment}")
                else:
                    lines.append(f"- {item}")

        # lessons_learned — lessons & improvements
        for sub_key in ("lessons", "improvements"):
            sub_list = section.get(sub_key, [])
            if sub_list:
                label = md_labels[sub_key]
                for item in sub_list:
                    lines.append(f"- [{label}] {item}")

        # rule_updates — suggestions
        suggestions = section.get("suggestions", [])
        if suggestions:
            for item in suggestions:
                lines.append(f"- {item}")

        lines.append("")

    return "\n".join(lines).strip()


def build_review_prompt_text(
    context_section: str,
    period_start: str,
    period_end: str,
    questions_answers: list[dict],
    trades_text: Optional[str],
    language: Optional[str] = None,
) -> str:
    """Build the final review prompt draft from the reviewed context inputs.

    ``language`` is optional so the context-render endpoint keeps working
    unchanged; None means the default language.
    """
    scaffold = _REVIEW_SCAFFOLD.get(language or DEFAULT_LANGUAGE, _REVIEW_SCAFFOLD[DEFAULT_LANGUAGE])

    qa_lines: list[str] = []
    for qa in questions_answers:
        q = qa.get("question", "")
        a = qa.get("answer", "")
        qa_lines.append(f"Q: {q}\nA: {a}")
    qa_block = "\n\n".join(qa_lines)

    trades_section = (
        scaffold["trades_section"].format(
            period_start=period_start, period_end=period_end, trades_text=trades_text
        )
        if trades_text
        else scaffold["no_trades_section"].format(
            period_start=period_start, period_end=period_end
        )
    )

    return (
        f"{context_section}\n\n"
        f"---\n\n{trades_section}\n\n"
        f"---\n\n{scaffold['qa_heading']}\n\n{qa_block}\n\n"
        f"{scaffold['closing']}"
    )


def _load_trades_text(
    period_start: str,
    period_end: str,
    db_path: str,
) -> Optional[str]:
    """Load raw trade rows for question generation."""
    try:
        conn = duckdb.connect(db_path, read_only=True)
        try:
            rows = conn.execute(
                """
                SELECT log_date, asset_id, action, quantity, price, decision_grade
                FROM trade_logs
                WHERE log_date >= ? AND log_date <= ?
                ORDER BY log_date
                """,
                [period_start, period_end],
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return None

        lines = [
            f"{r[0]} | {r[1]} | {r[2]} | qty={r[3]} | price={r[4]} | grade={r[5]}"
            for r in rows
        ]
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Could not load trade_logs (%s): %s", db_path, e)
        return None


def _save_to_db(record: dict, db_path: str) -> Optional[int]:
    """Insert a record into ai_reports. Returns inserted id or None on failure."""
    try:
        conn = duckdb.connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO ai_reports (
                    report_type, title, context_config_json,
                    content_json, content_markdown, model_used,
                    period_start, period_end, prompt_text, raw_response_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    record["report_type"],
                    record["title"],
                    record["context_config_json"],
                    record["content_json"],
                    record["content_markdown"],
                    record["model_used"],
                    record["period_start"],
                    record["period_end"],
                    record.get("prompt_text"),
                    record.get("raw_response_text"),
                ],
            )
            row = conn.execute(
                "SELECT id FROM ai_reports ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception as e:
        logger.error("Failed to save review to ai_reports: %s", e)
        return None


def _save_cross_check_to_db(
    db: "DatabaseConnector",
    period_start: str,
    period_end: str,
    context_summary: dict,
    audit_markdown: str,
    model_used: str,
    prompt_text: str,
) -> Optional[int]:
    """Insert a cross_check_audit record into ai_reports via DatabaseConnector.

    Mirrors the brief_generator._save_to_db pattern but accepts a connector directly.
    """
    import json as _json
    try:
        db.execute(
            """
            INSERT INTO ai_reports (
                report_type, title, context_config_json,
                content_json, content_markdown, model_used,
                period_start, period_end, prompt_text, raw_response_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "cross_check_audit",
                f"Cross-Check Audit {period_start} to {period_end}",
                _json.dumps(context_summary, ensure_ascii=False, default=str),
                _json.dumps({"audit_markdown": audit_markdown}, ensure_ascii=False),
                audit_markdown,
                model_used,
                period_start,
                period_end,
                prompt_text,
                audit_markdown,
            ],
        )
        row = db.execute(
            "SELECT id FROM ai_reports ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error("Failed to save cross_check_audit to ai_reports: %s", e)
        return None


def _extract_lessons_to_insights(
    db: "DatabaseConnector",
    audit_markdown: str,
    period_end: str,
    model_used: str,
    report_id: Optional[int],
) -> int:
    """Parse 'Top 3 lessons' section from audit markdown and upsert into insights(category='lesson').

    Returns the number of rows inserted.
    """
    import re as _re

    # Match "## 5. Top 3 lessons" with or without bold, then grab until next ## section
    section_match = _re.search(
        r"##\s*\d*\.?\s*\*{0,2}Top 3 lessons\*{0,2}[^\n]*\n(.*?)(?=\n##\s|\Z)",
        audit_markdown,
        _re.DOTALL | _re.IGNORECASE,
    )
    if not section_match:
        return 0

    section_text = section_match.group(1)
    # Accept bullet lines ("- text", "* text") and numbered lines ("1. text", "1.  **Title:** rest")
    bullets = _re.findall(r"^[\-\*]\s+(.+)$", section_text, _re.MULTILINE)
    if not bullets:
        bullets = _re.findall(r"^\d+\.\s+(.+)$", section_text, _re.MULTILINE)
    if not bullets:
        return 0

    inserted = 0
    for lesson_text in bullets:
        lesson_text = lesson_text.strip()
        if not lesson_text:
            continue
        # Extract short title from "短标题 — 详细叙述" format (matches historical entry style)
        if " — " in lesson_text:
            title = lesson_text.split(" — ")[0].strip()[:60]
        else:
            title = lesson_text[:60]
        try:
            existing = db.execute(
                "SELECT id FROM insights WHERE title = ? AND category = 'lesson' AND insight_date = ?",
                [title, period_end],
            ).fetchone()
            if existing:
                continue
            db.execute(
                """INSERT INTO insights
                       (insight_date, insight_type, category, title, content,
                        observation_source, ai_model, verified, confidence_score)
                   VALUES (?, 'lesson', 'lesson', ?, ?, 'cross_check_audit', ?, TRUE, 0.9)""",
                [period_end, title, lesson_text, model_used],
            )
            inserted += 1
        except Exception as e:
            logger.warning("Failed to insert lesson into insights: %s", e)

    if inserted:
        logger.info("Inserted %d lesson(s) into insights from cross-check audit (period_end=%s)", inserted, period_end)
    return inserted


def generate_cross_check_audit(
    db: "DatabaseConnector",
    period_start: "date_type",
    period_end: "date_type",
    model: Optional[str] = None,
) -> dict:
    """Generate a cross-check LLM audit for the given period.

    Calls build_cross_check_context(), renders CROSS_CHECK_AUDIT_PROMPT,
    calls LLMClient (primary → fallback), persists to ai_reports, and returns
    {audit_markdown, summary, model_used, generated_at, report_id}.

    Raises HTTPException(422) if context caps are exceeded.
    Raises HTTPException(502) if all LLM models fail.
    """
    from fastapi import HTTPException
    import json as _json

    # 1. Build context
    context = build_cross_check_context(db, period_start, period_end)
    if "error" in context:
        err = context.get("error", "unknown")
        if err == "period_too_large":
            detail = f"Period exceeds caps: {context.get('current_days')} days > {context.get('max_days')} max — narrow the date range"
        elif err == "too_many_insights":
            detail = f"Too many insights in period: {context.get('current')} > {context.get('max_insights')} max — narrow the date range"
        elif err == "too_many_trades":
            detail = f"Too many linked trades in period: {context.get('current')} > {context.get('max_trades')} max — narrow the date range"
        else:
            detail = f"Context cap exceeded ({err}) — narrow the date range"
        raise HTTPException(status_code=422, detail=detail)

    period_start_str = str(period_start)
    period_end_str = str(period_end)

    # 2. Serialize context and render prompt, injecting past lesson style examples
    context_json_str = _json.dumps(context, ensure_ascii=False, default=str)
    try:
        style_rows = db.execute(
            """SELECT title FROM insights
               WHERE category = 'lesson'
               AND (observation_source IS NULL OR observation_source <> 'cross_check_audit')
               ORDER BY insight_date DESC, id DESC
               LIMIT 3"""
        ).fetchall()
    except Exception:
        style_rows = []
    if style_rows:
        examples = "、".join(f"「{r[0][:25]}」" for r in style_rows)
        style_hint = f"\n   参照风格示例: {examples}"
    else:
        style_hint = ""
    prompt = CROSS_CHECK_AUDIT_PROMPT.format(
        period_start=period_start_str,
        period_end=period_end_str,
        context_json=context_json_str,
        style_hint=style_hint,
    )

    # 3. Call LLM
    try:
        client = LLMClient()
        response = client.complete(
            system_prompt="You are a precise investment decision auditor. Follow the strict rules in the prompt.",
            user_prompt=prompt,
            expect_json=False,
            report_type="cross_check_audit",
        )
    except LLMAllModelsFailedError as exc:
        raise HTTPException(
            status_code=502,
            detail={"detail": "llm_unavailable", "error": str(exc)},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"detail": "llm_unavailable", "error": str(exc)},
        )

    audit_markdown = response.content
    model_used = response.model_used
    now_str = datetime.now().isoformat()

    # 4. Persist to ai_reports
    context_summary = context.get("summary", {})
    report_id = _save_cross_check_to_db(
        db=db,
        period_start=period_start_str,
        period_end=period_end_str,
        context_summary=context_summary,
        audit_markdown=audit_markdown,
        model_used=model_used,
        prompt_text=prompt,
    )

    # Auto-populate Growth Timeline from this audit's lessons
    lessons_added = _extract_lessons_to_insights(
        db=db,
        audit_markdown=audit_markdown,
        period_end=period_end_str,
        model_used=model_used,
        report_id=report_id,
    )

    return {
        "audit_markdown": audit_markdown,
        "summary": context_summary,
        "model_used": model_used,
        "generated_at": now_str,
        "report_id": report_id,
        "lessons_added": lessons_added,
    }


def propose_memo_updates(
    db: "DatabaseConnector",
    memo_id: int,
    audit_report_id: Optional[int] = None,
) -> dict:
    """Generate LLM-proposed edits to a strategy memo grounded in the latest cross-check audit.

    Returns a dict with:
      proposals: list of {section, current_text, proposed_text, rationale}
      report_id: id of the persisted ai_reports row
      model_used: LLM model that generated the proposals
      memo_id: the memo that was targeted
    Returns {"error": "..."} on recoverable failures (memo not found, no audit available).
    Raises HTTPException(502) if all LLM models fail.
    """
    import json as _json

    # 1. Load the target memo
    memo_row = db.execute(
        "SELECT id, title, content FROM strategy_memos WHERE id = ? LIMIT 1",
        [memo_id],
    ).fetchone()
    if not memo_row:
        return {"error": f"memo {memo_id} not found"}

    memo_content = memo_row[2] or ""
    if not memo_content.strip():
        return {"error": f"memo {memo_id} has no content to propose updates for"}

    # 2. Load the audit (specified id, or most recent cross_check_audit)
    if audit_report_id is not None:
        audit_row = db.execute(
            "SELECT id, content_markdown FROM ai_reports WHERE id = ? AND report_type = 'cross_check_audit' LIMIT 1",
            [audit_report_id],
        ).fetchone()
    else:
        audit_row = db.execute(
            """SELECT id, content_markdown FROM ai_reports
               WHERE report_type = 'cross_check_audit'
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()

    if not audit_row:
        return {"error": "no cross_check_audit found — run a cross-check audit first"}

    audit_lessons = audit_row[1] or ""

    # 3. Render prompt and call LLM
    prompt = MEMO_UPDATE_PROPOSAL_PROMPT.format(
        memo_content=memo_content,
        audit_lessons=audit_lessons,
    )

    try:
        client = LLMClient()
        response = client.complete(
            system_prompt="You are a precise strategy memo editor. Follow the strict rules in the prompt.",
            user_prompt=prompt,
            expect_json=True,
            report_type="memo_update_proposal",
        )
    except LLMAllModelsFailedError as exc:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=502,
            detail={"detail": "llm_unavailable", "error": str(exc)},
        )

    model_used = response.model_used
    now_str = datetime.now().isoformat()

    # 4. Parse proposals (LLM returns a JSON array)
    proposals: list[dict] = []
    try:
        raw = response.content
        if isinstance(raw, list):
            proposals = raw
        elif isinstance(raw, str):
            proposals = _json.loads(raw)
        elif isinstance(raw, dict) and "proposals" in raw:
            proposals = raw["proposals"]
    except Exception as parse_err:
        logger.warning("propose_memo_updates: failed to parse LLM response: %s", parse_err)
        proposals = []

    # 5. Persist to ai_reports (does NOT write strategy_memos)
    report_id: Optional[int] = None
    try:
        db.execute(
            """INSERT INTO ai_reports (
                report_type, title, context_config_json, content_json,
                content_markdown, model_used
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            [
                "memo_update_proposal",
                f"Memo Update Proposal — {memo_row[1] or f'Memo {memo_id}'}",
                _json.dumps({"memo_id": memo_id, "audit_report_id": audit_row[0]}, ensure_ascii=False),
                _json.dumps({"proposals": proposals}, ensure_ascii=False),
                _json.dumps(proposals, ensure_ascii=False, indent=2),
                model_used,
            ],
        )
        row = db.execute("SELECT id FROM ai_reports ORDER BY created_at DESC LIMIT 1").fetchone()
        report_id = row[0] if row else None
    except Exception as e:
        logger.error("propose_memo_updates: failed to persist to ai_reports: %s", e)

    return {
        "proposals": proposals,
        "report_id": report_id,
        "model_used": model_used,
        "memo_id": memo_id,
        "generated_at": now_str,
    }
