#!/usr/bin/env bash
# =============================================================================
# Huinsight Pre-Commit Verification Suite
# Catches the project's actual, documented failure patterns before code reaches
# the DB or production. Run this before every commit.
#
# EXIT CODES:
#   0  All checks pass
#   1  P0: DB safety violation (stop everything — risk of wiping production DB)
#   2  Business logic violations (global MAX snapshot, currency hardcodes,
#      LLMClient bypass, UI buttons without handlers)
#   3  Code quality issues (large files, ruff lint)
#
# All checks in priority order. P0 aborts immediately; lower-priority checks
# accumulate and report at the end.
#
# Each check is mapped to known-issues.md. References are noted inline.
# =============================================================================

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO_ROOT"

SCRIPTS_DIR="$REPO_ROOT/scripts"
VENV_RUFF="$REPO_ROOT/.venv/bin/ruff"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

# Accumulated failure flags
HAS_LOGIC_FAILURE=0
HAS_QUALITY_FAILURE=0

# Colour helpers (safe on non-TTY)
if [[ -t 1 ]]; then
  RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; RESET='\033[0m'
else
  RED=''; YELLOW=''; GREEN=''; RESET=''
fi

_pass()  { echo -e "${GREEN}  OK${RESET}  $*"; }
_warn()  { echo -e "${YELLOW}WARN${RESET}  $*"; }
_fail()  { echo -e "${RED}FAIL${RESET}  $*"; }

# ─────────────────────────────────────────────────────────────────────────────
# Helper: compare current grep output against a baseline file.
# Prints NEW violations (lines in current but NOT in baseline).
# Returns 1 if any new violations found, 0 otherwise.
# Usage: _check_baseline "label" <baseline_file> <current_violations_string>
# ─────────────────────────────────────────────────────────────────────────────
_check_baseline() {
  local label="$1"
  local baseline_file="$2"
  local current="$3"

  if [[ ! -f "$baseline_file" ]]; then
    # No baseline exists: treat all current violations as new
    if [[ -n "$current" ]]; then
      _fail "$label — baseline missing, all violations treated as new:"
      echo "$current" | sed 's/^/  /'
      return 1
    fi
    _pass "$label"
    return 0
  fi

  local new_violations
  # comm -13: lines in current that are NOT in baseline (new violations)
  new_violations=$(comm -13 \
    <(sort "$baseline_file") \
    <(echo "$current" | sort) \
  )

  local baseline_count known_count current_count
  baseline_count=$(wc -l < "$baseline_file" | tr -d ' \n\t')
  # Count non-empty lines in $current. grep -c exits 1 on no match, so we must
  # NOT use || on the pipeline — that would append a second "0". Use a subshell
  # with set +e to capture the count cleanly regardless of exit status.
  current_count=$(set +e; printf '%s' "$current" | grep -c '.' 2>/dev/null; true)
  current_count=$(printf '%s' "$current_count" | tr -d ' \n\t')

  if [[ -n "$new_violations" ]]; then
    _fail "$label — NEW violations found (${current_count} current, ${baseline_count} baseline):"
    echo "$new_violations" | sed 's/^/  /'
    return 1
  fi

  # Current count may be ≤ baseline (violations were fixed — good)
  if [[ "$current_count" -lt "$baseline_count" ]]; then
    _pass "$label (${current_count} known; ${baseline_count} in baseline — some fixed, update baseline)"
  else
    _pass "$label (${current_count} known pre-existing, none new)"
  fi
  return 0
}

echo "============================================================"
echo " Huinsight Verify — $(date '+%Y-%m-%d %H:%M')"
echo "============================================================"

# =============================================================================
# CHECK a: P0 DB SAFETY
# Source: AGENTS.md Rule 6 — 2026-02-15 DB wipe incident
# What it catches: tests that open the production DB (data/unified.duckdb)
#                  because DatabaseConnector() defaults to that path.
# Exit 1 immediately if any match found.
# =============================================================================
echo ""
echo "[a] DB Safety (P0)"

P0_FAIL=0

# Pattern 1: DatabaseConnector() with no arguments in test files.
# DatabaseConnector() defaults to data/unified.duckdb (production).
# In tests, always pass a temp path or use ':memory:'.
UNSAFE_CONNECTOR=$(grep -rn 'DatabaseConnector()' tests/ --include='*.py' 2>/dev/null || true)
if [[ -n "$UNSAFE_CONNECTOR" ]]; then
  _fail "DatabaseConnector() with no args in tests/ — will open production DB"
  echo "$UNSAFE_CONNECTOR" | sed 's/^/  /'
  echo "  → Fix: pass tmp_path/'test.duckdb' or ':memory:'. See AGENTS.md Rule 6."
  P0_FAIL=1
fi

