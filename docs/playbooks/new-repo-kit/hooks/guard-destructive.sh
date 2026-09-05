#!/usr/bin/env bash
# guard-destructive.sh — Claude Code PreToolUse hook (matcher: Bash).
# Reads the tool call as JSON on stdin; exits 2 to BLOCK the command if it matches the destructive
# denylist (Claude Code treats PreToolUse exit code 2 as "block + show stderr to the model").
#
# Wire it in .claude/settings.json (see hooks/settings.json). Specialize {{DATA_DIR}} / {{DB_FILE}}.
set -uo pipefail

input="$(cat)"
cmd="$(printf '%s' "$input" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("tool_input",{}).get("command",""))
except Exception: print("")' 2>/dev/null)"

# Denylist of irreversible / high-blast-radius patterns. Tune to your project.
deny=(
  'rm +-rf? +.*{{DATA_DIR}}'          # deleting the data dir
  'rm +.*{{DB_FILE}}'                  # deleting the database file
  '> *{{DB_FILE}}'                     # truncating the database file
  '(DROP|TRUNCATE) +TABLE'             # destructive SQL
  'CREATE +OR +REPLACE +TABLE'
  '--init|--reset|--recreate'          # schema-recreating CLI flags
  'git +push +.*(--force|-f)\b.*(main|master)'  # force-push to default branch
  'git +reset +--hard +origin/(main|master)'
  'DELETE +FROM'                       # unscoped deletes (review before allowing)
)

for pat in "${deny[@]}"; do
  if printf '%s' "$cmd" | grep -qiE "$pat"; then
    echo "BLOCKED by guard-destructive.sh: command matches /$pat/" >&2
    echo "This action is destructive or irreversible. Ask the human for explicit confirmation," >&2
    echo "or run it manually outside the agent. (Override: edit scripts/guard-destructive.sh.)" >&2
    exit 2
  fi
done
exit 0
