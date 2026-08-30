"""Tests for scripts/maint_db.py — pull-cloud feature.

Tests use tmp_path only; never touch the real data/ tree.
Module-level path constants (LOCAL_DB, LOCAL_BACKUPS, etc.) are monkeypatched.

Run individually:
    pytest tests/database/test_maint_db.py -q -n 0
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

# ── Import maint_db from scripts/ (not a package) ──────────────────────────
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import maint_db  # noqa: E402  (path insert above)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_valid_db(
    path: Path,
    holdings_count: int = 700,
    schema_version: int = 66,
    with_trade_logs: bool = True,
    with_schema_version: bool = True,
) -> None:
    """Create a minimal DuckDB with the tables required by verify_pulled_db."""
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE holdings (id INTEGER, asset_id VARCHAR)")
    conn.executemany(
        "INSERT INTO holdings VALUES (?, ?)",
        [(i, f"ASSET_{i}") for i in range(holdings_count)],
    )
    if with_trade_logs:
        conn.execute("CREATE TABLE trade_logs (id INTEGER)")
    if with_schema_version:
        conn.execute("CREATE TABLE schema_version (version INTEGER)")
        conn.execute(f"INSERT INTO schema_version VALUES ({schema_version})")
    conn.close()


# ── verify_pulled_db ─────────────────────────────────────────────────────────

class TestVerifyPulledDb:

    def test_passes_on_well_formed_db(self, tmp_path, monkeypatch):
        """Well-formed DB (≥600 holdings, trade_logs, schema_version ≥64) passes."""
        db = tmp_path / "staged.duckdb"
        _make_valid_db(db, holdings_count=700, schema_version=66)
        monkeypatch.setattr(maint_db, "MIN_STAGING_SIZE_BYTES", 0)

        ok, msg = maint_db.verify_pulled_db(db)

        assert ok, f"expected OK but got: {msg}"
        assert "700" in msg
        assert "66" in msg

    def test_fails_on_missing_file(self, tmp_path):
        """Non-existent path returns failure immediately."""
        missing = tmp_path / "no_such.duckdb"
        ok, msg = maint_db.verify_pulled_db(missing)
        assert not ok
        assert "not found" in msg.lower()

    def test_fails_on_too_small_file(self, tmp_path):
        """File below MIN_STAGING_SIZE_BYTES (10 MiB) returns failure."""
        tiny = tmp_path / "tiny.duckdb"
        tiny.write_bytes(b"x" * 100)

        ok, msg = maint_db.verify_pulled_db(tiny)

        assert not ok
        assert "small" in msg.lower() or "min" in msg.lower()

    def test_fails_on_missing_holdings_table(self, tmp_path, monkeypatch):
        """DB without a holdings table returns failure."""
        db = tmp_path / "no_holdings.duckdb"
        conn = duckdb.connect(str(db))
        conn.execute("CREATE TABLE other_table (id INTEGER)")
        conn.close()
        monkeypatch.setattr(maint_db, "MIN_STAGING_SIZE_BYTES", 0)

        ok, msg = maint_db.verify_pulled_db(db)

        assert not ok

    def test_fails_on_low_holdings_count(self, tmp_path, monkeypatch):
        """DB with fewer than MIN_HOLDINGS_COUNT holdings returns failure."""
        db = tmp_path / "low.duckdb"
        _make_valid_db(db, holdings_count=100, schema_version=66)
        monkeypatch.setattr(maint_db, "MIN_STAGING_SIZE_BYTES", 0)

        ok, msg = maint_db.verify_pulled_db(db)

        assert not ok
        assert "100" in msg or "low" in msg.lower() or "count" in msg.lower()

    def test_fails_on_low_schema_version(self, tmp_path, monkeypatch):
        """DB with schema_version < MIN_SCHEMA_VERSION (64) returns failure."""
        db = tmp_path / "old_schema.duckdb"
        _make_valid_db(db, holdings_count=700, schema_version=63)
        monkeypatch.setattr(maint_db, "MIN_STAGING_SIZE_BYTES", 0)

        ok, msg = maint_db.verify_pulled_db(db)

        assert not ok
        assert "63" in msg or "schema" in msg.lower() or "version" in msg.lower()

    def test_fails_on_missing_trade_logs(self, tmp_path, monkeypatch):
        """DB without trade_logs table returns failure naming the table."""
        db = tmp_path / "no_trade_logs.duckdb"
        _make_valid_db(db, holdings_count=700, schema_version=66, with_trade_logs=False)
        monkeypatch.setattr(maint_db, "MIN_STAGING_SIZE_BYTES", 0)

        ok, msg = maint_db.verify_pulled_db(db)

        assert not ok
        assert "trade_logs" in msg

    def test_fails_on_missing_schema_version_table(self, tmp_path, monkeypatch):
        """DB without schema_version table returns failure naming the table."""
        db = tmp_path / "no_schema_ver.duckdb"
        _make_valid_db(db, holdings_count=700, schema_version=66, with_schema_version=False)
        monkeypatch.setattr(maint_db, "MIN_STAGING_SIZE_BYTES", 0)

        ok, msg = maint_db.verify_pulled_db(db)

        assert not ok
        assert "schema_version" in msg


# ── _rotate_backups ──────────────────────────────────────────────────────────

class TestRotateBackups:

    def _make_files(
        self, backup_dir: Path, prefix: str, count: int
    ) -> list[Path]:
        """Create fake backup files with staggered mtimes (0 = oldest, count-1 = newest)."""
        files = []
        for i in range(count):
            fname = f"{prefix}2026{i:04d}01_000000.duckdb"
            f = backup_dir / fname
            f.write_bytes(b"x")
            os.utime(f, (float(i), float(i)))
            files.append(f)
        return files

    def test_cloud_mirror_keeps_three_newest(self, tmp_path):
        """_rotate_backups keeps the 3 newest cloud-mirror files, deletes the rest."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        self._make_files(backup_dir, "cloud-mirror-", 5)

        maint_db._rotate_backups(backup_dir, "cloud-mirror-*.duckdb", 3)

        remaining = list(backup_dir.glob("cloud-mirror-*.duckdb"))
        assert len(remaining) == 3
        mtimes = sorted(f.stat().st_mtime for f in remaining)
        # The 3 newest (indices 2, 3, 4 → mtimes 2.0, 3.0, 4.0) should remain.
        assert mtimes == [2.0, 3.0, 4.0]

    def test_pre_pull_keeps_two_newest(self, tmp_path):
        """_rotate_backups keeps the 2 newest pre-pull files, deletes the rest."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        self._make_files(backup_dir, "pre-pull-", 4)

        maint_db._rotate_backups(backup_dir, "pre-pull-*.duckdb", 2)

        remaining = list(backup_dir.glob("pre-pull-*.duckdb"))
        assert len(remaining) == 2
        mtimes = sorted(f.stat().st_mtime for f in remaining)
        # Indices 2, 3 (mtimes 2.0, 3.0) should remain.
        assert mtimes == [2.0, 3.0]

    def test_no_delete_when_at_or_under_limit(self, tmp_path):
        """When file count <= keep nothing is deleted."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        self._make_files(backup_dir, "cloud-mirror-", 2)

        maint_db._rotate_backups(backup_dir, "cloud-mirror-*.duckdb", 3)

        remaining = list(backup_dir.glob("cloud-mirror-*.duckdb"))
        assert len(remaining) == 2  # nothing deleted


