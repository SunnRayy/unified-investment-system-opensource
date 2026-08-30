"""Generate reader YAML + settings + authority artifacts from a wizard-approved import adapter.

Task A2 of the import-adapter↔config-driven convergence (ADR-018).

Entry point: generate_reader_artifacts() — pure, all paths injectable so it is
unit-testable without touching real config/ or data/ directories.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Key sanitisation
# ---------------------------------------------------------------------------

def _sanitize_key(raw: str) -> str:
    """Lower-case, replace non-alnum runs with underscore, strip leading/trailing."""
    key = raw.lower()
    key = re.sub(r"[^a-z0-9]+", "_", key)
    key = key.strip("_")
    return key


# ---------------------------------------------------------------------------
# Atomic YAML helpers (no dependency on settings_manager so paths are injectable)
# ---------------------------------------------------------------------------

def _atomic_yaml_write(path: Path, data: dict) -> None:
    """Write *data* to *path* atomically (tmp file in same dir + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            yaml.dump(data, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# Reader YAML builder
# ---------------------------------------------------------------------------

def _build_reader_dict(
    *,
    reader_key: str,
    source_system: str,
    display_name: str,
    asset_prefixes: list[str],
    column_mapping: dict[str, str],
    fx_rate: Optional[float],
    import_type: str,
    file_format: str,
    file_glob: str,
) -> dict:
    """Build the raw dict that maps 1-to-1 to a ReaderConfig YAML."""
    allowed_extensions = [".csv"] if file_format == "csv" else [".xlsx", ".xls"]

    if file_format == "csv":
        sheet = {
            "name": import_type,   # logical name; the engine uses file_glob for CSV
            "target": import_type,
            "file_glob": file_glob,
            "select": "latest",
        }
    else:
        # Excel: sheet name defaults to the first sheet.
        # Limitation: we cannot know the actual sheet name from filename alone;
        # the wizard hook (wizard_holdings/transactions_from_sheet) reads whatever
        # sheet is loaded by the engine's first-sheet default.  We use "Sheet1"
        # as the conventional placeholder — operators should update this if the
        # workbook uses a different tab name.
        sheet = {
            "name": "Sheet1",
            "target": import_type,
        }

    # Hook field names match ParsingConfig field names exactly
    if import_type == "holdings":
        hook_field = "holdings_from_sheet_hook"
        hook_value = "wizard_holdings_from_sheet"
    else:
        hook_field = "transactions_from_sheet_hook"
        hook_value = "wizard_transactions_from_sheet"

    parsing = {
        "format": file_format,
        "snapshot_date": {"strategy": "read_timestamp"},
        "sheets": [sheet],
        hook_field: hook_value,
        "wizard": {
            "column_mapping": column_mapping,
            "fx_rate": fx_rate,
            "import_type": import_type,
        },
    }

    return {
        "identity": {
            "source_key": reader_key,
            "source_system": source_system,
            "display_label": display_name,
            "display_name": display_name,
            "asset_prefixes": asset_prefixes,
            "allowed_extensions": allowed_extensions,
            "category": "reader",
            "validator": None,
        },
        "parsing": parsing,
    }


# ---------------------------------------------------------------------------
# Authority rule helpers
# ---------------------------------------------------------------------------

def _build_authority_rule(prefix: str, source_system: str, priority: int) -> dict:
    """Build a single authority rule dict matching source_authority.yaml format."""
    pattern = f"{prefix}*" if not prefix.endswith("*") else prefix
    return {
        "pattern": pattern,
        "authority": source_system,
        "priority": priority,
    }


def _rule_matches(rule: dict, pattern: str, authority: str) -> bool:
    return rule.get("pattern") == pattern and rule.get("authority") == authority


def _update_authority_rules(
    authority_data: dict,
    asset_prefixes: list[str],
    source_system: str,
    priority: int,
) -> dict:
    """Idempotently append/update authority rules for every asset_prefix."""
    rules: list = authority_data.get("rules", [])
    for prefix in asset_prefixes:
        pattern = f"{prefix}*" if not prefix.endswith("*") else prefix
        # Check for an existing rule with the same pattern + authority
        existing = next(
            (r for r in rules if _rule_matches(r, pattern, source_system)), None
        )
        if existing is not None:
            # Update priority in case it changed
            existing["priority"] = priority
        else:
            # Insert before the catch-all "*" rule if present, else append
            catchall_idx = next(
                (i for i, r in enumerate(rules) if r.get("pattern") == "*"), None
            )
            new_rule = _build_authority_rule(prefix, source_system, priority)
            if catchall_idx is not None:
                rules.insert(catchall_idx, new_rule)
            else:
                rules.append(new_rule)
    authority_data["rules"] = rules
    return authority_data


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_reader_artifacts(
    *,
    reader_key: str,
    source_system: str,
    display_name: str,
    asset_prefixes: list[str],
    authority_priority: int,
    column_mapping: dict[str, str],
    fx_rate: Optional[float],
    import_type: str,
    upload_file_path: Optional[str],
    file_format: str,
    config_readers_dir: Path = Path("config/readers"),
    settings_path: Path = Path("config/settings.yaml"),
    authority_path: Path = Path("config/source_authority.yaml"),
    data_dir_root: Path = Path("data/import_adapters"),
) -> dict:
    """Generate all config artifacts so a wizard-approved source becomes a first-class reader.

    Parameters
    ----------
    reader_key:
        Sanitized lowercase identifier, e.g. ``"broker_x"``.  Becomes the file
        stem for the reader YAML and the source_registry key.
    source_system:
        Authority label, e.g. ``"Broker_X"``.
    display_name:
        Human-readable label shown in the UI.
    asset_prefixes:
        List of asset-ID prefixes owned by this source, e.g. ``["BRK_"]``.
    authority_priority:
        Integer priority written into source_authority.yaml (8 = typical reader).
    column_mapping:
        ``{dst_field: src_column}`` mapping as captured by the wizard.
    fx_rate:
        Optional FX rate (e.g. 7.1 for USD→CNY) or None.
    import_type:
        ``"holdings"`` or ``"transactions"``.
    upload_file_path:
        Absolute path to the uploaded file to seed data_dir with, or None.
    file_format:
        ``"csv"`` or ``"excel"``.
    config_readers_dir:
        Directory to write ``<reader_key>.yaml`` into.
    settings_path:
        Path to settings.yaml (injectable for tests).
    authority_path:
        Path to source_authority.yaml (injectable for tests).
    data_dir_root:
        Root for per-reader data directories.

    Returns
    -------
    dict
        Summary of written artifacts: paths + reader_key.

    Raises
    ------
    ValueError
        If a ``<reader_key>.yaml`` already exists with a *different* source_system
        (collision guard).
    ValueError
        If the generated YAML fails ``load_reader_config()`` self-validation.
    """
    # Late import to keep this module standalone; load_reader_config only needs
    # yaml + pydantic (no DB).
    from src.sources.reader_config import load_reader_config  # noqa: PLC0415

    config_readers_dir = Path(config_readers_dir)
    settings_path = Path(settings_path)
    authority_path = Path(authority_path)
    data_dir_root = Path(data_dir_root)

    reader_yaml_path = config_readers_dir / f"{reader_key}.yaml"

    # ------------------------------------------------------------------
    # Collision guard
    # ------------------------------------------------------------------
    if reader_yaml_path.exists():
        existing_raw = _load_yaml(reader_yaml_path)
        existing_ss = (existing_raw.get("identity") or {}).get("source_system")
        if existing_ss and existing_ss != source_system:
            raise ValueError(
                f"Reader YAML '{reader_yaml_path}' already exists with "
                f"source_system='{existing_ss}', which conflicts with requested "
                f"source_system='{source_system}'.  Use a different reader_key or "
                f"re-approve under the same source_system."
            )

    # ------------------------------------------------------------------
    # Data dir + file seed
    # ------------------------------------------------------------------
    data_dir = data_dir_root / reader_key
    data_dir.mkdir(parents=True, exist_ok=True)

    seeded_filename: Optional[str] = None
    file_glob: str

    if upload_file_path is not None:
        src_path = Path(upload_file_path)
        seeded_filename = src_path.name
        dest = data_dir / seeded_filename
        shutil.copy2(str(src_path), str(dest))

    # Build glob pattern
    if seeded_filename:
        # Use the exact filename as the glob so it matches precisely
        file_glob = seeded_filename
    else:
        # Fallback: match by extension
        if file_format == "csv":
            file_glob = "*.csv"
        else:
            file_glob = "*.xlsx"

    # ------------------------------------------------------------------
    # Reader YAML
    # ------------------------------------------------------------------
    reader_dict = _build_reader_dict(
        reader_key=reader_key,
        source_system=source_system,
        display_name=display_name,
        asset_prefixes=asset_prefixes,
        column_mapping=column_mapping,
        fx_rate=fx_rate,
        import_type=import_type,
        file_format=file_format,
        file_glob=file_glob,
    )

    # Atomic write
    config_readers_dir.mkdir(parents=True, exist_ok=True)
    _atomic_yaml_write(reader_yaml_path, reader_dict)

    # Self-validation: assert the written YAML parses cleanly
    try:
        load_reader_config(reader_yaml_path)
    except Exception as exc:
        # Clean up the bad file
        try:
            reader_yaml_path.unlink()
        except OSError:
            pass
        raise ValueError(
            f"Generated reader YAML failed self-validation: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # source_registry entry in settings.yaml
    # ------------------------------------------------------------------
    settings_data = _load_yaml(settings_path)
    if "source_registry" not in settings_data or settings_data["source_registry"] is None:
        settings_data["source_registry"] = {}

    registry_entry = {
        "reader": f"{reader_key}_reader",
        "enabled": True,
        "data_dir": str(data_dir),
        "asset_prefixes": asset_prefixes,
        "file_patterns": {file_format: file_glob},
    }
    settings_data["source_registry"][reader_key] = registry_entry

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_yaml_write(settings_path, settings_data)

    # ------------------------------------------------------------------
    # Authority rules in source_authority.yaml
    # ------------------------------------------------------------------
    authority_data = _load_yaml(authority_path)
    if "rules" not in authority_data or authority_data["rules"] is None:
        authority_data["rules"] = []

    authority_data = _update_authority_rules(
        authority_data,
        asset_prefixes=asset_prefixes,
        source_system=source_system,
        priority=authority_priority,
    )

    authority_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_yaml_write(authority_path, authority_data)

    # ------------------------------------------------------------------
    # Invalidate registry singleton (ADR-018 Phase 3)
    # The new reader YAML must be visible to the next get_registry() call
    # without requiring a server restart.  Local import avoids a cycle
    # (registry imports only stdlib/yaml/pydantic/reader_config).
    # ------------------------------------------------------------------
    try:
        from src.sources.registry import reset_registry  # noqa: PLC0415
        reset_registry()
    except Exception:
        pass  # Non-fatal — server restart picks it up if this fails

    # ------------------------------------------------------------------
    # Return summary
    # ------------------------------------------------------------------
    return {
        "reader_key": reader_key,
        "reader_yaml_path": str(reader_yaml_path),
        "data_dir": str(data_dir),
        "seeded_file": str(data_dir / seeded_filename) if seeded_filename else None,
        "settings_path": str(settings_path),
        "authority_path": str(authority_path),
        "file_glob": file_glob,
    }
