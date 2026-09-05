#!/usr/bin/env bash
# PreToolUse hook: hard-block destructive DB operations (CLAUDE.md Database Safety Rules).
# Reads the PreToolUse JSON payload on stdin; exit 2 blocks the tool call and
# feeds stderr back to the agent. Exit 0 allows the call.
#
# Blocks:
#   - main.py --init / --reset (schema-recreating commands)
#   - DROP TABLE / TRUNCATE / DELETE FROM in shell-issued SQL
#   - rm/mv/cp targeting data/unified.duckdb or data/backups/

set -u

CMD=$(python3 -c '
import json, sys
try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)
print(payload.get("tool_input", {}).get("command", ""))
' 2>/dev/null) || exit 0

[ -z "$CMD" ] && exit 0

block() {
  echo "BLOCKED by DB safety guard: $1. CLAUDE.md Database Safety Rules require explicit human confirmation for this operation. Ask the user before proceeding." >&2
  exit 2
}

# 0. Early allow: read-only GCS download to /tmp for inspection.
#    Strict single-command form only (no pipes/;/&&): `gsutil cp gs://... /tmp/...`
#    or `gcloud storage cp gs://... /tmp/...`. Source is remote, destination is
#    outside data/ — the local production DB and backups are untouched.
if echo "$CMD" | grep -qE '^[[:space:]]*(gsutil([[:space:]]+-[^[:space:]]+)*[[:space:]]+cp|gcloud[[:space:]]+storage[[:space:]]+cp)[[:space:]]+gs://[^[:space:]]+[[:space:]]+(/tmp/|/private/tmp/)[^[:space:];|&]*[[:space:]]*$'; then
  exit 0
fi

# 1. Schema-recreating CLI commands
if echo "$CMD" | grep -qE 'main\.py[^|;&]*--(init|reset)\b'; then
  block "main.py --init/--reset recreates the schema and drops tables"
fi

# 2. Destructive SQL issued via shell — only when the command invokes something
#    that can execute SQL (avoids false positives on commit messages, docs, grep)
if echo "$CMD" | grep -qE '\b(python3?|duckdb|sqlite3|psql)\b|\.sql\b' \
   && echo "$CMD" | grep -qiE '\b(DROP[[:space:]]+TABLE|TRUNCATE[[:space:]]+TABLE?|DELETE[[:space:]]+FROM)\b'; then
  block "destructive SQL (DROP TABLE / TRUNCATE / DELETE FROM)"
fi

# 3. Deleting/moving/overwriting the production DB or its backups
if echo "$CMD" | grep -qE '\b(rm|mv|cp|shred|unlink)\b[^|;&]*(unified\.duckdb|data/backups)'; then
  block "rm/mv/cp targeting data/unified.duckdb or data/backups/"
fi

exit 0
