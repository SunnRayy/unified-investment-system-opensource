#!/usr/bin/env python3
"""Find button elements shipped without an interaction handler.

Backs `verify.sh` check [d] (AGENTS.md Rule 19: never ship a button as a pure
visual affordance).

Why this replaced the inline grep
---------------------------------
The original check was a one-line `grep '<[Bb]utton[^>]*>'` over
`ux-command-center/src/` only. It had two blind spots that let a real violation
ship:

1. **Directory scope.** `src/` is 35 files. The pages and components live in
   `ux-command-center/pages/` (34 files) and `ux-command-center/components/`
   (62 files) — 96 files the check never opened.
2. **Single-line only.** A `<button>` whose props span multiple lines was
   invisible, and multi-line is the majority style in this codebase.

The Dashboard "CIRCUIT BREAKER" badge sat in both blind spots for months: a
primary-coloured button, no `onClick`, in `pages/`, written across seven lines.

Violation identity
------------------
Emitted as ``<file>:<label>``, never ``<file>:<line>``. Line-keyed baselines
generate phantom "NEW violations" the moment anything above them shifts, which
sends the next reader chasing ghosts. The label is the button's i18n key or its
visible text, which survives reformatting.

Exit code 1 if any violation is found (verify.sh compares against the baseline).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "ux-command-center"

SCAN_DIRS = ("src", "pages", "components")

# Any one of these makes the element interactive (or deliberately inert).
HANDLER_RE = re.compile(
    r"onClick|onMouseDown|onPointerDown|onKeyDown|"
    r"type=[\"']submit|disabled|form=|\{\.\.\."
)

# `<button` and `<Button`, but not `<ButtonGroup` / `<ButtonVariant` etc.
OPEN_TAG_RE = re.compile(r"<(button|Button)(?=[\s/>])")

SKIP_FILE_RE = re.compile(r"\.test\.|\.spec\.|\.stories\.")

I18N_KEY_RE = re.compile(r"\bt\(\s*['\"]([^'\"]+)['\"]")
TAG_RE = re.compile(r"<[^>]*>")


def _opening_tag_end(src: str, start: int) -> int:
    """Index of the '>' closing the opening tag that begins at `start`.

    Tracks brace depth so a '>' inside a JSX expression (`{a > b ? x : y}`) or
    inside an arrow function does not end the tag early.
    """
    i = start
    depth = 0
    while i < len(src):
        ch = src[i]
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        elif ch == ">" and depth == 0:
            return i
        i += 1
    return len(src) - 1


def _label_for(src: str, tag_end: int, tag: str) -> str:
    """A stable, human-readable identity for this button.

    Preference order: the i18n key it renders, then its literal visible text,
    then an aria-label, then a normalized slice of the opening tag.
    """
    body_end = src.find("</button>", tag_end)
    if body_end == -1:
        body_end = src.find("</Button>", tag_end)
    body = src[tag_end + 1 : body_end] if body_end != -1 else ""

    key = I18N_KEY_RE.search(body)
    if key:
        return f"t({key.group(1)})"

    aria = re.search(r"aria-label=[\"']([^\"']+)[\"']", tag)
    if aria:
        return f"aria-label={aria.group(1)}"

    text = TAG_RE.sub(" ", body)
    text = re.sub(r"\{[^{}]*\}", " ", text)
    text = " ".join(text.split())
    if text:
        return text[:40]

    icon = re.search(r">([a-z_]+)<", body)
    if icon:
        return f"icon:{icon.group(1)}"

    return "<unlabelled>"


def check_file(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    rel = path.relative_to(REPO_ROOT).as_posix()
    violations: list[str] = []

    for match in OPEN_TAG_RE.finditer(src):
        tag_end = _opening_tag_end(src, match.end())
        tag = src[match.start() : tag_end + 1]
        if HANDLER_RE.search(tag):
            continue
        violations.append(f"{rel}:{_label_for(src, tag_end, tag)}")

    return violations


def main() -> None:
    if not FRONTEND.exists():
        print(f"ERROR: frontend directory not found: {FRONTEND}", file=sys.stderr)
        sys.exit(1)

    all_violations: list[str] = []
    scanned = 0
    for name in SCAN_DIRS:
        root = FRONTEND / name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.tsx")):
            if SKIP_FILE_RE.search(path.name):
                continue
            scanned += 1
            all_violations.extend(check_file(path))

    if scanned == 0:
        print("ERROR: no .tsx files scanned — check SCAN_DIRS.", file=sys.stderr)
        sys.exit(1)

    for line in sorted(set(all_violations)):
        print(line)

    sys.exit(1 if all_violations else 0)


if __name__ == "__main__":
    main()
