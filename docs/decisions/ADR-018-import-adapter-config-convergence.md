# ADR-018: Import-Adapter Onboarding Generates a Config-Driven Reader

**Date:** 2026-06-20
**Status:** Accepted
**Related:** ADR-004 (import-adapter authority), ADR-014 (config-driven reader engine), ADR-016 (co-authority)
**Plan:** internal implementation notes

---

## Context

Huinsight has two parallel data-ingestion engines that share only the `holdings`/
`transactions` destination (traced 2026-06-20):

- **Config-driven readers** (6 built-ins + IBKR): defined by `config/readers/*.yaml`
  + `settings.yaml` `source_registry`; executed in sync phase P2; authority from the
  static `config/source_authority.yaml`; file-based and repeatable.
- **Import-adapter wizard** (custom sources): defined entirely in DB tables
  (`import_adapter_runs` / `_staged_rows` / `_approvals`); ingested by a separate
  `sync_approved_import_adapters()`; authority injected dynamically at P5
  (`_load_adapter_authority_rules`, ADR-004); **one-shot** — a wizard source never
  becomes a reusable reader, so every period's file re-runs the whole wizard.

This duplicates the normalize→upsert logic and the column-mapping capability, and
splits authority across two mechanisms.

## Decision

**Import-adapter onboarding produces a first-class config-driven reader.** At the
wizard's Approve step, the column-mapping + identity + authority are translated into a
`config/readers/<key>.yaml` (+ a `settings.yaml` `source_registry` entry + a
`source_authority.yaml` rule). The onboarded source then runs through the *same*
engine, *same* P2 path, and *same* static authority model as the built-ins, and is
repeatable on the next file with no re-wizarding. DB-staging is retained only for an
explicit **one-time import** mode (historical pastes that should not create a reader).

This is delivered in three phases (see the plan):
1. **P2 becomes registry-driven** so any reader YAML auto-runs (enabler; no behaviour change).
2. **Wizard emits the reader YAML + registry/authority entries** at approval.
3. **Authority unifies** onto the generated `source_authority.yaml`; the parallel
   `sync_approved_import_adapters` path is retired except for one-time imports.

## Consequences

- **Positive:** one ingestion engine, one authority model, one column-mapping
  implementation; custom sources gain repeatability and appear as normal source cards;
  the C5 generic config editor manages them.
- **Negative / risk:** the wizard now writes `config/*.yaml` — needs atomic writes,
  schema validation, and collision guards. Migrating dynamic DB authority to generated
  YAML must be resolution-equivalent (dual-run gate). Orchestrator P2 ordering is
  critical-path (Rule 18).
- **Neutral:** the three `import_adapter_*` tables remain for the one-time-import mode;
  fully removing them is out of scope until that mode's future is decided.

## Status notes

- Phase 1 (registry-driven P2 enumeration) implemented behaviour-preservingly: existing
  readers keep their specialized functions and exact order; a generic runner only
  catches new registry keys. Live before/after full-sync equality gate is the pre-merge
  acceptance test (cannot run in a code-only environment without source files + DB).

---

## Addendum — 2026-08-17: extended by the open hook registry

This ADR predates Program OSR's WS-2, which added `register_hook()` and plugin
discovery on top of the config-driven convergence described here
(`src/sources/hooks/`, discovered from `plugins/hooks/*.py`). The third-party
extension story — adding a source without editing core files — is documented in
[`docs/adding-a-source.md`](../adding-a-source.md), which this ADR should be read
alongside rather than in isolation.
