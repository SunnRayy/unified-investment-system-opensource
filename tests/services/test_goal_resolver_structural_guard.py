"""Structural guard: `target_net_worth_cny` is read in exactly two files
under src/ — src/services/verification_config.py (the definition/default)
and src/services/goal_resolver.py (the ONLY permitted reader, which falls
back to it when no active retirement goal exists).

Plan: docs/plans/2026-07-26-your-path-design-implementation.md §3.2/W-1.

Before this fix, `forecast_levers.compute_levers` and
`north_star_glide.glide_path` EACH independently read
`load_verification_config().north_star.target_net_worth_cny` — a second,
untethered source of the forecast target that only coincidentally matched
the Goals page's live retirement goal. This is the anti-drift ratchet (same
house style as tests/validation/test_amount_net_raw_sum_guard.py, Rule 26):
any future code that reintroduces a direct read outside the two allowed
files recreates the exact two-sources-of-truth defect goal_resolver exists
to close.

Uses an AST scan (not a plain grep) so it flags real attribute
reads (`cfg.target_net_worth_cny`) but does not false-positive on the
dataclass field name/keyword-argument occurrences that legitimately live
inside verification_config.py itself, or on prose mentions of the string in
docstrings/comments elsewhere.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_TARGET_ATTR = "target_net_worth_cny"

# The only two files allowed to read the attribute:
#   - verification_config.py: the definition + YAML-load site (not a "read"
#     via attribute access at all, but included for clarity/robustness)
#   - goal_resolver.py: the single resolver, permitted fallback reader
_ALLOWED_FILES = frozenset({
    "src/services/verification_config.py",
    "src/services/goal_resolver.py",
})


def _project_root() -> Path:
    # tests/services/test_goal_resolver_structural_guard.py -> project root
    return Path(__file__).resolve().parents[2]


def _attribute_read_sites(src_dir: Path) -> dict[str, list[int]]:
    """Return {relative_path: [line numbers]} for every AST Attribute node
    whose `.attr` is `target_net_worth_cny`, across every .py file under
    src_dir."""
    root = src_dir.parent
    hits: dict[str, list[int]] = {}

    for path in sorted(src_dir.rglob("*.py")):
        rel_path = str(path.relative_to(root))
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # pragma: no cover — defensive, no non-UTF8 .py files expected

        try:
            tree = ast.parse(text, filename=rel_path)
        except SyntaxError:
            continue  # pragma: no cover — no invalid-syntax .py files expected

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == _TARGET_ATTR:
                hits.setdefault(rel_path, []).append(getattr(node, "lineno", -1))

    return hits


def test_target_net_worth_cny_read_only_in_allowed_files():
    """Fail loudly if `target_net_worth_cny` is read via attribute access
    (`something.target_net_worth_cny`) anywhere under src/ outside
    verification_config.py / goal_resolver.py.

    Fix: route through src.services.goal_resolver.resolve_north_star_goal(db)
    instead of reading load_verification_config().north_star.target_net_worth_cny
    directly (see that module's docstring for the full resolution rule)."""
    root = _project_root()
    hits = _attribute_read_sites(root / "src")

    offenders = {path: lines for path, lines in hits.items() if path not in _ALLOWED_FILES}
    if offenders:
        detail = "\n".join(f"  {path}: lines {lines}" for path, lines in sorted(offenders.items()))
        pytest.fail(
            "Direct attribute read of target_net_worth_cny found outside the "
            "allowed files (src/services/verification_config.py, "
            "src/services/goal_resolver.py). This recreates the two-sources-"
            "of-truth forecast-target defect — route through "
            "src.services.goal_resolver.resolve_north_star_goal(db) instead.\n"
            f"Offending sites:\n{detail}"
        )


def test_allowed_files_still_exist_and_are_the_only_readers():
    """Sanity check that the allowlist itself isn't stale: both files must
    still exist, and goal_resolver.py must still actually read the attribute
    (proves the guard isn't vacuously passing because the resolver was
    rewritten to no longer touch it)."""
    root = _project_root()
    for rel in _ALLOWED_FILES:
        assert (root / rel).exists(), f"expected allowlisted file to exist: {rel}"

    hits = _attribute_read_sites(root / "src")
    assert "src/services/goal_resolver.py" in hits, (
        "goal_resolver.py no longer reads target_net_worth_cny via attribute "
        "access — update this guard's expectations if the fallback mechanism "
        "changed intentionally."
    )
