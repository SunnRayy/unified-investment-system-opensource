# Operations

Huinsight runs itself with almost no ongoing attention — but DuckDB's storage
model means "almost no attention" isn't "zero," and skipping the
maintenance below eventually turns into a multi-hundred-MB database file for
what should be a few megabytes of actual data. This page explains why that
happens and the three commands that keep it from mattering.

## Why maintenance exists: DuckDB never shrinks on its own

DuckDB is a columnar, MVCC (multi-version concurrency control) database.
Two structural facts follow from that, and neither is a bug:

1. **Columnar storage has per-column-per-row-group overhead.** A schema
   with many tables and columns carries real structural weight before any
   data is written at all.
2. **Every `UPDATE`/`DELETE` leaves a dead version behind.** MVCC keeps the
   old row version around (for snapshot isolation) until something
   explicitly reclaims it — plain `CHECKPOINT` or `VACUUM ANALYZE` does
   **not** do this. Huinsight's sync pipeline deletes and re-inserts holdings
   rows on every run (positions change), so dead versions accumulate at a
   steady rate — a few MB per sync isn't unusual on an actively-used
   instance.

Left alone, the `.duckdb` file grows roughly linearly with sync count, not
with actual portfolio size. This is a known, load-bearing characteristic of
the storage engine, not something a future migration removes — it's why
compaction is a routine, not a one-time fix.

## The three routines

All three live in `scripts/maint_db.py`, and are dry-run by default — they
print what they'd do and change nothing unless you pass `--execute`.

### Compact (reclaim MVCC dead versions)

```bash
./dev.sh stop && .venv/bin/python scripts/maint_db.py --compact-local && ./dev.sh start
```

Exports the database to Parquet and re-imports it into a fresh file — the
only way to actually reclaim the dead-version overhead described above.
Requires the app to be stopped (it needs exclusive access to the DB file).
For a Cloud Run deployment: `--compact-cloud` does the same thing against
the GCS-persisted database and restarts the service.

How often: there's no fixed schedule — compact when `pragma_database_size()`
looks disproportionate to your actual holdings count, or on whatever cadence
your own sync frequency makes sensible. Nothing breaks if you don't; the
file just keeps growing.

### Prune backups

```bash
.venv/bin/python scripts/maint_db.py --prune-backups            # dry-run
.venv/bin/python scripts/maint_db.py --prune-backups --execute  # actually delete
```

Every `--sync-v3` and `--compact-*` run takes a full backup first
(`data/backups/`, or the cloud equivalent) — that's the safety net, not
optional. Backups accumulate the same way the main DB does, just as whole
extra copies instead of dead row versions. Pruning keeps a bounded recent
set (newest 8 by default) and only removes backups under ~1 GiB, so a
pruning bug can't silently delete something large and important.

**Backups are otherwise human-delete-only** — no automated process deletes
them except this explicit, dry-run-by-default command.

### `--all`

`.venv/bin/python scripts/maint_db.py --all` runs prune + compact-local +
compact-cloud in sequence. The pre-push git hook
(`scripts/git-hooks/pre-push`) runs backup pruning automatically on every
push to a release branch — install it once per machine:

```bash
cp scripts/git-hooks/pre-push .git/hooks/ && chmod +x .git/hooks/pre-push
```

## Related commands (not maintenance, but adjacent)

```bash
python main.py --backup            # one-off manual backup, independent of a sync
python main.py --list-backups      # what backups exist and when
python main.py --check-integrity   # the 16 invariant checks, standalone (no sync)
```

## Database safety

A few rules this project treats as non-negotiable, enforced partly by a
pre-tool-use hook in the dev environment and partly by convention:

- Never run `--init`/`--reset`, `DROP TABLE`, `TRUNCATE`, or `DELETE FROM`
  against a database you care about without a fresh backup first.
- Verify a database has real data before syncing into it (row count > some
  sane floor) — syncing into an unexpectedly-empty DB is how a real
  instance's data has been lost before. If a DB looks empty when it
  shouldn't, stop and investigate rather than syncing.
- Backups are for humans to delete, not scripts (`--prune-backups` is the
  one sanctioned exception, and it's bounded and dry-run-by-default as
  described above).