# ── prune_backups exemption ──────────────────────────────────────────────────

class TestPruneExemption:

    def test_prune_backups_skips_cloud_mirror_and_pre_pull(self, tmp_path, monkeypatch):
        """--prune-backups (local) leaves cloud-mirror-* and pre-pull-* untouched."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # 10 regular backups on 10 DISTINCT DAYS (newest = 9). Retention is
        # date-based, so the mtimes must be a day apart; spacing them by
        # seconds would put all ten on one calendar day and keep only one.
        DAY = 86400.0
        for i in range(10):
            f = backup_dir / f"uis_backup_2026{i:02d}01.duckdb"
            f.write_bytes(b"x")
            os.utime(f, (i * DAY, i * DAY))

        # 5 cloud-mirror files — should survive prune
        for i in range(5):
            f = backup_dir / f"cloud-mirror-202605{i:02d}_000000.duckdb"
            f.write_bytes(b"x")
            os.utime(f, ((100 + i) * DAY, (100 + i) * DAY))

        # 3 pre-pull files — should survive prune
        for i in range(3):
            f = backup_dir / f"pre-pull-202606{i:02d}_000000.duckdb"
            f.write_bytes(b"x")
            os.utime(f, ((200 + i) * DAY, (200 + i) * DAY))

        monkeypatch.setattr(maint_db, "LOCAL_BACKUPS", backup_dir)
        monkeypatch.setattr(maint_db, "KEEP_NEWEST", 8)
        # Skip the GCS leg — we only care about local behaviour here.
        monkeypatch.setattr(maint_db, "_gcs_backups", lambda: [])
        # _require_bucket() (Program OSR WS-5b) only checks this is truthy.
        monkeypatch.setattr(maint_db, "_BUCKET_NAME", "test-bucket")

        maint_db.prune_backups(execute=True)

        # Regular backups: 10 total, keep 8 newest → 2 deleted.
        regular = list(backup_dir.glob("uis_backup_*.duckdb"))
        assert len(regular) == 8, (
            f"expected 8 regular backups after prune, got {len(regular)}"
        )

        # Self-managed files must be completely untouched.
        mirrors = list(backup_dir.glob("cloud-mirror-*.duckdb"))
        assert len(mirrors) == 5, (
            f"cloud-mirror files should not be pruned, got {len(mirrors)}"
        )
        pre_pulls = list(backup_dir.glob("pre-pull-*.duckdb"))
        assert len(pre_pulls) == 3, (
            f"pre-pull files should not be pruned, got {len(pre_pulls)}"
        )


# ── pull_cloud end-to-end ────────────────────────────────────────────────────

class TestPullCloudEndToEnd:

    def _patch_module(
        self,
        monkeypatch,
        *,
        tmp_path: Path,
        current_db: Path,
        cloud_db: Path,
        server_running: bool = False,
    ) -> None:
        """Patch all module-level variables and side-effecting functions."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(exist_ok=True)

        monkeypatch.setattr(maint_db, "LOCAL_DB", current_db)
        monkeypatch.setattr(maint_db, "LOCAL_BACKUPS", backup_dir)
        monkeypatch.setattr(maint_db, "MIN_STAGING_SIZE_BYTES", 0)
        monkeypatch.setattr(maint_db, "_check_server_running", lambda: server_running)
        # _require_bucket() (Program OSR WS-5b) only checks this is truthy —
        # the actual cloud download is faked below, never reads CLOUD_DB.
        monkeypatch.setattr(maint_db, "_BUCKET_NAME", "test-bucket")

        def fake_download(staging: Path) -> None:
            shutil.copy2(str(cloud_db), str(staging))

        monkeypatch.setattr(maint_db, "_download_cloud_db", fake_download)

    def test_installs_cloud_db_and_creates_backups(self, tmp_path, monkeypatch):
        """Full pull flow: old DB archived, mirror created, new DB installed."""
        current_db = tmp_path / "unified.duckdb"
        _make_valid_db(current_db, holdings_count=500)
        cloud_db = tmp_path / "cloud_source.duckdb"
        _make_valid_db(cloud_db, holdings_count=800, schema_version=66)

        self._patch_module(
            monkeypatch,
            tmp_path=tmp_path,
            current_db=current_db,
            cloud_db=cloud_db,
        )

        maint_db.pull_cloud(yes=True, force=False)

        # New unified.duckdb is installed with cloud data.
        assert current_db.exists()
        conn = duckdb.connect(str(current_db), read_only=True)
        count = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
        conn.close()
        assert count == 800

        backup_dir = tmp_path / "backups"

        # Old DB moved to exactly one pre-pull backup.
        pre_pulls = list(backup_dir.glob("pre-pull-*.duckdb"))
        assert len(pre_pulls) == 1

        # Cloud mirror created.
        mirrors = list(backup_dir.glob("cloud-mirror-*.duckdb"))
        assert len(mirrors) == 1

        # Staging file consumed (os.replace moved it to unified.duckdb).
        staging = backup_dir / maint_db.STAGING_NAME
        assert not staging.exists()

    def test_removes_stale_wal(self, tmp_path, monkeypatch):
        """WAL file belonging to the old DB is deleted after old DB is archived."""
        current_db = tmp_path / "unified.duckdb"
        _make_valid_db(current_db)
        # Simulate a stale WAL left by the old DB.
        wal_path = Path(str(current_db) + ".wal")
        wal_path.write_bytes(b"stale wal content")

        cloud_db = tmp_path / "cloud_source.duckdb"
        _make_valid_db(cloud_db, holdings_count=700, schema_version=66)

        self._patch_module(
            monkeypatch,
            tmp_path=tmp_path,
            current_db=current_db,
            cloud_db=cloud_db,
        )

        maint_db.pull_cloud(yes=True, force=False)

        # WAL must have been deleted.
        assert not wal_path.exists(), "Stale WAL was not removed"

    def test_aborts_on_verification_failure_local_db_untouched(
        self, tmp_path, monkeypatch
    ):
        """If verification fails, the local DB must be completely untouched."""
        current_db = tmp_path / "unified.duckdb"
        _make_valid_db(current_db, holdings_count=700)
        initial_size = current_db.stat().st_size

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        monkeypatch.setattr(maint_db, "LOCAL_DB", current_db)
        monkeypatch.setattr(maint_db, "LOCAL_BACKUPS", backup_dir)
        monkeypatch.setattr(maint_db, "_check_server_running", lambda: False)
        # Do NOT bypass size check — the bad staging file is tiny.

        def bad_download(staging: Path) -> None:
            staging.write_bytes(b"not a real db")

        monkeypatch.setattr(maint_db, "_download_cloud_db", bad_download)

        with pytest.raises(SystemExit) as exc_info:
            maint_db.pull_cloud(yes=True, force=False)

        assert exc_info.value.code != 0

        # Local DB must be untouched.
        assert current_db.exists()
        assert current_db.stat().st_size == initial_size

        # Staging file must be cleaned up.
        staging = backup_dir / maint_db.STAGING_NAME
        assert not staging.exists()

    def test_refuses_when_server_running_without_force(self, tmp_path, monkeypatch):
        """Exits non-zero when backend is running and --force is not set."""
        current_db = tmp_path / "unified.duckdb"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        monkeypatch.setattr(maint_db, "LOCAL_DB", current_db)
        monkeypatch.setattr(maint_db, "LOCAL_BACKUPS", backup_dir)
        monkeypatch.setattr(maint_db, "_check_server_running", lambda: True)

        with pytest.raises(SystemExit) as exc_info:
            maint_db.pull_cloud(yes=True, force=False)

        assert exc_info.value.code != 0

    def test_force_bypasses_server_guard(self, tmp_path, monkeypatch):
        """--force proceeds even when _check_server_running() returns True."""
        current_db = tmp_path / "unified.duckdb"
        _make_valid_db(current_db)
        cloud_db = tmp_path / "cloud_source.duckdb"
        _make_valid_db(cloud_db, holdings_count=700, schema_version=66)

        self._patch_module(
            monkeypatch,
            tmp_path=tmp_path,
            current_db=current_db,
            cloud_db=cloud_db,
            server_running=True,  # server appears running
        )

        # Should NOT raise; --force=True bypasses the guard.
        maint_db.pull_cloud(yes=True, force=True)

        assert current_db.exists()

    def test_rotation_limits_respected(self, tmp_path, monkeypatch):
        """After pull, at most PRE_PULL_KEEP pre-pull and CLOUD_MIRROR_KEEP mirror files exist."""
        current_db = tmp_path / "unified.duckdb"
        _make_valid_db(current_db)
        cloud_db = tmp_path / "cloud_source.duckdb"
        _make_valid_db(cloud_db, holdings_count=700, schema_version=66)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Pre-seed more files than the limits to force rotation.
        for i in range(5):
            f = backup_dir / f"pre-pull-2026050{i}_000000.duckdb"
            f.write_bytes(b"x")
            os.utime(f, (float(i), float(i)))
        for i in range(6):
            f = backup_dir / f"cloud-mirror-2026060{i}_000000.duckdb"
            f.write_bytes(b"x")
            os.utime(f, (float(i), float(i)))

        monkeypatch.setattr(maint_db, "LOCAL_DB", current_db)
        monkeypatch.setattr(maint_db, "LOCAL_BACKUPS", backup_dir)
        monkeypatch.setattr(maint_db, "MIN_STAGING_SIZE_BYTES", 0)
        monkeypatch.setattr(maint_db, "_check_server_running", lambda: False)
        monkeypatch.setattr(maint_db, "_BUCKET_NAME", "test-bucket")

        def fake_download(staging: Path) -> None:
            shutil.copy2(str(cloud_db), str(staging))

        monkeypatch.setattr(maint_db, "_download_cloud_db", fake_download)

        maint_db.pull_cloud(yes=True, force=False)

        pre_pulls = list(backup_dir.glob("pre-pull-*.duckdb"))
        mirrors = list(backup_dir.glob("cloud-mirror-*.duckdb"))

        assert len(pre_pulls) <= maint_db.PRE_PULL_KEEP, (
            f"expected ≤{maint_db.PRE_PULL_KEEP} pre-pull files, got {len(pre_pulls)}"
        )
        assert len(mirrors) <= maint_db.CLOUD_MIRROR_KEEP, (
            f"expected ≤{maint_db.CLOUD_MIRROR_KEEP} cloud-mirror files, got {len(mirrors)}"
        )


