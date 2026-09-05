# AI-Engineering Foundations — Portable Playbook

> A carry-over document. Distilled from running the Huinsight (and prior
> "vibe-code" projects) across thousands of AI-agent sessions. Project-agnostic on purpose —
> copy it into any new repo as `docs/playbooks/ai-engineering-foundations.md` and treat it as
> the "underground foundation" beneath your architecture.
>
> **Companions:** [`product-development-practices.md`](product-development-practices.md) (general git/PR/
> issues/worktrees/CI hygiene) and [`new-repo-kit/`](new-repo-kit/) (the actual templates, hooks, and
> skills to install — including `new-repo-kit/database-design-guide.md` for the schema decision).

---

## 0. The one law

> **With AI agents, any invariant that isn't mechanically enforced will eventually be violated.
> Conventions decay; enforcement holds.**

Every AI session is a *cold context*. A rule that lives only in a doc is followed by the sessions
that read it carefully and silently broken by the ones that don't. Over hundreds of sessions the
violation rate compounds. This single fact explains ~90% of the structural debt I have ever found
in an AI-built codebase.

Corollary: **conventions are for humans; enforcement is for agents.** Write both, but never rely on
the convention alone to hold a property you actually care about.

**Second corollary (the drift law):** *any fact copied into more than one place will diverge.* A value
that lives in code (a count, a version, a threshold) must be **generated into docs or left out of them —
never hand-copied into prose.** This was observed live in a mature repo: the canonical integrity-check
count lives in `INTEGRITY_CHECK_COUNT`, the code comment literally says "never hard-code 14, import it" —
and yet four separate docs hard-coded **12, 14, and 15**. The rule count drifted (a doc said "21" when the
ruleset had 23); module line-counts in an issues doc were ~1,000 lines stale. SOPs decay exactly like code.

---

## 1. What to PERSIST (these transfer 100%)

The most valuable asset from an AI-built project is **not the code** — it's the operating manual for
working with agents. Carry these:

1. **The governance skeleton.** `CLAUDE.md` (how to orient) + `AGENTS.md` (the rules) + an ADR
   directory + a skills library + HANDOVER / session-close rituals. Pre-seed it with hard-won rules
   so a new project front-loads scar tissue instead of re-earning it.

2. **ADRs as the coherence mechanism.** Decision records are *the* reason a codebase stays legible
   across many sessions — each session can reconstruct *why*, not just *what*. Non-negotiable for any
   multi-session AI project. One ADR per real architectural decision; keep a template.

3. **Invariants encoded as runnable checks.** Define your domain's "must always be true" statements
   and make them executable in CI (the Huinsight integrity-gate pattern). This is how a system
   survives constant AI edits.

4. **One golden test for the core domain, written first.** A frozen dataset + range assertions on the
   numbers that matter beats a thousand mock tests. Write it before features.

5. **Incident → encoded prevention reflex.** Every incident becomes a *mechanical* guard (a hook, a
   constraint, a lint rule), not a doc paragraph. (Huinsight's DB-safety hooks exist because the DB got
   wiped once.) This habit is the best one to keep.

6. **The lead-planner + cheap-subagent dispatch model.** Frontier model for planning/judgment/review;
   cheap models for search and mechanical work. Always: the Lead *verifies* subagent findings — they
   are confidently wrong ~15–20% of the time.

7. **A single dev harness** (`dev.sh` equivalent) owning servers/ports/env/logs/verify, and a
   **contract-first flow** (spec → backend → frontend) so UI is never built against an imagined API.

---

## 2. What to AVOID / IMPROVE (failure patterns + the fix)

Each row is something a real AI-built project *did*; the fix is the day-0 countermeasure.

