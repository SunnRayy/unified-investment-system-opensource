# ADR-014: Config-Driven Reader Engine + Source Registry

**Date:** 2026-06-12
**Status:** Accepted
**Deciders:** Ray (Owner), Claude Opus 4.8 (Architect)

---

## Context

Huinsight ingests investment data from 6 reader sources. As of V6.1.0, every reader is a
bespoke Python module (`gold_reader.py`, `insurance_reader.py`, etc.) plus a paired
transformer, with ~18 hardcoded source-name constants scattered across
`src/api/routes/sync.py`, `src/api/routes/settings.py`, `src/validation/data_integrity_gate.py`,
`src/sync/phases/_common.py`, `src/api/routes/data.py`, `src/api/routes/operations.py`,
`src/validation/cost_basis_validator.py`, and `src/validation/sync_audit.py`. Adding a
7th source (Workstream C: IBKR via Flex Query) requires updates in all 8 files plus a new
reader/transformer pair — there is no single registration point.

Workstream B1 in the Data Layer Transformation program
(an internal program plan) addresses this by introducing
a YAML-driven reader engine and a central registry. The change was designed under a strict
Rule 18 constraint: the default execution path must be unchanged during B1; the config engine
is gated behind a per-source flag with a default of `legacy` until a dual-run equality gate
confirms parity.

---

## Decision

Introduce a declarative reader engine and a source registry as two cooperating components:

**1. Source Registry** (`src/sources/registry.py`)
A lazy singleton that loads `config/readers/*.yaml` at import time and exposes typed
accessors (`holding_source_systems()`, `allowed_extensions()`, `validator_map()`, etc.)
that replace every hardcoded constant. The 8 consumer files now call the registry instead
of maintaining their own literal sets/dicts. Zero project imports inside the registry itself
(stdlib + yaml + pydantic only) — prevents circular-import cycles.

**2. Reader YAML Configs** (`config/readers/<key>.yaml`)
One file per source. Each has an `identity:` block (source_key, display_label, system name,
asset_prefixes, allowed_extensions, category, validator name), enabling the registry to
derive all constants for all 6 sources from day one. Sources with full parsing support also
have a `parsing:` block declaring sheets, rename maps, row filters, a generic `melt`
directive, asset-ID templates, value maps, and output column order.

As of B1: `gold.yaml` and `insurance.yaml` have full `parsing:` blocks. The other 4
(`cn_fund`, `rsu`, `schwab`, `financial_summary`) have `identity:` only — their parsing
is deferred to later workstream tasks and they continue running the legacy engine.

**3. Config-Driven Reader Engine** (`src/sources/config_driven_reader.py`,
`src/sources/reader_config.py`)
A `ConfigDrivenReader(BaseSourceReader)` that interprets the YAML declaratively: strip
whitespace, apply row filters, rename columns, melt wide→long, apply value maps, build
asset IDs from templates, attach constants, reorder output. A companion `sync_config_source()`
mirrors the `sync_gold()` contract so the dispatch is a one-line branch.

**4. Per-Source Flag Dispatch** (`src/sync/gold_sync.py`, `src/sync/insurance_sync.py`,
`config/settings.yaml`)
`source_registry.gold.reader_engine: legacy` (and same for `insurance`) in `settings.yaml`.
The sync wrappers check `type_config.get('reader_engine', 'legacy')` and, when the value is
`'config'`, delegate to `sync_config_source()`. Default `legacy` ⇒ zero production behavior
change. The flag flip and any merge are human-gated.

**5. Dual-Run Equality Gate** (`tests/sources/test_config_driven_reader.py`)
`pd.testing.assert_frame_equal` (legacy reader+transformer vs config engine) on both
holdings and transactions for gold and insurance, using real workbook fixtures in
`tests/fixtures/readers/`. These tests must remain green before any flag flip.

---

## Consequences

**Positive:**
- Adding an 8th source (IBKR) now requires a single `config/readers/ibkr.yaml` (identity
  block) to make it appear in all registry consumers — no 8-file scatter-update.
- All source-name constants are derived from a single source of truth; registry-rewiring
  regressions are caught by a golden-value test (`test_registry.py`).
- The declarative YAML covering gold + insurance removes ~250 lines of procedural
  reader/transformer code (replaceable after flag flip + dual-run green).
- The engine's strict output-column ordering enforced via `output_columns:` in the YAML
  prevents silent schema drift between legacy and config paths.

**Negative / Trade-offs:**
- Format validation (`validate_gold_format`, `validate_insurance_format`) is **not** called
  in `sync_config_source()`. This is intentional for B1 (default-legacy means it never
  runs), but **must be wired before flipping any source to `config` in production**. The
  registry's `validator_map()` accessor provides the validator names; the wiring is trivial
  but was kept out of B1 scope.
- The config engine only implements `file_mtime` snapshot-date strategy. Three others
  (`column`, `cell`, `filename_regex`) raise `NotImplementedError` by design — they are not
  needed for gold or insurance and will be implemented when a source requires them.
- The `format: excel` path in the engine is the only supported format. A CSV path (needed
  for Schwab) is deferred to a later workstream.

**Neutral / Future work:**
- Pre-flip checklist (before setting any source to `reader_engine: config`):
  1. ~~Wire `validate_*_format` into `sync_config_source` (or call it before the dispatch).~~ (done 2026-06-12)
  2. ~~Run a live sync with the flag at `config` and verify `--check-integrity` ≥ 13/14.~~ (done 2026-06-12 — 13/14, NW delta +0.055% price refresh only)
  3. ~~Compare row counts and spot-check market values in the UI against a legacy baseline.~~ (done 2026-06-12 — dual-run equality green, reconcile 0 unexplained)
- `cn_fund`, `rsu`, `schwab`, `financial_summary` will gain `parsing:` blocks in later
  Workstream B tasks; each will follow the same flag-gated dual-run process.
- IBKR (Workstream C) will need a `parsing:` block and either a new snapshot-date strategy
  or a Flex-Query-specific pre-processor before the config engine can replace its reader.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| Migrate all 6 sources at once in B1 | Too wide a blast radius; Schwab is CSV (not Excel), cn_fund has unique raw-processor-bypass pattern. Flag-gated incremental migration is safer. |
| Registry-only (no parsing engine) | Solves the constants-scatter problem but leaves bespoke reader/transformer pairs indefinitely. The YAML parsing engine enables eventual consolidation. |
| Generate Python code from YAML | Harder to audit, test, and iterate. Runtime interpretation is simpler, more transparent, and achieves the same output. |
| Put `reader_engine` flag in registry YAML | The flag controls a runtime behavior choice (which implementation to use), not a source identity property. `settings.yaml` is the right home for per-environment runtime toggles. |

---

## References

- Program plan: internal implementation notes (§B1)
- B1 blueprint (locked): internal implementation notes
- ADR-013: `docs/decisions/ADR-013-authority-resolver-semantics.md` (authority model context)
- Touchpoint inventory: internal implementation notes (§Verified touchpoint inventory)

---

## Addendum — 2026-08-17: extended by the open hook registry

This ADR predates Program OSR's WS-2, which added `register_hook()` and plugin
discovery on top of the engine described here (`src/sources/hooks/`, discovered
from `plugins/hooks/*.py`). The reader engine's third-party extension story —
adding a source without editing core files — is documented in
[`docs/adding-a-source.md`](../adding-a-source.md), which this ADR should be read
alongside rather than in isolation.