# Pattern 2: DROP TABLE or CREATE OR REPLACE TABLE in Python source files.
# Schema mutations belong in schema.sql or connector.py migrations only.
# These DDL statements in .py files have caused table drops during agent sessions.
UNSAFE_DDL=$(grep -rn 'DROP TABLE\|CREATE OR REPLACE TABLE\|TRUNCATE TABLE' \
  src/ tests/ --include='*.py' 2>/dev/null \
  | grep -v 'src/database/connector\.py\|src/database/schema\.sql' \
  | grep -v '^\s*#' \
  || true)
if [[ -n "$UNSAFE_DDL" ]]; then
  _fail "Destructive DDL found outside schema.sql/connector.py"
  echo "$UNSAFE_DDL" | sed 's/^/  /'
  echo "  → Fix: schema changes belong in connector.py migrations or schema.sql. See AGENTS.md Rule 6."
  P0_FAIL=1
fi

# Pattern 3: TestClient(app) with no get_db dependency override anywhere in
# the file — reads whatever DB the running process has configured (locally,
# the owner's real portfolio mirror), not a test fixture. Program OSR
# finding, 2026-08-17: tests/api/test_wealthos_endpoints.py asserted literal
# facts about the owner's real portfolio this way, invisible because it
# passed against his real DB every time — Pattern 1 doesn't catch it since
# no bare DatabaseConnector() call is involved.
# pytest.mark.requires_live_db (pyproject.toml, skipped by default) is a
# deliberate, documented exception — never flagged. Baseline-tracked, not a
# hard fail on every current hit: 24 tests/api/ files share this pattern
# today (most testing DB-content-independent behavior — 404s, validation),
# auditing which are actually risky is separate work. The guard's job here
# is stopping a NEW one from landing unnoticed, same as it already did for
# global-MAX/currency-hardcode/etc below.
UNSAFE_TESTCLIENT=""
for f in $(grep -rl 'TestClient(app)' tests/ --include='*.py' 2>/dev/null || true); do
  grep -q 'requires_live_db' "$f" && continue
  grep -q 'get_db\|dependency_overrides' "$f" || UNSAFE_TESTCLIENT="${UNSAFE_TESTCLIENT}${f}"$'\n'
done
if ! _check_baseline "TestClient(app) with no get_db override" \
     "$SCRIPTS_DIR/.baseline-testclient-no-override.txt" \
     "$UNSAFE_TESTCLIENT"; then
  echo "  → Fix: override get_db to a temp/fixture connector (app.dependency_overrides[get_db] = ...),"
  echo "         or mark @pytest.mark.requires_live_db with a reason if the live DB is genuinely the point."
  P0_FAIL=1
fi

# Pattern 4: a test reading the real config's database.path — connects to
# whatever's actually configured (locally, production), same failure class
# as Pattern 3 via a different route. Same requires_live_db exception,
# same baseline treatment (currently empty — nothing pre-existing).
UNSAFE_LOADCONFIG=""
for f in $(grep -rlE 'config\.get\(.database.\)|config\[.database.\]' tests/ --include='*.py' 2>/dev/null || true); do
  grep -q 'requires_live_db' "$f" && continue
  UNSAFE_LOADCONFIG="${UNSAFE_LOADCONFIG}${f}"$'\n'
done
if ! _check_baseline "Test resolves DB path from real config's database.path" \
     "$SCRIPTS_DIR/.baseline-loadconfig-db-path.txt" \
     "$UNSAFE_LOADCONFIG"; then
  echo "  → Fix: use a temp path or ':memory:' explicitly, or mark @pytest.mark.requires_live_db with a reason."
  P0_FAIL=1
fi

if [[ $P0_FAIL -eq 1 ]]; then
  echo ""
  echo "P0 DB safety check FAILED. All remaining checks skipped."
  echo "Fix DB safety violations before re-running verify.sh."
  exit 1
fi

_pass "DB safety"

# =============================================================================
# CHECK b: GLOBAL MAX(snapshot_date) ANTI-PATTERN
# Source: AGENTS.md Rule 3, known-issues.md
# What it catches: WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM holdings)
#                  This is the exact pattern that drops QDII assets with T+2 lag.
# Safe usage (SELECT asset_id, MAX(snapshot_date) GROUP BY asset_id) is NOT flagged.
# =============================================================================
echo ""
echo "[b] Global MAX(snapshot_date) anti-pattern"

MAX_CURRENT=$(grep -rn 'WHERE snapshot_date = (SELECT MAX(snapshot_date)' \
  src/ tests/ tools/ --include='*.py' 2>/dev/null || true)

if ! _check_baseline "Global MAX(snapshot_date)" \
     "$SCRIPTS_DIR/.baseline-max-snapshot.txt" \
     "$MAX_CURRENT"; then
  echo "  → Fix: use per-asset CTE:"
  echo "         WITH latest AS (SELECT asset_id, MAX(snapshot_date) AS max_date"
  echo "                         FROM holdings WHERE is_shadow=FALSE GROUP BY asset_id)"
  echo "  → See: AGENTS.md Rule 3, known-issues.md §MAX-snapshot"
  HAS_LOGIC_FAILURE=1