| Failure pattern (the symptom) | Root cause (how vibe-coding accretes it) | Countermeasure from day 0 |
|---|---|---|
| Integrity left to runtime checks; schema has no constraints | "Boring" foundational constraints get deferred; the runtime gate feels like enough | Enforce invariants at the **lowest layer possible** — DB constraints + strict types *first*, runtime gate as defense-in-depth |
| Dozens of raw DB connections bypassing the abstraction | The abstraction exists but the raw call is *easy to reach* | Make the wrong path **hard**: lint-ban direct access; the only door is the wrapper |
| Route/module files growing to 1,500–2,000 lines | Agents **append to existing files** rather than create modules | File-size + module budgets **as a lint rule** ("no route file > ~400 lines") |
| Parallel duplicate implementations of the same concept | Agents don't discover existing code → build their own | Make **discoverability first-class**: a "where does X live" index, strong naming, DRY-check in review |
| `print()` + fabricated success on error (silent zeros) | Vibe-coding optimizes for "it runs," not "it fails loudly" | **Structured logging + one error contract from line 1.** Ban `print`. A failure must look like a failure to every consumer |
| Plaintext or shortcut auth for a "single-user tool" | Security treated as afterthought | Minimal-but-correct **session-token / auth contract upfront** — single-user tools silently become multi-platform |
| Hardcoded paths, browser-coupled frontend, no service layer | Portability seams deferred until "needed" | **Stub the seams early** (storage/http adapters, service layer, shared `core` package). Cheap now, brutal to retrofit |
| Rules written reactively, after the violation | Governance grows as scar tissue | **Front-load known rules** — you already have them; seed the new repo on day one |
| Doc/changelog sprawl, duplicate handover files | Doc hygiene never enforced | One **source of truth per concern**; scheduled archive/rotation |
| Same value (counts/versions) stated differently across 4 docs | Facts hand-copied into prose; no regeneration | **Generate or omit** — a doc-freshness gate that diffs claimed values against the canonical code constant |
| Fast/default test scope silently *excludes* the tests that catch real bugs | "Make the quick suite quick" by dropping slow critical-path tests | Fast subset is for **speed, not hiding**; it must still include critical-path tests, and CI runs the full scope |
| Six words (Phase/Pass/Workstream/Batch/Step/Sitting) for one concept; one word ("Batch") for three | Each cycle invents its own vocabulary | **One naming convention, fixed on day 0** (see §6) — enforced in branch/commit/plan lint |
| Governance docs themselves go stale (rule counts, line counts, module paths) | SOPs are never re-verified | Treat SOP docs as **code under a freshness gate**; cross-check their factual claims in CI |

The meta-pattern across the whole right column: **move correctness from prose to machine.**

---

## 3. The Day-0 "underground foundation" checklist

Order matters — each item is something expensive to retrofit. Do these *before* the first feature.

1. **Governance skeleton** — `CLAUDE.md` + seeded `AGENTS.md` + ADR template + skills dir.
2. **Enforcement layer (the most under-invested part)** — strict type-checking, a linter with *custom*
   rules (file-size caps, banned imports/`print`, required error envelope), formatter, pre-commit hook,
   and a CI gate that **blocks**. This is where conventions become laws.
3. **Boundary contracts** — schema *with* constraints; a versioned, typed, response-modeled API with an
   OpenAPI spec; a single error envelope; a real (even minimal) auth/session model.
4. **One golden domain test + a fast/slow test split** so a quick CI subset is always runnable.
5. **Observability from line 1** — structured logging, so "silent zeros" can't exist.
6. **Dev harness + verify gate** (the one-command `dev.sh` equivalent).
7. **Portability seams** — data-access layer, http/storage adapters, and a shared `core` boundary *if*
   multi-platform is even possible.

---

## 4. The agent-workflow loop that actually works

```
Lead (frontier model): plan → adversarial self-review → lock blueprint
   ↓ dispatch with explicit model override
Subagents (cheap): Haiku = search/inventory/mechanical · Sonnet = implementation/tests/debug
   ↓ report back
Lead: VERIFY findings against source (reject false positives) → review diff → integrity gate
   ↓
Gates: lint + types + tests + domain-integrity check, all blocking
   ↓
Session-close ritual: update status/handover, commit, push
```

Rules that keep it cheap and correct:
- **Never spawn a subagent without an explicit cheap-model override.** A Haiku-sized search at
  frontier prices is the most common waste.
- **The Lead never rubber-stamps subagent output.** Verify the high-severity claims yourself.
- **Architecture beats stale tests.** If a deliberate change breaks tests, update the tests — never
  revert the architecture to make old tests pass.
- **Escalate, don't guess**, on: anything touching money math, schema changes, auth, or step-ordering.

---

## 5. The portable rule starters (seed your AGENTS.md with these)

Domain-agnostic rules earned the hard way; specialize per project:

