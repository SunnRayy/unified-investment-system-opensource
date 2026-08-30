"""Structural guard: no raw `SUM(amount_net)` in src/**/*.py.

Plan: docs/plans/2026-07-25-amount-net-sign-convention-sweep.md §6/§7 (F-4/T4).
Guardrail: AGENTS.md Rule 26 (transactions.amount_net Sign Conventions).

`transactions.amount_net` has no normalization layer — three per-reader sign
conventions live in the same column (magnitude-only, Schwab cash-flow-signed,
RSU inverted). A bare `SUM(amount_net)` — whether raw SQL text or a Python
`sum(amount_net ...)` reduction — silently mixes conventions and is a defect
on sight per Rule 26. The safe patterns are `SUM(ABS(amount_net))` (sign-
agnostic) or reading magnitude + re-deriving direction from
`LOWER(transaction_type)`.

This test scans every `.py` file under `src/` for the raw pattern and fails
loudly, pointing at AGENTS.md Rule 26, if it finds one outside the explicit
allowlist below (empty as of the 2026-07-25 sweep — every existing call site
already wraps in ABS(), see `attribution.py:577`).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


def _project_root() -> Path:
    # tests/validation/test_amount_net_raw_sum_guard.py -> project root
    return Path(__file__).resolve().parents[2]


# Explicit allowlist of (relative_path, matched_snippet_prefix) pairs that are
# KNOWN-SAFE despite matching the raw pattern (e.g. a comment/docstring
# discussing the anti-pattern itself). Must stay empty for real SQL/Python
# reductions — if you're tempted to add an entry here for a live SUM, stop
# and use SUM(ABS(amount_net)) or a directional re-sign instead (Rule 26).
ALLOWLIST: frozenset[tuple[str, int]] = frozenset()

# Matches `SUM(amount_net` (SQL) or `sum(amount_net` (Python), case-insensitive,
# allowing arbitrary whitespace between SUM/sum and the opening paren+name.
# Deliberately does NOT match `SUM(ABS(amount_net))` — the `ABS(` in between
# breaks the direct `sum\(\s*amount_net` adjacency this regex requires.
_RAW_SUM_PATTERN = re.compile(r"\bsum\s*\(\s*amount_net\b", re.IGNORECASE)


def _scan_src_for_raw_sum() -> list[tuple[str, int, str]]:
    """Return (relative_path, line_number, line_text) for every raw-sum hit
    under src/ that isn't in ALLOWLIST."""
    root = _project_root()
    src_dir = root / "src"
    violations: list[tuple[str, int, str]] = []

    for path in sorted(src_dir.rglob("*.py")):
        rel_path = str(path.relative_to(root))
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # pragma: no cover — defensive, no non-UTF8 .py files expected

        for line_no, line in enumerate(text.splitlines(), start=1):
            if _RAW_SUM_PATTERN.search(line):
                if (rel_path, line_no) in ALLOWLIST:
                    continue
                violations.append((rel_path, line_no, line.strip()))

    return violations


def test_no_raw_sum_amount_net_outside_allowlist():
    """Fail loudly on any un-allowlisted `SUM(amount_net)` / `sum(amount_net)`
    under src/. See AGENTS.md Rule 26 for the safe patterns
    (SUM(ABS(amount_net)), or abs() + re-sign from LOWER(transaction_type))."""
    violations = _scan_src_for_raw_sum()

    if violations:
        lines = "\n".join(f"  {path}:{line_no}: {text}" for path, line_no, text in violations)
        pytest.fail(
            "Raw SUM(amount_net) / sum(amount_net) found outside the allowlist — "
            "this silently mixes incompatible per-reader sign conventions "
            "(see AGENTS.md Rule 26: transactions.amount_net Sign Conventions).\n"
            "Fix: wrap in ABS() for a sign-agnostic total, or abs() + re-derive "
            "direction from LOWER(transaction_type) against an OUTFLOW/INFLOW set.\n"
            f"Violations:\n{lines}"
        )


def test_allowlist_is_empty():
    """As of the 2026-07-25 sweep, every real call site already uses
    SUM(ABS(amount_net)) — the allowlist should stay empty. If this test
    starts failing because someone added an entry, that addition needs its
    own justification comment and sign-off, not a silent bypass."""
    assert ALLOWLIST == frozenset(), (
        "ALLOWLIST is no longer empty — review each entry against AGENTS.md "
        "Rule 26 before accepting it as a legitimate exception."
    )
