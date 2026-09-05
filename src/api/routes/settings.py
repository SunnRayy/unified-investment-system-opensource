"""Settings API — LLM channel management and runtime parameter configuration."""

from __future__ import annotations

import glob
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import tempfile

import duckdb as _duckdb

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.import_adapters.service import ImportAdapterService
from src.services import env_manager, settings_manager
from src.storage.gcs_flush import mark_dirty
from src.storage.gcs import upload_source_to_gcs, prune_source_blobs
from src.sources.registry import get_registry
from src.identity.authority_resolver import AuthorityResolver
from src.api.routes._errors import api_error_response
from src.api.routes.settings_llm_usage import (
    LLMUsageResponse,
    aggregate_llm_usage,
)


def _history_db_path() -> str:
    """DB path for upload-history and source-health queries.

    UIS_DB_PATH env var takes priority (Cloud Run: /tmp/data/unified.duckdb).
    Falls back to SETTINGS_PATH-relative path so tests that mock SETTINGS_PATH
    still work correctly without needing to patch resolve_db_path.
    """
    env_override = os.environ.get("UIS_DB_PATH")
    if env_override:
        return env_override
    return str(settings_manager.SETTINGS_PATH.parent.parent / "data" / "unified.duckdb")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

_KEY_SENTINEL = "••••••••"


# ------------------------------------------------------------------
# Response models
# ------------------------------------------------------------------


class ChannelResponse(BaseModel):
    name: str
    provider: str
    enabled: bool
    api_key_env: str
    key_status: str  # "configured" | "missing"
    models: List[str]


class LLMSettingsResponse(BaseModel):
    channels: List[ChannelResponse]
    primary_model: str
    fallback_models: List[str]
    temperature: float
    max_output_tokens: int


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class ChannelUpdate(BaseModel):
    name: str
    provider: str
    enabled: bool
    api_key_env: str
    api_key_value: Optional[str] = None  # None = don't touch the key; sentinel = also skip
    models: List[str]


class LLMSettingsUpdate(BaseModel):
    channels: List[ChannelUpdate]
    primary_model: str
    fallback_models: List[str]
    temperature: float
    max_output_tokens: int


class ChannelTestRequest(BaseModel):
    provider: str
    model: str
    api_key: str


class ChannelTestResponse(BaseModel):
    success: bool
    model: str
    latency_ms: Optional[int] = None
    error: Optional[str] = None


class PromptBlock(BaseModel):
    text: str
    version: int
    updated_at: Optional[str]


class PromptsResponse(BaseModel):
    shared_persona: PromptBlock
    brief_instructions: PromptBlock
    review_instructions: PromptBlock
    review_questions: PromptBlock
    using_defaults: bool  # True if settings.yaml has no prompts section


class PromptUpdateRequest(BaseModel):
    shared_persona: Optional[str] = None
    brief_instructions: Optional[str] = None
    review_instructions: Optional[str] = None
    review_questions: Optional[str] = None


class PromptPreviewRequest(BaseModel):
    prompt_type: str  # "brief" | "review" | "review_questions"
    shared_persona: Optional[str] = None  # None = use current saved value
    instructions: Optional[str] = None   # None = use current saved value; "" = intentionally blank


class PromptPreviewResponse(BaseModel):
    composed_prompt: str
    current_prompt: str
    prompt_hash: str  # SHA-256 of composed_prompt


class PromptResetRequest(BaseModel):
    keys: List[str]  # e.g. ["shared_persona", "brief_instructions"]


class SourceTestResult(BaseModel):
    reader: str
    file_found: bool
    file_path: Optional[str] = None
    is_valid: bool
    warnings: List[str] = []
    file_type: Optional[str] = None
    file_size_bytes: Optional[int] = None
    file_modified: Optional[str] = None  # ISO datetime


class LastUpdateInfo(BaseModel):
    origin: str  # "upload" | "fetch"
    at: str      # ISO-8601 UTC timestamp


class SourceConfigOut(BaseModel):
    key: str
    enabled: bool
    reader: str
    data_dir: Optional[str] = None
    file_patterns: dict = {}
    asset_prefixes: List[str] = []
    # Enriched at read time:
    resolved_dir: Optional[str] = None
    fallback_active: bool = False
    file_found: bool = False
    file_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    file_modified: Optional[str] = None
    resolved_files: dict[str, str] = {}
    # C5 additions:
    label: str = ""
    authority: str = "authoritative"  # "authoritative" | "co-authority" | "non-authoritative" | "historical-shadow"
    authority_note: Optional[str] = None
    format: str = "csv"  # "csv" | "xlsx" | "flex_csv"
    can_fetch: bool = False
    last_update: Optional[LastUpdateInfo] = None
    # ADR-023 / WS-A A3: cheap unmapped-column count for the mapping-management
    # amber chip. Computed ONLY for financial_summary (the only mapping-managed
    # reader in WS-A scope); None for every other reader, and None on any
    # failure (missing/unreadable file) — see _compute_fs_unmapped_count.
    unmapped_count: Optional[int] = None


class SourceRegistryResponse(BaseModel):
    sources: List[SourceConfigOut]
    fallback_dir: Optional[str] = None


class SourceConfigUpdate(BaseModel):
    key: str
    enabled: Optional[bool] = None
    data_dir: Optional[str] = None  # empty string = clear to null
    file_patterns: Optional[dict[str, str]] = None


class SourceRegistryUpdateRequest(BaseModel):
    sources: List[SourceConfigUpdate]


class UploadResult(BaseModel):
    reader: str
    file_path: str
    file_size_bytes: int
    is_valid: bool
    warnings: List[str] = []
    file_type: Optional[str] = None


class SourceFileEntry(BaseModel):
    filename: str
    file_path: str
    file_size_bytes: int
    file_modified: str  # ISO datetime string
    is_active: bool


class SourceFilesResponse(BaseModel):
    reader: str
    directory: str
    files: list[SourceFileEntry]
    total_count: int


class TestSourceRequest(BaseModel):
    data_dir: Optional[str] = None


class SourceHealthEntry(BaseModel):
    reader: str
    last_sync_at: Optional[str] = None
    row_count: Optional[int] = None
    net_value_cny: Optional[float] = None
    file_path: Optional[str] = None
    file_modified: Optional[str] = None
    file_size_bytes: Optional[int] = None
    file_stale: bool = False
    status: str = "unknown"  # "ok" | "stale" | "pending_sync" | "missing" | "never_synced" | "unknown"


class SourceHealthResponse(BaseModel):
    sources: List[SourceHealthEntry]
    last_sync_at: Optional[str] = None
    all_healthy: bool = True


class InvestorPhilosophy(BaseModel):
    """Structured investor philosophy / strategy document.

    All fields are optional — callers may supply any subset.
    - goal: High-level financial goal (e.g. "财务独立 2000万 RMB")
    - horizon: Target investment horizon (e.g. "10-20年")
    - risk_tolerance: Risk tolerance statement (e.g. "最大回撤30%")
    - core_weakness: Known behavioural weaknesses (e.g. "追涨杀跌")
    - portfolio_structure: Full strategic allocation narrative including tier
      labels, target ranges, and dynamic valve rules (free-text, no truncation).
    """

    goal: Optional[str] = None
    horizon: Optional[str] = None
    risk_tolerance: Optional[str] = None
    core_weakness: Optional[str] = None
    portfolio_structure: Optional[str] = None


class ProfileResponse(BaseModel):
    display_name: str
    avatar_url: Optional[str] = None
    philosophy: InvestorPhilosophy = InvestorPhilosophy()


class ProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    philosophy: Optional[InvestorPhilosophy] = None


class UploadHistoryEntry(BaseModel):
    id: int
    reader: str
    filename: str
    file_size_bytes: Optional[int]
    uploaded_at: str  # ISO datetime
    is_valid: Optional[bool]
    warnings: list[str]
    previous_filename: Optional[str]


class UploadHistoryResponse(BaseModel):
    reader: Optional[str]
    entries: list[UploadHistoryEntry]
    total_count: int


class SourceEvent(BaseModel):
    id: int
    reader: str
    origin: str  # "upload" | "fetch"
    filename: str
    file_size_bytes: Optional[int]
    occurred_at: str  # ISO-8601 UTC
    is_valid: Optional[bool]
    warnings: List[str]
    previous_filename: Optional[str]


class SourceEventsResponse(BaseModel):
    reader: Optional[str]
    events: List[SourceEvent]
    total_count: int


class FetchResult(BaseModel):
    reader: str
    file_path: str
    file_size_bytes: int
    line_count: int
    fetched_at: str  # ISO-8601 UTC
    pruned: List[str]  # filenames removed by retention (local + GCS)


class ImportAdapterConfigureRequest(BaseModel):
    run_id: int
    column_mapping: dict[str, str]
    fx_rate: Optional[float] = None


class ImportAdapterRunRequest(BaseModel):
    run_id: int


