"""Hermetic unit tests for src/import_adapters/reader_generator.py (A2).

All file I/O is pointed at tmp_path — no real config/ or data/ directories are
read or written.  No database.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.import_adapters.reader_generator import generate_reader_artifacts, _sanitize_key
from src.sources.reader_config import load_reader_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sample_csv(tmp_path: Path, filename: str = "portfolio.csv") -> Path:
    """Create a minimal holdings-style CSV and return its path."""
    csv_path = tmp_path / filename
    csv_path.write_text(
        "symbol,qty,value_usd,account\n"
        "AAPL,10,1500.00,Main\n"
        "GOOG,5,800.00,Main\n",
        encoding="utf-8",
    )
    return csv_path


def _dirs(tmp_path: Path):
    """Return injectable path arguments for generate_reader_artifacts."""
    return {
        "config_readers_dir": tmp_path / "config" / "readers",
        "settings_path": tmp_path / "config" / "settings.yaml",
        "authority_path": tmp_path / "config" / "source_authority.yaml",
        "data_dir_root": tmp_path / "data" / "import_adapters",
    }


SAMPLE_COLUMN_MAPPING = {
    "asset_id": "symbol",
    "quantity": "qty",
    "market_value": "value_usd",
    "account": "account",
}


# ---------------------------------------------------------------------------
# Test: happy-path CSV holdings
# ---------------------------------------------------------------------------

def test_generate_reader_artifacts_csv_holdings(tmp_path: Path):
    csv_file = _make_sample_csv(tmp_path)
    dirs = _dirs(tmp_path)

    result = generate_reader_artifacts(
        reader_key="broker_x",
        source_system="Broker_X",
        display_name="Broker X Portfolio",
        asset_prefixes=["BRK_"],
        authority_priority=7,
        column_mapping=SAMPLE_COLUMN_MAPPING,
        fx_rate=7.1,
        import_type="holdings",
        upload_file_path=str(csv_file),
        file_format="csv",
        **dirs,
    )

    # ---- Reader YAML exists and parses cleanly ----
    reader_yaml_path = dirs["config_readers_dir"] / "broker_x.yaml"
    assert reader_yaml_path.exists(), "Reader YAML not created"

    cfg = load_reader_config(reader_yaml_path)
    assert cfg.identity.source_key == "broker_x"
    assert cfg.identity.source_system == "Broker_X"
    assert cfg.identity.asset_prefixes == ["BRK_"]
    assert cfg.identity.allowed_extensions == [".csv"]

    assert cfg.parsing is not None
    assert cfg.parsing.format == "csv"
    assert cfg.parsing.wizard is not None
    assert cfg.parsing.wizard.column_mapping == SAMPLE_COLUMN_MAPPING
    assert cfg.parsing.wizard.fx_rate == pytest.approx(7.1)
    assert cfg.parsing.wizard.import_type == "holdings"

    # holdings hook wired
    assert cfg.parsing.holdings_from_sheet_hook == "wizard_holdings_from_sheet"
    assert cfg.parsing.transactions_from_sheet_hook is None

    # sheets entry
    assert len(cfg.parsing.sheets) == 1
    sheet = cfg.parsing.sheets[0]
    assert sheet.target == "holdings"
    assert sheet.file_glob == csv_file.name
    assert sheet.select == "latest"

    # ---- settings.yaml gained source_registry entry ----
    settings_data = yaml.safe_load(dirs["settings_path"].read_text(encoding="utf-8"))
    registry = settings_data.get("source_registry", {})
    assert "broker_x" in registry, "source_registry entry missing"
    entry = registry["broker_x"]
    assert entry["enabled"] is True
    assert entry["asset_prefixes"] == ["BRK_"]
    data_dir = Path(entry["data_dir"])
    assert data_dir.is_dir()
    assert "broker_x" in str(data_dir)
    # file_patterns dict contains the glob
    assert "csv" in entry["file_patterns"]
    assert entry["file_patterns"]["csv"] == csv_file.name

    # ---- authority YAML gained a rule ----
    auth_data = yaml.safe_load(dirs["authority_path"].read_text(encoding="utf-8"))
    rules = auth_data.get("rules", [])
    matching = [r for r in rules if r.get("authority") == "Broker_X" and r.get("pattern") == "BRK_*"]
    assert len(matching) == 1, f"Expected 1 authority rule for Broker_X, got: {matching}"
    assert matching[0]["priority"] == 7

    # ---- uploaded file was copied into data_dir ----
    seeded = Path(result["seeded_file"])
    assert seeded.exists(), "Seeded file not found in data_dir"
    assert seeded.read_text(encoding="utf-8") == csv_file.read_text(encoding="utf-8")

    # ---- file_patterns glob matches the seeded file ----
    matches = list(data_dir.glob(entry["file_patterns"]["csv"]))
    assert len(matches) == 1 and matches[0].name == csv_file.name

    # ---- result dict is populated ----
    assert result["reader_key"] == "broker_x"
    assert Path(result["reader_yaml_path"]) == reader_yaml_path


# ---------------------------------------------------------------------------
# Test: collision guard — different source_system raises ValueError
# ---------------------------------------------------------------------------

def test_collision_different_source_system(tmp_path: Path):
    csv_file = _make_sample_csv(tmp_path)
    dirs = _dirs(tmp_path)

    # First call: create broker_x with source_system Broker_X
    generate_reader_artifacts(
        reader_key="broker_x",
        source_system="Broker_X",
        display_name="Broker X",
        asset_prefixes=["BRK_"],
        authority_priority=7,
        column_mapping=SAMPLE_COLUMN_MAPPING,
        fx_rate=None,
        import_type="holdings",
        upload_file_path=str(csv_file),
        file_format="csv",
        **dirs,
    )

    # Second call: same reader_key but DIFFERENT source_system → ValueError
    with pytest.raises(ValueError, match="already exists with source_system"):
        generate_reader_artifacts(
            reader_key="broker_x",
            source_system="Totally_Different_System",
            display_name="Different",
            asset_prefixes=["DIFF_"],
            authority_priority=5,
            column_mapping=SAMPLE_COLUMN_MAPPING,
            fx_rate=None,
            import_type="holdings",
            upload_file_path=str(csv_file),
            file_format="csv",
            **dirs,
        )


# ---------------------------------------------------------------------------
# Test: same source_system re-approve is idempotent (allowed overwrite)
# ---------------------------------------------------------------------------

def test_same_source_system_reapprove_is_idempotent(tmp_path: Path):
    csv_file = _make_sample_csv(tmp_path)
    dirs = _dirs(tmp_path)

    for _ in range(2):
        generate_reader_artifacts(
            reader_key="broker_x",
            source_system="Broker_X",
            display_name="Broker X",
            asset_prefixes=["BRK_"],
            authority_priority=7,
            column_mapping=SAMPLE_COLUMN_MAPPING,
            fx_rate=7.1,
            import_type="holdings",
            upload_file_path=str(csv_file),
            file_format="csv",
            **dirs,
        )

    # Should complete without error; YAML still parses
    reader_yaml_path = dirs["config_readers_dir"] / "broker_x.yaml"
    cfg = load_reader_config(reader_yaml_path)
    assert cfg.identity.source_system == "Broker_X"

    # Authority rules must NOT be duplicated
    auth_data = yaml.safe_load(dirs["authority_path"].read_text(encoding="utf-8"))
    rules = auth_data.get("rules", [])
    matching = [r for r in rules if r.get("authority") == "Broker_X" and r.get("pattern") == "BRK_*"]
    assert len(matching) == 1, "Authority rule was duplicated on re-approve"


# ---------------------------------------------------------------------------
# Test: transactions import_type uses correct hook
# ---------------------------------------------------------------------------

def test_transactions_import_type_hook(tmp_path: Path):
    csv_file = _make_sample_csv(tmp_path, filename="trades.csv")
    dirs = _dirs(tmp_path)

    generate_reader_artifacts(
        reader_key="broker_tx",
        source_system="Broker_TX",
        display_name="Broker TX",
        asset_prefixes=["BTX_"],
        authority_priority=6,
        column_mapping={"transaction_date": "date", "amount": "value_usd"},
        fx_rate=None,
        import_type="transactions",
        upload_file_path=str(csv_file),
        file_format="csv",
        **dirs,
    )

    reader_yaml_path = dirs["config_readers_dir"] / "broker_tx.yaml"
    cfg = load_reader_config(reader_yaml_path)
    assert cfg.parsing.transactions_from_sheet_hook == "wizard_transactions_from_sheet"
    assert cfg.parsing.holdings_from_sheet_hook is None
    assert cfg.parsing.sheets[0].target == "transactions"


# ---------------------------------------------------------------------------
# Test: no upload_file_path (None) — still writes YAML + settings + authority
# ---------------------------------------------------------------------------

def test_no_upload_file(tmp_path: Path):
    dirs = _dirs(tmp_path)

    result = generate_reader_artifacts(
        reader_key="bare_source",
        source_system="Bare_Source",
        display_name="Bare Source",
        asset_prefixes=["BARE_"],
        authority_priority=5,
        column_mapping={"asset_id": "id", "market_value": "val"},
        fx_rate=None,
        import_type="holdings",
        upload_file_path=None,
        file_format="csv",
        **dirs,
    )

    assert result["seeded_file"] is None
    reader_yaml_path = dirs["config_readers_dir"] / "bare_source.yaml"
    assert reader_yaml_path.exists()
    cfg = load_reader_config(reader_yaml_path)
    assert cfg.identity.source_system == "Bare_Source"


# ---------------------------------------------------------------------------
# Test: excel file_format uses correct extensions + no file_glob on sheet
# ---------------------------------------------------------------------------

def test_excel_file_format(tmp_path: Path):
    # Create a dummy xlsx file (content does not matter for config generation)
    xlsx_file = tmp_path / "portfolio.xlsx"
    xlsx_file.write_bytes(b"PK\x03\x04")  # minimal xlsx magic bytes
    dirs = _dirs(tmp_path)

    generate_reader_artifacts(
        reader_key="excel_source",
        source_system="Excel_Source",
        display_name="Excel Source",
        asset_prefixes=["EXL_"],
        authority_priority=6,
        column_mapping={"asset_id": "Symbol", "market_value": "Value"},
        fx_rate=6.9,
        import_type="holdings",
        upload_file_path=str(xlsx_file),
        file_format="excel",
        **dirs,
    )

    reader_yaml_path = dirs["config_readers_dir"] / "excel_source.yaml"
    cfg = load_reader_config(reader_yaml_path)
    assert cfg.identity.allowed_extensions == [".xlsx", ".xls"]
    # Excel sheet has a name but no file_glob
    sheet = cfg.parsing.sheets[0]
    assert sheet.file_glob is None
    assert sheet.name == "Sheet1"


# ---------------------------------------------------------------------------
# Test: authority catch-all rule (*) is preserved and new rule inserted before it
# ---------------------------------------------------------------------------

def test_authority_rule_inserted_before_catchall(tmp_path: Path):
    dirs = _dirs(tmp_path)

    # Pre-populate authority file with a catch-all
    auth_path = dirs["authority_path"]
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(
        "rules:\n"
        "  - pattern: '*'\n"
        "    authority: Financial_Summary_Excel\n"
        "    priority: 9\n",
        encoding="utf-8",
    )

    csv_file = _make_sample_csv(tmp_path)
    generate_reader_artifacts(
        reader_key="new_broker",
        source_system="New_Broker",
        display_name="New Broker",
        asset_prefixes=["NB_"],
        authority_priority=8,
        column_mapping=SAMPLE_COLUMN_MAPPING,
        fx_rate=None,
        import_type="holdings",
        upload_file_path=str(csv_file),
        file_format="csv",
        **dirs,
    )

    auth_data = yaml.safe_load(auth_path.read_text(encoding="utf-8"))
    rules = auth_data["rules"]
    patterns = [r["pattern"] for r in rules]
    # New rule should appear BEFORE the catch-all
    new_idx = patterns.index("NB_*")
    catchall_idx = patterns.index("*")
    assert new_idx < catchall_idx, "New rule must be inserted before the '*' catch-all"


# ---------------------------------------------------------------------------
# Test: _sanitize_key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Broker X", "broker_x"),
    ("Broker-X123", "broker_x123"),
    ("  My Fund  ", "my_fund"),
    ("CN_Fund_Excel", "cn_fund_excel"),
    ("broker.x!y@z", "broker_x_y_z"),
])
def test_sanitize_key(raw, expected):
    assert _sanitize_key(raw) == expected
