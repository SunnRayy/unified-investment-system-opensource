---
name: session-resume
description: Use at the START of a session that continues prior work (after a break, compaction, usage-limit, or handoff). Re-establishes context and the plan BEFORE writing any code, so a cold session doesn't undo the foundation or repeat finished work.
---

# Session Resume Ritual

A resumed session is a cold context wearing the previous session's clothes. Do not trust momentum —
re-orient first. "Finish the work" means finish it *via the protocol*, not inline from a half-memory.

## Steps (before any edit)

1. **Read the baton, in order:**
   - `HANDOVER.md` — what's in progress, next steps, landmines.
   - `docs/project-status.md` — current version, workstream/step status.
   - `task-context.md` — Completed / Remaining / Blockers for the active task.
   - The active plan in `docs/plans/` for this workstream/step.
2. **Re-orient against reality** (don't assume the handover is fresh — SOPs drift):
   ```bash
   git log --oneline -8 ; git status
   bash scripts/verify.sh           # confirm the baseline the handover claims
   ```
   If verify state ≠ what the handover claimed, the handover is stale — trust the repo, note the gap.
3. **Re-establish the plan.** If the task is non-trivial, re-invoke the `lead-planner` skill and restate
   the dispatch plan. An interruption does not downgrade the protocol; resumed work still gets a locked
   blueprint and (for implementation) subagents.
4. **Confirm in one paragraph** before coding: current state, the exact next action, and any landmine
   that applies. If you can't write this confidently, you haven't finished orienting — re-read.

## Recovering a dead subagent

If a subagent died mid-task (limit/error/interrupt): resume it via its agentId if the harness exposes
that, otherwise dispatch a FRESH implementation subagent (`model: sonnet`) restating the *remaining*
scope from scratch — a new agent has no memory of the dead one. The Lead may finish only review-level
fixes inline (≤~20 lines); new modules/tests are still implementation → delegate.

## Do not

- Start editing because the next step "looks obvious". Verify the baseline first.
- Re-do completed work because you didn't read "What got done".
- Absorb implementation into the lead session just because the prior session was interrupted.
