"""Structural parity across the bilingual prompt scaffold (Program BIL / WS-5).

The failure this guards against is mundane and certain to happen: someone
tightens a guardrail in English and forgets the Chinese sibling. The prompt
still composes, the tests still pass, and the two languages quietly diverge
until a model does something odd in one of them and nobody knows why.

So the parity is asserted, not trusted:
  - every block carries exactly the supported languages, no more, no fewer
  - bullet counts match across languages within a block
  - schema blocks expose an identical JSON key set across languages
  - no schema block contains a CJK character inside a JSON key

Each assertion below is paired with a red/green proof in
`test_parity_checks_are_not_vacuous`, because a gate that cannot fail is worse
than no gate at all.
"""

from __future__ import annotations

import re

import pytest

from src.services.ai_advisor import prompts as prompts_module
from src.services.ai_advisor.prompts import (
    BILINGUAL_PROMPT_BLOCKS,
    SCHEMA_PROMPT_BLOCKS,
    SUPPORTED_LANGUAGES,
)
from src.services.ai_advisor.review_generator import (
    _FALLBACK_QUESTIONS_BY_LANG,
    _QUESTION_SCAFFOLD,
    _REVIEW_SCAFFOLD,
)
from src.services.ai_advisor.section_ids import BRIEF_SECTION_IDS, REVIEW_SECTION_IDS

_BULLET_RE = re.compile(r"^- ", re.MULTILINE)
_JSON_KEY_RE = re.compile(r'"([^"]+)"\s*:')
_CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")


def _bullet_count(text: str) -> int:
    return len(_BULLET_RE.findall(text))


def _json_keys(text: str) -> set[str]:
    return set(_JSON_KEY_RE.findall(text))


# ---------------------------------------------------------------------------
# Language coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("block_name", sorted(BILINGUAL_PROMPT_BLOCKS))
def test_every_block_covers_exactly_the_supported_languages(block_name):
    block = BILINGUAL_PROMPT_BLOCKS[block_name]
    assert set(block) == set(SUPPORTED_LANGUAGES), (
        f"prompt block {block_name!r} covers {sorted(block)}, expected "
        f"{sorted(SUPPORTED_LANGUAGES)} — add the missing language variant "
        f"next to its sibling in the same literal"
    )
    for language, text in block.items():
        assert text.strip(), f"{block_name}[{language}] is empty"


@pytest.mark.parametrize("block_name", sorted(BILINGUAL_PROMPT_BLOCKS))
def test_bullet_counts_match_across_languages(block_name):
    """A guardrail added in one language and forgotten in the other fails here."""
    block = BILINGUAL_PROMPT_BLOCKS[block_name]
    counts = {language: _bullet_count(text) for language, text in block.items()}
    assert len(set(counts.values())) == 1, (
        f"prompt block {block_name!r} has mismatched bullet counts {counts} — "
        f"a rule was edited in one language only"
    )


# ---------------------------------------------------------------------------
# Schema blocks: the JSON contract must be language-independent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("block_name", sorted(SCHEMA_PROMPT_BLOCKS))
def test_schema_json_keys_are_identical_across_languages(block_name):
    block = BILINGUAL_PROMPT_BLOCKS[block_name]
    key_sets = {language: _json_keys(text) for language, text in block.items()}
    reference_language, reference = next(iter(key_sets.items()))
    for language, keys in key_sets.items():
        assert keys == reference, (
            f"{block_name}: JSON keys differ between {reference_language} and "
            f"{language}. Only prose VALUES may be localized; the contract is "
            f"identity. Missing in {language}: {sorted(reference - keys)}; "
            f"extra: {sorted(keys - reference)}"
        )


@pytest.mark.parametrize("block_name", sorted(SCHEMA_PROMPT_BLOCKS))
def test_no_schema_json_key_contains_cjk(block_name):
    """The whole point of WS-5: a section key is never a Chinese literal again."""
    for language, text in BILINGUAL_PROMPT_BLOCKS[block_name].items():
        offenders = [key for key in _json_keys(text) if _CJK_RE.search(key)]
        assert not offenders, (
            f"{block_name}[{language}] declares CJK JSON key(s) {offenders} — "
            f"section identity must be stable ASCII (see section_ids.py)"
        )


def test_brief_and_review_schemas_declare_every_section_id():
    """The schema the model is shown must list exactly the IDs we validate against."""
    for language, text in BILINGUAL_PROMPT_BLOCKS["brief_json_schema"].items():
        keys = _json_keys(text)
        missing = set(BRIEF_SECTION_IDS) - keys
        assert not missing, f"brief schema [{language}] omits section IDs {sorted(missing)}"

    for language, text in BILINGUAL_PROMPT_BLOCKS["review_json_schema"].items():
        keys = _json_keys(text)
        missing = set(REVIEW_SECTION_IDS) - keys
        assert not missing, f"review schema [{language}] omits section IDs {sorted(missing)}"


