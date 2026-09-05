#!/usr/bin/env python3
"""Fail if a shipped Markdown doc links to a path the export does not ship.

A link that 404s on the repository's front page is worse than no link: it tells a
newcomer the project is broken before they have read a line of code.

This was audited by hand once, on 2026-08-17, and the manifest still records that
audit as the reason several files are excluded. By 2026-09-05 the published tree
had **ten** dangling links, including the "Deploying to Cloud Run" link in both
READMEs and both quickstarts. That is what an un-automated check does: it is
correct on the day it is run and decays silently afterwards.

Two ways to satisfy it, and the right one depends on the target:
  * the target belongs in the public repo  -> add it to export_manifest.txt
  * the target is internal (incident logs, audits, planning docs) -> remove or
    reword the link, and do NOT export the target to silence the check

Usage:
    python scripts/check-dangling-links.py <exported-tree>
    python scripts/check-dangling-links.py            # defaults to a fresh export

Exit codes: 0 clean, 1 dangling links found, 2 could not build a tree to check.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# [text](target) — ignoring pure-anchor links and any URL scheme.
LINK = re.compile(r"\[[^\]]*\]\((?!#)([^)\s]+?)(?:#[^)]*)?\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "tel:", "data:")
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".pytest_cache"}


def find_dangling(tree: Path) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    for md in sorted(tree.rglob("*.md")):
        if SKIP_DIRS & set(md.parts):
            continue
        for lineno, line in enumerate(md.read_text(errors="ignore").splitlines(), 1):
            for m in LINK.finditer(line):
                target = m.group(1).strip()
                if target.startswith(SKIP_PREFIXES) or target.startswith("<"):
                    continue
                resolved = (md.parent / target).resolve()
                # Anything resolving outside the tree is dangling by definition:
                # the reader has only this tree.
                try:
                    resolved.relative_to(tree.resolve())
                except ValueError:
                    out.append((str(md.relative_to(tree)), target, lineno))
                    continue
                if not resolved.exists():
                    out.append((str(md.relative_to(tree)), target, lineno))
    return out


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        tree = Path(argv[1])
        if not tree.is_dir():
            print(f"not a directory: {tree}", file=sys.stderr)
            return 2
        cleanup = None
    else:
        tree = Path(tempfile.mkdtemp(prefix="huinsight-linkcheck."))
        script = ROOT / "tools" / "release" / "export_public.sh"
        if not script.is_file():
            print("export_public.sh not found; pass a tree explicitly", file=sys.stderr)
            return 2
        proc = subprocess.run(
            ["bash", str(script), str(tree)], capture_output=True, text=True
        )
        if proc.returncode != 0:
            print(proc.stdout[-2000:], file=sys.stderr)
            print(proc.stderr[-2000:], file=sys.stderr)
            return 2
        cleanup = tree

    dangling = find_dangling(tree)
    count = len(list(tree.rglob("*.md")))
    print(f"dangling-link check: scanned {count} markdown file(s) in {tree}")

    if dangling:
        print(f"\nFAIL — {len(dangling)} dangling link(s):\n")
        for src, target, lineno in dangling:
            print(f"  {src}:{lineno} -> {target}")
        print(
            "\nEither add the target to tools/release/export_manifest.txt (if it "
            "belongs in the public repo) or remove/reword the link (if it is "
            "internal). Do not export an internal document just to silence this."
        )
        return 1

    print("PASS — every internal link in the shipped docs resolves")
    if cleanup:
        subprocess.run(["rm", "-rf", str(cleanup)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
