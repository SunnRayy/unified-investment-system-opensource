# ⚠️ SUPERSEDED — see [`data-pipeline-v6.md`](data-pipeline-v6.md)

> **This document was superseded on 2026-06-10** by
> [`data-pipeline-v6.md`](data-pipeline-v6.md) (Data Layer Transformation,
> Workstream A4). It is kept verbatim for history. **Do not update it** and do
> not cite it for current behavior — notably, the phase numbering below
> (2.3/2.4.x/2.7) was replaced by the P0–P8 manifest, and the DSA market-data
> ingest (step 2.3) no longer runs in the orchestrator.

---

# Data Pipeline Architecture — V5.6.1 (Reader-First + Native-Currency P&L + AI Advisor + Import Adapters)

> **Version**: V5.6.1 — 2026-05-11
> **Previous version**: V5.0.0 — 2026-04-12
> **Previous doc**: `docs/archive/data-pipeline-v3-original-2026-01-27.md` (V3.0/V3.2, PIS/AIA/DSA model)
> **Status**: SUPERSEDED (was: Current)

---

## Section 1: What Changed?

### V4.0 (original)

V4.0 completes the **Reader-First Architecture** migration started in Phase 9 (ADR-003).

| Before (V3.0–V3.2) | After (V4.0) |
|--------------------|---------------|
| PIS (Personal Investment System) was authoritative | **6 Excel/CSV readers** are authoritative |
| AIA provisional transactions | AIA fully deprecated — no holdings or trade reconciliation |
| DSA as market data provider | DSA still used for market OHLCV data |
| 10 integrity invariant checks | **14 self-derived integrity invariant checks** |
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

# Verify data integrity (14 self-derived checks, historically — see data-pipeline-v6.md for current count)
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
| `src/validation/data_integrity_gate.py` | 14 self-derived invariant checks |
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
│  ORCHESTRATOR — run_full_sync_v3()                                      │
│  src/sync/orchestrator.py:1354                                          │
│                                                                          │
│  Phase 0: Backup (pre-sync + pre-reader-insertion)                      │
│           Pre-Sync Validation (freshness gate, taxonomy validator)      │
│           Schema creation (classification tables)                        │
│                                                                          │
│  Phase 1: Identity Sync                                                 │
│           1.1 Taxonomy sync — REMOVED (2026-03-10)                      │
│               taxonomy_classes is now authoritative (seeded once,       │
│               UI-managed). PIS YAML no longer read during sync.         │
│           1.2 Asset registry sync                                        │
│                                                                          │
│  Phase 2: Reader Sync (6 sources in sequence)                          │
│           2.3  Market data (DSA OHLCV)                                  │
│           2.4.5  Schwab CSV                                              │
│           2.4.6  CN Fund Excel                                           │
│           2.4.7  Gold Excel                                              │
│           2.4.8  Insurance Excel                                         │
│           2.4.9  RSU Excel                                               │
│           2.4.10 Financial Summary Excel (melt_balance_sheet)            │
│           2.4.11 Approved Import Adapters (staged rows only)             │
│                  - Adapters upload/validate/stage into control tables     │
│                  - Only explicitly approved adapters are synced           │
│                  - Adapter holdings must store `market_value` in CNY      │
│                  - Sync uses normal holdings/transactions persistence path │
│                                                                          │
│           2.4.11b Live Price Refresh (V5.2.0+):                         │
│                  MarketDataService.refresh_portfolio_prices()           │
│                  → fetches yfinance/DSA live quotes → upserts           │
│                    market_daily → _update_from_dsa()                    │
│                  _update_from_dsa() condition:                          │
│                    price_updated_at IS NULL                             │
│                    OR price_updated_at < md.date (new day)             │
│                    OR md.close != holdings.market_price_unit (C)        │
│                  Sets: market_price_unit = md.close (native USD)        │
│                        market_value = qty × md.close × live_FX (CNY)   │
│                  FX source: yfinance USDCNY=X; default=7.0 on failure  │
│                                                                          │
│           Shadow Pipeline (immediately after all 6 readers insert):     │
│           2.4.12 _shadow_stale_reader_holdings()                        │
│           2.4.13 _shadow_stale_historical_holdings()                    │
│           2.4.14 _shadow_legacy_holdings()                              │
│                                                                          │
│           2.4.15 Cleanup: UNKNOWN_*, GOLD_PAPER_*, CASH_ normalization  │
│                                                                          │
│           Post-Insertion Processing:                                    │
│           2.4.16 _backfill_fifo_cost_basis()                            │
│           2.4.13 _set_insurance_cost_from_premiums()                   │
│           2.4.14 _zero_pl_for_non_tradeable_assets()                   │
│           2.4.15 Sold-asset shadow (PIS phantom rows)                   │
│           2.4.16 _update_rsu_prices_from_external_sources()            │
│                                                                          │
│  Phase 2.7: Authority Resolution                                        │
│             HoldingsAggregator.apply_authority_rules() per snapshot date│
│                                                                          │
│  Phase 3: Derived Data                                                  │
│           3.1 Current allocations sync                                  │
│                                                                          │
│  Phase 4: Post-Sync Validation                                          │
│           4.1 Cost basis (FIFO sync vs calculated, 1% threshold)        │
│           4.2 Allocation drift (current vs target, 5% threshold)        │
│           4.3 Divergence check (authority vs shadow, 10% threshold)     │
│                                                                          │
│  Phase 5: Reconciliation                                                │
│           Holdings, transactions, trade-log linking (bidirectional)     │
│                                                                          │
│  Phase 6: Sync Diff Report + Integrity Gate (14 self-derived checks, historically) │
│           Before/after net worth, Δ%, alert if >30%                    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Section 5: Pre-Sync Validation Layer

