#!/usr/bin/env bash
# Huinsight dev environment manager — start/stop/restart/status/logs
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
UIS_DIR="$SCRIPT_DIR/.uis"
BACKEND_PID="$UIS_DIR/backend.pid"
FRONTEND_PID="$UIS_DIR/frontend.pid"
BACKEND_LOG="$UIS_DIR/backend.log"
FRONTEND_LOG="$UIS_DIR/frontend.log"

BACKEND_PORT=8008
FRONTEND_PORT=5003
BROWSER_URL="http://localhost:$FRONTEND_PORT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${CYAN}[uis]${NC} $*"; }
ok()   { echo -e "${GREEN}[uis]${NC} $*"; }
warn() { echo -e "${YELLOW}[uis]${NC} $*"; }
err()  { echo -e "${RED}[uis]${NC} $*" >&2; }

# -- Pre-flight ---------------------------------------------------------------

preflight() {
    local fail=0
    if [[ ! -f "$SCRIPT_DIR/.venv/bin/python" ]]; then
        err "Virtual environment not found."
        err "  Fix: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
        fail=1
    fi
    if [[ ! -d "$SCRIPT_DIR/ux-command-center/node_modules" ]]; then
        err "Frontend dependencies missing."
        err "  Fix: cd ux-command-center && npm install"
        fail=1
    fi
    [[ $fail -eq 0 ]]
}

# -- Port conflict ------------------------------------------------------------

check_port() {
    local port=$1 name=$2
    local occupant
    occupant=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | tail -n +2 || true)
    if [[ -n "$occupant" ]]; then
        warn "Port $port ($name) is already in use:"
        echo "$occupant"
        printf "  [k]ill and continue / [a]bort? "
        read -r answer
        case "$answer" in
            k|K)
                lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | xargs kill -TERM 2>/dev/null || true
                sleep 1
                ;;
            *)
                err "Aborted."
                exit 1
                ;;
        esac
    fi
}

# -- Readiness probes ---------------------------------------------------------

wait_backend() {
    local i=0
    while [[ $i -lt 40 ]]; do
        if curl -sf "http://localhost:$BACKEND_PORT/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
        ((i++))
    done
    err "Backend not ready after 20s — check logs: ./dev.sh logs backend"
    return 1
}

wait_frontend() {
    local i=0
    while [[ $i -lt 40 ]]; do
        if grep -q "ready in" "$FRONTEND_LOG" 2>/dev/null; then
            return 0
        fi
        sleep 0.5
        ((i++))
    done
    err "Frontend not ready after 20s — check logs: ./dev.sh logs frontend"
    return 1
}

# -- PID helpers --------------------------------------------------------------

pid_alive() {
    [[ -f "$1" ]] && kill -0 "$(cat "$1")" 2>/dev/null
}

stop_pid() {
    local pid_file=$1
    if [[ -f "$pid_file" ]]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
            local i=0
            while kill -0 "$pid" 2>/dev/null && [[ $i -lt 10 ]]; do
                sleep 0.5; ((i++))
            done
            kill -KILL "$pid" 2>/dev/null || true
        fi
        rm -f "$pid_file"
    fi
}

# -- Commands -----------------------------------------------------------------

do_start() {
    local no_browser=0
    [[ "${1:-}" == "--no-browser" ]] && no_browser=1

    preflight || exit 1
    mkdir -p "$UIS_DIR"

    check_port "$BACKEND_PORT" "backend"
    check_port "$FRONTEND_PORT" "frontend"

    # Backend — absolute venv path, runs from repo root
    log "Starting backend (port $BACKEND_PORT)..."
    "$SCRIPT_DIR/.venv/bin/python" -m uvicorn src.api.main:app \
        --reload --port "$BACKEND_PORT" \
        > "$BACKEND_LOG" 2>&1 &
    echo $! > "$BACKEND_PID"

    log "Waiting for backend..."
    if ! wait_backend; then
        do_stop; exit 1
    fi
    ok "Backend ready  → http://localhost:$BACKEND_PORT"

    # Frontend — cd into subdir, absolute log path
    log "Starting frontend (port $FRONTEND_PORT)..."
    (cd "$SCRIPT_DIR/ux-command-center" && npm run dev > "$FRONTEND_LOG" 2>&1) &
    echo $! > "$FRONTEND_PID"

    log "Waiting for frontend..."
    if ! wait_frontend; then
        do_stop; exit 1
    fi
    ok "Frontend ready → $BROWSER_URL"

    [[ $no_browser -eq 0 ]] && open "$BROWSER_URL" 2>/dev/null || true

    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}✅ Huinsight dev environment running${NC}"
    echo -e "   Frontend  → $BROWSER_URL"
    echo -e "   Backend   → http://localhost:$BACKEND_PORT"
    echo -e "   API docs  → http://localhost:$BACKEND_PORT/docs"
    echo -e "   Logs      → ./dev.sh logs [backend|frontend]"
    echo -e "   Stop      → ./dev.sh stop"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

