#!/usr/bin/env bash
# verify.sh — static pre-commit gate with a TYPED EXIT-CODE CONTRACT.
#   0 = clean              1 = safety violation (STOP, do not commit)
#   2 = logic violation    3 = quality only (may commit with a noted justification)
#
# Philosophy: convert AGENTS.md rules into machine-checked gates. Each check greps for a banned
# pattern, EXCLUDING entries listed in scripts/.baseline-*.txt (ratcheting baselines) so you can
# adopt this on an existing/messy repo and block only NEW violations.
#
# Specialize the patterns/paths (marked {{...}}) to your stack.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
APP_SRC="{{src}}"          # application source dir
WEB_SRC="{{web/src}}"      # frontend source dir (optional)
MAX_FILE_LINES="${MAX_FILE_LINES:-400}"

worst=0
note() { echo "  $1"; }
fail() { local code="$1"; shift; echo "[FAIL exit $code] $*"; (( code > worst )) && worst="$code"; }
pass() { echo "[ok] $*"; }

# Helper: grep that drops baselined lines. Usage: filtered_grep <baseline-file> <ripgrep-args...>
filtered_grep() {
  local baseline="$1"; shift
  local hits; hits="$(grep -rnE "$@" 2>/dev/null || true)"
  [ -f "$baseline" ] && hits="$(echo "$hits" | grep -vFf <(grep -v '^#' "$baseline") || true)"
  echo "$hits" | sed '/^$/d'
}

# ── [a] SAFETY (exit 1): destructive DDL / unguarded prod client in app or tests ──────────────
hits="$(filtered_grep scripts/.baseline-safety.txt 'DROP TABLE|TRUNCATE|CREATE OR REPLACE TABLE' "$APP_SRC" tests 2>/dev/null)"
if [ -n "$hits" ]; then fail 1 "[a] destructive DDL outside migrations"; note "$hits"; else pass "[a] no destructive DDL"; fi

# ── [b] LOGIC (exit 2): silent failure — return [] / {} on exception ──────────────────────────
hits="$(filtered_grep scripts/.baseline-silent.txt 'except[^:]*:\s*$' "$APP_SRC" 2>/dev/null | grep -i 'return \[\]\|return {}' || true)"
if [ -n "$hits" ]; then fail 2 "[b] possible silent failure (return []/{} on except)"; note "$hits"; else pass "[b] no obvious silent failures"; fi

# ── [c] LOGIC (exit 2): print/console.log in app code (use a logger) ──────────────────────────
hits="$(filtered_grep scripts/.baseline-print.txt '(^|[^.\w])(print|console\.log)\s*\(' "$APP_SRC" "$WEB_SRC" 2>/dev/null)"
if [ -n "$hits" ]; then fail 2 "[c] print/console.log in app code"; note "$hits"; else pass "[c] no stray print/console.log"; fi

# ── [d] LOGIC (exit 2): raw datastore/http client bypassing the wrapper ───────────────────────
hits="$(filtered_grep scripts/.baseline-rawclient.txt '{{duckdb\.connect|requests\.get|fetch\(}}' "$APP_SRC" "$WEB_SRC" 2>/dev/null)"
if [ -n "$hits" ]; then fail 2 "[d] raw client outside the designated wrapper"; note "$hits"; else pass "[d] no raw clients"; fi

# ── [e] QUALITY (exit 3): files over the line budget ──────────────────────────────────────────
big=""
while IFS= read -r f; do
  grep -qxF "$f" scripts/.baseline-large-files.txt 2>/dev/null && continue
  lines="$(wc -l < "$f")"; (( lines > MAX_FILE_LINES )) && big="$big\n  $f ($lines)"
done < <(git ls-files "$APP_SRC" "$WEB_SRC" 2>/dev/null | grep -E '\.(py|ts|tsx|js|jsx|go|rs)$')
if [ -n "$big" ]; then fail 3 "[e] files over $MAX_FILE_LINES lines"; echo -e "$big"; else pass "[e] no oversized files"; fi

# ── [f] QUALITY (exit 3): doc freshness — claimed counts vs canonical code constants ──────────
if [ -x scripts/check-doc-freshness.sh ]; then
  if ! scripts/check-doc-freshness.sh; then fail 3 "[f] doc-freshness drift"; fi
else note "[f] skipped (check-doc-freshness.sh not installed)"; fi

# ── result ────────────────────────────────────────────────────────────────────────────────────
echo "----------------------------------------"
case "$worst" in
  0) echo "verify.sh: CLEAN (exit 0)";;
  1) echo "verify.sh: SAFETY VIOLATION (exit 1) — STOP, do not commit";;
  2) echo "verify.sh: LOGIC VIOLATION (exit 2) — fix before committing";;
  3) echo "verify.sh: QUALITY ONLY (exit 3) — may commit with justification";;
esac
exit "$worst"