class ImportAdapterApproveRequest(BaseModel):
    source_system: str
    asset_prefixes: list[str]
    authority_priority: int
    approved_by: Optional[str] = None
    generate_reader: bool = True
    display_name: Optional[str] = None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _load_prompt_defaults() -> dict:
    from src.services.ai_advisor.prompts import (  # noqa: PLC0415
        _DEFAULT_SHARED_PERSONA_EDITABLE,
        _DEFAULT_BRIEF_INSTRUCTIONS,
        _DEFAULT_REVIEW_INSTRUCTIONS,
        _DEFAULT_REVIEW_QUESTIONS,
    )
    return {
        "shared_persona": _DEFAULT_SHARED_PERSONA_EDITABLE,
        "brief_instructions": _DEFAULT_BRIEF_INSTRUCTIONS,
        "review_instructions": _DEFAULT_REVIEW_INSTRUCTIONS,
        "review_questions": _DEFAULT_REVIEW_QUESTIONS,
    }


def _build_prompts_response(prompts_cfg: dict, using_defaults: bool) -> PromptsResponse:
    defaults = _load_prompt_defaults()

    def _block(key: str) -> PromptBlock:
        cfg = prompts_cfg.get(key, {})
        return PromptBlock(
            text=cfg.get("text", defaults[key]),
            version=cfg.get("version", 0) or 0,
            updated_at=cfg.get("updated_at"),
        )

    return PromptsResponse(
        shared_persona=_block("shared_persona"),
        brief_instructions=_block("brief_instructions"),
        review_instructions=_block("review_instructions"),
        review_questions=_block("review_questions"),
        using_defaults=using_defaults,
    )


def _build_response(settings: dict) -> LLMSettingsResponse:
    llm = settings.get("llm", {})
    raw_channels = llm.get("channels", [])
    channels = []
    for ch in raw_channels:
        api_key_env = ch.get("api_key_env", "")
        channels.append(
            ChannelResponse(
                name=ch.get("name", ""),
                provider=ch.get("provider", ""),
                enabled=ch.get("enabled", True),
                api_key_env=api_key_env,
                key_status=env_manager.get_key_status(api_key_env) if api_key_env else "missing",
                models=ch.get("models", []),
            )
        )
    return LLMSettingsResponse(
        channels=channels,
        primary_model=llm.get("primary_model", ""),
        fallback_models=llm.get("fallback_models", []),
        temperature=llm.get("temperature", 0.7),
        max_output_tokens=llm.get("max_output_tokens", 4096),
    )


# ---------------------------------------------------------------------------
# Source registry constants — derived from config/readers/*.yaml at import time.
# These keep the same names and values as before; the registry is now the
# single source of truth.
# ---------------------------------------------------------------------------
_registry = get_registry()

_KNOWN_SOURCE_READERS: list = _registry.key_known_list()

_READER_ALLOWED_EXTS: dict = _registry.allowed_extensions()

_VALIDATOR_MAP: dict = _registry.validator_map()

_READER_LABEL_MAP: dict = _registry.key_to_system()

_UPLOAD_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
_STALE_DAYS = 7

# ---------------------------------------------------------------------------
# C5 metadata helpers — authority, label, format, can_fetch
# ---------------------------------------------------------------------------

# Authority resolver singleton (reads config/source_authority.yaml)
_authority_resolver = AuthorityResolver()

# Co-authority source systems (e.g. frozenset({'Schwab_CSV', 'Broker_IBKR'}))
_COAUTHORITY_SYSTEMS: frozenset = _authority_resolver.coauthority_sources()


def _build_source_system_label_map() -> dict[str, str]:
    """Build a reverse map of source_system → display label from the registry.

    Iterates all known reader keys, calls _get_reader_meta for each, and maps
    the returned source_system to its display label.  Used by _get_authority
    to derive the co-authority peer note dynamically instead of a hardcoded string.
    """
    mapping: dict[str, str] = {}
    for reader_key in _KNOWN_SOURCE_READERS:
        label, _fmt, source_system = _get_reader_meta(reader_key)
        mapping.setdefault(source_system, label)
    return mapping

# Map reader format YAML value → spec enum (excel → xlsx)
_FORMAT_MAP: dict[str, str] = {
    "csv": "csv",
    "excel": "xlsx",
    "flex_csv": "flex_csv",
}


def _get_reader_meta(reader_key: str) -> tuple[str, str, str]:
    """Return (label, format_enum, source_system) for a reader key.

    Reads from the SourceRegistry singleton (backed by config/readers/*.yaml).
    Falls back gracefully if the key is missing.
    """
    reg = get_registry()
    cfg = reg._config_for_key(reader_key)
    if cfg is None:
        return reader_key, "csv", reader_key
    label = cfg.identity.display_name or reader_key
    raw_fmt = cfg.parsing.format if cfg.parsing else "csv"
    fmt = _FORMAT_MAP.get(raw_fmt, raw_fmt)
    return label, fmt, cfg.identity.source_system


def _get_authority(source_system: str) -> tuple[str, Optional[str]]:
    """Return (authority_enum, authority_note) for a source_system.

    Co-authority: source_system in the co-authority set → 'co-authority'
    Otherwise: 'authoritative'
    'historical-shadow' and 'non-authoritative' are reserved for future use.

    The co-authority note is derived dynamically from the registry: peer labels
    are looked up via _build_source_system_label_map() so no hardcoded strings
    are needed here.
    """
    if source_system in _COAUTHORITY_SYSTEMS:
        sys_label_map = _build_source_system_label_map()
        peers = _COAUTHORITY_SYSTEMS - {source_system}
        peer_labels = [sys_label_map.get(p, p) for p in sorted(peers)]
        if peer_labels:
            note = f"Co-authority with {', '.join(sorted(peer_labels))}"
        else:
            note = "Co-authority"
        return "co-authority", note
    return "authoritative", None


def _get_last_updates_all() -> dict[str, "LastUpdateInfo"]:
    """Return the most recent event (upload|fetch) per reader, keyed by reader.

    Single connection + single query (window function) for ALL readers — avoids
    opening one DuckDB connection per reader on every GET /sources, which also
    cuts read-lock contention with concurrent upload/fetch writes.
    Non-fatal — returns {} on any error (callers treat a missing key as no update).
    """
    result: dict[str, LastUpdateInfo] = {}
    try:
        with _duckdb.connect(_history_db_path(), read_only=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name='source_upload_history'"
            ).fetchone()
            if not exists:
                return result
            cols = conn.execute("PRAGMA table_info('source_upload_history')").fetchall()
            # origin_expr is a column name or a literal — never user input
            origin_expr = "origin" if any(row[1] == "origin" for row in cols) else "'upload'"
            inner = (
                f"SELECT reader, {origin_expr} AS origin, uploaded_at, "
                "ROW_NUMBER() OVER (PARTITION BY reader ORDER BY uploaded_at DESC) AS rn "
                "FROM source_upload_history"
            )
            rows = conn.execute(
                f"SELECT reader, origin, uploaded_at FROM ({inner}) WHERE rn = 1"
            ).fetchall()
            for r in rows:
                at_val = r[2]
                at_str = at_val.isoformat() if hasattr(at_val, "isoformat") else str(at_val)
                result[r[0]] = LastUpdateInfo(origin=r[1] or "upload", at=at_str)
    except Exception as exc:
        logger.debug("Could not batch-fetch last_update: %s", exc)
    return result


def prune_source_files(reader: str, data_dir: str, keep: int = 3) -> list[str]:
    """Prune old source files for a reader in data_dir, keeping the newest ``keep`` files.

    Policy per file pattern:
    - Scans data_dir for files matching the reader's configured file_patterns.
    - Sorts matches by mtime descending (newest first).
    - Keeps the first ``keep`` files; deletes older ones.
    - NEVER deletes the single newest file (index 0 is always protected).
    - Also prunes ``*.bak.<ts>`` files to keep the latest ``keep`` per base filename.

    Returns:
        List of filenames (basenames) that were deleted.
    """
    settings = settings_manager.load_settings()
    reader_cfg = settings.get("source_registry", {}).get(reader, {})
    patterns: dict = reader_cfg.get("file_patterns", {})

    deleted: list[str] = []
    dir_path = Path(data_dir)
    if not dir_path.is_dir():
        return deleted

    # Prune per-pattern source files
    for _key, pat_glob in patterns.items():
        matches = list(dir_path.glob(pat_glob))
        # Filter to actual files only
        matches = [m for m in matches if m.is_file()]
        if len(matches) <= keep:
            continue
        # Sort newest first by mtime
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        to_delete = matches[keep:]
        for path in to_delete:
            try:
                path.unlink()
                logger.info("Retention: pruned source file %s", path)
                deleted.append(path.name)
            except OSError as exc:
                logger.warning("Retention: could not delete %s: %s", path, exc)

    # Prune .bak.<ts> backup files — keep latest ``keep`` per base filename.
    # Scope to THIS reader's files only: readers can share a data_dir (Schwab and
    # IBKR both live in the Finance dir), so a dir-wide "*.bak.*" glob would delete
    # another reader's backups. A backup belongs to this reader iff its base name
    # (everything before ".bak.") matches one of the reader's configured globs.
    import fnmatch  # noqa: PLC0415
    pattern_globs = list(patterns.values())
    bak_by_base: dict[str, list[Path]] = {}
    for bak in dir_path.glob("*.bak.*"):
        if not bak.is_file():
            continue
        # base = everything before the first .bak. segment
        base = bak.name.split(".bak.")[0]
        if not any(fnmatch.fnmatch(base, g) for g in pattern_globs):
            continue  # backup belongs to a different reader sharing this dir
        bak_by_base.setdefault(base, []).append(bak)

    for base, bak_files in bak_by_base.items():
        if len(bak_files) <= keep:
            continue
        bak_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for bak in bak_files[keep:]:
            try:
                bak.unlink()
                logger.info("Retention: pruned backup file %s", bak)
                deleted.append(bak.name)
            except OSError as exc:
                logger.warning("Retention: could not delete backup %s: %s", bak, exc)

    return deleted


