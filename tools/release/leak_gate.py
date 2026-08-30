#!/usr/bin/env python3
"""OSR leak gate — fail if owner-identifying data appears in export candidates.

Program OSR (open-source release). Two modes:

  Artifact mode (default, used from WS-1 onward):
      python tools/release/leak_gate.py
  scans only the files being built FOR the public repo — the demo-data
  generator, the persona spec, and the synthetic fixtures. This runs while the
  private repo still legitimately contains the owner's real data everywhere
  else, so a whole-tree scan would be pure noise.

  Staging mode (WS-7, before the public push):
      python tools/release/leak_gate.py --paths <staging-tree>  --strict
  scans an assembled export tree, where NOTHING may match.

Exit 0 = clean, 1 = leak(s) found. Wire into CI on the osr branch.

Why this exists early rather than at WS-7: two real leaks were caught by manual
review alone (the owner's full Schwab account number copied out of a real
fixture into the generator; a verbatim Schwab interest label). Manual review
does not scale across phases — this does.

xlsx handling: Excel files are zip archives, so a plain text grep silently
finds nothing and gives false confidence. Cell values are extracted properly.
"""
from __future__ import annotations

import argparse
import base64
import re
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PATTERNS_FILE = HERE / "leak_patterns.txt"

# Files built for the public repo — the surface worth gating before WS-7.
DEFAULT_PATHS = [
    "tools/demo_data",
    "tests/fixtures/readers",
    "examples",
]

# --preflight: the surface that will ACTUALLY ship, beyond the synthetic
# artifacts. Artifact mode going green says nothing about these, which is how a
# tracked identity leak can sit in plain sight while the gate reports success —
# exactly what happened with config/settings.yaml (a real headshot embedded as a
# 141KB base64 avatar, plus the owner's iCloud path in every reader's data_dir).
# Expected RED until WS-4 gitignores the real configs and ships .example ones.
# Kept separate from DEFAULT_PATHS so per-phase gating stays a clean green/red
# signal while the pre-flight blocker list stays mechanically visible.
PREFLIGHT_PATHS = DEFAULT_PATHS + [
    "config",
    "src",
    "scripts",
    ".github",
    "seeds",
    "Dockerfile",
    "docker-compose.yml",
    "main.py",
    "dev.sh",
]

# The patterns file necessarily contains the strings it forbids.
SELF_EXEMPT = {PATTERNS_FILE.resolve(), Path(__file__).resolve()}

TEXT_SUFFIXES = {".py", ".txt", ".md", ".yaml", ".yml", ".json", ".csv", ".sh",
                 ".ts", ".tsx", ".js", ".sql", ".toml", ".cfg", ".ini", ".html"}
ZIP_SUFFIXES = {".xlsx", ".xlsm", ".docx", ".zip"}

MAX_FILE_BYTES = 1_000_000          # nothing in a clean export should exceed this
MAX_B64_BLOB = 10_000               # embedded base64 (e.g. the owner's avatar)
B64_RE = re.compile(r"[A-Za-z0-9+/]{%d,}={0,2}" % MAX_B64_BLOB)

# Large, NON-ROUND currency amounts — the systematic version of a leak class the
# literal patterns above cannot cover. Found 2026-08-17: four ADRs and seven
# api-specs carried real worked examples from the owner's data (RSU vests,
# housing-fund withdrawals, dated transactions, screenshotted net worth). The
# gate passed them all, because you cannot enumerate figures you have never seen.
#
# The heuristic that separates real from illustrative is ROUNDNESS, not size.
# Real personal financial data is large and arbitrary (e.g. an owner's actual
# account balance). Deliberately-authored example data is round (¥3,500,000
# persona target, ¥20,000,000 goal, ¥123,456 template placeholder). So: flag
# amounts at or above the threshold that are NOT a multiple of 1,000. Advisory
# by nature — expect some false positives on genuine constants; triage, don't
# auto-trust.
CURRENCY_MIN = 100_000
CURRENCY_RE = re.compile(r"[¥$€£]\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?)")

# Same leak class with the currency symbol stripped off: a real figure sitting in
# a raw JSON/dict number field — no ¥, no separators, so CURRENCY_RE cannot see it.
# Keyed on money-ish field names to stay out of timestamps, ids and counts.
MONEY_KEY_RE = re.compile(
    r"""["']?(\w*(?:net_worth|networth|_nw|nw|value|amount|cost|balance|worth|
        price|total|basis|pnl|gain|cash|premium|income|expense|contribution)\w*)
        ["']?\s*[:=]\s*(\d{6,}(?:\.\d+)?)""",
    re.IGNORECASE | re.VERBOSE,
)


