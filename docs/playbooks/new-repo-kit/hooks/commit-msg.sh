#!/usr/bin/env bash
# commit-msg.sh — git commit-msg hook enforcing conventional commits + the naming convention.
# Install: ln -sf ../../scripts/commit-msg.sh .git/hooks/commit-msg  (and chmod +x)
# Blocks the commit (exit 1) on violation.
set -uo pipefail
msg_file="$1"
subject="$(head -1 "$msg_file")"

# Allow merge/revert commits through.
case "$subject" in
  "Merge "*|"Revert "*) exit 0;;
esac

# <type>(<scope>): <imperative>   — type required; scope optional; lowercase type; no trailing period.
pattern='^(feat|fix|refactor|test|docs|chore|spec|perf|build|ci)(\([a-z0-9._-]+\))?: .+[^.]$'
if ! printf '%s' "$subject" | grep -qE "$pattern"; then
  echo "✗ commit-msg: subject must be '<type>(<scope>): <imperative>' (lowercase type, no trailing period)." >&2
  echo "  Got: $subject" >&2
  echo "  Types: feat fix refactor test docs chore spec perf build ci" >&2
  exit 1
fi

# Scope must NOT be a planning unit (those go in the body). Ban ws-*, pass-*, batch-*, phase-*, single-letter+digit.
scope="$(printf '%s' "$subject" | sed -nE 's/^[a-z]+\(([a-z0-9._-]+)\):.*/\1/p')"
if printf '%s' "$scope" | grep -qiE '^(ws-?[a-z]|pass-?[a-z0-9]|batch-?[0-9]|phase-?[0-9]|[a-z][0-9](\.[0-9])?)$'; then
  echo "✗ commit-msg: scope '($scope)' looks like a planning unit." >&2
  echo "  Scope = module/doc category (readers, api, docs...). Put the Step (e.g. C3.3) in the body." >&2
  exit 1
fi
exit 0