def _resolve_source_file(settings: dict, reader_name: str) -> tuple[str, str, bool, dict[str, str]]:
    """Resolve the data directory and latest matching file for a source reader.

    Re-implements the path resolution logic from
    src/validation/run_reader_validation._find_source_file() without importing
    that module (it has heavyweight dependencies).

    Returns:
        (resolved_dir, file_path, fallback_active, resolved_files)
        - resolved_dir: the directory used for glob
        - file_path: path of the latest matching file for the PRIMARY pattern, or "" if none found
        - fallback_active: True if data_dir was null and a fallback was used
        - resolved_files: dict mapping pattern_key -> resolved file path for each pattern (sparse: only matched keys)
    """
    registry = settings.get("source_registry", {})
    reader_cfg = registry.get(reader_name, {})
    data_dir = reader_cfg.get("data_dir") or ""
    fallback_active = False

    finance_dir_override = os.environ.get("UIS_FINANCE_DIR")
    if finance_dir_override:
        data_dir = str(Path(finance_dir_override) / reader_name)
    elif not data_dir:
        fallback_active = True
        data_dir = settings.get("finance_dir", "")

    patterns = reader_cfg.get("file_patterns", {})
    file_path = ""
    resolved_files: dict[str, str] = {}
    if patterns and data_dir:
        for pat_key, pat_glob in patterns.items():
            matches = glob.glob(str(Path(data_dir) / pat_glob))
            if matches:
                matched = str(sorted(matches)[-1])
                resolved_files[pat_key] = matched
                if not file_path:
                    file_path = matched  # primary = first match found

    return data_dir or "", file_path, fallback_active, resolved_files


def _compute_fs_unmapped_count(settings: dict, db: "DatabaseConnector | None") -> Optional[int]:
    """ADR-023 / WS-A A3 — cheap unmapped-column count for the financial_summary
    source row's amber chip on the Data Sources page.

    Reuses the same column-scan heuristic as the mappings preview endpoint
    (src.services.reader_mappings.scan_unmapped_columns) against the reader's
    currently resolved uploaded file. Only columns the scan classifies as
    `category='candidate'` are counted — `native`/`computed`/`liability`/
    `ignored` columns are "not melted by design" (ADR-023 A4.1) and would
    otherwise inflate the chip with totals, ratios, liability rows, and
    reader-covered informational duplicates. Returns None whenever the count
    cannot be cheaply computed (no db handle, file missing/unreadable, any
    parse error) — this must NEVER raise or block the main sources list.
    """
    if db is None:
        return None
    try:
        import pandas as pd  # noqa: PLC0415 — lazy: only this one FS check needs it
        from src.services.reader_mappings import (
            get_ignored_map_keys,
            load_reader_mappings,
            scan_unmapped_columns,
        )

        _resolved_dir, file_path, _fallback, _resolved_files = _resolve_source_file(
            settings, "financial_summary"
        )
        if not file_path or not Path(file_path).exists():
            return None
        sheet_df = pd.read_excel(file_path, sheet_name="资产负债", header=3, engine="openpyxl")
        merged = load_reader_mappings(db, "financial_summary", "fs_column")
        ignored_keys = get_ignored_map_keys(db, "financial_summary", "fs_column")
        scanned = scan_unmapped_columns(list(sheet_df.columns), merged, ignored_keys=ignored_keys)
        # Only 'candidate' columns are genuinely actionable — native/computed/
        # liability/ignored are "not melted by design" (ADR-023 A4.1).
        return sum(1 for c in scanned if c["category"] == "candidate")
    except Exception as e:
        logger.debug("FS unmapped-column scan failed (non-blocking): %s", e)
        return None


# reader_key -> path to its declarative YAML config (ADR-023 WS-B —
# id_field_map readers: gold/insurance/rsu). Mirrors
# src.api.routes.reader_mappings._READER_YAML_PATH.
_ID_FIELD_MAP_READER_YAML: dict[str, str] = {
    "gold": "config/readers/gold.yaml",
    "insurance": "config/readers/insurance.yaml",
    "rsu": "config/readers/rsu.yaml",
}


def _compute_id_field_map_unmapped_count(
    settings: dict, db: "DatabaseConnector | None", reader_key: str
) -> Optional[int]:
    """ADR-023 WS-B — cheap unmapped-label count for the gold/insurance/rsu
    source rows' amber chip on the Data Sources page.

    Generalizes _compute_fs_unmapped_count's fail-safe-to-None contract to
    the id_field_map kind: scans the reader's currently uploaded file for
    id-source label values (src.api.routes.reader_mappings._extract_field_labels)
    and counts labels not present in the merged (defaults + DB overrides)
    id_field_map. Returns None whenever the count cannot be cheaply computed
    (no db handle, file missing/unreadable, any parse error) — this must
    NEVER raise or block the main sources list.
    """
    if db is None:
        return None
    try:
        from src.api.routes.reader_mappings import _extract_field_labels, _load_reader_cfg
        from src.services.reader_mappings import load_reader_mappings, scan_unmapped_id_field_map_labels

        _resolved_dir, file_path, _fallback, _resolved_files = _resolve_source_file(settings, reader_key)
        if not file_path or not Path(file_path).exists():
            return None
        reader_cfg = _load_reader_cfg(reader_key)
        field_labels = _extract_field_labels(reader_cfg, file_path)
        merged = load_reader_mappings(db, reader_key, "id_field_map")
        scanned = scan_unmapped_id_field_map_labels(field_labels, merged)
        return sum(1 for item in scanned if not item["mapped"])
    except Exception as e:
        logger.debug("%s unmapped-label scan failed (non-blocking): %s", reader_key, e)
        return None


def _compute_vocab_unmapped_count(
    settings: dict, db: "DatabaseConnector | None", reader_key: str, kind: str
) -> Optional[int]:
    """ADR-023 WS-C — cheap unmapped-value count for the schwab/cn_fund
    source rows' amber chip. Counts ONLY action_map/type_map candidates (an
    unmapped raw action / 操作类型 label melts to 'other' — a genuine gap);
    known_etf/symbol_norm gaps are deliberately not chip-counted (an
    unmapped symbol is the normal case — the A4.1 cries-wolf lesson).
    Fail-safe None on any failure — never blocks the sources list.
    """
    if db is None:
        return None
    try:
        from src.api.routes.reader_mappings import _vocab_file_values
        from src.services.reader_mappings import load_reader_mappings, scan_unmapped_vocab_values

        values = _vocab_file_values(reader_key, kind)
        if values is None:
            return None
        merged = load_reader_mappings(db, reader_key, kind)
        scanned = scan_unmapped_vocab_values(values, merged, kind)
        return sum(1 for item in scanned if not item["mapped"])
    except Exception as e:
        logger.debug("%s/%s vocab unmapped scan failed (non-blocking): %s", reader_key, kind, e)
        return None


def _compute_unmapped_count(settings: dict, db: "DatabaseConnector | None", reader_key: str) -> Optional[int]:
    """Dispatch to the right unmapped-count scan for a mapping-managed reader
    (fs_column: financial_summary; id_field_map: gold/insurance/rsu; WS-C
    vocab: schwab action_map, cn_fund type_map), else None. ibkr shares
    schwab's vocabularies (co-authority) and deliberately has no chip."""
    if reader_key == "financial_summary":
        return _compute_fs_unmapped_count(settings, db)
    if reader_key in _ID_FIELD_MAP_READER_YAML:
        return _compute_id_field_map_unmapped_count(settings, db, reader_key)
    if reader_key == "schwab":
        return _compute_vocab_unmapped_count(settings, db, "schwab", "action_map")
    if reader_key == "cn_fund":
        return _compute_vocab_unmapped_count(settings, db, "cn_fund", "type_map")
    return None


def _build_source_registry_response(
    settings: dict, db: "DatabaseConnector | None" = None
) -> SourceRegistryResponse:
    """Build SourceRegistryResponse from settings dict with enriched file status.

    `db` is optional (defaults to None → unmapped_count stays None everywhere)
    so existing non-GET call sites (e.g. update_sources' PUT response) are
    unaffected; only the GET /sources route passes a live read-only handle.
    """
    registry = settings.get("source_registry", {})
    fallback_dir = settings.get("finance_dir")
    sources: list[SourceConfigOut] = []

    from src.fetchers.registry import can_fetch as _can_fetch  # noqa: PLC0415

    last_updates = _get_last_updates_all()  # one query for all readers (not per-reader)

    for key in _KNOWN_SOURCE_READERS:
        cfg = registry.get(key, {})
        resolved_dir, file_path, fallback_active, resolved_files = _resolve_source_file(settings, key)

        file_found = False
        file_size_bytes = None
        file_modified = None
        if file_path and Path(file_path).exists():
            file_found = True
            stat = Path(file_path).stat()
            file_size_bytes = stat.st_size
            file_modified = datetime.fromtimestamp(stat.st_mtime).isoformat()

        # C5: derive label, format, authority, can_fetch, last_update
        label, fmt, source_system = _get_reader_meta(key)
        authority, authority_note = _get_authority(source_system)
        can_fetch_flag = _can_fetch(key)
        last_update = last_updates.get(key)
        unmapped_count = _compute_unmapped_count(settings, db, key)

        sources.append(
            SourceConfigOut(
                key=key,
                enabled=cfg.get("enabled", True),
                reader=cfg.get("reader", ""),
                data_dir=cfg.get("data_dir") or None,
                file_patterns=cfg.get("file_patterns", {}),
                asset_prefixes=cfg.get("asset_prefixes", []),
                resolved_dir=resolved_dir or None,
                fallback_active=fallback_active,
                file_found=file_found,
                file_path=file_path or None,
                file_size_bytes=file_size_bytes,
                file_modified=file_modified,
                resolved_files=resolved_files,
                # C5 additions:
                label=label,
                authority=authority,
                authority_note=authority_note,
                format=fmt,
                can_fetch=can_fetch_flag,
                last_update=last_update,
                unmapped_count=unmapped_count,
            )
        )

    return SourceRegistryResponse(sources=sources, fallback_dir=fallback_dir)


