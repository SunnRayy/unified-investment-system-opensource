"""Structural guard: no "Your Path" design-mock literal in src/ (backend).

Plan: docs/plans/2026-07-26-your-path-design-implementation.md §4b / plan
task spec §4b — "no design constant may appear as a literal in src/ or
ux-command-center/". The mock (`Forecast - Your Path.dc.html`, see
docs/design/2026-07-26-your-path.dc.html.md's header) hardcodes illustrative
NW0/GOAL/RUN_RATE/ER/VOL figures that happen to look plausible but are NOT
live data — pasting one into real code is the same failure class as a second,
drifted implementation of the ADR-026 forecast engine.

No pre-existing forbidden-literal guard was found in this repo when W-5 was
implemented (searched tests/validation/ and ux-command-center/tests/ for
"forbidden"/"design constant"/"literal_guard" — nothing matched); this file
and its frontend sibling (ux-command-center/tests/your-path-forbidden-
literals.test.ts) are new, not extensions of a prior guard, despite the task
spec's "extend the existing" phrasing — see the W-5 dispatch report for that
discrepancy.

Companion frontend guard: ux-command-center/tests/your-path-forbidden-
literals.test.ts (same literal set, scans ux-command-center/src +
components + pages).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# The mock's illustrative constants (docs/design/2026-07-26-your-path.dc.html.md
# §4, "Adversarial self-review" bullet + §3.4/§3.6). Word-boundary matched so
# "20000000" doesn't also flag "120000000" etc.
FORBIDDEN_LITERALS: tuple[str, ...] = (
    "3269850",
    "20000000",
    "44632",
    "12.4",
    "17.6",
    "397980",
    "137600",
    "224900",
)

_PATTERNS = [re.compile(r"(?<![\w.])" + re.escape(lit) + r"(?![\w.])") for lit in FORBIDDEN_LITERALS]


def _project_root() -> Path:
    # tests/validation/test_your_path_forbidden_literals.py -> project root
    return Path(__file__).resolve().parents[2]


def _scan_backend_src() -> list[tuple[str, int, str]]:
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
            for pattern in _PATTERNS:
                if pattern.search(line):
                    violations.append((rel_path, line_no, line.strip()))
                    break

    return violations


def test_no_your_path_mock_literals_in_backend_src():
    """Fail loudly if any of the design mock's illustrative figures appear as
    a literal anywhere under src/ — every number in the forecast pipeline
    must be live/derived (see forecast_levers.py's own §4b docstring)."""
    violations = _scan_backend_src()

    if violations:
        lines = "\n".join(f"  {path}:{line_no}: {text}" for path, line_no, text in violations)
        pytest.fail(
            "'Your Path' design-mock literal found in src/ — every forecast number "
            "must be live/derived, never copied from the illustrative mock "
            "(docs/design/2026-07-26-your-path.dc.html.md §4, plan §4b).\n"
            f"Forbidden literals: {FORBIDDEN_LITERALS}\n"
            f"Violations:\n{lines}"
        )
