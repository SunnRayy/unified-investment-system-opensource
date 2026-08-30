"""DuckDB compaction via EXPORT DATABASE → IMPORT DATABASE cycle.

DuckDB 1.4.x does not reclaim dead MVCC block space via CHECKPOINT or VACUUM.
The only reclamation path is a full EXPORT → fresh-file IMPORT.

See docs/decisions/2026-05-05-duckdb-size-management.md (Option F) for the
rationale and manual procedure this function automates.

Usage:
    python main.py --compact-db
"""
import logging
import os
import shutil
from pathlib import Path

import duckdb

from src.database.backup import create_backup
from src.database.connector import DEFAULT_DB_PATH, resolve_db_path

logger = logging.getLogger(__name__)

# Tables used for row-count verification before the atomic swap.
_VERIFY_TABLES = ("holdings", "transactions", "trade_logs")


def compact_database(db_path: str = DEFAULT_DB_PATH) -> dict:
    """Compact the DuckDB database via EXPORT DATABASE → IMPORT into fresh file → atomic swap.

    Steps:
    1. Resolve path (honours UIS_DB_PATH env override).
    2. Record before_bytes.
    3. Take a safety backup (reason='pre-compact').
    4. EXPORT to a sibling temp directory using Parquet + ZSTD.
    5. IMPORT into a fresh .compact.duckdb file.
    6. Row-count verification across holdings / transactions / trade_logs.
    7. Atomic os.replace swap (original untouched until this point).
    8. Record after_bytes.
    9. Clean up export directory.

    Returns:
        dict with keys: before_bytes, after_bytes, rows_verified, backup_path.

    Raises:
        RuntimeError: if row-count verification fails — original file is untouched.
    """
    resolved_path = resolve_db_path(db_path)
    if resolved_path == ":memory:":
        raise ValueError("compact_database does not support in-memory databases")

    resolved = Path(resolved_path)
    if not resolved.exists():
        raise FileNotFoundError(f"Database file not found: {resolved}")

    before_bytes = os.path.getsize(str(resolved))

    # Probe lock BEFORE taking expensive backup — avoids writing a 600+MB backup
    # only to discover the API server holds the write lock.
    try:
        probe = duckdb.connect(str(resolved), read_only=True)
        probe.close()
    except Exception as e:
        raise RuntimeError(
            f"Cannot open DB for compaction (is the API server running?): {e}"
        ) from e

    # Safety backup after lock probe succeeds
    backup_path = create_backup(db_path=str(resolved), reason="pre-compact")
    logger.info("Pre-compact backup created: %s", backup_path)

    # Sibling export/compact paths — PID-qualified to avoid collisions with
    # concurrent compaction runs.
    pid = os.getpid()
    tmp_export = resolved.parent / f"uis_db_compact_export_{pid}"
    compact_path = resolved.with_suffix(f".compact_{pid}.duckdb")

    try:
        # --- Step 1: Export from read-only connection ---
        shutil.rmtree(tmp_export, ignore_errors=True)
        src_conn = duckdb.connect(str(resolved), read_only=True)
        try:
            src_conn.execute(
                f"EXPORT DATABASE '{tmp_export}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        finally:
            src_conn.close()

        # --- Step 2: Import into a fresh file ---
        if compact_path.exists():
            compact_path.unlink()

        dst_conn = duckdb.connect(str(compact_path))
        try:
            dst_conn.execute(f"IMPORT DATABASE '{tmp_export}'")
        finally:
            dst_conn.close()

        # --- Step 3: Row-count verification ---
        orig_counts = _count_rows(str(resolved), read_only=True)
        compact_counts = _count_rows(str(compact_path), read_only=True)

        mismatches = [
            t for t in _VERIFY_TABLES
            if orig_counts.get(t) != compact_counts.get(t)
        ]
        if mismatches:
            compact_path.unlink(missing_ok=True)
            detail = "; ".join(
                f"{t}: {orig_counts.get(t)} vs {compact_counts.get(t)}"
                for t in mismatches
            )
            raise RuntimeError(
                f"Row-count mismatch: compaction aborted, original untouched ({detail})"
            )

        # --- Step 4: Atomic swap ---
        os.replace(str(compact_path), str(resolved))

        # Remove any stale WAL that would replay against the wrong page layout
        wal_path = Path(str(resolved) + ".wal")
        if wal_path.exists():
            logger.warning("Removing stale WAL file after compaction: %s", wal_path)
            wal_path.unlink()

        after_bytes = os.path.getsize(str(resolved))
        logger.info(
            "Compaction complete: %dMB → %dMB (saved %.1fMB)",
            before_bytes // 1_000_000,
            after_bytes // 1_000_000,
            (before_bytes - after_bytes) / 1_000_000,
        )

        return {
            "before_bytes": before_bytes,
            "after_bytes": after_bytes,
            "rows_verified": True,
            "backup_path": str(backup_path),
        }

    finally:
        shutil.rmtree(tmp_export, ignore_errors=True)
        # Clean up compact file if still present (failure path)
        if compact_path.exists():
            compact_path.unlink(missing_ok=True)


def _count_rows(db_path: str, read_only: bool = True) -> dict:
    """Return {table_name: row_count} for each verification table that exists."""
    conn = duckdb.connect(db_path, read_only=read_only)
    counts: dict = {}
    try:
        for table in _VERIFY_TABLES:
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                counts[table] = row[0] if row else 0
            except Exception:
                # Table may not exist in minimal test DBs — treat as 0
                counts[table] = 0
    finally:
        conn.close()
    return counts