do_stop() {
    local any=0
    if pid_alive "$BACKEND_PID" || pid_alive "$FRONTEND_PID"; then
        any=1
    fi
    stop_pid "$BACKEND_PID"
    stop_pid "$FRONTEND_PID"
    # Kill any uvicorn reloader children that survived SIGTERM
    pkill -f "uvicorn.*main:app" 2>/dev/null || true
    [[ $any -eq 1 ]] && ok "Servers stopped." || log "No running servers found."
}

do_status() {
    echo ""
    for svc in backend frontend; do
        local pid_file="$UIS_DIR/${svc}.pid"
        local log_file="$UIS_DIR/${svc}.log"
        local port
        [[ $svc == backend ]] && port=$BACKEND_PORT || port=$FRONTEND_PORT

        if pid_alive "$pid_file"; then
            local pid last
            pid=$(cat "$pid_file")
            last=$(tail -1 "$log_file" 2>/dev/null | cut -c1-80 || echo "(no log)")
            echo -e "${GREEN}✅ $svc${NC}  PID $pid  port $port"
            echo "   $last"
        else
            echo -e "${RED}✗  $svc${NC}  not running"
        fi
        echo ""
    done
}

do_logs() {
    local target="${1:-both}"
    case "$target" in
        backend)  tail -f "$BACKEND_LOG" ;;
        frontend) tail -f "$FRONTEND_LOG" ;;
        both)
            if command -v multitail &>/dev/null; then
                multitail -l "tail -f $BACKEND_LOG" -l "tail -f $FRONTEND_LOG"
            else
                tail -f "$BACKEND_LOG" "$FRONTEND_LOG"
            fi
            ;;
        *)
            err "Usage: ./dev.sh logs [backend|frontend|both]"
            exit 1
            ;;
    esac
}

