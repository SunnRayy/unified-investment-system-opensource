# Quickstart

Clone → generate demo data → sync → see a populated dashboard. No access to
anyone's real financial data required — every command below runs against a
synthetic persona (see [`tools/demo_data/persona.yaml`](../tools/demo_data/persona.yaml)),
deterministically generated from a fixed seed.

Tested end-to-end on a fresh checkout; expect ~10 minutes total, most of it
the first sync's live price fetch.

## 1. Clone and install

Requires **Python 3.9–3.13** (tested on 3.9.6 and 3.13.7 — `requirements.txt`
uses environment markers to pick validated dependency versions for each).
Check your default `python3` before creating the venv:

```bash
python3 --version   # must be 3.9–3.13; if not, point at a specific interpreter,
                     # e.g. python3.12 -m venv .venv
```

```bash
git clone https://github.com/SunnRayy/huinsight
cd huinsight

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd ux-command-center && npm install && cd ..
```

## 2. Generate demo data

```bash
.venv/bin/python tools/demo_data/generate.py
```

This writes 9 synthetic source files under `tools/demo_data/out/` — Schwab
CSV positions + transactions, IBKR Flex reports, and Excel workbooks for CN
Fund, Gold, Insurance, RSU, and the Financial Summary. Same seed, same
output, every time (`tools/demo_data/generate.py --help` for `--out-dir` /
`--persona` overrides).

**Check before moving on** — if the previous command errored (missing
dependency, permission issue), the copy step below fails with a confusing
"no matches found" instead of the real cause. Confirm 9 files exist first:

```bash
find tools/demo_data/out -type f | wc -l   # expect 9
```

If that doesn't say 9, re-run the generator and read its output before
continuing — don't proceed to the copy step below on a partial/failed run.

Collect everything into one flat folder — the two IBKR files land in
subdirectories (`out/ibkr/`, `out/ibkr_trades/`) because that's how the
reader's own unit-test fixtures are organized; a real `data_dir` expects them
flat alongside everything else, so the same reader can pick the newer of the
two:

```bash
mkdir -p data/import
cp tools/demo_data/out/*.csv tools/demo_data/out/*.xlsx data/import/
cp tools/demo_data/out/ibkr/*.csv tools/demo_data/out/ibkr_trades/*.csv data/import/
```

(zsh users: an unmatched glob here — e.g. if the check above was skipped and
`out/` is actually empty — aborts the whole pasted block with "no matches
found" rather than just that one command, unlike bash. The check above exists
specifically so you hit a clear error before this step, not a confusing one
during it.)

## 3. Configure

```bash
cp config/settings.example.yaml config/settings.yaml
```

No editing needed for the demo — the example config's `finance_dir` already
points at `./data/import`, matching step 2, and every reader is enabled by
default (correct for the demo set, which has all 7). Pointing this at your
own real data later is a one-line path change; see "What's next" below.

## 4. Initialize the database and sync

```bash
.venv/bin/python main.py --init
.venv/bin/python main.py --sync-v3
```

`--init` creates `data/unified.duckdb` and applies the full schema — safe to
run against a path that doesn't exist yet, which is the normal case for a
fresh clone. `--sync-v3` reads all 7 demo sources, runs the full pipeline
(reader ingest → live price refresh → shadow/staleness → FIFO cost basis →
authority resolution → the 16-check integrity gate), and prints a summary.

The live price refresh step fetches real quotes from yfinance/akshare for
every demo holding — this is the slow part (a few minutes) and needs network
access. A single provider hiccup (an akshare timeout, say) is logged and
skipped, not fatal; the sync still completes.

You should see something like:

```
✅ Sync complete: 208 holdings, 346 transactions synced
Integrity gate: 14/16 passed (or 16/16 — see below)
```

(`unmatched_security_transfer` may show as an advisory finding on the demo
dataset specifically — a known, harmless characteristic of the synthetic
transfer history, not a bug in your setup. `--check-integrity --json` also
works if you want a machine-readable rerun without a full sync.)

## 5. Run the app

```bash
./dev.sh start
```

Starts the backend (port 8008) and frontend (port 5003), opens your browser.
You should land on a Dashboard showing net worth, holdings by source, and a
populated Compass / Performance / WealthOS suite — all real numbers, just
for a persona instead of you.

Manual alternative if you'd rather not use `dev.sh`:

```bash
# Terminal 1
.venv/bin/python -m uvicorn src.api.main:app --reload --port 8008
# Terminal 2
cd ux-command-center && npm run dev
```

## What's next

- **Point it at your own data**: replace the demo files in `data/import/`
  with your real exports (same filenames/formats — see
  `config/readers/*.yaml` for what each reader expects) and re-sync. Nothing
  else changes.
- **Add a source Huinsight doesn't support yet**: [`docs/adding-a-source.md`](adding-a-source.md) —
  no code changes required, two files outside `src/sources/`.
- **Deploy it somewhere always-on**: [`docs/deployment-instructions.md`](deployment-instructions.md)
  (Google Cloud Run + GitHub Actions).
- **Keep it running long-term**: [`docs/operations.md`](operations.md) — DuckDB
  compaction, backup pruning, why both exist.
- **Understand the shape of the codebase and what we'd change knowing what
  we know now**: [`docs/design-retrospective.md`](design-retrospective.md).