fi

# =============================================================================
# CHECK c: HARDCODED CURRENCY CONVERSION CONSTANTS
# Source: AGENTS.md Rule 2, known-issues.md
# What it catches: standalone USD_TO_CNY or usd_to_cny constants, and "USD": 7.0
#                  dict literals — each module defining its own rate causes drift
#                  when the rate needs updating.
# Canonical location: src/data_manager/currency_converter.py
# =============================================================================
echo ""
echo "[c] Hardcoded currency conversion constants"

CURRENCY_CURRENT=$(grep -rn \
  'USD_TO_CNY.*= 7\.\|usd_to_cny = 7\.\|USD_TO_CNY_RATE = 7\.\|"USD": 7\.0' \
  src/ --include='*.py' 2>/dev/null \
  | grep -v 'src/data_manager/currency_converter\.py' \
  | grep -v '^\s*#' \
  || true)

if ! _check_baseline "Currency hardcodes" \
     "$SCRIPTS_DIR/.baseline-currency.txt" \
     "$CURRENCY_CURRENT"; then
  echo "  → Fix: import the rate from src/data_manager/currency_converter.py."
  echo "         Do not define USD_TO_CNY locally in transformer/sync files."
  echo "  → See: AGENTS.md Rule 2, known-issues.md §currency-constants"
  HAS_LOGIC_FAILURE=1
fi

# =============================================================================
# CHECK d: UI BUTTONS WITHOUT HANDLERS
# Source: AGENTS.md Rule 19 — 20+ decorative buttons shipped in Operations Phase 1
# What it catches: <button / <Button JSX elements with no onClick, onMouseDown,
#                  onPointerDown, onKeyDown, type=submit, disabled, form= or
#                  prop spread — anywhere in the opening tag, however many lines
#                  it spans.
# Known pre-existing exceptions: scripts/.baseline-ui-buttons.txt
#
# Rewritten 2026-08-30. The previous inline grep scanned ux-command-center/src/
# only (35 files) and matched single-line tags only, so it reported "all have
# handlers" while the Dashboard CIRCUIT BREAKER badge — a 7-line <button> in
# pages/ with no onClick — sat unflagged for months. Coverage is now src/ +
# pages/ + components/ (131 files) and multi-line aware.
# =============================================================================
echo ""
echo "[d] UI buttons without handlers"

_D_PYTHON="$VENV_PYTHON"
if [[ ! -x "$_D_PYTHON" ]]; then
  command -v python3 &>/dev/null && _D_PYTHON="python3" || _D_PYTHON="python"
fi

if [[ -f "$SCRIPTS_DIR/check_ui_button_handlers.py" ]]; then
  UI_BUTTONS=$("$_D_PYTHON" "$SCRIPTS_DIR/check_ui_button_handlers.py" 2>/dev/null || true)

  if ! _check_baseline "UI buttons" \
       "$SCRIPTS_DIR/.baseline-ui-buttons.txt" \
       "$UI_BUTTONS"; then
    echo "  → Fix: add an onClick handler, or remove the button."
    echo "         Never ship a button as a visual affordance. See AGENTS.md Rule 19."
    echo "         Violations are keyed file:label, not file:line — reformatting"
    echo "         above a button will not produce a phantom NEW violation."
    HAS_LOGIC_FAILURE=1
  fi
else
  _warn "[d] scripts/check_ui_button_handlers.py not found — skipping UI button check"
fi

# =============================================================================
# CHECK e: FILE SIZE (informational — warns on oversized files)
# Source: Project convention — files over 400 lines become hard to review
# Known exception: src/sync/orchestrator.py (2,367 lines — central pipeline,
#                  decomposition deferred)
# Exit 3 only for files NOT in the pre-existing baseline.
# =============================================================================
echo ""
echo "[e] File size check (>400 lines)"

# Build sorted list of current oversized files (excluding orchestrator.py)
LARGE_CURRENT=$(find src/ tests/ ux-command-center/src/ -name '*.py' -o -name '*.ts' -o -name '*.tsx' 2>/dev/null \
  | xargs wc -l 2>/dev/null \
  | awk '$1 > 400 && $2 != "total" {print $2}' \
  | grep -v 'orchestrator\.py' \
  | sort \
  || true)

LARGE_BASELINE="$SCRIPTS_DIR/.baseline-large-files.txt"
if [[ -f "$LARGE_BASELINE" ]]; then
  NEW_LARGE=$(comm -13 <(sort "$LARGE_BASELINE") <(echo "$LARGE_CURRENT" | sort))
  if [[ -n "$NEW_LARGE" ]]; then
    _warn "New files over 400 lines (not in baseline):"
    echo "$NEW_LARGE" | while read -r f; do
      lines=$(wc -l < "$f" 2>/dev/null || echo '?')
      echo "  ${lines} lines: $f"
    done
    echo "  → Consider splitting or update baseline: echo 'path/to/file.py' >> scripts/.baseline-large-files.txt"
    HAS_QUALITY_FAILURE=1
  else
    known_count=$(wc -l < "$LARGE_BASELINE" | tr -d ' ')
    current_count=$(echo "$LARGE_CURRENT" | grep -c '.' 2>/dev/null || echo 0)
    _pass "File size (${current_count} files over 400 lines — all pre-existing; orchestrator.py excepted)"
  fi
