"""Seed-pack loader (Program OSR WS-3a — additive scaffolding only).

Reads seeds/<profile>/ (YAML) and returns the same shapes
src.services.reader_mappings._DEFAULTS currently provides, plus the
schema.sql/connector.py-V15 seed categories (memos, data_fixes,
unforced_errors, valuation_reference) that have no _DEFAULTS-style runtime
merge today.

NOT WIRED INTO ANY RUNTIME PATH YET. src/database/connector.py,
src/database/mapping_seeds.py, and src/services/reader_mappings.py are all
untouched by this module — nothing here executes during a sync. Re-pointing
V75-V82 at an active pack (without archiving or deleting any of the owner's
existing reader_mappings rows) is WS-3b, reviewed separately before this
loader's output is consumed by anything production-adjacent.

See seeds/README.md for the on-disk format this module reads.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from src.database.mapping_seeds import IEColumn

SEEDS_ROOT = Path(__file__).resolve().parent.parent.parent / "seeds"
DEFAULT_PROFILE = "example"
PROFILE_ENV_VAR = "UIS_SEED_PROFILE"

# reader_mappings.py's mapping_kind names, for the (reader_key, kind) tuple
# keys the loader emits — kept as constants so a typo here fails loudly
# (KeyError in a test) rather than silently producing an unused shape.
_FS_COLUMN = "fs_column"
_IE_COLUMN = "ie_column"
_ID_FIELD_MAP = "id_field_map"
_KNOWN_ETF = "known_etf"
_SYMBOL_NORM = "symbol_norm"
_ACTION_MAP = "action_map"
_TYPE_MAP = "type_map"


class SeedProfileNotFoundError(FileNotFoundError):
    """Raised when seeds/<profile>/ does not exist."""


@dataclass(frozen=True)
class SeedPack:
    """Everything one seed profile provides.

    ``reader_mappings`` is keyed exactly like
    ``src.services.reader_mappings._DEFAULTS`` — (reader_key, mapping_kind)
    -> value dict — a drop-in for that dict's shape in WS-3b.
    """

    profile: str
    reader_mappings: dict[tuple[str, str], dict[str, Any]]
    fs_ignored_columns: list[str]
    memo_registry: list[dict[str, Any]]
    memo_asset_map: list[dict[str, Any]]
    data_fixes: list[dict[str, Any]]
    unforced_errors: list[dict[str, Any]]
    valuation_reference: list[dict[str, Any]]


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    return doc or {}


def _load_fs_column(doc: dict) -> dict[str, tuple[str, str, str]]:
    fs = doc.get("fs_column") or {}
    result: dict[str, tuple[str, str, str]] = {}
    for row in fs.get("mapped") or []:
        result[row["excel_col"]] = (row["asset_id"], row["asset_name"], row["currency"])
    return result


def _load_fs_ignored(doc: dict) -> list[str]:
    fs = doc.get("fs_column") or {}
    return [row["excel_col"] for row in fs.get("ignored") or []]


def _load_ie_column(doc: dict) -> dict[str, IEColumn]:
    result: dict[str, IEColumn] = {}
    for row in doc.get("ie_column") or []:
        result[row["excel_col"]] = IEColumn(
            role=row["role"],
            bucket=row.get("bucket"),
            currency=row["currency"],
            group=row.get("group"),
            validates=row.get("validates"),
        )
    return result


def _load_id_field_map(doc: dict) -> dict[str, str]:
    return {f'{row["field"]}:{row["label"]}': row["code"] for row in (doc.get("id_field_map") or [])}


def _load_known_etf(doc: dict) -> dict[str, bool]:
    return {ticker: True for ticker in (doc.get("known_etf") or [])}


def _load_symbol_norm(doc: dict) -> dict[str, str]:
    return {row["from"]: row["to"] for row in (doc.get("symbol_norm") or [])}


def _load_raw_type_map(doc: dict, key: str) -> dict[str, str]:
    return {row["raw"]: row["type"] for row in (doc.get(key) or [])}


def resolve_profile(profile: Optional[str] = None) -> str:
    """profile arg > $UIS_SEED_PROFILE > DEFAULT_PROFILE, in that order."""
    return profile or os.environ.get(PROFILE_ENV_VAR) or DEFAULT_PROFILE


def list_profiles(seeds_root: Path = SEEDS_ROOT) -> list[str]:
    if not seeds_root.is_dir():
        return []
    return sorted(p.name for p in seeds_root.iterdir() if p.is_dir())


def load_seed_pack(profile: Optional[str] = None, seeds_root: Path = SEEDS_ROOT) -> SeedPack:
    """Load seeds/<profile>/.

    Args:
        profile: explicit profile name. Falls back to $UIS_SEED_PROFILE, then
            'example' (resolve_profile's order).
        seeds_root: override for tests; defaults to the repo's seeds/ dir.

    Raises:
        SeedProfileNotFoundError: seeds/<profile>/ does not exist.

    An empty-but-present profile (seeds/empty/) loads cleanly and returns
    empty collections everywhere — every YAML file is optional; a missing
    file is treated the same as a present-but-empty one.
    """
    resolved = resolve_profile(profile)
    root = seeds_root / resolved
    if not root.is_dir():
        raise SeedProfileNotFoundError(
            f"seed profile {resolved!r} not found at {root} "
            f"(known profiles: {list_profiles(seeds_root)})"
        )

    rm_dir = root / "reader_mappings"
    fs_doc = _load_yaml(rm_dir / "financial_summary.yaml")
    gold_doc = _load_yaml(rm_dir / "gold.yaml")
    insurance_doc = _load_yaml(rm_dir / "insurance.yaml")
    rsu_doc = _load_yaml(rm_dir / "rsu.yaml")
    schwab_doc = _load_yaml(rm_dir / "schwab.yaml")
    cn_fund_doc = _load_yaml(rm_dir / "cn_fund.yaml")

    reader_mappings: dict[tuple[str, str], dict[str, Any]] = {
        ("financial_summary", _FS_COLUMN): _load_fs_column(fs_doc),
        ("financial_summary", _IE_COLUMN): _load_ie_column(fs_doc),
        ("gold", _ID_FIELD_MAP): _load_id_field_map(gold_doc),
        ("insurance", _ID_FIELD_MAP): _load_id_field_map(insurance_doc),
        ("rsu", _ID_FIELD_MAP): _load_id_field_map(rsu_doc),
        ("schwab", _KNOWN_ETF): _load_known_etf(schwab_doc),
        ("schwab", _SYMBOL_NORM): _load_symbol_norm(schwab_doc),
        ("schwab", _ACTION_MAP): _load_raw_type_map(schwab_doc, "action_map"),
        ("cn_fund", _TYPE_MAP): _load_raw_type_map(cn_fund_doc, "type_map"),
    }

    memos_doc = _load_yaml(root / "memos.yaml")
    data_fixes_doc = _load_yaml(root / "data_fixes.yaml")
    unforced_errors_doc = _load_yaml(root / "unforced_errors.yaml")
    valuation_doc = _load_yaml(root / "valuation_reference.yaml")

    return SeedPack(
        profile=resolved,
        reader_mappings=reader_mappings,
        fs_ignored_columns=_load_fs_ignored(fs_doc),
        memo_registry=memos_doc.get("memo_registry") or [],
        memo_asset_map=memos_doc.get("memo_asset_map") or [],
        data_fixes=data_fixes_doc.get("data_fixes") or [],
        unforced_errors=unforced_errors_doc.get("unforced_errors") or [],
        valuation_reference=valuation_doc.get("valuation_reference") or [],
    )


def seed_demo_content(connector, profile: Optional[str] = None) -> None:
    """Populate data_fixes / unforced_errors / memo_registry / memo_asset_map /
    valuation_reference from a seed pack (Program OSR WS-3c).

    These five tables used to be seeded with the owner's real content directly
    in schema.sql / migrations/015_memo_registry.sql / connector.py's V15
    migration — moved here so a public export ships DDL only. Gated the same
    way src.services.reader_mappings._get_defaults() gates reader_mappings:
    $UIS_SEED_PROFILE unset -> do nothing (every deployment today, incl.
    production — the owner's real rows already exist from when these seeds
    ran inline, and are untouched by this function either way). Set -> load
    that profile's pack and insert, idempotently (WHERE NOT EXISTS / ON
    CONFLICT DO NOTHING — safe to call on every bootstrap).

    Call this from bootstrap_database(), after initialize_schema() and
    run_migrations() so every target table is guaranteed to exist.
    """
    import os as _os

    if not _os.environ.get(PROFILE_ENV_VAR):
        return

    pack = load_seed_pack(profile)

    for row in pack.data_fixes:
        connector.execute(
            """
            INSERT INTO data_fixes (title, description, metric_key, due_at, status)
            SELECT ?, ?, ?, CURRENT_TIMESTAMP + (? * INTERVAL 1 DAY), ?
            WHERE NOT EXISTS (SELECT 1 FROM data_fixes WHERE title = ?)
            """,
            (
                row["title"], row.get("description"), row.get("metric_key"),
                row.get("due_days", 30), row.get("status", "open"), row["title"],
            ),
        )

    for row in pack.unforced_errors:
        connector.execute(
            """
            INSERT INTO unforced_errors (error_date, description, est_cost_cny, root_cause, linked_rule)
            SELECT ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM unforced_errors WHERE error_date = ? AND description = ?
            )
            """,
            (
                row["error_date"], row["description"], row.get("est_cost_cny"),
                row.get("root_cause"), row.get("linked_rule"),
                row["error_date"], row["description"],
            ),
        )

    for row in pack.memo_registry:
        connector.execute(
            """
            INSERT INTO memo_registry (memo_id, title, status, falsification_summary, doc_link)
            SELECT ?, ?, ?, ?, ?
            WHERE NOT EXISTS (SELECT 1 FROM memo_registry WHERE memo_id = ?)
            """,
            (
                row["memo_id"], row["title"], row.get("status", "active"),
                row.get("falsification_summary"), row.get("doc_link"), row["memo_id"],
            ),
        )

    for row in pack.memo_asset_map:
        connector.execute(
            """
            INSERT INTO memo_asset_map (memo_id, asset_id)
            SELECT ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM memo_asset_map WHERE memo_id = ? AND asset_id = ?
            )
            """,
            (row["memo_id"], row["asset_id"], row["memo_id"], row["asset_id"]),
        )

    for row in pack.valuation_reference:
        connector.execute(
            """
            INSERT INTO valuation_reference
              (ticker, metric, low_threshold, high_threshold, historical_mean, rate_sensitive, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (ticker, metric) DO NOTHING
            """,
            (
                row["ticker"], row["metric"], row["low_threshold"], row["high_threshold"],
                row.get("historical_mean"), row.get("rate_sensitive", False), row.get("notes"),
            ),
        )
