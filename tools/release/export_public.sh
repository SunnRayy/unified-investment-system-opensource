#!/usr/bin/env bash
# OSR WS-7 — assemble the public export as a physically separate tree.
#
# Copy-tree, NOT git-history-based: nothing here reads git blob history, so
# the staging tree has zero history leakage by construction. This script
# does NOT create a remote and does NOT push anywhere — that is explicitly
# Ray's action, not this script's.
#
# What it does, in order:
#   1. Read tools/release/export_manifest.txt (an allowlist + a small
#      subtractive exclude list).
#   2. Copy every included path's TRACKED-OR-UNTRACKED-BUT-NOT-GITIGNORED
#      files (via `git ls-files --cached --others --exclude-standard`) into
#      a fresh staging directory — this is what keeps stray local cruft
#      (__pycache__, .DS_Store, node_modules, .env.local, generated output)
#      out without reimplementing gitignore parsing.
#   3. Delete the manifest's EXCLUDE paths from the staging copy.
#   4. Replace src/database/mapping_seeds.py with the persona-safe
#      tools/release/mapping_seeds.public.py twin.
#   5. Run tools/release/patch_staging.py — the small set of staging-only
#      test/frontend patches that only make sense once mapping_seeds.py is
#      the persona twin (see that script's own docstring).
#   6. git init + one "Initial public release" commit in the staging tree.
#   7. Run leak_gate.py --strict against the staging tree. Non-zero exit
#      propagates — this script does not swallow a failed gate.
#
# Test-suite and quickstart verification are deliberately NOT run by this
# script (they're slow and belong in CI later) — run them by hand against
# the printed staging path after this script exits 0.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
MANIFEST="$HERE/export_manifest.txt"

STAGING="${1:-}"
if [ -z "$STAGING" ]; then
    STAGING="$(mktemp -d "${TMPDIR:-/tmp}/uis-osr-export.XXXXXX")"
fi
mkdir -p "$STAGING"

echo "[export_public] staging tree: $STAGING"
cd "$ROOT"

# ── 1+2. Copy included paths ────────────────────────────────────────────
includes=()
excludes=()
while IFS= read -r line; do
    line="${line%%$'\r'}"
    [ -z "$line" ] && continue
    case "$line" in
        \#*) continue ;;
        EXCLUDE\ *) excludes+=("${line#EXCLUDE }") ;;
        *) includes+=("$line") ;;
    esac
done < "$MANIFEST"

for path in "${includes[@]}"; do
    if [ -f "$path" ]; then
        mkdir -p "$STAGING/$(dirname "$path")"
        cp "$path" "$STAGING/$path"
    elif [ -d "$path" ]; then
        # List tracked + untracked-but-not-gitignored files under this dir,
        # then copy each preserving its relative path.
        while IFS= read -r f; do
            [ -z "$f" ] && continue
            mkdir -p "$STAGING/$(dirname "$f")"
            cp "$f" "$STAGING/$f"
        done < <(git ls-files --cached --others --exclude-standard -- "$path")
    else
        echo "[export_public] WARNING: manifest path not found, skipping: $path" >&2
    fi
done

# ── 3. Subtractive excludes ──────────────────────────────────────────────
for path in "${excludes[@]}"; do
    rm -rf "${STAGING:?}/${path:?}"
done

# ── 4. mapping_seeds.py persona swap ────────────────────────────────────
cp "$HERE/mapping_seeds.public.py" "$STAGING/src/database/mapping_seeds.py"

# ── 5. Staging-only test/frontend patches ───────────────────────────────
"$ROOT/.venv/bin/python" "$HERE/patch_staging.py" "$STAGING"

# ── 6. Fresh git history — one commit, no remote ────────────────────────
(
    cd "$STAGING"
    git init -q
    git add -A
    git -c user.name="Huinsight Release" -c user.email="release@localhost" \
        commit -q -m "Initial public release"
)

file_count=$(find "$STAGING" -type f -not -path '*/.git/*' | wc -l | tr -d ' ')
echo "[export_public] staging file count: $file_count"

# ── 7. Leak gate, strict mode ────────────────────────────────────────────
echo "[export_public] running leak_gate.py --strict ..."
"$ROOT/.venv/bin/python" "$HERE/leak_gate.py" --paths "$STAGING" --strict

echo "[export_public] done. Staging tree: $STAGING"
