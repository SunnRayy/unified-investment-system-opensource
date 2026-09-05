# New-Repo Setup Prompt Library

> A copy-paste prompt sequence for bootstrapping a fresh repository with an AI agent (Claude Code).
> It lays the "underground foundation" from `ai-engineering-foundations.md`: a structured development
> process **plus** the enforcement and AI-learning scaffolding earned across thousands of sessions.
>
> **How to use:** run the prompts in order in a fresh Claude Code session at the repo root. Each is
> self-contained. Adapt the `{{PLACEHOLDERS}}`. Phases 0–4 are the foundation; do them before any
> feature code. Phases 5–8 are scaffolding you complete as the architecture takes shape.
>
> **Don't start from a blank page:** the ready-made files referenced throughout (AGENTS.md, CLAUDE.md,
> ADR/HANDOVER/known-issues/CHANGELOG templates, `verify.sh`, the PreToolUse/SessionStart hooks, the
> commit-msg + doc-freshness checks, and portable `lead-planner` / `pre-development-gate` /
> `session-close` skills) all live in [`new-repo-kit/`](new-repo-kit/). Copy that folder in, then use
> these prompts to fill the `{{PLACEHOLDERS}}` and wire everything up. See `new-repo-kit/README.md` for
> the install order.

---

## Guiding principle to paste at the top of session 1

```
We are bootstrapping a new project. Before any feature code, we lay an enforced foundation.
Core law: with AI agents, any invariant that isn't mechanically enforced will eventually be
violated — so we encode correctness as machine-checked gates (lint, types, hooks, CI, schema
constraints), not just as prose rules. Conventions are for humans; enforcement is for agents.
Use a lead-planner workflow: plan → self-review → lock → implement → verify. Ask before any
destructive or irreversible action. Confirm you understand before we begin.
```

---

## Phase 0 — Project charter + ADR-000

```
Create docs/decisions/ADR-000-charter.md. Interview me (one question at a time) to fill in:
- Problem this project solves and the single core domain invariant ("what must always be true").
- Target platforms now and *possible* later (web / mobile / CLI / API consumers).
- Tech stack and the ONE primary datastore.
- Single-user or multi-user/multi-tenant from day one?
Then write ADR-000 capturing these decisions with rationale, and a docs/project-status.md as the
single source of truth for current state. Keep both terse.
```

> Why: the "possible later platforms" answer decides whether you stub portability seams now (cheap)
> or retrofit them later (brutal).

---

## Phase 1 — Governance skeleton

```
Scaffold the agent-governance layer:
1. CLAUDE.md — short orientation: project overview, common commands, key modules table, and a
   "single source of truth" pointer to docs/project-status.md. Keep under ~150 lines.
2. AGENTS.md — seed it with the rule starters from Appendix A of this library, specialized to our
   stack. Number the rules.
3. docs/decisions/template.md — ADR template (Context / Decision / Consequences / Alternatives).
4. docs/playbooks/ — copy in ai-engineering-foundations.md (the carry-over doc).
5. A HANDOVER.md stub and a "session-close" checklist.
Do not invent rules we won't enforce — every rule in AGENTS.md must map to an enforcement mechanism
we set up in Phase 2, or be marked [convention-only].
```

---

## Phase 1b — Naming convention (LOCK before the first branch)

```
Establish the project's naming convention now and write it into AGENTS.md + CLAUDE.md. Use Appendix D
of this library as the template. Decide and record, one line each:
- The cycle vocabulary: ONE word per level (Program > Workstream > Step > Task). Ban synonyms
  (no Phase/Pass/Batch/Sitting for planning units); reserve any domain words (e.g. "Phase" for runtime
  stages only).
- Branch schema: <prefix>/<workstream><step>[-slug], allowed prefixes feat/chore/fix/claude.
- Version scheme (semver mapping to cycle units).
- Plan/doc file naming: docs/plans/YYYY-MM-DD-<workstream><step>-<slug>.md.
- Commit format: conventional commits; scope = module/doc category, NEVER a planning unit.
- Session/handoff title format.
Then, where cheap, ENFORCE it: a commit-msg hook validating the conventional-commit format, and a
branch-name check in CI. Naming cannot be retrofitted cheaply — every old branch/commit stays wrong forever.
```