def _validate_file_at_path(reader: str, path: Path) -> tuple:
    """Return (is_valid, warnings, file_type) for a resolved source file path."""
    validator_name = _VALIDATOR_MAP.get(reader)
    if validator_name:
        try:
            from src.validation import source_format_validator  # noqa: PLC0415

            validator = getattr(source_format_validator, validator_name)
            result = validator(path)
            return result.is_valid, result.warnings, result.file_type
        except Exception as e:
            logger.warning("%s format validation failed: %s", reader, e)
            return False, [f"Validation error: {e}"], None

    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        return True, [], None
    return False, [f"Unexpected file extension: {ext!r}"], None


def _ensure_upload_history_table(conn) -> None:
    """Create source_upload_history table + sequence if they don't exist.

    C5 migration: idempotently adds the ``origin`` column (default 'upload')
    to existing tables that pre-date C5.  Existing rows default to 'upload'.
    """
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
            previous_filename VARCHAR,
            origin VARCHAR DEFAULT 'upload'
        )
    """)
    # Idempotent migration: add origin column if it doesn't exist (pre-C5 tables).
    # Use PRAGMA table_info to check; DuckDB does not support IF NOT EXISTS for ALTER COLUMN.
    try:
        cols = conn.execute("PRAGMA table_info('source_upload_history')").fetchall()
        col_names = {row[1] for row in cols}
        if "origin" not in col_names:
            conn.execute("ALTER TABLE source_upload_history ADD COLUMN origin VARCHAR DEFAULT 'upload'")
    except Exception as exc:
        # Surface (don't swallow): a failed origin migration breaks the events feed,
        # which would otherwise show an empty feed with no error.
        logger.warning(
            "source_upload_history origin-column migration failed: %s — "
            "events feed (origin) may be unavailable until resolved.", exc
        )


def _parse_warnings(val) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    try:
        import json as _json
        return _json.loads(val)
    except Exception:
        return []


def _row_to_history_entry(row) -> "UploadHistoryEntry":
    return UploadHistoryEntry(
        id=row[0], reader=row[1], filename=row[2],
        file_size_bytes=row[3],
        uploaded_at=row[4].isoformat() if hasattr(row[4], 'isoformat') else str(row[4]),
        is_valid=row[5],
        warnings=_parse_warnings(row[6]),
        previous_filename=row[7],
    )


def _row_to_source_event(row) -> "SourceEvent":
    """Convert a DB row (id, reader, filename, file_size_bytes, uploaded_at,
    is_valid, warnings, previous_filename, origin) to SourceEvent."""
    occurred_raw = row[4]
    occurred_at = occurred_raw.isoformat() if hasattr(occurred_raw, 'isoformat') else str(occurred_raw)
    origin = row[8] if len(row) > 8 and row[8] else "upload"
    return SourceEvent(
        id=row[0],
        reader=row[1],
        origin=origin,
        filename=row[2],
        file_size_bytes=row[3],
        occurred_at=occurred_at,
        is_valid=row[5],
        warnings=_parse_warnings(row[6]),
        previous_filename=row[7],
    )


def _validate_data_dir(data_dir: str) -> str:
    """Validate and canonicalize a user-supplied data_dir path.

    Rejects paths that are:
    - Not absolute (checked on raw input before resolve)
    - Contain traversal components (..)
    - Do not resolve to an existing directory

    Returns the canonicalized absolute path string, or raises HTTPException(422).
    """
    raw = Path(data_dir).expanduser()
    if not raw.is_absolute():
        raise HTTPException(status_code=422, detail="data_dir must be an absolute path")
    if ".." in raw.parts:
        raise HTTPException(status_code=422, detail="Path traversal not allowed")
    if os.environ.get("UIS_GCS_BUCKET") and str(raw).startswith("/tmp/sources/"):
        Path(raw).mkdir(parents=True, exist_ok=True)
        return str(Path(raw).resolve())
    try:
        p = raw.resolve(strict=True)
    except FileNotFoundError:
        raise HTTPException(status_code=422, detail=f"data_dir does not exist: {raw}")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid path: {e}")
    if not p.is_dir():
        raise HTTPException(status_code=422, detail=f"data_dir is not a directory: {p}")
    return str(p)


def _validate_file_patterns(merged: dict[str, str]) -> list[str]:
    errors = []
    if len(merged) > 5:
        errors.append(f"Max 5 patterns allowed, got {len(merged)}")
    for key, val in merged.items():
        if not key or not isinstance(key, str) or not key.strip():
            errors.append(f"Invalid pattern key: {key!r}")
        if not val or not isinstance(val, str) or not val.strip():
            errors.append(f"Invalid or empty pattern value for key {key!r}")
        elif "/" in val or "\\" in val or ".." in val or val.startswith("~") or val.startswith("/"):
            errors.append(f"Pattern value must not contain path separators or be absolute: {val!r}")
    return errors


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/llm", response_model=LLMSettingsResponse)
def get_llm_settings() -> LLMSettingsResponse:
    """Return LLM channel config with key status (never returns raw keys)."""
    settings = settings_manager.load_settings()
    return _build_response(settings)


@router.put("/llm", response_model=LLMSettingsResponse)
def update_llm_settings(body: LLMSettingsUpdate) -> LLMSettingsResponse:
    """Save channel config and runtime params. API keys stored in .env, never in YAML."""
    # 1. Write new API keys to .env where provided
    for ch in body.channels:
        val = ch.api_key_value
        if val and val != _KEY_SENTINEL and ch.api_key_env:
            env_manager.update_key(ch.api_key_env, val)

    # 2. Build channels list without the api_key_value field for YAML storage
    channels_for_yaml = [
        {
            "name": ch.name,
            "provider": ch.provider,
            "enabled": ch.enabled,
            "api_key_env": ch.api_key_env,
            "models": ch.models,
        }
        for ch in body.channels
    ]

    # 3. Load current settings and update the llm section
    settings = settings_manager.load_settings()
    settings["llm"] = {
        "channels": channels_for_yaml,
        "primary_model": body.primary_model,
        "fallback_models": body.fallback_models,
        "temperature": body.temperature,
        "max_output_tokens": body.max_output_tokens,
    }
    settings_manager.save_settings(settings)
    mark_dirty()

    # 4. Return freshly loaded response (confirms actual written state)
    return _build_response(settings_manager.load_settings())


@router.post("/llm/test", response_model=ChannelTestResponse)
def test_channel(body: ChannelTestRequest) -> ChannelTestResponse:
    """Test a single LLM channel. The api_key is used only for this call and never stored."""
    full_model = f"{body.provider}/{body.model}"
    start = time.monotonic()
    try:
        # Direct litellm call is intentional: this endpoint tests a user-supplied API key
        # against a specific provider/model, which LLMClient cannot do (it uses configured keys).
        # See AGENTS.md Rule 21 — this is a documented exception.
        import litellm  # noqa: PLC0415, LLMClient-bypass
        litellm.completion(
            model=full_model,
            messages=[{"role": "user", "content": "Respond with OK"}],
            max_tokens=5,
            api_key=body.api_key,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        return ChannelTestResponse(success=True, model=full_model, latency_ms=latency_ms)
    except Exception as e:
        latency_ms = int((time.monotonic() - start) * 1000)
        return ChannelTestResponse(
            success=False,
            model=full_model,
            latency_ms=latency_ms,
            error=str(e)[:200],
        )


@router.get("/llm/usage", response_model=LLMUsageResponse)
def get_llm_usage() -> LLMUsageResponse:
    """Aggregate llm_usage table by model. Empty table → valid empty response, NOT an error.

    On a real DB/query exception, returns a Rule-12-compliant 5xx error.
    """
    try:
        return aggregate_llm_usage()
    except Exception as e:
        logger.exception("get_llm_usage failed")
        return api_error_response(e, context="get_llm_usage")


@router.get("/prompts", response_model=PromptsResponse)
def get_prompts():
    """Return current prompt blocks from settings.yaml (or defaults if not configured)."""
    settings = settings_manager.load_settings()
    prompts_cfg = settings.get("prompts", {})
    if not prompts_cfg:
        using_defaults = True
    else:
        # using_defaults only if all 4 blocks are at version 0 (never customized)
        using_defaults = all(
            prompts_cfg.get(key, {}).get("version", 0) == 0
            for key in ("shared_persona", "brief_instructions", "review_instructions", "review_questions")
        )
    return _build_prompts_response(prompts_cfg, using_defaults)


@router.put("/prompts", response_model=PromptsResponse)
def update_prompts(req: PromptUpdateRequest):
    """Save prompt block updates. Only keys present (and non-null) are updated."""
    updates = {
        k: v for k, v in req.model_dump().items() if v is not None
    }
    try:
        saved = settings_manager.save_prompts(updates)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    using_defaults = all(
        saved.get(key, {}).get("version", 0) == 0
        for key in ("shared_persona", "brief_instructions", "review_instructions", "review_questions")
    )
    mark_dirty()
    return _build_prompts_response(saved, using_defaults=using_defaults)


@router.post("/prompts/preview", response_model=PromptPreviewResponse)
def preview_prompt(req: PromptPreviewRequest):
    """Compose the full system prompt from draft edits, for preview/diff."""
    MAX_CHARS = 10_000
    for field_name, value in [("shared_persona", req.shared_persona), ("instructions", req.instructions)]:
        if value is not None and len(value) > MAX_CHARS:
            raise HTTPException(
                status_code=422,
                detail=f"Field '{field_name}' exceeds maximum length of {MAX_CHARS} characters",
            )

    import hashlib  # noqa: PLC0415
    from src.services.ai_advisor.prompts import (  # noqa: PLC0415
        compose_brief_prompt,
        compose_review_prompt,
        compose_review_questions_prompt,
        get_brief_system_prompt,
        get_review_system_prompt,
        get_review_questions_system_prompt,
    )

    settings = settings_manager.load_settings()
    prompts_cfg = settings.get("prompts", {})
    defaults = _load_prompt_defaults()

    def _current_text(key: str) -> str:
        return prompts_cfg.get(key, {}).get("text", defaults[key])

    if req.prompt_type == "brief":
        persona = req.shared_persona if req.shared_persona is not None else _current_text("shared_persona")
        instructions = req.instructions if req.instructions is not None else _current_text("brief_instructions")
        composed = compose_brief_prompt(persona, instructions)
        current = get_brief_system_prompt()
    elif req.prompt_type == "review":
        persona = req.shared_persona if req.shared_persona is not None else _current_text("shared_persona")
        instructions = req.instructions if req.instructions is not None else _current_text("review_instructions")
        composed = compose_review_prompt(persona, instructions)
        current = get_review_system_prompt()
    elif req.prompt_type == "review_questions":
        text = req.instructions if req.instructions is not None else _current_text("review_questions")
        composed = compose_review_questions_prompt(text)
        current = get_review_questions_system_prompt()
    else:
        raise HTTPException(status_code=422, detail=f"Unknown prompt_type: {req.prompt_type!r}")

    prompt_hash = hashlib.sha256(composed.encode()).hexdigest()
    return PromptPreviewResponse(
        composed_prompt=composed,
        current_prompt=current,
        prompt_hash=prompt_hash,
    )


@router.post("/prompts/reset", response_model=PromptsResponse)
def reset_prompts(req: PromptResetRequest):
    """Reset specified prompt blocks to hardcoded defaults (version increments)."""
    try:
        saved = settings_manager.save_prompts({}, reset_keys=req.keys)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    using_defaults = all(
        saved.get(key, {}).get("version", 0) == 0
        for key in ("shared_persona", "brief_instructions", "review_instructions", "review_questions")
    )
    mark_dirty()
    return _build_prompts_response(saved, using_defaults=using_defaults)


# ------------------------------------------------------------------
# Source Registry endpoints
# ------------------------------------------------------------------


@router.get("/sources", response_model=SourceRegistryResponse)
def get_sources(db: DatabaseConnector = Depends(get_db)):
    """Return source_registry with enriched file status (resolved dir, file found, size, mtime).

    `db` (read-only) is used only for the financial_summary `unmapped_count`
    chip (ADR-023 / WS-A A3) — see _compute_fs_unmapped_count.
    """
    settings = settings_manager.load_settings()
    return _build_source_registry_response(settings, db=db)


@router.put("/sources", response_model=SourceRegistryResponse)
def update_sources(req: SourceRegistryUpdateRequest):
    """Update source configs (data_dir, enabled, file_patterns). Only existing sources are modified."""
    updates: dict = {}
    settings = settings_manager.load_settings()
    for s in req.sources:
        if s.key not in _KNOWN_SOURCE_READERS:
            raise HTTPException(status_code=404, detail=f"Unknown reader: {s.key}")
        fields: dict = {}
        if s.enabled is not None:
            fields["enabled"] = s.enabled
        # Handle data_dir: empty string means clear to null
        if s.data_dir is not None:
            if s.data_dir == "":
                fields["data_dir"] = None
            else:
                fields["data_dir"] = _validate_data_dir(s.data_dir)
        if s.file_patterns is not None:
            existing_patterns = settings.get("source_registry", {}).get(s.key, {}).get("file_patterns", {})
            merged_patterns = {**existing_patterns, **s.file_patterns}
            errs = _validate_file_patterns(merged_patterns)
            if errs:
                raise HTTPException(status_code=422, detail=errs)
            fields["file_patterns"] = merged_patterns
        if fields:
            updates[s.key] = fields

    try:
        settings_manager.save_source_registry(updates)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error saving source registry")
        raise HTTPException(status_code=500, detail=f"Could not save source registry: {e}")

    settings = settings_manager.load_settings()
    mark_dirty()
    return _build_source_registry_response(settings)


@router.post("/sources/test/{reader}", response_model=SourceTestResult)
def test_source(reader: str, req: Optional[TestSourceRequest] = Body(default=None)):
    """Test whether a source file exists and is in a valid format.

    Optionally accepts { data_dir } in the request body to test a directory that hasn't
    been saved yet — useful for validating a draft path before saving.
    """
    if reader not in _KNOWN_SOURCE_READERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown reader '{reader}'. Must be one of: {_KNOWN_SOURCE_READERS}",
        )

    settings = settings_manager.load_settings()

    if req and req.data_dir:
        # Test with the provided data_dir override (draft, not yet saved to YAML)
        validated_dir = _validate_data_dir(req.data_dir)
        registry = settings.get("source_registry", {})
        patterns = registry.get(reader, {}).get("file_patterns", {})
        file_path = ""
        for pat_glob in patterns.values():
            matches = glob.glob(str(Path(validated_dir) / pat_glob))
            if matches:
                candidate = str(sorted(matches)[-1])
                if not file_path:
                    file_path = candidate
    else:
        _resolved_dir, file_path, _fallback_active, _ = _resolve_source_file(settings, reader)

    file_found = bool(file_path) and Path(file_path).exists()
    file_size_bytes: Optional[int] = None
    file_modified: Optional[str] = None
    is_valid = False
    warnings: List[str] = []
    file_type: Optional[str] = None

    if not file_found:
        warnings = ["File not found"]
        return SourceTestResult(
            reader=reader,
            file_found=False,
            file_path=file_path or None,
            is_valid=False,
            warnings=warnings,
        )

    stat = Path(file_path).stat()
    file_size_bytes = stat.st_size
    file_modified = datetime.fromtimestamp(stat.st_mtime).isoformat()

    is_valid, warnings, file_type = _validate_file_at_path(reader, Path(file_path))

    return SourceTestResult(
        reader=reader,
        file_found=True,
        file_path=file_path,
        is_valid=is_valid,
        warnings=warnings,
        file_type=file_type,
        file_size_bytes=file_size_bytes,
        file_modified=file_modified,
    )


@router.post("/sources/test-all", response_model=List[SourceTestResult])
def test_all_sources():
    """Test all 6 known source readers. Returns results for each, regardless of individual failures."""
    settings = settings_manager.load_settings()
    results: List[SourceTestResult] = []
    for reader in _KNOWN_SOURCE_READERS:
        try:
            _resolved_dir, file_path, _fallback_active, _ = _resolve_source_file(settings, reader)
            file_found = bool(file_path) and Path(file_path).exists()
            if not file_found:
                results.append(SourceTestResult(
                    reader=reader,
                    file_found=False,
                    file_path=file_path or None,
                    is_valid=False,
                    warnings=["File not found"],
                ))
                continue
            stat = Path(file_path).stat()
            file_size_bytes = stat.st_size
            file_modified = datetime.fromtimestamp(stat.st_mtime).isoformat()
            is_valid, warnings, file_type = _validate_file_at_path(reader, Path(file_path))
            results.append(SourceTestResult(
                reader=reader,
                file_found=True,
                file_path=file_path,
                is_valid=is_valid,
                warnings=warnings,
                file_type=file_type,
                file_size_bytes=file_size_bytes,
                file_modified=file_modified,
            ))
        except Exception as e:
            logger.exception("Unexpected error testing source %r", reader)
            results.append(SourceTestResult(
                reader=reader,
                file_found=False,
                file_path=None,
                is_valid=False,
                warnings=[f"Unexpected error: {e}"],
            ))
    return results


@router.post("/sources/upload/{reader}", response_model=UploadResult)
def upload_source_file(reader: str, file: UploadFile = File(...)):
    """Upload a source data file for a reader. Saves to configured directory, auto-validates."""
    if reader not in _KNOWN_SOURCE_READERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown reader '{reader}'. Must be one of: {_KNOWN_SOURCE_READERS}",
        )

    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    allowed = _READER_ALLOWED_EXTS[reader]
    if ext not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid file type for '{reader}': got {ext!r}, expected one of {sorted(allowed)}",
        )

    settings = settings_manager.load_settings()
    resolved_dir, _, _, _ = _resolve_source_file(settings, reader)
    if not resolved_dir:
        raise HTTPException(
            status_code=422,
            detail=f"No directory configured for reader '{reader}'. Set data_dir first.",
        )
    dest_dir = Path(resolved_dir)
    if not dest_dir.is_dir():
        if os.environ.get("UIS_GCS_BUCKET") and str(dest_dir).startswith("/tmp/"):
            dest_dir.mkdir(parents=True, exist_ok=True)
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Configured directory does not exist: {resolved_dir}",
            )

    # Derive a safe destination filename from upload filename (strip path components)
    safe_name = Path(filename).name
    if not safe_name:
        raise HTTPException(status_code=422, detail="Could not determine filename from upload")

    bucket = os.getenv("UIS_GCS_BUCKET")

    # Write to a temp file in the DESTINATION directory (same filesystem → atomic rename)
    tmp_path: Optional[str] = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=dest_dir, suffix=".upload.tmp")
        total_bytes = 0
        try:
            with os.fdopen(tmp_fd, "wb") as tmp_f:
                while True:
                    chunk = file.file.read(64 * 1024)  # 64 KB chunks, sync read
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > _UPLOAD_MAX_BYTES:
                        raise HTTPException(status_code=413, detail="File exceeds 50 MB limit")
                    tmp_f.write(chunk)
                tmp_f.flush()
                os.fsync(tmp_f.fileno())
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Error reading upload for reader %r", reader)
            raise HTTPException(status_code=500, detail="Error reading upload. Check server logs.") from e

        # Atomic publish first (validation is informational — file is always placed)
        dest_path = dest_dir / safe_name
        pre_warnings: List[str] = []

        # Backup existing file before overwrite
        previous_filename: Optional[str] = None
        backup_path: Optional[Path] = None
        if dest_path.exists():
            previous_filename = dest_path.name  # Set here, before backup attempt
            timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
            backup_path = dest_dir / f"{dest_path.name}.bak.{timestamp}"
            try:
                shutil.copy2(str(dest_path), str(backup_path))
            except OSError as e:
                if bucket:
                    raise HTTPException(status_code=500, detail="Could not create backup for transactional upload") from e
                logger.warning("Failed to backup %s before overwrite: %s", dest_path, e)
                pre_warnings = pre_warnings + [f"Backup failed: {e}"]
                # Non-fatal — upload proceeds; failure surfaced in warnings

        os.replace(tmp_path, dest_path)
        tmp_path = None  # prevent cleanup

        if bucket:
            try:
                upload_source_to_gcs(bucket, reader, str(dest_path))
            except Exception as e:
                logger.warning("Failed to upload source file %s for %r to GCS: %s", dest_path, reader, e)
                try:
                    if backup_path and backup_path.exists():
                        os.replace(str(backup_path), str(dest_path))
                    elif dest_path.exists():
                        os.unlink(dest_path)
                except OSError as rollback_error:
                    logger.error("Rollback failed for %s after GCS upload error: %s", dest_path, rollback_error)
                return JSONResponse(
                    status_code=503,
                    content={"error": "GCS upload failed; source not committed"},
                )

        # Validate on final path (correct filename — important for Schwab type detection)
        is_valid, val_warnings, file_type = _validate_file_at_path(reader, dest_path)
        warnings: List[str] = pre_warnings + list(val_warnings)

        # Check whether filename matches any configured file_pattern (warn if not)
        reader_cfg = settings.get("source_registry", {}).get(reader, {})
        patterns = reader_cfg.get("file_patterns", {})
        if patterns:
            import fnmatch  # noqa: PLC0415
            pattern_list = list(patterns.values()) if isinstance(patterns, dict) else patterns
            if not any(fnmatch.fnmatch(safe_name, p) for p in pattern_list):
                warnings = list(warnings) + [
                    f"Filename '{safe_name}' does not match configured pattern(s) {pattern_list}. "
                    "File may not be found on next sync."
                ]

        # Record upload event in DuckDB with origin='upload' (non-fatal if DB unavailable)
        try:
            import json as _json  # noqa: PLC0415
            with _duckdb.connect(_history_db_path()) as conn:
                _ensure_upload_history_table(conn)
                conn.execute(
                    """INSERT INTO source_upload_history
                       (reader, filename, file_size_bytes, uploaded_at, is_valid, warnings, previous_filename, origin)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    [reader, safe_name, total_bytes, datetime.now(), is_valid,
                     _json.dumps(list(warnings)), previous_filename, "upload"]
                )
        except _duckdb.IOException as e:
            logger.warning("Failed to record upload history for %r (DB lock contention — concurrent upload?): %s", reader, e)
            # Non-fatal — upload already succeeded
        except Exception as e:
            logger.warning("Failed to record upload history for %r: %s", reader, e)
            # Non-fatal — upload already succeeded

        # C5 retention: prune source files + GCS blobs (non-fatal)
        try:
            prune_source_files(reader, str(dest_dir))
        except Exception as e:
            logger.warning("Retention: local prune failed for %r: %s", reader, e)
        if bucket:
            try:
                prune_source_blobs(bucket, reader)
            except Exception as e:
                logger.warning("Retention: GCS prune failed for %r: %s", reader, e)

        mark_dirty()
        return UploadResult(
            reader=reader,
            file_path=str(dest_path),
            file_size_bytes=total_bytes,
            is_valid=is_valid,
            warnings=warnings,
            file_type=file_type,
        )
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@router.get("/sources/files/{reader}")
async def get_source_files(reader: str) -> SourceFilesResponse:
    """List all matching files in the resolved data directory for a reader."""
    if reader not in _KNOWN_SOURCE_READERS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown reader '{reader}'. Must be one of: {_KNOWN_SOURCE_READERS}",
        )

    settings = settings_manager.load_settings()
    resolved_dir, active_file_path, _, resolved_files = _resolve_source_file(settings, reader)

    if not resolved_dir:
        return SourceFilesResponse(reader=reader, directory="", files=[], total_count=0)

    allowed_exts = _READER_ALLOWED_EXTS.get(reader, set())

    # Build set of all active canonical paths (one per matched pattern)
    active_canonicals: set[Path] = set()
    for fp in resolved_files.values():
        if fp:
            try:
                active_canonicals.add(Path(fp).resolve())
            except OSError:
                pass

    raw_entries: list[tuple[float, SourceFileEntry]] = []
    try:
        with os.scandir(resolved_dir) as it:
            for entry in it:
                if not entry.is_file(follow_symlinks=False):
                    continue
                if Path(entry.name).suffix.lower() not in allowed_exts:
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue

                # Determine is_active via canonical path set
                is_active = False
                if active_canonicals:
                    try:
                        entry_path = Path(entry.path).resolve()
                        is_active = entry_path in active_canonicals
                    except OSError:
                        is_active = False

                raw_entries.append((
                    stat.st_mtime,
                    SourceFileEntry(
                        filename=entry.name,
                        file_path=entry.path,
                        file_size_bytes=stat.st_size,
                        file_modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        is_active=is_active,
                    )
                ))
    except OSError:
        return SourceFilesResponse(reader=reader, directory=resolved_dir, files=[], total_count=0)

    # Sort by raw mtime float descending (avoids DST fold ISO string misordering)
    raw_entries.sort(key=lambda t: t[0], reverse=True)
    entries = [e for _, e in raw_entries]

    return SourceFilesResponse(
        reader=reader,
        directory=resolved_dir,
        files=entries,
        total_count=len(entries),
    )


