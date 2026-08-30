"""Tests for src/database/compaction.py — TDD: write tests BEFORE implementation."""
import shutil
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

from src.database.compaction import compact_database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_db(db_path: Path) -> None:
    """Create a minimal DuckDB file with holdings, transactions, and trade_logs tables."""
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE holdings (id INTEGER, asset_id VARCHAR)")
    conn.execute("INSERT INTO holdings VALUES (1, 'AAPL'), (2, 'MSFT'), (3, 'VOO')")
    conn.execute("CREATE TABLE transactions (id INTEGER, asset_id VARCHAR, qty DECIMAL)")
    conn.execute("INSERT INTO transactions VALUES (1, 'AAPL', 10.0), (2, 'MSFT', 5.0)")
    conn.execute("CREATE TABLE trade_logs (id INTEGER, asset_id VARCHAR)")
    conn.execute("INSERT INTO trade_logs VALUES (1, 'AAPL')")
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_compact_database_preserves_row_counts(tmp_path):
    """Compact a real tmp DuckDB, verify row counts match after."""
    db_path = tmp_path / "test.duckdb"
    _make_minimal_db(db_path)

    result = compact_database(db_path=str(db_path))

    # Result keys
    assert "before_bytes" in result
    assert "after_bytes" in result
    assert "rows_verified" in result
    assert "backup_path" in result

    assert result["rows_verified"] is True
    assert result["before_bytes"] > 0
    assert result["after_bytes"] > 0

    # Verify original file still has same row counts
    conn = duckdb.connect(str(db_path), read_only=True)
    assert conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM trade_logs").fetchone()[0] == 1
    conn.close()


def test_compact_database_takes_backup_before_compact(tmp_path, monkeypatch):
    """Verify create_backup is called with reason='pre-compact' when lock probe succeeds.

    When the lock probe fails (DB inaccessible), create_backup must NOT be called.
    """
    db_path = tmp_path / "test.duckdb"
    _make_minimal_db(db_path)

    # --- Sub-test 1: probe succeeds → backup IS called ---
    backup_calls = []

    def fake_create_backup(db_path=None, reason="manual", **kwargs):
        backup_calls.append({"db_path": db_path, "reason": reason})
        fake_path = tmp_path / "backup.duckdb"
        shutil.copy2(db_path, fake_path)
        return fake_path

    monkeypatch.setattr("src.database.compaction.create_backup", fake_create_backup)

    compact_database(db_path=str(db_path))

    assert len(backup_calls) == 1
    assert backup_calls[0]["reason"] == "pre-compact"

    # --- Sub-test 2: probe fails → backup is NOT called ---
    backup_calls.clear()

    original_connect = duckdb.connect
    probe_call_count = {"n": 0}

    def failing_probe(path, read_only=False, **kwargs):
        # Fail the very first connect call (the lock probe)
        if probe_call_count["n"] == 0:
            probe_call_count["n"] += 1
            raise Exception("IO Error: Could not set lock on file")
        return original_connect(path, read_only=read_only, **kwargs)

    monkeypatch.setattr("src.database.compaction.duckdb.connect", failing_probe)

    with pytest.raises(RuntimeError, match="Cannot open DB for compaction"):
        compact_database(db_path=str(db_path))

    # Backup must NOT have been called
    assert len(backup_calls) == 0, (
        "create_backup was called even though the lock probe failed"
    )


def test_compact_database_aborts_on_row_count_mismatch(tmp_path, monkeypatch):
    """If compact file has different row counts, raise RuntimeError and leave original intact."""
    db_path = tmp_path / "test.duckdb"
    _make_minimal_db(db_path)

    call_count = {"n": 0}

    def fake_count_rows(path_arg, read_only=True):
        """First call (original) returns real counts; second call (compact) returns fewer rows."""
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Original: 3 holdings
            return {"holdings": 3, "transactions": 2, "trade_logs": 1}
        else:
            # Compact: simulate mismatch — 1 holding instead of 3
            return {"holdings": 1, "transactions": 2, "trade_logs": 1}

    monkeypatch.setattr("src.database.compaction._count_rows", fake_count_rows)

    with pytest.raises(RuntimeError, match="Row-count mismatch"):
        compact_database(db_path=str(db_path))

    # Original file must be untouched (still has 3 rows)
    conn = duckdb.connect(str(db_path), read_only=True)
    count = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
    conn.close()
    assert count == 3


def test_compact_database_cleans_up_export_dir(tmp_path):
    """Export temp dir is deleted after compaction (success or failure).

    With PID-qualified names the export dir is <parent>/uis_db_compact_export_<pid>.
    We verify no uis_db_compact_export* directories remain after compaction.
    """
    db_path = tmp_path / "test.duckdb"
    _make_minimal_db(db_path)

    compact_database(db_path=str(db_path))

    # No export directory leftovers (regardless of PID suffix)
    leftover_dirs = list(db_path.parent.glob("uis_db_compact_export*"))
    assert leftover_dirs == [], f"Export dir(s) were not cleaned up: {leftover_dirs}"


def test_compact_database_uses_pid_qualified_paths(tmp_path, monkeypatch):
    """Temp export dir and compact file names include the current PID."""
    db_path = tmp_path / "test.duckdb"
    _make_minimal_db(db_path)

    fake_pid = 99999
    monkeypatch.setattr("src.database.compaction.os.getpid", lambda: fake_pid)

    # Track which paths were actually created during the run
    created_paths: list[str] = []
    original_connect = duckdb.connect

    def tracking_connect(path, read_only=False, **kwargs):
        conn = original_connect(path, read_only=read_only, **kwargs)
        created_paths.append(str(path))
        return conn

    monkeypatch.setattr("src.database.compaction.duckdb.connect", tracking_connect)

    compact_database(db_path=str(db_path))

    # The PID must appear in at least one of the temp paths used
    pid_str = str(fake_pid)
    assert any(pid_str in p for p in created_paths), (
        f"PID {fake_pid} not found in any duckdb.connect paths: {created_paths}"
    )

    # After success, no PID-qualified leftovers should remain
    leftover_exports = list(db_path.parent.glob(f"uis_db_compact_export_{fake_pid}*"))
    leftover_compacts = list(db_path.parent.glob(f"*.compact_{fake_pid}.duckdb"))
    assert leftover_exports == [], f"Export dir not cleaned: {leftover_exports}"
    assert leftover_compacts == [], f"Compact file not cleaned: {leftover_compacts}"


def test_compact_database_removes_stale_wal(tmp_path):
    """If a .wal file exists after the atomic swap, it should be deleted."""
    import os as _os

    db_path = tmp_path / "test.duckdb"
    _make_minimal_db(db_path)

    # We'll detect the WAL path that compaction would check for.
    wal_path = Path(str(db_path) + ".wal")

    original_replace = _os.replace

    def replace_and_create_wal(src, dst):
        original_replace(src, dst)
        # Simulate a stale WAL that exists right after the atomic swap
        wal_path.write_bytes(b"fake wal content")

    with patch("src.database.compaction.os.replace", side_effect=replace_and_create_wal):
        compact_database(db_path=str(db_path))

    # The WAL file should have been removed by compaction
    assert not wal_path.exists(), f"Stale WAL file was not removed: {wal_path}"
