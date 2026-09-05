# API Spec: Reader Mapping Management (ADR-023, formerly ADR-023 / WS-A + WS-B + WS-C)

> Feature: UI-managed "how raw reader-file data BECOMES assets" layer — the Financial
> Summary Excel column -> asset_id mapping (WS-A), Gold/Insurance/RSU
> `id_field_map` label -> code-segment mappings (WS-B), and Schwab/CN-fund
> vocabularies (`known_etf`/`symbol_norm`/`action_map`/`type_map`, WS-C).
> Status: Implemented (WS-A Steps A3 + A4.1, 2026-07-18; WS-B, 2026-07-18; WS-C, 2026-07-19).
> Plan: internal implementation notes.
> Last Updated: 2026-07-19 (WS-C — Schwab/CN-fund vocabularies, Section C3)

---

## Overview

The classification layer (what an asset *is* — taxonomy class, tier) is already fully
UI-managed via `taxonomy.py` / `Taxonomy.tsx`. The **reader-mapping layer** (how a raw
column/label in a source file becomes a specific `asset_id`) was code-only, so every new
bank account or ETF ticker required a code change + tests + deploy. This API exposes that
layer through the `reader_mappings` table (migration V75, extended by V77) for CRUD, preview,
and unmapped-column/label detection — reusing the existing Data Sources page's UX language
and the existing `DELETE /taxonomy/assets/{asset_id}` deactivation flow (no new
shadow-direction logic is introduced).

**Managed today**:
- `financial_summary` / mapping_kind `fs_column` (WS-A) — 资产负债 Excel column header ->
  `{asset_id, asset_name, currency}`.
- `financial_summary` / mapping_kind `ie_column` (plan 2026-08-01 WS-A, migration V82) —
  月度收支 Excel column header -> `{role, bucket, currency}`, the column's **ledger
  semantics** (is this money invested? a redemption? income? a native-currency display
  sibling?). Read by `src/services/investment_contributions.py`. See Section C4.
  `financial_summary` is therefore multi-kind, but unlike `schwab` it keeps a documented
  **default kind** (`fs_column`) when `kind` is omitted — a backward-compat guarantee for
  callers written before `ie_column` existed.
- `gold`, `insurance`, `rsu` / mapping_kind `id_field_map` (WS-B) — `"field:label"` ->
  `{code}`, a **segment** of a template-built `asset_id` (e.g.
  `GOLD_{asset_name}_{account}`), not a full asset_id on its own. See Section C2.
- `schwab` / mapping_kinds `known_etf`, `symbol_norm`, `action_map` and `cn_fund` /
  mapping_kind `type_map` (WS-C) — reader vocabularies: ticker -> `{etf: true}`,
  raw symbol -> `{to}`, raw action/操作类型 label -> `{type}` (validated against the
  transaction_type enum). See Section C3. `schwab` is the first **multi-kind** reader —
  `kind` is REQUIRED on its list/preview calls (422 when omitted).

Every other reader (`ibkr`, …) returns **404** with a "not yet mapping-managed" detail.
`ibkr` is a *deliberate* exclusion, not an omission: it is co-authority with Schwab and
reuses the schwab `symbol_norm` vocabulary (the orchestrator loads `reader_key='schwab'`
for the ibkr run too) — giving it its own rows would fork the shared vocabulary. The UI
shows a muted "vocabularies managed under Schwab (co-authority)" note on the ibkr row.

Key tables: `reader_mappings`, `reader_mapping_audit` (migration V75; `id_field_map` rows
added by migration V77; WS-C vocab rows added by migration V78; `ie_column` rows added by
migration V82 — same table/schema, no new columns).

---

## Section A: API Contract

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/settings/sources/{reader}/mappings?kind=` | List all mappings (active + archived) for a reader |
| POST | `/api/settings/sources/{reader}/mappings` | Create a new mapping |
| PATCH | `/api/settings/sources/{reader}/mappings/{id}` | Edit `value.asset_name`, `sort_order`, or (conditionally) `value.asset_id` |
| POST | `/api/settings/sources/{reader}/mappings/{id}/archive` | Archive (stop future melts from producing this asset_id) |
| POST | `/api/settings/sources/{reader}/mappings/{id}/restore` | Restore an archived mapping to active |
| DELETE | `/api/settings/sources/{reader}/mappings/{id}` | Hard-delete (only if the asset_id has zero holdings/transactions) |
| POST | `/api/settings/sources/{reader}/mappings/preview?kind=` | Dry-run: `fs_column` melts the current file; `id_field_map` (WS-B) scans it for mapped/unmapped label values; vocab kinds (WS-C) scan the file's symbols/actions/labels (read-only in every case) |
| POST | `/api/settings/sources/{reader}/mappings/ignore-column` | A4.1 — mark a currently-unmapped column "not melted by design" (owner decision). **`fs_column` only** — 404 for every other kind |
| POST | `/api/settings/sources/{reader}/mappings/{id}/unignore` | A4.1 — delete an ignore marker. **`fs_column` only** — 404 for every other kind |
| GET | `/api/settings/sources` | **(extended)** each mapping-managed source row now also carries `unmapped_count` (fs_column: unmapped columns; id_field_map: unmapped labels; schwab/cn_fund: unmapped actions/操作类型 — see C3) |

`PATCH`/create `value` payload shape depends on `mapping_kind`: `fs_column` -> `{asset_id?,
asset_name?, currency}`; `ie_column` -> `{role, bucket, currency}` (Section C4);
`id_field_map` -> `{code}`; `known_etf` -> `{etf: true}` (fixed);
`symbol_norm` -> `{to}`; `action_map`/`type_map` -> `{type}`. `MappingOut.map_value` and
`MappingCreateRequest`/`MappingPatchRequest.value` are loosely typed (`dict`) on the wire for
this reason — see Sections C2 and C3.

All writes follow the house convention: Pydantic request/response models, `_open_writable(db)`
+ `mark_dirty()` on every successful write, Rule-12 `api_error_response(e, context=...)`
catch-all, `LookupError` -> 404, `ValueError`/explicit validation -> 422, writable connection
closed in `finally`. An audit row (`reader_mapping_audit`) is written for every create / update
/ archive / restore / delete.

---

### GET `/api/settings/sources/{reader}/mappings`

**Path params:** `reader` — must be a WS-A-managed reader (currently only `financial_summary`).

**Query params:** `kind` (optional) — defaults to the reader's expected mapping_kind
(`fs_column` for `financial_summary`); a mismatched value returns 422.

**Response (200):**

```typescript
interface MappingValue {
  asset_id: string;
  asset_name: string;
  currency: string;   // fs_column: always "CNY"
}