@router.get("/sources/health", response_model=SourceHealthResponse)
def get_sources_health():
    """Return per-reader health metrics combining sync history with file status."""
    health_data = settings_manager.get_source_health(_history_db_path())

    settings_cfg = settings_manager.load_settings()
    by_source = health_data.get("by_source_after") or {}
    last_sync_at = health_data.get("last_sync_at")
    db_ok = health_data.get("db_available", False)

    # Parse last_sync_at to datetime for pending_sync comparison
    last_sync_dt = None
    if last_sync_at:
        try:
            last_sync_dt = datetime.fromisoformat(last_sync_at)
        except Exception:
            last_sync_dt = None

    now = datetime.now()
    entries: List[SourceHealthEntry] = []
    all_healthy = True

    for reader in _KNOWN_SOURCE_READERS:
        _, file_path, _, _ = _resolve_source_file(settings_cfg, reader)
        file_found = bool(file_path) and Path(file_path).exists()

        file_modified: Optional[str] = None
        file_size_bytes: Optional[int] = None
        file_stale = False
        file_mtime_dt = None

        if file_found:
            stat = Path(file_path).stat()
            file_mtime_dt = datetime.fromtimestamp(stat.st_mtime)
            file_modified = file_mtime_dt.isoformat()
            file_size_bytes = stat.st_size
            file_stale = (now - file_mtime_dt).days > _STALE_DAYS

        label = _READER_LABEL_MAP.get(reader, reader)
        source_entry = by_source.get(label)
        row_count: Optional[int] = None
        net_value_cny: Optional[float] = None
        if isinstance(source_entry, dict):
            raw_count = source_entry.get("count")
            try:
                row_count = int(raw_count) if raw_count is not None else None
            except (ValueError, TypeError):
                row_count = None
            raw_value = source_entry.get("value")
            try:
                net_value_cny = float(raw_value) if raw_value is not None else None
            except (ValueError, TypeError):
                net_value_cny = None

        # Determine status
        if not db_ok:
            status = "unknown"
            all_healthy = False
        elif not file_found:
            status = "missing"
            all_healthy = False
        elif last_sync_at is None:
            status = "never_synced"
            all_healthy = False
        elif last_sync_dt is not None and file_mtime_dt is not None:
            # Both are naive local datetimes — compare directly
            if file_mtime_dt > last_sync_dt:
                status = "pending_sync"
            elif file_stale:
                status = "stale"
                all_healthy = False
            else:
                status = "ok"
        elif file_stale:
            status = "stale"
            all_healthy = False
        else:
            status = "ok"

        entries.append(SourceHealthEntry(
            reader=reader,
            last_sync_at=last_sync_at,
            row_count=row_count,
            net_value_cny=net_value_cny,
            file_path=file_path,
            file_modified=file_modified,
            file_size_bytes=file_size_bytes,
            file_stale=file_stale,
            status=status,
        ))

    return SourceHealthResponse(
        sources=entries,
        last_sync_at=last_sync_at,
        all_healthy=all_healthy,
    )