class TestRequireBucket:
    """Program OSR WS-5b: UIS_GCS_BUCKET replaced a hardcoded real bucket
    name. A cloud-touching command must fail loudly (not silently operate on
    gs://None/...) when the env var is unset AND Secret Manager cannot supply
    it either.

    Every test here stubs the Secret Manager lookup. Without that they depend
    on whether the developer's gcloud happens to be authenticated, which is
    exactly the kind of environment-coupled test that passes for the wrong
    reason.
    """

    @pytest.fixture(autouse=True)
    def _no_secret_manager(self, monkeypatch):
        monkeypatch.setattr(maint_db, "_bucket_from_secret_manager", lambda: None)

    def test_raises_when_bucket_unset(self, monkeypatch):
        monkeypatch.setattr(maint_db, "_BUCKET_NAME", None)
        with pytest.raises(SystemExit, match="UIS_GCS_BUCKET is not set"):
            maint_db._require_bucket()

    def test_passes_when_bucket_set(self, monkeypatch):
        monkeypatch.setattr(maint_db, "_BUCKET_NAME", "some-bucket")
        maint_db._require_bucket()  # must not raise

    def test_compact_cloud_requires_bucket(self, monkeypatch):
        monkeypatch.setattr(maint_db, "_BUCKET_NAME", None)
        with pytest.raises(SystemExit, match="UIS_GCS_BUCKET is not set"):
            maint_db.compact_cloud()

    def test_prune_backups_requires_bucket(self, monkeypatch):
        monkeypatch.setattr(maint_db, "_BUCKET_NAME", None)
        with pytest.raises(SystemExit, match="UIS_GCS_BUCKET is not set"):
            maint_db.prune_backups(execute=False)

    def test_pull_cloud_requires_bucket(self, monkeypatch):
        monkeypatch.setattr(maint_db, "_BUCKET_NAME", None)
        with pytest.raises(SystemExit, match="UIS_GCS_BUCKET is not set"):
            maint_db.pull_cloud(yes=True, force=False)

    def test_bucket_derived_from_env_var_at_import(self, monkeypatch):
        """Reads $UIS_GCS_BUCKET, not a hardcoded name — verified by
        reloading the module with the env var set."""
        import importlib

        monkeypatch.setenv("UIS_GCS_BUCKET", "my-test-bucket-xyz")
        importlib.reload(maint_db)
        try:
            assert maint_db._BUCKET_NAME == "my-test-bucket-xyz"
            assert maint_db.BUCKET == "gs://my-test-bucket-xyz"
            assert maint_db.CLOUD_DB == "gs://my-test-bucket-xyz/db/unified.duckdb"
        finally:
            monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)
            importlib.reload(maint_db)