else
  # No baseline: just report counts
  count=$(echo "$LARGE_CURRENT" | grep -c '.' 2>/dev/null || echo 0)
  _warn "File size baseline missing — ${count} files over 400 lines. Run: bash scripts/verify.sh --update-baseline"
fi

# =============================================================================
# CHECK f: LLMCLIENT BYPASS
# Source: AGENTS.md Rule 21 — direct litellm calls bypass fallback chain,
#         usage tracking, and model config
# Canonical path: src/services/llm_client.py
# Known baseline: settings.py API key validation endpoint
# =============================================================================
echo ""
echo "[f] LLMClient bypass check"

LLM_CURRENT=$(grep -rn 'import litellm\|litellm\.' \
  src/ --include='*.py' 2>/dev/null \
  | grep -v 'src/services/llm_client\.py' \
  | grep -v '^\s*#' \
  || true)

if ! _check_baseline "LLMClient bypass" \
     "$SCRIPTS_DIR/.baseline-llmclient.txt" \
     "$LLM_CURRENT"; then
  echo "  → Fix: use LLMClient from src/services/llm_client.py instead of calling litellm directly."
  echo "         Direct calls skip the model fallback chain and usage logging."
  echo "  → See: AGENTS.md Rule 21, known-issues.md §llmclient-bypass"
  HAS_LOGIC_FAILURE=1
fi

# =============================================================================
# CHECK g: RUFF LINT + SYNTAX CHECK
# Only catches violations not present in the baseline (pre-existing violations
# are grandfathered in; only NEW violations fail this check).
# Rules: F (pyflakes: unused imports/vars, undefined names) + E (style errors)
# Excludes: E501 (line length), E402 (import order) — cosmetic pre-existing noise
# =============================================================================
echo ""
echo "[g] Ruff lint + syntax"

# py_compile runs unconditionally — catches syntax errors even when ruff is absent.
# Covers src/, tests/, AND root-level *.py files (main.py etc.).
# This is a quality gate (exit 3), not a P0, but must not be skipped.
# Use a worktree-aware Python: fall back to the main project's .venv when the
# worktree has no local venv (same pattern as check [i]).
_PYCOMPILE_BIN="$VENV_PYTHON"
if [[ ! -x "$_PYCOMPILE_BIN" ]]; then
  _PYCOMPILE_BIN="$(cd "$REPO_ROOT/../.." 2>/dev/null && pwd)/.venv/bin/python"
fi
if [[ ! -x "$_PYCOMPILE_BIN" ]]; then
  # Try PATH as last resort
  if command -v python3 &>/dev/null; then
    _PYCOMPILE_BIN="python3"
  elif command -v python &>/dev/null; then
    _PYCOMPILE_BIN="python"
  else
    _fail "REQUIRED TOOL MISSING: python not found in .venv/bin or \$PATH — py_compile syntax check cannot run"
    echo "  → Fix: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    HAS_QUALITY_FAILURE=1
    _PYCOMPILE_BIN=""
  fi
fi
if [[ -n "$_PYCOMPILE_BIN" ]]; then
# Use a writable pycache dir so py_compile never fails due to read-only filesystem
# (worktree environments, CI, Codex sandboxes). PYTHONPYCACHEPREFIX is honoured by
# Python 3.8+ and redirects all __pycache__ writes to the given directory.
_PYC_TMPDIR="/tmp/uis-pycache-$$"
mkdir -p "$_PYC_TMPDIR" 2>/dev/null || _PYC_TMPDIR="/tmp/uis-pycache"
SYNTAX_ERRORS=$(find src/ tests/ -name '*.py' -print0 2>/dev/null | \
  xargs -0 -I{} env PYTHONPYCACHEPREFIX="$_PYC_TMPDIR" "$_PYCOMPILE_BIN" -m py_compile {} 2>&1 | grep -v '^$' || true)
# Also check root-level Python files explicitly
ROOT_PY_ERRORS=$(find "$REPO_ROOT" -maxdepth 1 -name '*.py' -print0 2>/dev/null | \
  xargs -0 -I{} env PYTHONPYCACHEPREFIX="$_PYC_TMPDIR" "$_PYCOMPILE_BIN" -m py_compile {} 2>&1 | grep -v '^$' || true)
ALL_SYNTAX_ERRORS="${SYNTAX_ERRORS}${ROOT_PY_ERRORS}"
if [[ -n "$ALL_SYNTAX_ERRORS" ]]; then
  _fail "Python syntax errors (py_compile):"
  echo "$ALL_SYNTAX_ERRORS" | sed 's/^/  /'
  HAS_QUALITY_FAILURE=1