> Why this is its own phase: in a real multi-session project, six interchangeable words accumulated for
> "a chunk of work" and one word ("Batch") came to mean three different things. No reader could decode a
> branch or commit without knowing its date. Fixing the vocabulary on day 0 is nearly free; fixing it later
> is impossible (history is immutable).

---

## Phase 2 — Enforcement layer (convert rules into laws)

This is the phase most projects skip and most regret. Three sub-prompts.

**2a. Static enforcement**
```
Set up blocking static checks for {{LANGUAGE}}:
- Formatter + linter with STRICT settings; strict type-checking (e.g. mypy --strict / tsc strict).
- CUSTOM lint rules for our AGENTS.md invariants, specifically:
  * ban `print`/console.log in app code (logging only)
  * ban raw datastore clients outside the designated data-access wrapper
  * a max-file-length rule for route/handler/module files (~400 lines)
  * require the single error envelope on API handlers
- A pre-commit hook running format + lint + types.
- A `scripts/verify.sh` that runs all static checks under a TYPED EXIT-CODE CONTRACT:
  * 0 = clean; 1 = safety violation (stop, do not commit); 2 = logic violation (fix before commit);
    3 = quality only (may commit with a noted justification).
- RATCHETING BASELINES: store known pre-existing violations in scripts/.baseline-*.txt so the gate
  blocks only NEW violations. This lets us start enforcing on a messy/inherited codebase today and
  ratchet the baseline down over time. Each baseline entry needs a one-line justification.
Show me each custom rule and how it's enforced before wiring it in.
```

**2d. Doc-freshness gate (prevents the "value drifts across docs" failure)**
```
Add a verify.sh check that diffs factual claims in docs against their canonical source in code:
any count/version/threshold a doc states must match the constant it comes from (e.g. a check-count
constant, the VERSION file, rule counts). Fail the gate on mismatch. Rule: a value that lives in code
is GENERATED into docs or OMITTED — never hand-copied into prose. (Real failure this prevents: the same
check-count was hard-coded as 12, 14, and 15 across four different docs while the code said otherwise.)
```

**2b. Claude Code hooks (`.claude/settings.json`)**
```
Configure .claude/settings.json hooks (see Appendix B for shape):
- A PreToolUse hook that hard-BLOCKS destructive/irreversible commands (schema-drop, prod-data
  delete, rm on the datastore file, force-push to main) and requires explicit confirmation.
- A SessionStart hook that prints orientation (current branch, project-status summary, how to run
  verify) so every cold session orients fast.
- A PreToolUse/PostToolUse hook that runs `scripts/verify.sh` (or a fast subset) before commits.
Explain each hook's matcher and command before writing the file.
```

**2c. CI gate**
```
Create a CI workflow that BLOCKS merge unless: format + lint + strict types + fast tests + the
domain-integrity check all pass. No path bypasses it. Add a fast/slow test split so CI can run a
quick subset on every push and the full suite on demand.
```

---

## Phase 3 — Skills to install & build

```
Set up the skills library:
1. Install the "superpowers" plugin (Claude Code plugin marketplace) for its curated workflow skills
   (brainstorming, planning, TDD, debugging, systematic verification). Confirm install and list what
   it added.
2. Then scaffold our OWN project skills in .claude/skills/, built from my prior experience:
   - lead-planner: plan → adversarial self-review → lock → dispatch cheap subagents → verify findings.
   - pre-development-gate: mandatory architecture/invariant-baseline read before touching critical-path code.
   - session-resume: re-establish context + plan at the START of a continued session (post break/compaction/handoff).
   - handover: write a HANDOVER.md specific enough for a zero-context resume.
   - session-close: verify + doc-sync + commit + handover ritual.
   - push-merge-deploy: pre-merge gate + merge + LIVE deploy verification (a push is not a deploy).
   - code-review: diff review for correctness + reuse/simplification at a chosen effort level.
   - <domain>-accuracy-verification: re-run the domain golden checks after any change that could
     affect the core invariant.
   (Ready-made SKILL.md files for the process skills are in new-repo-kit/skills/.)
Write each as a SKILL.md with a clear "when to use" trigger. Keep them short and composable.
```

