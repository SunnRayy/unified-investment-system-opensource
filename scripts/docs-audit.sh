#!/usr/bin/env bash
# docs-audit: compare code reality to documentation and output a drift report
# Usage: bash scripts/docs-audit.sh
# Exit 0 = clean, Exit 1 = drift found

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

BOLD='\033[1m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

DRIFT=0

section() { echo -e "\n${CYAN}${BOLD}=== $1 ===${NC}"; }
warn()    { echo -e "  ${YELLOW}DRIFT${NC}  $1"; ((++DRIFT)); }
ok()      { echo -e "  ${GREEN}OK${NC}     $1"; }
info()    { echo -e "         $1"; }

ver_from() {
  # Extract first X.Y.Z from a file; strip leading V
  grep -oE 'V[0-9]+\.[0-9]+\.[0-9]+' "$1" 2>/dev/null | head -1 | sed 's/^V//' || echo "not found"
}

# ── 1. VERSION NUMBERS ──────────────────────────────────────────────────────

section "1. VERSION NUMBERS"

CHANGELOG_VER=$(grep -m1 '^\#\# \[[0-9]' CHANGELOG.md 2>/dev/null \
  | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "not found")
README_VER=$(ver_from README.md)
CLAUDE_VER=$(grep "^\*\*Version\*\*:" CLAUDE.md 2>/dev/null \
  | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "not found")
STATUS_VER=$(ver_from docs/project-status.md)
LAYOUT_VER=$(ver_from ux-command-center/components/Layout.tsx)

info "CHANGELOG.md latest : ${CHANGELOG_VER}"
info "README.md           : ${README_VER}"
info "CLAUDE.md           : ${CLAUDE_VER}"
info "project-status.md   : ${STATUS_VER}"
info "Layout.tsx sidebar  : ${LAYOUT_VER}"

ALL_VERS=("$CHANGELOG_VER" "$README_VER" "$CLAUDE_VER" "$STATUS_VER" "$LAYOUT_VER")
UNIQUE_VERS=$(printf '%s\n' "${ALL_VERS[@]}" | grep -v "not found" | sort -u | wc -l | tr -d ' ')
if [[ "$UNIQUE_VERS" -gt 1 ]]; then
  VERS_LIST=$(printf '%s\n' "${ALL_VERS[@]}" | grep -v "not found" | sort -u | tr '\n' ' ')
  warn "Version mismatch across files — ${VERS_LIST}"
else
  ok "Version references consistent (${CHANGELOG_VER})"
fi

# ── 2. API ROUTES ────────────────────────────────────────────────────────────

section "2. API ROUTES"

