# API Spec — Data Source Management v2 (registry-driven + fetch + lifecycle)

**Status:** Contract (locked for C5 implementation). Supersedes the implicit v1 contract used by the legacy `DataSourceManager.tsx`.
**Workstream:** C5. **Plan:** internal implementation notes.
**Language:** English. **Currency:** CNY (¥), thousands-separated, 0 decimals for net values. **Timestamps:** ISO-8601 UTC; UI renders relative ("3h ago") + absolute on hover.

This spec lets the frontend render the entire data-source panel from the registry payload with **zero hardcoded source knowledge**, manage source config, trigger/track fetches, and reflect file provenance — on both local and Cloud Run (GCS-backed).

---

## Section A: API Contract

### A1. `GET /settings/sources` — registry-driven source list (EXTEND existing)
Existing `SourceConfig` fields are kept; **add** `label`, `authority`, `format`, `can_fetch`, `last_update`.

```typescript
interface SourceConfig {
  // --- identity / config (existing) ---
  key: string;                 // "schwab" | "cn_fund" | "ibkr" | ...
  enabled: boolean;
  reader: string;              // e.g. "ibkr_reader"
  data_dir: string | null;     // null => uses fallback_dir
  file_patterns: Record<string, string>;  // { "flexquery": "IBKR_UIS_Report*.csv" }
  asset_prefixes: string[];    // ["US_STK_","US_ETF_","CASH_USD"]
  // --- resolved (existing, backend-filled) ---
  resolved_dir: string | null;
  fallback_active: boolean;
  file_found: boolean;
  file_path: string | null;
  file_size_bytes: number | null;
  file_modified: string | null;            // ISO
  resolved_files: Record<string, string>;
  // --- NEW (C5) ---
  label: string;               // human display name, e.g. "Interactive Brokers (IBKR)"
  authority: "authoritative" | "co-authority" | "non-authoritative" | "historical-shadow";
  authority_note: string | null;           // e.g. "Co-authority with Schwab for US equities/cash"
  format: "csv" | "xlsx" | "flex_csv";
  can_fetch: boolean;          // true if the reader has a registered fetcher (IBKR=true)
  last_update: { origin: "upload" | "fetch"; at: string } | null;  // most recent file event
}

interface SourceRegistryResponse {
  sources: SourceConfig[];
  fallback_dir: string | null;             // finance_dir
}
```

### A2. `GET /settings/sources/health` — per-source health (UNCHANGED; document)
```typescript
interface SourceHealthEntry {
  reader: string;
  last_sync_at: string | null;
  row_count: number | null;
  net_value_cny: number | null;
  file_path: string | null;
  file_modified: string | null;
  file_size_bytes: number | null;
  file_stale: boolean;                      // (now - mtime).days > 7
  status: "ok" | "stale" | "pending_sync" | "missing" | "never_synced" | "unknown";
}
interface SourceHealthResponse { sources: SourceHealthEntry[]; last_sync_at: string | null; all_healthy: boolean; }
```

### A3. `POST /settings/sources/fetch/{reader}` — trigger a fetch (NEW)
Runs the reader's registered fetcher (IBKR: Flex Web Service), writes the timestamped file to the reader's `data_dir`, **pushes it to GCS** (`upload_source_to_gcs`, cloud only), records a `fetch` event, applies retention (keep latest 3), and `mark_dirty()`. 400 if the reader has no fetcher (`can_fetch=false`). Token is server-side only — never accepted from or returned to the client.

```typescript
// Request: none (token/query-id read from server env/secret)
interface FetchResult {
  reader: string;
  file_path: string;           // newly written file
  file_size_bytes: number;
  line_count: number;
  fetched_at: string;          // ISO
  pruned: string[];            // filenames removed by retention (local+GCS)
}
// Errors: 400 unknown/no-fetcher; 502 FlexFetchError (upstream); 503 GCS push failed (rolled back)
```

### A4. `GET /settings/sources/events[/{reader}]` — unified upload+fetch feed (NEW; supersedes upload-history)
```typescript
interface SourceEvent {
  id: number;
  reader: string;
  origin: "upload" | "fetch";  // NEW discriminator
  filename: string;
  file_size_bytes: number | null;
  occurred_at: string;         // ISO
  is_valid: boolean | null;
  warnings: string[];
  previous_filename: string | null;   // file this one replaced (upload) or null
}
interface SourceEventsResponse { reader: string | null; events: SourceEvent[]; total_count: number; }
```

### A5. `PUT /settings/sources` — edit source config (EXISTING; used by C5.4)
Body: partial `SourceConfig` per key (`data_dir`, `file_patterns`, `enabled`). Atomic `settings.yaml` write + GCS settings flush. Returns the updated `SourceRegistryResponse`. **Identity fields (`key`, `reader`, `format`, `asset_prefixes`) are read-only** (owned by `config/readers/{reader}.yaml`).

### A6. `POST /settings/sources/upload/{reader}` — upload (EXISTING; document)
Unchanged behavior (write → backup `.bak.<ts>` → GCS push → validate → history → `mark_dirty`). **C5 adds:** record event with `origin="upload"`, and run retention (keep latest 3) after publish.

