"""Tests for database backup utility.

TDD: These tests are written FIRST, before the implementation.
Run: pytest tests/database/test_backup.py -v
Expected: All tests should FAIL initially (RED phase).
"""
import pytest
from pathlib import Path
from datetime import datetime
import time


class TestCreateBackup:
    """Tests for create_backup() function."""

    def test_create_backup_creates_timestamped_copy(self, tmp_path):
        """Backup creates a timestamped copy of the database file."""
        from src.database.backup import create_backup

        # Create a fake DB file
        db_path = tmp_path / "test.duckdb"
        db_path.write_bytes(b"fake database content")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Call create_backup
        backup_path = create_backup(
            db_path=str(db_path),
            backup_dir=str(backup_dir),
            reason="test"
        )

        # Assert backup file exists
        assert backup_path.exists()
        # Assert naming pattern: unified_YYYY-MM-DD_HHMMSS_<reason>.duckdb
        assert "test" in backup_path.name
        assert backup_path.suffix == ".duckdb"
        # Assert content matches original
        assert backup_path.read_bytes() == db_path.read_bytes()

    def test_create_backup_never_overwrites(self, tmp_path):
        """Two backups in quick succession should both exist (no overwrite)."""
        from src.database.backup import create_backup

        db_path = tmp_path / "test.duckdb"
        db_path.write_bytes(b"database v1")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create first backup
        backup1 = create_backup(str(db_path), str(backup_dir), reason="first")

        # Tiny delay to ensure different timestamp (or suffix)
        time.sleep(0.01)

        # Modify DB content
        db_path.write_bytes(b"database v2")

        # Create second backup
        backup2 = create_backup(str(db_path), str(backup_dir), reason="second")

        # Both should exist
        assert backup1.exists()
        assert backup2.exists()
        assert backup1 != backup2

        # Contents should be different
        assert backup1.read_bytes() == b"database v1"
        assert backup2.read_bytes() == b"database v2"

    def test_create_backup_collision_same_reason_same_second(self, tmp_path):
        """Same reason in same second uses counter suffix to avoid overwrite."""
        from src.database.backup import create_backup

        db_path = tmp_path / "test.duckdb"
        db_path.write_bytes(b"v1")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create two backups with SAME reason in quick succession
        backup1 = create_backup(str(db_path), str(backup_dir), reason="sync")
        db_path.write_bytes(b"v2")
        backup2 = create_backup(str(db_path), str(backup_dir), reason="sync")

        # Both should exist with different names
        assert backup1.exists()
        assert backup2.exists()
        assert backup1 != backup2
        
        # Second should have counter suffix like _1
        assert "_1" in backup2.name or backup1.name != backup2.name
        
        # Contents preserved correctly
        assert backup1.read_bytes() == b"v1"
        assert backup2.read_bytes() == b"v2"

    def test_create_backup_missing_db_raises_error(self, tmp_path):
        """Calling create_backup on non-existent path raises FileNotFoundError."""
        from src.database.backup import create_backup

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        non_existent = tmp_path / "does_not_exist.duckdb"

        with pytest.raises(FileNotFoundError) as exc_info:
            create_backup(str(non_existent), str(backup_dir), reason="test")

        # Error message should be helpful
        assert "does_not_exist.duckdb" in str(exc_info.value)

    def test_create_backup_creates_backup_dir_if_missing(self, tmp_path):
        """If backup_dir doesn't exist, it should be created."""
        from src.database.backup import create_backup

        db_path = tmp_path / "test.duckdb"
        db_path.write_bytes(b"content")
        backup_dir = tmp_path / "new_backups"

        # backup_dir doesn't exist yet
        assert not backup_dir.exists()

        backup_path = create_backup(str(db_path), str(backup_dir), reason="test")

        # Should create the directory and the backup
        assert backup_dir.exists()
        assert backup_path.exists()


