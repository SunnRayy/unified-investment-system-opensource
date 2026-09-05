"""Tests for upload history: backup logic, history recording, and GET history endpoints."""
import io
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from src.api.main import app
    return TestClient(app)


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a fresh in-memory-style DuckDB path in tmp_path."""
    db_path = tmp_path / "test_upload_history.duckdb"
    return db_path


@pytest.fixture
def source_dir(tmp_path):
    """A temporary directory to act as the source reader data directory."""
    d = tmp_path / "source_data"
    d.mkdir()
    return d


def _mock_settings(source_dir: Path, reader: str = "schwab"):
    ext = "*.csv" if reader == "schwab" else "*.xlsx"
    return {
        "source_registry": {
            reader: {
                "data_dir": str(source_dir),
                "file_patterns": {"main": ext},
                "enabled": True,
                "reader": f"{reader}_reader",
                "asset_prefixes": [],
            }
        },
        "sources": {"pis": {}},
        "subsystems": {},
    }


def _make_upload(client, source_dir, reader="schwab", filename="Schwab-2024-01-01.csv", content=b"header,data\n1,2"):
    """Helper to POST an upload and return the response."""
    mock_settings = _mock_settings(source_dir, reader)

    def mock_validate(*args, **kwargs):
        return True, [], "csv"

    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        with patch("src.api.routes.settings._validate_file_at_path", return_value=(True, [], "csv")):
            response = client.post(
                f"/settings/sources/upload/{reader}",
                files={"file": (filename, io.BytesIO(content), "text/csv")},
            )
    return response


# ------------------------------------------------------------------
# Test 1: upload creates a history row
# ------------------------------------------------------------------

def test_upload_creates_history_row(client, source_dir, tmp_db):
    """Upload a file, verify a row appears in GET upload-history endpoint."""
    mock_settings = _mock_settings(source_dir)

    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        with patch("src.api.routes.settings._validate_file_at_path", return_value=(True, [], "csv")):
            with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH",
                       tmp_db.parent / "config" / "settings.yaml"):
                # Ensure the data dir exists at db_path resolution
                (tmp_db.parent / "data").mkdir(parents=True, exist_ok=True)
                response = client.post(
                    "/settings/sources/upload/schwab",
                    files={"file": ("Schwab-2024-01-01.csv", io.BytesIO(b"h,d\n1,2"), "text/csv")},
                )

    assert response.status_code == 200
    data = response.json()
    assert data["reader"] == "schwab"
    assert data["is_valid"] is True

    # Verify row is in the DuckDB table
    actual_db = tmp_db.parent / "data" / "unified.duckdb"
    with duckdb.connect(str(actual_db), read_only=True) as conn:
        rows = conn.execute(
            "SELECT reader, filename FROM source_upload_history WHERE reader='schwab'"
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0][0] == "schwab"
        assert rows[0][1] == "Schwab-2024-01-01.csv"


# ------------------------------------------------------------------
# Test 2: upload overwrite creates a .bak. backup
# ------------------------------------------------------------------

def test_upload_overwrite_creates_backup(client, source_dir, tmp_db):
    """Upload same filename twice — second upload creates a .bak. file."""
    filename = "Schwab-2024-01-01.csv"
    dest = source_dir / filename
    dest.write_bytes(b"original content")

    mock_settings = _mock_settings(source_dir)

    # Patch DB path to avoid touching production DB
    settings_yaml_path = tmp_db.parent / "config" / "settings.yaml"
    (tmp_db.parent / "data").mkdir(parents=True, exist_ok=True)

    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        with patch("src.api.routes.settings._validate_file_at_path", return_value=(True, [], "csv")):
            with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_yaml_path):
                response = client.post(
                    "/settings/sources/upload/schwab",
                    files={"file": (filename, io.BytesIO(b"new content"), "text/csv")},
                )

    assert response.status_code == 200

    # Verify a .bak. file was created
    bak_files = list(source_dir.glob(f"{filename}.bak.*"))
    assert len(bak_files) == 1, f"Expected 1 backup file, found: {bak_files}"
    # Verify backup contains original content
    assert bak_files[0].read_bytes() == b"original content"
    # Verify destination has new content
    assert dest.read_bytes() == b"new content"


# ------------------------------------------------------------------
# Test 3: backup failure is non-fatal
# ------------------------------------------------------------------

def test_backup_failure_is_nonfatal(client, source_dir, tmp_db):
    """OSError during backup → upload still succeeds; warning in result."""
    filename = "Schwab-2024-01-01.csv"
    dest = source_dir / filename
    dest.write_bytes(b"original content")

    mock_settings = _mock_settings(source_dir)
    settings_yaml_path = tmp_db.parent / "config" / "settings.yaml"
    (tmp_db.parent / "data").mkdir(parents=True, exist_ok=True)

    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        with patch("src.api.routes.settings._validate_file_at_path", return_value=(True, [], "csv")):
            with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_yaml_path):
                with patch("src.api.routes.settings.shutil.copy2", side_effect=OSError("disk full")):
                    response = client.post(
                        "/settings/sources/upload/schwab",
                        files={"file": (filename, io.BytesIO(b"new content"), "text/csv")},
                    )

    assert response.status_code == 200
    data = response.json()
    # Upload succeeded despite backup failure
    assert data["reader"] == "schwab"
    # The file was written (destination has new content)
    assert dest.read_bytes() == b"new content"
    # Warning about backup failure appears
    assert any("Backup failed" in w for w in data["warnings"])


# ------------------------------------------------------------------
# Test 4: GET reader history when table doesn't exist → empty list, not 500
# ------------------------------------------------------------------

def test_get_reader_history_empty_table(client, tmp_db):
    """GET /sources/upload-history/{reader} when table doesn't exist returns empty list."""
    # Create a fresh DB without the history table
    (tmp_db.parent / "data").mkdir(parents=True, exist_ok=True)
    fresh_db = tmp_db.parent / "data" / "unified.duckdb"
    with duckdb.connect(str(fresh_db)) as conn:
        conn.execute("CREATE TABLE some_other_table (id INTEGER)")

    settings_yaml_path = tmp_db.parent / "config" / "settings.yaml"
    with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_yaml_path):
        response = client.get("/settings/sources/upload-history/schwab")

    assert response.status_code == 200
    data = response.json()
    assert data["reader"] == "schwab"
    assert data["entries"] == []
    assert data["total_count"] == 0