else
  _pass "py_compile syntax (src/, tests/, root *.py)"
fi
rm -rf "$_PYC_TMPDIR" 2>/dev/null || true
fi  # end: if _PYCOMPILE_BIN available

# Resolve ruff: prefer .venv/bin/ruff, then PATH, else fail loud (required tool).
_RUFF_BIN="$VENV_RUFF"
if [[ ! -x "$_RUFF_BIN" ]]; then
  # Try PATH
  if command -v ruff &>/dev/null; then
    _RUFF_BIN="$(command -v ruff)"
  else
    _fail "REQUIRED TOOL MISSING: ruff not found in $VENV_RUFF or \$PATH — lint check cannot run"
    echo "  → Fix: .venv/bin/pip install ruff" >&2
    HAS_QUALITY_FAILURE=1
    _RUFF_BIN=""
  fi
fi
if [[ -n "$_RUFF_BIN" ]]; then
  RUFF_BASELINE="$SCRIPTS_DIR/.baseline-ruff.txt"
  # FA102: PEP604 `X | Y` annotations without `from __future__ import annotations`.
  # Local venv is Python 3.9, cloud container is 3.11 — this catches the class of
  # break that hit the V7.0.0 deploy (gcs.py `int | None` passed in cloud, failed locally).
  RUFF_CURRENT=$("$_RUFF_BIN" check src/ tests/ \
    --select F,E,FA102 \
    --ignore E501,E402,E401 \
    --output-format=concise \
    2>/dev/null | grep -E '^src/|^tests/' | sort || true)

  if ! _check_baseline "Ruff lint" "$RUFF_BASELINE" "$RUFF_CURRENT"; then
    echo "  → Fix: resolve the new ruff violations listed above."
    echo "         Pre-existing violations are baselined and do not block commits."
    echo "         To fix all pre-existing violations: .venv/bin/ruff check src/ tests/ --fix"
    HAS_QUALITY_FAILURE=1
  fi
fi

# =============================================================================
# CHECK h: RAW FETCH() CALLS IN FRONTEND (Cloud auth boundary)
# Source: Plan docs/plans/2026-05-05-cloud-deploy-main-features-merge.md Phase 4.1
# What it catches: direct fetch() calls in ux-command-center/src/ that bypass
#                  authFetch — on Cloud Run these silently fail with 401.
# Safe patterns: authFetch(...), createAuthSSE(...) are always OK.
# =============================================================================
if [[ -d "ux-command-center/src" ]]; then
  echo ""
  echo "[h] Raw fetch() audit (cloud auth)"
  RAW_FETCH=$(grep -rEn "(^|[^a-zA-Z_])fetch[[:space:]]*\(" ux-command-center/src/ \
    | grep -v "authFetch\|createAuthSSE\|node_modules" || true)

  if ! _check_baseline "Raw fetch() audit" \
       "$SCRIPTS_DIR/.baseline-raw-fetch.txt" \
       "$RAW_FETCH"; then
    echo "  → Fix: replace fetch(...) with authFetch(...) from src/services/authFetch.ts"
    echo "         On Cloud Run, raw fetch() silently fails with 401. See AGENTS.md Rule 21."
    HAS_LOGIC_FAILURE=1
  fi
fi

# =============================================================================
# CHECK i: CANONICAL COUNT/VERSION DRIFT
# Source: Pass 1 agent-trust layer — single-source-of-truth discipline
# What it catches: docs/code claiming a different integrity-check count or app
#                  version than the canonical sources.
# Canonical sources:
#   count  → INTEGRITY_CHECK_COUNT in src/validation/data_integrity_gate.py
#   version → repo-root VERSION file
#   rules  → number of "^### Rule N" headings in AGENTS.md
# Excludes: docs/archive/**, CHANGELOG.md, HANDOVER-previous.md, and lines
#           that are clearly historical quotations (grep -v "was\|used to\|previously").
# Exit 2 on any mismatch (logic violation — same class as Rule 12/Rule 7 traps).
# =============================================================================
echo ""
echo "[i] Canonical count/version drift check"

_DRIFT_FAIL=0

# Derive canonical values STATICALLY from their single sources — by PARSING files,
# never by importing the app. This makes the drift check run everywhere (CI lint
# stage, fresh clone, git worktree) instead of silently skipping whenever the Python
# deps (duckdb etc.) or the venv are absent — which is exactly where drift slips in.
#   count   → number of ("name", fn) tuples in the INTEGRITY_CHECKS registry
#   version → repo-root VERSION file
#   rules   → number of "## Rule N" headings in AGENTS.md
_CANON_COUNT=$(awk '/INTEGRITY_CHECKS[^=]*=[[:space:]]*\[/{f=1;next} f&&/^[[:space:]]*\("/{c++} f&&/^\]/{print c;exit}' "$REPO_ROOT/src/validation/data_integrity_gate.py" 2>/dev/null)
[[ -z "$_CANON_COUNT" ]] && _CANON_COUNT="ERROR"
_CANON_VERSION=$(tr -d '[:space:]' < "$REPO_ROOT/VERSION" 2>/dev/null || echo "ERROR")
_CANON_RULES=$(grep -cE "^## Rule [0-9]+" AGENTS.md 2>/dev/null || echo "ERROR")