interface Mapping {
  id: number;
  reader_key: string;
  mapping_kind: string;
  map_key: string;           // the raw Excel column name
  map_value: MappingValue;
  status: "active" | "archived";
  sort_order: number | null;
  updated_at: string | null; // ISO-8601
}

/** A4.1 classification, in precedence order: ignored > native > computed >
 *  liability > candidate. Only 'candidate' counts toward unmapped_count. */
type UnmappedColumnCategory = "ignored" | "native" | "computed" | "liability" | "candidate";

interface UnmappedColumn {
  column: string;
  /** true = a native-currency sibling column (header ends _USD/_HKD) —
   *  informational only, NOT counted toward the amber-chip unmapped_count.
   *  Kept for backward compat; equivalent to category === "native". */
  ignored_native: boolean;
  category: UnmappedColumnCategory;
  /** Only non-null for category === "ignored" — the reader_mappings row id,
   *  needed to call POST .../mappings/{mapping_id}/unignore. */
  mapping_id: number | null;
}

interface MappingListResponse {
  reader: string;
  mapping_kind: string;
  mappings: Mapping[];
  /** true if the reader_mappings table has zero rows for this (reader, kind) —
   *  a pre-seed / seed-failure edge case; the loader still falls back to code
   *  defaults on the sync path, but there is nothing to show/manage in the UI yet. */
  defaults_only: boolean;
  /** Best-effort scan of the currently uploaded file's columns against the
   *  merged mapping set. Empty if the file is missing/unreadable — never
   *  raises (see Section D). */
  unmapped_columns: UnmappedColumn[];
}
```

**Error modes:**
- 404 — unknown/unmanaged reader
- 422 — `kind` doesn't match the reader's expected mapping_kind

---

### POST `/api/settings/sources/{reader}/mappings`

**Request Body:**

```typescript
interface MappingCreateRequest {
  kind: string;              // must equal the reader's expected mapping_kind
  map_key: string;           // non-empty; the raw Excel column name
  value: {
    asset_id: string;        // non-empty
    asset_name: string;
    currency: string;        // fs_column: MUST be "CNY"
  };
}
```

**Validation (in order):**
1. `kind` must match the reader's expected mapping_kind (422)
2. `map_key` non-empty (422)
3. `value.asset_id` non-empty (422)
4. `fs_column`: `value.currency` must be `"CNY"` — the FS Excel stores owner-converted CNY
   values in every column; the asset_id `_USD`/`_HKD` suffix is traceability only, not the
   stored currency (422 with an explanatory message)
5. `map_key` must not already have an **active** mapping for `(reader, kind)` (422)
6. `asset_id` must not already be used by another **active** mapping in the merged set
   (code defaults + DB overrides) for `(reader, kind)` (422)
7. `asset_id` must not already be referenced by `holdings`/`transactions` rows whose
   `source_system` differs from this reader's own source_system (409 — see Section D for why
   this checks `holdings`/`transactions` rather than `asset_registry`)

**Response (201):** the created `Mapping`.

**Error modes:** 404 (unmanaged reader), 422 (validation above), 409 (cross-reader asset_id collision).

> **Reactivation note**: `reader_mappings` has a `UNIQUE(reader_key, mapping_kind, map_key)`
> constraint that applies regardless of `status` — every code-default `map_key` already has a
> row from the V75 seed. So creating a mapping for a `map_key` whose only existing row is
> `archived` (the account-closure "archive, then later re-map this column" flow) is handled as
> an in-place reactivation (`UPDATE ... SET status='active', map_value=...`) rather than a second
> `INSERT`, which would otherwise violate the unique constraint. This is still a normal 201 +
> a `create`-action audit row (old_value = the archived row's previous value) from the caller's
> point of view — the reactivation is an implementation detail.

---

### PATCH `/api/settings/sources/{reader}/mappings/{id}`

**Request Body:**

```typescript
interface MappingPatchRequest {
  value?: {
    asset_name?: string;
    /** Only accepted if the CURRENT asset_id has zero holdings rows — else 409.
     *  Renaming an asset_id with live holdings is archive + create instead. */
    asset_id?: string;
  };
  sort_order?: number;
}
```

**Response (200):** the updated `Mapping`.

**Error modes:**
- 404 — mapping not found for this reader
- 409 — `value.asset_id` change requested but the current asset_id has holdings rows
- 409 — `value.asset_id` change requested but the current asset_id has holdings/transactions
  from a different reader's source_system
- 422 — new `asset_id` empty, or already used by another active mapping

---

### POST `/api/settings/sources/{reader}/mappings/{id}/archive`

Flips `status` to `archived` — this is the "account closure" flow. An archived mapping's
`map_key` is **removed** from the merged dict the next sync loads (`load_reader_mappings`),
so future melts stop producing that asset_id. It does **not** touch existing `holdings` rows.

**Response (200):**

```typescript
interface ArchiveResponse {
  mapping: Mapping;              // status now "archived"
  asset_has_holdings: boolean;
  /** Present only when asset_has_holdings is true. The UI chains this into the
   *  EXISTING DELETE /taxonomy/assets/{asset_id} endpoint — this API does NOT
   *  reimplement deactivation/shadowing. */
  deactivate_hint: {
    asset_id: string;
    endpoint: string;   // "/taxonomy/assets/{asset_id}"
    method: "DELETE";
    note: string;
  } | null;
}
```

**Error modes:** 404 — mapping not found.

---

### POST `/api/settings/sources/{reader}/mappings/{id}/restore`

Flips `status` back to `active`. Includes a defensive 409 check for another active mapping
already claiming the same `map_key`, though this cannot actually occur today: the table's
`UNIQUE(reader_key, mapping_kind, map_key)` constraint applies regardless of `status`, so
`POST .../mappings` reactivates an archived row in place instead of ever creating a second one
for the same key (see the create endpoint's "Reactivation note" above). The check is kept as a
cheap guard in case that invariant changes.

**Response (200):** the updated `Mapping`.

**Error modes:** 404 (not found).

---

### DELETE `/api/settings/sources/{reader}/mappings/{id}`

Hard delete. Allowed **only** if the mapping's `asset_id` has zero `holdings` rows and zero
`transactions` rows — otherwise use archive.

**Response (200):**

```typescript
interface DeleteResponse {
  deleted: number;    // the mapping id
  asset_id: string;
}
```

**Error modes:**
- 404 — mapping not found
- 409 — asset_id still referenced by holdings and/or transactions (message states the counts)

---

### POST `/api/settings/sources/{reader}/mappings/preview`

Read-only dry-run. Resolves the reader's currently uploaded file (same resolution logic as
`GET /settings/sources`), reads the `资产负债` sheet exactly as the reader does
(`header=3`), and re-runs `melt_financial_summary_holdings` in-memory against the merged
mapping set (optionally overlaid with `proposed` — not-yet-saved candidate mappings, so the
UI can preview an edit before committing it). **No DB writes; uses the request's read-only
connection.**

**Request Body (optional):**

```typescript
interface PreviewRequest {
  proposed?: Array<{
    map_key: string;
    value: { asset_id: string; asset_name: string; currency: string };
  }>;
}
```

**Response (200):**

```typescript
interface PreviewColumnResult {
  map_key: string;
  column_found: boolean;     // is map_key actually a column header in the current file?
  nonzero_rows: number;      // holdings rows produced for this asset_id by the melt
  latest_value: number | null;
  latest_date: string | null;  // ISO date (YYYY-MM-DD)
}

