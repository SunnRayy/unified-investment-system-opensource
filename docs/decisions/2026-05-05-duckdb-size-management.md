# Decision: DuckDB File Size Management

**Date**: 2026-05-05
**Status**: Decision made — compact before first GCS upload; add `--compact-db` CLI flag in follow-up
**Context**: `feature/cloud-deploy` — first cloud deploy preparation

---

## Problem

The local `data/unified.duckdb` is **645MB** despite containing only ~0.8MB of actual data
(confirmed by `EXPORT DATABASE ... FORMAT PARQUET` → 0.8MB total). The ~800× overhead
affects cold-start latency on Cloud Run: every instance restart downloads the full file from GCS.

---

## Root Cause Analysis

**Three compounding mechanisms:**

### 1. Columnar block minimum allocation
DuckDB's storage format is columnar. Every column in every table gets its own "column segment,"
and the minimum allocation unit is **one block = 256KB**. The schema has 537 columns across 49 tables.

```
537 columns × 256KB minimum = ~134MB structural overhead (column data segments)
+ validity bitmaps per column (~1 block each) = ~134MB more
+ VARCHAR dictionary blocks = ~70MB more
Total structural overhead: ~340MB before any row data
```

This is inherent to the format — not a bug, not fixable without reducing table/column count.

### 2. MVCC dead versions from sync pipeline
DuckDB uses Multi-Version Concurrency Control. Every `DELETE` or `UPDATE` keeps the old row
version in place on disk, marked "dead." The sync pipeline does `DELETE + re-INSERT` for
~100 holdings rows per sync run.

After 28+ sync cycles: thousands of dead holding row versions accumulate, each consuming
block space. The DB grew from 483MB (2026-04-07) to 645MB (2026-05-05) = +6MB/sync.

### 3. No auto-compaction in DuckDB 1.4.x
- `CHECKPOINT` — flushes the write-ahead log, does not reclaim dead-version blocks
- `VACUUM ANALYZE` — updates column statistics only, no space reclamation
- No equivalent to PostgreSQL's `autovacuum` background process

The only reclamation path is the **EXPORT → IMPORT cycle** (creates a fresh file
with only live data).

---

## Options Considered

### Option A: Accept the bloat
- **Pro**: No operational overhead
- **Con**: 645MB → ~45s cold start on Cloud Run; grows ~6MB/sync → 1GB+ in a year
- **Decision**: Not acceptable for cloud deployment

### Option B: Run `VACUUM` in DuckDB
- **Pro**: Simple one-liner
- **Con**: DuckDB 1.4.x VACUUM doesn't reclaim space from dead MVCC versions. Verified: no size change after `VACUUM ANALYZE`.
- **Decision**: Doesn't work

### Option C: One-time EXPORT/IMPORT before first GCS upload
- **Pro**: Reduces 645MB → ~5-10MB; one-time manual step; no pipeline changes needed
- **Con**: Requires careful backup + row-count verification; can't be automated easily (needs DB to be closed)
- **Decision**: **Do this now** before first `setup-gcs.sh` run

### Option D: Add periodic compaction to sync pipeline
- **Pro**: Keeps DB small automatically
- **Con**: EXPORT/IMPORT requires exclusive access (can't run while API is serving); adds complexity and latency to sync
- **Decision**: Not suitable for inline-with-sync; better as a separate admin command

### Option E: Change pipeline write pattern (TRUNCATE + INSERT instead of DELETE + INSERT)
- **Pro**: Generates fewer dead versions per sync (TRUNCATE doesn't create per-row dead versions)
- **Con**: Risky schema change to the central pipeline (`orchestrator.py`); would still accumulate overhead from other tables
- **Decision**: Investigate separately — potential partial improvement, not blocking

### Option F: Add `python main.py --compact-db` CLI flag
- **Pro**: Clear, operator-triggered, safe with backup guard; consistent with existing CLI patterns
- **Con**: Requires implementation; operator must remember to run it
- **Decision**: **Implement in follow-up** (V5.6.x); document cadence as quarterly + before major GCS uploads

---

## Decision

**Immediate (before V5.6.0 cloud deploy):**
Manually compact the DB via EXPORT/IMPORT before running `setup-gcs.sh`. Steps:

```bash
# Step 1: export (read-only, safe)
python -c "
import duckdb, shutil, os
shutil.rmtree('/tmp/uis_db_export', ignore_errors=True)
src = duckdb.connect('data/unified.duckdb', read_only=True)
src.execute(\"EXPORT DATABASE '/tmp/uis_db_export' (FORMAT PARQUET, COMPRESSION ZSTD)\")
src.close()
print('Export done. Check /tmp/uis_db_export/')
"

# Step 2: create compact copy
python main.py --backup   # safety backup to data/backups/
python -c "
import duckdb
dst = duckdb.connect('data/unified_compact.duckdb')
dst.execute(\"IMPORT DATABASE '/tmp/uis_db_export'\")
dst.close()
print('Import done. Verify with --check-integrity before swapping.')
"

# Step 3: verify
# Run python main.py --check-integrity against compact DB first
# Compare row counts for critical tables

# Step 4: swap (only after verification)
# mv data/unified.duckdb data/backups/unified_pre_compact_$(date +%Y%m%d).duckdb
# mv data/unified_compact.duckdb data/unified.duckdb
```

**Near-term (V5.6.x follow-up):**
Implement `python main.py --compact-db` with:
- Automatic backup to `data/backups/`
- EXPORT → fresh file IMPORT
- Row-count verification before swap
- Print before/after file sizes

**Ongoing cadence:**
- Run `--compact-db` **before any major GCS upload** (initial deploy, data migration)
- Schedule **quarterly** for local DB (ties to ~monthly sync schedule: ~20 syncs/quarter = ~120MB growth without compaction)
- Cloud DB: compact and re-upload after significant data changes (not after every sync — one cold start per quarter is acceptable)

---

## Size Projections

| Scenario | Estimated size |
|----------|---------------|
| After compaction (current data) | ~5–10MB |
| After 1 month of weekly syncs | ~50–80MB |
| After 3 months without compaction | ~150–250MB |
| After 6 months without compaction | ~300–500MB |

---

## Relation to Pre-Existing Test Failures

The 9 pre-existing failing tests (Schwab FX convention, valuation signal thresholds,
sync history data dependency) are unrelated to DB size. They reflect code changes
(V5.2.0 native-currency, V5.3.x valuation) that were not accompanied by test updates.
They are tracked separately from this issue.

See the internal issue tracker's duckdb-bloat and raw-fetch entries.