### A7. Internal — retention (NOT an endpoint)
`prune_source_files(reader, keep=3)` (local) + `prune_source_blobs(bucket, reader, keep=3)` (GCS) run at the end of upload + fetch. Keep the 3 newest matching files by mtime/`updated`; **never delete the file the reader's `select: latest` resolves**. `.bak.<ts>` pruned to latest 3 per base file.

---

## Section B: Data Binding Map

| UI element | API field | Format |
|------------|-----------|--------|
| Source card title | `SourceConfig.label` | English text |
| Authority badge | `SourceConfig.authority` (+ `authority_note` tooltip) | enum → colored chip |
| Format chip | `SourceConfig.format` | uppercase ("FLEX_CSV") |
| Enabled toggle | `SourceConfig.enabled` | boolean switch (PUT on change) |
| Data dir field (editable) | `SourceConfig.data_dir` / `resolved_dir` | path; placeholder = `fallback_dir` |
| File patterns (editable) | `SourceConfig.file_patterns` | key→glob rows |
| Accepted upload exts | derived from `format` | csv→`.csv`; xlsx→`.xlsx,.xls`; flex_csv→`.csv` |
| Active file + size | `file_path`, `file_size_bytes`, `file_modified` | basename · KB · relative time |
| Health status badge | `SourceHealthEntry.status` | enum → colored chip |
| Rows / value | `row_count`, `net_value_cny` | int · ¥ thousands, 0 dp |
| Last update line | `SourceConfig.last_update.{origin,at}` | "via auto-fetch · 3h ago" / "via upload · 2d ago" |
| "Fetch now" button | shown iff `can_fetch` | POST /fetch/{reader} → spinner → FetchResult |
| Update feed (per source) | `SourceEventsResponse.events[]` | origin icon · filename · time · valid/warnings |

## Section C: Demo Data Markers
- Placeholder values mockups may contain (REPLACE with live data): `label`, `net_value_cny`, `last_update.at`, `file_modified`, event timestamps.
- Static (do NOT replace): authority enum chip colors, format chip text, the `fallback_dir` placeholder styling, the "Fetch now" affordance for `can_fetch=false` (hidden, not disabled).

## Section D: Component Reference

```
DataSourceManager (renders sources.map(SourceCard) — NO hardcoded list)
└─ SourceCard  [per SourceConfig]
   ┌─────────────────────────────────────────────────────────┐
   │ {label}            [{authority chip}] [{format}] [enabled⏻]│
   │ Active: {file basename} · {KB} · {file_modified rel}       │
   │ Status: [{status chip}]   Rows {row_count}  ¥{net_value}   │
   │ Last update: via {origin} · {at rel}                       │
   │ [ Upload file ]  [ Fetch now * ]      (*only if can_fetch) │
   │ ▸ Config (data_dir, file_patterns, enabled)  [editable]    │
   │ ▸ Update history  → SourceEvents feed (upload+fetch)       │
   └─────────────────────────────────────────────────────────┘
```
Styling: reuse existing settings card styling + status-chip colors from `SourceHealthDashboard`. Authority chips: authoritative=slate, co-authority=indigo, non-authoritative=amber, historical-shadow=zinc.

## Section E: Data Quality Requirements
- **Language:** English throughout (matches the existing Settings console).
- **Currency:** `net_value_cny` formatted `¥` + thousands separator, 0 decimals (reuse `formatCNY`).
- **Timestamps:** ISO-8601 UTC from backend; UI shows relative + absolute-on-hover (reuse `relativeTime`).
- **Authority:** derived server-side from `config/source_authority.yaml` (co-authority for `US_STK_*`/`US_ETF_*`/`CASH_USD` ⇒ Schwab + IBKR; others authoritative; PIS-style historical ⇒ historical-shadow). Never hardcoded in the frontend.
- **Security:** Flex token/query-id are server-side secrets; never in any payload or client call.

---

## Validation Checklist
**Backend (C5.2/C5.3/C5.4):**
- [ ] `GET /sources` returns `label`, `authority`, `authority_note`, `format`, `can_fetch`, `last_update` for all 7 sources
- [ ] `POST /sources/fetch/ibkr` fetches → writes → GCS push → event(origin=fetch) → retention(3) → mark_dirty; 400 when `can_fetch=false`; 502 on FlexFetchError; 503+rollback on GCS failure
- [ ] `source_upload_history` gains `origin` column (default 'upload'); `GET /sources/events` returns unified feed
- [ ] retention keeps latest 3 (local + GCS); **protect-active-file test** + net-worth-unchanged test green
- [ ] upload path adds origin='upload' event + retention; existing backup/GCS/rollback behavior unchanged
- [ ] Cloud Scheduler job def + OIDC + Flex secret wiring added under `deploy/`
- [ ] actual response captured: __________

**Frontend (C5.1/C5.2/C5.4):**
- [ ] DataSourceManager renders from payload; `UPLOAD_KEY`/`SOURCE_DISPLAY`/`READER_META` hardcoded maps removed
- [ ] all 7 sources render with correct label/exts/authority/format (parity vs current)
- [ ] "Fetch now" works for IBKR; update feed shows upload+fetch provenance
- [ ] config editor (data_dir/file_patterns/enabled) persists via PUT /sources
- [ ] screenshot evidence: __________

**Annotation:** [ ] bindings (Section B) verified against live response
