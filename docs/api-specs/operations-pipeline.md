# API Spec: Pipeline Status & Source Freshness Panel (A3b)

**Version**: 1.0 (2026-06-10)
**Base path**: backend routes have NO `/api` prefix; frontend calls `/api/operations/pipeline` (vite proxy strips `/api`).
**Page**: Operations › Sync / Import History (`/import`, `pages/ImportWorkbench.tsx`) — new "Pipeline" section above the runs list.

## Section A — API Contract

### `GET /operations/pipeline`

Returns the declarative pipeline topology (from `src/sync/phases/manifest.py`
PIPELINE_MANIFEST), the most recent sync run summary with per-phase step
results, and per-source data freshness computed live from `holdings`.

```typescript
export interface PipelinePhase {
  phase_id: string;            // "P0".."P8"
  name: string;                // e.g. "Live price refresh"
  description: string;
  tables_read: string[];
  tables_written: string[];
}

export interface PipelineStepResult {
  phase_id: string;            // "P0".."P8"
  name: string;                // phase display name from manifest
  status: 'ok' | 'failed';
  duration_ms: number;
  error: string | null;
}

export interface PipelineLastRun {
  id: string;                  // sync_audit_reports.id (UUID)
  timestamp: string;           // ISO local naive, e.g. "2026-06-10T14:03:22"
  integrity_result: string;    // "13/14"
  integrity_status: 'ok' | 'degraded' | 'failed';
  net_worth_after: number | null;     // CNY
  net_worth_change_pct: number | null;
  warning_count: number;
  alert: boolean;
  is_no_change: boolean;
  steps: PipelineStepResult[] | null; // null for runs persisted before steps column existed
}

export interface SourceFreshness {
  source_system: string;       // "Schwab_CSV" | "CN_Fund_Excel" | "Gold_Excel" | "Insurance_Excel" | "RSU_Excel" | "Financial_Summary_Excel"
  display_name: string;        // "Schwab" | "CN Funds" | "Gold" | "Insurance" | "RSU" | "Financial Summary"
  active_assets: number;       // non-shadow assets at per-asset latest snapshot
  latest_snapshot: string;     // "YYYY-MM-DD" — newest snapshot_date among active rows
  snapshot_age_days: number;   // today - latest_snapshot
  total_value_cny: number;     // sum of market_value at per-asset latest active rows
  last_price_refresh: string | null;  // ISO max(price_updated_at) or null (never refreshed)
  price_refreshed_assets: number;     // active assets with price_updated_at NOT NULL
  staleness: 'fresh' | 'aging' | 'stale';  // ≤14d | 15–45d | >45d on snapshot_age_days
}

export interface PipelineStatusResponse {
  phases: PipelinePhase[];     // ordered P0..P8
  last_run: PipelineLastRun | null;  // null if no sync has ever run
  sources: SourceFreshness[];  // ordered by latest_snapshot DESC
  generated_at: string;        // ISO local naive
}
```

Example JSON:

