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

### Set a login password before the first start

The app requires a login. On its **first** boot it seeds a credential and never
asks again, so choose the password now rather than hunting for it later:

```bash
export UIS_AUTH_TOKEN='pick-something'
```

Any process you start from this shell inherits it, `./dev.sh start` included.
Set it **before** the first start: it is read only while no credential exists
yet, so exporting it afterwards changes nothing — the database is the source of
truth from that point on.

Setting this variable also switches the backend to its production routing, where
only `/api/*` is served. That is intentional (the auth middleware lets unprefixed
GETs through so the page shell can load, so an unprefixed API surface alongside a
token would be readable without one). The dev server forwards `/api` unchanged,
so both work; if you have an older checkout whose `vite.config.ts` rewrites the
prefix away, login will 404 through port 5003 while succeeding directly against
port 8008.

**If you already started the app without setting it**, a random 32-character
password was generated and written to the backend log exactly once:

```bash
grep FIRST_BOOT_CREDENTIAL .uis/backend.log
```

(`.uis/backend.log` is where `dev.sh` writes backend output; if you started
uvicorn by hand, it went to that terminal.) Lost it, and the fastest way back
is to reset the credential:

```bash
.venv/bin/python -c "
from src.database.connector import DatabaseConnector
DatabaseConnector().execute('TRUNCATE auth_credentials')
"
```

Then set `UIS_AUTH_TOKEN` and restart. This clears the login credential only —
it does not touch holdings, transactions or any other data.

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

You will land on a **login screen** — enter the password you set in step 3.
Behind it is a Dashboard showing net worth, holdings by source, and a populated
Compass / Performance / WealthOS suite — all real numbers, just for a persona
instead of you.

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
