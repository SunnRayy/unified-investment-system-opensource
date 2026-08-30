---
name: pre-development-gate
description: Use BEFORE any change to a critical-path file (the core pipeline, domain calculations, identity/auth, schema, or the integrity gate). A mandatory architecture + baseline read so a change can't silently contradict the design.
---

# Pre-Development Architecture Gate

Before touching any **critical-path file** (defined in CLAUDE.md), complete this gate. Skipping it is how
cold sessions introduce changes that pass tests but break the design.

## Steps

1. **Read the architecture** for the area you're about to change — the architecture doc section + the
   relevant ADR(s). If none exists for a decision you're about to make, write an ADR first.
2. **Read AGENTS.md** in full if you haven't this session (especially the Core Doctrine + the rules in
   scope for your change).
3. **Capture the baseline** so you can detect regressions:
   ```bash
   bash scripts/verify.sh            # record exit code
   {{domain integrity / golden check}}   # record score
   ```
4. **Write the consistency statement** (out loud, before coding):
   > "I have read [specific sections/ADRs]. My planned change is consistent with the architecture
   > because [reason]. Baseline: verify=[exit], integrity=[score]."
5. **If your change CONTRADICTS the architecture doc → STOP and flag the human.** Do not "just make it
   work" against the documented design — either the doc is wrong (update it + ADR) or the change is.

## Known-trap pass

Before writing, re-read the project's known traps (`docs/known-issues.md` + AGENTS.md trap table) for
this area. Most "new" bugs are re-introductions of a documented one. Confirm your approach avoids each
relevant trap.

## Output

You may not begin implementation until the consistency statement is written and the baseline recorded.
Carry both into the implementation phase so the post-change diff is meaningful.
