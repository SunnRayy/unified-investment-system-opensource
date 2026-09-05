# Product Development Practices — Portable Playbook

> Companion to [`ai-engineering-foundations.md`](ai-engineering-foundations.md). That doc covers what's
> *specific* to building with AI agents. This one covers the **general software-engineering hygiene** a
> solo/"vibe" coder is most likely to skip — git workflow, PRs/issues, worktrees, CI/CD, feature flags,
> versioning. Researched against current (2025–2026) industry practice; sources at the bottom.

The mental shift: you are not just "writing code that works", you are running a **product development
process**. The process is what lets a one-person (+ agents) shop move fast without breaking things.

---

## 1. Branching: short-lived branches off a green main (GitHub Flow / Trunk-Based)

For a solo dev + agents, **don't** use heavy GitFlow (develop/release/hotfix branches). Use the
lightweight model the high-velocity teams use:

- **main is always releasable and always green.** Nothing merges to main red.
- **Short-lived branches** — hours to a couple of days, not weeks. Long branches = painful merges and
  divergence. Pull main into your branch frequently; merge back fast.
- One branch = one logical change (one Step/Task in the naming convention). Branch name encodes type +
  the planning unit: `feat/b1-config-reader`, `fix/a3-fx-rounding`.
- Incomplete work that has to land on main hides behind a **feature flag** (see §5), not a long branch.

This is "trunk-based development": continuous integration of small changes, used by Google/Meta and most
fast teams. The discipline that makes it safe is **CI must pass before merge** and **nobody ships
untested code because main is live**.

---

## 2. Pull requests — even when you're solo

A PR is not bureaucracy; for a solo dev it's three things at once: **the CI gate, the audit trail, and
the place review comments (human or AI) live.** Use one for every non-trivial change.

- **Keep PRs small and focused — target under ~400 changed lines.** Small PRs get better review (from a
  human, an AI reviewer, or future-you) and revert cleanly. This mirrors the file-size budget.
- **Use a PR template** (`.github/pull_request_template.md`) that forces: *what* changed, *why*, *how to
  test*, and a checklist (tests pass, docs updated, breaking changes noted). The template is enforcement —
  it makes you answer the questions you'd otherwise skip.
- **The body explains *why*; the diff shows *what*.** Don't narrate the diff; capture the reasoning,
  alternatives considered, and non-obvious consequences (same as a good commit body).
- **Self-review before you ask anyone (or merge).** Read your own diff top to bottom as if it were
  someone else's. You will catch real bugs.
