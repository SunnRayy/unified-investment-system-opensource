#!/usr/bin/env bash
# check-doc-freshness.sh — prevents the "same value drifts across docs" failure.
# Asserts that values stated in prose docs match their CANONICAL source in code.
# Exit 0 = fresh, 1 = drift detected. Called by verify.sh check [f].
#
# Add one assert_match block per canonical value you care about. The cautionary tale: an integrity
# count was hard-coded as 12 / 14 / 15 across four docs while the code said something else.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
drift=0

# assert_match <human-label> <canonical-value> <file:regex-with-one-capture-group> ...
assert_match() {
  local label="$1" canonical="$2"; shift 2
  for spec in "$@"; do
    local file="${spec%%:*}" rx="${spec#*:}"
    [ -f "$file" ] || continue
    while IFS= read -r found; do
      if [ -n "$found" ] && [ "$found" != "$canonical" ]; then
        echo "✗ doc-freshness: $label = '$found' in $file but canonical is '$canonical'"
        drift=1
      fi
    done < <(grep -oE "$rx" "$file" 2>/dev/null | grep -oE '[0-9]+' || true)
  done
}

# ── EXAMPLE (replace with your project's canonical values) ───────────────────────────────────
# Canonical lives in code; grep it once, then assert every doc agrees.
# CANON_CHECKS="$(grep -oE 'INTEGRITY_CHECK_COUNT[^0-9]*([0-9]+)' src/.../gate.py | grep -oE '[0-9]+' | tail -1)"
# assert_match "integrity-check count" "$CANON_CHECKS" \
#   "AGENTS.md:([0-9]+) invariant checks" \
#   "CLAUDE.md:([0-9]+) invariant checks" \
#   "docs/known-issues.md:([0-9]+) checks"
#
# RULE_COUNT="$(grep -cE '^## Rule [0-9]+' AGENTS.md)"
# assert_match "AGENTS rule count" "$RULE_COUNT" "agent-handoff.md:([0-9]+) non-negotiable rules"

if [ "$drift" -ne 0 ]; then
  echo "Fix: generate these values into docs or omit them — never hand-copy a code value into prose."
  exit 1
fi
echo "[ok] doc-freshness: no drift"
exit 0
