# AGENTS.md — Agent Instructions

Read this before any task that touches data pipelines, financial calculations, or the DuckDB
database. These are project facts and contracts, not style preferences — most of them cannot be
inferred from the code, and each one is here because getting it wrong has already cost a session.

Written for Claude Code. Codex is opt-in only (the human asks for it explicitly); Antigravity is
no longer part of this workflow.

> **Rule numbers are stable, not contiguous.** Numbers are cited from ADRs and code
> comments, so retired rules leave gaps rather than renumbering the rest.
> **Retired 2026-07-28:** Rules 8, 9, 14 and 15 merged into **Rule 1** (verify outcomes) and
> **Rule 13** (pipeline change protocol); Rule 16 (cross-agent review) deleted with Antigravity,
> its unique merge triggers folded into **Rule 18**.

---

## ⚡ Core Doctrine: Nothing May Report Success It Cannot Prove

**This is the foundational rule from which all others derive.**

An agent that reports "done" on the basis of a green exit code, a missing exception, or
a `success=True` field it cannot inspect is operating on a false signal. This project has
a history of silent failures — sync returning `success=True` with failed steps, API
endpoints returning `[]` on a crash, migrations swallowing errors with `pass`. Every one
of those was an agent-trust failure, not a code bug.

**Corollary rules for all agents:**
1. **Verify outcomes, not process.** Confirming that code ran ≠ confirming it worked. Use
   `--check-integrity --json` + a non-zero exit check; read `SyncResult.steps` for
   `status="failed"` entries; confirm UI renders data (not blanks) after API changes.
2. **Treat `success=True` as provisional.** Check `degraded` and `steps` for non-critical
   failures; check `integrity_checks_passed == integrity_checks_total`.
3. **Surface errors immediately.** Never swallow exceptions with bare `except: pass` or
   `return []`/`return {}` on failure. Use `logger.exception(...)` and return a structured
   error envelope (HTTP 5xx + `{"error": {...}}`). If you cannot surface an error, record
   it in `result.steps` as `status="failed"`.
4. **Make verification commands machine-readable.** Prefer `--json` flags, structured exit
   codes, and `/health/deep` over parsing prose output. Agents should be able to verify
   themselves without human interpretation.

See also: Rule 12 (API error contract), Rule 15 (post-development gate), `--check-integrity --json`.

---

## Rule 1: Never Trust Row Counts Alone

After any sync, DB mutation, or pipeline change, verify **financial outcomes** — not just row counts.
The test suite validates plumbing (SQL syntax, signatures, column names); it does not know whether
net worth should be ¥5.4M or ¥303K.

```
Bad:   "Inserted 38 holdings rows" -> done
Good:  "Net worth before: 5.37M, after: 5.38M (+0.2%), holdings 38→38" -> sanity checked
```

Every data-touching change reports that before/after line. The sync diff report
(`python main.py --sync-v3`) produces it automatically.

Run `python main.py --check-integrity` after any data pipeline change — 16 invariant checks that
flag impossible values. The count is canonical: `INTEGRITY_CHECK_COUNT` in
`src/validation/data_integrity_gate.py` — never hard-code it. `--json` gives machine-readable
output and a non-zero exit; `GET /integrity/status` is the API equivalent.

---

## Rule 2: Currency Convention

**ALL `market_value` in the `holdings` table is stored in CNY (base currency).**

| Source | Raw Currency | Transformer Behavior |
|--------|-------------|----------------------|
| PIS | CNY | Already CNY (qty x price x FX rate) |
| Schwab_CSV | USD | Transformer multiplies by USD_TO_CNY (~7.0) |
| CN_Fund_Excel | CNY | Already CNY |
| Gold_Excel | CNY | Already CNY |
| Insurance_Excel | CNY | Already CNY |
| RSU_Excel | USD | Transformer multiplies by USD_TO_CNY (~7.0) |

