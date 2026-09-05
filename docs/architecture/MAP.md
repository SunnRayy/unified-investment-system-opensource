# Huinsight Territory Map

> **Agent orientation:** You are in a reader-first DuckDB investment aggregator. The
> orchestrator is the single point of truth for sync ordering. Before editing any file
> over 500 lines, read its entry in this map.

---

## 1. Repository Layout

| Directory | Role |
|-----------|------|
| `src/sync/` | Pipeline orchestration: `orchestrator.py` is the central coordinator |
| `src/sources/` | One reader + transformer per source (Schwab, CN Fund, Gold, Insurance, RSU, Financial Summary) |
| `src/identity/` | Asset ID normalizer, authority resolver |
| `src/validation/` | 16-check integrity gate, source-format validators |
| `src/api/` | FastAPI app (`main.py`) + route modules |
| `src/services/` | Cross-cutting services: LLMClient, compassAllocation, costBasisCalculator, etc. |
| `src/market_data/` | Price fetchers (yfinance, AKShare), scrapers, scheduler |
| `src/storage/` | GCS flush manager |
| `src/data_manager/` | Currency converter, financial analytics |
| `src/financial_analysis/` | XIRR, TWR, P&L calculators |
| `src/database/` | `connector.py` (DuckDB wrapper), `schema.sql` |
| `ux-command-center/` | React/TypeScript frontend (Vite, port 5003) |
| `ux-command-center/src/services/api.ts` | Frontend API client — 2,916 lines, see Large File Warning |
| `docs/architecture/` | Architecture docs (`data-pipeline-v6.md`, `data-sources.md`, `MAP.md`) |
| `docs/decisions/` | Architecture Decision Records (ADRs) |
| `config/` | `settings.yaml`, `source_authority.yaml` |
| `scripts/` | `verify.sh` (7-check gate), baselines |
| `tests/` | pytest test suite (~1400+ tests) |
| `data/` | `unified.duckdb` (gitignored), `backups/` |

---

## 2. Key Modules

| Module | Responsibility | Lines | Entry Point | See |
|--------|---------------|-------|-------------|-----|
| `src/sync/orchestrator.py` | Full sync pipeline — all 6 readers, shadow, FIFO, market data, GCS flush | 2,716 | `run_full_sync_v3()` | ADR-006, MAP.md §4 |
| `src/sources/schwab_transformer.py` | Schwab CSV → holdings rows; stores `market_price_unit` in native USD | ~400 | `transform_holdings()` | ADR-007 |
| `src/sources/rsu_transformer.py` | RSU Excel → derived holdings (vest−sold); cost in native USD | ~300 | `transform_holdings()` | ADR-007 |
| `src/identity/identity_sync.py` | Asset ID normalizer; canonical_id assignment | ~400 | `sync_identity()` | ADR-004 |
| `src/validation/data_integrity_gate.py` | 16-check invariant suite; `INTEGRITY_CHECK_COUNT` | ~400 | `DataIntegrityGate().run_all_checks()` | AGENTS.md Rule 1 |
| `src/api/main.py` | FastAPI app; `/health`, `/health/deep`, startup | ~350 | `app = FastAPI()` | ADR-006 (§/health/deep) |
| `src/api/routes/_errors.py` | `ApiErrorResponse` — Rule 12-compliant error contract | ~100 | `ApiErrorResponse` | AGENTS.md Rule 12 |
| `src/services/cost_basis_calculator.py` | FIFO cost basis in native currency; stale-CNY detection | ~300 | `CostBasisCalculator` | ADR-007 |
| `src/services/llm_client.py` | All LLM calls (Gemini/DeepSeek); model fallback chain | ~350 | `LLMClient().complete()` | ADR-010, AGENTS.md Rule 21 |
| `src/storage/gcs_flush_manager.py` | GCS persistence: flush DuckDB to GCS after sync | ~200 | `GCSFlushManager.flush()` | ADR-006 |
| `ux-command-center/src/services/api.ts` | Frontend API client — all backend calls | 2,916 | (import `api`) | MAP.md §4 |