```json
{
  "phases": [
    { "phase_id": "P0", "name": "Backup & schema setup", "description": "Full DB backup (pre-sync-v3) unless dry-run; idempotent creation of classification tables.", "tables_read": [], "tables_written": ["classification tables"] },
    { "phase_id": "P3", "name": "Live price refresh", "description": "Fetch live quotes (yfinance / akshare / SGE) ...", "tables_read": ["holdings"], "tables_written": ["market_daily", "holdings"] }
  ],
  "last_run": {
    "id": "0d1f4c1e-9f1b-4a36-9a8e-1c2b3d4e5f60",
    "timestamp": "2026-06-10T14:03:22",
    "integrity_result": "13/14",
    "integrity_status": "degraded",
    "net_worth_after": 3500000.0,
    "net_worth_change_pct": -1.15,
    "warning_count": 3,
    "alert": false,
    "is_no_change": false,
    "steps": [
      { "phase_id": "P0", "name": "Backup & schema setup", "status": "ok", "duration_ms": 412, "error": null },
      { "phase_id": "P2", "name": "Reader & adapter ingest", "status": "ok", "duration_ms": 8231, "error": null },
      { "phase_id": "P3", "name": "Live price refresh", "status": "ok", "duration_ms": 12894, "error": null }
    ]
  },
  "sources": [
    { "source_system": "RSU_Excel", "display_name": "RSU", "active_assets": 1, "latest_snapshot": "2026-06-10", "snapshot_age_days": 0, "total_value_cny": 55000.0, "last_price_refresh": "2026-06-10T14:03:40", "price_refreshed_assets": 1, "staleness": "fresh" },
    { "source_system": "Financial_Summary_Excel", "display_name": "Financial Summary", "active_assets": 10, "latest_snapshot": "2026-05-01", "snapshot_age_days": 40, "total_value_cny": 2000000.0, "last_price_refresh": null, "price_refreshed_assets": 0, "staleness": "aging" }
  ],
  "generated_at": "2026-06-10T15:30:00"
}
```

### Change to `GET /operations/sync-history/{run_id}`

Detail response gains one field: `steps: PipelineStepResult[] | null` (same
shape as above; null for legacy runs).

### Backend persistence changes (implementation contract)

1. `run_full_sync_v3` (src/sync/orchestrator.py) records one `StepResult` per
   manifest phase in the dispatch loop: `name = spec.phase_id` (e.g. "P3"),
   `status = "ok"` unless the dispatch raised (phases swallow their own
   errors), `duration_ms` measured around the call. Existing finer-grained
   `_record_step` entries (e.g. `live_price_refresh`) keep flowing into
   `SyncResult.steps` unchanged. **P8 special case**: persistence happens
   inside P8 itself, so `_run_phase8_audit` appends a synthetic P8 entry
   (status "ok", duration = phase work up to persistence) to the persisted
   list; the loop-level P8 step exists only in memory.
2. `sync_audit_reports` gains additive column `steps JSON` (same mechanism as
   V4.8's `is_no_change`/`info_messages` columns — schema.sql + idempotent
   ALTER for existing DBs). `persist_sync_audit`
   (src/validation/sync_audit.py) serializes `SyncResult.steps` as
   `[{name, status, critical, error, duration_ms}]`.
3. The endpoint maps stored steps → `PipelineStepResult[]`: keep only entries
   whose `name` matches `^P\d$`, join display `name` from the manifest, map
   status `ok`→`ok`, anything else→`failed`.
4. Freshness SQL: per-asset latest active rows per source (the standard
   latest-per-asset CTE — NEVER global MAX), aggregated per source_system.

## Section B — Data Binding Map

| UI element | API field | Format |
|---|---|---|
| Phase rail node label | `phases[].phase_id` + `phases[].name` | "P3 · Live price refresh" |
| Phase rail node status dot | `last_run.steps[].status` (match by phase_id) | green=ok, red=failed, gray=no data |
| Phase rail node duration | `last_run.steps[].duration_ms` | `< 1000` → "412ms"; else "12.9s" (1 decimal) |
| Phase rail tooltip | `phases[].description` + `tables_written` | plain text, "writes: a, b" |
| Last-run header time | `last_run.timestamp` | "2026-06-10 14:03" (local, no tz suffix) |
| Last-run integrity chip | `last_run.integrity_result` + `integrity_status` | "13/14" — green pill (ok), amber (degraded), red (failed) |
| Last-run NW delta | `last_run.net_worth_change_pct` | "+1.2%" / "−1.2%" with sign, 2 decimals max; gray if null |
| Freshness card title | `sources[].display_name` | as-is |
| Freshness card snapshot | `sources[].latest_snapshot` + `snapshot_age_days` | "2026-06-06 · 4d ago" ("today" when 0) |
| Freshness card badge | `sources[].staleness` | fresh=green, aging=amber, stale=red |
| Freshness card assets | `sources[].active_assets` | integer |
| Freshness card value | `sources[].total_value_cny` | `fmtCNY` helper (existing) |
| Freshness card price line | `last_price_refresh` + `price_refreshed_assets`/`active_assets` | "Prices: 9/9 · Jun 9" or "Prices: file values" when null |

