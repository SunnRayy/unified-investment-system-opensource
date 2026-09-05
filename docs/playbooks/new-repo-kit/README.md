# New-Repo Bootstrap Kit

Drop-in templates, hooks, and skills that turn the principles in
[`../ai-engineering-foundations.md`](../ai-engineering-foundations.md) and the prompts in
[`../new-repo-setup-prompt-library.md`](../new-repo-setup-prompt-library.md) into **files you actually
install**. The playbooks tell you *why*; this kit gives you the *what*.

> Core law (from the playbook): *any invariant that isn't mechanically enforced will eventually be
> violated by a cold session.* Everything here exists to make the foundation mechanical.

## What's in here

```
new-repo-kit/
├── README.md                     ← you are here
├── database-design-guide.md      ← the highest-stakes early decision — read before Phase 5
├── templates/                    ← copy to repo root / docs, fill the {{PLACEHOLDERS}}
│   ├── AGENTS.md.template         ← seeded non-negotiable rules + verification protocol
│   ├── CLAUDE.md.template         ← agent orientation (overview, commands, modules, pointers)
│   ├── ADR.template.md            ← one architecture decision record
│   ├── HANDOVER.template.md       ← end-of-session handoff
│   ├── known-issues.template.md   ← living failure-pattern → automated-check checklist
│   ├── CHANGELOG.template.md      ← Keep-a-Changelog + semver mapping
│   ├── project-status.template.md ← single source of truth for current state
│   └── task-context.template.md   ← Completed / Remaining / Blockers
├── hooks/                         ← executable enforcement
│   ├── verify.sh                  ← typed exit-code gate (1/2/3) + ratcheting baselines
│   ├── guard-destructive.sh       ← PreToolUse: block destructive/irreversible commands
│   ├── session-orient.sh          ← SessionStart: orient every cold session
│   ├── commit-msg.sh              ← conventional-commit + naming enforcement
│   ├── check-doc-freshness.sh     ← diff doc-claimed counts vs canonical code constants
│   └── settings.json              ← .claude/settings.json wiring for the hooks
└── skills/                        ← portable, project-agnostic SKILL.md files
    ├── lead-planner/SKILL.md       ← plan → self-review → delegate → verify
    ├── pre-development-gate/SKILL.md ← architecture read before critical-path changes
    ├── session-resume/SKILL.md     ← re-establish context at the START of a continued session
    ├── handover/SKILL.md           ← write a zero-context-needed HANDOVER.md
    ├── session-close/SKILL.md      ← end-of-session verify + doc-sync + commit ritual
    └── push-merge-deploy/SKILL.md  ← pre-merge gate + merge + LIVE deploy verification
```

> Companion reading (in the parent `playbooks/` dir, not copied per-repo):
> [`ai-engineering-foundations.md`](../ai-engineering-foundations.md) (why) and
> [`product-development-practices.md`](../product-development-practices.md) (git/PR/issues/worktrees/CI).

## Install order (maps to the prompt-library phases)

1. **Templates → repo.** Copy `templates/*` to their homes, strip `.template`, fill `{{PLACEHOLDERS}}`:
   - `AGENTS.md`, `CLAUDE.md`, `CHANGELOG.md` → repo root
   - `HANDOVER.md`, `task-context.md` → repo root
   - `docs/decisions/template.md` ← `ADR.template.md` (and write `ADR-000-charter.md` first)
   - `docs/known-issues.md`, `docs/project-status.md`
2. **Hooks → `.claude/` + `scripts/`.** Copy `hooks/verify.sh`, `guard-destructive.sh`,
   `session-orient.sh`, `commit-msg.sh`, `check-doc-freshness.sh` into `scripts/`; `chmod +x` them;
   merge `hooks/settings.json` into `.claude/settings.json`; symlink `commit-msg.sh` →
   `.git/hooks/commit-msg`.
3. **Skills → `.claude/skills/`.** Copy `skills/*`. Then install the community **superpowers** plugin
   for general workflow skills (brainstorming, TDD, debugging) and keep these three as the project's
   own load-bearing process skills.
4. **Before designing the schema, read [`database-design-guide.md`](database-design-guide.md)** and write
   the DB-choice ADR (the guide ends with the exact prompt). The database is the hardest decision to
   reverse later — orient before you commit to it.
5. **Run `bash scripts/verify.sh`** — it should exit 0 on an empty repo. Now write feature #1.

## Conventions baked in

- **Naming:** Program › Workstream › Step › Task (see the playbook §6 / Appendix D). Synonyms banned.
- **Enforcement contract:** `verify.sh` exit 0 clean · 1 safety (STOP) · 2 logic (fix before commit) ·
  3 quality (commit with note).
- **Drift control:** no value that lives in code is hand-copied into prose; `check-doc-freshness.sh`
  enforces it.

Everything here is a **starting point** — specialize the `{{PLACEHOLDERS}}` and the grep patterns in
`verify.sh` to your stack. The structure is the value; the specifics are yours to fill.
