# ADR-012: Pass F — DB Evolution (Version-Ledger, Compaction, Dry-Run Sync, Orphan Drop)

**Date:** 2026-06-04
**Status:** Accepted
**Deciders:** Ray (Owner), Claude Opus 4.8 (Architect + Lead)

---

## Context

Pass D (ADR-011) built the `bootstrap_database()` entry point and the `_run_migration()` + `_migration_failures` collector but **explicitly deferred** four items:

1. **Version-ledger**: `run_migrations()` re-applied every statement on every startup, relying on `IF NOT EXISTS`/`ON CONFLICT DO NOTHING` for idempotency. No record of which migrations had been applied. ~30 migration blocks used a legacy silent `try/except logger.warning` pattern that swallowed non-idempotency failures.
2. **Naive SQL splitter**: `initialize_schema()` used `str.split(';')` which breaks on semicolons inside SQL string literals (e.g. `DEFAULT 'a;b;c'`).
3. **Orphaned tables**: 8 tables in `schema.sql` had zero production SQL references (`committee_decisions`, `market_events`, `economic_indicators`, `exchange_rates`, `schema_snapshots`, `rsu_vesting_schedules`, `source_authority_rules`, `asset_taxonomy`).
4. **No safe sync testing path**: The 2026-02-15 DB wipe incident showed agents can't safely test sync logic. There was no way to preview what a sync would change without risking the live 645 MB production DB.

A fifth gap was identified during auditing: 9 GET handlers in `taxonomy.py`, `management.py`, `risk_profiles.py` opened writable `DatabaseConnector()` connections, holding the write lock during sync.

---

## Decision

### 1. Version-Ledger Migration Runner
Added `schema_version` table (`version INTEGER PRIMARY KEY, label VARCHAR, applied_at TIMESTAMP`). Created `_apply_versioned_migration(version, label, stmt)` helper that: (a) checks `schema_version` for an existing row, (b) skips if found, (c) calls `_run_migration()` for loud-failure semantics, (d) records on success. All 54 existing migration blocks + V191 backfill were assigned monotonically-increasing integers and routed through the new helper. The legacy silent-swallow pattern (migrations 1–21) is eliminated — failures now surface in `_migration_failures` and block bootstrap.

The `schema_version` table is created unconditionally at the top of `run_migrations()` (`CREATE TABLE IF NOT EXISTS`). `schema_version` is added to `_REQUIRED_TABLES` so `_assert_bootstrap_complete()` enforces its presence.

### 2. Literal-Aware SQL Splitter
Replaced `str.split(';')` in `initialize_schema()` with `_split_sql_statements()` — a character-by-character parser tracking single-quoted strings, double-quoted identifiers, `--` line comments, and `/* */` block comments. The function is exported and tested.

### 3. Orphaned Table Drop
Confirmed all 8 tables had zero SQL references in `src/` (grepped for each table name in all `.py` files). `asset_taxonomy` additionally verified: test fixtures that CREATE/INSERT into it use isolated in-memory DBs, not the production schema. All 8 dropped via Migration V35–V54 in `run_migrations()`, plus their associated indexes and sequences. `CREATE` blocks removed from `schema.sql`.

### 4. `--compact-db` CLI
DuckDB 1.4.x does not reclaim MVCC dead-block space via `CHECKPOINT` or `VACUUM` (verified experimentally per `docs/decisions/2026-05-05-duckdb-size-management.md`). The only working approach is EXPORT DATABASE (Parquet/ZSTD) → IMPORT DATABASE into a fresh file → atomic swap. Implemented as `python main.py --compact-db` in `src/database/compaction.py`. Safety: lock probe before backup (avoids 645 MB wasted copy if server is running), PID-qualified temp paths (no concurrent collision), row-count verification for 3 key tables before swap, WAL deletion after swap.

