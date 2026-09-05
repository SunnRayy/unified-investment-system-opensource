# ADR-021: Single-Production Topology — Cloud Is Production, Local Is a Verified Mirror

**Status**: Accepted (2026-07-06)
**Owner approval**: Ray, 2026-07-06 ("sync cloud db to local … avoid two system maintenance and differences")

## Context

Huinsight ran two effectively-independent productions: the Cloud Run instance (DuckDB in
tmpfs, flushed to GCS) and the local dev DB (`data/unified.duckdb`), each synced
separately by the owner from local files. Divergence between them was a recurring
incident source: stale local IBKR file → local-only `consolidated_equals_sum`
BLOCKING failure; local trades scored under pre-V7.1.8 semantics vs cloud
(Regret vs Missed Opportunity on the same Apr trades); a polluted local sync on
Jul 5 required an owner-approved backup restore. Maintaining two write-paths
doubled the owner's operational burden and made "which number is right?"
questions unanswerable.

## Decision

1. **Cloud is the only production.** All reader-file uploads and syncs happen on
   the cloud UI. The GCS-persisted DB is the single source of truth.
2. **Local is a read-only mirror**, refreshed explicitly via `./dev.sh pull-cloud`
   (`scripts/maint_db.py --pull-cloud`): download → verify (size > 10 MiB,
   holdings ≥ 600, `trade_logs` + `schema_version` present, version ≥ 64) →
   archive current local DB as `pre-pull-<ts>` (keep 2) → snapshot as
   `cloud-mirror-<ts>` (keep 3) → atomic `os.replace` install. Verification
   failure aborts with the local DB untouched.
3. **The mirror copies double as offline disaster insurance** against GCS/account
   -level failures (cf. the 2026-07 billing outage): the 3 newest cloud snapshots
   always exist on the owner's disk, outside GCP.
4. The pull is **never automatic** — replacing `data/unified.duckdb` stays behind
   an explicit human-initiated command (see AGENTS.md's Database Safety rules).
5. `--prune-backups` exempts `cloud-mirror-*` / `pre-pull-*` (self-managed
   retention), so the keep-8 rotation cannot evict the insurance copies.

## Consequences

- Local integrity failures caused purely by divergence (stale reader files)
  disappear — first pull took local from 13/15 to 15/15.
- Local syncs remain *possible* (dev/testing) but any local write is throwaway:
  the next pull overwrites it. Agents must not run local syncs for production
  purposes.
- Local dev tests against a recent production snapshot instead of a drifted one.
- The dual-run comparison value of two independent pipelines is given up; the
  divergence cost had exceeded that verification value.

## Alternatives considered

- **Bidirectional sync** — rejected: two-way merge of a DuckDB file is conflict-
  prone and recreates the divergence problem it is meant to solve.
- **Scheduled/automatic pulls** — rejected: silent overwrite of the local DB
  violates the DB-safety confirmation rules and could destroy in-flight dev state.
- **Local as primary, cloud as mirror** — rejected: cloud is where the owner
  actually operates (uploads, syncs, review), and GCS flush + timestamped GCS
  backups already give cloud the stronger durability story.
