#!/usr/bin/env bash
# session-orient.sh — Claude Code SessionStart hook. Prints orientation so every COLD session lands
# with the same context: branch, current status, how to verify. stdout is shown to the agent.
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

echo "=== SESSION ORIENTATION ==="
echo "Branch:  $(git branch --show-current 2>/dev/null)"
echo "Head:    $(git log --oneline -1 2>/dev/null)"
echo
echo "--- docs/project-status.md (top) ---"
sed -n '1,20p' docs/project-status.md 2>/dev/null || echo "(no project-status.md yet)"
echo
echo "--- Reminders ---"
echo "• Read AGENTS.md before touching critical-path files (Rule list + Core Doctrine)."
echo "• Run 'bash scripts/verify.sh' before AND after work (exit 0/3 ok; 1/2 = fix first)."
echo "• Non-trivial task → use the lead-planner skill. End of session → session-close skill."
echo "• Naming: Program > Workstream > Step > Task. Branch <prefix>/<ws><step>-slug."
echo "==========================="