class TestListBackups:
    """Tests for list_backups() function."""

    def test_list_backups_returns_sorted(self, tmp_path):
        """List backups returns sorted list (newest first)."""
        from src.database.backup import list_backups, BackupInfo

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create backup files with known timestamps in names
        (backup_dir / "unified_2026-02-08_100000_old.duckdb").write_bytes(b"old")
        (backup_dir / "unified_2026-02-08_120000_mid.duckdb").write_bytes(b"mid")
        (backup_dir / "unified_2026-02-08_140000_new.duckdb").write_bytes(b"new")

        backups = list_backups(str(backup_dir))

        # Should return list of BackupInfo
        assert len(backups) == 3
        assert all(isinstance(b, BackupInfo) for b in backups)

        # Newest first (14:00 > 12:00 > 10:00)
        assert "140000" in backups[0].path.name
        assert "120000" in backups[1].path.name
        assert "100000" in backups[2].path.name

    def test_list_backups_returns_backup_info_fields(self, tmp_path):
        """BackupInfo has path, timestamp, reason, size_bytes fields."""
        from src.database.backup import list_backups

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        content = b"test content 123"
        (backup_dir / "unified_2026-02-08_153000_pre-sync.duckdb").write_bytes(content)

        backups = list_backups(str(backup_dir))

        assert len(backups) == 1
        backup = backups[0]

        assert isinstance(backup.path, Path)
        assert isinstance(backup.timestamp, datetime)
        assert backup.reason == "pre-sync"
        assert backup.size_bytes == len(content)

    def test_list_backups_preserves_reason_with_numbers(self, tmp_path):
        """Reason suffixes like 'phase_3' are preserved, not stripped."""
        from src.database.backup import list_backups

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create backups with numeric suffixes in reason (NOT collision counters)
        (backup_dir / "unified_2026-02-08_100000_phase_3.duckdb").write_bytes(b"a")
        (backup_dir / "unified_2026-02-08_110000_batch_10.duckdb").write_bytes(b"b")
        
        backups = list_backups(str(backup_dir))
        reasons = {b.reason for b in backups}

        # These should be preserved, not stripped to "phase" or "batch"
        assert "phase_3" in reasons
        assert "batch_10" in reasons

    def test_list_backups_empty_directory(self, tmp_path):
        """Empty backup directory returns empty list."""
        from src.database.backup import list_backups

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        backups = list_backups(str(backup_dir))

        assert backups == []

    def test_list_backups_ignores_non_duckdb_files(self, tmp_path):
        """Only .duckdb files are listed."""
        from src.database.backup import list_backups

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        (backup_dir / "unified_2026-02-08_100000_test.duckdb").write_bytes(b"db")
        (backup_dir / "readme.txt").write_text("ignore me")
        (backup_dir / "other.db").write_bytes(b"wrong extension")

        backups = list_backups(str(backup_dir))

        assert len(backups) == 1
        assert backups[0].path.name == "unified_2026-02-08_100000_test.duckdb"


class TestPruneBackups:
    """Tests for prune_backups() retention policy."""

    def test_prune_backups_keeps_restore_relevant_snapshots(self, tmp_path):
        """Prune keeps only restore-relevant backups and removes stale extras."""
        from src.database.backup import prune_backups

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        names = [
            "unified_2026-02-13_100000_pre-sync-v3.duckdb",
            "unified_2026-02-13_090000_pre-sync-v3_1.duckdb",
            "unified_2026-02-12_080000_pre-sync-v3.duckdb",
            "unified_2026-02-11_080000_pre-sync-v3.duckdb",
            "unified_2026-02-10_080000_pre-reader-insertion.duckdb",
            "unified_2026-02-09_080000_pre-reader-insertion.duckdb",
            "unified_2026-02-01_080000_pre-seed-taxonomy.duckdb",
            "unified_2026-01-31_080000_pre-seed-taxonomy.duckdb",
            "unified_2026-01-30_080000_pre-seed-taxonomy.duckdb",
            "unified_2026-02-13_070000_pre-sync-v3_KEEP.duckdb",
            "unified_custom_snapshot.duckdb",
        ]
        for idx, name in enumerate(names):
            (backup_dir / name).write_bytes(f"db-{idx}".encode())

        removed = prune_backups(
            backup_dir=str(backup_dir),
            now=datetime(2026, 2, 13, 12, 0, 0),
            max_files=50,
            max_total_bytes=10_000_000
        )

        remaining = {p.name for p in backup_dir.glob("*.duckdb")}
        removed_names = {p.name for p in removed}

        assert "unified_2026-01-30_080000_pre-seed-taxonomy.duckdb" in removed_names
        assert "unified_2026-01-30_080000_pre-seed-taxonomy.duckdb" not in remaining

        assert "unified_2026-02-13_100000_pre-sync-v3.duckdb" in remaining
        assert "unified_2026-02-13_090000_pre-sync-v3_1.duckdb" in remaining
        assert "unified_2026-02-12_080000_pre-sync-v3.duckdb" in remaining
        assert "unified_2026-02-11_080000_pre-sync-v3.duckdb" in remaining
        assert "unified_2026-02-10_080000_pre-reader-insertion.duckdb" in remaining
        assert "unified_2026-02-09_080000_pre-reader-insertion.duckdb" in remaining
        assert "unified_2026-02-01_080000_pre-seed-taxonomy.duckdb" in remaining
        assert "unified_2026-01-31_080000_pre-seed-taxonomy.duckdb" in remaining
        assert "unified_2026-02-13_070000_pre-sync-v3_KEEP.duckdb" in remaining
        assert "unified_custom_snapshot.duckdb" in remaining

    def test_prune_backups_enforces_max_files_cap(self, tmp_path):
        """Prune enforces max non-KEEP backup count by deleting oldest files."""
        from src.database.backup import prune_backups

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        names = [
            "unified_2026-02-13_100000_pre-sync-v3.duckdb",
            "unified_2026-02-12_100000_pre-sync-v3.duckdb",
            "unified_2026-02-11_100000_pre-reader-insertion.duckdb",
            "unified_2026-02-10_100000_pre-reader-insertion.duckdb",
            "unified_2026-02-09_100000_pre-seed-taxonomy.duckdb",
            "unified_2026-02-08_100000_pre-seed-taxonomy.duckdb",
            "unified_2026-02-07_100000_pre-sync-v3_KEEP.duckdb",
            "unified_manual_snapshot.duckdb",
        ]
        for idx, name in enumerate(names):
            (backup_dir / name).write_bytes(f"db-{idx}".encode())

        prune_backups(
            backup_dir=str(backup_dir),
            now=datetime(2026, 2, 13, 12, 0, 0),
            max_files=3,
            max_total_bytes=10_000_000
        )

        remaining = {p.name for p in backup_dir.glob("*.duckdb")}
        remaining_non_keep_parsed = sorted(
            name for name in remaining
            if name.startswith("unified_2026-") and "_KEEP" not in name
        )

        assert remaining_non_keep_parsed == sorted([
            "unified_2026-02-13_100000_pre-sync-v3.duckdb",
            "unified_2026-02-12_100000_pre-sync-v3.duckdb",
            "unified_2026-02-11_100000_pre-reader-insertion.duckdb",
        ])
        assert "unified_2026-02-07_100000_pre-sync-v3_KEEP.duckdb" in remaining
        assert "unified_manual_snapshot.duckdb" in remaining

    def test_create_backup_calls_prune_backups(self, tmp_path, monkeypatch):
        """create_backup should invoke prune_backups after writing new backup."""
        import src.database.backup as backup_module

        db_path = tmp_path / "test.duckdb"
        db_path.write_bytes(b"db-content")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        called = {"count": 0, "backup_dir": None}

        def fake_prune_backups(backup_dir: str, **kwargs):
            called["count"] += 1
            called["backup_dir"] = Path(backup_dir)
            return []

        monkeypatch.setattr(backup_module, "prune_backups", fake_prune_backups)

        backup_module.create_backup(
            db_path=str(db_path),
            backup_dir=str(backup_dir),
            reason="pre-sync-v3"
        )

        assert called["count"] == 1
        assert called["backup_dir"] == backup_dir