- **Let an AI review the diff** (`/code-review` or the platform's reviewer) on critical-path or large
  PRs — cheap second pair of eyes.

---

## 3. Issues — your externalized backlog and decision log

- **One issue per unit of intended work or bug.** Issues are where "I should do X later" goes — not a
  TODO buried in code, not your memory.
- **Link PRs to issues with closing keywords** (`Closes #42`) so the issue auto-closes on merge and the
  history ties change ↔ rationale.
- **Labels as a lightweight system**: `type:bug|feat|chore`, `prio:p0..p3`, `area:<module>`. Enough to
  filter, not so many you stop using them.
- Use issues (or a pinned milestone) to represent the **Workstream/Step** structure from the naming
  convention — so the planning hierarchy is visible on GitHub, not just in `docs/plans/`.

---

## 4. Git worktrees — the right primitive for parallel agent work

When you run multiple agents (or work on two features at once), **don't** juggle `git stash` and branch
switches in one directory. Use **worktrees**: each gets its own working directory + index, sharing one
object store.

- `git worktree add ../repo-feat-b1 feat/b1-config-reader` → an isolated checkout. One agent/feature per
  worktree → no file-level conflicts, no lock contention, no context contamination between agents.
- **Decompose by domain/feature boundary.** Don't split work that touches the same files from different
  directions — that just moves the conflict to merge time.
- A **shared task doc** (a markdown checklist all agents read) coordinates who's doing what: pick up →
  mark in-progress → mark done. Prevents duplicated effort.
- For larger parallel efforts, merge all the branches into an **integration branch**, test there, then
  promote the clean result to main — main stays green throughout.
- Clean up with `git worktree remove` when done. (Modern editors added first-class worktree support in
  2025–2026; a worktree often needs its own dependency install since it's a separate directory.)

> Reported results from worktree+agent workflows are large (one case: 30h → 8.4h with four worktrees) —
> but the win comes from *good decomposition*, not the tool itself.

---

## 5. Feature flags — decouple deploy from release

The enabler for trunk-based development. A flag gates an incomplete or risky code path so you can **merge
continuously without exposing unfinished functionality.**

- Start trivially: a config/env boolean or a simple `flags.py`/`flags.ts` map. You don't need a SaaS.
- Lets you ship small increments behind `if flag.enabled("new_x")`, turn features on per-environment, and
  kill a bad feature without a revert+redeploy.
- **Pay down flag debt:** remove a flag once the feature is fully on. Stale flags become their own mess.

---

## 6. CI/CD — fast feedback on every push

- **CI on every push/PR:** lint + types + tests + the project's domain gate, all blocking. This is the
  same enforcement layer the foundations playbook insists on, wired to the platform.
- **Branch protection on main:** require passing CI before merge; for solo work you can skip required
  approvals, but never skip required checks. (Add required human/AI review the moment a second person —
  or a high-risk area — is involved.)
- **CD with verification:** automating the deploy is good; automating the *verification* of the deploy is
  the part people skip. A deploy that wasn't smoke-tested against the live service is unverified. (See the
  `push-merge-deploy` skill.)

---

## 7. Conventional commits → automated semver

- **Conventional commits** (`feat:`, `fix:`, `chore:`…) aren't just tidy — they map mechanically to
  **semantic versioning**: `feat` → minor bump, `fix` → patch, `BREAKING CHANGE` → major. Tools can then
  generate the changelog and the version tag for you.
- Enforce the format with a `commit-msg` hook (see the kit's `commit-msg.sh`) so it can't drift.
- Tag every release `vMAJOR.MINOR.PATCH`. Map versions to the planning hierarchy (major = Program
  boundary, minor = shipped Workstream/Step, patch = hotfix) — consistent with the naming convention.

---

## 8. The solo-vibe-coder priority order

If you adopt these incrementally, this order gives the most safety per unit of effort:

1. main always green + short-lived branches + CI-before-merge (the core loop).
2. PR template + small PRs + self-review (quality gate that costs almost nothing).
3. Issues as the backlog + closing-keyword links (you stop losing work).
4. Conventional commits + a commit-msg hook (free, compounds into changelog/versioning).
5. Worktrees once you run parallel agents (throughput).
6. Feature flags once a change is too big to land safely in one short branch.
7. CD with live verification once deploys are frequent enough to automate.

The through-line with the AI foundations playbook: **make the process mechanical.** A PR template, a
branch-protection rule, and a commit-msg hook are enforcement — they hold when discipline doesn't.

---

## Sources

- [Git Worktrees for Parallel AI Agent Execution — Augment Code](https://www.augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution)
- [Git Worktrees for AI Coding — MindStudio](https://www.mindstudio.ai/blog/git-worktrees-parallel-ai-coding-agents)
- [Using Git Worktrees for Multi-Feature Development with AI Agents — Nick Mitchinson](https://www.nrmitchi.com/2025/10/using-git-worktrees-for-multi-feature-development-with-ai-agents/)
- [Trunk-Based Pull Request Workflow — WSBC Technical Blog](https://wsbctechnicalblog.github.io/engineering-practices-pull-request-v2.html)
- [The Perfect Pull Request: Best Practices — DeployHQ](https://www.deployhq.com/blog/the-perfect-pull-request-best-practices-for-collaborative-development)
- [GitHub Flow — GitHub Docs](https://guides.github.com/introduction/flow/)
- [Conventional Commits Guide: Rules, Tools and CI/CD Enforcement — DeployHQ](https://www.deployhq.com/blog/conventional-commits-a-standardized-approach-to-commit-messages)
- [Feature Flags 101 — LaunchDarkly](https://launchdarkly.com/blog/what-are-feature-flags/)
- [11 Software Development Best Practices in 2026 — Netguru](https://www.netguru.com/blog/best-software-development-practices)
