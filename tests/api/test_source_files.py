"""Tests for GET /settings/sources/files/{reader} endpoint."""
import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch


@pytest.fixture
def client():
    from src.api.main import app
    return TestClient(app)


def test_get_source_files_unknown_reader(client):
    """Returns 404 for unknown reader."""
    response = client.get("/settings/sources/files/unknown_reader")
    assert response.status_code == 404


def test_get_source_files_empty_when_no_dir(client):
    """Returns empty files list when resolved_dir is empty."""
    mock_settings = {"source_registry": {}, "sources": {"pis": {}}, "subsystems": {}}
    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        response = client.get("/settings/sources/files/schwab")
    assert response.status_code == 200
    data = response.json()
    assert data["files"] == []
    assert data["total_count"] == 0


def test_get_source_files_lists_matching_files(client, tmp_path):
    """Returns files matching reader's allowed extensions, sorted by mtime desc."""
    # Create test files
    csv1 = tmp_path / "Schwab-2024-01-01.csv"
    csv2 = tmp_path / "Schwab-2024-02-01.csv"
    csv1.write_text("data")
    csv2.write_text("data")
    # Set different mtimes
    os.utime(csv1, (1000, 1000))
    os.utime(csv2, (2000, 2000))

    mock_settings = {
        "source_registry": {"schwab": {"data_dir": str(tmp_path), "file_patterns": {"main": "Schwab-*.csv"}, "enabled": True, "reader": "schwab_reader", "asset_prefixes": []}},
        "sources": {"pis": {}},
        "subsystems": {},
    }
    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        response = client.get("/settings/sources/files/schwab")

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 2
    # Should be sorted by mtime desc — csv2 first
    assert data["files"][0]["filename"] == "Schwab-2024-02-01.csv"
    assert data["files"][1]["filename"] == "Schwab-2024-01-01.csv"


def test_get_source_files_excludes_wrong_extensions(client, tmp_path):
    """Excludes files with non-matching extensions."""
    (tmp_path / "file.csv").write_text("data")
    (tmp_path / "file.txt").write_text("data")
    (tmp_path / "file.xlsx").write_text("data")

    mock_settings = {
        "source_registry": {"schwab": {"data_dir": str(tmp_path), "file_patterns": {"main": "*.csv"}, "enabled": True, "reader": "schwab_reader", "asset_prefixes": []}},
        "sources": {"pis": {}},
        "subsystems": {},
    }
    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        response = client.get("/settings/sources/files/schwab")

    data = response.json()
    filenames = [f["filename"] for f in data["files"]]
    assert "file.csv" in filenames
    assert "file.txt" not in filenames
    assert "file.xlsx" not in filenames  # schwab only allows .csv


def test_get_source_files_skips_symlinks(client, tmp_path):
    """Excludes symlinks from results."""
    real_file = tmp_path / "real.csv"
    real_file.write_text("data")
    symlink = tmp_path / "link.csv"
    symlink.symlink_to(real_file)

    mock_settings = {
        "source_registry": {"schwab": {"data_dir": str(tmp_path), "file_patterns": {"main": "*.csv"}, "enabled": True, "reader": "schwab_reader", "asset_prefixes": []}},
        "sources": {"pis": {}},
        "subsystems": {},
    }
    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        response = client.get("/settings/sources/files/schwab")

    data = response.json()
    filenames = [f["filename"] for f in data["files"]]
    assert "real.csv" in filenames
    assert "link.csv" not in filenames


def test_get_source_files_handles_unreadable_dir(client):
    """Returns empty list when directory is unreadable (OSError)."""
    mock_settings = {
        "source_registry": {"schwab": {"data_dir": "/nonexistent_dir_xyz", "file_patterns": {}, "enabled": True, "reader": "schwab_reader", "asset_prefixes": []}},
        "sources": {"pis": {}},
        "subsystems": {},
    }
    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        response = client.get("/settings/sources/files/schwab")

    assert response.status_code == 200
    data = response.json()
    assert data["files"] == []


def test_get_source_files_marks_active(client, tmp_path):
    """is_active=True for the glob-selected file (alphabetically last, not mtime-newest)."""
    # Create 3 files where alphabetically-last != mtime-newest
    # _resolve_source_file uses sorted()[-1] (alphabetically last)
    csv_alpha_last = tmp_path / "Schwab-2024-03-01.csv"  # alpha-LAST → active
    csv_mid = tmp_path / "Schwab-2024-02-01.csv"
    csv_mtime_newest = tmp_path / "Schwab-2024-01-01.csv"  # mtime-NEWEST but alpha-FIRST
    for f in [csv_alpha_last, csv_mid, csv_mtime_newest]:
        f.write_text("data")
    # alpha-last gets oldest mtime; mtime-newest gets alpha-first name
    os.utime(csv_alpha_last, (500, 500))
    os.utime(csv_mid, (1000, 1000))
    os.utime(csv_mtime_newest, (2000, 2000))

    mock_settings = {
        "source_registry": {"schwab": {"data_dir": str(tmp_path), "file_patterns": {"main": "Schwab-*.csv"}, "enabled": True, "reader": "schwab_reader", "asset_prefixes": []}},
        "sources": {"pis": {}},
        "subsystems": {},
    }
    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        response = client.get("/settings/sources/files/schwab")

    data = response.json()
    active_files = [f for f in data["files"] if f["is_active"]]
    assert len(active_files) == 1
    # Must be the alphabetically-last file, NOT the mtime-newest
    assert active_files[0]["filename"] == "Schwab-2024-03-01.csv"
    # Explicitly verify mtime-newest is NOT active
    mtime_newest_entry = next(f for f in data["files"] if f["filename"] == "Schwab-2024-01-01.csv")
    assert mtime_newest_entry["is_active"] is False


def test_get_source_files_empty_dir_all_filtered(client, tmp_path):
    """Returns empty files list when directory exists but no files match extensions."""
    # Create files with wrong extensions
    (tmp_path / "data.txt").write_text("data")
    (tmp_path / "notes.pdf").write_text("data")

    mock_settings = {
        "source_registry": {"schwab": {"data_dir": str(tmp_path), "file_patterns": {"main": "*.csv"}, "enabled": True, "reader": "schwab_reader", "asset_prefixes": []}},
        "sources": {"pis": {}},
        "subsystems": {},
    }
    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        response = client.get("/settings/sources/files/schwab")

    assert response.status_code == 200
    data = response.json()
    assert data["files"] == []
    assert data["total_count"] == 0
    assert data["directory"] == str(tmp_path)