# ------------------------------------------------------------------
# Test 5: GET reader history returns entries after upload
# ------------------------------------------------------------------

def test_get_reader_history_returns_entries(client, source_dir, tmp_db):
    """After upload, GET /sources/upload-history/{reader} returns the entry."""
    filename = "Schwab-2024-02-01.csv"
    mock_settings = _mock_settings(source_dir)
    settings_yaml_path = tmp_db.parent / "config" / "settings.yaml"
    (tmp_db.parent / "data").mkdir(parents=True, exist_ok=True)

    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        with patch("src.api.routes.settings._validate_file_at_path", return_value=(True, [], "csv")):
            with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_yaml_path):
                upload_resp = client.post(
                    "/settings/sources/upload/schwab",
                    files={"file": (filename, io.BytesIO(b"h,d\n1,2"), "text/csv")},
                )
    assert upload_resp.status_code == 200

    with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_yaml_path):
        history_resp = client.get("/settings/sources/upload-history/schwab")

    assert history_resp.status_code == 200
    data = history_resp.json()
    assert data["reader"] == "schwab"
    assert data["total_count"] >= 1
    filenames = [e["filename"] for e in data["entries"]]
    assert filename in filenames


# ------------------------------------------------------------------
# Test 6: GET reader history for unknown reader → 404
# ------------------------------------------------------------------

def test_get_reader_history_unknown_reader(client):
    """GET /sources/upload-history/{reader} with unknown reader returns 404."""
    response = client.get("/settings/sources/upload-history/nonexistent_reader")
    assert response.status_code == 404
    assert "nonexistent_reader" in response.json()["detail"]


# ------------------------------------------------------------------
# Test 7: GET all history returns entries from multiple readers
# ------------------------------------------------------------------