interface PreviewResponse {
  reader: string;
  mapping_kind: string;
  file_path: string | null;   // null if no file is currently resolved/found
  results: PreviewColumnResult[];
  unmapped_columns: UnmappedColumn[];
}
```

**Error modes:** 404 — unmanaged reader. Any other failure (file unreadable, workbook
corrupt) is reported through the Rule-12 `api_error_response` envelope, not a 500 crash.

---

### POST `/api/settings/sources/{reader}/mappings/ignore-column` (A4.1)

Marks a currently-unmapped column as **"not melted by design"** — an owner decision that a
column the automatic native/computed/liability rules didn't catch (a stray label, or a
reader-covered informational duplicate — see the A4.1 background below) should never be
melted into holdings, without inventing a fake asset mapping for it. Upserts a
`reader_mappings` row with `status='ignored'`, `map_value='{}'` — reuses the same
archived-row reactivation pattern as `POST .../mappings` (the table's
`UNIQUE(reader_key, mapping_kind, map_key)` applies regardless of status).

**Request Body:**

```typescript
interface IgnoreColumnRequest {
  map_key: string;   // non-empty; must not already have an active mapping
}
```

**Response (201):** the ignored `UnmappedColumn` (`category: "ignored"`, `mapping_id` set).

**Error modes:**
- 404 — unmanaged reader
- 422 — `map_key` empty, or `map_key` already has an active mapping (archive it first)

---

### POST `/api/settings/sources/{reader}/mappings/{id}/unignore` (A4.1)

Deletes an `ignored` row outright — the column goes back to being scanned normally (it will
reappear in `unmapped_columns`, most likely as `category: "candidate"`, on the next scan).

Deliberately **not** folded into `POST .../restore`: an ignored row's `map_value` is `'{}'`
(no `asset_id`/`asset_name`/`currency`), so flipping `status` back to `'active'` the way
restore does would produce an invalid mapping and a confusing 422 telling the owner to "map
it" from inside what looks like a restore action. A plain delete-the-marker endpoint is the
cleaner un-ignore — this was a deliberate choice between two designs considered during A4.1
(see internal implementation notes, A4.1).

**Response (200):**

```typescript
interface UnignoreResponse {
  unignored: number;   // the mapping id that was deleted
  map_key: string;
}
```

**Error modes:**
- 404 — mapping not found
- 422 — the row at `id` is not `status='ignored'` (it's a real active/archived mapping — use
  the generic archive/restore/delete endpoints instead)

**Safety note**: the generic `PATCH`/`archive`/`restore`/`DELETE` endpoints all reject an
`id` that resolves to an `ignored` row with **409** (`_reject_if_ignored`) — an ignored row's
`map_value='{}'` has no `asset_id`, so building a `MappingOut` response from it would raise a
validation error. Ignored rows are managed only through `ignore-column`/`unignore`.

---

### GET `/api/settings/sources` (extended)

Each source row (`SourceConfigOut`) gains one new field:

```typescript
interface SourceConfig {
  // ...existing fields unchanged...
  /** WS-A A3: cheap unmapped-column count, computed ONLY for financial_summary.
   *  null for every other reader, and null whenever the count can't be cheaply
   *  computed (file missing/unreadable) — never breaks the sources list. */
  unmapped_count: number | null;
}
```

The count reuses the same column-scan heuristic as the preview endpoint
(`src.services.reader_mappings.scan_unmapped_columns`) and counts **only**
`category === "candidate"` columns (A4.1) — `native`/`computed`/`liability`/`ignored`
columns are "not melted by design" and must not inflate the amber chip.

---

## Section B: Archive -> Deactivate Chaining Contract

Archiving a mapping only stops **future** melts; the asset's most recent `holdings` row would
otherwise linger in net worth (latest-per-asset reads). The UI flow is:

1. `POST .../mappings/{id}/archive` — always safe, reversible via `/restore`.
2. If the response's `asset_has_holdings` is `true`, show a "also deactivate asset?" prompt
   using `deactivate_hint`.
3. If the owner confirms, call the **existing** `DELETE /taxonomy/assets/{asset_id}`
   (`src/api/routes/taxonomy.py::deactivate_asset`) — this sets `asset_registry.is_active =
   FALSE` and shadows (`is_shadow = TRUE`) all of that asset's `holdings` rows. This API does
   **not** reimplement that logic; it only returns the `asset_id` so the UI can chain the call.

Renaming an asset_id (PATCH `value.asset_id`) is only permitted when there are **zero**
holdings rows for the current asset_id — otherwise the owner is told to archive + create with
the same deactivation prompt, exactly mirroring the plan's "account-closure semantics" design.

---

## Section C: Reader Restriction

Every endpoint in this file (except the extended `GET /settings/sources`) starts by resolving
`reader -> mapping_kind(s)` through a fixed allowlist (WS-C generalized the value to a tuple —
`schwab` is the first reader managing more than one kind):

```python
_MANAGED_READERS = {
    "financial_summary": ("fs_column", "ie_column"),
    "gold": ("id_field_map",),
    "insurance": ("id_field_map",),
    "rsu": ("id_field_map",),
    "schwab": ("known_etf", "symbol_norm", "action_map"),
    "cn_fund": ("type_map",),
}
_READER_DEFAULT_KIND = {"financial_summary": "fs_column"}
```

Kind resolution (`_resolve_kind`): a single-kind reader defaults to its one kind when `kind`
is omitted; a multi-kind reader falls back to `_READER_DEFAULT_KIND` if it declares one
(`financial_summary` -> `fs_column`), else **422s** when `kind` is omitted or not in its
tuple. `schwab` deliberately declares no default (it was multi-kind from birth, so no caller
depends on an implicit one); `financial_summary` does, because every caller predating
`ie_column` omits `kind` and means `fs_column`.
Row-addressed endpoints (PATCH/archive/restore/DELETE) don't take a `kind` param at all — the
row id is fetched constrained to the reader's kinds and the row's own `mapping_kind` drives
the kind-specific guard dispatch.

Any other `reader` value returns **404** with `detail` explaining the reader is not yet
mapping-managed and listing the currently supported set. `ibkr` is deliberately excluded —
co-authority with Schwab, it consumes the *schwab* `symbol_norm` vocabulary (see Section C3).

---

## Section C2: `id_field_map` Contract (WS-B — Gold/Insurance/RSU)

### Why this is a different shape than `fs_column`

`fs_column`'s `map_value` (`{asset_id, asset_name, currency}`) is a *complete* asset
identity — one row, one asset_id. `id_field_map` is different: an `id_template` like
`gold.yaml`'s `"GOLD_{asset_name}_{account}"` builds one asset_id out of **multiple**
placeholder fields, each independently mapped. A single `id_field_map` row
(`"account:招行"` -> `{"code": "CMB"}`) is a **segment** substitution, not a full asset_id —
the same code can appear in many different produced asset_ids (one per `asset_name` value
sharing that account). This shapes every guard below: there is no single `asset_id` to look
up for the holdings/transactions collision checks the way `fs_column` has one.

### map_key format and field validation

`map_key` must be `"field:label"` (e.g. `"account:招行"`, `"asset_name:纸黄金"`,
`"product_name:示例定期寿险1"`) where `field` is a real `id_template` placeholder name for
that reader, read live from `config/readers/{reader}.yaml` (not a fixed list — a future YAML
edit adding a new placeholder is picked up automatically):

| Reader | Declared `id_template` fields | Source |
|--------|-------------------------------|--------|
| `gold` | `asset_name`, `account` | `"GOLD_{asset_name}_{account}"` (both sheets) |
| `insurance` | `product_name`, `policy_name` | holdings: `"INS_{product_name}"`; premiums: `"INS_{policy_name}"` (melt `var_name`) |
| `rsu` | `asset_name` | `"RSU_{asset_name}"` |

A `field` not in this set (per-reader) -> **422**. A `map_key` missing the `:` separator, or
with an empty field/label -> **422**.

`insurance.yaml` declares **zero** `id_field_maps` today (legacy behavior: raw
`product_name`/`policy_name` used directly — see the YAML's own comments) — the V77 seed for
`insurance` is an empty dict, and `GET .../insurance/mappings?kind=id_field_map` returns
`mappings: [], defaults_only: true` on a fresh DB. `product_name`/`policy_name` are still
valid fields to create a *first* mapping against — the reader_key being present in
`_MANAGED_READERS` doesn't require any pre-existing defaults.

### value shape and validation

`value = {"code": "<string>"}`. `code` must be non-empty, ASCII-only, and contain no
whitespace (422 otherwise) — it becomes a literal segment of every asset_id built from that
label going forward, so it must be safe to embed directly in an `_`-joined identifier.
No currency rule (that's `fs_column`-specific — Financial Summary's owner-converted-CNY
constraint doesn't apply to a code segment).

### Affected-asset_id guard (archive / patch / delete)

Because a label:code mapping has no single `asset_id`, the archive/patch(code-change)/delete
guards use a **conservative LIKE-pattern heuristic** instead of an exact `asset_id =`
lookup:

```sql
SELECT COUNT(*) FROM holdings  WHERE asset_id LIKE '{prefix}%{code}%'
SELECT COUNT(*) FROM transactions WHERE asset_id LIKE '{prefix}%{code}%'
```

for every `prefix` in the reader's `identity.asset_prefixes` (e.g. `GOLD_` for gold). This
may **over-count** if `code` happens to appear as a substring inside an unrelated segment of
another asset_id under the same prefix — an accepted trade-off that errs toward a false 409
(forcing the owner to double-check) rather than silently allowing an archive/delete that
breaks live holdings. Effects:

- **PATCH `.value.code`**: if the *old* code has any LIKE-matching holdings/transactions,
  409 (mirrors `fs_column`'s asset_id-rename guard) — archive and create a new mapping
  instead of renaming in place.
- **POST `.../archive`**: always succeeds (archiving never touches historical rows), but
  `asset_has_holdings` reports whether the LIKE pattern found any. **`deactivate_hint` is
  always `null`** for `id_field_map` — unlike `fs_column`, there is no single asset_id to
  chain into `DELETE /taxonomy/assets/{asset_id}` (a label:code mapping can affect *multiple*
  asset_ids). The owner must locate and deactivate each affected asset individually via the
  existing Asset Audit UI.
- **DELETE**: 409 if the LIKE pattern finds any holdings/transactions row (archive instead).
  Response carries `code` (not `asset_id`, which is `null` for this kind).

### Preview and unmapped-label scan

`POST .../mappings/preview` for an `id_field_map` reader returns a **different response
shape** than `fs_column`'s (`IdFieldMapPreviewResponse`, not `PreviewResponse` — the route
has no single `response_model` declared for this reason):

```json
{
  "reader": "gold",
  "mapping_kind": "id_field_map",
  "file_path": "/path/to/Gold.xlsx",
  "items": [
    {"field": "asset_name", "label": "纸黄金", "map_key": "asset_name:纸黄金", "mapped": true, "code": "PAPER"},
    {"field": "account", "label": "澳门银行", "map_key": "account:澳门银行", "mapped": false, "code": null}
  ],
  "unmapped_columns": [
    {"column": "account:澳门银行", "ignored_native": false, "category": "candidate", "mapping_id": null}
  ]
}
```

Unlike `fs_column`'s preview, there is no `proposed` overlay support for `id_field_map` in
this step (WS-B scope: read-only scan only). The label extraction
(`src.api.routes.reader_mappings._extract_field_labels`) handles two shapes, matching how
each field reaches `id_template` in the config-driven engine:

- **rename-based** (gold's `asset_name`/`account`, insurance's `product_name`, rsu's
  `asset_name`): the field is a `rename` target column — labels are that column's unique
  cell values.
- **melt-based** (insurance premiums' `policy_name`): the field is the sheet's
  `melt.var_name` — labels are the sheet's *other* column headers (the wide-format policy
  names), not cell values.

`unmapped_columns` (and the `GET /settings/sources` `unmapped_count` amber chip, generalized
in `_compute_unmapped_count` / `settings.py`) has no `native`/`computed`/`liability`/
`ignored` categories for this kind — every unmapped label is `category: "candidate"`.
`ignore-column`/`unignore` (A4.1) are **not available** for `id_field_map` — 404.

### Engine wiring (how a DB-managed mapping reaches the sync pipeline)

Unlike `fs_column` (injected into `SourceData.metadata` between `read()` and `transform()`,
which is late enough since the FS melt hook runs at transform time), `id_field_map`
resolution happens **inside** `ConfigDrivenReader.read()` (building `canonical_id` before
`transform()` is even called) — so the merged mapping must be supplied at construction time:

```python
overrides = load_id_field_maps(connector, "gold")   # nested {field: {label: code}}
reader = ConfigDrivenReader(reader_cfg, id_field_maps_override=overrides)
```

`sync_config_source(..., extra_metadata={"id_field_maps_override": overrides})` still accepts
this via the same `extra_metadata` parameter the orchestrator already used for `fs_column` —
it is extracted and passed to the `ConfigDrivenReader` constructor *before* `read()` runs,
then also merged into `source_data.metadata` afterwards for introspection (though that merge
isn't what makes it take effect). `overrides` is `None` (not passed at all, or an explicit
`None`) for exactly one thing: preserving byte-identical legacy behavior when no override is
supplied — every existing call site outside the orchestrator (tests,
`ConfigDrivenReader(cfg)` alone) is unaffected. Unknown-label behavior — a label not present
in the merged map (removed by an `archived` mapping, or simply never mapped) — falls back to
**the raw label used verbatim** as the asset_id segment (passthrough, not an error, not a
dropped row) — this was the engine's existing behavior for any `id_field_maps` gap and is
unchanged by WS-B.

---

## Section C3: Vocabulary Contract (WS-C — Schwab known_etf/symbol_norm/action_map, CN-fund type_map)

### The four kinds

| Reader | Kind | map_key | map_value | Consumed by (all at TRANSFORM time) |
|--------|------|---------|-----------|--------------------------------------|
| `schwab` | `known_etf` | ticker (uppercased on write) | `{"etf": true}` (fixed) | `schwab_transactions_from_csv` -> `_schwab_normalize_transaction_symbol` (positions CSV has `Security Type`; the transactions CSV does not — this list decides `US_ETF_*` vs the `US_STK_*` default) |
| `schwab` | `symbol_norm` | raw compound ticker, e.g. `BRK/B` (uppercased on write) | `{"to": "BRK-B"}` | `_schwab_normalize_symbol` — reached from `schwab_holdings_from_csv`, `schwab_transactions_from_csv`, **and both IBKR hooks** (`ibkr_holdings_from_flex` / `ibkr_transactions_from_flex` call `_schwab_normalize_to_canonical_id` directly — co-authority means the SAME merged vocabulary must reach both brokers, so the orchestrator loads `reader_key='schwab'` for the ibkr run too) |
| `schwab` | `action_map` | raw Schwab action string, e.g. `Cash Dividend` (free text — actions contain spaces) | `{"type": "dividend"}` | `schwab_transactions_from_csv` -> `_schwab_map_action` |
| `cn_fund` | `type_map` | raw 操作类型 label, e.g. `申购` (free text — Chinese) | `{"type": "buy"}` | `cn_fund_transactions_from_sheet` (incl. the memo-override `_resolve` path — the 现金分红/红利再投资 memo overrides run BEFORE the type_map lookup, unchanged) |

**Pseudo-type `transfer` (Attribution & Flows WS-3.1, migration V79)**: Schwab's
`Security Transfer` action is directionally ambiguous (one label covers both ACAT legs),
so its action_map target is the pseudo-type `transfer`, resolved to
`transfer_out`/`transfer_in` by quantity sign inside `schwab_transactions_from_csv` —
never persisted on a transactions row. Kind-scoped: valid ONLY for `action_map`
(`_validate_vocab_value` 422s it for `type_map`, and the UI's type_map dropdown omits
it), because no other reader hook resolves it.

Single source of truth: `src.database.mapping_seeds.VOCAB_SEEDS` (and the per-kind
`SCHWAB_KNOWN_ETFS_SEED` / `SCHWAB_SYMBOL_NORMALIZATIONS_SEED` / `SCHWAB_ACTION_MAPPING_SEED`
/ `CN_FUND_TYPE_MAP_SEED` constants it derives from). `src.sources.reader_hooks`'s legacy
module constants (`_SCHWAB_KNOWN_ETFS` etc.) are now re-exports of these — same names and
shapes, so every existing consumer/test is unaffected. Migration **V78** seeds the rows
idempotently on the natural UNIQUE key (same pattern as V75–V77).

### Engine wiring

All four vocabularies are consumed inside **transform-time hooks** (`transactions_from_sheet_hook`
/ `holdings_from_sheet_hook`), NOT inside `read()` — so the standard `extra_metadata` merge
(applied after `read()`, before `transform()`) is sufficient; no constructor-override path
(WS-B's `id_field_maps_override`) is needed for any of them. The orchestrator loads via the
sync's own connection and injects:

```python
# _run_schwab_reader
sync_schwab(config, extra_metadata={
    "schwab_known_etf":  {k for k, v in load_reader_mappings(conn, "schwab", "known_etf").items() if v},
    "schwab_symbol_norm": load_reader_mappings(conn, "schwab", "symbol_norm"),
    "schwab_action_map":  load_reader_mappings(conn, "schwab", "action_map"),
})
# _run_ibkr_reader (co-authority — schwab's vocab, NOT an ibkr one)
sync_ibkr(config, extra_metadata={"schwab_symbol_norm": load_reader_mappings(conn, "schwab", "symbol_norm")})
# _run_cn_fund_reader
sync_cn_fund(config, extra_metadata={"cn_fund_type_map": load_reader_mappings(conn, "cn_fund", "type_map")})
```

Hooks read these via `metadata.get(...)` and fall back to the module defaults when the key is
absent — every call site that doesn't inject (tests, direct hook calls) is byte-identical to
legacy behavior (golden tests in `tests/services/test_reader_mappings.py::TestGoldenVocabParity`).

### Loader decoding

`load_reader_mappings(conn, reader, kind)` decodes per kind (`_KIND_DECODERS`):
`known_etf` -> `{ticker: True}` (callers take `set(merged.keys())`); `symbol_norm` ->
`{raw: to}`; `action_map`/`type_map` -> `{label: type}`. An **archived** row removes the
entry from the merged dict, which restores the exact legacy unknown-value behavior:

| Kind | Unknown/archived behavior (preserved verbatim) |
|------|-----------------------------------------------|
| `known_etf` | ticker not in set -> `US_STK_{sym}` (the transactions-CSV fallback in `_schwab_normalize_transaction_symbol`) |
| `symbol_norm` | symbol not in map -> `s.replace('/', '-')` slash-to-dash fallback |
| `action_map` | action not in map -> `'other'` (`_SCHWAB_ACTION_MAPPING.get(action, 'other')`) |
| `type_map` | label not in map -> `'other'` (`.get(raw_type, 'other')` / `.map(...).fillna('other')`) — memo overrides for 现金分红/红利再投资 still win first |

Never an error, never a dropped row, in every case.

### Validation

- `known_etf`: `value` is **fixed** at `{"etf": true}` — anything else 422s (removing a
  ticker is archive, not a value change). `map_key` must be a non-empty ASCII token with no
  whitespace and is uppercased on write (the normalizer uppercases before lookup — a
  lowercase key would silently never match).
- `symbol_norm`: `value.to` non-empty ASCII no-whitespace, uppercased; `map_key` same rules.
- `action_map`/`type_map`: `value.type` MUST be a member of
  `src.services.reader_mappings.ALLOWED_TRANSACTION_TYPES` — 422 listing the allowed values
  otherwise. **Note on the enum's provenance**: no single canonical transaction_type enum
  existed in the codebase (schema.sql declares a bare VARCHAR; `xirr.py`/`twr.py`'s
  OUTFLOW/INFLOW frozensets are cash-flow-classification subsets, not exhaustive — e.g.
  neither contains `other`/`stock_split`). `ALLOWED_TRANSACTION_TYPES` is therefore defined
  in `src/services/reader_mappings.py` as the union of every type literal the reader/hook
  pipeline actually produces today, and a seed test asserts every V78-seeded value is a
  member (so a seed row can always be re-saved unchanged).

### Archive / delete guards (the "conservative approach" for vocabularies)

- `known_etf` ticker `T`: **exact** reference check on asset_id `US_ETF_{T}` (deterministic —
  no LIKE heuristic needed). DELETE 409s if referenced; archive always succeeds and reports
  `asset_has_holdings`.
- `symbol_norm` -> `{to: V}`: exact checks on `{prefix}{V}` for every canonical Schwab prefix
  (`US_STK_`, `US_ETF_`, `US_BND_`, `US_FUND_`, `US_OPT_`). Changing `to` while the old
  target is referenced -> 409 (archive + create instead, mirroring C2's code-rename guard).
- `action_map`/`type_map`: **no reference check is possible** — raw action/type labels are
  not persisted on transaction rows (only the mapped `transaction_type` is), so there is
  nothing to look up. Archive is inherently safe (future rows fall back to `'other'`, the
  documented unknown behavior); DELETE is allowed without a guard. This is a documented
  weaker guard, not an oversight — the enum validation above is the real guardrail for
  these kinds.
- `deactivate_hint` is **always `null`** for every vocab kind (like C2: no single asset to
  chain a deactivation into).

### Preview and the amber chip

`POST .../preview?kind=` (kind required for schwab) scans the current file:
schwab `Symbol` column (known_etf/symbol_norm), schwab `Action` column (action_map) — both
from the resolved TRANSACTIONS CSV (`resolved_files['transactions']`) — and cn_fund's
`基金交易记录`.`操作类型` (type_map). Response is `VocabPreviewResponse`
(`items: [{value, mapped, mapped_value}]` + `unmapped_columns`); fail-safe: `file_path: null`
+ empty lists when no file resolves, Rule-12 envelope on hard errors.

**Candidates policy** (the A4.1 cries-wolf lesson): `unmapped_columns` (and the
`GET /settings/sources` `unmapped_count` chip) surface candidates ONLY for
`action_map`/`type_map` — an unmapped action/label silently melts to `'other'`, a genuine
gap. `known_etf`/`symbol_norm` show the full mapped/unmapped scan in preview `items` but
surface **zero** candidates: an unmapped symbol is the normal case (most tickers are stocks
needing no entry), and listing every ticker would train the owner to ignore the chip. The
schwab chip counts `action_map` candidates; the cn_fund chip counts `type_map` candidates;
both fail-safe to `null`.

---

## Section C4: `ie_column` Contract (月度收支 column semantics — plan 2026-08-01 WS-A, V82)

### What it maps

`fs_column` answers "which asset does this 资产负债 column become?". `ie_column` answers a
different question about the *other* sheet: "when this 月度收支 column has a number in it,
what does that number MEAN to the contribution/savings ledger?" It produces no `asset_id` and
melts nothing into `holdings` — it is read only by
`src/services/investment_contributions.py` (`monthly_investment_flows` /
`contributions_summary_v2`, the authority behind ADR-025's `investment.*` figures).

Before V82 those semantics were six hardcoded string literals in that module, so a column the
owner added to the Excel was silently dropped out of `gross_invested` with no error — the
silent-failure / convention-contract class in the `uis-failure-classes` memory.

### `map_value` shape

```json
{"role": "invested|redemption|income|reimbursement|expense|computed|reference|ignored",
 "bucket": "cn_fund|us_schwab|us_ibkr|gold|bank_wealth|total_income|null",
 "currency": "CNY|USD"}