if [[ "$_CANON_COUNT" == "ERROR" || "$_CANON_VERSION" == "ERROR" || "$_CANON_RULES" == "ERROR" ]]; then
  _warn "Could not derive one or more canonical values — skipping drift check (python3/VERSION/AGENTS.md missing?)"
else
  # Target files to check — Pass 1 + Pass C reconciled files.
  # Expand this list whenever a new file is reconciled to the canonical count.
  TARGET_FILES=(
    "CLAUDE.md"
    "README.md"
    "AGENTS.md"
    "docs/architecture/data-pipeline-v4.md"
    "src/validation/data_integrity_gate.py"
    "ux-command-center/src/version.ts"
    "agent-handoff.md"
    "ux-command-center/README.md"
    "docs/project-status.md"
  )
  # NOTE: .claude/skills/*.md are covered by sub-check (a) below, which globs the whole
  # directory — do not list individual skill files here. Three flat-path entries
  # (.claude/skills/<name>.md, .agent/skills/...) were silently dead for months because the
  # skills live at <name>/SKILL.md; the [[ -f ]] guard skipped them without complaint.

  # ── integrity check count ──
  # Look for the numeric count adjacent to "invariant" or "integrity" keywords.
  # Exclude: archive/, CHANGELOG, HANDOVER-previous, historical/quoted references.
  for _FILE in "${TARGET_FILES[@]}"; do
    [[ -f "$_FILE" ]] || continue
    # grep for bare numbers adjacent to the keywords; filter out the canonical-source line itself
    _BAD=$(grep -nE "[0-9]+ (self-derived |invariant )?checks?|[0-9]+ invariant|Runs [0-9]+ |Run [0-9]+ |runs [0-9]+ " "$_FILE" \
      | grep -vE "INTEGRITY_CHECK_COUNT|INTEGRITY_CHECKS|len\(|# historically|was .check|historically" \
      | grep -vE "\b${_CANON_COUNT}\b" \
      | grep -vE "^.*CHANGELOG|^.*archive/" \
      | grep -vE "[0-9]+→[0-9]+|~~|\| V[0-9]+\.| Final Audit |→ [0-9]+ self-derived" \
      || true)
    if [[ -n "$_BAD" ]]; then
      _fail "Count drift in $_FILE — expected ${_CANON_COUNT}, found:"
      echo "$_BAD" | sed 's/^/    /'
      _DRIFT_FAIL=1
    fi
  done
  # Also catch N/N PASS ratio-badge format (e.g. "14/14 PASS" in README/AGENTS.md)
  # Excludes: CHANGELOG, archive/, docs/project-status.md (historical log),
  #           and lowercase "passing" (pytest output in history entries — different meaning).
  _BAD_RATIO=$(grep -rn "[0-9][0-9]*/[0-9][0-9]* PASS" \
      "${TARGET_FILES[@]}" 2>/dev/null \
    | grep -vE "^[^:]*CHANGELOG|^[^:]*archive/" \
    | grep -vE "^docs/project-status\.md:" \
    | grep -vE "${_CANON_COUNT}/${_CANON_COUNT}" \
    || true)
  if [[ -n "$_BAD_RATIO" ]]; then
      _fail "Integrity count ratio-format drift (N/N PASS) with wrong count"
      echo "$_BAD_RATIO" | sed 's/^/  /'
      _DRIFT_FAIL=1
  fi
  [[ $_DRIFT_FAIL -eq 0 ]] && _pass "Integrity count consistent (${_CANON_COUNT}) across target files"

  # ── version string ──
  # Check frontend version.ts exports the canonical version
  _VTS_VER=$(grep -oE "APP_VERSION = '[0-9]+\.[0-9]+\.[0-9]+'" ux-command-center/src/version.ts 2>/dev/null | grep -oE "[0-9]+\.[0-9]+\.[0-9]+" || echo "MISSING")
  if [[ "$_VTS_VER" != "$_CANON_VERSION" ]]; then
    _fail "Version mismatch: VERSION=${_CANON_VERSION}, version.ts=${_VTS_VER}"
    _DRIFT_FAIL=1
  else
    _pass "Version consistent (${_CANON_VERSION}): VERSION file ↔ version.ts"
  fi

  # ── AGENTS.md rule count ──
  # Docs that claim "<N> ... rules" must match the canonical heading count.
  # Excludes "Rules 1–N" ranges and historical quotations.
  _RULE_DRIFT=0
  for _FILE in "agent-handoff.md" "CLAUDE.md" "README.md"; do
    [[ -f "$_FILE" ]] || continue
    _BADR=$(grep -nE "[0-9]+ (non-negotiable )?rules" "$_FILE" \
      | grep -vE "Rules 1|historically|was [0-9]|previously" \
      | grep -vE "\b${_CANON_RULES} (non-negotiable )?rules\b" || true)
    if [[ -n "$_BADR" ]]; then
      _fail "Rule-count drift in $_FILE — expected ${_CANON_RULES}, found:"
      echo "$_BADR" | sed 's/^/    /'
      _DRIFT_FAIL=1; _RULE_DRIFT=1
    fi
  done
  [[ $_RULE_DRIFT -eq 0 ]] && _pass "AGENTS.md rule count consistent (${_CANON_RULES})"

  # ── sub-check a: integrity count drift in .claude/skills/*.md ──
  # Conservative: only match near integrity/invariant/check keywords.
  # Pattern 1: "<N> (invariant )?checks?" — number directly preceding "checks"
  # Pattern 2: "/<N>" — slash-count in a line that also contains integrity/check keywords
  if [[ -d ".claude/skills" ]]; then
    _SKILLS_DRIFT_RAW=$(
      {
        grep -rniE "[0-9]+[[:space:]]+(invariant[[:space:]]+)?checks?" \
          .claude/skills/ --include='*.md' 2>/dev/null \
          | grep -v "HANDOVER-auto" \
          | grep -vE "(^[^:]+:.*[^0-9]|^[^:]+:.*^)${_CANON_COUNT}[[:space:]]+(invariant[[:space:]]+)?[Cc]hecks?" \
          | grep -vE ":(.*[^0-9])?${_CANON_COUNT}[[:space:]]+(invariant[[:space:]]+)?[Cc]hecks?" \
          || true
        grep -rnE "/[0-9]+" \
          .claude/skills/ --include='*.md' 2>/dev/null \
          | grep -iE "integrit|invariant|[Cc]heck" \
          | grep -v "HANDOVER-auto" \
          | grep -vE "/${_CANON_COUNT}([^0-9]|$)" \
          || true
      } | sort -u
    )
    if [[ -n "$_SKILLS_DRIFT_RAW" ]]; then
      _fail "Integrity count drift in .claude/skills/ — expected ${_CANON_COUNT}, found:"
      echo "$_SKILLS_DRIFT_RAW" | sed 's/^/    /'
      _DRIFT_FAIL=1
    else
      _pass "Integrity count consistent in .claude/skills/ (${_CANON_COUNT})"
    fi
  fi

  # ── sub-check b: CLAUDE.md version vs Layout.tsx (via version.ts) ──
  # CLAUDE.md "Current Status" block carries the canonical project version in
  # "**Version**: V<semver>"; Layout.tsx displays APP_VERSION_DISPLAY from
  # version.ts. Both must agree or the sidebar shows a stale version.
  _CLAUDE_MD_VER=$(grep -oE '\*\*Version\*\*: V[0-9]+\.[0-9]+\.[0-9]+' CLAUDE.md 2>/dev/null \
    | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "MISSING")
  _LAYOUT_VER=$(grep -oE "APP_VERSION = '[0-9]+\.[0-9]+\.[0-9]+'" \
    ux-command-center/src/version.ts 2>/dev/null \
    | grep -oE "[0-9]+\.[0-9]+\.[0-9]+" || echo "MISSING")
  if [[ "$_CLAUDE_MD_VER" == "MISSING" || "$_LAYOUT_VER" == "MISSING" ]]; then
    _warn "CLAUDE.md or ux-command-center/src/version.ts not parseable — skipping version sub-check"
  elif [[ "$_CLAUDE_MD_VER" != "$_LAYOUT_VER" ]]; then
    _fail "CLAUDE.md version drift: CLAUDE.md=V${_CLAUDE_MD_VER}, Layout.tsx (via version.ts)=V${_LAYOUT_VER}"
    _DRIFT_FAIL=1
  else
    _pass "CLAUDE.md version matches Layout.tsx (V${_CLAUDE_MD_VER})"
  fi

  # ── sub-check c: installed git hook vs the repo copy ──
  # scripts/git-hooks/pre-push runs the release backup prune. It is COPIED into
  # .git/hooks/ per machine, so editing the repo copy does nothing until it is
  # reinstalled — and .git/hooks/ is not version-controlled, so nothing else
  # would ever say so. That gap is why a prune fix could look applied while the
  # old hook kept running. Warning, not a failure: CI and fresh clones have no
  # .git/hooks/pre-push at all, and that is legitimate.
  if [[ -f .git/hooks/pre-push ]]; then
    if diff -q scripts/git-hooks/pre-push .git/hooks/pre-push >/dev/null 2>&1; then
      _pass "Installed pre-push hook matches scripts/git-hooks/pre-push"
    else
      _warn "Installed .git/hooks/pre-push differs from scripts/git-hooks/pre-push"
      echo "  → Reinstall: cp scripts/git-hooks/pre-push .git/hooks/ && chmod +x .git/hooks/pre-push"
    fi
  fi

  if [[ $_DRIFT_FAIL -ne 0 ]]; then
    HAS_LOGIC_FAILURE=1
    echo "  → Fix: update the flagged files to match the canonical sources above."
    echo "         Canonical sources: VERSION (version), INTEGRITY_CHECKS registry (count),"
    echo "         AGENTS.md rule headings (rule count). Never hard-code these numbers."
  fi