def test_get_all_history_returns_all_readers(client, tmp_path):
    """GET /sources/upload-history returns entries from multiple readers."""
    # Directly insert rows into a temp DB
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "data" / "unified.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_source_upload_history_id START 1")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS source_upload_history (
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
        conn.execute(
            "INSERT INTO source_upload_history (reader, filename, file_size_bytes, uploaded_at, is_valid, warnings, previous_filename) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["schwab", "Schwab.csv", 1024, datetime.now(), True, "[]", None]
        )
        conn.execute(
            "INSERT INTO source_upload_history (reader, filename, file_size_bytes, uploaded_at, is_valid, warnings, previous_filename) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["cn_fund", "Fund.xlsx", 2048, datetime.now(), True, "[]", None]
        )

    settings_yaml_path = tmp_path / "config" / "settings.yaml"
    with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_yaml_path):
        response = client.get("/settings/sources/upload-history")

    assert response.status_code == 200
    data = response.json()
    assert data["reader"] is None
    assert data["total_count"] == 2
    readers_in_results = {e["reader"] for e in data["entries"]}
    assert "schwab" in readers_in_results
    assert "cn_fund" in readers_in_results


# ------------------------------------------------------------------
# Test 8: limit parameter is respected
# ------------------------------------------------------------------

def test_history_limit_param(client, tmp_path):
    """limit=2 returns at most 2 entries even if more exist."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "data" / "unified.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_source_upload_history_id START 1")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS source_upload_history (
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
        for i in range(5):
            conn.execute(
                "INSERT INTO source_upload_history (reader, filename, file_size_bytes, uploaded_at, is_valid, warnings, previous_filename) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ["schwab", f"Schwab-{i}.csv", 100 * i, datetime.now(), True, "[]", None]
            )

    settings_yaml_path = tmp_path / "config" / "settings.yaml"
    with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_yaml_path):
        response = client.get("/settings/sources/upload-history/schwab?limit=2")

    assert response.status_code == 200
    data = response.json()
    # The endpoint returns entries up to the limit
    assert len(data["entries"]) == 2
    # total_count reflects the actual returned count (not total in DB)
    assert data["total_count"] == len(data["entries"])


@pytest.fixture
def settings_dir(tmp_path):
    """A temporary directory to act as the source reader data directory (alias for source_dir)."""
    d = tmp_path / "source_data"
    d.mkdir()
    return d


# ------------------------------------------------------------------
# Test 9: Invalid limit returns 422
# ------------------------------------------------------------------

def test_get_reader_history_invalid_limit(client, settings_dir):
    """Negative and zero limit values should return 422 (FastAPI validation)."""
    for bad_limit in [0, -1, -100]:
        resp = client.get(f"/settings/sources/upload-history/schwab?limit={bad_limit}")
        assert resp.status_code == 422, f"Expected 422 for limit={bad_limit}, got {resp.status_code}"


def test_get_all_history_invalid_limit(client):
    """Negative and zero limit values should return 422 for the all-readers endpoint."""
    for bad_limit in [0, -1, -100]:
        resp = client.get(f"/settings/sources/upload-history?limit={bad_limit}")
        assert resp.status_code == 422, f"Expected 422 for limit={bad_limit}, got {resp.status_code}"


# ------------------------------------------------------------------
# Test 10: Non-fatal DB insert failure — upload still succeeds
# ------------------------------------------------------------------

def test_history_insert_failure_is_nonfatal(client, settings_dir, tmp_path):
    """If the history DB insert fails, the upload should still succeed."""
    mock_settings = _mock_settings(settings_dir, reader="schwab")
    content = b"some,csv,content\n1,2,3\n"

    settings_yaml_path = tmp_path / "config" / "settings.yaml"
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        with patch("src.api.routes.settings._validate_file_at_path", return_value=(True, [], "csv")):
            with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_yaml_path):
                with patch("src.api.routes.settings._ensure_upload_history_table", side_effect=Exception("DB failure")):
                    resp = client.post(
                        "/settings/sources/upload/schwab",
                        files={"file": ("test.csv", io.BytesIO(content), "text/csv")},
                    )

    # Upload should succeed despite history insert failure
    assert resp.status_code == 200
    data = resp.json()
    assert data["reader"] == "schwab"