do_verify() {
    # ── verify / verify --full / verify --ci ──────────────────────────────
    # verify:        fast gate — scripts/verify.sh (static + drift check) +
    #                pytest on the default scope (unit tests, no live DB).
    # verify --full: full gate — adds pytest tests/ -q (all tests, forced
    #                tmp DB) + --check-integrity --json (reads live DB).
    # verify --ci:   CI-parity gate — reproduces the exact CI test run locally
    #                against an isolated tmp DB (DB-SAFE: never touches prod DB).
    #
    # Exit codes mirror scripts/verify.sh: 0=clean, 1=P0, 2=logic, 3=quality.
    local full=0
    local ci=0
    [[ "${1:-}" == "--full" ]] && full=1
    [[ "${1:-}" == "--ci" ]] && ci=1

    local PYTHON="$SCRIPT_DIR/.venv/bin/python"
    # Worktree: venv lives two levels up (main project root)
    if [[ ! -x "$PYTHON" ]]; then
        PYTHON="$(cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd)/.venv/bin/python"
    fi
    [[ -x "$PYTHON" ]] || { err "venv not found — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }

    if [[ $ci -eq 1 ]]; then
        # ── CI-parity mode (DB-SAFE) ─────────────────────────────────────
        # Reproduces the exact CI test environment locally:
        #   1. Create a tmp directory to serve as the isolated project root.
        #   2. Init a fresh DB at <TMP_ROOT>/data/unified.duckdb.
        #      UIS_PROJECT_ROOT=<TMP_ROOT> is set so that connector.project_root()
        #      uses TMP_ROOT, not the main repo root (which would resolve to the
        #      production DB in the worktree context).
        #   3. Run scripts/run-tests.sh with UIS_PROJECT_ROOT=<TMP_ROOT> and
        #      UIS_SKIP_DB_STARTUP_VALIDATION=1. UIS_DB_PATH is NOT set for the
        #      test run — this matches CI where UIS_DB_PATH is never set in the
        #      "Run tests" step, so test monkeypatching of _DB_PATH works correctly.
        #   4. The trap cleans TMP_ROOT dir on exit/interrupt.
        #
        # DB-safety: the production DB at data/unified.duckdb is NEVER opened.
        # The DB-safety assert verifies TMP_ROOT is in /tmp, not under repo data/.
        #
        # KNOWN LOCAL DEVIATION (+1 test vs CI):
        # tests/database/test_connector.py::test_default_db_path_resolves_to_project_root
        # fails locally because UIS_PROJECT_ROOT overrides the worktree resolution.
        # This test passes in CI (no UIS_PROJECT_ROOT set in a flat checkout).
        # All 8 canonical failures from the 2026-05-30 CI run are reproduced here.

        # Locate the production DB for size-check reporting only.
        local PROD_DB
        PROD_DB="$(cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd)/data/unified.duckdb"
        [[ -f "$SCRIPT_DIR/data/unified.duckdb" ]] && PROD_DB="$SCRIPT_DIR/data/unified.duckdb"
        local prod_size_before="N/A"
        if [[ -f "$PROD_DB" ]]; then
            prod_size_before=$(stat -f %z "$PROD_DB" 2>/dev/null || stat -c %s "$PROD_DB" 2>/dev/null || echo "N/A")
        fi
        log "CI-parity gate: prod DB ($PROD_DB) size before = $prod_size_before bytes"

        # Create isolated tmp root (replaces repo root for this test run)
        local TMP_ROOT
        TMP_ROOT=$(mktemp -d /tmp/uis_ci_XXXXXX)

        # Always clean up tmp root on exit or interrupt.
        # Double-quote the trap so $TMP_ROOT expands NOW (at registration time),
        # not when the trap fires — by which point the local variable is gone.
        # shellcheck disable=SC2064
        trap "rm -rf '$TMP_ROOT'" EXIT

        # ── DB-safety assert ─────────────────────────────────────────────
        # Resolve the real path of TMP_ROOT and verify it is NOT under the
        # repo's data/ directory and IS under /tmp (or /private/tmp on macOS).
        local TMP_ROOT_REAL
        TMP_ROOT_REAL=$("$PYTHON" -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$TMP_ROOT" 2>/dev/null || echo "$TMP_ROOT")
        local REPO_DATA_REAL
        REPO_DATA_REAL=$("$PYTHON" -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$SCRIPT_DIR/data" 2>/dev/null || echo "$SCRIPT_DIR/data")

        # Reject if tmp root is under the repo data/ dir
        if [[ "$TMP_ROOT_REAL" == "$REPO_DATA_REAL"* ]]; then
            err "DB-SAFETY ASSERTION FAILED: TMP_ROOT ($TMP_ROOT_REAL) is under repo data/ ($REPO_DATA_REAL)."
            err "Refusing to run --init inside the repo data directory."
            exit 1
        fi

        # Reject if tmp root is not under /tmp or /private/tmp (macOS resolves /tmp → /private/tmp)
        if [[ "$TMP_ROOT_REAL" != /tmp/* && "$TMP_ROOT_REAL" != /private/tmp/* ]]; then
            err "DB-SAFETY ASSERTION FAILED: TMP_ROOT ($TMP_ROOT_REAL) is not under /tmp or /private/tmp."
            err "Refusing to run --init on an unexpected path."
            exit 1
        fi

        log "DB-safety assert passed: TMP_ROOT=$TMP_ROOT_REAL (not under $REPO_DATA_REAL)"

        # Create the data/ directory inside TMP_ROOT (mirrors CI's 'mkdir -p data')
        mkdir -p "$TMP_ROOT/data"

        log "Initializing tmp DB at $TMP_ROOT/data/unified.duckdb ..."

        # Init with both UIS_PROJECT_ROOT and UIS_DB_PATH set so --init writes
        # to the tmp root, not the main project.
        UIS_PROJECT_ROOT="$TMP_ROOT" UIS_DB_PATH="$TMP_ROOT/data/unified.duckdb" \
            UIS_SKIP_DB_STARTUP_VALIDATION=1 \
            "$PYTHON" "$SCRIPT_DIR/main.py" --init

        log "Running CI-parity test suite (scripts/run-tests.sh)..."
        log "  UIS_PROJECT_ROOT=$TMP_ROOT"
        log "  UIS_DB_PATH is NOT set (matches CI; test monkeypatching works)"
        local tests_exit=0
        # Do NOT set UIS_DB_PATH for tests — matches CI's "Run tests" step.
        # UIS_PROJECT_ROOT points tests to the isolated tmp DB as the default.
        UIS_PROJECT_ROOT="$TMP_ROOT" UIS_SKIP_DB_STARTUP_VALIDATION=1 \
            bash "$SCRIPT_DIR/scripts/run-tests.sh" || tests_exit=$?

        # Report prod DB size after (should be unchanged)
        local prod_size_after="N/A"
        if [[ -f "$PROD_DB" ]]; then
            prod_size_after=$(stat -f %z "$PROD_DB" 2>/dev/null || stat -c %s "$PROD_DB" 2>/dev/null || echo "N/A")
        fi
        log "CI-parity gate: prod DB ($PROD_DB) size after  = $prod_size_after bytes"
        if [[ "$prod_size_before" != "N/A" && "$prod_size_after" != "N/A" ]]; then
            if [[ "$prod_size_before" == "$prod_size_after" ]]; then
                ok "Prod DB unchanged: $prod_size_before bytes (before) = $prod_size_after bytes (after)"
            else
                err "CRITICAL: Prod DB size changed! before=$prod_size_before after=$prod_size_after"
                err "STOP — investigate immediately before continuing."
            fi
        fi

        if [[ $tests_exit -ne 0 ]]; then
            err "CI-parity test suite failed (exit $tests_exit)"
            exit $tests_exit
        fi

        echo ""
        ok "CI-parity gate passed"
        return
    fi

    log "Running pre-commit verification (verify.sh)..."
    bash "$SCRIPT_DIR/scripts/verify.sh"
    local verify_exit=$?
    if [[ $verify_exit -ne 0 ]]; then
        err "verify.sh failed (exit $verify_exit)"
        exit $verify_exit
    fi
    ok "verify.sh passed"

    if [[ $full -eq 0 ]]; then
        # Fast gate: default pytest scope only (no live DB needed)
        log "Running unit tests (default scope — no live DB)..."
        "$PYTHON" -m pytest -q
        local pytest_exit=$?
        if [[ $pytest_exit -ne 0 ]]; then
            err "Unit tests failed (exit $pytest_exit)"
            exit $pytest_exit
        fi
        ok "Unit tests passed"
    else
        # Full gate: all tests + integrity check
        log "Running full test suite (forced tmp DB)..."
        local TMP_DB
        TMP_DB=$(mktemp /tmp/uis_verify_XXXXXX.duckdb)
        UIS_DB_PATH="$TMP_DB" "$PYTHON" -m pytest tests/ -q
        local pytest_exit=$?
        rm -f "$TMP_DB"
        if [[ $pytest_exit -ne 0 ]]; then
            err "Full test suite failed (exit $pytest_exit)"
            exit $pytest_exit
        fi
        ok "Full test suite passed"

        if [[ -f "$SCRIPT_DIR/data/unified.duckdb" ]]; then
            log "Running integrity checks (live DB, read-only)..."
            "$PYTHON" "$SCRIPT_DIR/main.py" --check-integrity --json | python3 -m json.tool
            local integrity_exit=${PIPESTATUS[0]}
            if [[ $integrity_exit -ne 0 ]]; then
                err "Integrity checks failed — see output above"
                exit 2
            fi
            ok "All integrity checks passed"
        else
            warn "data/unified.duckdb not found — skipping integrity checks"
        fi
    fi

    echo ""
    ok "All verification gates passed ✅"
}

do_status_json() {
    # ── status --json ──────────────────────────────────────────────────────
    # Machine-readable session status for agents. Generated (not tracked in git).
    # Output: stdout JSON + written to .uis/status.json (gitignored).
    local PYTHON="$SCRIPT_DIR/.venv/bin/python"
    # Worktree: venv lives two levels up (main project root)
    if [[ ! -x "$PYTHON" ]]; then
        PYTHON="$(cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd)/.venv/bin/python"
    fi
    [[ -x "$PYTHON" ]] || { err "venv not found — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }

    mkdir -p "$UIS_DIR"

    local version
    version=$(cat "$SCRIPT_DIR/VERSION" 2>/dev/null | tr -d '[:space:]' || echo "unknown")

    local integrity_count
    integrity_count=$("$PYTHON" -c "
import sys; sys.path.insert(0,'$SCRIPT_DIR')
from src.validation.data_integrity_gate import INTEGRITY_CHECK_COUNT
print(INTEGRITY_CHECK_COUNT)
" 2>/dev/null || echo "unknown")

    local backend_running="false"
    pid_alive "$BACKEND_PID" && backend_running="true"

    local frontend_running="false"
    pid_alive "$FRONTEND_PID" && frontend_running="true"

    local db_exists="false"
    [[ -f "$SCRIPT_DIR/data/unified.duckdb" ]] && db_exists="true"

    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    local payload
    payload=$(cat <<EOF
{
  "generated_at": "$ts",
  "version": "$version",
  "integrity_check_count": $integrity_count,
  "services": {
    "backend_running": $backend_running,
    "frontend_running": $frontend_running
  },
  "db": {
    "exists": $db_exists
  },
  "verify_command": "./dev.sh verify",
  "verify_full_command": "./dev.sh verify --full"
}
EOF
)

    echo "$payload"
    echo "$payload" > "$UIS_DIR/status.json"
    log "Status written to .uis/status.json"
}

# -- Pull-cloud ---------------------------------------------------------------

do_pull_cloud() {
    # Stop servers first so the DB file is not held open.
    log "Stopping servers before DB pull..."
    do_stop

    log "Pulling cloud (production) DB → local..."
    # --yes: ./dev.sh pull-cloud IS the confirmation; no interactive prompt needed.
    if ! "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/scripts/maint_db.py" \
            --pull-cloud --yes; then
        err "pull-cloud FAILED — servers NOT restarted."
        err "Inspect the error above. If the old DB was archived, check:"
        err "  data/backups/pre-pull-*.duckdb   (restore with shutil.move or cp)"
        exit 1
    fi

    ok "Cloud DB installed. Starting servers..."
    do_start --no-browser
}

# -- Entrypoint ---------------------------------------------------------------

CMD="${1:-help}"
shift || true

case "$CMD" in
    start)        do_start ${@+"$@"} ;;
    stop)         do_stop ;;
    restart)      do_stop; do_start ${@+"$@"} ;;
    status)
        if [[ "${1:-}" == "--json" ]]; then
            do_status_json
        else
            do_status
        fi
        ;;
    logs)         do_logs "${1:-both}" ;;
    verify)       do_verify "${1:-}" ;;
    pull-cloud)   do_pull_cloud ;;
    *)
        echo "Usage: ./dev.sh <command> [options]"
        echo ""
        echo "  start [--no-browser]          Start backend + frontend, open browser"
        echo "  stop                          Stop both servers"
        echo "  restart [--no-browser]        stop then start"
        echo "  status                        Running status + last log line"
        echo "  status --json                 Machine-readable session status (agents)"
        echo "  logs [backend|frontend|both]  Tail logs (default: both)"
        echo "  verify                        Fast gate: verify.sh + unit tests"
        echo "  verify --full                 Full gate: verify.sh + all tests + integrity"
        echo "  verify --ci                   CI-parity gate: tmp DB + exact CI pytest scope (DB-safe)"
        echo "  pull-cloud                    Replace local DB with cloud (production) DB;"
        echo "                                keeps 3 cloud-mirror + 2 pre-pull backups"
        ;;
esac
