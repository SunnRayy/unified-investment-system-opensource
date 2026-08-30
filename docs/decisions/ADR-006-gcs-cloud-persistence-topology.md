# ADR-006: Cloud / GCS Persistence Topology

**Date:** 2026-05-29
**Status:** Accepted
**Deciders:** Ray (Owner), Claude Code (Architect)

---

## Context

Huinsight runs on Cloud Run, which provides a stateless, ephemeral container. The DuckDB
file (`data/unified.duckdb`) is the single source of truth for all holdings history.
On Cloud Run, the container filesystem is wiped whenever the container is replaced or
scaled down. This creates a silent disaster-recovery gap: if the container is replaced
without a GCS sync, all data since the last GCS flush is silently lost.

The existing sync pipeline (`run_full_sync_v3` in `src/sync/orchestrator.py`) was
extended to flush to GCS at the end of each sync, but this was not formally documented
as an architecture decision. Health-check endpoints were added that probe GCS
reachability — their exact shape and constraints were also undocumented.

This ADR formalizes both the persistence topology and the health-probe discipline.

---

## Decision

**1. DuckDB is the local cache; GCS is the persistent store.**

All writes go to the local DuckDB file first. After each successful full sync, the
DuckDB file is flushed to GCS (currently via `GCSFlushManager` in
`src/storage/gcs_flush_manager.py`). The flush must complete before the sync result
is marked `success=True` and returned to the caller.

**2. The `/health/deep` GCS block performs only a metadata check — no writes.**

The `gcs` subsystem in `/health/deep` calls `blob.exists()` only (implemented in
`src/api/main.py`). It does NOT write, download full objects, or modify any GCS state.
This is a permanent constraint — a health check that writes to shared state is a
side-effectful probe that can corrupt the persistence timeline.

The current shape of the `subsystems.gcs` block is one of:
```json
// local mode (no GCS bucket env var configured):
{ "ok": true, "configured": false, "note": "local mode" }

// configured + reachable:
{ "ok": true, "configured": true, "db_blob_present": bool }

// configured + error:
{ "ok": false, "configured": true, "error": "<ExceptionTypeName>" }
```
No bucket names, credentials, or object paths appear in the health payload.

**3. A full write→read→verify round-trip is a separate explicit operation.**

A real GCS round-trip (to confirm persistence works end-to-end) is appropriate as:
- A deploy-time smoke test step in CI (post-deploy verification), or
- A manual command: `python main.py --verify-gcs` (proposed, not yet implemented).

It must never be on the hot path of a health or status endpoint.

---

## Consequences

**Positive:**
- Stateless Cloud Run containers can be replaced without data loss.
- Health checks remain side-effect-free (safe to call frequently / on every request).
- The sync pipeline's success flag accurately reflects whether persistence succeeded.

**Negative / Trade-offs:**
- A sync that completes but fails to flush to GCS will be marked `success=False`,
  potentially causing spurious alerts. The sync data is still in local DuckDB.
- The `last_flush` timestamp is not currently exposed in `/health/deep` — operators
  cannot see when the last successful flush occurred from the health endpoint alone.

**Neutral / Future work:**
- Adding a `last_flush` timestamp to `/health/deep` would require an API contract
  change; defer to a later pass with an explicit ADR update.
- The `--verify-gcs` CLI command is not yet implemented; tracked internally.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| Write GCS round-trip in `/health/deep` | Writes to shared state from health checks cause side effects; can corrupt persistence timeline if health is probed concurrently with sync |
| Use Cloud SQL or managed DB instead of DuckDB+GCS | DuckDB provides zero-infrastructure single-file analytics with all financial computation in-process; replacing it would require a full schema migration |
| Flush to GCS asynchronously in background | Async flush creates a window where the container can be replaced before the flush completes; sync flush ensures the success flag is trustworthy |

---

## References

- `src/storage/gcs_flush_manager.py` — GCS flush implementation
- `src/sync/orchestrator.py` — `run_full_sync_v3()`, where flush is called
- `src/api/main.py` — `/health/deep` endpoint, lines ~294–313
- `deploy/setup-gcs.sh` — one-time GCS bucket setup
- AGENTS.md Rule 23 — health-probe discipline