@router.get("/sources/upload-history/{reader}")
async def get_reader_upload_history(reader: str, limit: int = Query(default=20, ge=1, le=500)) -> UploadHistoryResponse:
    if reader not in _KNOWN_SOURCE_READERS:
        raise HTTPException(status_code=404, detail=f"Unknown reader: {reader}")
    entries = []
    try:
        with _duckdb.connect(_history_db_path()) as conn:
            exists = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name='source_upload_history'"
            ).fetchone()
            if exists:
                rows = conn.execute(
                    "SELECT id, reader, filename, file_size_bytes, uploaded_at, is_valid, warnings, previous_filename "
                    "FROM source_upload_history WHERE reader=? ORDER BY uploaded_at DESC LIMIT ?",
                    [reader, limit]
                ).fetchall()
                entries = [_row_to_history_entry(r) for r in rows]
    except Exception as e:
        logger.warning("Failed to query upload history: %s", e)
    return UploadHistoryResponse(reader=reader, entries=entries, total_count=len(entries))


@router.get("/sources/upload-history")
async def get_all_upload_history(limit: int = Query(default=50, ge=1, le=500)) -> UploadHistoryResponse:
    entries = []
    try:
        with _duckdb.connect(_history_db_path()) as conn:
            exists = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name='source_upload_history'"
            ).fetchone()
            if exists:
                rows = conn.execute(
                    "SELECT id, reader, filename, file_size_bytes, uploaded_at, is_valid, warnings, previous_filename "
                    "FROM source_upload_history ORDER BY uploaded_at DESC LIMIT ?",
                    [limit]
                ).fetchall()
                entries = [_row_to_history_entry(r) for r in rows]
    except Exception as e:
        logger.warning("Failed to query upload history: %s", e)
    return UploadHistoryResponse(reader=None, entries=entries, total_count=len(entries))


