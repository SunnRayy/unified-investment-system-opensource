---
name: push-merge-deploy
description: Use when a branch is ready to merge and/or deploy. Runs the pre-merge document + verification gate, performs the merge, and — critically — VERIFIES the deploy (a successful push is not a successful deploy). Stops on any red gate.
---

# Push / Merge / Deploy Gate

A push is not a merge; a merge is not a deploy; a deploy is not a *working* deploy. Each transition has
its own gate. Never report a later state on the evidence of an earlier one.

## Stage 1 — Pre-merge gate (on the feature branch, BEFORE merging)

1. **Verification green:**
   ```bash
   bash scripts/verify.sh            # exit 0 or 3
   {{TEST_CMD}}                      # full scope, report pass count
   {{domain integrity / golden check}}
   ```
2. **Document checklist** (commit doc updates to the feature branch *before* the merge, so the merge
   commit reflects the true final state — never "merge first, document later"):
   - `CHANGELOG.md` — new version section (semver: major=program · minor=workstream/step · patch=hotfix).
   - `docs/project-status.md` — version + workstream/step status.
   - `HANDOVER.md` — final state.
   - ADR added/updated if an architectural decision was made (or state "no new decision").
   - Version string bumped wherever it's displayed.
3. **Self-review the full diff** for the project's critical-path invariants and known traps. For a large
   or critical-path diff, run `/code-review` and triage.

## Stage 2 — Merge

- Prefer a PR even solo (it's the audit trail + the CI gate + the place review comments live). Link the
  issue with a closing keyword (`Closes #N`). Squash unless the individual commits carry value.
- For parallel/worktree efforts, merge feature branches into an **integration branch** first, test there,
  then fast-forward to main — keeps main always-green.
- Branch protection: require passing CI before merge. Don't merge red.

## Stage 3 — Deploy verification (the step most often skipped)

A successful deploy command ≠ a working service.
1. Watch the CI/CD run to completion (`{{ci watch command}}`) — exit-status gated.
2. Smoke-test the LIVE service: health endpoint returns ok AND the version/SHA matches what you shipped.
3. Hit one real data path (login + one core read) against the live deploy.
4. Only now report "deployed". If any check fails, report the failure + where it stopped — never a
   green status you didn't observe.

## Stop conditions

Any red verify/CI, a failed smoke test, a version mismatch, or an escalation trigger (core metric moved,
schema/auth change, ordering change) → STOP and report. Do not push past a red gate to "fix it after".
