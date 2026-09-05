# ADR-019: Cloud Settings Persistence — Reader-Seed and Sync-Flush Invariants

**Date:** 2026-06-26
**Status:** Accepted
**Deciders:** Ray (Owner), Claude Code (Architect)

---

## Context

Cloud Run restores the GCS-persisted `config/settings.yaml` over the repo copy at startup
(`download_settings_from_gcs`). This means the live `source_registry` block reflects a
snapshot taken at some past deploy — not the current repo defaults. Any reader key added
to the repo after that GCS snapshot was created will be absent from the live `source_registry`.

This caused a production incident (2026-06-26): the `ibkr` reader key was added in Workstream
C (V6.4.0), but the GCS `settings.yaml` predated that release. The orchestrator's enabled-gate
(`source_registry.ibkr.get('enabled', False)`) returned False → `_run_ibkr_reader` returned
`(0, 0)` silently → net worth understated ~¥350K on every cloud sync.

A second latent bug compounded the incident: `run_sync_background` / `run_sync_reader_background`
called `flush_now()` without first calling `mark_dirty()`. The persistence layer short-circuits
`flush_now()` when `_write_seq == _flushed_seq` — a freshly-started instance always had seq
equality, so sync results were never uploaded to GCS from a UI-driven sync.

---

## Decision

Two invariants are locked as durable, enforced behaviours:

### Invariant 1 — Reader-seed on startup

After every `download_settings_from_gcs` call in `src/api/main.py` lifespan,
`settings_manager.seed_missing_readers()` **must** run. It:

1. Iterates every reader key registered in the reader registry.
2. For any key absent from the live `source_registry`, inserts a default entry
   (`enabled: True`, `file_prefixes`/`file_patterns` from the reader's `ReaderConfig`).
3. Writes `settings.yaml` atomically to disk.
4. Pushes the updated file to GCS.

Properties: **idempotent** (existing entries are never modified), **additive** (entries are never
removed), **non-crashing** (startup must not be interrupted by a seed failure — errors are logged
and swallowed). The seed runs on every startup so a new reader added to the repo propagates to
the cloud instance on next deploy without requiring a manual GCS patch.

### Invariant 2 — Sync marks DB dirty before flush

Any code path that writes to the DuckDB database as part of a sync (either
`run_full_sync_v3()` or a single-reader sync) **must** call `mark_dirty()` on the GCS
persistence object before calling `flush_now()`. `flush_now()` is a no-op when
`_write_seq == _flushed_seq`; `mark_dirty()` increments `_write_seq`, ensuring the flush
actually uploads.

This applies to both `run_sync_background` and `run_sync_reader_background` in
`src/api/routes/sync.py`, and to any future sync paths.

---

## Consequences

**Positive:**
- New readers added to the repo automatically propagate to the GCS-persisted `source_registry`
  on next startup — no manual GCS patch required.
- UI-driven syncs reliably persist to GCS: the SSE stream holds CPU allocated through the flush,
  and `mark_dirty()` ensures `flush_now()` does not short-circuit.
- Both invariants are enforced in code, not convention — they cannot be silently bypassed.

**Negative / Trade-offs:**
- Every startup incurs one additional GCS read (the settings file is already downloaded) and
  potentially one GCS write (only if a key is missing). On Cloud Run cold starts this adds
  ~100–300 ms; acceptable.
- `seed_missing_readers()` must be tolerant of reader registry unavailability (e.g. a malformed
  YAML reader config) — it must not crash startup. This requires defensive error handling.

**Neutral / Future work:**
- **CPU-throttling follow-up**: the 60-second periodic background flush is unreliable under
  Cloud Run CPU throttling. Invariant 2 fixes UI-driven syncs (SSE keeps CPU allocated). A
  fully robust fix for background flushes is `--no-cpu-throttling` or a synchronous
  in-request flush — deferred.
- If `settings.yaml` ever gains a schema version, the seed logic should be updated to gate on
  the schema version rather than key presence.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| Bake all reader defaults into the GCS `settings.yaml` at deploy time | Requires CI/CD to maintain the GCS file; deploy pipeline would need access to the full reader registry. Seed-on-startup is simpler and self-healing. |
| Require manual GCS patch after adding a new reader | Error-prone; the V6.4.0 IBKR incident proves it will be missed. |
| Always call `flush_now()` unconditionally (skip the seq check) | Would force an unnecessary GCS upload on every flush tick even when nothing changed. The `mark_dirty()` invariant preserves the efficiency of the seq check while fixing the omission. |
| Synchronous in-request GCS flush after sync | Eliminates the CPU-throttle gap entirely, but adds latency to the sync SSE response. Deferred as a follow-up option. |

---

## References

- Incident record: internal implementation notes
- `src/services/settings_manager.py` — `seed_missing_readers()` implementation
- `src/api/main.py` — lifespan wiring (order: download_settings → seed → rest of startup)
- `src/api/routes/sync.py` — `mark_dirty()` + `flush_now()` in both sync background paths
- ADR-006 (`docs/decisions/ADR-006-gcs-cloud-persistence-topology.md`) — GCS persistence topology
- ADR-014 (`docs/decisions/ADR-014-config-driven-reader-engine.md`) — reader registry this invariant builds on
- ADR-017 (`docs/decisions/ADR-017-ibkr-flex-ingestion.md`) — IBKR reader whose missing key triggered this incident