@router.get("/sources/events", response_model=SourceEventsResponse)
async def get_all_source_events(limit: int = Query(default=50, ge=1, le=500)) -> SourceEventsResponse:
    """Return unified upload+fetch event feed across all readers, newest first.

    Supersedes /sources/upload-history (kept for back-compat).
    """
    events: list[SourceEvent] = []
    try:
        with _duckdb.connect(_history_db_path()) as conn:
            _ensure_upload_history_table(conn)
            rows = conn.execute(
                "SELECT id, reader, filename, file_size_bytes, uploaded_at, is_valid, warnings, "
                "previous_filename, origin "
                "FROM source_upload_history ORDER BY uploaded_at DESC LIMIT ?",
                [limit]
            ).fetchall()
            events = [_row_to_source_event(r) for r in rows]
    except Exception as e:
        logger.warning("Failed to query source events: %s", e)
    return SourceEventsResponse(reader=None, events=events, total_count=len(events))


@router.get("/sources/events/{reader}", response_model=SourceEventsResponse)
async def get_reader_source_events(reader: str, limit: int = Query(default=20, ge=1, le=500)) -> SourceEventsResponse:
    """Return unified upload+fetch event feed for a single reader, newest first.

    400 for unknown readers (matches the upload endpoint contract).
    """
    if reader not in _KNOWN_SOURCE_READERS:
        raise HTTPException(status_code=400, detail=f"Unknown reader: {reader}")
    events: list[SourceEvent] = []
    try:
        with _duckdb.connect(_history_db_path()) as conn:
            _ensure_upload_history_table(conn)
            rows = conn.execute(
                "SELECT id, reader, filename, file_size_bytes, uploaded_at, is_valid, warnings, "
                "previous_filename, origin "
                "FROM source_upload_history WHERE reader=? ORDER BY uploaded_at DESC LIMIT ?",
                [reader, limit]
            ).fetchall()
            events = [_row_to_source_event(r) for r in rows]
    except Exception as e:
        logger.warning("Failed to query source events for reader %r: %s", reader, e)
    return SourceEventsResponse(reader=reader, events=events, total_count=len(events))