---

## 3. Data Flow

```
  ┌──────────────────────────────────────────────────────────┐
  │                    6 Reader Sources                       │
  │  Schwab CSV  CN Fund  Gold  Insurance  RSU  Fin.Summary  │
  └──────────────────┬───────────────────────────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────────────────────────┐
  │           src/sync/orchestrator.py                        │
  │           run_full_sync_v3()                              │
  │                                                           │
  │  P0: Backup & schema setup                                │
  │  P1: Identity sync (normalizer → asset_registry)          │
  │  P2: Reader & adapter ingest (6 readers, sequential)      │
  │  P3: Live price refresh (yfinance/akshare/SGE)            │  ← see ADR-009
  │  P4: Shadow pipeline + FIFO/cost normalization            │  ← see data-pipeline-v6.md §8; ADR-007
  │  P5: Authority resolution                                 │  ← see ADR-013
  │  P6: Derived data (allocations)                           │
  │  P7: Validation & decision layer                          │
  │  P8: Sync diff + integrity gate (16 checks) + GCS flush   │  ← AGENTS.md Rule 1; ADR-006
  │  (registry: src/sync/phases/manifest.py PIPELINE_MANIFEST)│
  └──────────────────┬───────────────────────────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────────────────────────┐
  │                 DuckDB (data/unified.duckdb)               │
  │  holdings  transactions  market_daily  balance_sheet_…   │
  └──────────────────┬───────────────────────────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────────────────────────┐
  │              FastAPI (src/api/main.py + routes/)          │
  │  /holdings  /performance  /analytics  /health/deep  etc. │
  └──────────────────┬───────────────────────────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────────────────────────┐
  │         React frontend (ux-command-center/)               │
  │  api.ts → authFetch → backend API (port 8008)            │
  └──────────────────────────────────────────────────────────┘

  GCS (Cloud Run only):
  ┌─────────────────────────────────────────────────────────┐
  │  GCSFlushManager: DuckDB → GCS blob after each sync     │
  │  /health/deep gcs block: blob.exists() check only       │  ← see ADR-006
  └─────────────────────────────────────────────────────────┘
```

---

## 4. Large File Warnings

**Do not edit these files without reading their section in this map first.**

### `src/sync/orchestrator.py` — 2,716 lines

The entire sync pipeline lives here. Step ordering has implicit data-dependency
constraints: FIFO backfill must run after all reader inserts; shadow pipeline must
run before aggregation; GCS flush must be the last step before marking success=True.

Agents editing this file without reading the full step sequence have introduced
step-ordering bugs that pass unit tests but break financial correctness on real data.

**Deferred decomposition ADR:** A decomposition plan (splitting into per-phase modules)
is tracked as a deferred item in `docs/known-issues.md`. No code changes toward this
goal in the current branch.

### `ux-command-center/src/services/api.ts` — 2,916 lines

All frontend API calls. Adding a new endpoint call: add it at the bottom and import
`authFetch` for auth (raw `fetch()` fails with 401 on Cloud Run).

**Deferred splitting ADR:** Tracked in `docs/known-issues.md`.

---

## 5. Key Invariants (Do Not Break)

| Invariant | Source | AGENTS.md |
|-----------|--------|-----------|
| `market_value` always in CNY | ADR-007 | Rule 2 |
| `cost_price_unit` / `market_price_unit` for Schwab/RSU in native USD | ADR-007 | Rule 2 |
| Reader rows must never have `is_shadow=TRUE` | ADR-003 | Rule 4 |
| No global `MAX(snapshot_date)` — always per-asset | data-pipeline-v6.md §3 | Rule 3 |
| All LLM calls through `LLMClient` | ADR-010 | Rule 21 |
| All external HTTP calls through `http_get` | AGENTS.md Rule 22 | Rule 22 |
| GCS health probe is read-only (`blob.exists()` only) | ADR-006 | Rule 23 |
