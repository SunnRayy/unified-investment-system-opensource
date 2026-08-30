"""Tests for seed_missing_readers() in src.services.settings_manager.

Verifies additive-only semantics, idempotency, and that existing reader
entries are never modified.  No DatabaseConnector is used; GCS upload is
always patched to avoid network calls.
"""
from __future__ import annotations

import yaml
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_settings(path: Path, source_registry: dict) -> None:
    """Write a minimal settings.yaml with the given source_registry."""
    data = {
        "source_registry": source_registry,
        "llm": {"model": "gemini/gemini-2.0-flash"},
    }
    with open(path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def _read_registry(path: Path) -> dict:
    with open(path) as f:
        return (yaml.safe_load(f) or {}).get("source_registry", {})


def _base_registry_without_ibkr() -> dict:
    """Minimal source_registry with all original 6 readers but no ibkr."""
    return {
        "schwab": {"enabled": True, "reader": "schwab_reader", "data_dir": None, "file_patterns": {"positions": "Individual-Positions-*.csv"}, "asset_prefixes": ["US_STK_", "US_ETF_", "CASH_USD"]},
        "cn_fund": {"enabled": True, "reader": "cn_fund_reader", "data_dir": None, "file_patterns": {"workbook": "funding_transactions.xlsx"}, "asset_prefixes": ["CN_FUND_"]},
        "gold": {"enabled": True, "reader": "gold_reader", "data_dir": None, "file_patterns": {"workbook": "Gold_transactions.xlsx"}, "asset_prefixes": ["GOLD_"]},
        "insurance": {"enabled": True, "reader": "insurance_reader", "data_dir": None, "file_patterns": {"workbook": "Insurance_Portfolio.xlsx"}, "asset_prefixes": ["INS_"]},
        "rsu": {"enabled": True, "reader": "rsu_reader", "data_dir": None, "file_patterns": {"workbook": "RSU_transactions.xlsx"}, "asset_prefixes": ["RSU_"]},
        "financial_summary": {"enabled": True, "reader": "financial_summary_reader", "data_dir": None, "file_patterns": {"workbook": "Financial Summary_new.xlsx"}, "asset_prefixes": []},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_seed_missing_readers_adds_absent_reader(tmp_path, monkeypatch):
    """A reader key absent from source_registry is added with enabled=True."""
    settings_file = tmp_path / "settings.yaml"
    _write_settings(settings_file, _base_registry_without_ibkr())

    monkeypatch.setattr("src.services.settings_manager.SETTINGS_PATH", settings_file)
    monkeypatch.setattr(
        "src.services.settings_manager.SETTINGS_LOCK_PATH",
        settings_file.with_suffix(".lock"),
    )
    monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)

    from src.services.settings_manager import seed_missing_readers

    seeded = seed_missing_readers()

    assert "ibkr" in seeded, f"Expected ibkr in seeded list, got: {seeded}"
    registry = _read_registry(settings_file)
    assert "ibkr" in registry
    assert registry["ibkr"]["enabled"] is True
    assert registry["ibkr"]["reader"] == "ibkr_reader"
    # flex_csv format → file_patterns should have 'flexquery' key
    assert "flexquery" in registry["ibkr"]["file_patterns"]


def test_seed_missing_readers_does_not_modify_existing_entries(tmp_path, monkeypatch):
    """Existing reader entries (even with custom values) must never be touched."""
    settings_file = tmp_path / "settings.yaml"
    # schwab has a custom data_dir and enabled=False — must not be overwritten
    registry = _base_registry_without_ibkr()
    registry["schwab"]["enabled"] = False
    registry["schwab"]["data_dir"] = "/custom/path"
    registry["schwab"]["file_patterns"] = {"positions": "my-schwab-*.csv"}
    _write_settings(settings_file, registry)

    monkeypatch.setattr("src.services.settings_manager.SETTINGS_PATH", settings_file)
    monkeypatch.setattr(
        "src.services.settings_manager.SETTINGS_LOCK_PATH",
        settings_file.with_suffix(".lock"),
    )
    monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)

    from src.services.settings_manager import seed_missing_readers

    seed_missing_readers()

    result_registry = _read_registry(settings_file)
    # Schwab entry must be completely unchanged
    assert result_registry["schwab"]["enabled"] is False
    assert result_registry["schwab"]["data_dir"] == "/custom/path"
    assert result_registry["schwab"]["file_patterns"] == {"positions": "my-schwab-*.csv"}


def test_seed_missing_readers_idempotent(tmp_path, monkeypatch):
    """Calling seed_missing_readers() twice adds nothing on the second call."""
    settings_file = tmp_path / "settings.yaml"
    _write_settings(settings_file, _base_registry_without_ibkr())

    monkeypatch.setattr("src.services.settings_manager.SETTINGS_PATH", settings_file)
    monkeypatch.setattr(
        "src.services.settings_manager.SETTINGS_LOCK_PATH",
        settings_file.with_suffix(".lock"),
    )
    monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)

    from src.services.settings_manager import seed_missing_readers

    first = seed_missing_readers()
    second = seed_missing_readers()

    assert "ibkr" in first, f"First call should have seeded ibkr, got: {first}"
    assert second == [], f"Second call must return [], got: {second}"


def test_seed_missing_readers_all_present_returns_empty(tmp_path, monkeypatch):
    """When all known readers are already in source_registry, returns []."""
    settings_file = tmp_path / "settings.yaml"
    full_registry = _base_registry_without_ibkr()
    full_registry["ibkr"] = {
        "enabled": True,
        "reader": "ibkr_reader",
        "data_dir": None,
        "file_patterns": {"flexquery": "IBKR_UIS_Report*.csv"},
        "asset_prefixes": ["US_STK_", "US_ETF_", "CASH_USD"],
    }
    _write_settings(settings_file, full_registry)

    monkeypatch.setattr("src.services.settings_manager.SETTINGS_PATH", settings_file)
    monkeypatch.setattr(
        "src.services.settings_manager.SETTINGS_LOCK_PATH",
        settings_file.with_suffix(".lock"),
    )
    monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)

    from src.services.settings_manager import seed_missing_readers

    result = seed_missing_readers()
    assert result == [], f"Expected [], got: {result}"


def test_seed_missing_readers_attempts_gcs_upload_when_bucket_set(tmp_path, monkeypatch):
    """When UIS_GCS_BUCKET is set and readers were seeded, upload_settings_to_gcs is called."""
    settings_file = tmp_path / "settings.yaml"
    _write_settings(settings_file, _base_registry_without_ibkr())

    monkeypatch.setattr("src.services.settings_manager.SETTINGS_PATH", settings_file)
    monkeypatch.setattr(
        "src.services.settings_manager.SETTINGS_LOCK_PATH",
        settings_file.with_suffix(".lock"),
    )
    monkeypatch.setenv("UIS_GCS_BUCKET", "test-bucket")

    upload_mock = MagicMock()
    # The function does a local import: from src.storage.gcs import upload_settings_to_gcs
    # Patching the module attribute ensures the local import picks up the mock.
    with patch("src.storage.gcs.upload_settings_to_gcs", upload_mock):
        from src.services.settings_manager import seed_missing_readers
        seeded = seed_missing_readers()

    assert "ibkr" in seeded
    upload_mock.assert_called_once()