@router.post("/sources/fetch/{reader}", response_model=FetchResult)
def fetch_source(reader: str) -> FetchResult:
    """Trigger a fetch for a reader with a registered fetcher (currently: ibkr).

    Steps (mirrors the upload path's transactional discipline):
    1. Validate: 400 if reader unknown or not can_fetch.
    2. Resolve data_dir (same chain as upload).
    3. Call the reader's fetcher → new file path.
    4. Push to GCS if UIS_GCS_BUCKET set; on GCS failure → rollback (delete new file) + 503.
    5. Record event with origin='fetch' (non-fatal).
    6. Run retention (local + GCS, non-fatal).
    7. mark_dirty().

    Token/query-id are never exposed to the client — server-side only.
    Maps FlexFetchError → HTTP 502.
    """
    from src.fetchers.registry import can_fetch as _can_fetch, fetch as _fetch  # noqa: PLC0415
    from src.fetchers.ibkr_flex import FlexFetchError  # noqa: PLC0415
    import json as _json  # noqa: PLC0415

    if reader not in _KNOWN_SOURCE_READERS:
        raise HTTPException(status_code=400, detail=f"Unknown reader: {reader}")
    if not _can_fetch(reader):
        raise HTTPException(
            status_code=400,
            detail=f"Reader '{reader}' has no registered fetcher (can_fetch=false).",
        )

    settings = settings_manager.load_settings()
    resolved_dir, _, _, _ = _resolve_source_file(settings, reader)
    if not resolved_dir:
        raise HTTPException(
            status_code=422,
            detail=f"No directory configured for reader '{reader}'. Set data_dir first.",
        )
    dest_dir = Path(resolved_dir)
    if not dest_dir.is_dir():
        bucket_env = os.environ.get("UIS_GCS_BUCKET")
        if bucket_env and str(dest_dir).startswith("/tmp/"):
            dest_dir.mkdir(parents=True, exist_ok=True)
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Configured directory does not exist: {resolved_dir}",
            )

    bucket = os.getenv("UIS_GCS_BUCKET")
    new_file_path: Optional[Path] = None

    try:
        new_file_path = _fetch(reader, dest_dir)
    except EnvironmentError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FlexFetchError as e:
        raise HTTPException(status_code=502, detail=f"IBKR Flex fetch failed: {e}") from e
    except Exception as e:
        logger.exception("Unexpected error fetching source for reader %r", reader)
        raise HTTPException(status_code=500, detail=f"Fetch failed: {e}") from e

    file_size_bytes = new_file_path.stat().st_size
    fetched_at = datetime.now().isoformat()

    if bucket:
        try:
            upload_source_to_gcs(bucket, reader, str(new_file_path))
        except Exception as e:
            logger.warning("GCS push failed after fetch for %r: %s", reader, e)
            try:
                new_file_path.unlink(missing_ok=True)
            except OSError as rollback_err:
                logger.error("Rollback failed after GCS push error for %r: %s", reader, rollback_err)
            return JSONResponse(
                status_code=503,
                content={"error": "GCS upload failed; fetched file not committed"},
            )

    # Read line count from the new file
    try:
        text = new_file_path.read_text(encoding="utf-8", errors="replace")
        line_count = len(text.splitlines())
    except Exception:
        line_count = 0

    # Record fetch event (non-fatal)
    try:
        with _duckdb.connect(_history_db_path()) as conn:
            _ensure_upload_history_table(conn)
            conn.execute(
                """INSERT INTO source_upload_history
                   (reader, filename, file_size_bytes, uploaded_at, is_valid, warnings, previous_filename, origin)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [reader, new_file_path.name, file_size_bytes, datetime.now(),
                 True, _json.dumps([]), None, "fetch"]
            )
    except Exception as e:
        logger.warning("Failed to record fetch event for %r: %s", reader, e)

    # Retention (non-fatal)
    pruned: list[str] = []
    try:
        pruned.extend(prune_source_files(reader, str(dest_dir)))
    except Exception as e:
        logger.warning("Retention: local prune failed for %r: %s", reader, e)
    if bucket:
        try:
            gcs_deleted = prune_source_blobs(bucket, reader)
            pruned.extend(gcs_deleted)
        except Exception as e:
            logger.warning("Retention: GCS prune failed for %r: %s", reader, e)

    mark_dirty()

    return FetchResult(
        reader=reader,
        file_path=str(new_file_path),
        file_size_bytes=file_size_bytes,
        line_count=line_count,
        fetched_at=fetched_at,
        pruned=pruned,
    )


@router.get("/import-adapters")
def get_import_adapters():
    with DatabaseConnector(_history_db_path()) as connector:
        return {"adapters": ImportAdapterService(connector).list_adapters()}


@router.post("/import-adapters/{key}/upload")
async def upload_import_adapter_file(key: str, import_type: str = Query(...), header_row: int = Query(0), file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".csv", ".xls", ".xlsx"}:
        raise HTTPException(status_code=422, detail="Unsupported extension")

    # Persist to a stable directory (survives restarts, unlike OS tempdir)
    adapter_dir = Path("data/import_adapters")
    adapter_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{key}_{file.filename or 'upload'}{suffix}" if not (file.filename or "").endswith(suffix) else f"{key}_{file.filename}"
    dest_path = adapter_dir / safe_name

    try:
        content = await file.read()
        if len(content) > _UPLOAD_MAX_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds 50 MB limit")
        dest_path.write_bytes(content)

        with DatabaseConnector(_history_db_path()) as connector:
            result = ImportAdapterService(connector).create_import_run(
                adapter_key=key,
                import_type=import_type,
                filename=file.filename or safe_name,
                file_path=str(dest_path),
                header_row=header_row,
            )
        mark_dirty()
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Import adapter upload failed for %s", key)
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}") from e


@router.post("/import-adapters/{key}/configure")
def configure_import_adapter(key: str, req: ImportAdapterConfigureRequest):
    try:
        with DatabaseConnector(_history_db_path()) as connector:
            ImportAdapterService(connector).configure_import_run(req.run_id, req.column_mapping, req.fx_rate)
        mark_dirty()
        return {"ok": True}
    except Exception as e:
        logger.exception("Import adapter configure failed for %s", key)
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/import-adapters/{key}/validate")
def validate_import_adapter(key: str, req: ImportAdapterRunRequest):
    try:
        with DatabaseConnector(_history_db_path()) as connector:
            return ImportAdapterService(connector).validate_import_run(req.run_id)
    except Exception as e:
        logger.exception("Import adapter validate failed for %s", key)
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/import-adapters/{key}/stage")
def stage_import_adapter(key: str, req: ImportAdapterRunRequest):
    try:
        with DatabaseConnector(_history_db_path()) as connector:
            staged_rows = ImportAdapterService(connector).stage_import_run(req.run_id)
        mark_dirty()
        return {"staged_rows": staged_rows}
    except Exception as e:
        logger.exception("Import adapter stage failed for %s", key)
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/import-adapters/{key}/staged-rows")
def get_import_adapter_staged_rows(key: str, run_id: int = Query(...), limit: int = Query(50)):
    try:
        with DatabaseConnector(_history_db_path()) as connector:
            rows = ImportAdapterService(connector).get_staged_rows(run_id, limit=limit)
        return {"rows": rows, "count": len(rows)}
    except Exception as e:
        logger.exception("Failed to get staged rows for %s run %s", key, run_id)
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/import-adapters/{key}/approve")
def approve_import_adapter(key: str, req: ImportAdapterApproveRequest):
    try:
        with DatabaseConnector(_history_db_path()) as connector:
            # Reject duplicate source_system from a different adapter
            existing = connector.execute(
                "SELECT adapter_key FROM import_adapter_approvals WHERE source_system = ? AND adapter_key != ?",
                (req.source_system, key),
            ).fetchone()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"source_system '{req.source_system}' is already used by adapter '{existing[0]}'"
                )
            ImportAdapterService(connector).approve_adapter(
                adapter_key=key,
                source_system=req.source_system,
                asset_prefixes=req.asset_prefixes,
                authority_priority=req.authority_priority,
                approved_by=req.approved_by,
            )

            # ----------------------------------------------------------------
            # Optional: generate config-driven reader artifacts (A2)
            # ----------------------------------------------------------------
            reader_key: Optional[str] = None
            reader_warning: Optional[str] = None

            if req.generate_reader:
                try:
                    from src.import_adapters.reader_generator import (  # noqa: PLC0415
                        generate_reader_artifacts,
                        _sanitize_key,
                    )

                    # Derive reader_key from adapter key
                    reader_key = _sanitize_key(key)

                    # Fetch latest run for this adapter to get column_mapping / fx_rate / import_type / file_path
                    run_row = connector.execute(
                        """
                        SELECT import_type, file_path, filename, column_mapping
                        FROM import_adapter_runs
                        WHERE adapter_key = ?
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (key,),
                    ).fetchone()

                    if run_row is None:
                        reader_warning = (
                            "generate_reader skipped: no import_adapter_runs found for "
                            f"adapter '{key}' — approve without a prior upload/configure run."
                        )
                    else:
                        run_import_type, run_file_path, run_filename, raw_mapping = run_row
                        import json as _json  # noqa: PLC0415
                        parsed_mapping = _json.loads(raw_mapping) if raw_mapping else {}
                        column_mapping = parsed_mapping.get("column_mapping", parsed_mapping)
                        fx_rate = parsed_mapping.get("fx_rate")

                        # Infer file format from filename extension
                        fname_lower = (run_filename or "").lower()
                        if fname_lower.endswith((".xlsx", ".xls")):
                            file_format = "excel"
                        else:
                            file_format = "csv"

                        display_name = req.display_name or req.source_system

                        generate_reader_artifacts(
                            reader_key=reader_key,
                            source_system=req.source_system,
                            display_name=display_name,
                            asset_prefixes=req.asset_prefixes,
                            authority_priority=req.authority_priority,
                            column_mapping=column_mapping,
                            fx_rate=fx_rate,
                            import_type=run_import_type,
                            upload_file_path=run_file_path,
                            file_format=file_format,
                        )

                        # ADR-018 Phase 3: record the generated reader key so
                        # DB-staging and authority-injection paths skip this adapter
                        # (mutual exclusion — prevents double-count).
                        connector.execute(
                            "UPDATE import_adapter_approvals "
                            "SET generated_reader_key = ? WHERE adapter_key = ?",
                            (reader_key, key),
                        )

                except Exception as gen_exc:
                    logger.warning(
                        "generate_reader_artifacts failed for adapter '%s' (approval already recorded): %s",
                        key,
                        gen_exc,
                        exc_info=True,
                    )
                    reader_warning = f"Reader artifact generation failed: {gen_exc}"
                    reader_key = None

        mark_dirty()
        response: dict = {
            "ok": True,
            "adapter_key": key,
            "source_system": req.source_system,
            "authority_priority": req.authority_priority,
        }
        if reader_key is not None:
            response["generated_reader_key"] = reader_key
        if reader_warning is not None:
            response["reader_warning"] = reader_warning
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Import adapter approve failed for %s", key)
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/profile", response_model=ProfileResponse)
def get_profile() -> ProfileResponse:
    """Return user profile including investor philosophy.

    GET /settings/profile
    Response: { display_name, avatar_url, philosophy: { goal?, horizon?,
                risk_tolerance?, core_weakness?, portfolio_structure? } }
    """
    profile = settings_manager.get_profile()
    raw_phil = profile.get("philosophy") or {}
    return ProfileResponse(
        display_name=profile.get("display_name") or "",
        avatar_url=profile.get("avatar_url") or None,
        philosophy=InvestorPhilosophy(**{k: v for k, v in raw_phil.items() if k in InvestorPhilosophy.model_fields}),
    )


@router.put("/profile", response_model=ProfileResponse)
def update_profile(body: ProfileUpdate) -> ProfileResponse:
    """Update user profile fields (display_name, avatar_url, philosophy).

    PUT /settings/profile
    Body: { display_name?, avatar_url?, philosophy?: { goal?, horizon?,
            risk_tolerance?, core_weakness?, portfolio_structure? } }
    - Omitted top-level fields are preserved from the current profile.
    - philosophy is a PARTIAL update: only keys supplied in the request body
      are written; omitted philosophy keys retain their existing values.

    Response: same shape as GET /settings/profile.
    """
    current = settings_manager.get_profile()
    display_name = body.display_name.strip() if body.display_name is not None else (current.get("display_name") or "")
    if body.avatar_url is not None:
        avatar = body.avatar_url.strip()
        if avatar and len(avatar) > 200_000:
            raise HTTPException(status_code=400, detail="Avatar too large (max 200KB)")
        avatar_url = avatar or None
    else:
        avatar_url = current.get("avatar_url") or None

    # Merge philosophy: existing dict + incoming non-None fields
    current_phil: dict = current.get("philosophy") or {}
    if body.philosophy is not None:
        incoming = body.philosophy.model_dump(exclude_none=True)
        merged_phil = {**current_phil, **incoming}
    else:
        merged_phil = current_phil  # unchanged

    settings_manager.save_profile(display_name, avatar_url, philosophy=merged_phil)
    mark_dirty()
    return ProfileResponse(
        display_name=display_name,
        avatar_url=avatar_url,
        philosophy=InvestorPhilosophy(**{k: v for k, v in merged_phil.items() if k in InvestorPhilosophy.model_fields}),
    )