fi

# =============================================================================
# [j] Pipeline diagram drift — docs/architecture/pipeline-flow.md must match
#     src/sync/phases/manifest.py (generated; see Phase A3)
# =============================================================================
echo ""
echo "[j] Pipeline diagram drift check"
_J_PY="${VENV_PYTHON}"
if [[ ! -x "$_J_PY" ]]; then
  _J_COMMON="$(cd "$REPO_ROOT/../.." 2>/dev/null && pwd)/.venv/bin/python"
  [[ -x "$_J_COMMON" ]] && _J_PY="$_J_COMMON" || _J_PY="python3"
fi
if [[ -f "$REPO_ROOT/scripts/generate_pipeline_diagram.py" ]]; then
  if "$_J_PY" "$REPO_ROOT/scripts/generate_pipeline_diagram.py" --check >/dev/null 2>&1; then
    _pass "pipeline-flow.md diagram in sync with PIPELINE_MANIFEST"
  else
    _fail "pipeline-flow.md diagram is stale vs src/sync/phases/manifest.py"
    echo "  → Fix: .venv/bin/python scripts/generate_pipeline_diagram.py"
    HAS_QUALITY_FAILURE=1
  fi
fi

# =============================================================================
# CHECK k: WRITABLE DB WITHOUT mark_dirty() IN ROUTE HANDLERS
# Source: DB-safety discipline — Cloud Run GCS flush
# What it catches: async route functions in src/api/routes/*.py that open a
#                  writable DuckDB connection (read_only=False, _open_writable,
#                  get_writable_db) without calling mark_dirty() — meaning DB
#                  mutations are not uploaded to GCS on Cloud Run.
# Known pre-existing exceptions: scripts/.baseline-mark-dirty.txt
# Scope: src/api/routes/ async functions only (helpers exempt)
# =============================================================================
echo ""
echo "[k] Writable DB without mark_dirty() (route handlers)"