**If you see `market_value < 100,000` for a multi-share US stock position, this may indicate
raw USD was stored instead of CNY (investigate before concluding it's a bug).** A single
Schwab holding worth ~$33K should appear as ~¥231K in the DB if correctly converted.

---

## Rule 3: Global MAX(snapshot_date) Is Almost Always Wrong

Different sources sync at different times. QDII funds lag 2 days. Use per-asset or per-source
MAX dates.

```sql
-- BAD: misses QDII assets with older dates
WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM holdings)

-- GOOD: per-source latest
WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM holdings WHERE source_system = ...)

-- BEST: per-asset latest (use this for net worth calculations)
WITH latest_per_asset AS (
    SELECT asset_id, MAX(snapshot_date) AS max_date
    FROM holdings WHERE is_shadow = FALSE
    GROUP BY asset_id
)
SELECT h.* FROM holdings h
JOIN latest_per_asset l ON h.asset_id = l.asset_id AND h.snapshot_date = l.max_date
WHERE h.is_shadow = FALSE
```

---

## Rule 4: Shadow Logic Direction

Reader data is **AUTHORITATIVE** over PIS. Shadow direction is one-way:

- Reader rows (`Schwab_CSV`, `CN_Fund_Excel`, `Gold_Excel`, `Insurance_Excel`, `RSU_Excel`)
  **MUST NEVER** have `is_shadow = TRUE`
- PIS rows get `is_shadow = TRUE` when a reader source exists for the same asset
- If you see reader rows with `is_shadow = TRUE`, that is a **bug**

Quick check:
```sql
SELECT source_system, COUNT(*) as total,
       SUM(CASE WHEN is_shadow THEN 1 ELSE 0 END) as shadowed
FROM holdings
WHERE source_system IN ('Schwab_CSV','CN_Fund_Excel','Gold_Excel','Insurance_Excel','RSU_Excel')
GROUP BY source_system;
-- 'shadowed' column should be 0 for all reader sources
```

---

## Rule 5: Post-Change Verification Queries

After ANY change to sync pipeline, transformers, shadow logic, or financial calculations, run
these 5 queries:

```sql
-- 1. Net worth sanity (should be 3M–10M CNY range for this portfolio)
WITH latest AS (
    SELECT asset_id, MAX(snapshot_date) AS max_date
    FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
)
SELECT SUM(h.market_value) AS net_worth
FROM holdings h JOIN latest l ON h.asset_id=l.asset_id AND h.snapshot_date=l.max_date
WHERE h.is_shadow=FALSE AND h.market_value > 0;

-- 2. Shadow status by source (reader sources must have 0 shadowed rows)
SELECT source_system,
       COUNT(*) AS total,
       SUM(CASE WHEN is_shadow THEN 1 ELSE 0 END) AS shadowed
FROM holdings
GROUP BY source_system ORDER BY source_system;

-- 3. Currency consistency (no Schwab asset should have market_value < 100K if qty > 1)
SELECT asset_id, source_system, quantity, market_value, snapshot_date
FROM holdings
WHERE source_system = 'Schwab_CSV'
  AND is_shadow = FALSE
  AND quantity > 1
  AND market_value < 100000
ORDER BY market_value ASC LIMIT 10;

-- 4. Cost basis sanity (cost_price_unit * quantity should be within 10x of market_value)
SELECT asset_id, cost_price_unit, quantity,
       cost_price_unit * quantity AS total_cost, market_value,
       (cost_price_unit * quantity) / NULLIF(market_value, 0) AS cost_to_mv_ratio
FROM holdings
WHERE is_shadow = FALSE AND cost_price_unit IS NOT NULL AND quantity > 0 AND market_value > 0
  AND (cost_price_unit * quantity) / market_value > 10
ORDER BY cost_to_mv_ratio DESC LIMIT 10;

-- 5. Active holdings count by source (should match expected counts)
SELECT source_system, COUNT(*) AS active_holdings,
       SUM(market_value) AS total_value
FROM holdings
WHERE is_shadow = FALSE
GROUP BY source_system ORDER BY total_value DESC;
```

**When an integrity check fails, start here** — the usual cause for each:

| Integrity failure | Likely cause | Fix |
|-------------------|-------------|-----|
| `no_raw_usd_in_schwab_holdings` | USD not converted to CNY | Multiply by FX rate in transformer |
| `net_worth_plausible` | Global `MAX(snapshot_date)` used | Switch to per-asset MAX date |
| `reader_rows_not_all_shadowed` | Shadow logic running backwards | Check `_shadow_legacy_holdings()` direction |
| `active_holdings_have_positive_value` | Transformer returning None | Check transformer output for NULL |
| `cash_pnl_is_zero` | Wrong cost basis for CASH | Set `cost_price_unit = 1` for CASH |
| `cost_basis_ratio_under_10x` | Using total cost as unit cost | Divide by quantity |
| `shadow_mutual_exclusion` | Duplicate rows from failed idempotency | Check upsert logic |
| `net_worth_cross_endpoint_consistency` | Different calculation paths disagree | Check `HoldingsAggregator` authority resolution |
| `twr_xirr_consistency` | TWR/XIRR spread > 25% | Partial snapshot or gap in transaction history |

---

## Rule 6: DB Safety (from 2026-02-15 incident)

On 2026-02-15, the production DuckDB was wiped to 274KB during an agent session. Only 6
classification tables survived. Restored from backup.

Two mechanical guards now cover the obvious cases: the `PreToolUse` hook
(`scripts/hook-db-safety-guard.sh`) hard-blocks `--init`/`--reset`, `DROP TABLE`, `TRUNCATE`,
`DELETE FROM`, and `rm`/`mv` on the DB; `verify.sh` check [a] (exit 1) catches bare
`DatabaseConnector()` in tests and DDL in `.py` files.

What the guards do **not** cover:
- Verify DB > 1MB and `SELECT COUNT(*) FROM holdings` → 600+ **before** any sync or mutation.
  If it looks empty, STOP — do not sync on an empty DB, that is how a wipe becomes permanent.
- Never run `--sync-v3` locally: stale local source files make stale-reader cleanup write phantom
  liquidation tombstones. Fix source data on cloud, then `./dev.sh pull-cloud`.
- Backups in `data/backups/` are human-delete-only (automated pruning goes through `scripts/maint_db.py`).

---

## Rule 7: Known Traps (from 30+ historical bugs)

| Trap | What Goes Wrong | Correct Pattern |
|------|----------------|-----------------|
| `cost_basis` vs `cost_price_unit` | Schwab CSV `cost_basis` = total cost, not per-unit | Divide by quantity |
| Sold asset fallback | Aggregator falls back to stale PIS for sold assets | Permanent shadow persistence |
| QDII T+2 lag | Fund NAV lags 2 days, global MAX date misses it | Per-asset MAX date |
| Insurance `market_value` | Insurance transformer outputs None | Use cumulative premiums |
| Ghost asset propagation | LAST_VALUE carries sold assets forever | Zero after end_date |
| Global snapshot date | `MAX(snapshot_date)` across all holdings skips QDII | Per-source or per-asset MAX |
| RSU cost basis | Huinsight uses vest price, PIS uses 0 — intentional divergence | Do not flag as bug |
| `asset_registry.is_rebalanceable` | Set TRUE even for Insurance/Property | Use `taxonomy_classes.is_rebalanceable` |
| `at` keyword in DuckDB | `at` is reserved — aliases fail silently | Use `tiers` as alias |
| Schwab ETF type | CSV has `"ETFs & Closed End Funds"`, not `"ETF"` | Use substring matching |
| Stale reader sold-cleanup | `sold_after_snapshot` using "any sell" incorrectly removes re-entered active assets (e.g., SGOV sold then re-bought) | Check `net_post_snapshot_qty > 0` and `last_post_buy > last_post_sell` before removing |
| Stale reader shadowing | Source-global `MAX(snapshot_date)` shadows QDII-lagged reader rows (e.g., CN funds 1 day behind) | Use per-asset age (>7 days) + full-liquidation signal — see `STALE_READER_SHADOW_DAYS` in orchestrator |
| Attribution cash cost | Cash-equivalent holdings with `cost=0` inflate their contribution effect to 100%+ | Zero cost basis for Cash/BankWealth/MoneyMarket in attribution SQL before computing effects |
| Correlation structural jumps | Raw Pearson correlation inflated by class-level structural shifts (e.g., Cash vs Equity = 0.88) | Apply per-class MAD jump masking + 180-day stable window + 5%/95% winsorization + min-overlap gate |
| Context export global snapshot | `context_generator.py` using global MAX date misses QDII assets; sections use different snapshot bases, causing internal inconsistency | Replace all section queries with latest-per-asset CTE authority join |
| Decorative UI buttons | Frontend buttons with no `onClick` handler ship as visual affordances — users click, nothing happens | Never ship a button without a real handler; remove dead controls or replace with `disabled` + tooltip |
| Hardcoded backend groups | Static entries in API responses (e.g., AIA/trade_logs always in every portfolio audit row) make every asset appear affected | Only include dynamic data; filter group entries from DB state, not hardcoded lists |
| Log severity uniformity | Displaying all sync log lines as red errors causes alert fatigue; real errors hidden in noise | Classify log lines by severity (error/warn/info) and apply distinct colors/icons |
| Gold transaction deduplication | `sync_gold()` produces `GOLD_PAPER_CMB` rows; post-sync cleanup renames existing DB rows to `ALTS_Paper_Gold`; next sync's DELETE finds 0 rows and INSERTs anew → 20× accumulation after 20 syncs | In `_normalize_transactions_df` for `Gold_Excel`, rename `GOLD_PAPER_*` → `ALTS_Paper_Gold` in asset_id **before** calling `_replace_transactions` — so DELETE correctly matches existing rows |

---

## Rule 10: Partial Snapshots Are the Norm

Each reader inserts holdings with dates from its SOURCE DATA, not the sync run date.
On any given date, only a subset of readers may have data. This means:

- Most snapshot dates are PARTIAL (only 1-3 of 6 sources present)
- Only PIS_Historical month-end dates (last day of month) are complete
- Metrics that aggregate across snapshot_dates (TWR, Sharpe, net worth history)
  MUST use per-asset-latest or qualified-snapshot logic, NEVER raw date grouping

**Bad — sums partial snapshots, produces wild swings:**
```sql
SELECT snapshot_date, SUM(market_value) FROM holdings
WHERE is_shadow = FALSE GROUP BY snapshot_date
```

**Good — per-asset latest combines all sources correctly:**
```sql
WITH latest_per_asset AS (
    SELECT asset_id, MAX(snapshot_date) AS latest_date
    FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
)
SELECT SUM(h.market_value) FROM holdings h
JOIN latest_per_asset l ON h.asset_id = l.asset_id AND h.snapshot_date = l.latest_date
WHERE h.is_shadow = FALSE
```

---

## Rule 11: Sold Assets Need Explicit Handling

The shadow pipeline marks PIS holdings as `is_shadow=TRUE` only when a reader row
exists for the same asset_id. When an asset is fully sold:

1. The reader stops including it (no more source data)
2. PIS may still have Adjustment_Buy phantom transactions re-creating the asset
3. Since no reader row exists, the shadow pipeline CANNOT shadow the stale PIS row
4. The asset appears as "Active" in WealthOS with stale/wrong values

**Detection**: Cross-reference latest holdings snapshot with sell transactions.
If sells occur AFTER the last snapshot date, the asset is likely fully sold.

**Prevention**: After sync, run the stale-PIS shadow cleanup step (orchestrator.py step 2.4.15).
This UPDATE marks PIS-sourced holdings as shadow when:
- No reader source covers that asset_id, AND
- The asset has sell/adjustment_sell transactions

---

## Rule 12: Never Return Empty Data with HTTP 200 on Exception

API endpoints that catch exceptions and return `[]` or `{}` with HTTP 200 create
silent data loss. The frontend cannot distinguish "no data exists" from "query failed."

**Bad — silent failure looks like empty data:**
```python
except Exception as e:
    return []  # HTTP 200, frontend shows "No data available"
```

**Good — log error so failures are visible:**
```python
except Exception as e:
    logger.error(f"Endpoint failed: {e}")
    return []  # Still 200 for graceful degradation, but logged
```

**Best — separate independent data sources into independent try/except blocks:**
```python
history = []
try:  # Source A
    history.extend(fetch_balance_sheet())
except Exception as e:
    logger.error(f"Balance sheet failed: {e}")
try:  # Source B
    history.extend(fetch_current_point())
except Exception as e:
    logger.error(f"Current point failed: {e}")
return history  # Returns whatever succeeded
```

**Return vs raise contract (from Pass 1 L1 learning):**
- `return JSONResponse({"error": ...})` — use when the endpoint needs the exact top-level `{"error": {...}}` contract.
- `raise HTTPException(status_code=..., detail=...)` — use for standard FastAPI `{"detail": ...}` error responses. This is valid and expected.
- **Never `raise JSONResponse(...)` or `raise Response(...)`** — they are not exceptions; raising them produces an empty 500 and bypasses FastAPI's exception middleware. The verify gate checks for this pattern.

**Safe error messages (from Pass 1 L3 learning):**
- Never use `str(e)` in externally-visible API error fields — it leaks filesystem paths (e.g., `data/unified.duckdb`) and venv site-package paths.
- Use `type(e).__name__` for the error type, or a curated human-readable message.
- Internal logs may use `str(e)` or `repr(e)` — this restriction applies only to the API response payload.

**Silent file/path skips:**
- If a sync step skips a source file because it is missing or unreadable, that skip must be recorded in `result.steps` as `status="skipped"` with `error` containing the path (not leaked to the API, but visible in structured logs and `SyncResult`).
- Returning from a reader with an empty result and no log entry is the file-skip analogue of `return []` on an API crash. Both are Rule 12 violations.

---

## Rule 13: Pipeline Change Protocol

**Pipeline code paths** — this rule applies when modifying any of:
`src/sync/` · `src/sources/` · `src/financial_analysis/` · `src/identity/` ·
`src/validation/data_integrity_gate.py` · `config/source_authority.yaml`

**Before:** read the relevant sections of `docs/architecture/data-pipeline-v6.md`, plus
`config/source_authority.yaml` if touching shadow or authority logic. Capture the baseline:
`python main.py --check-integrity` → record `N/16`.

| Changing… | Read section |
|-----------|--------------|
| Shadow logic, `_shadow_*` functions | 8 (Shadow Pipeline), 4 (Diagram) |
| A reader or transformer | 7 (that reader), 9 (Post-Insertion Processing) |
| Authority resolution, `holdings_aggregator.py` | 10 (Authority Resolution) |
| Integrity checks | 12 (Integrity Gate) |
| Orchestrator phases | 4, 5, 6, 7, 8, 9, 10 |
| FIFO cost basis | 9.1, Known Edge Cases A/E |

**If the planned change contradicts the architecture doc → STOP and flag it to the human.**
Don't silently implement against the documented design.

**During:** re-run `--check-integrity` after each conceptual unit of change (not every line). A check
that was passing and now fails is a regression — fix it before continuing, don't accumulate.

**Before claiming complete:**
1. `python main.py --check-integrity` — matches or beats the baseline
2. `pytest tests/e2e/test_financial_integrity.py -v` — golden assertions pass
3. Report before/after: net_worth, active_holdings, integrity score
4. **Net worth moved >5% without a market-data refresh to explain it → STOP and investigate** (see Rule 18)

---

## Rule 17: Architecture Doc Currency (Commit Convention)

For commits touching `src/sync/`, `src/sources/`, `src/identity/`, `src/validation/data_integrity_gate.py`, `src/services/valuation/fetchers/`, or `src/market_data/`, the commit message MUST include one of:

- `Docs: no update needed (change is within documented behavior)`
- `Docs: updated [section] in data-pipeline-v6.md to reflect [change]`

**Data-feed source changes require an additional update:**
If you change a data-feed source, URL, or unit convention, you must also:
1. Update the `## Change Log` table in `docs/architecture/data-sources.md`.
2. Add a `# see docs/architecture/data-sources.md Change Log` comment at the change site in the source file.

Architecture doc updates MUST be in the same commit or PR as the code change.

Undocumented pipeline changes are considered **incomplete work**.

---

## Rule 18: Human Review Triggers

These changes MUST NOT merge without explicit human review:

| Trigger | Why |
|---------|-----|
| Net worth changes >5% after pipeline code change | Currency mixing, shadow error, or partial snapshot |
| New source reader added | Authority model, shadow direction, cleanup logic all need verification |
| Shadow logic modified (`_shadow_*`, `holdings_aggregator.py`) | Direction errors cause data inflation/deflation tests don't catch |
| Cost basis calculation changed | FIFO edge cases require human judgment |
| Orchestrator step ordering changed | Pipeline steps have implicit dependencies |
| `MAX(snapshot_date)` without `GROUP BY asset_id` | Partial-snapshot bug (Rule 3) |
| Integrity check thresholds loosened | May mask real bugs |
| Any `@pytest.mark.skip` added or existing check removed | May hide real failures |

---

## Rule 19: UI Controls Are a Contract

Every interactive-looking control in the frontend must have a real `onClick` handler before shipping.

**Bad — decorative button that ships with no handler:**
```tsx
<button className="btn-primary">Run New Audit</button>  {/* does nothing */}
```

**Good — wired or explicitly disabled:**
```tsx
<button onClick={handleRunAudit}>Run New Audit</button>
{/* OR if not yet implemented: */}
<button disabled title="Coming soon">Run New Audit</button>
```

**Checklist before any frontend PR:**
1. Click every button in the page — does each one respond?
2. Every `<button>` and `<IconButton>` must have `onClick`, `href`, or `disabled`
3. Confirm the wired action actually calls an API endpoint (not just `console.log`)
4. Log severity must match display style — errors red, warnings yellow, info grey

**Why**: After the Operations Phase 1 redesign, 20+ buttons were shipped as visual affordances with no handlers. Users lost trust in the UI. This is a product quality baseline, not a "nice to have".

---

## Rule 20: Backend APIs Are Often Already Rich Enough

Before modifying a backend API, verify the frontend is actually using all fields the API returns.
The most common root cause of "missing data" UI bugs is the frontend not wiring up existing API fields — not a missing backend feature.

**Before any "we need a new API field" decision:**
```bash
# Check what the API actually returns
curl -s http://localhost:8008/api/the-endpoint | python3 -m json.tool | head -100
# Then verify which fields the frontend component is reading
grep -n "response\.\|data\." ux-command-center/pages/ThePage.tsx | head -30
```

**Why**: In the Operations redesign (V3.30+), 12 of 13 HR findings were fixed by frontend wiring changes alone. Only 1 required a backend change (removing a hardcoded group list). Checking the API response first saves hours of unnecessary backend work.

---

## Rule 21: All LLM Calls Must Go Through LLMClient

Never call `litellm.completion()` directly in application code. Always use `LLMClient` from `src/services/llm_client.py`.

```python
# WRONG
import litellm
resp = litellm.completion(model="gemini/...", messages=[...])

# CORRECT
from src.services.llm_client import LLMClient
result = LLMClient().complete(system_prompt, user_prompt, report_type="brief")
```

`LLMClient` provides:
- Model fallback chain (primary → fallback 1 → fallback 2) from `config/settings.yaml`
- Fire-and-forget usage logging to `llm_usage` table (never crashes the caller)
- `json-repair` JSON parsing for resilient structured output
- `is_available()` check before prompting users to generate

**Why**: Direct litellm calls bypass the fallback chain (if Gemini quota is exhausted, the call fails instead of trying DeepSeek), bypass usage tracking, and bypass the cost estimation logic. Centralizing in LLMClient means model config changes in settings.yaml apply everywhere.

---

## Rule 22: External-Feed Unit / Staleness / Fallback Discipline

Every external data-feed integration must satisfy three obligations before merge:

**Unit obligation:** Document the unit of the returned value at the call site.
```python
# ^TNX returns yield in percent (e.g. 4.455 means 4.455%) — divide by 100 at the calculation boundary only
rate = yf.download("^TNX", ...)["Close"].iloc[-1]  # percent
```
If the unit is ambiguous, add an assertion or a comment referencing the source API docs. Do not apply unit conversions at the fetch site unless the conversion is documented there.

**Staleness obligation:** Every feed result must be checked for a minimum-freshness threshold before use in calculations. If stale beyond the threshold, log a warning and use the last-known-good value or return a degraded response. Do not silently use stale data in a P&L, NAV, or net-worth computation.

**Fallback obligation:** Every feed fetch must have an explicit fallback:
- Acceptable: `try/except: return cached_value` (if `None` is handled by caller)
- Acceptable: `try/except: logger.warning(...); return []`
- **Never acceptable**: `try/except: pass` (no return, no log — the silent discard pattern)

**Migration note:** The `^TNX ÷ 10` bug (commit `b5af5ef`) is the canonical example of this class of failure. A feed returned percent (4.455) which was incorrectly divided by 10, producing 0.445%. New feed integrations must be reviewed against this rule before merge.

**All external HTTP calls must go through `http_get` from `src/utils/http_client.py`** — not bare `requests.get()`. The shared client provides retry logic, timeout enforcement, and a browser UA. See Batch 3 of Pass C for the migration of the three remaining raw fetchers.

---

## Rule 23: GCS Persistence Round-Trip + Health Probe Discipline

**No GCS writes from health checks.**

The `/health/deep` endpoint's `gcs` block performs only a `blob.exists()` metadata check. It does not write, download full objects, or modify any GCS state. This is a permanent constraint — a health check that writes to shared state is a side-effectful probe.

**Current `/health/deep` `gcs` block shape** (as implemented in `src/api/main.py`):
```json
// local mode (no bucket env):
{ "ok": true, "configured": false, "note": "local mode" }

// configured + reachable:
{ "ok": true, "configured": true, "db_blob_present": bool }

// configured + error:
{ "ok": false, "configured": true, "error": "<ExceptionTypeName>" }
```
No bucket names, credentials, or object paths appear in the health payload. `error` uses `type(e).__name__` (not `str(e)`) per Rule 12 safe-message obligation.

**GCS round-trip test is a separate explicit operation.**

A real write→read→verify GCS round-trip (to confirm persistence works end-to-end) is appropriate as: (a) a deploy-time smoke test step in CI, or (b) a manual `python main.py --verify-gcs` command. It must never be on the hot path of a health or status endpoint.

Enriching `/health/deep` with a `last_flush` timestamp or a full round-trip probe would be an API contract change — defer to a later pass, with the contract change documented in an ADR. See ADR-006 for the GCS persistence topology decision.

---

## Rule 24: Incident → Structural Guard Ratchet

**Every resolved incident must end with a guard that makes the CLASS structurally impossible — or an explicit, written decision not to build one.**

A fix that only repairs the instance leaves the class armed (2026-07-06 P0: "unmatched rows invisible" was flagged in review on Jul 5, patch-fixed for one filter, and the same class re-fired as a P0 two days later). Before closing any incident, answer: which of the five recurring failure classes does this belong to, and what construction-level guard kills the class?

| Failure class | Guard pattern |
|---------------|---------------|
| Invisible states (enum value no surface shows) | State-coverage test: every status value visible through ≥1 UI filter |
| Convention contracts (mark_dirty, write-back) | Safe-by-construction helper, or verify.sh static check |
| Multi-writer fields (price, status, verdict) | Explicit precedence predicate in the writer (e.g. `md.date >= snapshot_date`) |
| Non-atomic file ops under concurrency | tmp + `os.replace`; re-audit file ops when concurrency increases |
| Always-failing checks | Zero-violation baseline or immediate by-design carve-out — never "ignore the advisory" |

---

## Rule 25: Complexity Budget (Sustainability)

**Every new feature surface is a permanent maintenance liability. Default answer to net-new surface is no; prefer deepening existing surfaces.**

- Before building a new page/endpoint/reader/scheduled job, state its recurring cost: what can silently break, what data it writes, what the owner must maintain (files, credentials, uploads).
- Prefer: fewer states, fewer writers per field, fewer files the owner must hand-maintain.
- Unbounded growth is a bug even when each increment is cheap: backups, Docker images, audit rows, migration blocks, always-on log noise. Anything that grows per-deploy or per-sync needs a retention policy at birth.
- Periodically (each release ritual) do a subtraction check: dead flags, unused endpoints, superseded docs, stale baselines.

---

## Rule 26: transactions.amount_net Sign Conventions

**`transactions.amount_net` has no normalization layer — its sign is a per-reader convention artifact, not economic direction. Never raw-`SUM(amount_net)` across more than one `source_system`.**

`_ingest.py` (`amount_net = amount_gross − commission_fee`) faithfully propagates whatever sign each reader emits. There are three distinct, incompatible conventions live in the same column:

| Convention | Sources | buy/vest | sell |
|---|---|---|---|
| Magnitude-only (sign carries no direction) | `CN_Fund_Excel`, `Gold_Excel`, `Insurance_Excel`, `AIA` | + | + |
| Cash-flow signed | `Schwab_CSV` | **−** | + |
| Inverted | `RSU_Excel` | + (vest) | **−** |

`RSU_Excel` is opposite to *both* other conventions on sells — there is no single sign rule that works across sources.

**Secondary trap: `transaction_type` is mixed-case.** `AIA` writes `'Buy'`/`'Sell'`; every other source writes lowercase. A bare `IN ('BUY', 'SELL')` or `= 'buy'` filter without `LOWER()` silently matches zero or a partial set of rows — always `LOWER(transaction_type) IN (...)`.

**Safe patterns:**
1. Read magnitude with `abs(amount_net)`, then re-derive the real direction from `LOWER(transaction_type)` against an explicit OUTFLOW/INFLOW frozenset (see `financial_analysis/xirr.py`, `financial_analysis/twr.py`).
2. If direction doesn't matter, sum magnitudes: `SUM(ABS(amount_net))` — never a bare `SUM(amount_net)` spanning more than one `source_system`.
3. FX-convert per row before summing across currencies — `transactions.currency` is native (USD for Schwab/IBKR/RSU, CNY elsewhere); a raw cross-currency sum silently mixes units.

A raw `SUM(amount_net)` across sources, or a `transaction_type` filter without `LOWER()`, is a defect on sight — do not wait for a test to catch it.

**Incident record**: integrity check #4 (`xirr_proxy_in_range`) was silently vacuous from inception until 2026-07-25 — the case-mismatch alone made its `total_invested` CTE match zero rows, so it always returned a false PASS (`actual_value="insufficient_data"`) instead of ever computing a real percentage. A second stacked defect (`AND amount_net > 0`) additionally dropped all negative Schwab buys once the case bug was fixed, and there was no FX conversion.

---

## Verification Protocol

**Before marking any task complete, run `bash scripts/verify.sh` and fix all failures.**

```bash
bash scripts/verify.sh
```

| Exit code | Meaning | Action required |
|-----------|---------|-----------------|
| `0` | All checks pass | Safe to commit |
| `1` | **P0 DB safety** — unguarded `DatabaseConnector()` or destructive DDL in Python | Stop immediately. Fix before anything else. |
| `2` | Business logic violations — global `MAX(snapshot_date)`, currency hardcodes, LLMClient bypass, buttons without handlers | Fix before committing — see the four patterns below. |
| `3` | Code quality — new oversized files, ruff lint violations | Fix if possible; if not, update baseline with justification. |

**Fixing exit-code-2 violations:**

```sql
-- §MAX-snapshot: global MAX(snapshot_date) silently drops T+2-lagged assets (e.g. QDII funds)
-- WRONG
WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM holdings)
-- RIGHT: per-asset latest
WITH latest_per_asset AS (
    SELECT asset_id, MAX(snapshot_date) AS max_date FROM holdings
    WHERE is_shadow = FALSE GROUP BY asset_id
)
SELECT h.* FROM holdings h
JOIN latest_per_asset l ON h.asset_id = l.asset_id AND h.snapshot_date = l.max_date
WHERE h.is_shadow = FALSE
```

```python
# §currency-constants: don't hardcode the FX rate locally — import the canonical one
# WRONG
USD_TO_CNY_RATE = 7.0
# RIGHT
from src.data_manager.currency_converter import get_fx_rate
usd_to_cny = get_fx_rate("USD", "CNY")
```

```tsx
// §ui-handlers: every visible button needs a real action or an explicit disabled state
// WRONG — decorative, looks interactive but does nothing
<button className="btn-primary">Run Audit</button>
// RIGHT
<button onClick={handleRunAudit}>Run Audit</button>
<button disabled title="Coming in next release">Run Audit</button>
```

```python
# §llmclient-bypass: direct litellm calls skip the fallback chain, usage logging, and JSON repair
# WRONG
import litellm
resp = litellm.completion(model="gemini/...", messages=[...])
# RIGHT
from src.services.llm_client import LLMClient
result = LLMClient().complete(system_prompt, user_prompt, report_type="brief")
```

**Rules:**

1. If `verify.sh` exits `1`, do not proceed. Fix the DB safety violation first.
2. If `verify.sh` exits `2`, fix the listed violations before marking the task done.
3. If a check fails more than once on the same issue class, open an issue documenting the root cause and fix pattern (see `CONTRIBUTING.md`) so the next occurrence is caught faster.
4. Runtime checks are separate and also required after any data-touching change:
   ```bash
   python main.py --check-integrity        # 16 invariant checks (requires live DB)
   python main.py --check-integrity --json # machine-readable, non-zero exit on failure
   ```

**Updating baselines (when justified):**

Pre-existing violations are stored in `scripts/.baseline-*.txt` files. To add a new
known exception — for example, a new large file that is intentionally large:
```bash
echo 'src/path/to/file.py' >> scripts/.baseline-large-files.txt
```
Always include a comment in the code explaining why the exception is justified.

**What `verify.sh` does NOT cover (runtime checks):**

- `is_shadow=TRUE` on reader rows — requires live DB query (AGENTS.md Rule 4)
- Net worth >5% change after pipeline change (AGENTS.md Rule 18)
- FIFO cost basis correctness (AGENTS.md Rule 7)
- Gold transaction dedup, stale reader cleanup, insurance `market_value=None`
