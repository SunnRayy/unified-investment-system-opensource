"""Tests for C5 data-source management v2 backend.

Coverage:
 1. GET /sources payload: new fields (label, authority, format, can_fetch, last_update) for ibkr and schwab
 2. POST /sources/fetch/ibkr: happy path (fake fetch, event recorded, retention applied, mark_dirty called)
 3. POST /sources/fetch/schwab: 400 (no fetcher)
 4. POST /sources/fetch/ibkr: FlexFetchError → 502
 5. POST /sources/fetch/ibkr: GCS push fail → 503, file rolled back
 6. GET /sources/events: upload + fetch rows returned newest-first with correct origin
 7. GET /sources/events/{reader}: filtered by reader
 8. Retention (local): prune_source_files leaves newest 3, newest preserved
 9. Retention (GCS): prune_source_blobs — mock list/delete, keep-3
10. origin migration: _ensure_upload_history_table idempotent on existing table without origin column
"""
from __future__ import annotations

import io
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from src.api.main import app
    return TestClient(app)


@pytest.fixture
def source_dir(tmp_path):
    d = tmp_path / "source_data"
    d.mkdir()
    return d


@pytest.fixture
def tmp_db_dir(tmp_path):
    """Set up a DB dir with a fresh DuckDB at the expected path."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return tmp_path


def _mock_settings_for(source_dir: Path, reader: str = "ibkr") -> dict:
    if reader == "ibkr":
        pattern = "IBKR_UIS_Report*.csv"
    elif reader == "schwab":
        pattern = "Individual-Positions-*.csv"
    else:
        pattern = "*.xlsx"
    return {
        "source_registry": {
            reader: {
                "data_dir": str(source_dir),
                "file_patterns": {"main": pattern},
                "enabled": True,
                "reader": f"{reader}_reader",
                "asset_prefixes": [],
            }
        },
        "finance_dir": str(source_dir),
    }


def _setup_db(tmp_db_dir: Path) -> Path:
    """Create and return path to test DuckDB with history table (no origin column — pre-C5)."""
    db_path = tmp_db_dir / "data" / "unified.duckdb"
    return db_path


def _settings_yaml_path(tmp_db_dir: Path) -> Path:
    config_dir = tmp_db_dir / "config"
    config_dir.mkdir(exist_ok=True)
    return config_dir / "settings.yaml"


# ---------------------------------------------------------------------------
# 1. GET /sources — new fields
# ---------------------------------------------------------------------------


class TestGetSourcesNewFields:
    """Verify C5 additions to GET /settings/sources payload."""

    def test_ibkr_fields(self, client):
        """ibkr: can_fetch=True, authority=co-authority, format=flex_csv, label non-empty."""
        resp = client.get("/settings/sources")
        assert resp.status_code == 200
        data = resp.json()
        sources = {s["key"]: s for s in data["sources"]}

        assert "ibkr" in sources, "ibkr not in sources"
        ibkr = sources["ibkr"]
        assert ibkr["can_fetch"] is True, "ibkr.can_fetch should be True"
        assert ibkr["authority"] == "co-authority", f"ibkr.authority={ibkr['authority']!r}"
        assert ibkr["format"] == "flex_csv", f"ibkr.format={ibkr['format']!r}"
        assert ibkr["label"], "ibkr.label should be non-empty"
        assert ibkr["authority_note"] is not None, "ibkr.authority_note should be set for co-authority"
        # last_update may be None or dict
        assert "last_update" in ibkr

    def test_schwab_fields(self, client):
        """schwab: can_fetch=False, authority=co-authority, format=csv."""
        resp = client.get("/settings/sources")
        assert resp.status_code == 200
        data = resp.json()
        sources = {s["key"]: s for s in data["sources"]}

        assert "schwab" in sources
        schwab = sources["schwab"]
        assert schwab["can_fetch"] is False, "schwab.can_fetch should be False"
        assert schwab["authority"] == "co-authority"
        assert schwab["format"] == "csv"
        assert schwab["label"], "schwab.label should be non-empty"

    def test_all_sources_have_new_fields(self, client):
        """All 7 sources return label, authority, format, can_fetch, last_update keys."""
        resp = client.get("/settings/sources")
        assert resp.status_code == 200
        data = resp.json()
        required = {"label", "authority", "format", "can_fetch", "last_update"}
        for s in data["sources"]:
            missing = required - set(s.keys())
            assert not missing, f"Source {s['key']} missing fields: {missing}"

    def test_excel_readers_have_xlsx_format(self, client):
        """Readers with format=excel in YAML expose format=xlsx in the API."""
        resp = client.get("/settings/sources")
        assert resp.status_code == 200
        data = resp.json()
        sources = {s["key"]: s for s in data["sources"]}
        for key in ("cn_fund", "gold", "insurance", "rsu", "financial_summary"):
            if key in sources:
                assert sources[key]["format"] == "xlsx", (
                    f"{key}.format should be xlsx, got {sources[key]['format']!r}"
                )


# ---------------------------------------------------------------------------
# 2. POST /sources/fetch/ibkr — happy path
# ---------------------------------------------------------------------------


class TestFetchEndpointHappyPath:
    """POST /sources/fetch/ibkr with a monkeypatched fetcher."""

    def test_fetch_ibkr_happy_path(self, client, source_dir, tmp_db_dir):
        """Fake fetch: event recorded, retention called, mark_dirty called."""
        settings_path = _settings_yaml_path(tmp_db_dir)
        mock_settings = _mock_settings_for(source_dir, "ibkr")
        fake_file = source_dir / "IBKR_UIS_Report_20260617T120000Z.csv"
        fake_file.write_text("BrokerageAccount,Conid,Description\nDU123,265598,AAPL\n")

        def fake_ibkr_fetch(data_dir: Path) -> Path:
            return fake_file

        pruned_local: list[str] = []

        with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
            with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_path):
                with patch("src.fetchers.registry.FETCHERS", {"ibkr": fake_ibkr_fetch}):
                    with patch("src.api.routes.settings.prune_source_files", return_value=pruned_local) as mock_local_prune:
                        with patch("src.api.routes.settings.mark_dirty") as mock_mark_dirty:
                            resp = client.post("/settings/sources/fetch/ibkr")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["reader"] == "ibkr"
        assert data["file_path"] == str(fake_file)
        assert data["file_size_bytes"] > 0
        assert data["line_count"] > 0
        assert "fetched_at" in data
        assert isinstance(data["pruned"], list)

        # mark_dirty was called
        mock_mark_dirty.assert_called_once()
        # prune_source_files was called
        mock_local_prune.assert_called_once()

    def test_fetch_ibkr_records_fetch_event(self, client, source_dir, tmp_db_dir):
        """After fetch, source_upload_history has a row with origin='fetch'."""
        settings_path = _settings_yaml_path(tmp_db_dir)
        mock_settings = _mock_settings_for(source_dir, "ibkr")
        fake_file = source_dir / "IBKR_UIS_Report_20260617T120000Z.csv"
        fake_file.write_text("header\nrow1\n")

        def fake_ibkr_fetch(data_dir: Path) -> Path:
            return fake_file

        with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
            with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_path):
                with patch("src.fetchers.registry.FETCHERS", {"ibkr": fake_ibkr_fetch}):
                    with patch("src.api.routes.settings.prune_source_files", return_value=[]):
                        with patch("src.api.routes.settings.mark_dirty"):
                            resp = client.post("/settings/sources/fetch/ibkr")

        assert resp.status_code == 200

        db_path = tmp_db_dir / "data" / "unified.duckdb"
        with duckdb.connect(str(db_path), read_only=True) as conn:
            rows = conn.execute(
                "SELECT origin, reader FROM source_upload_history WHERE reader='ibkr' AND origin='fetch'"
            ).fetchall()
        assert len(rows) >= 1, "Expected a fetch event row in source_upload_history"
        assert rows[0][0] == "fetch"


# ---------------------------------------------------------------------------
# 3. POST /sources/fetch/schwab — 400 (no fetcher)
# ---------------------------------------------------------------------------


class TestFetchEndpointNoFetcher:
    def test_fetch_schwab_returns_400(self, client):
        """schwab has no registered fetcher → 400."""
        resp = client.post("/settings/sources/fetch/schwab")
        assert resp.status_code == 400
        assert "can_fetch" in resp.json().get("detail", "").lower() or "fetcher" in resp.json().get("detail", "").lower()

    def test_fetch_unknown_reader_returns_400(self, client):
        """Unknown reader → 400."""
        resp = client.post("/settings/sources/fetch/nonexistent_reader_xyz")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 4. POST /sources/fetch/ibkr — FlexFetchError → 502
# ---------------------------------------------------------------------------


class TestFetchFlexError:
    def test_flex_fetch_error_returns_502(self, client, source_dir, tmp_db_dir):
        """FlexFetchError raised by the fetcher → HTTP 502."""
        from src.fetchers.ibkr_flex import FlexFetchError

        settings_path = _settings_yaml_path(tmp_db_dir)
        mock_settings = _mock_settings_for(source_dir, "ibkr")

        def failing_fetch(data_dir: Path) -> Path:
            raise FlexFetchError("Flex API error [1012]: Invalid token", code="1012")

        with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
            with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_path):
                with patch("src.fetchers.registry.FETCHERS", {"ibkr": failing_fetch}):
                    resp = client.post("/settings/sources/fetch/ibkr")

        assert resp.status_code == 502, f"Expected 502, got {resp.status_code}: {resp.text}"
        assert "Flex" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# 5. POST /sources/fetch/ibkr — GCS push failure → 503 + rollback
# ---------------------------------------------------------------------------


class TestFetchGCSFailure:
    def test_gcs_push_failure_returns_503_and_rolls_back(self, client, source_dir, tmp_db_dir):
        """If GCS upload fails after fetch, return 503 and delete the new file."""
        settings_path = _settings_yaml_path(tmp_db_dir)
        mock_settings = _mock_settings_for(source_dir, "ibkr")
        fake_file = source_dir / "IBKR_UIS_Report_20260617T130000Z.csv"
        fake_file.write_text("header\nrow1\n")

        def fake_ibkr_fetch(data_dir: Path) -> Path:
            return fake_file

        with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
            with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_path):
                with patch("src.fetchers.registry.FETCHERS", {"ibkr": fake_ibkr_fetch}):
                    with patch.dict(os.environ, {"UIS_GCS_BUCKET": "test-bucket"}):
                        with patch(
                            "src.api.routes.settings.upload_source_to_gcs",
                            side_effect=Exception("GCS network error")
                        ):
                            resp = client.post("/settings/sources/fetch/ibkr")

        assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
        # File should have been rolled back (deleted)
        assert not fake_file.exists(), "Fetched file should be deleted on GCS rollback"


# ---------------------------------------------------------------------------
# 6. GET /sources/events — upload + fetch both appear
# ---------------------------------------------------------------------------


class TestSourceEventsEndpoint:
    def _insert_events(self, db_path: Path) -> None:
        """Insert one upload and one fetch event into source_upload_history."""
        with duckdb.connect(str(db_path)) as conn:
            from src.api.routes.settings import _ensure_upload_history_table
            _ensure_upload_history_table(conn)
            # Insert fetch event (newer)
            conn.execute(
                "INSERT INTO source_upload_history "
                "(reader, filename, file_size_bytes, uploaded_at, is_valid, warnings, previous_filename, origin) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ["ibkr", "IBKR_UIS_Report_20260617T120000Z.csv", 1024,
                 datetime(2026, 6, 17, 12, 0, 0), True, "[]", None, "fetch"]
            )
            # Insert upload event (older)
            conn.execute(
                "INSERT INTO source_upload_history "
                "(reader, filename, file_size_bytes, uploaded_at, is_valid, warnings, previous_filename, origin) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ["schwab", "Schwab-2026-06-16.csv", 512,
                 datetime(2026, 6, 16, 10, 0, 0), True, "[]", None, "upload"]
            )

    def test_events_returns_both_origins(self, client, tmp_db_dir):
        """GET /sources/events returns upload and fetch rows with correct origin."""
        settings_path = _settings_yaml_path(tmp_db_dir)
        db_path = tmp_db_dir / "data" / "unified.duckdb"
        self._insert_events(db_path)

        with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_path):
            resp = client.get("/settings/sources/events")

        assert resp.status_code == 200
        data = resp.json()
        assert data["reader"] is None
        assert data["total_count"] == 2
        origins = {e["origin"] for e in data["events"]}
        assert "upload" in origins, "Should have an upload event"
        assert "fetch" in origins, "Should have a fetch event"

    def test_events_newest_first(self, client, tmp_db_dir):
        """Events are returned newest first."""
        settings_path = _settings_yaml_path(tmp_db_dir)
        db_path = tmp_db_dir / "data" / "unified.duckdb"
        self._insert_events(db_path)

        with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_path):
            resp = client.get("/settings/sources/events")

        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 2
        # Fetch event (2026-06-17) should come before upload event (2026-06-16)
        assert events[0]["origin"] == "fetch"
        assert events[1]["origin"] == "upload"

    def test_events_per_reader(self, client, tmp_db_dir):
        """GET /sources/events/{reader} filters by reader."""
        settings_path = _settings_yaml_path(tmp_db_dir)
        db_path = tmp_db_dir / "data" / "unified.duckdb"
        self._insert_events(db_path)

        with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_path):
            resp = client.get("/settings/sources/events/ibkr")

        assert resp.status_code == 200
        data = resp.json()
        assert data["reader"] == "ibkr"
        assert data["total_count"] == 1
        assert data["events"][0]["reader"] == "ibkr"
        assert data["events"][0]["origin"] == "fetch"

    def test_events_unknown_reader_400(self, client):
        """GET /sources/events/{reader} with unknown reader → 400."""
        resp = client.get("/settings/sources/events/nonexistent_xyz")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 7. Local retention: prune_source_files
# ---------------------------------------------------------------------------


class TestPruneSourceFiles:
    def test_keeps_newest_3_deletes_older(self, tmp_path):
        """Create 5 files, prune → 3 remain (newest 3), 2 deleted."""
        from src.api.routes.settings import prune_source_files

        source_dir = tmp_path / "ibkr_data"
        source_dir.mkdir()

        # Create 5 files with distinct mtimes (oldest first)
        files = []
        for i in range(5):
            f = source_dir / f"IBKR_UIS_Report_2026061{i}T120000Z.csv"
            f.write_text(f"row {i}\n")
            # Stagger mtimes: file i → mtime base + i seconds
            t = 1_750_000_000.0 + i * 10
            os.utime(str(f), (t, t))
            files.append(f)

        mock_settings = {
            "source_registry": {
                "ibkr": {
                    "data_dir": str(source_dir),
                    "file_patterns": {"main": "IBKR_UIS_Report*.csv"},
                    "enabled": True,
                    "reader": "ibkr_reader",
                    "asset_prefixes": [],
                }
            }
        }

        with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
            deleted = prune_source_files("ibkr", str(source_dir), keep=3)

        remaining = list(source_dir.glob("IBKR_UIS_Report*.csv"))
        assert len(remaining) == 3, f"Expected 3 remaining, got {len(remaining)}: {remaining}"
        assert len(deleted) == 2, f"Expected 2 deleted, got {deleted}"

        # Newest file (files[4], highest mtime) must still exist
        newest = files[-1]  # last created = highest mtime
        assert newest.exists(), f"Newest file {newest.name} was deleted — must never happen"

    def test_does_not_delete_if_lte_keep(self, tmp_path):
        """If number of files ≤ keep, nothing is deleted."""
        from src.api.routes.settings import prune_source_files

        source_dir = tmp_path / "ibkr_data"
        source_dir.mkdir()

        for i in range(3):
            f = source_dir / f"IBKR_UIS_Report_2026061{i}T120000Z.csv"
            f.write_text("row\n")

        mock_settings = {
            "source_registry": {
                "ibkr": {
                    "data_dir": str(source_dir),
                    "file_patterns": {"main": "IBKR_UIS_Report*.csv"},
                    "enabled": True,
                    "reader": "ibkr_reader",
                    "asset_prefixes": [],
                }
            }
        }

        with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
            deleted = prune_source_files("ibkr", str(source_dir), keep=3)

        assert deleted == []

    def test_newest_file_always_preserved(self, tmp_path):
        """The single newest file is never deleted even when keep=1."""
        from src.api.routes.settings import prune_source_files

        source_dir = tmp_path / "ibkr_data"
        source_dir.mkdir()

        newest = source_dir / "IBKR_UIS_Report_20260617T999999Z.csv"
        for i in range(4):
            f = source_dir / f"IBKR_UIS_Report_2026060{i}T120000Z.csv"
            f.write_text("row\n")
            t = 1_749_000_000.0 + i
            os.utime(str(f), (t, t))

        newest.write_text("newest row\n")
        t_newest = 1_750_000_000.0
        os.utime(str(newest), (t_newest, t_newest))

        mock_settings = {
            "source_registry": {
                "ibkr": {
                    "data_dir": str(source_dir),
                    "file_patterns": {"main": "IBKR_UIS_Report*.csv"},
                    "enabled": True,
                    "reader": "ibkr_reader",
                    "asset_prefixes": [],
                }
            }
        }

        with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
            deleted = prune_source_files("ibkr", str(source_dir), keep=1)

        assert newest.exists(), "Newest file must never be deleted"
        assert newest.name not in deleted


# ---------------------------------------------------------------------------
# 8. GCS retention: prune_source_blobs
# ---------------------------------------------------------------------------


class TestPruneSourceBlobs:
    def _make_blob(self, name: str, updated) -> MagicMock:
        blob = MagicMock()
        blob.name = name
        blob.updated = updated
        return blob

    def test_keeps_newest_3_deletes_older(self):
        """5 blobs → 3 kept (newest), 2 deleted."""
        from src.storage.gcs import prune_source_blobs
        from datetime import timezone

        base_dt = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
        blobs = [
            self._make_blob(f"sources/ibkr/IBKR_UIS_Report_0{i}.csv", base_dt + timedelta(hours=i))
            for i in range(5)
        ]

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = blobs
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        with patch("src.storage.gcs._get_client", return_value=mock_client):
            deleted = prune_source_blobs("test-bucket", "ibkr", keep=3)

        assert len(deleted) == 2
        # Ensure the 2 oldest were deleted (index 0 and 1 when sorted oldest first)
        # After sorting newest first: blobs[4], blobs[3], blobs[2] are kept; blobs[1], blobs[0] deleted
        deleted_names = set(deleted)
        assert "sources/ibkr/IBKR_UIS_Report_00.csv" in deleted_names
        assert "sources/ibkr/IBKR_UIS_Report_01.csv" in deleted_names
        # Newest never deleted
        assert "sources/ibkr/IBKR_UIS_Report_04.csv" not in deleted_names

    def test_does_not_delete_if_lte_keep(self):
        """3 blobs, keep=3 → nothing deleted."""
        from src.storage.gcs import prune_source_blobs
        from datetime import timezone

        base_dt = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
        blobs = [
            self._make_blob(f"sources/ibkr/IBKR_{i}.csv", base_dt + timedelta(hours=i))
            for i in range(3)
        ]

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = blobs
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        with patch("src.storage.gcs._get_client", return_value=mock_client):
            deleted = prune_source_blobs("test-bucket", "ibkr", keep=3)

        assert deleted == []
        mock_bucket.delete_blob.assert_not_called()

    def test_newest_blob_never_deleted(self):
        """The newest blob (index 0 after sort) is never deleted even with keep=1."""
        from src.storage.gcs import prune_source_blobs
        from datetime import timezone

        base_dt = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
        blobs = [
            self._make_blob(f"sources/ibkr/IBKR_{i}.csv", base_dt + timedelta(hours=i))
            for i in range(4)
        ]

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = blobs
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        with patch("src.storage.gcs._get_client", return_value=mock_client):
            deleted = prune_source_blobs("test-bucket", "ibkr", keep=1)

        # Newest blob is blobs[3] (index 3, largest timedelta)
        assert "sources/ibkr/IBKR_3.csv" not in deleted
        assert len(deleted) == 3  # 4 - keep=1 = 3 deleted


# ---------------------------------------------------------------------------
# 9. origin migration: _ensure_upload_history_table idempotent
# ---------------------------------------------------------------------------


class TestOriginMigration:
    def test_ensure_table_adds_origin_to_existing_table(self, tmp_path):
        """_ensure_upload_history_table: idempotent when called on pre-C5 table (no origin col)."""
        from src.api.routes.settings import _ensure_upload_history_table

        db_path = tmp_path / "test.duckdb"
        # Simulate pre-C5 table (no origin column)
        with duckdb.connect(str(db_path)) as conn:
            conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_source_upload_history_id START 1")
            conn.execute("""
                CREATE TABLE source_upload_history (
                    id INTEGER PRIMARY KEY DEFAULT nextval('seq_source_upload_history_id'),
                    reader VARCHAR NOT NULL,
                    filename VARCHAR NOT NULL,
                    file_size_bytes BIGINT,
                    uploaded_at TIMESTAMP NOT NULL,
                    is_valid BOOLEAN,
                    warnings JSON,
                    previous_filename VARCHAR
                )
            """)
            # Insert pre-existing row
            conn.execute(
                "INSERT INTO source_upload_history "
                "(reader, filename, file_size_bytes, uploaded_at, is_valid, warnings, previous_filename) "
                "VALUES ('schwab', 'old.csv', 100, '2026-01-01', true, '[]', NULL)"
            )

        # Run _ensure_upload_history_table — should add origin column
        with duckdb.connect(str(db_path)) as conn:
            _ensure_upload_history_table(conn)
            cols = conn.execute("PRAGMA table_info('source_upload_history')").fetchall()
            col_names = {row[1] for row in cols}
            assert "origin" in col_names, "origin column should have been added"

            # Pre-existing row should default to 'upload'
            rows = conn.execute("SELECT origin FROM source_upload_history").fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "upload", f"Pre-existing row should default to 'upload', got {rows[0][0]!r}"

    def test_ensure_table_idempotent_when_called_twice(self, tmp_path):
        """_ensure_upload_history_table: safe to call twice on a fresh table."""
        from src.api.routes.settings import _ensure_upload_history_table

        db_path = tmp_path / "test2.duckdb"
        with duckdb.connect(str(db_path)) as conn:
            _ensure_upload_history_table(conn)
            _ensure_upload_history_table(conn)  # second call — should not raise
            cols = conn.execute("PRAGMA table_info('source_upload_history')").fetchall()
            col_names = {row[1] for row in cols}
            assert "origin" in col_names


# ---------------------------------------------------------------------------
# 10. Upload endpoint: sets origin='upload'
# ---------------------------------------------------------------------------


class TestUploadSetsOrigin:
    def test_upload_records_origin_upload(self, client, source_dir, tmp_db_dir):
        """The upload endpoint inserts a row with origin='upload'."""
        settings_path = _settings_yaml_path(tmp_db_dir)
        mock_settings = _mock_settings_for(source_dir, "schwab")
        # Adjust the pattern to match the filename we'll upload
        mock_settings["source_registry"]["schwab"]["file_patterns"]["main"] = "*.csv"

        with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
            with patch("src.api.routes.settings._validate_file_at_path", return_value=(True, [], "csv")):
                with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_path):
                    with patch("src.api.routes.settings.prune_source_files", return_value=[]):
                        resp = client.post(
                            "/settings/sources/upload/schwab",
                            files={"file": ("Schwab-2026-06-17.csv", io.BytesIO(b"h,d\n1,2"), "text/csv")},
                        )

        assert resp.status_code == 200

        db_path = tmp_db_dir / "data" / "unified.duckdb"
        with duckdb.connect(str(db_path), read_only=True) as conn:
            rows = conn.execute(
                "SELECT origin FROM source_upload_history WHERE reader='schwab'"
            ).fetchall()
        assert len(rows) >= 1
        assert rows[0][0] == "upload", f"Expected origin='upload', got {rows[0][0]!r}"
