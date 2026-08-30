#!/usr/bin/env bash
# =============================================================================
# Huinsight canonical pytest invocation — single source of truth for test scope.
# Used by:
#   - CI (.github/workflows/deploy.yml "Run tests" step)
#   - Local CI-parity gate (dev.sh verify --ci)
#
# INTENTIONALLY has NO UIS_DB_PATH guard — CI runs with the default path and
# no UIS_DB_PATH set.  DB isolation is the CALLER's responsibility:
#   - CI:  "Initialize test database" step runs --init before calling this.
#   - Local: dev.sh verify --ci creates a tmp DB, sets UIS_DB_PATH, then calls here.
#
# Extra args forwarded to pytest: e.g.
#   bash scripts/run-tests.sh -x           # stop on first failure
#   bash scripts/run-tests.sh -v           # verbose
#   bash scripts/run-tests.sh --co -q     # collect-only dry run
# =============================================================================
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)

# ---------------------------------------------------------------------------
# DB-path safety guard (skipped in CI — GitHub Actions sets CI=true and uses
# an empty --init DB with no explicit UIS_DB_PATH).
#
# For local invocations, at least one isolation mechanism must be set and
# must NOT resolve under repo data/:
#   UIS_DB_PATH       — explicit DB path (e.g. a tmp file)
#   UIS_PROJECT_ROOT  — project root override (dev.sh verify --ci uses this;
#                       DatabaseConnector defaults to <root>/data/unified.duckdb)
#
# Neither set → refuse.  Either pointing into repo data/ → refuse.
# The canonical local entrypoint is:  ./dev.sh verify --ci
# ---------------------------------------------------------------------------
if [[ -z "${CI:-}" ]]; then
    _data_dir=$(realpath "${REPO_ROOT}/data")
    if [[ -n "${UIS_DB_PATH:-}" ]]; then
        _resolved=$(realpath "${UIS_DB_PATH}")
        if [[ "${_resolved}" == "${_data_dir}"* ]]; then
            echo "ERROR: run-tests.sh — UIS_DB_PATH resolves under repo data/ (production DB). Refusing." >&2
            echo "  Resolved: ${_resolved}" >&2
            exit 1
        fi
    elif [[ -n "${UIS_PROJECT_ROOT:-}" ]]; then
        _resolved=$(realpath "${UIS_PROJECT_ROOT}")
        if [[ "${_resolved}" == "${_data_dir}"* || "${_resolved}" == "$(realpath "${REPO_ROOT}")" ]]; then
            echo "ERROR: run-tests.sh — UIS_PROJECT_ROOT points at or under repo root/data. Refusing." >&2
            echo "  Resolved: ${_resolved}" >&2
            exit 1
        fi
    else
        echo "ERROR: run-tests.sh — neither UIS_DB_PATH nor UIS_PROJECT_ROOT is set." >&2
        echo "  Safe usage: ./dev.sh verify --ci  (creates and cleans up a tmp DB automatically)" >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Resolve Python executable: prefer repo .venv, else PATH, else loud exit.
# ---------------------------------------------------------------------------
PYTHON=""
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    PYTHON="$REPO_ROOT/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
fi

if [[ -z "$PYTHON" ]]; then
    echo "ERROR: run-tests.sh — python not found in $REPO_ROOT/.venv/bin/python or \$PATH." >&2
    echo "  Fix: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

# Verify the resolved python is actually executable.
if ! "$PYTHON" -c "import sys" 2>/dev/null; then
    echo "ERROR: run-tests.sh — resolved python ($PYTHON) is not runnable." >&2
    exit 1
fi

echo "run-tests.sh: using python = $PYTHON"
echo "run-tests.sh: running pytest from $REPO_ROOT"

# ---------------------------------------------------------------------------
# The canonical pytest command — copied verbatim from deploy.yml "Run tests"
# step (2 --ignore + 9 --deselect + 1 -k). Do NOT edit this without also
# updating deploy.yml (or, better: deploy.yml should call this script instead).
# ---------------------------------------------------------------------------
cd "$REPO_ROOT"

set -x
"$PYTHON" -m pytest tests/ -q --tb=short \
    --ignore=tests/api/test_performance_values.py \
    --ignore=tests/api/test_wealthos_endpoints.py \
    -k "not test_complete_fallback_on_first_failure" \
    --deselect tests/api/test_operations_routes.py::test_sync_history_contract \
    --deselect tests/api/test_operations_routes.py::test_sync_history_filter_param_supports_all_and_no_change \
    --deselect tests/services/valuation/test_signal.py::test_high_with_adjustment \
    --deselect tests/services/valuation/test_signal.py::test_boundary_inversion \
    --deselect tests/services/valuation/test_signal.py::test_none_percentile_falls_back_to_absolute \
    --deselect tests/sources/test_schwab_transformer.py::TestTransformHoldings::test_transform_holdings_converts_security_market_value_to_cny \
    --deselect tests/sources/test_schwab_transformer.py::TestTransformHoldings::test_transform_holdings_converts_cash_market_value_to_cny \
    --deselect tests/validation/test_cost_basis_validator.py::TestCostBasisValidator::test_validates_with_currency_conversion \
    --deselect tests/validation/test_cost_basis_validator.py::TestCostBasisValidator::test_uses_schwab_fixed_fx_convention_even_when_live_fx_differs \
    "$@"