def _suspicious_amounts(text: str) -> list[str]:
    hits = []
    for m in CURRENCY_RE.finditer(text):
        raw = m.group(1)
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if value >= CURRENCY_MIN and value % 1000 != 0:
            hits.append(m.group(0))
    for m in MONEY_KEY_RE.finditer(text):
        try:
            value = float(m.group(2))
        except ValueError:
            continue
        if value >= CURRENCY_MIN and value % 1000 != 0:
            hits.append(f"{m.group(1)}={m.group(2)}")
    return sorted(set(hits))


def _persona_fund_codes() -> set[str]:
    """Fund codes the persona legitimately uses, read from the spec itself.

    Derived rather than hardcoded so the allowlist cannot drift from the persona.
    """
    codes = {"000001", "000002"}          # sequential example placeholders
    persona = ROOT / "tools" / "demo_data" / "persona.yaml"
    try:
        codes |= set(re.findall(r"code:\s*[\"']?(\d{6})", persona.read_text(encoding="utf-8")))
    except Exception:
        pass
    return codes


# Real CN fund identifiers are a leak class no other check covers: they are not
# currency amounts and cannot be enumerated in leak_patterns (there are thousands).
# Found 2026-08-17 sitting beside amounts being scrubbed — visible only by
# reading. Any six-digit fund code that is not the persona's own is presumed
# real.
FUND_CODE_RE = re.compile(r"CN_FUND_(\d{6})")


def _foreign_fund_codes(text: str) -> list[str]:
    allowed = _persona_fund_codes()
    return sorted({f"CN_FUND_{c}" for c in FUND_CODE_RE.findall(text) if c not in allowed})


ACCEPTED_FILE = HERE / "accepted_findings.txt"


def load_accepted() -> list[tuple[str, str]]:
    """Triaged findings that are known-safe, as (path-substring, match-substring).

    Without this the gate is unusable at the finish line: triage repeatedly shows
    most findings are arbitrary test literals or structural product vocabulary, so
    a gate that cannot record "investigated, safe" either never goes green (and is
    ignored) or gets weakened at the pattern level (and goes blind). This records
    the judgement instead, per finding, and the accepted count is always printed so
    the list stays visible rather than becoming a rubber stamp.

    Format: `<path-substring> :: <match-substring> :: <justification>`
    Both substrings must match, so an entry cannot silently widen to other files.
    """
    if not ACCEPTED_FILE.exists():
        return []
    out = []
    for line in ACCEPTED_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("::")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            # '*' = every finding in this file (used for manifest-excluded files,
            # where the whole file is out of scope rather than one match).
            out.append((parts[0], "" if parts[1] == "*" else parts[1]))
    return out


def load_patterns(path: Path) -> list[tuple[str, re.Pattern]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append((line, re.compile(line, re.IGNORECASE)))
    return out


def iter_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        target = Path(p)
        if not target.is_absolute():
            target = ROOT / target
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            files.extend(f for f in sorted(target.rglob("*")) if f.is_file())
    files = [f for f in files if f.resolve() not in SELF_EXEMPT
             and ".git/" not in str(f) and "__pycache__" not in str(f)]
    return _drop_gitignored(files)


def _drop_gitignored(files: list[Path]) -> list[Path]:
    """Skip files git ignores — they cannot reach a public export.

    Without this, --preflight flags `seeds/private-ray/` on every run: a tree
    that is gitignored, .dockerignore-excluded, and absent from CI checkouts,
    i.e. structurally unable to ship. Recurring findings that are always benign
    train the reader to skim past the list, which is how a real one gets missed.
    The count is still reported so the skip is visible, never silent.
    """
    if not files:
        return files
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            input="\n".join(str(f) for f in files),
            capture_output=True, text=True, cwd=ROOT, timeout=30,
        )
    except Exception:
        return files          # git unavailable (e.g. a WS-7 staging copy) — scan everything
    ignored = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    if ignored:
        print(f"leak gate: skipping {len(ignored)} gitignored file(s) — cannot reach an export")
    return [f for f in files if str(f) not in ignored]


