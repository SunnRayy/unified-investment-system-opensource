"""Structural guards on section identity (Program BIL / WS-5).

Three things are asserted here that no unit test of the adapter would catch:

1. **The backend's markdown labels and the frontend's catalog stay in lockstep.**
   Two independent label maps for the same ten sections is the two-sources
   signature this repo has been bitten by seven times. They cannot be merged
   (one is Python building a markdown artifact, the other is a React catalog),
   so the coupling is asserted instead.

2. **Every API read site runs the adapter.** The normalizer existed for a year
   and was wired at exactly zero read sites — the endpoints returned
   `json.loads(content_json)` raw. An AST walk over the router makes that
   impossible to reintroduce quietly.

3. **No Chinese literal is used as a section key anywhere in the service.**
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from src.services.ai_advisor.section_ids import (
    ACCURACY_TIERS,
    ACTION_VALUES,
    ALL_SECTION_IDS,
    BRIEF_SECTION_IDS,
    LEGACY_SECTION_KEYS,
    POSITION_STATUSES,
    REVIEW_SECTION_IDS,
    SECTION_LABELS,
    SUPPORTED_LANGUAGES,
    adapt_stored_content_json,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO_ROOT / "ux-command-center" / "src" / "i18n" / "locales"
ROUTER_PATH = REPO_ROOT / "src" / "api" / "routes" / "ai_advisor.py"
_CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")

_ADAPTER_NAMES = {"adapt_stored_content_json", "normalize_section_keys"}


def _catalog(language: str) -> dict:
    path = CATALOG_DIR / language / "aiAdvisor.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Backend labels <-> frontend catalog
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_frontend_catalog_labels_every_section_id(language):
    sections = _catalog(language)["briefSection"]["sections"]
    missing = ALL_SECTION_IDS - set(sections)
    assert not missing, (
        f"aiAdvisor.json [{language}] has no label for section ID(s) {sorted(missing)} — "
        f"the section would render as its raw machine ID"
    )


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_backend_markdown_labels_match_the_frontend_catalog(language):
    """The copy-markdown artifact and the on-screen card must say the same thing."""
    catalog = _catalog(language)["briefSection"]["sections"]
    for section_id in sorted(ALL_SECTION_IDS):
        assert SECTION_LABELS[language][section_id] == catalog[section_id], (
            f"section {section_id!r} [{language}]: backend markdown label "
            f"{SECTION_LABELS[language][section_id]!r} != catalog "
            f"{catalog[section_id]!r} — two labels for one section will drift"
        )


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_frontend_catalog_covers_every_enum_value(language):
    block = _catalog(language)["briefSection"]
    assert set(block["accuracyTier"]) == set(ACCURACY_TIERS)
    assert set(block["status"]) == set(POSITION_STATUSES)
    assert set(block["action"]) == set(ACTION_VALUES)


def test_zh_cn_section_labels_are_the_pre_bil_production_strings():
    """The Chinese labels are exactly what production wrote for a year.

    Not a new translation — the glossary's instruction is to reuse the advisor's
    existing wording rather than invent a second vocabulary for the same ideas.
    """
    zh = SECTION_LABELS["zh-CN"]
    assert zh["macro_outlook"] == "宏观形势"
    assert zh["holdings_risk"] == "持仓分析与风险预警"
    assert zh["risk_alerts"] == "风险预警汇总"
    assert zh["action_items"] == "操作建议"
    assert zh["watchlist"] == "明日关注"
    assert zh["trade_summary"] == "交易汇总"
    assert zh["advice_accuracy"] == "建议准确性"
    assert zh["portfolio_performance"] == "组合表现"
    assert zh["lessons_learned"] == "经验沉淀"
    assert zh["rule_updates"] == "准则更新建议"


# ---------------------------------------------------------------------------
# 2. Every read site runs the adapter
# ---------------------------------------------------------------------------


def _content_json_read_sites() -> list[tuple[int, ast.expr]]:
    """Find every `"content_json": <expr>` value built in the AI advisor router.

    Returns (lineno, value_expression) pairs. Dict keys are how these endpoints
    build their responses, so this is where a raw `json.loads` would hide.
    """
    tree = ast.parse(ROUTER_PATH.read_text(encoding="utf-8"))
    found: list[tuple[int, ast.expr]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "content_json":
                found.append((getattr(value, "lineno", node.lineno), value))
    return found


def _mentions_adapter(expr: ast.expr) -> bool:
    for node in ast.walk(expr):
        if isinstance(node, ast.Name) and node.id in _ADAPTER_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _ADAPTER_NAMES:
            return True
    return False


def test_every_content_json_response_goes_through_the_adapter():
    """The gap this workstream closed: five read sites, zero adapters.

    A row written before Program BIL is keyed in Chinese. If a response hands it
    back raw, the frontend gets keys it cannot match, the section order list
    misses, and the card renders under a machine key or not at all.
    """
    sites = _content_json_read_sites()
    assert len(sites) >= 5, (
        f"expected at least 5 content_json response sites in {ROUTER_PATH.name}, "
        f"found {len(sites)} — did the router move?"
    )

    unadapted = [
        lineno
        for lineno, value in sites
        # A bare Name (e.g. `next_content_json`) is a local already adapted above;
        # what must never appear is a json.loads(...) with no adapter around it.
        if not _mentions_adapter(value) and _reads_json(value)
    ]
    assert not unadapted, (
        f"{ROUTER_PATH.name}: content_json returned WITHOUT the read-time adapter at "
        f"line(s) {unadapted}. Wrap it in adapt_stored_content_json() — legacy "
        f"Chinese-keyed rows are never rewritten, so every read site must map them."
    )


def _reads_json(expr: ast.expr) -> bool:
    for node in ast.walk(expr):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "loads":
                return True
    return False


def test_read_site_guard_is_not_vacuous():
    """Red proof: a raw json.loads in a content_json response must be caught."""
    tree = ast.parse('resp = {"content_json": _json.loads(row[4])}')
    dict_node = next(n for n in ast.walk(tree) if isinstance(n, ast.Dict))
    value = dict_node.values[0]
    assert _reads_json(value)
    assert not _mentions_adapter(value), "the guard would not flag a bare json.loads"

    adapted = ast.parse('resp = {"content_json": adapt_stored_content_json(_json.loads(row[4]))}')
    adapted_value = next(n for n in ast.walk(adapted) if isinstance(n, ast.Dict)).values[0]
    assert _mentions_adapter(adapted_value)


# ---------------------------------------------------------------------------
# 3. No Chinese literal is a section key
# ---------------------------------------------------------------------------


def test_section_ids_are_ascii_and_disjoint():
    for section_id in ALL_SECTION_IDS:
        assert section_id.isascii(), f"{section_id!r} is not ASCII"
        assert section_id == section_id.lower(), f"{section_id!r} is not lowercase"
    assert not set(BRIEF_SECTION_IDS) & set(REVIEW_SECTION_IDS), (
        "a brief section and a review section share an ID — the two report types "
        "would collide in any shared lookup"
    )


def test_legacy_map_targets_are_all_real_section_ids():
    unknown = {v for v in LEGACY_SECTION_KEYS.values()} - ALL_SECTION_IDS
    assert not unknown, f"legacy map points at non-existent section ID(s) {sorted(unknown)}"


def test_legacy_map_covers_every_id_in_both_scripts():
    """Every section must be reachable from BOTH Simplified and Traditional."""
    for section_id in sorted(ALL_SECTION_IDS):
        aliases = [k for k, v in LEGACY_SECTION_KEYS.items() if v == section_id]
        assert len(aliases) >= 2, (
            f"{section_id} has only {aliases} — a Traditional-Chinese row written by "
            f"a drifting model would not resolve"
        )
        assert all(_CJK_RE.search(a) for a in aliases)


def test_adapter_is_idempotent():
    legacy = {"宏观形势": {"narrative": "x"}, "操作建议": {"actions": [{"action": "买入"}]}}
    once = adapt_stored_content_json(legacy)
    twice = adapt_stored_content_json(once)
    assert once == twice
    assert once["action_items"]["actions"][0]["action"] == "buy"


def test_adapter_never_raises_on_malformed_rows():
    assert adapt_stored_content_json(None) == {}
    assert adapt_stored_content_json("not a dict") == {}
    assert adapt_stored_content_json({"宏观形势": "a bare string"}) == {"macro_outlook": "a bare string"}
    assert adapt_stored_content_json({"操作建议": {"actions": ["not a dict"]}}) == {
        "action_items": {"actions": ["not a dict"]}
    }


def test_canonical_id_wins_over_a_legacy_alias_for_the_same_section():
    """A mixed payload must not let the legacy spelling shadow the real key."""
    mixed = {"宏观形势": {"narrative": "legacy"}, "macro_outlook": {"narrative": "canonical"}}
    assert adapt_stored_content_json(mixed)["macro_outlook"]["narrative"] == "canonical"