## Section C — Demo Data Markers

No demo/placeholder values. Every field binds to live API data. When
`last_run` is null render the phase rail with all-gray dots and the text
"No sync recorded yet". When `last_run.steps` is null (legacy run) render
dots gray with tooltip "Run pre-dates step tracking".

## Section D — Component Reference

```
┌─ Pipeline ─────────────────────────────────────────────────────────────┐
│  Last sync: 2026-06-10 14:03   [13/14 degraded]   NW Δ −1.15%   ⚠ 3    │
│                                                                        │
│  (P0)──(P1)──(P2)──(P3)──(P4)──(P5)──(P6)──(P7)──(P8)                  │
│   ●     ●     ●     ●     ●     ●     ●     ●     ●                    │
│  0.4s  0.1s  8.2s  12.9s 2.1s  0.8s  0.3s  4.0s  1.2s                 │
│  Backup Ident Ingest Price Shadow Auth  Deriv Valid Gate               │
└────────────────────────────────────────────────────────────────────────┘
┌─ Source Freshness ─────────────────────────────────────────────────────┐
│ ┌─ RSU ────────┐ ┌─ Gold ───────┐ ┌─ Insurance ──┐ ┌─ Schwab ─────┐    │
│ │ [fresh]      │ │ [fresh]      │ │ [fresh]      │ │ [aging]      │    │
│ │ 2026-06-10   │ │ 2026-06-06   │ │ 2026-06-06   │ │ 2026-05-23   │    │
│ │ today        │ │ 4d ago       │ │ 4d ago       │ │ 18d ago      │    │
│ │ 1 asset      │ │ 1 asset      │ │ 15 assets    │ │ 9 assets     │    │
│ │ ¥55,000      │ │ ¥65,000      │ │ ¥38,000      │ │ ¥310,000     │    │
│ │ Prices: 1/1  │ │ Prices: 1/1  │ │ Prices: file │ │ Prices: 9/9  │    │
│ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘    │
│ ┌─ CN Funds ───┐ ┌─ Fin Summary ┐    (grid: 4 per row desktop,         │
│ │ ...          │ │ ...          │     2 per row tablet, 1 mobile)      │
└────────────────────────────────────────────────────────────────────────┘
```

Visual notes: reuse `components/operations/` primitives — `Card`, `Section`,
`StatusPill`, `fmtCNY`. Phase rail: horizontal flex with connector lines
(`border-t`), small mono durations. Place both sections at the TOP of
ImportWorkbench (above the existing runs list), collapsible is not required.
Tailwind for layout; Card CSS vars for surfaces (existing convention).

## Section E — Data Quality Requirements

- Language: English UI labels (existing Operations convention); source
  display names as listed in Section A.
- Currency: CNY via existing `fmtCNY` (¥ + thousands separators, 0 decimals).
- Percentages: 2 decimals max, explicit sign, true minus sign acceptable as
  ASCII "-".
- Dates: `YYYY-MM-DD` for snapshots; `YYYY-MM-DD HH:mm` for timestamps; all
  naive local (DB stores local naive — do NOT convert as UTC, AGENTS.md).
- Durations: ms below 1s, otherwise seconds with 1 decimal.
- Nulls: `last_run` null → empty-state text; `last_price_refresh` null →
  "file values" wording (these sources have no live quote feed).

## Validation checklist

- [x] All response fields typed (Section A interface)
- [x] All bound fields have format specs (Section B)
- [x] Language declared (Section E)
- [x] Realistic example data from the live system (2026-06-10 values)
- [x] Component diagram sufficient for implementation (Section D)