### 5.1 Freshness Gate

**File**: `src/validation/freshness_validator.py`

Checks whether the PIS Excel file is newer than the PIS SQLite file. If Excel is significantly
older (threshold: 24 hours), sync is blocked. If within warning threshold (1 hour), a warning
is added but sync proceeds.

- **Why**: Protects against syncing stale data
- **Config**: `validation.freshness.threshold_hours` (default: 24), `warning_threshold_hours` (default: 1)

### 5.2 Taxonomy Validator

**File**: `src/validation/taxonomy_validator.py` *(DEPRECATED)*

> **Removed from sync pipeline**: 2026-03-10 — PIS taxonomy YAML is no longer read during
> `--sync-v3`. The `asset_taxonomy` table is retained for historical reference but no longer
> refreshed. `taxonomy_classes` is the authoritative taxonomy (seeded once, UI-managed via
> the Compass / Settings UI).
>
> The two-taxonomy problem: `asset_taxonomy` stored Chinese names while `asset_registry.asset_class`
> stored English names, causing most JOINs in `performance.py` and `attribution.py` to fail.
> All API routes now use `taxonomy_classes` exclusively.

> **Previous behavior**: Loaded PIS taxonomy YAML and synced to `asset_taxonomy` table. PIS was
> authoritative for taxonomy on conflict.

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

---

## Section 8: Shadow Pipeline

The shadow pipeline ensures only one active (non-shadowed) row exists per asset at any given
snapshot date, and that reader sources always take precedence over legacy PIS data.

**Core invariant**: Reader rows (`Schwab_CSV`, `CN_Fund_Excel`, `Gold_Excel`, `Insurance_Excel`, `RSU_Excel`) must **NEVER** have `is_shadow=TRUE`. This is enforced by integrity Check 6 (`shadow_mutual_exclusion`).

### 8.1 Stale Reader Shadow

**Function**: `_shadow_stale_reader_holdings(connector)` — `orchestrator.py:690`

For each non-historical reader source, any holding row with a snapshot date older than the
maximum snapshot date for that source → `is_shadow=TRUE`.

**Example**: Schwab has holdings at 2026-03-01 and 2026-03-10. The 2026-03-01 rows get
`is_shadow=TRUE`. Only 2026-03-10 rows are active.

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

### 8.5 Permanent Persistence

**Function**: `HoldingsAggregator.apply_authority_rules(connector, date)` — `src/sync/holdings_aggregator.py`

Once `is_shadow=TRUE`, it stays `TRUE`. This function runs on each distinct snapshot date
across all reader sources. It does NOT re-activate previously shadowed rows.

---

## Section 9: Post-Insertion Processing

These steps run after all 6 readers have inserted their data and the shadow pipeline has run.

### 9.0 Live Price Refresh (V5.2.0+)

**Function**: `MarketDataService.refresh_portfolio_prices(connector)` — `src/market_data/service.py`

**When**: Step 2.4.11 — immediately after all 6 readers insert, before the shadow pipeline.

**What it does**:

1. Fetches live OHLCV quotes from yfinance (US stocks/ETFs) and DSA (CN funds) for all active assets.
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

## Section 12: Integrity Gate — 14 Self-Derived Checks

**File**: `src/validation/data_integrity_gate.py`

Run with: `python main.py --check-integrity` (human-readable) or `python main.py --check-integrity --json` (machine-readable, non-zero exit on failure).

All 14 checks (historically — see data-pipeline-v6.md for current count) are read-only (non-mutating). Total runtime < 5 seconds.

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

## Section 14: Appendix — Known Edge Cases

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

*Document Version: V5.6.1*
*Created: 2026-01-27 (original) | Updated: 2026-05-22 (V5.7.0: AIA catch-all rule removed from authority table, AIA fully deprecated)*
*Source references: `src/sync/orchestrator.py:1354-1999`, `src/validation/data_integrity_gate.py:93-113`, `src/financial_analysis/snapshot_provider.py`, `src/market_data/service.py`, `src/sources/schwab_transformer.py`*