> Note: tailor the custom skill set to the project. The five above are the load-bearing ones from Huinsight.

---

## Phase 4 — MCP servers

```
Configure the MCP servers we'll actually use, and document each in CLAUDE.md:
- Source control / issue tracker MCP (e.g. GitHub) — scoped to this repo only.
- Any domain data MCP relevant to the project (brokerage, market data, calendar, etc.).
For each: note the auth/secret it needs (via env, never committed), and the least-privilege scope.
Do NOT add MCPs we won't use — each one is attack surface and context noise.
```

---

## Phase 5 — Boundary contracts (before feature work)

> **Read [`new-repo-kit/database-design-guide.md`](new-repo-kit/database-design-guide.md) first** and
> write the DB-choice ADR — the database is the hardest decision to reverse, so orient before scaffolding
> the schema. The guide ends with a paste-ready ADR prompt and an anti-pattern checklist.

```
Establish the contracts every feature must conform to:
1. Datastore schema WITH constraints from the start — primary keys, foreign keys, CHECK constraints
   for domain enums/currency/ranges, and the indexes for the most common query (latest-per-entity).
   Enforce the core invariant at the schema layer, not just at runtime.
2. A single data-access wrapper; raw clients are lint-banned (Phase 2a).
3. A versioned, typed API: /v1 namespace, a response_model on every endpoint (so OpenAPI/SDK gen
   works), one error envelope, and pagination on every list endpoint.
4. A real auth/session model: random server-stored session tokens (never credentials-as-token),
   even if single-user today.
Write an ADR for each of these four decisions.
```

---

## Phase 6 — Test scaffolding

```
Before features, create the test backbone:
1. ONE golden integration test for the core domain: a frozen seed dataset + range/exact assertions
   on the numbers that matter and the core invariant. This is the most important test in the repo.
2. Fast/slow markers so a quick subset runs in CI — but the fast subset MUST still include critical-path
   tests. The default scope must never EXCLUDE a directory (sync/integration/e2e) where real regressions
   live; otherwise bugs merge clean and only full CI catches them. CI always runs the full scope.
3. A rule (AGENTS.md): test BEHAVIOR/outcomes, not mocks; no "negative-space" tests asserting code
   is absent; update tests when architecture changes deliberately — never revert architecture to pass
   stale tests.
4. A living `docs/known-issues.md` that maps each recurring failure class to the gate check that catches
   it, with a standing rule: if a class recurs, document the root cause and — when automatable — PROMOTE
   it from "Not Yet Automated" into verify.sh. Enforcement grows from real incidents, not speculation.
```

---

## Phase 7 — Dev harness

```
Create one dev harness script (dev.sh equivalent) that owns: start/stop/restart all services, port
and env/venv management, log tailing, `verify` (fast gate) and `verify --full` (all tests + integrity).
One command to run the whole system; one command to gate a commit.
```

---

## Phase 8 — Observability & portability seams

```
1. Structured logging from line one (no print): a logger config, request/operation IDs, and an error
   path that always surfaces failures (never fabricated success).
2. If ADR-000 lists any "possible later platform", stub the seams NOW:
   - a shared `core` package boundary for domain types + API client + pure formatters (no UI/platform
     code), so a second client (mobile/CLI) can reuse it.
   - http/storage adapter interfaces so the client isn't coupled to one platform's primitives.
   Don't build the second platform — just put the seam in.
```

---

## Appendix A — Seeded AGENTS.md rule starters

Domain-agnostic, earned the hard way. Number and specialize per project; each must map to an
enforcement mechanism or be tagged `[convention-only]`.

1. No `print`/`console.log` in application code — structured logging only. *(lint-enforced)*
2. Exactly one error envelope; never return fabricated success on failure (no `except → return []`
   at HTTP 200). *(lint + review)*