_K_PYTHON="$VENV_PYTHON"
if [[ ! -x "$_K_PYTHON" ]]; then
  _K_PYTHON="$(cd "$REPO_ROOT/../.." 2>/dev/null && pwd)/.venv/bin/python"
fi
if [[ ! -x "$_K_PYTHON" ]]; then
  command -v python3 &>/dev/null && _K_PYTHON="python3" || _K_PYTHON="python"
fi

if [[ -f "$SCRIPTS_DIR/check_mark_dirty.py" ]]; then
  MARK_DIRTY_CURRENT=$("$_K_PYTHON" "$SCRIPTS_DIR/check_mark_dirty.py" 2>/dev/null || true)

  if ! _check_baseline "Writable DB without mark_dirty" \
       "$SCRIPTS_DIR/.baseline-mark-dirty.txt" \
       "$MARK_DIRTY_CURRENT"; then
    echo "  → Fix: add mark_dirty() after any DB write in the flagged route handler."
    echo "         If the write is best-effort / ambiguous, add to .baseline-mark-dirty.txt"
    echo "         with a justification comment in check_mark_dirty.py."
    echo "  → See: AGENTS.md (GCS flush discipline), src/storage/gcs_flush.py"
    HAS_LOGIC_FAILURE=1
  fi
else
  _warn "[k] scripts/check_mark_dirty.py not found — skipping writable DB check"
fi

# =============================================================================
# SUMMARY
# =============================================================================
echo ""
echo "============================================================"
if [[ $HAS_LOGIC_FAILURE -eq 0 && $HAS_QUALITY_FAILURE -eq 0 ]]; then
  echo -e "${GREEN}All checks passed.${RESET}"
  exit 0
elif [[ $HAS_LOGIC_FAILURE -eq 1 && $HAS_QUALITY_FAILURE -eq 0 ]]; then
  echo -e "${RED}Business logic violations found (exit 2).${RESET}"
  echo "See known-issues.md for fix guidance."
  exit 2
elif [[ $HAS_LOGIC_FAILURE -eq 0 && $HAS_QUALITY_FAILURE -eq 1 ]]; then
  echo -e "${YELLOW}Code quality issues found (exit 3).${RESET}"
  exit 3
else
  # Both logic and quality failures — exit 2 takes precedence
  echo -e "${RED}Business logic + code quality violations found (exit 2).${RESET}"
  echo "See known-issues.md for fix guidance."
  exit 2
fi