1. No `print` in application code — structured logging only.
2. Exactly one error envelope; never return fabricated success on failure (no `except → return []` HTTP 200).
3. All external/DB access goes through the designated wrapper; raw clients are lint-banned.
4. Enforce domain invariants at the schema/type layer, not just at runtime.
5. File-size budget per module; split by domain when exceeded.
6. Auth uses random server-stored session tokens, never credentials-as-token.
7. Every architectural decision gets an ADR before it's merged.
8. Never run destructive/irreversible commands without an explicit confirmation gate (and a hook to back it).
9. One source of truth per concern (status, version, config); cross-link, don't duplicate.
10. Every fixed incident produces a mechanical guard, not just a doc note.
11. No value that lives in code is hand-copied into prose docs — generate it or omit it.
12. One naming convention for cycles/branches/versions/commits, fixed before the first branch (§6).
13. The fast test subset must include critical-path tests; CI always runs the full scope.
14. Recurring failure classes get promoted from a living issues-checklist into the static gate.

---

## 6. Naming convention SOP (fix this on day 0 — it cannot be retrofitted cheaply)

The single most preventable source of long-term confusion. A mature repo accumulated **six interchangeable
words** for "a chunk of work" (Phase, Pass, Workstream, Batch, Step, Sitting) and **three meanings** for
"Batch" — so no one could tell what a commit or branch referred to without knowing its date. Pick one scheme
and lint it.

**Cycle vocabulary — one word per level, synonyms banned:**

| Level | Word | Identifier | Example |
|-------|------|-----------|---------|
| Multi-branch initiative | **Program** | noun slug | "Data Layer Transformation" |
| Major track | **Workstream** | capital letter | A, B, C |
| Sequential chunk | **Step** | letter+number | A1, B2, C3 |
| Execution sub-unit | **Task** | decimal | C3.1, C3.2 |

Reserve domain words so they can't double as planning words: e.g. "Phase" = *runtime* pipeline stages only;
"Batch" = the *merge operation* only. Retire ad-hoc words ("Pass", "Sitting", "Milestone") permanently.

**Branch:** `<prefix>/<workstream><step>[-<slug>]` — prefixes `feat/ chore/ fix/ claude/`. Never embed the
program name (implicit from the workstream letter). e.g. `feat/b1-config-reader-engine`.

**Version (semver):** major = program boundary; minor = each shipped Workstream/Step; patch = hotfix only
(never docs-only). Keep one tag format.

**Plan files:** `docs/plans/YYYY-MM-DD-<workstream><step>-<slug>.md`; archive on step close.

**Commits (strict conventional):** `<type>(<scope>): <imperative>` — scope = a **module or doc category**
(`readers`, `api`, `handover`), never a planning unit. The planning unit (e.g. C3.3) goes in the description
body, not the scope token. No bare `merge`/`spec`, no capitalized starts.

**Sessions/handoff:** one title format (`Session Handover: <version> — <one-line state>`); agent branches
carry the step they belong to, not a random slug alone.

---

## 7. Earned lessons from a mature AI codebase (don't relearn these the hard way)

1. **The exit-code gate is the enforcement exemplar.** A single `verify.sh` with a *typed exit-code contract*
   (1 = safety violation → stop; 2 = logic violation → fix before commit; 3 = quality → may commit with note)
   plus per-check grep rules is the highest-ROI enforcement artifact. Build it early; it's how prose rules
   become laws.
2. **Ratcheting baselines let you add enforcement to an existing codebase without a big-bang cleanup.** Store
   known pre-existing violations in `.baseline-*.txt` files; the gate blocks only *new* violations and you
   ratchet the baseline down over time. This is how you start enforcing on a messy repo *today*.
3. **Beware the masking scope.** If your default/fast test run excludes a directory (e.g. `tests/sync`,
   `tests/integration`), bugs in it merge clean and only full CI catches them — and it will recur every time
   the gap exists. Make the fast subset a true *subset of signal*, not a way to skip the hard tests.
4. **Run a living issues-checklist with a promotion ritual.** Keep a `known-issues.md` that maps each recurring
   failure class to the automated check that catches it, with a standing rule: *if a class recurs, document the
   root cause and, when automatable, promote it from "Not Yet Automated" into the gate.* Enforcement grows from
   real incidents, not speculation.
5. **Your governance docs are code.** Onboarding/SOP docs drift (stale rule counts, stale module sizes, retired
   paths) and a stale SOP misleads every cold session. Put their factual claims under the same freshness gate
   as everything else.
6. **Canonical doctrine, stated once:** *"Nothing may report success it cannot prove — verify outcomes, not
   process."* Treat `success=True`, a green exit code, and a missing exception as **provisional** until you've
   inspected the actual result (the numbers, the rendered UI, the structured step statuses).