3. All datastore/external access goes through the designated wrapper; raw clients are banned. *(lint)*
4. Enforce domain invariants at the schema/type layer, not just at runtime. *(schema constraints + CI)*
5. File-size budget per module; split by domain when exceeded. *(lint)*
6. Auth uses random server-stored session tokens, never credentials-as-token. *(review + test)*
7. Every architectural decision gets an ADR before merge. *(review gate)*
8. Never run destructive/irreversible commands without an explicit confirmation gate. *(PreToolUse hook)*
9. One source of truth per concern (status, version, config); cross-link, don't duplicate. *(review)*
10. Every fixed incident produces a mechanical guard (hook/constraint/lint rule), not just a doc note.
11. Subagents always get an explicit cheap-model override; the Lead verifies their high-severity claims.
12. Architecture beats stale tests — update tests for deliberate changes; never revert architecture to
    pass old tests.
13. No value that lives in code is hand-copied into prose docs — generate it or omit it. *(doc-freshness gate)*
14. One naming convention for cycles/branches/versions/commits, fixed before the first branch. *(commit-msg + branch CI check)*
15. The fast test subset includes critical-path tests; the default scope never excludes a test dir. *(CI runs full scope)*
16. Recurring failure classes get promoted from the living issues-checklist into the static gate. *(feedback loop)*

---

## Appendix B — `.claude/settings.json` hook shape (illustrative)

```jsonc
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            // Block destructive ops: schema-drop, prod data delete, rm on the datastore, force-push to main.
            "command": "scripts/guard-destructive.sh"
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "scripts/session-orient.sh" }
        ]
      }
    ]
  }
}
```

`scripts/guard-destructive.sh` reads the proposed command on stdin, exits non-zero (blocking) on a
denylist match, and prints why. `scripts/session-orient.sh` prints branch, a project-status summary,
and the verify command. Keep both tiny and fast.

---

## Appendix C — Suggested order of operations (TL;DR)

```
0 charter+ADR-000  →  1 governance skeleton  →  1b naming convention (LOCK)  →  2 ENFORCEMENT (static+hooks+CI+freshness)  →
3 skills (superpowers + custom)  →  4 MCPs  →  5 boundary contracts (schema constraints,
versioned typed API, auth)  →  6 golden test + fast/slow split  →  7 dev harness  →
8 observability + portability seams  →  THEN feature work.
```

The whole point: by the time you write feature #1, the agent is already fenced in by machine-checked
gates that encode everything you learned the hard way — so the thousandth cold session can't quietly
undo the foundation.

---

## Appendix D — Naming convention reference card (fill in Phase 1b)

One scheme per dimension. Synonyms are banned, not discouraged.

**Cycle vocabulary — one word per level:**

| Level | Word | ID format | Example |
|-------|------|-----------|---------|
| Multi-branch initiative | Program | noun slug | "Data Layer Transformation" |
| Major track | Workstream | capital letter | A, B, C |
| Sequential chunk | Step | letter+number | A1, B2, C3 |
| Execution sub-unit | Task | decimal | C3.1, C3.2 |

Reserved (cannot be reused as planning words): `Phase` = runtime stages only · `Batch` = the merge
operation only. Retired permanently: `Pass`, `Sitting`, `Milestone`.

| Dimension | Convention | Example |
|-----------|-----------|---------|
| Branch | `<prefix>/<workstream><step>[-slug]`, prefix ∈ {feat, chore, fix, claude} | `feat/b1-config-reader-engine` |
| Version | semver: major=program · minor=shipped workstream/step · patch=hotfix only | `V6.2.0` |
| Plan file | `docs/plans/YYYY-MM-DD-<workstream><step>-<slug>.md` | `2026-06-10-c3-ibkr-coauthority.md` |
| Commit | `<type>(<scope>): <imperative>` — scope = module/doc category, NEVER a planning unit | `feat(readers): C3.3 — merged-ledger FIFO cost basis` |
| Session/handoff | `Session Handover: <version> — <one-line state>` | — |

Enforce the cheap parts: a commit-msg hook for the conventional-commit format, and a CI branch-name check.
The planning unit (e.g. `C3.3`) goes in the commit *description*, never the scope token.