ROUTE_MODULES=$(ls src/api/routes/*.py 2>/dev/null | grep -v "__init__" \
  | xargs -I{} basename {} .py | sort || true)
ROUTE_MODULE_COUNT=$(printf '%s\n' "$ROUTE_MODULES" | grep -c "." 2>/dev/null || echo "0")
ROUTE_COUNT=$(grep -rEh '@router\.(get|post|put|delete|patch)' src/api/routes/ 2>/dev/null \
  | wc -l | tr -d ' ' || echo "0")
info "Route modules: $ROUTE_MODULE_COUNT | Individual routes: $ROUTE_COUNT"

UNSPECCED=0
while IFS= read -r module; do
  [[ -z "$module" ]] && continue
  # Check if any spec file mentions this module name
  if ! grep -qriF "$module" docs/api-specs/ 2>/dev/null; then
    warn "Route module $module has no entry in docs/api-specs/"
    ((++UNSPECCED))
  fi
done <<< "$ROUTE_MODULES"

[[ "$UNSPECCED" -eq 0 ]] && ok "All $ROUTE_MODULE_COUNT route modules covered in docs/api-specs/"

# ── 3. CLI FLAGS ─────────────────────────────────────────────────────────────

section "3. CLI FLAGS"

CODE_FLAGS=$(grep -E "add_argument\('--" main.py 2>/dev/null \
  | grep -oE "\-\-[a-z][a-z-]+" | sort -u || true)
FLAG_COUNT=$(printf '%s\n' "$CODE_FLAGS" | grep -c "\-\-" 2>/dev/null || echo "0")
info "CLI flags in main.py: $FLAG_COUNT"

UNDOC_FLAGS=0
while IFS= read -r flag; do
  [[ -z "$flag" ]] && continue
  if ! grep -qF -- "$flag" CLAUDE.md README.md 2>/dev/null; then
    warn "CLI flag $flag — in main.py but not documented in CLAUDE.md or README.md"
    ((++UNDOC_FLAGS))
  fi
done <<< "$CODE_FLAGS"

[[ "$UNDOC_FLAGS" -eq 0 ]] && ok "All $FLAG_COUNT CLI flags documented"

# ── 4. ENVIRONMENT VARIABLES ─────────────────────────────────────────────────

section "4. ENVIRONMENT VARIABLES"

# Use perl for reliable env var extraction (avoids BSD grep -P limitation)
CODE_ENVVARS=$(grep -rh "os\.environ\|os\.getenv\|environ\.get" src/ main.py 2>/dev/null \
  | perl -ne 'while (/["\x27]([A-Z][A-Z0-9_]{2,})["\x27]/g) { print "$1\n" }' | sort -u || true)
ENVVAR_COUNT=$(printf '%s\n' "$CODE_ENVVARS" | grep -c "[A-Z]" 2>/dev/null || echo "0")
info "Env vars in source code: $ENVVAR_COUNT"

UNDOC_VARS=0
while IFS= read -r var; do
  [[ -z "$var" ]] && continue
  if ! grep -qF "$var" CLAUDE.md README.md .env.example 2>/dev/null; then
    warn "Env var $var — used in code but not documented"
    ((++UNDOC_VARS))
  fi
done <<< "$CODE_ENVVARS"

[[ "$UNDOC_VARS" -eq 0 ]] && ok "All $ENVVAR_COUNT env vars documented"

# ── 5. DATA SOURCE READERS ───────────────────────────────────────────────────

section "5. DATA SOURCE READERS"

SOURCE_FILES=$(ls src/sources/*_reader.py 2>/dev/null \
  | xargs -I{} basename {} .py | sort || true)
SOURCE_COUNT=$(printf '%s\n' "$SOURCE_FILES" | grep -c "." 2>/dev/null || echo "0")
info "Reader modules in src/sources/: $SOURCE_COUNT"
printf '%s\n' "$SOURCE_FILES" | sed 's/^/           /'

CLAUDE_SOURCE_COUNT=$(grep -cE '\| .+ \| (Real-time|Derived|Historical)' CLAUDE.md 2>/dev/null || echo "0")
info "Sources documented in CLAUDE.md: $CLAUDE_SOURCE_COUNT"

if [[ "$SOURCE_COUNT" -ne "$CLAUDE_SOURCE_COUNT" ]]; then
  warn "Reader count mismatch — code: $SOURCE_COUNT, CLAUDE.md table: $CLAUDE_SOURCE_COUNT"
else
  ok "Reader count consistent ($SOURCE_COUNT)"
fi

# ── 6. INTEGRITY CHECK COUNT ─────────────────────────────────────────────────

section "6. INTEGRITY CHECKS"

GATE_COUNT=$(grep -c "def _check_" src/validation/data_integrity_gate.py 2>/dev/null || echo "0")
info "Check functions in data_integrity_gate.py: $GATE_COUNT"

# Extract a documented count that precedes "invariant" or "check" or "integrity"
DOC_COUNT=$(grep -Eoh '[0-9]+.{0,4}(invariant|integrity check|self-derived)' \
  CLAUDE.md README.md AGENTS.md 2>/dev/null \
  | grep -oE '^[0-9]+' | head -1 || echo "unknown")
info "Documented count (CLAUDE.md/README/AGENTS.md): $DOC_COUNT"

if [[ "$DOC_COUNT" != "unknown" && "$GATE_COUNT" != "$DOC_COUNT" ]]; then
  warn "Integrity check count drift — code: $GATE_COUNT, docs: $DOC_COUNT"
else
  ok "Integrity check count consistent ($GATE_COUNT)"
fi

# ── SUMMARY ──────────────────────────────────────────────────────────────────

section "SUMMARY"

if [[ "$DRIFT" -eq 0 ]]; then
  echo -e "${GREEN}${BOLD}  No drift detected. Docs are in sync with code.${NC}"
  exit 0
else
  echo -e "${RED}${BOLD}  $DRIFT drift item(s) found. Update docs to match code reality.${NC}"
  exit 1
fi
