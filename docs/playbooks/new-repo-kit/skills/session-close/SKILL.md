---
name: session-close
description: Use at the end of every coding session before handing off — verification, doc sync, commit, and handover ritual. Prevents the "merged first, documented never" failure.
---

# Session Close Ritual

Run this before considering any session done. The goal: the next cold session (or the human) can resume
with zero verbal context.

## 1. Verify

```bash
bash scripts/verify.sh                 # must exit 0 or 3 (never 1 or 2)
{{TEST_CMD}}                            # report exact pass/fail count
{{domain integrity / golden check}}    # report score
```
Fix anything red before proceeding. Do not document a green state you didn't actually observe.

## 2. Sync the docs (only where something changed)

- **`docs/project-status.md`** — current version, workstream/step status, health snapshot, dated session note.
- **`HANDOVER.md`** — what got done, what's in progress (exact state), ordered next steps, landmines.
- **`task-context.md`** — Completed / Remaining / Blockers for the current task.
- **`docs/known-issues.md`** — if you hit a new failure pattern, add it; if a check caught something,
  confirm it's mapped; if automatable, note it for promotion into `verify.sh`.
- **`docs/architecture/*` + ADR** — if you changed documented behavior or made an architectural decision.
- **`CHANGELOG.md`** — only on a release.

## 3. Commit

- Stage only files you touched (`git add <paths>`) — never `git add -A`.
- Conventional-commit subject; planning unit (e.g. C3.3) in the body, not the scope.
- If a critical-path file changed, the commit body states the doc impact
  (`Docs: no update needed` or `Docs: updated [section]`).

## 4. Push

```bash
git push -u origin "$(git branch --show-current)"
```

## 5. Confirm

State, in one line: verify exit code, test pass count, what was committed, and the single next action.
A session is not closed until this confirmation exists.
