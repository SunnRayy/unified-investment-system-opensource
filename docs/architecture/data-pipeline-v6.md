# Data Pipeline Architecture — V6.1 (Manifest-Driven Phases P0–P8 + Reader-First)

> **Version**: V6.1 — 2026-06-10
> **Previous doc**: `docs/architecture/data-pipeline-v4.md` (V5.6.1 — superseded, kept for history)
> **Status**: Current
> **Quick map**: [`pipeline-flow.md`](pipeline-flow.md) — ASCII flow + auto-generated mermaid
> diagram. The mermaid diagram there is generated from `src/sync/phases/manifest.py` by
> `scripts/generate_pipeline_diagram.py` and drift-checked by `verify.sh` step [j].
> **Runnable verification**: `scripts/reconcile_readers.py` (files ↔ DB proof, read-only),
> `scripts/freshness_report.py` (what's latest per asset).

---

## Section 1: What Changed?

### V6.1 (2026-06-10) — Manifest-Driven Pipeline (Data Layer Transformation, Workstream A)

| Before (V5.6.1) | After (V6.1) |
|------------------|--------------|
| Phase order encoded in comments ("2.4.13" appeared twice, no Phase 5) | **`PIPELINE_MANIFEST` (P0–P8)** in `src/sync/phases/manifest.py` is the single source of truth; `run_full_sync_v3()` iterates it |
| Deprecated DSA SQLite market-data ingest still ran as step 2.3 | **Removed from the orchestrator** — the only price path is P3 (`MarketDataService.refresh_portfolio_prices`); historical backfill stays available via `main.py --sync-market` |
| Per-phase results in-memory only | Per-phase `StepResult` (status + duration) recorded in the manifest loop and **persisted to `sync_audit_reports.steps`** (migration V55); served by `GET /operations/pipeline` |
| Pipeline diagram hand-maintained (went stale) | Diagram generated from the manifest; doc drift fails `verify.sh` |

Phase map (old → new): Phase 0 → P0 · Phase 1 → P1 · Phase 2 readers/adapters → P2 ·
2.4.11b live price refresh → P3 · 2.4.12–16 shadow/cleanup/FIFO/cost → P4 ·
Phase 2.7 authority → P5 · Phase 3 derived → P6 · Phase 4 validation + decision layer → P7 ·
Phase 6 diff + integrity gate → P8.

**Planned next** (not yet built — see `docs/plans/2026-06-10-data-layer-transformation-program.md`):
Workstream B (config-driven readers + Excel template v2) and Workstream C (IBKR via Flex
Query with multi-broker co-authority, ADR-016). Do not assume those exist.

### V4.0 (original)

V4.0 completes the **Reader-First Architecture** migration started in Phase 9 (ADR-003).

| Before (V3.0–V3.2) | After (V4.0) |
|--------------------|---------------|
| PIS (Personal Investment System) was authoritative | **6 Excel/CSV readers** are authoritative |
| AIA provisional transactions | AIA fully deprecated — no holdings or trade reconciliation |
| DSA as market data provider | DSA still used for market OHLCV data |
| 10 integrity invariant checks | **15 self-derived integrity invariant checks** |
| Single-layer PIS shadow | **3-tier shadow pipeline** (stale-reader, historical, legacy) |

**Authority model**: 6 readers are the source of truth for current holdings. PIS remains in the
database as historical baseline (shadowed, `is_shadow=TRUE`). It is no longer refreshed.

### V5.2.0 (2026-04-12) — Native-Currency P&L and Sync Fixes

| Before (V4.x) | After (V5.2.0) |
|----------------|----------------|
| `market_price_unit` for Schwab = price × 7.0 (CNY) | `market_price_unit` for Schwab = native USD price |
| `cost_price_unit` for Schwab = cost/qty × 7.0 (CNY) | `cost_price_unit` for Schwab = cost/qty in native USD |
| `CostBasisCalculator` converted USD cashflows → CNY via live FX | `CostBasisCalculator` works in native currency; FX is caller's responsibility |
| USD P&L polluted by historical FX rates (SGOV showed −86%) | USD P&L computed in USD; CNY P&L uses today's live FX |
| XIRR: historical USD cashflows converted at historical rate | XIRR: all USD cashflows multiplied by today's rate for constant-FX comparison |
| `validate_cost_basis` multiplied transaction prices by 7.0 | Validator is a no-op (native USD matches native USD, no conversion needed) |
| `trade_log.linked_transaction_id` FK violated on re-sync | `_reset_trade_log_links()` called before `_replace_transactions()` |
| Portfolio oscillated ±0.41% every sync on Sundays | Fixed — DSA condition C no longer fires when USD price is stored natively |

**Key invariant introduced in V5.2.0** (see Section 2.2 item 2 and Section 14.J):

- `market_price_unit` = **native USD** for Schwab/RSU assets (set by `_update_from_dsa`)
- `cost_price_unit` = **native USD** for Schwab/RSU assets (set by transformer or FIFO backfill)
- `market_value` = **always CNY** for all assets (set by `_update_from_dsa` using live FX)
- Never compute `market_value − cost_price_unit × qty` for USD assets — it mixes currencies

---

## Section 2: Quick Reference

### Key Commands

```bash
# Full sync (recommended)
python main.py --sync-v3

# Verify data integrity (15 self-derived checks)
python main.py --check-integrity

# Validate reader sources before sync
python main.py --validate-readers

# FIFO cost basis consistency check
python main.py --validate-cost-basis
```

### Critical Files

| File | Purpose |
|------|---------|
| `src/sync/orchestrator.py` | Central pipeline — `run_full_sync_v3()` |
| `src/sources/schwab_sync.py` | Schwab CSV reader sync |
| `src/sources/cn_fund_sync.py` | CN Fund Excel reader sync |
| `src/sources/gold_sync.py` | Gold Excel reader sync |
| `src/sources/insurance_sync.py` | Insurance Excel reader sync |
| `src/sources/rsu_sync.py` | RSU Excel reader sync |
| `src/sources/financial_summary_sync.py` | Financial Summary Excel reader sync |
| `src/sync/holdings_aggregator.py` | Authority resolution per snapshot date |
| `src/identity/authority_resolver.py` | Pattern-based source priority matching |
| `src/validation/data_integrity_gate.py` | 15 self-derived invariant checks |
| `src/services/transaction_source_selector.py` | FIFO transaction source authority |
| `src/services/rebalanceable_filter.py` | Rebalanceable asset filtering |
| `src/services/compass_allocation.py` | Shared current / target / drift semantics for Compass and AI Advisor |
| `src/services/portfolio_semantics.py` | Shared Performance / WealthOS portfolio semantics reused by AI Advisor |
| `src/services/llm_client.py` | Centralized LLM call path for AI Advisor Brief / Review |
| `config/source_authority.yaml` | Source priority rules |

### "Watch Out For" — Common Agent Traps

> See also AGENTS.md Rule 7 for full list.

1. **Global `MAX(snapshot_date)`** is wrong — QDII funds lag 2 days. Always use per-asset MAX.
2. **Currency invariant (V5.2.0+)**: `market_price_unit` and `cost_price_unit` for Schwab/RSU are **native USD**. `market_value` is **always CNY**. Do NOT multiply `market_price_unit` by 7.0 — `_update_from_dsa` stores native USD. A Schwab non-cash holding with `market_value < 500 CNY` and `qty > 1` may indicate raw USD leaked into `market_value` (Check 2 catches this).
3. **`asset_registry.is_rebalanceable` is unreliable** — use `taxonomy_classes.is_rebalanceable` joined via `tc.name = r.asset_class`.
4. **`at` is a reserved keyword in DuckDB** — use `tiers` as alias for `asset_tiers`.
5. **PIS `Cost_Price_Unit`** = total-buy-cost, NOT FIFO remaining cost — never use it directly.
6. **Financial_Summary_Excel shadow direction** — only the latest snapshot per asset is active (`is_shadow=FALSE`). Older historical snapshots are shadowed.
7. **Reader rows must never be shadowed** — `is_shadow=TRUE` for reader sources triggers the `shadow_mutual_exclusion` integrity check (Check 6).
8. **RSU holdings are derived from transactions** — the RSU reader does NOT read current holdings directly; it computes them from vest/sell history.

---

## Section 3: Design Decisions Summary

| Decision | What | Why |
|----------|------|-----|
| Reader-first authority | 6 readers replace PIS/AIA as primary holding sources | PIS data had phantom transactions, Schwab cash bugs, FIFO mismatches |
| PIS deprecated | PIS stays in DB as `is_shadow=TRUE` baseline only | ADR-003 — see `docs/decisions/ADR-003-phase9-pis-deprecation.md` |
| `config/source_authority.yaml` | Pattern-based priority rules per asset ID | Flexible, auditable, no hard-coded logic |
| Multi-layer shadow (3 tiers) | Stale-reader, historical, legacy | Prevents duplicate active holdings across time and source |
| Gold aggregation | Per-account → `ALTS_Paper_Gold` at insertion | PIS used a single gold asset; readers have per-account breakdown |
| Insurance `cash_value` | `market_value = cash_value` column from spreadsheet | Surrender value, not face value |
| Insurance cost = cumulative premiums | `cost_price_unit = SUM(premium_payments)` | Correct P&L for insurance = total money paid in |
| FIFO backfill post-insertion | Reader holdings with `NULL cost_price_unit` get FIFO applied after insert | Readers don't compute cost; cost comes from transaction history |
| Permanent shadow persistence | `apply_authority_rules()` in HoldingsAggregator: once `is_shadow=TRUE`, stays `TRUE` | Prevents re-activation of stale data on subsequent syncs |
| Financial Summary melt extraction | `melt_balance_sheet_to_holdings()` extracts 10 discrete assets from balance sheet rows | Property, cash, pension, bank wealth exist only in Financial Summary |
| UNKNOWN_/GOLD_PAPER_/CASH_ cleanup | Cleanup step in orchestrator normalizes these IDs | Avoids classification drift from PIS phantom assets |
| API `read_only=True` | DuckDB opened in read-only mode by API server | Prevents concurrent write corruption |
| TWR/metrics use BS monthly | Historical portfolio values from balance sheet, not holdings spine | Holdings `is_shadow=FALSE` is too sparse for time-series analysis; balance_sheet has 72 monthly points |
| XIRR search range | `-0.99` to `1000.0` | Handles early-stage RSU accumulation with 10x+ gains |
| AI Advisor is downstream only | Brief / Review may consume shared portfolio semantics, but never become a source of truth | Prevents a second inconsistent portfolio interpretation from emerging inside prompt code |
| **V5.2.0** Native-currency P&L | `market_price_unit` and `cost_price_unit` for Schwab/RSU stored in native USD; `market_value` always CNY | USD P&L was FX-polluted — SGOV showed −86% return because historical USD cashflows used historical FX. Storing prices in native USD isolates currency impact to a single live-FX multiply at display time |
| **V5.2.0** DSA trigger condition | `_update_from_dsa` updates when `md.close != holdings.market_price_unit` (condition C) | With market_price_unit in native USD (≈100), condition C fires only when price actually changes. Previously (USD×7.0≈703 stored), condition C fired every sync, forcing FX-rate-dependent recalculation |
| **V5.2.0** Constant-FX XIRR | USD cashflows × today's live FX rate; terminal value = SUM(qty × market_price_unit) × today_rate | Eliminates historical-FX pollution from XIRR. Result measures USD performance, not USD/CNY movement |
| **V5.2.0** FIFO FX-migration guard | `_backfill_fifo_cost_basis()` nulls stale CNY cost entries where `cost / market_price_unit > 4.5` before FIFO recompute | Old DB rows had cost stored as USD×7.0 CNY; migration guard fingerprints the ~7× ratio and resets them so FIFO recomputes in USD |
| **V5.2.0** Trade-log FK reset | `_reset_trade_log_links()` NULLs `trade_logs.linked_transaction_id` before `_replace_transactions()` | `_replace_transactions` deletes and reinserts rows (new auto-increment IDs). Old IDs in `trade_logs.linked_transaction_id` caused FK constraint violations on every sync |

### 3.1 Downstream AI Advisor Integration

AI Advisor in V4.3.1 is a **downstream consumer** of the reader-first pipeline, not a parallel data model.

- **Prompt preview**: `ContextBuilder` renders a prompt draft from already-authoritative data.
- **Portfolio semantics**: AI Advisor must reuse shared helpers instead of writing its own allocation / performance / holdings SQL:
  - `src/services/compass_allocation.py`
  - `src/services/portfolio_semantics.py`
- **LLM execution**: final Brief / Review calls go through `src/services/llm_client.py`, not direct `litellm` calls.
- **Safety boundary**: LLM outputs are advisory artifacts stored in `ai_reports` / `ai_insights`; they do not write back into authoritative holdings, transactions, or taxonomy state.
- **Prompt boundary**: Brief / Review prompts are required to reason only from provided context and must not claim external search or unseen data.

---

## Section 4: High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  SOURCE FILES (Excel/CSV on iCloud Drive)                               │
│  Individual-Positions-*.csv   funding_transactions.xlsx                  │
│  Gold_transactions.xlsx       Insurance_Portfolio.xlsx                   │
│  RSU_transactions.xlsx        Financial Summary_new.xlsx                 │
└────────────────────────────┬────────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  6 READERS (src/sources/*_reader.py)                                    │
│  Each reads raw source file(s), returns raw DataFrame                   │
└────────────────────────────┬────────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  6 TRANSFORMERS (src/sources/*_transformer.py)                          │
│  Each normalizes: canonical IDs, currency conversion, type coercion     │
└────────────────────────────┬────────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR — run_full_sync_v3() iterates PIPELINE_MANIFEST (P0–P8)   │
│  src/sync/orchestrator.py + src/sync/phases/manifest.py                 │
│  Each phase is timed; StepResults persist to sync_audit_reports.steps   │
│                                                                          │
│  P0 Backup & schema setup                                               │
│     create_backup("pre-sync-v3"); classification tables (idempotent)    │
│     Cloud mode (UIS_GCS_BUCKET set): local backup SKIPPED — every GCS   │
│     flush uploads a timestamped backup (gcs.py); /tmp is tmpfs          │
│                                                                          │
│  P1 Identity sync                                                       │
│     Asset registry sync (taxonomy sync REMOVED 2026-03-10 —             │
│     taxonomy_classes is authoritative, seeded once, UI-managed)         │
│                                                                          │
│  P2 Reader & adapter ingest                                             │
│     pre-reader backup (skipped in cloud mode, as P0) ·                  │
│     legacy prefix normalization                                          │
│     Schwab CSV → CN Fund Excel → Gold Excel → Insurance Excel →         │
│     RSU Excel → Financial Summary Excel (melt_balance_sheet)            │
│     Approved Import Adapters (staged rows only):                        │
│       - upload/validate/stage into control tables                        │
│       - only explicitly approved adapters are synced                     │
│       - adapter holdings must store market_value in CNY                  │
│     _auto_register_new_assets · position deltas ·                       │
│     no-op backup cleanup · zero-ingest alert                            │
│                                                                          │
│  P3 Live price refresh (THE ONLY PRICE PATH)                            │
│     MarketDataService.refresh_portfolio_prices()                        │
│     → yfinance (US) / akshare (CN funds) / SGE (gold) quotes            │
│     → upserts market_daily → _update_from_dsa() updates holdings        │
│     _update_from_dsa() condition:                                       │
│       price_updated_at IS NULL                                          │
│       OR price_updated_at < md.date (new day)                           │
│       OR md.close != holdings.market_price_unit (C)                     │
│     Sets: market_price_unit = md.close (native ccy)                     │
│           market_value = qty × md.close × live_FX (CNY)                 │
│     FX source: yfinance USDCNY=X; default=7.0 on failure                │
│                                                                          │
│  P4 Shadow pipeline & post-ingest normalization  [is_shadow writer #1]  │
│     _shadow_stale_reader_holdings()                                     │
│     _shadow_stale_non_tradable_holdings()                               │
│     _shadow_stale_historical_holdings()                                 │
│     _shadow_legacy_holdings()                                           │
│     Cleanup: UNKNOWN_*, GOLD_PAPER_*, BRK/CASH_ normalization           │
│     _backfill_fifo_cost_basis() (native currency)                       │
│     _set_insurance_cost_from_premiums()                                 │
│     _zero_pl_for_non_tradeable_assets()                                 │
│     Sold-asset shadow (PIS phantom rows)                                │
│     _update_rsu_prices_from_external_sources()                          │
│                                                                          │
│  P5 Authority resolution  [is_shadow writer #2]                         │
│     HoldingsAggregator.apply_authority_rules() per snapshot date        │
│                                                                          │
│  P6 Derived data — current allocations sync                             │
│                                                                          │
│  P7 Validation & decision layer                                         │
│     Cost basis (1%) · allocation drift (5%) · divergence (10%)          │
│     Trade-log linking (bidirectional) · backfill · scoring              │
│                                                                          │
│  P8 Sync diff & integrity gate (15 self-derived checks, 6 blocking)     │
│     Before/after net worth, Δ%, alert if >30% · persist sync audit      │
│                                                                          │
│  P9 Insights continuity (ADVISORY — never blocks sync success)           │
│     (a0) refresh_prices_for_asset_ids — price continuity for pending-    │
│         verification assets (pending ≤45d; verification_blocked ≤120d)  │
│         so sold assets keep +30d outcome prices                          │
│     (a) bridge_ai_insights_to_decision_hub — qualifying ai_insights      │
│         (recommendation/recurring/principle) → Decision Hub insights     │
│     (b) score_all_trades — verdict/outcome_pct backfill for matured      │
│         trade_logs rows; narrative-optional ONLY for pending/blocked     │
│         statuses (auto rows marked 'auto:'); never overwrites verdicts  │
│     (c) recompute_auto_links — upsert insight↔trade attribution links   │
│         from the ±3-day source-match heuristic                           │
│     (d) compute_verification_report — freshness-gated: only runs when   │
│         verification_logs has no row created in the last 24 h            │
│     (e) BehavioralMetricsComputer (window_days=90) — all 6 dimensions   │
│         persisted to ai_behavioral_log                                   │
│     All five sub-tasks share the orchestrator write connection (no new   │
│     connections opened). Config: insights_continuity.enabled (default ON)│
└─────────────────────────────────────────────────────────────────────────┘
```

> The authoritative, always-current version of this diagram is the mermaid block in
> [`pipeline-flow.md`](pipeline-flow.md), generated from the manifest. If this ASCII
> sketch and the manifest ever disagree, the manifest wins.

---

## Section 5: Pre-Sync Validation Layer (REMOVED — history)

Both components of the old pre-sync validation layer are **gone from the codebase**
(files deleted); this section is kept only so historical references resolve.

### 5.1 Freshness Gate — REMOVED

`src/validation/freshness_validator.py` no longer exists. It compared PIS Excel vs PIS
SQLite timestamps — meaningless after PIS deprecation (ADR-003). Freshness is now an
*observable*, not a gate: per-source freshness is served by `GET /operations/pipeline`
(Source Freshness panel) and `scripts/freshness_report.py`.

### 5.2 Taxonomy Validator — REMOVED

`src/validation/taxonomy_validator.py` no longer exists (removed 2026-03-10, table
`asset_taxonomy` dropped in Pass F Migration V42). `taxonomy_classes` is the authoritative
taxonomy (seeded once, UI-managed via Compass / Settings).

What still validates inputs before insertion: per-source **format validators**
(`src/validation/source_format_validator.py`, invoked inside each `sync_<source>()`),
and the read-only pre-sync preview (`POST /management/import/preview`).

---

## Section 6: Identity Layer

**Files**: `src/identity/normalizer.py`, `src/identity/identity_sync.py`, `src/identity/authority_resolver.py`

### 6.1 Canonical ID Format

Pattern: `{MARKET}_{TYPE}_{CODE}`

| Example | Meaning |
|---------|---------|
| `CN_FUND_000001` | Chinese mutual fund |
| `US_STK_AAPL` | US stock (Apple) |
| `HK_STK_00700` | Hong Kong stock (Tencent) |
| `RSU_AMZN` | Amazon RSU |
| `CASH_USD` | Cash in USD |
| `ALTS_Paper_Gold` | Paper gold (aggregated) |
| `INS_CITIC_Life` | Insurance policy |
| `Property_Home` | Real estate asset |

### 6.2 Asset Registry

Table: `asset_registry`

- `canonical_id` — unique asset identifier
- `source_system` — which reader registered this asset
- `asset_class` — from taxonomy (e.g., "CN Equity", "US Equity", "Cash Checking")
- `is_rebalanceable` — **unreliable** — use `taxonomy_classes.is_rebalanceable` instead

### 6.3 Synthetic Assets

`identity_sync.py` registers synthetic assets that don't come from any reader:

- `CASH_USD` as "Cash Checking" with asset class from taxonomy

### 6.4 Financial Summary Manual Classifications

`melt_balance_sheet_to_holdings()` registers these asset IDs with hardcoded classifications:

| Asset ID | Description |
|----------|-------------|
| `Property_Home` | Primary residence |
| `Pension_CNY` | Pension account |
| `Wealth_CMB` | Bank wealth management |
| `CASH_Deposit_*` | Time deposits (3 RMB + 3 USD) |

---

## Section 7: Source Reader Layer

### 7.1 Schwab CSV

**Files**: `src/sources/schwab_reader.py`, `src/sources/schwab_transformer.py`, `src/sources/schwab_sync.py`

| Detail | Value |
|--------|-------|
| Holdings artifact | `Individual-Positions-*.csv` (latest file only, no archive) |
| Transaction artifact | `Individual_*_Transactions_*.csv` |
| `market_price_unit` | **Native USD** (NOT × 7.0). `_update_from_dsa` writes `md.close` (native USD); transformer must match. |
| `cost_price_unit` | **Native USD** = `cost_basis / quantity` (Schwab total cost ÷ shares). |
| `market_value` | **CNY** = `market_value_usd × USD_TO_CNY_RATE` (initial), overwritten by `_update_from_dsa` with live FX on next DSA run. |
| `USD_TO_CNY_RATE` | `7.0` (initial approximation for `market_value` only — overwritten by live FX from yfinance). |
| Cash | `CASH_USD` extracted from cash balance row. `market_price_unit = cash_balance` (native USD). |
| ETF remap | `US_ETF_*` → `US_STK_*` at insertion boundary |
| Authority | `source_authority.yaml`: `US_STK_*`, `US_ETF_*`, `CASH_USD` → `Schwab_CSV` (priority 8) |

### 7.2 CN Fund Excel

**Files**: `src/sources/cn_fund_reader.py`, `src/sources/cn_fund_transformer.py`, `src/sources/cn_fund_sync.py`

| Detail | Value |
|--------|-------|
| Artifact | `funding_transactions.xlsx` |
| Sheet | Processed tab (raw processor for unprocessed sheets) |
| Currency | Already CNY |
| Authority | `CN_FUND_*` → `CN_Fund_Excel` (priority 8) |

### 7.3 Gold Excel

**Files**: `src/sources/gold_reader.py`, `src/sources/gold_transformer.py`, `src/sources/gold_sync.py`

| Detail | Value |
|--------|-------|
| Artifact | `Gold_transactions.xlsx` |
| Holdings | Per-account holdings (multiple rows) |
| Aggregation | `_aggregate_gold_holdings()` combines per-account → single `ALTS_Paper_Gold` row |
| Authority | `ALTS_Paper_Gold`, `GOLD_*` → `Gold_Excel` (priority 8) |

> **Why aggregation?** PIS stored gold as a single `ALTS_Paper_Gold` entry. The reader has
> per-account breakdown. We aggregate to maintain consistency with historical PIS data and
> simplify allocation calculations.

### 7.4 Insurance Excel

**Files**: `src/sources/insurance_reader.py`, `src/sources/insurance_transformer.py`, `src/sources/insurance_sync.py`

| Detail | Value |
|--------|-------|
| Artifact | `Insurance_Portfolio.xlsx` |
| Holdings sheet | `保险汇总` (Insurance Summary) |
| Market value | `cash_value` column (surrender value) |
| Premium payments | `保费记录` sheet (wide-to-long melt) |
| Cost | Set post-insertion by `_set_insurance_cost_from_premiums()` |
| Authority | `INS_*` → `Insurance_Excel` (priority 8) |

### 7.5 RSU Excel

**Files**: `src/sources/rsu_reader.py`, `src/sources/rsu_transformer.py`, `src/sources/rsu_sync.py`

| Detail | Value |
|--------|-------|
| Artifact | `RSU_transactions.xlsx` |
| Holdings | **Derived from transactions** (net vest qty − sold qty) — NOT read directly |
| Market value | `vest_price × USD_TO_CNY` (cost basis = vest price = taxable income) |
| Price update | Post-insertion: `_update_rsu_prices_from_external_sources()` uses yfinance primary, Financial Summary fallback, × `USD_CNY` rate |
| Authority | `RSU_*` → `RSU_Excel` (priority 5) |

> **RSU cost basis divergence from PIS**: Huinsight uses vest price (= taxable income reported to IRS).
> PIS uses 0 (total wealth approach). This divergence is intentional — documented as known edge case.

### 7.6 Financial Summary Excel

**Files**: `src/sources/financial_summary_reader.py`, `src/sources/financial_summary_transformer.py`, `src/sources/financial_summary_sync.py`

| Detail | Value |
|--------|-------|
| Artifact | `Financial Summary_new.xlsx` |
| Function | `melt_balance_sheet_to_holdings()` |
| Assets extracted | 10 discrete assets: 3 RMB deposits, 3 USD deposits, cash (CNY), property, bank wealth, pension |
| Historical data | Snapshots back to 2019 (all inserted, only latest active per asset) |
| Shadowing | `_shadow_stale_historical_holdings()` — per asset, all but latest `snapshot_date` → `is_shadow=TRUE` |
| Authority | No PIS catch-all fallback; Financial Summary rows remain active reader rows when no higher-priority source overlaps |

> **Why Financial Summary is separate from readers**: The balance sheet rows are metadata
> (net worth summary), not individual holdings records. `melt_balance_sheet_to_holdings()`
> transforms them into discrete holdings rows at insertion time.

> **Reader mapping management (ADR-023, WS-A)**: the Excel column → `asset_id` mapping used
> by `melt_financial_summary_holdings()` (`src/sources/reader_hooks.py`) is no longer a
> hardcoded dict. `src/sync/orchestrator.py::_run_financial_summary_reader` loads the merged
> mapping (code defaults + DB overrides) via `src.services.reader_mappings.load_reader_mappings`
> from the sync's own connection and injects it through the existing config-engine `metadata`
> argument — the reader hook itself stays stdlib+pandas only (no `src.database` import). The
> mapping rows live in the `reader_mappings` table (migration V75) and are UI-managed from the
> Data Sources page's "Manage assets" expander (`docs/api-specs/reader-mappings.md`). A column
> can also be marked `status='ignored'` (V76) — "reviewed, never melted by design" — for FS
> columns that are informational-only duplicates of data another reader already owns (Schwab/
> IBKR US equities, RSU_Excel vesting, Gold Excel paper gold, Insurance Excel policies) or
> computed totals/ratios/liability rows the Balance Sheet report reads separately.

> **Zero-value tombstones (P1 fix, 2026-08-01)**: the melt used to drop every null **and zero**
> cell "to keep the holdings table lean". Because §8.2's historical shadow is keyed **per
> asset_id**, an asset that stopped emitting rows kept its last non-zero row as its own latest
> snapshot and went on counting in net worth forever — the "invisible states" failure class
> (absence indistinguishable from "no update"). Live instance: `CASH_Deposit_BOC_USD` held
> ~¥149K at 2026-07-01 after the owner moved the balance into `Bond_CMB_USD`.
> `_shadow_stale_reader_holdings` cannot catch it — that phase measures a row's age against the
> source's own latest snapshot, and the phantom row *is* at the latest snapshot.
>
> `melt_financial_summary_holdings()` now emits an explicit `market_value = 0` /
> `quantity = 0` row (never `is_shadow` — reader rows are `is_shadow=FALSE` on ingest by
> construction, AGENTS.md Rule 4) in two cases:
> 1. the cell literally contains `0` — an affirmative owner entry; and
> 2. the cell is blank **and** lies in the trailing run of blanks after the column's last
>    non-zero value (`_fs_trailing_blank_tombstones`).
>
> Interior blanks and never-populated columns still emit nothing — the lean-table filter is
> intact for the ~1000 historical blank cells that are followed by a real value. The zero row
> becomes the asset's per-asset MAX snapshot, so §8.2 shadows the phantom on the same sync.
> A mapped column that vanishes from the sheet entirely is **logged, not tombstoned**: a
> rename is indistinguishable from a deletion and zeroing a live asset on that signal is the
> more damaging error.

---

## Section 8: Shadow Pipeline

The shadow pipeline ensures only one active (non-shadowed) row exists per asset at any given
snapshot date, and that reader sources always take precedence over legacy PIS data.

### 8.0 The two `is_shadow` writers (read this first)

Two different stages write the same column with two different meanings:

| Writer | Phase | Meaning of `is_shadow=TRUE` | Reversible? |
|---|---|---|---|
| Shadow pipeline (this section) | P4 | "this row is OBSOLETE" — older snapshot, liquidated stale position, or legacy PIS baseline | No — permanent archival |
| Authority resolution (Section 10) | P5 | "this row LOST the source conflict" — another source is authoritative for this asset on this date | Re-evaluated each sync from `config/source_authority.yaml` |

Consequence for every query: `is_shadow = FALSE` means "active AND authoritative".

**What "latest" means**: the latest holding for an asset is per-asset
`MAX(snapshot_date) WHERE is_shadow = FALSE`, per source — never a global MAX
(QDII funds legitimately lag 2+ days; AGENTS.md Rule 3). An active holding's
*value* can be newer than its *snapshot*: P3 stamps `price_updated_at` /
`price_source` and rewrites `market_price_unit`/`market_value` without touching
`snapshot_date` (which is reader truth for quantity).

**Core invariant**: shadowing direction. Reader rows (`Schwab_CSV`, `CN_Fund_Excel`,
`Gold_Excel`, `Insurance_Excel`, `RSU_Excel`) must **never be shadowed by legacy PIS
precedence** — only by the pipeline's own staleness rules (P4: older snapshots,
liquidated stale positions) or by authority resolution between sources (P5). A
reader-sourced asset whose *latest* row is shadowed with no active replacement is a
broken pipeline — enforced by integrity Checks 6 (`shadow_mutual_exclusion`) and 9
(`reader_rows_not_all_shadowed`).

### 8.1 Stale Reader Shadow

**Function**: `_shadow_stale_reader_holdings(connector, empty_verified_sources)` —
`src/sync/phases/_shadow.py`

For each source in `READER_HOLDING_SOURCES`, a row is shadowed only when BOTH hold: it is older
than that source's latest snapshot by more than `STALE_READER_SHADOW_DAYS` (7), AND the asset shows
a post-snapshot liquidation signal (a post-snapshot sell exists, no later post-snapshot buy, net
post-snapshot quantity ≤ 0). The sell requirement is what protects QDII T+1/T+2 laggards from being
shadowed just for being a few days behind. See 8.6 for the `empty_verified_sources` carve-out.

### 8.2 Historical Shadow (Financial Summary)

**Function**: `_shadow_stale_historical_holdings(connector)` — `orchestrator.py:723`

For `Financial_Summary_Excel` only: per asset_id, all snapshot dates except the maximum →
`is_shadow=TRUE`. This preserves historical data in DB for trend analysis while keeping only
the latest snapshot active.

### 8.3 Legacy Shadow

**Function**: `_shadow_legacy_holdings(connector, reader_sources)` — `orchestrator.py:753`

Sources shadowed: `PIS`, `PIS_SQLite`, `PIS_Excel`, `AIA`, `PIS_Historical`

Logic: For each asset_id covered by ANY reader source (including Financial_Summary_Excel),
ALL legacy source rows → `is_shadow=TRUE`. This is **asset-level** (not date-level).

**Example**: `CN_FUND_000001` exists in both PIS and CN_Fund_Excel. The PIS row gets
`is_shadow=TRUE` regardless of snapshot date. The CN_Fund_Excel row is authoritative.

### 8.4 Sold-Asset Shadow

**Code**: `orchestrator.py:1797-1826`

PIS phantom rows for fully-sold assets: if an asset has sell transactions and no active
reader coverage → `is_shadow=TRUE`. Handles the CN_FUND_000002 case where PIS auto-generated
`Adjustment_Buy` transactions for zero-balance positions.

### 8.5 Co-authority Tombstone (C3.2, P4 step 2.4.12.6)

**Function**: `_shadow_coauthority_tombstone(connector)` — `src/sync/phases/_shadow.py`

**ACAT gap**: When an asset transfers from Schwab to IBKR via ACAT, Schwab simply omits it from
the next CSV — there is no sell transaction. The stale-reader shadow phase (8.1) requires a sell
signal and therefore misses this, leaving a stale Schwab row active alongside the IBKR row → double-count.

**What it does**: For each co-authority broker source (those in rules with ≥2 declared authorities,
e.g. `{Schwab_CSV, Broker_IBKR}`), it finds asset_ids present in an older snapshot but absent from
the source's latest file. Each such dropped asset has its stale active row set to `is_shadow=TRUE`
and a zero-qty tombstone row (`quantity=0, is_shadow=TRUE, price_source='coauthority_tombstone'`) is
inserted at `date.today()` for idempotent re-sync. Scoped to co-authority broker sources only —
CN funds, Gold, Insurance, and RSU are excluded to avoid QDII-lag false positives.

**Integrity gate**: check #6 (`shadow_mutual_exclusion`) exempts (a) zero-qty tombstone rows and
(b) reader rows superseded by a `Consolidated` source row. Qty-bearing unsuperseded reader rows
remain a violation — the original Gold/Insurance protection is preserved.

### 8.6 Empty-Source Tombstone (task #16, P4 step 2.4.11.9)

**Function**: `_tombstone_empty_verified_sources(connector, empty_verified_sources, as_of_date)`
— `src/sync/phases/_shadow.py`

**Empty-source gap**: 8.1 and 8.7 both shadow rows whose `snapshot_date` is *older* than their
source's `MAX(snapshot_date)`. A source that emits **no rows at all** (total liquidation, empty
workbook) never advances `MAX`, so its previous rows sit exactly *at* that date, `<` is false, and
the whole last snapshot stays active forever. Same "invisible states" class as the V7.8.1
Financial-Summary blank-column phantom.

**The signal**: `sync_config_source` now returns `read_status` alongside the DataFrames.
`READ_STATUS_OK` is the *only* affirmative value — artifact located, format validator passed, parse
did not raise. P2 (`_record_empty_source_signal` in `orchestrator.py`) adds a source to
`SyncResult.empty_verified_sources` only on `READ_STATUS_OK` **and** zero holdings rows. Disabled,
missing file, failed validator, raised reader, and any unrecognised status all stay out and instead
produce a loud `[EMPTY-SOURCE]` warning that changes no data. Zeroing a live portfolio because a
file was still uploading is the more damaging error, so the ambiguous case is loud, never
destructive.

**What it does**: for each verified-empty source, writes an **active** `quantity = 0,
market_value = 0` row (`price_source = 'empty_source_tombstone'`, `is_shadow = FALSE`) at
`as_of_date` for every asset whose latest active row is still non-zero, via
`ON CONFLICT … DO UPDATE` so a same-day row is zeroed in place. Idempotent: an asset already at
zero is skipped.

**Why an active zero and not `is_shadow`**: integrity check #6 (`shadow_mutual_exclusion`,
BLOCKING) inspects reader rows at each source's newest **qty-bearing** snapshot_date and fails when
one is shadowed without a `Consolidated` supersession. For an empty source that row *is* the last
real snapshot, so shadowing it trips the gate. An active zero dated later wins every per-asset
`MAX(snapshot_date)` query instead and drops the asset from net worth (which filters
`market_value > 0`). Checks #5 and #10 already tolerate zero-valued rows. This also matches the
V7.8.1 FS precedent.

**Freeze**: `_shadow_stale_reader_holdings` (8.1) and `_shadow_stale_non_tradable_holdings` (8.7)
both take `empty_verified_sources` and **skip** those sources for that sync, so nothing shadows the
last qty-bearing row out from under the tombstone. Normal sweeping resumes the moment the source
returns data.

### 8.7 Non-Tradable Staleness Sweep

**Function**: `_shadow_stale_non_tradable_holdings(connector, empty_verified_sources)` —
`src/sync/phases/_shadow.py`

For `Insurance_Excel`, `RSU_Excel`, `Gold_Excel` (`NON_TRADABLE_HOLDING_SOURCES` in `_common.py`):
these sources carry no buy/sell stream, so 8.1's liquidation signal cannot apply, but each file is
a COMPLETE snapshot — every row older than the source's latest active snapshot date is shadowed.
See 8.6 for the empty-source carve-out.

### 8.8 Permanent Persistence

**Function**: `HoldingsAggregator.apply_authority_rules(connector, date)` — `src/sync/holdings_aggregator.py`

Once `is_shadow=TRUE`, it stays `TRUE`. This function runs on each distinct snapshot date
across all reader sources. It does NOT re-activate previously shadowed rows.

---

## Section 9: Post-Insertion Processing

These steps run after all 6 readers have inserted their data and the shadow pipeline has run.

### 9.0 Live Price Refresh (V5.2.0+)

**Function**: `MarketDataService.refresh_portfolio_prices(connector)` — `src/market_data/service.py`

**When**: P3 — immediately after all reader/adapter ingestion (P2), before the shadow pipeline (P4).

**What it does**:

1. Fetches live OHLCV quotes from yfinance (US stocks/ETFs/RSU), akshare (CN funds), and SGE (gold) for all active assets.
2. Upserts quotes into `market_daily` table.
3. Calls `_update_from_dsa(connector, fx_rates)` to push live prices into `holdings`.

**`_update_from_dsa` trigger condition** (fires for any row where):

```sql
WHERE holdings.price_updated_at IS NULL
   OR CAST(holdings.price_updated_at AS DATE) < md.date   -- new day
   OR md.close != holdings.market_price_unit               -- condition C
```

Condition C is the critical one. Before V5.2.0, `market_price_unit` was stored as USD×7.0 (≈703), while `md.close` was native USD (≈100). Condition C was always `TRUE` → every sync triggered a full market-value recalculation using whatever live FX was available, causing portfolio oscillation.

**Fields written by `_update_from_dsa`**:

| Field | Value | Unit |
|-------|-------|------|
| `market_price_unit` | `md.close` | Native USD (Schwab/RSU) or CNY (CN assets) |
| `market_value` | `quantity × md.close × fx_rate` | Always CNY |
| `price_updated_at` | `md.date` | Date of price |

**FX rate source**: `fetch_fx_rates()` in `src/market_data/fetchers/yfinance_fetcher.py`
- Fetches `USDCNY=X` from yfinance.
- **Default fallback**: `{"USD": 7.0}` if yfinance fails (e.g., weekends, network issues).
- FX oscillation risk: if live fetch alternates between real rate (≈6.83) and fallback (7.0) across syncs, `market_value` (CNY) oscillates by ≈(7.0/6.83 − 1) × portfolio ≈ 2.5% per sync. Since V5.2.0, `market_price_unit` is stable (native USD), so only `market_value` fluctuates with FX — which is correct economic behavior.

### 9.1 FIFO Cost Basis Backfill

**Function**: `_backfill_fifo_cost_basis(connector)` — `orchestrator.py:808`

Reader holdings with `NULL cost_price_unit` get FIFO cost computed from transaction history
using `CostBasisCalculator`. The transaction source is selected by
`select_transaction_sources()` from `src/services/transaction_source_selector.py`.

**Only applies to**: Active reader holdings where `cost_price_unit IS NULL`.

**V5.2.0 FX-migration guard**: Before FIFO recompute, a one-time migration NULLs stale CNY
cost entries for Schwab/RSU holdings where `cost_price_unit / market_price_unit > 4.5`. This
ratio fingerprints the old USD×7.0 storage (ratio ≈ 7). After NULL-out, FIFO recomputes in
native USD using `CostBasisCalculator` (which no longer converts USD→CNY internally).

**`CostBasisCalculator` native-currency behavior (V5.2.0+)**:
- Works in native currency (USD for Schwab/RSU transactions).
- No internal FX conversion — the caller is responsible for FX at display time.
- `native_currency` attribute auto-detected from first non-CNY transaction.

**C3.3 Co-authority merged-ledger FIFO** (V6.4.0+):

For co-authority assets (`US_STK_*`, `US_ETF_*`, `CASH_USD`) authoritative from both
`Schwab_CSV` and `Broker_IBKR`, the cost basis is a **lifetime MERGED-ledger FIFO per
`asset_id`** across both brokers. When an asset transfers Schwab→IBKR via ACAT:

- `select_transaction_sources()` resolves co-authority via the **authority RULE**
  (i.e. which sources are declared co-authority for this asset), NOT the latest-holding
  source. This returns ALL co-authority sources that have transactions — e.g.
  `['Broker_IBKR', 'Schwab_CSV']` — even if only IBKR carries the surviving holding.
  Without this, IBKR-only selection would drop Schwab buy lots → cost basis $0.

- **ACAT transfer legs are non-realizing**: `transfer_in` and `transfer_out` transaction
  types (and Schwab `Security Transfer` mapped to `other`) fall through
  `_process_single_transaction` as no-ops. Lots persist across the broker boundary;
  cost basis carries from Schwab into the IBKR position.

- **C3.3 RISK-3 null-out**: Before the backfill loop, co-authority broker holdings
  (e.g. `Broker_IBKR`) with `cost_price_unit = 0` and `quantity > 0` are NULLed so
  the FIFO loop picks them up and recomputes from the merged ledger. `CASH_*` assets
  are excluded (cash cost basis is not FIFO). The existing Schwab/RSU ratio-guard runs
  first; the co-authority null-out runs immediately after.

### 9.2 Insurance Cost from Premiums

**Function**: `_set_insurance_cost_from_premiums(connector)` — `orchestrator.py:902`

For each `INS_*` holding:

- `cost_price_unit` = cumulative sum of premium payments
- `quantity` = 1 (forces P&L formula to work correctly)
- `market_value` = `cash_value` (surrender value); fallback = cumulative premiums

### 9.3 Non-Tradeable P&L Zeroing

**Function**: `_zero_pl_for_non_tradeable_assets(connector)` — `orchestrator.py:958`

For non-tradeable assets:

- `Pension_*` with missing cost:
  - `cost_price_unit = market_value`, `quantity = 1` (keeps unrealized P&L at 0)
- `Property_*` with missing cost:
  - First try legacy shadow history:
    - `source_system IN ('PIS', 'PIS_Historical')`
    - `is_shadow = TRUE`
    - latest `cost_price_unit > 0`
  - If found, reuse that historical cost basis (`quantity = 1`)
  - If not found, fallback to `cost_price_unit = market_value` and log warning
- `INS_*`:
  - always normalized to `cost_price_unit = market_value`, `quantity = 1` in this step
  - insurance-specific premium-based cost is handled in Section 9.2 before this step

### 9.4 RSU Price Update

**Function**: `_update_rsu_prices_from_external_sources(connector, config)` — `orchestrator.py:1016`

Updates `market_price_unit` for `RSU_*` holdings:

1. Primary source: yfinance (live AMZN quote) — replaced AIA JSON in V5.2.1
2. Fallback: Financial Summary Excel (last known price from balance sheet)
3. Both × `USD_CNY` rate

### 9.5 Cleanup

**Code**: `orchestrator.py:1717-1771`

| Step | Action |
|------|--------|
| Delete `UNKNOWN_*` holdings | Metadata artifacts from PIS phantom assets (zero-value only) |
| Delete `UNKNOWN_*` transactions | PIS phantom adjustment transactions |
| Migrate `GOLD_PAPER_*` transactions | → `ALTS_Paper_Gold` canonical ID |
| Normalize `CASH_` asset_class | `'现金'` → `'Cash Checking'` in `asset_registry` |

### 9.6 Legacy Prefix Normalization

**Function**: `_normalize_legacy_prefixes(connector)` — `orchestrator.py:270`

Applied at start of reader sync block:

| Old Prefix | New Prefix |
|------------|------------|
| `Ins_` | `INS_` |
| `RSU_RSU_` | `RSU_` |

---

## Section 10: Authority Resolution

**Files**: `config/source_authority.yaml`, `src/identity/authority_resolver.py`, `src/sync/holdings_aggregator.py`

### 10.1 Priority Rules (`config/source_authority.yaml`)

| Pattern | Authority | Priority |
|---------|-----------|----------|
| `RSU_*` | `RSU_Excel` | 5 |
| `US_STK_*`, `US_ETF_*`, `CASH_USD` | `Schwab_CSV` | 8 |
| `CN_FUND_*` | `CN_Fund_Excel` | 8 |
| `GOLD_*`, `ALTS_Paper_Gold` | `Gold_Excel` | 8 |
| `INS_*` | `Insurance_Excel` | 8 |
| `Financial_Summary_Excel` assets (catch-all) | `Financial_Summary_Excel` | 9 | ← AIA catch-all removed in V5.7.0 |

Lower number = higher priority. Multiple rules can match; lowest priority number wins.

### 10.2 HoldingsAggregator

For each distinct snapshot date (from reader sources + today):

1. Loads all holdings for that date
2. For each `asset_id`, calls `AuthorityResolver.resolve()` to find the authoritative source
3. Sets `is_authoritative=TRUE` for the winning source row; `FALSE` for others
4. Permanent: once `is_shadow=TRUE`, not changed by authority resolution

### 10.3 AuthorityResolver

Uses pattern matching (fnmatch-style) against `config/source_authority.yaml`. Returns
the source with the lowest priority number that matches the asset ID.

---

## Section 11: Post-Sync Validation

### 11.1 Cost Basis Validation

**Threshold**: 1%
Compares FIFO-calculated cost basis against the stored `cost_price_unit` in holdings.
Discrepancies above threshold are logged as warnings.

### 11.2 Allocation Drift

**Threshold**: 5%
Compares current allocation percentages (from `current_allocations`) against target
allocations. Drift above threshold triggers a warning.

### 11.3 Divergence Check (Authority vs Shadow)

**Threshold**: 10% (warning only, does not fail sync)
Compares market value of authoritative holdings against shadow (legacy PIS) holdings for
the same asset. Significant divergence may indicate data quality issues.
Report generated at: `reports/discrepancy_*.md`

---

## Section 12: Integrity Gate — 15 Self-Derived Checks

**File**: `src/validation/data_integrity_gate.py`

Run with: `python main.py --check-integrity` (human-readable) or `python main.py --check-integrity --json` (machine-readable, non-zero exit on failure).

All 15 checks are read-only (non-mutating). Total runtime < 5 seconds.

**Canonical count**: `INTEGRITY_CHECK_COUNT = len(INTEGRITY_CHECKS)` in `data_integrity_gate.py`.
Checks 13–14 are labeled by their historical bug IDs (#19/#20) in older session notes.

| # | Check Name | Description | Threshold |
|---|-----------|-------------|-----------|
| 1 | `net_worth_plausible` | Net worth (per-asset latest) is in reasonable range | 1M–100M CNY |
| 2 | `no_raw_usd_in_schwab_holdings` | No Schwab non-cash holding with `qty > 1` and `market_value < 500 CNY` | 0 suspect rows |
| 3 | `twr_in_range` | Annualized TWR is within range | -80% to +200% |
| 4 | `xirr_proxy_in_range` | XIRR proxy is within range | -80% to +200% |
| 5 | `active_holdings_have_positive_value` | No active holdings with NULL or negative `market_value` | 0 violations |
| 6 | `shadow_mutual_exclusion` | Reader source rows (`Schwab_CSV`, etc.) must never be shadowed | 0 shadowed reader rows |
| 7 | `cost_basis_ratio_under_10x` | `market_value / cost_basis` never exceeds 10x for any single holding | < 10x |
| 8 | `cash_pnl_is_zero` | CNY-denominated `CASH_*` holdings have P&L ratio < 50%. **Excludes `CASH_USD`** (cost_price_unit is native USD after V5.2.0; arithmetic on CNY market_value vs USD cost would always fail). | < 50% P&L ratio |
| 9 | `reader_rows_not_all_shadowed` | At least some reader rows are active (not all shadowed) | > 0 active reader rows |
| 10 | `no_extreme_single_asset_change` | No single asset changes > 15x in 14 days. **Excludes `CASH_*`** — cash balances can legitimately drop to near-zero when converted to securities (e.g., sell CASH_USD to buy SGOV). | < 15x change |
| 11 | `net_worth_cross_endpoint_consistency` | Net worth computed 3 different ways agrees within 0.1% | < 0.1% spread |
| 12 | `twr_xirr_consistency` | TWR and XIRR spread is within bounds | < 25% spread |
| 13 | `trade_log_verdict_consistency` *(was "check #19")* | Trade-log verdicts are consistent with linked trade records | 0 inconsistencies |
| 14 | `insight_trade_links_no_orphans` *(was "check #20")* | `insight_trade_links` table has no orphaned rows (all insights and trade logs exist) | 0 orphans |
| 15 | `consolidated_equals_sum` *(C3.4)* | Each active `Consolidated` row's market_value (and, for non-cash, quantity) equals the sum of its contributing co-authority broker rows' latest values | rel ≤0.5% or abs ≤1.0 |

---

## Section 13: Database Bootstrap & Migration Layer (V5.11.0 — Pass D)

**Context**: Prior to V5.11.0, the schema was defined in four places and the server lifespan called only `run_migrations()`, never `initialize_schema()`. This left classification tables and newer sentinel columns absent on servers that hadn't synced. See ADR-011.

### Bootstrap sequence

All entry points (server lifespan, CLI `--init`, CLI sync/check-integrity) now call `bootstrap_database(connector)` in `src/database/schema.py`:

```
1. initialize_schema(connector)   # schema.sql — all CREATE TABLE IF NOT EXISTS (idempotent)
2. connector.run_migrations()     # incremental ALTERs, new tables, indexes, data-fixes
3. _assert_bootstrap_complete()   # raises if any required table/column/index is missing
```

| File | Role |
|------|------|
| `src/database/schema.sql` | Canonical base: ~40 `CREATE TABLE IF NOT EXISTS`, sequences, indexes. Run by `initialize_schema()`. |
| `src/database/connector.py:run_migrations()` | Migrations 008–15. Numbered order; each migration uses `_run_migration(label, stmt)` which logs and collects non-idempotent failures. |
| `src/database/schema.py:bootstrap_database()` | Single entry point. Calls `initialize_schema` → `run_migrations` → `_assert_bootstrap_complete`. |
| `src/database/schema.py:_assert_bootstrap_complete()` | Post-bootstrap assertion. Checks `_REQUIRED_TABLES` (20), `_REQUIRED_COLUMNS` (3 sentiment cols), `_REQUIRED_INDEXES` (4 hot-path). Raises `RuntimeError` on any gap — server refuses to start. |

### Migrations added in Pass D

| Migration | What | Why |
|-----------|------|-----|
| 13 (a–f) | 6 classification tables (`taxonomy_classes`, `asset_tiers`, `risk_profiles`, `risk_profile_allocations`, `classification_rules`, `classification_audit_log`) | Previously only created during orchestrator sync. Server without a completed sync lacked them. |
| 14 (a–c) | `market_sentiment_cache.is_stale`, `.last_refresh_attempt`, `.error_detail` | Previously only added by `ensure_sentiment_table()` at request time — failed on `read_only=True` connections (Issue #6 class). |
| 15 (a–d) | Hot-path indexes: `idx_holdings_source_system`, `idx_holdings_is_shadow`, `idx_transactions_asset_id`, `idx_trade_logs_linked_transaction_id` | Back per-source/shadow queries and post-sync trade-linker FK rebuild. |

### Loud-failure doctrine

`_run_migration(label, stmt)` catches only DuckDB "already exists"/"duplicate column" (idempotency-safe). Any other failure is `logger.warning(..., exc_info=True)` and appended to `connector._migration_failures`. `_assert_bootstrap_complete()` raises on any collected failure. All pre-existing silent `except: pass` blocks in `run_migrations()` were upgraded to `logger.warning`.

### Deferred (not in V5.11.0)

- Single version-ledger migration runner + iterating `migrations/` directory (orphaned 001–007 files not yet wired).
- Replacing naive `split(';')` in `initialize_schema()` with a proper DuckDB multi-statement executor.
- Dropping orphaned tables (`committee_decisions`, `market_events`, etc.) — pending usage audit.
- `POST /goals` read-only write-path bug (see `docs/known-issues.md`).

---

## Section 14: Configuration

### `config/settings.yaml`

```yaml
finance_dir: "./data/import"  # owner path shown here is illustrative only

source_registry:
  schwab:
    enabled: true
    data_dir: null
    file_patterns:
      positions: "Individual-Positions-*.csv"
      transactions: "Individual_*_Transactions_*.csv"
  cn_fund:
    enabled: true
    data_dir: null
    file_patterns:
      workbook: "funding_transactions.xlsx"
  gold:
    enabled: true
    data_dir: null
    file_patterns:
      workbook: "Gold_transactions.xlsx"
  insurance:
    enabled: true
    data_dir: null
    file_patterns:
      workbook: "Insurance_Portfolio.xlsx"
  rsu:
    enabled: true
    data_dir: null
    file_patterns:
      workbook: "RSU_transactions.xlsx"
  financial_summary:
    enabled: true
    data_dir: null
    file_patterns:
      workbook: "Financial Summary_new.xlsx"

validation:
  cost_basis:
    threshold_pct: 1.0
  allocations:
    drift_threshold_pct: 5.0
  divergence:
    threshold_pct: 10.0
```

### `config/source_authority.yaml`

Priority-ordered rules for which source is authoritative per asset ID pattern.
See Section 10.1 for full table. Lower `priority` number = higher authority.

## Section 15: Appendix — Known Edge Cases

> These edge cases have caused production bugs. Every agent should read this section before
> touching pipeline code.

### A. Schwab CSV Security Types

Schwab uses verbose security type strings: `"ETFs & Closed End Funds"` (not `"ETF"`).
Always use substring matching for type detection.

### B. RSU Cost Basis Divergence from PIS

Huinsight RSU cost basis = vest price (taxable income). PIS RSU cost basis = 0 (total wealth model).
The divergence is **intentional**. Do not attempt to reconcile them.

### C. Insurance `cash_value` Column

Insurance policies have two values: face value (death benefit) and surrender value (cash value).
Huinsight uses `cash_value` (surrender value) as `market_value` because it represents the actual
economic value the holder can realize.

### D. Financial Summary Historical Snapshots

The Financial Summary Excel has balance sheet rows going back to 2019. These are all inserted
into DuckDB and shadowed (except the latest per asset). This enables historical trend analysis
via the API. **Do not delete old Financial Summary rows** — they are needed for trend data.

### E. PIS `Cost_Price_Unit` Column Trap

The PIS Excel exports `Cost_Price_Unit` as the **total cost of all historical purchases**,
not the FIFO remaining cost basis. Using this column directly will overstate cost basis.
Huinsight computes FIFO cost from transaction history instead.

### F. Complex Fund Transaction History

`CN_FUND_000001` has a complex transaction history (partial redemptions, reinvestments) that
triggers FIFO edge case warnings. These warnings are suppressed in production — the computed
cost basis is within acceptable tolerance.

### G. RSU Price Update Source Priority

RSU market prices: yfinance (primary, live quote) → Financial Summary Excel (fallback, balance sheet price) → vest price (last resort).
AIA JSON was replaced by yfinance in V5.2.1 (see CHANGELOG). Do not remove the Financial Summary fallback — it activates when yfinance is unavailable (network errors, weekends).

### H. QDII Fund T+2 Settlement Lag

QDII funds (e.g., `CN_FUND_000002`) settle T+2. After a sync, the QDII holding snapshot date
may be 2 days behind other CN funds. This is expected — **never use global `MAX(snapshot_date)`**.
Always group by asset or source.

### I. Multi-Account Gold Aggregation

Gold is held in multiple accounts (bank A, bank B, etc.). The reader produces one row per
account. `_aggregate_gold_holdings()` sums them to `ALTS_Paper_Gold` with account breakdown
logged in `sync_audit_logs`. The breakdown is preserved for reconciliation but not exposed
in the holdings table.

---

### J. TWR/Risk Metrics Historical Data Source

TWR and risk metrics (`Sharpe`, `Sortino`, `max_drawdown`, `volatility`) use `balance_sheet_monthly` as the primary historical value series instead of the `holdings` table spine. The `holdings` table's shadow pipeline produces too few `is_shadow=FALSE` dates for meaningful time-series analysis. When `include_asset_ids` filtering is active (exclude non-rebalanceable), the balance sheet total is adjusted by subtracting estimated non-rebalanceable portions (property, insurance), which is approximate. Edge-trimming is no longer necessary as BS monthly data is consistently populated.

### K. Native-Currency P&L Invariant (V5.2.0+)

**The invariant** (must be preserved — breaking it causes FX-polluted P&L or oscillation):

| Field | Schwab/RSU | CN/Gold/Insurance | Cash CNY |
|-------|-----------|-------------------|----------|
| `market_price_unit` | Native USD | Native CNY | Native CNY |
| `cost_price_unit` | Native USD | Native CNY | 0 (or native CNY) |
| `market_value` | CNY (via live FX) | CNY | CNY |

**Never write `market_price_unit = price × 7.0`** for Schwab/RSU. This breaks condition C in `_update_from_dsa` and causes portfolio oscillation whenever FX data is unavailable.

**Never compute `(market_value − cost_price_unit × qty)`** for USD assets in SQL — it mixes CNY and USD. Use the API route helpers which apply currency-aware P&L:
- `calculate_unrealized_pl_values()` in `src/api/routes/performance.py`
- Returns `(cny, native)` tuple: USD unrealized = `(market_price_unit − cost_price_unit) × qty`

### L. FX Oscillation Pattern

**Symptom**: Portfolio toggles between two values ±1–3% on consecutive syncs (e.g., ~¥5.57M ↔ ~¥5.60M). Net worth change shows 0% one sync, ±0.4% next.

**Root cause**: `_update_from_dsa` condition C fires because stored `market_price_unit` doesn't match `md.close`. If stored in CNY (≈703) but DSA returns native USD (≈100), condition C is always TRUE. `market_value` recalculates using whichever FX rate was available — alternating between live (~6.83) and default (7.0) on weekends.

**Diagnostic check**:
```sql
-- Ratio should be 0.9–1.3 for Schwab/RSU (native USD price vs native USD cost)
-- If ratio is 5–10, market_price_unit was stored as USD×7.0 (pre-V5.2.0 bug)
SELECT asset_id, market_price_unit, cost_price_unit,
       ROUND(market_price_unit / NULLIF(cost_price_unit, 0), 2) AS ratio
FROM holdings
WHERE source_system = 'Schwab_CSV' AND is_shadow = FALSE
ORDER BY ratio DESC;
```

**Fix**: `schwab_transformer.py` must store `market_price_unit = float(row['price'])` (native USD). Run `_backfill_fifo_cost_basis` to clear stale CNY cost entries.

### M. Trade Log FK Reset (V5.2.0+)

`_replace_transactions()` in the orchestrator deletes and reinserts transaction rows, assigning new auto-increment IDs. Before V5.2.0, `trade_logs.linked_transaction_id` still referenced the deleted IDs, causing FK constraint violations on the next sync for Schwab_CSV, CN_Fund_Excel, Gold_Excel, RSU_Excel.

**Fix**: `_reset_trade_log_links()` is called before each delete path in `_replace_transactions()`. It:
1. NULLs `linked_transaction_id` for affected assets.
2. Sets `verification_status = 'pending'`.
3. The post-sync `trade_linker` rebuilds links from scratch using the new IDs.

---

*Document Version: V6.1 — 2026-06-10 (Data Layer Transformation, Workstream A)*
*Created: 2026-01-27 (original) | Updated: 2026-05-22 (V5.7.0: AIA catch-all rule removed from authority table, AIA fully deprecated)*
*Source references: `src/sync/orchestrator.py:1354-1999`, `src/validation/data_integrity_gate.py:93-113`, `src/financial_analysis/snapshot_provider.py`, `src/market_data/service.py`, `src/sources/schwab_transformer.py`*
