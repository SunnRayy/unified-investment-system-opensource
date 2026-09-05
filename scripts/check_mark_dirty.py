#!/usr/bin/env python3
"""Static check: async route handlers that open a writable DB must call mark_dirty().

Scope: src/api/routes/*.py, async functions only (route handlers).
Exempt: sync helper functions (e.g. _open_writable), non-route modules.

Writable-connection idioms detected (text patterns inside function body):
  - read_only=False       (explicit DatabaseConnector / duckdb.connect call)
  - _open_writable(       (route-file helper that opens a writable connection)
  - get_writable_db       (FastAPI dependency that yields writable connection)

Exit 0: no violations found.
Exit 1: at least one violation found (new, not in baseline).

Output format (one line per violation):
  src/api/routes/<file>.py:<funcname>

Usage:
  python scripts/check_mark_dirty.py            # detect violations (for verify.sh)
  python scripts/check_mark_dirty.py --all      # print ALL violations including baseline
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
ROUTES_DIR = REPO_ROOT / "src" / "api" / "routes"

# Text patterns inside a function body that indicate it opens a writable DB.
# Only check async functions (route handlers); sync helpers like _open_writable
# are excluded because they RETURN the writable connection to the caller.
WRITABLE_PATTERNS = [
    "read_only=False",
    "_open_writable(",
    "get_writable_db",
]

MARK_DIRTY_PATTERN = "mark_dirty("


def _func_source(source_lines: list[str], node: ast.AsyncFunctionDef) -> str:
    """Extract the complete source text for an async function node."""
    start = node.lineno - 1        # 0-based
    end = node.end_lineno          # end_lineno is 1-based inclusive; slice end is exclusive+1
    return "\n".join(source_lines[start:end])


def check_file(path: Path) -> list[str]:
    """Return a list of violation lines for the given route file.

    Each line is in the format: src/api/routes/<file>.py:<funcname>
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []  # syntax errors are caught by verify.sh check [g]

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue

        func_source = _func_source(lines, node)

        has_writable = any(pat in func_source for pat in WRITABLE_PATTERNS)
        has_mark_dirty = MARK_DIRTY_PATTERN in func_source

        if has_writable and not has_mark_dirty:
            violations.append(f"src/api/routes/{path.name}:{node.name}")

    return violations


def main() -> None:
    if not ROUTES_DIR.exists():
        print(f"ERROR: routes directory not found: {ROUTES_DIR}", file=sys.stderr)
        sys.exit(1)

    all_violations: list[str] = []
    for path in sorted(ROUTES_DIR.glob("*.py")):
        all_violations.extend(check_file(path))

    for line in all_violations:
        print(line)

    if all_violations:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