def test_accuracy_tier_is_in_the_review_schema_for_both_languages():
    """Deliverable 2: the scorecard badge is an enum, not matched prose."""
    for language, text in BILINGUAL_PROMPT_BLOCKS["review_json_schema"].items():
        assert "accuracy_tier" in _json_keys(text), (
            f"review schema [{language}] is missing the accuracy_tier enum"
        )
        assert "high|medium|low" in text, (
            f"review schema [{language}] does not pin accuracy_tier to the enum values"
        )


# ---------------------------------------------------------------------------
# The non-prompt bilingual scaffolds carry the same obligation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,scaffold",
    [
        ("_QUESTION_SCAFFOLD", _QUESTION_SCAFFOLD),
        ("_REVIEW_SCAFFOLD", _REVIEW_SCAFFOLD),
        ("_FALLBACK_QUESTIONS_BY_LANG", _FALLBACK_QUESTIONS_BY_LANG),
        ("_MARKDOWN_LABELS", None),
    ],
)
def test_scaffold_dicts_cover_the_supported_languages(name, scaffold):
    if scaffold is None:
        from src.services.ai_advisor.brief_generator import _MARKDOWN_LABELS

        scaffold = _MARKDOWN_LABELS
    assert set(scaffold) == set(SUPPORTED_LANGUAGES), (
        f"{name} covers {sorted(scaffold)}, expected {sorted(SUPPORTED_LANGUAGES)}"
    )
    variants = list(scaffold.values())
    if isinstance(variants[0], dict):
        reference = set(variants[0])
        for language, entry in scaffold.items():
            assert set(entry) == reference, (
                f"{name}[{language}] has keys {sorted(entry)}, expected {sorted(reference)}"
            )
    else:  # list of Question objects
        lengths = {language: len(entry) for language, entry in scaffold.items()}
        assert len(set(lengths.values())) == 1, f"{name} entry counts differ: {lengths}"


# ---------------------------------------------------------------------------
# Anti-vacuity — watch every gate fail before trusting it
# ---------------------------------------------------------------------------


def test_parity_checks_are_not_vacuous(monkeypatch):
    """Break each invariant deliberately; the corresponding check must go RED."""
    # 1 ── a language dropped from a block
    monkeypatch.setitem(
        prompts_module.BILINGUAL_PROMPT_BLOCKS,
        "brief_reminders",
        {"en": "Key reminders:\n- one"},
    )
    with pytest.raises(AssertionError, match="expected"):
        test_every_block_covers_exactly_the_supported_languages("brief_reminders")

    # 2 ── a bullet added in EN only
    monkeypatch.setitem(
        prompts_module.BILINGUAL_PROMPT_BLOCKS,
        "brief_reminders",
        {"en": "Key reminders:\n- one\n- two", "zh-CN": "重点提醒：\n- 一"},
    )
    with pytest.raises(AssertionError, match="mismatched bullet counts"):
        test_bullet_counts_match_across_languages("brief_reminders")

    # 3 ── a JSON key that exists in one language only
    monkeypatch.setitem(
        prompts_module.BILINGUAL_PROMPT_BLOCKS,
        "brief_json_schema",
        {
            "en": '{"macro_outlook": {"narrative": "x", "key_factors": []}}',
            "zh-CN": '{"macro_outlook": {"narrative": "x"}}',
        },
    )
    with pytest.raises(AssertionError, match="JSON keys differ"):
        test_schema_json_keys_are_identical_across_languages("brief_json_schema")

    # 4 ── the original bug: a Chinese literal used as a JSON key
    monkeypatch.setitem(
        prompts_module.BILINGUAL_PROMPT_BLOCKS,
        "brief_json_schema",
        {
            "en": '{"宏观形势": {"narrative": "x"}}',
            "zh-CN": '{"宏观形势": {"narrative": "x"}}',
        },
    )
    with pytest.raises(AssertionError, match="CJK JSON key"):
        test_no_schema_json_key_contains_cjk("brief_json_schema")

    # 5 ── a section ID missing from the schema shown to the model
    monkeypatch.setitem(
        prompts_module.BILINGUAL_PROMPT_BLOCKS,
        "brief_json_schema",
        {"en": '{"macro_outlook": {}}', "zh-CN": '{"macro_outlook": {}}'},
    )
    with pytest.raises(AssertionError, match="omits section IDs"):
        test_brief_and_review_schemas_declare_every_section_id()

    # 6 ── accuracy_tier removed from the review schema
    monkeypatch.setitem(
        prompts_module.BILINGUAL_PROMPT_BLOCKS,
        "review_json_schema",
        {
            "en": '{"advice_accuracy": {"scorecard": []}}',
            "zh-CN": '{"advice_accuracy": {"scorecard": []}}',
        },
    )
    with pytest.raises(AssertionError, match="missing the accuracy_tier enum"):
        test_accuracy_tier_is_in_the_review_schema_for_both_languages()
