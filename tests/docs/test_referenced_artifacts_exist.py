"""Curated guidance docs must not reference repo artifacts that don't exist.

Motivation (2026-07-26 retrospective §10 #2, and the 2026-08-02 doc-purge):
CLAUDE.md and project-status.md told agents to "run `purge_orphan_flow_tags.py`"
for weeks — a script that was never written. A stale pointer costs a session:
someone goes looking for a file that isn't there. This test would have caught it.

Scope is deliberately the *curated, current-guidance* docs (CLAUDE.md, AGENTS.md),
NOT the append-only history logs (project-status.md, CHANGELOG.md), which
legitimately name files that have since been deleted. A reference to a live
source/script/config/doc artifact in the curated docs must resolve on disk.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Curated docs whose artifact references must all resolve.
# CLAUDE.md is excluded from this public export (architect decision,
# 2026-08-17) — checking a doc that was never shipped isn't this test's job.
CURATED_DOCS = ["AGENTS.md"]

# Match artifact paths directly, wherever they appear (backticks, markdown links,
# or plain prose). Character-class stops naturally at `:` (line numbers), `#`
# (anchors), backticks, parens, and whitespace. Globs/placeholders using `*` `<`
# `>` never match because those chars aren't in the class.
# Longest-match-first: `tsx` before `ts`, else `foo.tsx` truncates to `foo.ts`.
_EXT_ALT = r"tsx|ts|py|md|yaml|yml|sh|sql|json|css"
_PREFIX_ALT = r"scripts|src|config|docs|tests|ux-command-center|deploy"
_PATH = re.compile(
    rf"(?:{_PREFIX_ALT})/[A-Za-z0-9_./-]+\.(?:{_EXT_ALT})"
)

# Explicit allowlist for known non-file references that survive the filters —
# illustrative placeholder paths inside example commands, not real artifacts.
ALLOWLIST: set[str] = {
    "src/path/to/file.py",              # AGENTS.md: example baseline-append command
    "ux-command-center/pages/ThePage.tsx",  # AGENTS.md: example grep command
}


def _extract_artifact_refs(text: str) -> set[str]:
    refs: set[str] = set()
    for m in _PATH.finditer(text):
        path = m.group(0).rstrip("/")
        if path in ALLOWLIST:
            continue
        refs.add(path)
    return refs


@pytest.mark.parametrize("doc", CURATED_DOCS)
def test_curated_doc_artifact_references_resolve(doc: str) -> None:
    doc_path = REPO_ROOT / doc
    assert doc_path.exists(), f"curated doc {doc} itself is missing"
    refs = _extract_artifact_refs(doc_path.read_text(encoding="utf-8"))
    missing = sorted(p for p in refs if not (REPO_ROOT / p).exists())
    assert not missing, (
        f"{doc} references artifacts that do not exist on disk: {missing}. "
        f"Either the file was deleted/renamed (fix the doc) or the reference is a "
        f"phantom (remove it). If it is a deliberate non-file reference, add it to "
        f"ALLOWLIST with a reason."
    )


def test_the_scan_actually_finds_references() -> None:
    """Anti-vacuity: the extractor must be pulling real refs, not silently zero."""
    total = 0
    for doc in CURATED_DOCS:
        total += len(_extract_artifact_refs((REPO_ROOT / doc).read_text(encoding="utf-8")))
    assert total >= 10, f"expected the curated docs to reference >=10 artifacts, found {total}"


def test_extractor_mechanics() -> None:
    """The extractor catches a phantom, ignores placeholders, and does not
    truncate .tsx to .ts (the ordering bug this test itself exposed)."""
    sample = (
        "See `scripts/ghost.py` which does not exist. "
        "Real file `src/api/routes/data.py`. "
        "Line ref `scripts/verify.sh:42` and anchor `docs/known-issues.md#x`. "
        "A component grep on `ux-command-center/pages/WealthOS.tsx`. "
        "Placeholder `src/path/to/<name>.py` and glob `tests/foo_*.py`. "
        "Prose path config/verification.yaml too."
    )
    refs = _extract_artifact_refs(sample)
    assert "scripts/ghost.py" in refs                       # phantom is caught
    assert "src/api/routes/data.py" in refs
    assert "scripts/verify.sh" in refs                      # :42 stripped
    assert "docs/known-issues.md" in refs                   # #x stripped
    assert "ux-command-center/pages/WealthOS.tsx" in refs   # .tsx NOT truncated
    assert "config/verification.yaml" in refs               # bare prose path
    # placeholders / globs never become refs
    assert not any("<name>" in r or "*" in r or "path/to" in r for r in refs)
    # and the phantom would fail the existence check
    assert not (REPO_ROOT / "scripts/ghost.py").exists()