### 5. `--dry-run` Sync Sandbox
`python main.py --sync-v3 --dry-run` copies the live DB (and its `.wal` sibling) to a PID-scoped tmp path, closes the live connector (checkpoints WAL before copy), runs `run_full_sync_v3(tmp_connector, config, dry_run=True)`, reports the diff summary, then deletes the copy. The `dry_run=True` flag gates only the two Phase 0/2 backup side-effects — normalization and all sync logic run fully on the sandbox. GCS flush confirmed absent from the orchestrator (only in `sync.py` API route).

### 6. Read-Only GET Handlers
9 GET handlers switched from `DatabaseConnector()` (writable) to `Depends(get_db)` (the project's existing dependency that opens `read_only=True` with a mixed-mode fallback). Using bare `read_only=True` was reviewed and rejected: DuckDB raises `ConnectionException` when read-only and writable connections to the same file coexist in the same process, and the sync route + scheduler hold writable connections during their runs. The `get_db()` dependency at `src/api/dependencies.py:86` handles this gracefully.

---

## Consequences

**Positive:**
- Migrations are now run-once and traceable. A DBA can query `SELECT * FROM schema_version ORDER BY version` to see exactly what has been applied and when.
- Server startup is faster — skips already-applied migrations rather than re-executing `IF NOT EXISTS` for all 55 entries.
- `--compact-db` provides a safe path to reclaim dead-block space (645 MB → ~5 MB expected) before GCS uploads.
- `--dry-run` closes the safety gap from the 2026-02-15 wipe incident — agents can preview sync changes without risking production data.
- Schema is ~150 lines smaller (8 orphaned table definitions removed).

**Negative / Trade-offs:**
- First deployment to the existing production DB (V5.11.3 → V5.12.0): `schema_version` table doesn't exist yet, so all 55 migration entries re-apply. All are safe (DDL uses `IF NOT EXISTS`, data-fixes use `WHERE`/`ON CONFLICT DO NOTHING` guards).
- `_split_sql_statements()` does not handle SQL-standard escaped single-quotes (`''`). Schema.sql currently contains no such patterns; if added in future, the splitter must be updated.
- Version 191 (V19b backfill) is a non-contiguous integer — a minor cosmetic oddity in the version ledger.

**Neutral / Future work:**
- ADR-011 deferred `ensure_goals_table()` write-path as a known issue; fixed in V5.11.1 (separate from Pass F scope).
- The `_split_sql_statements` `''`-escape limitation is documented and can be addressed if schema.sql evolves to need it.
- Orphaned migration files `001`, `003`, `005`, `006`, `007` are tombstoned (RETIRED comments) but not deleted — preserves git history.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| Assign one version per logical "migration phase" (~16 versions) | The implementer assigned one version per DDL statement (~55 versions). More granular but achieves the same run-once semantics. No re-work warranted. |
| `VACUUM` / `CHECKPOINT` for compaction | Experimentally verified to NOT reclaim space in DuckDB 1.4.x (see decision doc 2026-05-05). |
| Use `Depends(get_db)` vs bare `read_only=True` for GET handlers | Bare `read_only=True` was tried and rejected by code review: DuckDB mixed-mode `ConnectionException` at runtime during syncs. `get_db()` fallback is the correct solution. |
| Delete orphaned migration SQL files | Tombstoned instead — file deletion destroys git history of why those tables existed. |

---

## References

- `src/database/connector.py` — `run_migrations()`, `_apply_versioned_migration()`, `_record_migration()`
- `src/database/schema.py` — `_split_sql_statements()`, `_REQUIRED_TABLES`, `bootstrap_database()`
- `src/database/compaction.py` — `compact_database()`
- `src/sync/dry_run.py` — `run_dry_sync()`
- `src/sync/orchestrator.py` — `run_full_sync_v3(dry_run=False)`
- `docs/decisions/2026-05-05-duckdb-size-management.md` — compaction approach decision
- ADR-011 (`ADR-011-schema-migration-consolidation.md`) — the Pass D foundation this supersedes/extends
