# tests/test_config.py
from pathlib import Path

import pytest

def test_config_loads_settings():
    """Test that config loads settings.yaml correctly."""
    from src.config import load_config
    
    config = load_config()
    
    assert 'subsystems' in config
    assert 'database' in config
    assert 'finance_dir' in config


def test_config_returns_finance_dir():
    """Test that config returns the shared Finance source directory."""
    from src.config import load_config

    config = load_config()
    finance_dir = config.get('finance_dir')

    assert finance_dir is not None
    assert Path(finance_dir).exists()


def test_config_rsu_workbook_points_to_excel_source():
    """RSU source should default to the Excel transaction workbook."""
    from src.config import load_config

    config = load_config()
    workbook = config["source_registry"]["rsu"]["file_patterns"]["workbook"]
    assert workbook == "RSU_transactions.xlsx"


def test_config_has_fred_external_data_settings():
    """FRED API integration settings should exist."""
    from src.config import load_config

    config = load_config()
    fred = config.get("external_data", {}).get("fred", {})

    assert fred.get("api_key") == "${FRED_API_KEY}"
    assert "fred/series/observations" in fred.get("base_url", "")


def test_falls_back_to_example_template_when_real_file_missing(tmp_path):
    """Program OSR WS-4b: settings.yaml missing but a committed .example
    twin present must load from the example — this is what lets a clean
    clone / Docker image boot without the owner's real config."""
    from src.config import load_config

    example = tmp_path / "settings.example.yaml"
    example.write_text(
        "database:\n  path: data/unified.duckdb\nfinance_dir: ./data/import\n",
        encoding="utf-8",
    )
    real_path = tmp_path / "settings.yaml"  # deliberately not created

    config = load_config(str(real_path))

    assert config["finance_dir"] == "./data/import"


def test_real_file_wins_over_example_when_both_present(tmp_path):
    from src.config import load_config

    example = tmp_path / "settings.example.yaml"
    example.write_text("finance_dir: ./from-example\n", encoding="utf-8")
    real = tmp_path / "settings.yaml"
    real.write_text("finance_dir: ./from-real\n", encoding="utf-8")

    config = load_config(str(real))

    assert config["finance_dir"] == "./from-real"


def test_raises_when_neither_real_nor_example_exists(tmp_path):
    from src.config import load_config

    with pytest.raises(FileNotFoundError):
        load_config(str(tmp_path / "nonexistent.yaml"))


def test_committed_example_template_loads_and_matches_real_shape():
    """config/settings.example.yaml as committed must parse and expose the
    same top-level shape load_config() consumers rely on."""
    from src.config import load_config

    config = load_config("config/settings.example.yaml")
    assert "source_registry" in config
    assert "database" in config
    assert config["finance_dir"] == "./data/import"
    assert "profile" in config
    assert "avatar_url" not in config["profile"]