```

Vocabulary constants live in `src/database/mapping_seeds.py` (`IE_ROLES`,
`IE_DESTINATION_BUCKETS`, `IE_TOTAL_INCOME_BUCKET`, `IE_ROLE_BUCKETS`, `IE_CURRENCIES`) — the
same constants the V82 seed, the API validator, and the ledger service all read.

How each field is consumed:

| role | bucket | effect |
|---|---|---|
| `invested` | destination (**required**) | summed into `by_destination[bucket]` -> `gross_invested` |
| `redemption` | source destination or null | summed into `redemptions` (netted, trailing-window only) |
| `income` | `total_income` | **the** income basis (`income_ttm`). At most one active row may hold it |
| `income` | null | a component already inside the `total_income` column's Excel SUM — records what it IS, contributes to no total |
| `reimbursement` | null | subtracted from `income_basis_ttm` (the savings-rate denominator) and from nothing else. 报销 sits inside `总收入合计` but is repayment of money the owner fronted, not earnings. **Deliberately not `redemption`**: that role also subtracts from the NUMERATOR (`net_external`), which would be wrong here. ADR-025 Amendment 2026-08-01 |
| `expense` / `computed` / `reference` / `ignored` | null | contributes to nothing |

`currency: "USD"` columns contribute to **nothing**, whatever their role. They are
native-currency display siblings (`_Schawab_USD`, `_IBKR_USD`, `_RSU_USD`,
`_股票卖出收益_USD`) of a CNY column the owner already converted in Excel; ADR-025 §3 verified
`Schawab == Schawab_USD × 参考_美元汇率` exactly, every month. This is Rule 2 at the ledger
layer.

### Validation (422)

- `role` outside `IE_ROLES`; `currency` outside `{CNY, USD}`.
- `bucket` not allowed for that role (a destination bucket on an `expense`, `total_income` on
  an `invested`, …).
- `role='invested'` with no `bucket` — it would contribute to no destination and vanish from
  `gross_invested`. This is the exact failure the kind exists to prevent, so it is a hard 422,
  not a default.
- A **second** `bucket='total_income'` row — it would add a column already inside the first
  one's SUM, double-counting income and silently deflating `savings_rate_ttm`.
- A `bucket` on a `reimbursement` row (the role carries none).
- PATCH re-validates the **whole** post-patch value (not just the changed field), so
  `{"role": "invested"}` on a bucket-less row 422s instead of persisting a dangling invested
  column.

### Archive / delete guards

None needed: an `ie_column` row derives no `asset_id`, so `asset_has_holdings` is always
`false` and `deactivate_hint` always `null`. Archiving a row makes that column contribute to
nothing — which is exactly what archiving should mean here. There is no `ignore-column` flow
either; `role='ignored'` is the equivalent, and it is a normal value edit.

### Preview and the unmapped scan

`POST .../preview?kind=ie_column` reads the workbook's `月度收支` sheet with `header=3` (the
same read the sync path does) and returns the **same** `PreviewResponse` shape `fs_column`
uses: per mapped column, `column_found` / `nonzero_rows` / `latest_value` / `latest_date`
(latest = the last row with a non-zero value; `日期` supplies the date). No `proposed`
overlay (matching the WS-B/WS-C preview scope). Fail-safe to `file_path: null` + empty lists.

`GET .../mappings?kind=ie_column` runs the same scan and returns `unmapped_columns`, reusing
`scan_unmapped_columns`: a new `月度收支` column comes back as `category='candidate'`, a new
`_USD`/`_HKD` sibling as `category='native'` (structurally non-actionable — it must
contribute to nothing whether mapped or not). Column headers and map_keys are compared
**strip-normalized** on both sides: the live header row contains `'参考_美元汇率 '` with a
trailing space, and a hand-edited header can gain or lose whitespace at any time.

**Not wired into the `GET /settings/sources` amber chip.** `unmapped_count` for
`financial_summary` still counts `资产负债` candidates only — extending it to both sheets
changes a shipped number on the Data Sources page and is an owner-visible decision. Open
follow-up.

---

## Section D: Design Notes / Deviations

- **Cross-reader asset_id collision check target**: the plan text says "check for an
  `asset_registry` row from a different `source_system`", but `asset_registry` (schema.sql)
  has no `source_system` column — `canonical_id` is its primary key and there is nothing to
  compare a *source* against. The actual source_system per asset_id lives on `holdings` /
  `transactions` (both have a `source_system` column), so the 409 check queries those two
  tables instead: "does any holdings/transactions row for this asset_id have a source_system
  other than this reader's own (`Financial_Summary_Excel`)?" This preserves the intent (don't
  let a new FS mapping silently collide with an asset id another live reader owns) using the
  columns that actually exist.
- **`unmapped_columns` heuristic (A4.1 refinement)**: the initial WS-A A3 scan only excluded
  the date column, blank/pandas-default headers, and native-currency siblings — a live smoke
  test against the real FS Excel showed `unmapped_count: 29`, most of which were totals,
  ratios, liability columns, and reader-covered informational duplicates, not genuine gaps.
  `scan_unmapped_columns` (see `src/services/reader_mappings.py`) now classifies every
  surfaced column into one of five categories, in this precedence order:
  1. **`ignored`** — an explicit `reader_mappings` row with `status='ignored'` exists for this
     `map_key` (an owner decision about this *specific* column — see the ignore-column
     endpoints above and the A4.1 background below).
  2. **`native`** — header ends `_USD`/`_HKD` (unchanged from A3; `ignored_native: true`).
  3. **`computed`** — starts with `合计`, or contains `比例` or `资产负债率`, or equals
     `"USD Rate"` (totals and ratio rows).
  4. **`liability`** — starts with `短期负债`/`长期负债`/`其他负债` — the Balance Sheet report
     reads these separately and intentionally does **not** melt them into holdings.
  5. **`candidate`** — everything else: a genuinely actionable gap. **Only this category
     counts toward `unmapped_count`** and appears in the frontend's amber strip.
  Categories 2–4 are structural, code-level rules (deliberately simple — no hardcoded list of
  specific column names, to avoid drift). Category 1 (`ignored`) is data, not code: it is how
  the owner marks a column the structural rules didn't catch (e.g. a stray label, or a
  reader-covered duplicate that isn't named consistently enough for a structural rule to
  catch safely) as "never melt this" without inventing a fake asset mapping.
- **A4.1 background — why `投资资产_*` columns needed data, not a structural rule**: the FS
  Excel carries several `投资资产_*` columns (e.g.
  `投资资产_股票基金_美股基金_Schwab`, `投资资产_公司RSU_Amazon Stock`,
  `投资资产_黄金_纸黄金(元)`, `投资资产_长期保险_安泰人生`) that are the owner's own informational
  copy of a value another reader already owns authoritatively (Schwab/IBKR US equities,
  RSU_Excel vesting positions, Gold Excel paper gold, Insurance Excel policies) — melting them
  would double-count. But `投资资产_股票基金_A股基金` (an A-share fund with no reader coverage
  today) must **not** be caught by the same rule, and a generic `投资资产_*` prefix rule cannot
  tell these apart without hardcoding reader-specific knowledge into the scan (the thing A3's
  Section D explicitly avoided). So these specific columns are seeded with `status='ignored'`
  via migration V76 (`src.database.mapping_seeds.FS_IGNORED_COLUMNS_SEED`) — a data decision,
  not a pattern match. `投资资产_股票基金_A股基金` (and any unrecognized column, e.g. `创业股权投资`)
  is left as `category: "candidate"` for the owner to actually decide on.
- **`GET /settings/sources` `unmapped_count`** is wired only through the GET route (which now
  takes a `db: DatabaseConnector = Depends(get_db)` dependency). The `PUT /settings/sources`
  response (immediately after a settings save) does not pass a `db` handle through and will
  show `unmapped_count: null` for financial_summary until the next GET — an accepted minor gap
  since the frontend always re-fetches via GET after a save.

---

## Section E: Data Model Reference

### Key tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `reader_mappings` | UI-managed reader mapping rows (migration V75; `'ignored'` status + `fs_column` seed added V76; `id_field_map` rows for gold/insurance/rsu added V77 — same table, no schema change) | `id`, `reader_key`, `mapping_kind` (`fs_column`\|`id_field_map`), `map_key`, `map_value` (JSON; `{asset_id,asset_name,currency}` for fs_column, `{code}` for id_field_map, `'{}'` for ignored rows), `status` (`active`\|`archived`\|`ignored`), `sort_order`, `created_at`, `updated_at`; `UNIQUE(reader_key, mapping_kind, map_key)` |
| `reader_mapping_audit` | Audit trail for every write | `id`, `mapping_id`, `action` (`create`\|`update`\|`archive`\|`restore`\|`delete`\|`ignore`\|`unignore`), `old_value`, `new_value`, `at` |

### Related non-API modules

| Module | Role |
|--------|------|
| `src/database/mapping_seeds.py` | Single source of truth for code-default mapping values: `FS_ASSET_MAPPING_SEED`/`FS_IGNORED_COLUMNS_SEED` (fs_column) and `ID_FIELD_MAP_SEEDS` (id_field_map, WS-B — `dict[reader_key, dict["field:label", code]]`, mirrors `config/readers/{gold,insurance,rsu}.yaml` `id_field_maps` exactly; a test asserts this equality). Imported by the V75/V76/V77 migration seeds and `src/services/reader_mappings.py`. |
| `src/services/reader_mappings.py` | `load_reader_mappings()` (defaults + DB overrides merge, kind-dispatched via `_KIND_DECODERS`); `scan_unmapped_columns()` (fs_column heuristic); `nest_id_field_map()` / `load_id_field_maps()` (WS-B — flat `"field:label"` -> nested `{field: {label: code}}`); `scan_unmapped_id_field_map_labels()` (WS-B label scan) |
| `src/sources/reader_hooks.py::melt_financial_summary_holdings` | The actual FS balance-sheet melt hook; reads `metadata["fs_asset_mappings"]` with a hardcoded fallback |
| `src/sources/config_driven_reader.py::ConfigDrivenReader` | `__init__(reader_cfg, id_field_maps_override=None)` (WS-B) — when set, replaces every sheet's YAML `id_field_maps` for `id_template` resolution during `read()`. `sync_config_source(..., extra_metadata={"id_field_maps_override": ...})` extracts it before constructing the reader (see Section C2 "Engine wiring"). |
| `src/sync/orchestrator.py::_run_financial_summary_reader` | Loads `fs_column` mappings from the sync's own connection and injects them via `extra_metadata` before the melt runs |
| `src/sync/orchestrator.py::_run_gold_reader` / `_run_insurance_reader` / `_run_rsu_reader` | WS-B — load each reader's merged `id_field_map` via `load_id_field_maps(connector, reader_key)` and pass it through `sync_gold`/`sync_insurance`/`sync_rsu`'s new `extra_metadata` parameter |