def extract_text(path: Path) -> str:
    """Readable text for scanning. Excel/zip members are decompressed first."""
    suffix = path.suffix.lower()
    if suffix in ZIP_SUFFIXES:
        chunks: list[str] = []
        try:
            with zipfile.ZipFile(path) as zf:
                for member in zf.namelist():
                    chunks.append(member)
                    if member.endswith((".xml", ".rels", ".txt")):
                        try:
                            chunks.append(zf.read(member).decode("utf-8", "replace"))
                        except Exception:
                            pass
        except zipfile.BadZipFile:
            return ""
        return "\n".join(chunks)
    if suffix in TEXT_SUFFIXES or suffix == "":
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    return ""


def scan(paths: list[str], strict: bool) -> int:
    patterns = load_patterns(PATTERNS_FILE)
    files = iter_files(paths)
    findings: list[str] = []

    for f in files:
        rel = f.relative_to(ROOT) if str(f).startswith(str(ROOT)) else f
        text = extract_text(f)

        for raw, rx in patterns:
            m = rx.search(text)
            if m:
                line_no = text[: m.start()].count("\n") + 1
                findings.append(
                    f"{rel}:{line_no}: matches forbidden pattern /{raw}/ "
                    f"(matched {m.group(0)[:40]!r})"
                )

        size = f.stat().st_size
        if size > MAX_FILE_BYTES:
            findings.append(f"{rel}: {size:,} bytes exceeds the {MAX_FILE_BYTES:,} limit")

        amounts = _suspicious_amounts(text)
        if amounts:
            # Report the true count alongside a capped sample: truncating silently
            # hid 18 of 24 findings in one api-spec, so the reader believed the
            # file was nearly clean when it was the worst offender in the set.
            shown = ", ".join(amounts[:6])
            more = f" (+{len(amounts) - 6} more)" if len(amounts) > 6 else ""
            findings.append(
                f"{rel}: {len(amounts)} large non-round currency amount(s) — real "
                f"data or illustrative? {shown}{more}"
            )

        funds = _foreign_fund_codes(text)
        if funds:
            findings.append(
                f"{rel}: {len(funds)} fund code(s) not in the persona catalog — "
                f"presumed real: {', '.join(funds[:6])}"
            )

        blob = B64_RE.search(text)
        if blob:
            try:
                base64.b64decode(blob.group(0)[:400] + "==", validate=False)
                findings.append(
                    f"{rel}: embedded base64 blob of {len(blob.group(0)):,} chars "
                    f"(avatar/binary smuggled into a text file?)"
                )
            except Exception:
                pass

    if strict:
        # docs/playbooks/new-repo-kit/templates/ ships HANDOVER.template.md and
        # known-issues.template.md ON PURPOSE — blank templates for a NEW repo
        # to fill in, not the owner's real files. WS-7 finding 2026-08-17: a
        # bare substring match flagged them as "private-only" false positives.
        TEMPLATE_DIR = "docs/playbooks/new-repo-kit/templates/"
        for f in files:
            rel = str(f)
            if TEMPLATE_DIR in rel:
                continue
            if "docs/archive/" in rel or "HANDOVER" in rel or "known-issues" in rel:
                findings.append(f"{rel}: private-only file present in an export tree")

    accepted = load_accepted()
    if accepted:
        kept, dropped = [], 0
        for f in findings:
            if any(p in f and m in f for p, m in accepted):
                dropped += 1
            else:
                kept.append(f)
        findings = kept
        if dropped:
            print(f"leak gate: {dropped} finding(s) previously triaged as safe "
                  f"(see {ACCEPTED_FILE.name}) — reviewed, not ignored")

    print(f"leak gate: scanned {len(files)} file(s) against {len(patterns)} pattern(s)")
    if findings:
        print(f"\nFAIL — {len(findings)} finding(s):\n")
        for line in findings:
            print(f"  {line}")
        return 1
    print("PASS — no owner-identifying data found")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paths", nargs="*", default=None,
                    help="files/dirs to scan (default: the OSR public-artifact set)")
    ap.add_argument("--preflight", action="store_true",
                    help="also scan the real ship surface (config/, src/, .github/, …); "
                         "expected RED until WS-4 splits the owner's live config out")
    ap.add_argument("--strict", action="store_true",
                    help="WS-7 staging mode: also reject private-only files")
    args = ap.parse_args()
    paths = args.paths or (PREFLIGHT_PATHS if args.preflight else DEFAULT_PATHS)
    return scan(paths, args.strict)


if __name__ == "__main__":
    sys.exit(main())
