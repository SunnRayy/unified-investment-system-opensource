# ADR-023: Reader Mapping Management (DB-Backed Reader-Mapping Layer)

**Date:** 2026-07-18
**Status:** Accepted
**Deciders:** Ray (Owner), Claude Code (Fable-5 Lead Architect)

> **Numbering note**: this branch (`claude/reader-mapping-management`) was started before
> `ADR-022-process-based-verification.md` merged to `main` (V7.4.0, 2026-07-08). Every in-code
> comment and doc written on this branch during WS-A (A1–A4) refers to this decision as
> "ADR-023" — `src/services/reader_mappings.py`, `src/api/routes/reader_mappings.py`,
> `docs/api-specs/reader-mappings.md`, the internal implementation plan,
> both test files, and the frontend panel/types. This file is filed as **ADR-023** to avoid
> colliding with the merged ADR-023. The in-code "ADR-023" references are a known,
> intentionally-deferred cleanup — a repo-wide `ADR-023 / WS-A` → `ADR-023 / WS-A` rename pass
> belongs in the pre-merge document checklist (`batch-merge-gate` skill) when this branch
> merges, not as a mid-implementation churn commit. See the internal implementation plan.

---

## Context

Huinsight aggregates 7 reader sources into one DuckDB database. The **classification layer** (what
an asset *is* — taxonomy class, tier) has been fully UI-managed since early in the project
(`taxonomy.py` + `Taxonomy.tsx`, `ClassificationRules.tsx`). The **reader-mapping layer** (how
a raw column/label/ticker in a source file *becomes* a specific `asset_id`) was code-only:
`_FS_ASSET_MAPPING` (a hardcoded dict in `reader_hooks.py`), per-reader `id_field_map`s in
`config/readers/*.yaml`, and Schwab/CN-fund vocabularies embedded directly in code.

This became a recurring operational cost. Three separate incidents — adding the ICBC deposit
column (V7.1.7), the HSBC HK multi-currency columns (2026-07-18), and a general pattern of
"new bank account / new ETF ticker requires a code change + tests + deploy" — showed that
every owner-side account change forced a full development cycle for what is fundamentally a
data change, not a logic change. The gap analysis in the plan doc found the classification
layer had this exact UI-managed capability already; the reader-mapping layer had never
received the same treatment.

A live smoke test during implementation surfaced a second problem one layer up: once the
scan existed, the amber "N unmapped columns" chip over-counted. Of 29 flagged columns on the
real Financial Summary Excel, only 2 were genuine gaps — the rest were computed totals/ratios,
liability columns the Balance Sheet report reads separately, and FS's own informational copy
of values another reader (Schwab/IBKR/RSU_Excel/Gold Excel/Insurance Excel) already owns
authoritatively. A hint chip that cries wolf 27 times out of 29 trains the owner to ignore it.

---

## Decision

Introduce a single generic table, `reader_mappings` (migration V75), keyed on
`(reader_key, mapping_kind, map_key)` with a JSON `map_value` payload and a `status` column
(`active` | `archived` | `ignored`). WS-A implements the first slice: `reader_key =
'financial_summary'`, `mapping_kind = 'fs_column'` — the Excel-column → `asset_id` mapping
that had been hardcoded in `_FS_ASSET_MAPPING`.

**Layers:**
- **Seed migration** (V75): idempotent, keyed on the natural UNIQUE key — never re-burns the
  version gate even if re-run (see memory `migration-decimal-precision-noop`). Seeds from
  `src.database.mapping_seeds.FS_ASSET_MAPPING_SEED`, the single source of truth shared with
  the reader-hook code-default fallback.
