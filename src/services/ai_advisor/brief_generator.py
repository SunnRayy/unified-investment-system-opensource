"""BriefGenerator: assembles context, calls LLM, validates output, persists to DB."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import duckdb

from src.services.ai_advisor.context_builder import ContextBuilder, render_context
from src.services.ai_advisor.language_resolver import resolve_language_code
from src.services.ai_advisor.prompts import (
    BRIEF_SECTION_IDS,
    get_brief_system_prompt,
    section_placeholder,
)
from src.services.ai_advisor.section_ids import (
    DEFAULT_LANGUAGE,
    LEGACY_SECTION_KEYS,
    normalize_section_keys,
    section_label,
)
from src.services.llm_client import LLMClient

# Backwards-compatible alias. The Traditional-Chinese repair map this module used
# to own is now one slice of the canonical legacy→ID map in section_ids.py, so the
# API read sites and the generator share exactly one adapter.
_BRIEF_KEY_VARIANTS = LEGACY_SECTION_KEYS
BRIEF_SECTION_KEYS = BRIEF_SECTION_IDS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class BriefResult:
    id: Optional[int]
    report_type: str          # always "brief"
    content_json: dict
    content_markdown: str
    model_used: str
    created_at: str
    context_config: dict
    usage: dict
    prompt_text: Optional[str] = None
    raw_response_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class BriefGenerator:
    """Generate a structured daily brief using context tiers + LLM."""

    def generate(
        self,
        context_config: dict,
        db_path: str = "data/unified.duckdb",
        reviewed_context_text: Optional[str] = None,
        language: Optional[str] = None,
    ) -> BriefResult:
        """
        Generate a brief from the given context configuration.

        context_config shape::

            {
              "tiers": {
                "identity":     {"enabled": True,  "detail": "summary"},
                "portfolio":    {"enabled": True,  "detail": "summary"},
                "market":       {"enabled": True,  "detail": "summary"},
                "strategy":     {"enabled": False, "detail": "summary"},
                "transactions": {"enabled": True,  "detail": "summary", "timeframe": "14d"}
              },
              "include_realtime": False,
              "model": None  # None = use settings.yaml primary
            }

        ``language`` is the narrative output language. None means "resolve it" —
        request locale, then ``user_profile.language``, then settings.yaml, then
        'en' (see :mod:`src.services.ai_advisor.language_resolver`). Scheduled
        generation has no request locale, which is why the persisted value exists.

        Returns a :class:`BriefResult` even when LLM or DB steps fail partially.
        """
        lang = _resolve_generation_language(language)

        # ------------------------------------------------------------------
        # 1. Build context blocks from enabled tiers
        # ------------------------------------------------------------------
        if reviewed_context_text is not None:
            user_prompt = reviewed_context_text
        else:
            cb = ContextBuilder()
            user_prompt = render_context(cb, context_config)

        # ------------------------------------------------------------------
        # 2. Call LLM
        # ------------------------------------------------------------------
        client = LLMClient()
        response = client.complete(
            system_prompt=get_brief_system_prompt(lang),
            user_prompt=user_prompt,
            expect_json=True,
            report_type="brief",
        )
        prompt_text_to_save = user_prompt

        if _should_retry_brief_response(
            response,
            client_max_tokens=_coerce_client_max_tokens(getattr(client, "_max_tokens", 4096)),
        ):
            missing_keys = _missing_brief_keys(response.content_json)
            retry_prompt = _build_brief_retry_prompt(
                context_text=user_prompt,
                partial_response=response.content,
                missing_keys=missing_keys,
                language=lang,
            )
            retry_response = client.complete(
                system_prompt=get_brief_system_prompt(lang),
                user_prompt=retry_prompt,
                expect_json=True,
                report_type="brief",
            )
            if _brief_completeness_score(retry_response.content_json) >= _brief_completeness_score(response.content_json):
                response = retry_response
                prompt_text_to_save = f"{user_prompt}\n\n--- RETRY PROMPT ---\n{retry_prompt}"

        # ------------------------------------------------------------------
        # 3. Normalize section keys (legacy Chinese / Traditional → stable IDs),
        #    then validate sections — insert placeholder for any missing key
        # ------------------------------------------------------------------
        content_json: dict = _normalize_brief_keys(response.content_json or {})
        for key in BRIEF_SECTION_IDS:
            if key not in content_json:
                logger.warning("Brief response missing section '%s'; inserting placeholder", key)
                content_json[key] = section_placeholder(lang)

        # ------------------------------------------------------------------
        # 4. Generate markdown
        # ------------------------------------------------------------------
        content_markdown = _build_content_markdown(content_json, lang)

        # ------------------------------------------------------------------
        # 5. Save to DB
        # ------------------------------------------------------------------
        now_str = datetime.now().isoformat()
        stored_context_config = {**context_config, "language": lang}
        record_id = _save_to_db(
            record={
                "report_type": "brief",
                "title": None,
                "context_config_json": json.dumps(stored_context_config, ensure_ascii=False),
                "content_json": json.dumps(content_json, ensure_ascii=False),
                "content_markdown": content_markdown,
                "model_used": response.model_used,
                "period_start": None,
                "period_end": None,
                "prompt_text": prompt_text_to_save,
                "raw_response_text": response.content,
            },
            db_path=db_path,
        )

        return BriefResult(
            id=record_id,
            report_type="brief",
            content_json=content_json,
            content_markdown=content_markdown,
            model_used=response.model_used,
            created_at=now_str,
            context_config=stored_context_config,
            usage=response.usage,
            prompt_text=prompt_text_to_save,
            raw_response_text=response.content,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_generation_language(language: Optional[str]) -> str:
    """Resolve the output language, never raising out of a generation call."""
    if language:
        return language
    try:
        return resolve_language_code()
    except Exception as e:  # pragma: no cover — resolver is already defensive
        logger.warning("Language resolution failed (%s); using %s", e, DEFAULT_LANGUAGE)
        return DEFAULT_LANGUAGE


# Markdown scaffolding words. The backend builds `content_markdown` (the "copy
# markdown" payload) with no access to the React catalog, so the handful of
# labels it needs live here as sibling values in one literal — same rule as the
# prompt scaffold.
_MARKDOWN_LABELS: dict[str, dict[str, str]] = {
    "en": {"trigger": "trigger", "level": "level"},
    "zh-CN": {"trigger": "触发", "level": "价位"},
}


def _md_label(key: str, language: str) -> str:
    return (_MARKDOWN_LABELS.get(language) or _MARKDOWN_LABELS[DEFAULT_LANGUAGE])[key]


def _build_content_markdown(content_json: dict, language: str = DEFAULT_LANGUAGE) -> str:
    """Convert structured 5-section content_json to markdown string.

    Headings are the resolved display LABEL for each stable section ID — the ID
    itself is machine identity and never shown to the reader.
    """
    lines: list[str] = []

    for key in BRIEF_SECTION_IDS:
        section = content_json.get(key, {})
        narrative = section.get("narrative", "") if isinstance(section, dict) else str(section)
        lines.append(f"## {section_label(key, language)}")
        lines.append(narrative)

        if not isinstance(section, dict):
            lines.append("")
            continue

        # holdings_risk — positions list
        positions = section.get("positions", [])
        if positions:
            for pos in positions:
                name = pos.get("name", "")
                status = pos.get("status", "")
                comment = pos.get("comment", "")
                lines.append(f"- **{name}** [{status}]: {comment}")

        # risk_alerts — items list
        items = section.get("items", [])
        if items:
            for item in items:
                title = item.get("title", "")
                severity = item.get("severity", "")
                description = item.get("description", "")
                lines.append(f"- [{severity.upper()}] **{title}**: {description}")

        # action_items — actions list
        actions = section.get("actions", [])
        if actions:
            for action in actions:
                asset = action.get("asset", "")
                act = action.get("action", "")
                reasoning = action.get("reasoning", "")
                lines.append(f"- **{asset}** → {act}: {reasoning}")

        # macro_outlook — key_factors
        key_factors = section.get("key_factors", [])
        if key_factors:
            for factor in key_factors:
                lines.append(f"- {factor}")

        # watchlist — watchlist entries
        watchlist = section.get("watchlist", [])
        if watchlist:
            for w in watchlist:
                item_name = w.get("item", "")
                trigger = w.get("trigger", "")
                level = w.get("level", "")
                parts = [f"**{item_name}**"]
                if trigger:
                    parts.append(f"{_md_label('trigger', language)}: {trigger}")
                if level:
                    parts.append(f"{_md_label('level', language)}: {level}")
                lines.append("- " + " | ".join(parts))

        lines.append("")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Key normalization — legacy Chinese (Simplified AND Traditional) → stable IDs
# ---------------------------------------------------------------------------
# The map itself lives in section_ids.py so the API read sites use the SAME
# adapter. This wrapper keeps the historical private name working.


def _normalize_brief_keys(content_json: dict) -> dict:
    """Rename legacy Chinese section keys to the canonical ASCII section IDs."""
    return normalize_section_keys(content_json)


def _missing_brief_keys(content_json: Optional[dict]) -> list[str]:
    if not isinstance(content_json, dict):
        return list(BRIEF_SECTION_IDS)
    normalized = _normalize_brief_keys(content_json)
    return [key for key in BRIEF_SECTION_IDS if key not in normalized]


def _brief_completeness_score(content_json: Optional[dict]) -> int:
    if not isinstance(content_json, dict):
        return 0
    normalized = _normalize_brief_keys(content_json)
    return sum(1 for key in BRIEF_SECTION_IDS if key in normalized)


def _looks_truncated_json(raw_text: str) -> bool:
    stripped = (raw_text or "").strip()
    if not stripped:
        return False
    if stripped.startswith("```") and not stripped.endswith("```"):
        return True
    open_braces = stripped.count("{")
    close_braces = stripped.count("}")
    if open_braces > close_braces:
        return True
    if stripped.startswith("{") and not stripped.endswith("}"):
        return True
    return False


def _should_retry_brief_response(response, client_max_tokens: int) -> bool:
    missing_keys = _missing_brief_keys(response.content_json)
    completion_tokens = int((response.usage or {}).get("completion_tokens", 0) or 0)
    if not missing_keys:
        return False
    if _looks_truncated_json(response.content):
        return True
    return len(missing_keys) >= 2 and completion_tokens >= max(client_max_tokens - 64, 0)


def _coerce_client_max_tokens(value: object) -> int:
    if isinstance(value, int):
        return value
    try:
        coerced = int(value)  # type: ignore[arg-type]
    except Exception:
        return 4096
    return coerced if coerced > 0 else 4096


# Retry scaffold — EN and zh-CN as sibling values in one literal, same rule as
# prompts.py. `{keys}` is the ASCII section-ID list, identical in both.
_RETRY_PROMPT_BY_LANG: dict[str, str] = {
    "en": (
        "Your previous Brief JSON output was truncated or missing key sections.\n"
        "Regenerate the complete JSON from the same context below.\n"
        "Requirements:\n"
        "1. Return JSON only — no markdown code fences.\n"
        "2. It must contain these 5 keys: {keys}.\n"
        "3. Keep each narrative to 2-3 sentences and each list to at most 3 entries.\n"
        "4. Sections missing last time: {missing}.\n\n"
        "=== CONTEXT ===\n"
        "{context}\n\n"
        "=== PARTIAL OUTPUT TO REPAIR ===\n"
        "{partial}"
    ),
    "zh-CN": (
        "你上一次的Brief JSON输出被截断或缺少关键section。\n"
        "请基于下面相同的context，重新生成完整JSON。\n"
        "要求：\n"
        "1. 只返回JSON，不要使用markdown code fences。\n"
        "2. 必须包含这5个key：{keys}。\n"
        "3. 每个 narrative 控制在2-3句内，列表最多3项，保持简洁，避免超长输出。\n"
        "4. 上一次缺失的section：{missing}。\n\n"
        "=== CONTEXT ===\n"
        "{context}\n\n"
        "=== PARTIAL OUTPUT TO REPAIR ===\n"
        "{partial}"
    ),
}


def _build_brief_retry_prompt(
    context_text: str,
    partial_response: str,
    missing_keys: list[str],
    language: str = DEFAULT_LANGUAGE,
) -> str:
    template = _RETRY_PROMPT_BY_LANG.get(language) or _RETRY_PROMPT_BY_LANG[DEFAULT_LANGUAGE]
    return template.format(
        keys=", ".join(BRIEF_SECTION_IDS),
        missing=", ".join(missing_keys) if missing_keys else ", ".join(BRIEF_SECTION_IDS),
        context=context_text,
        partial=partial_response,
    )


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
        logger.error("Failed to save brief to ai_reports: %s", e)
        return None