# ── _plan: date-based retention ──────────────────────────────────────────────

class TestPlanDateRetention:
    """`_plan` keeps one backup per day for the newest KEEP_NEWEST dates.

    The previous policy kept the newest N OBJECTS. That is indistinguishable
    from this one until a single day produces N backups — and then it silently
    collapses the whole restore window onto that day. It did: on 2026-08-29 the
    bucket held 16 backups from one day, so "keep newest 8" would have kept
    eight copies of one afternoon and deleted three weeks of history.
    """

    @staticmethod
    def _item(day: int, hour: int, size: int = 100):
        ts = datetime(2026, 8, day, hour, 0, tzinfo=timezone.utc)
        return (f"gs://b/backups/{day:02d}T{hour:02d}.duckdb", ts, size)

    def test_keeps_newest_per_date_not_newest_n_objects(self):
        """The regression that caused the incident: many backups on one day."""
        items = [self._item(29, h) for h in range(16)]          # 16 on one day
        items += [self._item(d, 22) for d in (28, 27, 26, 25)]  # 4 earlier days
        keep, delete = maint_db._plan(items)

        kept_dates = sorted({ts.date() for _, ts, _ in keep})
        assert len(kept_dates) == 5, "one slot per date, not per object"
        assert len(keep) == 5
        # The one kept from the busy day is that day's NEWEST.
        busy = [ts for _, ts, _ in keep if ts.day == 29]
        assert len(busy) == 1 and busy[0].hour == 15
        # Every earlier day survives — the old policy would have deleted them all.
        assert {d.day for d in kept_dates} == {25, 26, 27, 28, 29}
        assert len(delete) == 15
        assert all(ts.day == 29 for _, ts, _ in delete)

    def test_drops_dates_beyond_the_window(self):
        items = [self._item(d, 22) for d in range(1, 15)]   # 14 distinct dates
        keep, delete = maint_db._plan(items)
        assert len(keep) == maint_db.KEEP_NEWEST
        assert len(delete) == 14 - maint_db.KEEP_NEWEST
        # Kept dates are the most recent ones.
        assert min(ts.day for _, ts, _ in keep) > max(ts.day for _, ts, _ in delete)

    def test_nothing_deleted_when_within_window(self):
        items = [self._item(d, 22) for d in (20, 21, 22)]
        keep, delete = maint_db._plan(items)
        assert len(keep) == 3 and delete == []

    def test_empty_input(self):
        assert maint_db._plan([]) == ([], [])

    def test_small_collection_is_never_pruned(self):
        """Date retention is stricter than count retention on small sets.

        Five local backups spread over two days would otherwise drop to two —
        deleting real history to reclaim nothing. Within budget, keep all.
        """
        items = [self._item(9, h) for h in (1, 2, 3)] + [self._item(16, h) for h in (1, 2)]
        keep, delete = maint_db._plan(items)
        assert len(keep) == 5 and delete == []
