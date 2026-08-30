"""Stable ASCII section identity for AI advisor reports (Program BIL / WS-5).

WHY THIS MODULE EXISTS
----------------------
The advisor's JSON contract used to be a set of literal **Chinese strings**:

    BRIEF_SECTION_KEYS = ["宏观形势", "持仓分析与风险预警", ...]

Those strings were the LLM's output keys, the storage keys in
``ai_reports.content_json``, the API's response keys, AND the frontend's
match targets — one value doing four jobs across three languages of
tooling. The model already drifted between Simplified and Traditional
(``宏觀形勢`` / ``宏觀職勢``), which is why ``brief_generator`` grew a repair
map before a second output language was ever on the table.

Section **identity** is now a stable ASCII ID. The LLM emits these IDs
regardless of the narrative language; only prose *values* are localized.
Display labels are resolved from the frontend catalog
(``ux-command-center/src/i18n/locales/{en,zh-CN}/aiAdvisor.json``) against
the ID — **never** taken from the model's output.

Nothing is rewritten in the database. Legacy Chinese-keyed rows hold
irreplaceable historical analysis and are mapped to IDs at **read time**
by :func:`normalize_section_keys`, which every read site calls.

THE SAME BUG, IN VALUES
-----------------------
Section keys were not the only free-text value the frontend string-matched.
``status`` (持有/关注/预警), ``action`` (买入/卖出/持有) and the accuracy
scorecard badges (高准确度/中准确度/低准确度) were all prose that styling
keyed off. All three are enums here now, with the legacy Chinese kept as
read-time aliases so old rows keep their colours.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical section IDs
# ---------------------------------------------------------------------------

BRIEF_SECTION_IDS: list[str] = [
    "macro_outlook",
    "holdings_risk",
    "risk_alerts",
    "action_items",
    "watchlist",
]

REVIEW_SECTION_IDS: list[str] = [
    "trade_summary",
    "advice_accuracy",
    "portfolio_performance",
    "lessons_learned",
    "rule_updates",
]

#: Sections that are attached deterministically (not LLM-authored) and already
#: carry an ASCII key. Listed so the normalizer's "unknown key" logging stays quiet.
NON_LLM_SECTION_IDS: frozenset[str] = frozenset({"north_star"})

ALL_SECTION_IDS: frozenset[str] = frozenset(BRIEF_SECTION_IDS) | frozenset(REVIEW_SECTION_IDS)


# ---------------------------------------------------------------------------
# Enum values that used to be free-text prose
# ---------------------------------------------------------------------------

#: Scorecard accuracy tier. Was three Chinese literals the frontend matched on
#: (``'高准确度'``/``'中准确度'``/``'低准确度'``) — an English brief broke the badge
#: styling outright. The tier is the value; the Chinese is a display label.
ACCURACY_TIERS: tuple[str, ...] = ("high", "medium", "low")

#: Position status inside the ``holdings_risk`` section.
POSITION_STATUSES: tuple[str, ...] = ("hold", "watch", "alert")

#: Recommended action inside the ``action_items`` section. NOTE: the pre-BIL
#: Chinese schema told the model to emit 买入/卖出/持有 while the frontend's
#: ACTION_STYLES map was keyed on buy/sell/hold — so every action badge silently
#: fell through to the "hold" grey. Fixed by making the enum the contract.
ACTION_VALUES: tuple[str, ...] = ("buy", "sell", "hold")

#: Legacy free-text values → enum. Applied at read time only; never written.
LEGACY_VALUE_ALIASES: dict[str, dict[str, str]] = {
    "accuracy_tier": {
        "高准确度": "high",
        "高準確度": "high",
        "中准确度": "medium",
        "中準確度": "medium",
        "低准确度": "low",
        "低準確度": "low",
    },
    "status": {
        "持有": "hold",
        "关注": "watch",
        "關注": "watch",
        "预警": "alert",
        "預警": "alert",
    },
    "action": {
        "买入": "buy",
        "買入": "buy",
        "加仓": "buy",
        "加倉": "buy",
        "卖出": "sell",
        "賣出": "sell",
        "减仓": "sell",
        "減倉": "sell",
        "持有": "hold",
        "观望": "hold",
        "觀望": "hold",
    },
}


# ---------------------------------------------------------------------------
# Legacy key → ID map (read-time adapter input)
# ---------------------------------------------------------------------------

# Simplified Chinese: the keys production actually wrote from V5 through V7.9.
# Traditional Chinese: empirically observed model drift (gemini-3-flash-preview
# among others). 職勢 is not a real word — it is a transcription slip the model
# made repeatedly, kept here because rows carrying it exist in the database.
#
# Both 汇 → 匯 and 汇 → 彙 appear as Traditional forms of 汇总; both are mapped.
LEGACY_SECTION_KEYS: dict[str, str] = {
    # ---- brief · Simplified (canonical pre-BIL) -----------------------------
    "宏观形势": "macro_outlook",
    "持仓分析与风险预警": "holdings_risk",
    "风险预警汇总": "risk_alerts",
    "操作建议": "action_items",
    "明日关注": "watchlist",
    # ---- brief · Traditional / model drift ---------------------------------
    "宏觀形勢": "macro_outlook",
    "宏觀職勢": "macro_outlook",
    "持倉分析與風險預警": "holdings_risk",
    "風險預警彙總": "risk_alerts",
    "風險預警匯總": "risk_alerts",
    "操作建議": "action_items",
    "明日關注": "watchlist",
    # ---- review · Simplified (canonical pre-BIL) ---------------------------
    "交易汇总": "trade_summary",
    "建议准确性": "advice_accuracy",
    "组合表现": "portfolio_performance",
    "经验沉淀": "lessons_learned",
    "准则更新建议": "rule_updates",
    # ---- review · Traditional / model drift --------------------------------
    "交易匯總": "trade_summary",
    "交易彙總": "trade_summary",
    "建議準確性": "advice_accuracy",
    "組合表現": "portfolio_performance",
    "經驗沉澱": "lessons_learned",
    "經驗沉淀": "lessons_learned",
    "準則更新建議": "rule_updates",
}


# ---------------------------------------------------------------------------
# Server-side display labels (markdown rendering only)
# ---------------------------------------------------------------------------

# The FRONTEND resolves its labels from aiAdvisor.json — these are for the
# markdown artifact the backend builds (`content_markdown`, the "copy markdown"
# payload), which has no access to the React catalog.
#
# `tests/services/test_ai_advisor_section_ids.py` asserts these stay in lockstep
# with the frontend catalog, so the two cannot drift apart silently.
SECTION_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "macro_outlook": "Macro outlook",
        "holdings_risk": "Holdings analysis & risk alerts",
        "risk_alerts": "Risk alert summary",
        "action_items": "Recommended actions",
        "watchlist": "Tomorrow's watchlist",
        "trade_summary": "Trade summary",
        "advice_accuracy": "Advice accuracy",
        "portfolio_performance": "Portfolio performance",
        "lessons_learned": "Lessons learned",
        "rule_updates": "Proposed rule updates",
    },
    "zh-CN": {
        "macro_outlook": "宏观形势",
        "holdings_risk": "持仓分析与风险预警",
        "risk_alerts": "风险预警汇总",
        "action_items": "操作建议",
        "watchlist": "明日关注",
        "trade_summary": "交易汇总",
        "advice_accuracy": "建议准确性",
        "portfolio_performance": "组合表现",
        "lessons_learned": "经验沉淀",
        "rule_updates": "准则更新建议",
    },
}

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "zh-CN")
DEFAULT_LANGUAGE = "en"


def section_label(section_id: str, language: str = DEFAULT_LANGUAGE) -> str:
    """Display label for a section ID, for backend-rendered markdown.

    Falls back to the ID itself for an unknown section — an unrecognised key is
    still rendered rather than dropped (AGENTS.md Rule 12: never blank the page).
    """
    labels = SECTION_LABELS.get(language) or SECTION_LABELS[DEFAULT_LANGUAGE]
    return labels.get(section_id, section_id)


# ---------------------------------------------------------------------------
# Read-time adapter
# ---------------------------------------------------------------------------


def normalize_section_keys(content_json: Any) -> dict:
    """Map legacy Chinese section keys → stable ASCII IDs. Read-time only.

    This is the adapter that lets pre-BIL rows keep rendering. It is called at
    generation time AND at every API read site, because the rows already in
    ``ai_reports`` are never rewritten.

    Two-pass, so a canonical ID present in the input always wins over a value
    that arrived via a legacy alias:

    - Pass 1: copy every entry, renaming legacy keys to their ID.
    - Pass 2: re-apply entries whose key is already an ID (or an unknown key),
      overwriting anything a legacy alias put there.

    Returns a NEW dict; the input is not mutated. A non-dict input returns ``{}``
    rather than raising — a malformed stored row must not 500 the endpoint.
    """
    if not isinstance(content_json, dict):
        return {}

    result: dict = {}

    # Pass 1 — legacy keys renamed to IDs (lower priority)
    for key, value in content_json.items():
        canonical = LEGACY_SECTION_KEYS.get(key, key)
        if canonical not in result:
            if canonical != key:
                logger.info("AI report section key normalized: %r -> %r", key, canonical)
            result[canonical] = value

    # Pass 2 — canonical IDs / unknown keys overwrite any legacy-aliased value
    for key, value in content_json.items():
        if key not in LEGACY_SECTION_KEYS:
            if key in result and result[key] is not value:
                logger.warning(
                    "AI report section key collision: %r overwrites a legacy alias mapping", key
                )
            result[key] = value

    return result


def normalize_enum_values(content_json: Any) -> dict:
    """Map legacy Chinese enum VALUES → the stable enum, in place of a rewrite.

    Handles the three fields the frontend styles off:
    ``holdings_risk.positions[].status``, ``action_items.actions[].action`` and
    ``advice_accuracy.scorecard[].accuracy_tier``.

    Unknown values are left untouched — they still render as text, they just get
    the neutral styling. Returns a new dict; the input is not mutated.
    """
    if not isinstance(content_json, dict):
        return {}

    def _coerce(field: str, raw: Any) -> Any:
        if not isinstance(raw, str):
            return raw
        alias = LEGACY_VALUE_ALIASES.get(field, {})
        stripped = raw.strip()
        if stripped in alias:
            return alias[stripped]
        lowered = stripped.lower()
        valid = {
            "accuracy_tier": ACCURACY_TIERS,
            "status": POSITION_STATUSES,
            "action": ACTION_VALUES,
        }.get(field, ())
        return lowered if lowered in valid else raw

    result: dict = {}
    for section_key, section in content_json.items():
        if not isinstance(section, dict):
            result[section_key] = section
            continue
        new_section = dict(section)
        for list_key, field in (
            ("positions", "status"),
            ("actions", "action"),
            ("scorecard", "accuracy_tier"),
        ):
            items = new_section.get(list_key)
            if not isinstance(items, list):
                continue
            new_section[list_key] = [
                {**item, field: _coerce(field, item[field])}
                if isinstance(item, dict) and field in item
                else item
                for item in items
            ]
        result[section_key] = new_section
    return result


def adapt_stored_content_json(content_json: Any) -> dict:
    """Full read-time adaptation: legacy section keys AND legacy enum values.

    This is what the API read sites call. Keep it the single entry point so a
    new read site cannot pick up half the adapter.
    """
    return normalize_enum_values(normalize_section_keys(content_json))