class TestCreateBackupPathResolution:
    """Tests for UIS_DB_PATH-aware path resolution in backup functions."""

    def test_create_backup_default_db_path_uses_uis_db_path_env(self, tmp_path, monkeypatch):
        """create_backup() with default db_path resolves via UIS_DB_PATH env override.

        On Cloud Run the DB lives at /tmp/data/unified.duckdb (or similar), not at
        the relative 'data/unified.duckdb'.  create_backup() must honour UIS_DB_PATH
        so the backup lands next to the real DB, not in the cwd-relative default.
        """
        import src.database.backup as backup_module

        # Create a minimal file representing the DB at a tmp location
        db_file = tmp_path / "unified.duckdb"
        db_file.write_bytes(b"fake-db")

        monkeypatch.setenv("UIS_DB_PATH", str(db_file))

        # Call with default db_path — should resolve via env override
        backup_path = backup_module.create_backup(reason="test-env-resolution")

        # The backup must live next to the resolved DB (i.e. inside tmp_path/backups)
        assert backup_path.exists()
        assert backup_path.parent == tmp_path / "backups"
        assert "test-env-resolution" in backup_path.name

    def test_create_backup_explicit_db_path_and_explicit_backup_dir_unchanged(self, tmp_path):
        """Explicit absolute db_path + explicit backup_dir pass through unchanged."""
        from src.database.backup import create_backup

        db_file = tmp_path / "mydb.duckdb"
        db_file.write_bytes(b"db-content")
        backup_dir = tmp_path / "custom_backups"
        backup_dir.mkdir()

        backup_path = create_backup(
            db_path=str(db_file),
            backup_dir=str(backup_dir),
            reason="explicit"
        )

        assert backup_path.exists()
        assert backup_path.parent == backup_dir

    def test_list_backups_default_dir_uses_uis_db_path_env(self, tmp_path, monkeypatch):
        """list_backups() with default backup_dir resolves next to UIS_DB_PATH."""
        import src.database.backup as backup_module

        db_file = tmp_path / "unified.duckdb"
        db_file.write_bytes(b"fake-db")
        monkeypatch.setenv("UIS_DB_PATH", str(db_file))

        # Prepare a backup file in the expected resolved location
        expected_backup_dir = tmp_path / "backups"
        expected_backup_dir.mkdir()
        (expected_backup_dir / "unified_2026-07-04_100000_test.duckdb").write_bytes(b"bkp")

        backups = backup_module.list_backups()

        assert len(backups) == 1
        assert backups[0].reason == "test"

    def test_prune_backups_default_dir_uses_uis_db_path_env(self, tmp_path, monkeypatch):
        """prune_backups() with default backup_dir resolves next to UIS_DB_PATH."""
        import src.database.backup as backup_module

        db_file = tmp_path / "unified.duckdb"
        db_file.write_bytes(b"fake-db")
        monkeypatch.setenv("UIS_DB_PATH", str(db_file))

        # No backup directory exists → prune_backups should return [] without error
        result = backup_module.prune_backups()
        assert result == []
