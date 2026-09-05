# Contributing

Thanks for looking. This started as a single-user personal finance tool and
is now open-sourced as both — a working app you can run for your own
portfolio, and a reference for the patterns that held up under real use.
[`docs/design-retrospective.md`](docs/design-retrospective.md) is the honest
account of what worked and what's on the list to rebuild; that list **is**
the contribution roadmap — start there if you're looking for something
worth doing rather than a small fix.

## Before you start

- [`docs/quickstart.md`](docs/quickstart.md) gets you a running instance
  against synthetic demo data in about 10 minutes — no access to anyone's
  real portfolio needed to develop against this codebase. If you're editing
  it, run it — literally, on a clean checkout — before merging; this doc has
  broken twice from being reviewed rather than executed (a missing copy step
  the first time, an unstated Python-version requirement the second), and
  neither was catchable by reading.
- [`docs/adding-a-source.md`](docs/adding-a-source.md) if you're adding a
  new broker/asset-type reader — it's designed to need zero changes under
  `src/sources/`.
- [`AGENTS.md`](AGENTS.md) is the guardrail rule set this codebase enforces
  on itself (mostly aimed at AI coding agents, but the rules are the
  project's actual data-correctness invariants — worth reading regardless of
  who or what is writing the patch).

## Development loop

```bash
git clone https://github.com/SunnRayy/huinsight && cd huinsight
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd ux-command-center && npm install && cd ..

# Run the test suite (parallel by default via pytest-xdist)
.venv/bin/pytest tests/ -q

# The pre-commit gate — run this before every commit
bash scripts/verify.sh
```

`scripts/verify.sh` exits `0` (clean), `1` (a P0 database-safety violation —
these block unconditionally), `2` (a data-correctness/logic issue), or `3`
(a code-quality issue, e.g. a new lint violation not already in the
baseline). It's the same gate CI runs on every PR
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) — a red CI run
almost always means one of these four categories, and the script's own
output tells you which.

## Making a change

1. **Branch from `main`.** `deploy` is a release branch a maintainer pushes
   to — PRs never target it directly, and pushing to it is what triggers
   a real Cloud Run deployment, so it isn't something a contributor's PR
   should touch.
2. **Keep data-correctness changes on their own PR**, separate from
   refactors or doc updates. This project has been burned before by a
   pipeline behavior change riding along inside an unrelated cleanup PR —
   see `AGENTS.md`'s rule on this. If your change touches sync/ingest logic,
   say so explicitly in the PR description and reference which of the 16
   integrity checks (`src/validation/data_integrity_gate.py`) it interacts
   with, if any.
3. **Tests are not optional for pipeline/data-layer changes.** A behavior
   change to a reader, the shadow pipeline, cost-basis calculation, or
   anything under `src/sync/` needs a test that would have caught the bug
   you're fixing (or would break if your new behavior regressed). UI-only
   changes have a lighter bar but should still cover the logic, not just
   the rendering.
4. **Run `bash scripts/verify.sh` and the full test suite locally** before
   opening the PR. Both run in CI too, but catching it locally is faster
   for everyone.
5. **Commit messages**: this project doesn't enforce a strict format, but
   prefers `type(scope): summary` (`fix(sync): ...`, `feat(api): ...`,
   `docs: ...`) — matches the existing `git log`.

## Adding or improving a translation

Huinsight ships a bilingual UI (English / Simplified Chinese). The catalogs live
in `ux-command-center/src/i18n/locales/{en,zh-CN}/` — one JSON file per
namespace (e.g. `common.json`, `aiAdvisor.json`), mirrored between the two
locale directories with the same filenames and the same key structure.

- **Terminology comes from [`docs/i18n-glossary.md`](docs/i18n-glossary.md).**
  It's the canonical term list — if a term is already there, use exactly
  that translation; if it isn't, add a row in the same commit that first
  uses it. The glossary's governing principle: match the vocabulary already
  in the owner's data (spreadsheet columns, taxonomy names, prompt text),
  not a dictionary translation.
- **Add a key to both locales in the same commit.** A key present in `en`
  but missing (or empty) in `zh-CN`, or vice versa, is exactly what the
  check below catches.
- **`npm run i18n:check` must pass** before you open a PR touching either
  locale directory. It enforces: EN/zh-CN key parity across every
  namespace, no empty values, and no file referencing a namespace whose
  catalog doesn't exist.
- Run it from `ux-command-center/`:
  ```bash
  cd ux-command-center && npm run i18n:check
  ```

## What's a good first contribution

- Anything in `docs/design-retrospective.md`'s "what we'd rebuild" list —
  each item there is scoped as a real, standalone piece of work, not a
  vague direction.
- A new reader for a broker/asset type this project doesn't support —
  `docs/adding-a-source.md` walks through it, and it's genuinely
  achievable without touching the core engine.
- Non-CNY base-currency support — flagged as an honest limitation in the
  README; the architecture doesn't forbid it, nobody's built it yet.

## How this repository is published

This repo is **generated**, not developed in. Development happens in a private
repository, and each release copies an allowlisted subset here as a single
fresh commit. Two consequences that otherwise look like something has gone
wrong:

- **The history is one commit, rewritten on every release.** `Initial public
  release` is the only commit you will ever see, and its hash changes each
  time. The commit *count* carries no information — the commit *date* is the
  freshness signal. A SHA quoted in a changelog, an issue or a review may be a
  private one, and it will not resolve here, ever.
- **Some referenced files genuinely do not exist here.** The private repo's
  status tracker, handover notes, known-issues log and planning documents are
  outside the export allowlist by design. If a document you have been pointed
  at 404s, that is the expected outcome for those paths rather than a broken
  link — but everything needed to *build, run, test and change the software*
  is here, and a missing file in that category is a real bug worth reporting.

The practical ask: when you cite a commit or a path in an issue or review, say
which repository you are looking at. Both use `main`, and the ambiguity has
already cost two review cycles.

## Reporting a bug

Open an issue with: what you expected, what happened, and — if it's a
data-correctness issue — which of the 16 integrity checks (if any) flagged
it, or would have. `python main.py --check-integrity --json` is the
fastest way to get a structured answer to that question.

## Security issues

Do not open a public issue for a security vulnerability — see
[`SECURITY.md`](SECURITY.md).
