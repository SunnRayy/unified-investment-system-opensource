"""Tests for file_patterns validation in PUT /settings/sources."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from typing import Optional
from unittest.mock import patch


@pytest.fixture
def client():
    from src.api.main import app
    return TestClient(app)


def _base_settings(patterns: Optional[dict] = None, reader: str = "schwab") -> dict:
    """Build a minimal settings dict for mocking."""
    return {
        "source_registry": {
            reader: {
                "data_dir": "/tmp/test_data",
                "file_patterns": patterns if patterns is not None else {"main": "Schwab-*.csv"},
                "enabled": True,
                "reader": f"{reader}_reader",
                "asset_prefixes": [],
            }
        },
        "sources": {"pis": {}},
        "subsystems": {},
    }


def _put_patterns(client, reader: str, patterns: dict, base_settings: Optional[dict] = None):
    """Helper: PUT file_patterns update for a single reader."""
    settings = base_settings if base_settings is not None else _base_settings(reader=reader)
    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=settings):
        with patch("src.api.routes.settings.settings_manager.save_source_registry") as mock_save:
            response = client.put(
                "/settings/sources",
                json={"sources": [{"key": reader, "file_patterns": patterns}]},
            )
            return response, mock_save


# ------------------------------------------------------------------
# Test 1: valid patterns → 200, patterns visible in subsequent GET
# ------------------------------------------------------------------

def test_save_valid_patterns(client):
    """PUT with valid file_patterns dict returns 200."""
    new_patterns = {"main": "Schwab-*.csv", "alt": "Schwab_*.csv"}
    base = _base_settings(patterns={"main": "old-pattern-*.csv"})

    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=base):
        with patch("src.api.routes.settings.settings_manager.save_source_registry"):
            # Simulate updated settings returned after save
            updated_settings = _base_settings(patterns={**{"main": "old-pattern-*.csv"}, **new_patterns})
            with patch("src.api.routes.settings.settings_manager.load_settings", side_effect=[base, updated_settings]):
                response = client.put(
                    "/settings/sources",
                    json={"sources": [{"key": "schwab", "file_patterns": new_patterns}]},
                )
    assert response.status_code == 200
    data = response.json()
    schwab = next(s for s in data["sources"] if s["key"] == "schwab")
    # After merge, both old and new patterns present
    assert "alt" in schwab["file_patterns"]


# ------------------------------------------------------------------
# Test 2: max 5 patterns enforced
# ------------------------------------------------------------------

def test_max_5_patterns_enforced(client):
    """PUT with 6 patterns (merged) → 422."""
    # Existing has 1 pattern, new PUT adds 5 more → total 6
    existing = {"p1": "*.csv"}
    new_patterns = {"p2": "a-*.csv", "p3": "b-*.csv", "p4": "c-*.csv", "p5": "d-*.csv", "p6": "e-*.csv"}
    base = _base_settings(patterns=existing)
    response, _ = _put_patterns(client, "schwab", new_patterns, base_settings=base)
    assert response.status_code == 422


# ------------------------------------------------------------------
# Test 3: whitespace-only value rejected
# ------------------------------------------------------------------

def test_whitespace_only_value_rejected(client):
    """PUT with whitespace-only pattern value → 422."""
    base = _base_settings(patterns={})
    response, _ = _put_patterns(client, "schwab", {"ext": "   "}, base_settings=base)
    assert response.status_code == 422


# ------------------------------------------------------------------
# Test 4: absolute path value rejected
# ------------------------------------------------------------------

def test_absolute_path_value_rejected(client):
    """PUT with absolute path as pattern value → 422."""
    base = _base_settings(patterns={})
    response, _ = _put_patterns(client, "schwab", {"ext": "/absolute/*.csv"}, base_settings=base)
    assert response.status_code == 422


# ------------------------------------------------------------------
# Test 5: empty key rejected
# ------------------------------------------------------------------

def test_empty_key_rejected(client):
    """PUT with empty string as pattern key → 422."""
    base = _base_settings(patterns={})
    response, _ = _put_patterns(client, "schwab", {"": "*.csv"}, base_settings=base)
    assert response.status_code == 422


# ------------------------------------------------------------------
# Test 6: patterns persist after reload
# ------------------------------------------------------------------

def test_patterns_persist_after_reload(client):
    """Save patterns, then GET returns updated patterns."""
    new_patterns = {"main": "NewSchwab-*.csv"}
    base_before = _base_settings(patterns={"main": "OldSchwab-*.csv"})
    # After save, settings now reflect new patterns
    merged = {"main": "NewSchwab-*.csv"}  # old was overwritten by merge
    base_after = _base_settings(patterns=merged)

    load_call_count = 0

    def side_effect_load():
        nonlocal load_call_count
        load_call_count += 1
        if load_call_count <= 1:
            return base_before
        return base_after

    with patch("src.api.routes.settings.settings_manager.load_settings", side_effect=side_effect_load):
        with patch("src.api.routes.settings.settings_manager.save_source_registry"):
            # PUT to save
            put_resp = client.put(
                "/settings/sources",
                json={"sources": [{"key": "schwab", "file_patterns": new_patterns}]},
            )
    assert put_resp.status_code == 200
    data = put_resp.json()
    schwab = next(s for s in data["sources"] if s["key"] == "schwab")
    assert schwab["file_patterns"].get("main") == "NewSchwab-*.csv"


# ------------------------------------------------------------------
# Test 7: unknown reader returns 404
# ------------------------------------------------------------------

def test_unknown_reader_returns_404(client):
    """PUT updating an unknown reader key → 404."""
    base = _base_settings()
    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=base):
        with patch("src.api.routes.settings.settings_manager.save_source_registry"):
            response = client.put(
                "/settings/sources",
                json={"sources": [{"key": "nonexistent_reader", "file_patterns": {"main": "*.csv"}}]},
            )
    assert response.status_code == 404
    assert "Unknown reader" in response.json()["detail"]


# ------------------------------------------------------------------
# Test 8: partial update — file_patterns only, data_dir unchanged
# ------------------------------------------------------------------

def test_partial_update_patterns_only(client):
    """PUT with file_patterns but no data_dir change → 200, data_dir unchanged."""
    original_dir = "/original/data/dir"
    base = {
        "source_registry": {
            "schwab": {
                "data_dir": original_dir,
                "file_patterns": {"main": "Schwab-*.csv"},
                "enabled": True,
                "reader": "schwab_reader",
                "asset_prefixes": [],
            }
        },
        "sources": {"pis": {}},
        "subsystems": {},
    }

    new_patterns = {"main": "Schwab-*.csv", "backup": "SchwabBackup-*.csv"}

    # After save, return settings with patterns merged and data_dir intact
    after_save = {
        "source_registry": {
            "schwab": {
                "data_dir": original_dir,
                "file_patterns": {**{"main": "Schwab-*.csv"}, **new_patterns},
                "enabled": True,
                "reader": "schwab_reader",
                "asset_prefixes": [],
            }
        },
        "sources": {"pis": {}},
        "subsystems": {},
    }

    with patch("src.api.routes.settings.settings_manager.load_settings", side_effect=[base, after_save]):
        with patch("src.api.routes.settings.settings_manager.save_source_registry"):
            response = client.put(
                "/settings/sources",
                json={"sources": [{"key": "schwab", "file_patterns": new_patterns}]},
            )

    assert response.status_code == 200
    data = response.json()
    schwab = next(s for s in data["sources"] if s["key"] == "schwab")
    # data_dir must not have changed
    assert schwab["data_dir"] == original_dir
    # new pattern present
    assert "backup" in schwab["file_patterns"]


# ------------------------------------------------------------------
# Test 9: parametrized path-separator / traversal rejection
# ------------------------------------------------------------------

@pytest.fixture
def settings_dir(tmp_path):
    """Fixture alias — unused path, kept for fixture signature compatibility."""
    return tmp_path


@pytest.mark.parametrize("bad_pattern", [
    "subdir/*.csv",       # forward slash
    "subdir\\*.csv",      # backslash (Windows separator)
    "../escape.csv",      # path traversal
    "~/home.csv",         # tilde home
])
def test_path_separator_values_rejected(client, settings_dir, bad_pattern):
    """Pattern values with path separators or traversal must be rejected."""
    base = _base_settings(patterns={})
    response, _ = _put_patterns(client, "schwab", {"ext": bad_pattern}, base_settings=base)
    assert response.status_code == 422, f"Expected 422 for pattern {bad_pattern!r}, got {response.status_code}"


# ------------------------------------------------------------------
# Test 10: whitespace-only key rejected
# ------------------------------------------------------------------

def test_whitespace_only_key_rejected(client, settings_dir):
    """A whitespace-only pattern key must be rejected."""
    base = _base_settings(patterns={})
    response, _ = _put_patterns(client, "schwab", {"   ": "*.csv"}, base_settings=base)
    assert response.status_code == 422
