---
name: handover
description: Use when writing or updating HANDOVER.md at the end of a work block — produces a handoff specific enough that the next cold session resumes with zero verbal context. Lighter than session-close (which is the full verify+commit ritual); this is the doc-writing craft itself.
---

# Handover Craft

A handover is a letter to the next agent (often a cold session, often future-you). It has failed if the
reader has to ask you anything. Optimize for **resumability**, not summary.

## The test

Before saving, ask: *"Could a fresh session with no memory of mine continue from this alone?"* If any
answer below is "they'd have to guess", fix it.

## Required sections (use templates/HANDOVER.template.md)

1. **One-line state** in the title: `Session Handover: <version> — <what's true right now>`.
2. **What got done** — link commit hashes; "done" means merged or verified, not "wrote some code".
3. **In progress, with EXACT state** — not "working on the API" but "`routes/x.py` handler written and
   passing; the frontend call in `Y.tsx` is stubbed and returns mock data; FX edge case untested".
4. **Next steps, ordered and unambiguous** — name the file and the change, not the goal. "Add pagination
   to `/holdings` (cursor on `id`)" beats "improve holdings endpoint".
5. **Landmines** — anything that will bite: a rule added this branch, a half-applied migration, a flaky
   test, a "do not run X until Y".
6. **Verification state** — verify exit code, test pass count, integrity/golden score *as observed now*.

## Anti-patterns

- Narrating activity ("explored the codebase, made some changes") instead of state.
- Listing what you *intended* as if done. Only verified outcomes go in "done".
- Burying the one blocker that matters under five that don't.
- Restating the whole project. The reader has the repo; give them the delta and the pointer.

## Where it lives

`HANDOVER.md` at repo root is the current handoff (overwrite each session). Durable state belongs in
`docs/project-status.md`; this is the transient baton. Commit it in the same commit as the work, not after.