- **Loader** (`src.services.reader_mappings.load_reader_mappings`): merges code defaults with
  DB rows inside the sync's own connection — `active` rows overlay/override, `archived` **and**
  `ignored` rows remove the key from the merged dict entirely (both stop a column from
  reaching the melt hook; only the unmapped-column scan's `category` field distinguishes them).
- **Hook injection**: `src.sync.orchestrator._run_financial_summary_reader` loads the merged
  mapping from the sync's own connection and passes it through the existing config-engine
  `metadata` argument — `reader_hooks.py` stays stdlib+pandas only (no `src.database` import,
  cycle guard preserved).
- **API** (`src/api/routes/reader_mappings.py`, nested under `/settings/sources`): full CRUD
  (create/patch/archive/restore/delete), a read-only preview (dry-run melt against the
  currently uploaded file), and an unmapped-column scan. All writes follow the house
  convention (`_open_writable`, `mark_dirty()`, Rule-12 error envelope, `reader_mapping_audit`
  row per write).
- **Archive → deactivate chaining**: archiving a mapping only stops *future* melts; if the
  asset still has holdings rows, the response carries a `deactivate_hint` the UI chains into
  the **existing** `DELETE /taxonomy/assets/{asset_id}` — no new shadow-direction logic.
- **Unmapped-column categorization + ignore mechanism (A4.1)**: `scan_unmapped_columns`
  classifies every surfaced column as `native` / `computed` / `liability` / `ignored` /
  `candidate`, in that precedence order. The first three are structural, code-level rules
  (currency-suffix, total/ratio pattern, liability prefix) — deliberately simple, no hardcoded
  list of specific column names. `ignored` is **data, not code**: a `reader_mappings` row with
  `status='ignored'` (`map_value='{}'`) marks a specific column, reviewed by the owner, as
  "never melt this" — used both for a fresh `POST .../mappings/ignore-column` UI action and
  for the V76 seed (`FS_IGNORED_COLUMNS_SEED`) that retires FS's own informational copies of
  Schwab/IBKR/RSU_Excel/Gold Excel/Insurance Excel data. Only `category === 'candidate'`
  counts toward the amber-chip `unmapped_count` — the count went from 29 to 2 on the real
  file with zero owner action required, because the 27 non-candidate columns are either
  structurally recognizable or previously-reviewed data, not a growing hardcoded list.
- **Un-ignore is a delete, not a status flip**: `POST .../mappings/{id}/unignore` deletes the
  ignored row outright rather than reusing `restore` (which flips `status` back to `'active'`)
  — an ignored row's `map_value='{}'` has no `asset_id`/`asset_name`/`currency`, so reactivating
  it as `'active'` would produce an invalid mapping. See
  `docs/api-specs/reader-mappings.md` for the two designs considered.

---

## Consequences

**Positive:**
- New bank account / new FS column is now a UI action (Add mapping / Ignore column), not a
  code change + test + deploy cycle — directly closes the gap that triggered this ADR.
- The amber "N unmapped columns" chip is now a trustworthy signal (2 genuine gaps, not 29
  noisy false positives) — a noisy hint chip that owners learn to ignore is worse than no
  chip at all.
- `archived` vs `ignored` gives a clean vocabulary: `archived` = "this used to be a real asset
  mapping, now retired" (account closure); `ignored` = "this was never a real asset mapping
  and never will be" (owner-reviewed non-asset column). Both stop the melt; the audit trail
  and the unmapped-column `category` field make the distinction visible.
- Everything routes through the table's natural `UNIQUE(reader_key, mapping_kind, map_key)`
  key — archive/create, and ignore/unignore, all reactivate or delete the same physical row
  rather than accumulating duplicate history for one column.

**Negative / Trade-offs:**
- The `ignored` seed (V76, `FS_IGNORED_COLUMNS_SEED`) is a hardcoded list of specific column
  names — exactly the kind of drift-prone list the A3 scan heuristic was designed to avoid.
  This is an accepted, bounded trade-off: it is *data* (owner decisions about *specific*
  columns already observed in the live file), reviewable and editable through the ignore/
  unignore API, not a code pattern that silently reclassifies future unseen columns.
- WS-A only covers `financial_summary`/`fs_column`. Gold/Insurance/RSU `id_field_map` (WS-B)
  and Schwab/CN-fund vocabularies (WS-C, "highest blast radius" — live broker data) are
  explicitly out of scope; every other `reader` value 404s from this API today.
- The repo-wide `ADR-023` → `ADR-023` in-code comment rename (see the numbering note above)
  is deferred to the pre-merge document checklist rather than done as a mid-implementation
  churn commit — a `grep -rl 'ADR-023'` sweep is needed before this branch merges to `main`.

**Neutral / Future work:**
- WS-B (`id_field_map` for Gold/Insurance/RSU) and WS-C (`known_etf`/`symbol_norm`/
  `action_map`/`type_map` for Schwab/CN-fund) extend the same table and the same
  `_MANAGED_READERS` allowlist pattern.
- A structured admin-review screen for ambiguous rows (deferred from the Process-Verification
  program, F1.4) could eventually surface `category='candidate'` columns across all readers,
  not just financial_summary, once WS-B/WS-C land.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| Edit `config/readers/*.yaml` via a generic YAML editor UI | YAML files are per-reader, ad-hoc shaped, and not owned by one schema — a generic editor would need per-file JSON-schema validation and still couldn't give a clean audit trail or archive/ignore semantics. A DB table with one schema is simpler and reusable across WS-B/WS-C. |
| Per-reader dedicated tables (e.g. `fs_column_mappings`, `gold_field_maps`) | Duplicates CRUD/audit/API-route boilerplate per reader; the generic `(reader_key, mapping_kind, map_key, map_value JSON)` shape already handles every WS-B/WS-C kind without a schema migration per reader. |
| Fold `ignored` into `archived` (reuse one status instead of adding a second) | Loses the distinction between "was mapped, now retired" (account closure, has holdings history) and "never was a real mapping" (a computed/liability/informational column, `map_value='{}'`) — the unmapped-column scan's `category` field and the un-ignore-as-delete design both depend on being able to tell these apart. |
| Keep the A3 scan heuristic simple forever and let the owner manually dismiss noise per session | The live smoke test showed a 29-item chip that cries wolf 27 times out of 29 — an owner who has to re-triage the same 27 non-actionable columns every time they check Data Sources will stop trusting (and stop checking) the chip. A one-time categorization (structural rules) + a one-time data seed (10 known columns) fixes the signal permanently. |

---

## References

- Plan: internal implementation notes
- API spec: `docs/api-specs/reader-mappings.md`
- Migrations V75 (`reader_mappings` + `reader_mapping_audit` + FS active-mapping seed) and V76
  (`'ignored'` status + `FS_IGNORED_COLUMNS_SEED`), `src/database/connector.py`
- `src/database/mapping_seeds.py` (`FS_ASSET_MAPPING_SEED`, `FS_IGNORED_COLUMNS_SEED`)
- `src/services/reader_mappings.py` (`load_reader_mappings`, `scan_unmapped_columns`,
  `get_ignored_map_keys`)
- `src/api/routes/reader_mappings.py`
- `ux-command-center/components/settings/ReaderMappingsPanel.tsx`
- ADR-022 (`docs/decisions/ADR-022-process-based-verification.md`) — unrelated decision that
  claimed the "022" number on `main` first; see the numbering note above.
