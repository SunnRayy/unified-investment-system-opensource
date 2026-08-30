"""Reader Mapping Management API (ADR-023/ADR-023 Reader Mapping Management
plan, Workstream A — Step A3; Workstream B — Gold/Insurance/RSU id_field_map).

UI-managed CRUD over the `reader_mappings` table (migration V75, extended by
V77) — the layer that decides HOW raw reader-file data BECOMES assets (e.g.
Financial Summary Excel column -> asset_id, or a Gold Excel account label ->
an asset_id template segment), as opposed to the classification layer (what
an asset IS), which already has its own UI (taxonomy.py).

mapping_kind shapes managed here:
  - fs_column (financial_summary): map_key = Excel column header (资产负债
    sheet), map_value = {"asset_id", "asset_name", "currency"} — WS-A.
  - ie_column (financial_summary): map_key = Excel column header (月度收支
    sheet), map_value = {"role", "bucket", "currency"} — the column's LEDGER
    SEMANTICS (invested / redemption / income / expense / computed /
    reference / ignored, and for invested/redemption which destination
    bucket), read by src.services.investment_contributions. Plan
    2026-08-01 WS-A, migration V82. financial_summary is therefore the second
    MULTI-KIND reader; unlike schwab it keeps a documented DEFAULT kind
    (fs_column) so every pre-existing caller that omits `kind` keeps working.
  - id_field_map (gold/insurance/rsu): map_key = "field:label" (e.g.
    "account:招行"), map_value = {"code"} — WS-B. A label:code mapping is a
    SEGMENT of a template-built asset_id (e.g. "GOLD_{asset_name}_{account}"),
    not a full asset_id on its own — see docs/api-specs/reader-mappings.md
    for the affected-asset_id LIKE-pattern heuristic this implies for the
    archive/delete guards.
  - known_etf / symbol_norm / action_map (schwab) and type_map (cn_fund) —
    WS-C vocabularies. map_key = ticker / raw symbol / raw action / raw
    操作类型 label; map_value = {"etf": true} / {"to": ...} / {"type": ...}.
    action_map/type_map "type" values are validated against
    src.services.reader_mappings.ALLOWED_TRANSACTION_TYPES (422 otherwise).
    schwab is the first MULTI-KIND reader — the `kind` query/body param is
    REQUIRED where a reader has more than one kind (422 when omitted).
    NOTE: ibkr is deliberately NOT in the allowlist — it is co-authority with
    Schwab and reuses the schwab symbol_norm vocabulary (the orchestrator
    loads reader_key='schwab' for the ibkr run too); giving ibkr its own rows
    would fork the shared vocabulary. Any other reader returns 404.

Rule 12: every route body is wrapped in try/except -> api_error_response, so
an unhandled failure never degrades to a silent [] + 200.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.dependencies import get_db
from src.api.routes._errors import api_error_response
from src.api.routes.settings import _resolve_source_file
from src.database.connector import DatabaseConnector
from src.database.mapping_seeds import (
    IE_CURRENCIES,
    IE_GROUPS,
    IE_ROLE_BUCKETS,
    IE_ROLES,
)
from src.services import settings_manager
from src.services.reader_mappings import (
    ALLOWED_TRANSACTION_TYPES,
    get_ignored_map_keys,
    load_reader_mappings,
    scan_unmapped_columns,
    scan_unmapped_id_field_map_labels,
    scan_unmapped_vocab_values,
)
from src.storage.gcs_flush import mark_dirty

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/sources", tags=["reader-mappings"])

# reader_key -> tuple of managed mapping_kinds (WS-C generalization: schwab is
# the first reader with more than one). Order matters only for display.
_MANAGED_READERS: Dict[str, "tuple[str, ...]"] = {
    "financial_summary": ("fs_column", "ie_column"),
    "gold": ("id_field_map",),
    "insurance": ("id_field_map",),
    "rsu": ("id_field_map",),
    "schwab": ("known_etf", "symbol_norm", "action_map"),
    "cn_fund": ("type_map",),
}
# Multi-kind readers that keep a DEFAULT kind when `kind` is omitted.
# financial_summary shipped as a single-kind reader (fs_column) long before
# ie_column existed (plan 2026-08-01 WS-A), and its callers — the frontend
# panel, docs/api-specs/reader-mappings.md, the existing API tests — rely on
# the omitted-kind default. schwab is deliberately NOT here: it was multi-kind
# from birth and 422s so the caller must disambiguate.
_READER_DEFAULT_KIND: Dict[str, str] = {
    "financial_summary": "fs_column",
}
# The WS-C vocabulary kinds (validation/guard/preview logic dispatches on this).
_VOCAB_KINDS = frozenset({"known_etf", "symbol_norm", "action_map", "type_map"})
# reader_key -> the source_system value its own holdings/transactions rows carry
# (used for the fs_column asset_id cross-reader collision check).
_READER_SOURCE_SYSTEM: Dict[str, str] = {
    "financial_summary": "Financial_Summary_Excel",
}
_FS_SHEET_NAME = "资产负债"
_IE_SHEET_NAME = "月度收支"
_FS_HEADER_ROW = 3
# The two financial_summary sheet-column kinds and the sheet each one scans.
# Both sheets are read with header=3, exactly as the sync path reads them
# (src/sync/financial_summary_sync.py::_read_fs_sheet).
_SHEET_COLUMN_KINDS: Dict[str, str] = {
    "fs_column": _FS_SHEET_NAME,
    "ie_column": _IE_SHEET_NAME,
}
# How many recent ledger months the ie_column list endpoint cross-checks against
# the Excel's own aggregate columns. Bounded so the list stays cheap; the full
# history is available via src.services.ie_ledger.validate_ie_totals(months=N).
_IE_AGGREGATE_CHECK_MONTHS = 24
_CN_FUND_TXN_SHEET = "基金交易记录"
_CN_FUND_TYPE_COLUMN = "操作类型"

# id_field_map readers (WS-B) — YAML config path, used to validate map_key
# fields and to scan the currently uploaded file for label values.
_READER_YAML_PATH: Dict[str, Path] = {
    "gold": Path("config/readers/gold.yaml"),
    "insurance": Path("config/readers/insurance.yaml"),
    "rsu": Path("config/readers/rsu.yaml"),
}

# WS-C known_etf/symbol_norm affected-asset_id guard: the canonical prefixes
# Schwab's normalizers can produce (schwab.yaml identity.asset_prefixes only
# lists US_STK_/US_ETF_/CASH_USD; the normalizer can also emit US_BND_/
# US_FUND_/US_OPT_ — include them all so the guard never under-counts).
_SCHWAB_CANONICAL_PREFIXES = ("US_STK_", "US_ETF_", "US_BND_", "US_FUND_", "US_OPT_")


def _open_writable(db: DatabaseConnector) -> DatabaseConnector:
    """Return a writable DB connection, closing the read-only one if needed."""
    if getattr(db, "read_only", False):
        db_path = db.db_path
        db.close()
        return DatabaseConnector(db_path, read_only=False)
    return db


def _reader_kinds(reader: str) -> "tuple[str, ...]":
    """Return the managed mapping_kinds for a reader, else raise 404."""
    if reader not in _MANAGED_READERS:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Reader '{reader}' is not yet mapping-managed. "
                f"Currently supported: {sorted(_MANAGED_READERS)}."
            ),
        )
    return _MANAGED_READERS[reader]


def _resolve_kind(reader: str, kind: Optional[str]) -> str:
    """Resolve the effective mapping_kind for a (reader, requested-kind) pair.

    - unmanaged reader -> 404 (via _reader_kinds)
    - kind omitted: single-kind readers default to their one kind; multi-kind
      readers use _READER_DEFAULT_KIND if they declare one (financial_summary
      -> fs_column, a backward-compat guarantee for callers written before
      ie_column existed), else -> 422 and the caller must disambiguate
    - kind supplied but not one of the reader's kinds -> 422
    """
    kinds = _reader_kinds(reader)
    if kind is None or kind == "":
        if len(kinds) == 1:
            return kinds[0]
        default_kind = _READER_DEFAULT_KIND.get(reader)
        if default_kind is not None:
            return default_kind
        raise HTTPException(
            status_code=422,
            detail=(
                f"reader '{reader}' manages multiple mapping kinds — pass kind= "
                f"(one of {sorted(kinds)})"
            ),
        )
    if kind not in kinds:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of {sorted(kinds)} for reader '{reader}', got '{kind}'",
        )
    return kind


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class MappingValue(BaseModel):
    """fs_column map_value payload shape (financial_summary only)."""

    asset_id: str
    asset_name: str
    currency: str


class IeColumnValue(BaseModel):
    """ie_column map_value payload shape (financial_summary 月度收支, V82).

    Documentation of the wire shape only — the create/patch bodies stay loosely
    typed dicts (see MappingCreateRequest) and are validated by
    _validate_ie_column_value against the src.database.mapping_seeds
    vocabulary, which is also what the V82 seed and the ledger service read.
    """

    role: str
    bucket: Optional[str] = None
    currency: str


class IdFieldMapValue(BaseModel):
    """id_field_map map_value payload shape (gold/insurance/rsu, WS-B)."""

    code: str


class MappingOut(BaseModel):
    id: int
    reader_key: str
    mapping_kind: str
    map_key: str
    # Loosely typed: fs_column carries {asset_id, asset_name, currency};
    # ie_column carries {role, bucket, currency}; id_field_map carries {code};
    # the WS-C vocab kinds carry {etf}/{to}/{type}. All are decoded straight
    # from the stored JSON — see MappingValue / IeColumnValue / IdFieldMapValue
    # for the kind-specific shapes.
    map_value: Dict[str, Any]
    status: str
    sort_order: Optional[int] = None
    updated_at: Optional[str] = None


class UnmappedColumnOut(BaseModel):
    column: str
    ignored_native: bool = False
    # ADR-023 A4.1 — 'ignored' | 'native' | 'computed' | 'liability' | 'candidate'.
    # Only 'candidate' is genuinely actionable; see scan_unmapped_columns.
    # id_field_map labels (WS-B) are always 'candidate' — no structural
    # native/computed/liability/ignored categories apply to label scanning.
    category: str = "candidate"
    # Only set for category='ignored' — the reader_mappings row id, so the UI
    # can call POST .../mappings/{mapping_id}/unignore. fs_column only.
    mapping_id: Optional[int] = None


class AggregateCheckOut(BaseModel):
    """One month where an Excel aggregate column disagrees with Huinsight's own sum
    of the leaf columns that aggregate declares (`validates`).

    ie_column only. Huinsight never uses these aggregates as calculation inputs
    (owner ruling 2026-08-01) — this is the cross-check that keeps that
    honest: a divergence means either a leaf column is unmapped (Huinsight is short)
    or the workbook's SUM range is broken / reaches a `_USD` sibling. Warn
    only; nothing here gates anything.
    """

    month: str
    column: str
    excel_value: float
    derived_value: float
    delta: float


class MappingListResponse(BaseModel):
    reader: str
    mapping_kind: str
    mappings: list[MappingOut]
    defaults_only: bool
    unmapped_columns: list[UnmappedColumnOut] = []
    # ie_column only; [] for every other kind and whenever everything reconciles.
    aggregate_checks: list[AggregateCheckOut] = []


class MappingCreateRequest(BaseModel):
    kind: str
    map_key: str
    # fs_column: {asset_id, asset_name, currency}. ie_column: {role, bucket,
    # currency}. id_field_map: {code}. vocab kinds: {etf}/{to}/{type}.
    value: Dict[str, Any]


class MappingPatchRequest(BaseModel):
    # fs_column: {asset_name?, asset_id?}. ie_column: {role?, bucket?,
    # currency?} — re-validated as a whole. id_field_map: {code?}.
    value: Optional[Dict[str, Any]] = None
    sort_order: Optional[int] = None


class ArchiveResponse(BaseModel):
    mapping: MappingOut
    asset_has_holdings: bool
    deactivate_hint: Optional[dict] = None


class DeleteResponse(BaseModel):
    deleted: int
    # fs_column: the asset_id that was mapped. id_field_map: not a single
    # asset_id (a label:code mapping is a template segment) — see `code`.
    asset_id: Optional[str] = None
    # id_field_map only: the code that was mapped.
    code: Optional[str] = None


class PreviewMappingItem(BaseModel):
    map_key: str
    value: MappingValue


class PreviewRequest(BaseModel):
    proposed: Optional[list[PreviewMappingItem]] = None


class IgnoreColumnRequest(BaseModel):
    map_key: str


class UnignoreResponse(BaseModel):
    unignored: int
    map_key: str


class PreviewColumnResult(BaseModel):
    map_key: str
    column_found: bool
    nonzero_rows: int
    latest_value: Optional[float] = None
    latest_date: Optional[str] = None


class PreviewResponse(BaseModel):
    reader: str
    mapping_kind: str
    file_path: Optional[str] = None
    results: list[PreviewColumnResult]
    unmapped_columns: list[UnmappedColumnOut]


class IdFieldMapPreviewItem(BaseModel):
    field: str
    label: str
    map_key: str
    mapped: bool
    code: Optional[str] = None


class IdFieldMapPreviewResponse(BaseModel):
    reader: str
    mapping_kind: str
    file_path: Optional[str] = None
    items: list[IdFieldMapPreviewItem]
    unmapped_columns: list[UnmappedColumnOut]


class VocabPreviewItem(BaseModel):
    """WS-C — one raw file value (ticker/action/操作类型) scanned against the
    merged vocabulary. mapped_value echoes the kind's map_value shape."""

    value: str
    mapped: bool
    mapped_value: Optional[Dict[str, Any]] = None


class VocabPreviewResponse(BaseModel):
    reader: str
    mapping_kind: str
    file_path: Optional[str] = None
    items: list[VocabPreviewItem]
    unmapped_columns: list[UnmappedColumnOut]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _row_to_out(row: tuple) -> MappingOut:
    """row = (id, reader_key, mapping_kind, map_key, map_value, status, sort_order, updated_at)."""
    return MappingOut(
        id=row[0],
        reader_key=row[1],
        mapping_kind=row[2],
        map_key=row[3],
        map_value=json.loads(row[4]),
        status=row[5],
        sort_order=row[6],
        updated_at=str(row[7]) if row[7] is not None else None,
    )


_MAPPING_SELECT = (
    "SELECT id, reader_key, mapping_kind, map_key, map_value, status, sort_order, updated_at "
    "FROM reader_mappings"
)


def _fetch_mapping_row(db: DatabaseConnector, reader: str, kinds: "tuple[str, ...]", mapping_id: int) -> tuple:
    """Fetch a mapping row by id, constrained to the reader and its managed
    kinds (multi-kind readers pass all their kinds — the row's own
    mapping_kind, row[2], tells the caller which one it is)."""
    placeholders = ", ".join("?" for _ in kinds)
    row = db.execute(
        f"{_MAPPING_SELECT} WHERE id = ? AND reader_key = ? AND mapping_kind IN ({placeholders})",
        [mapping_id, reader, *kinds],
    ).fetchone()
    if not row:
        raise LookupError(f"mapping {mapping_id} not found for reader '{reader}'")
    return row


def _reject_if_ignored(row: tuple) -> None:
    """Guard for the generic patch/archive/restore/delete endpoints: an
    'ignored' row (map_value='{}', no asset_id/asset_name/currency) isn't a
    real asset mapping — building a MappingOut from it would raise a Pydantic
    validation error. Ignored rows are managed only via ignore-column/unignore
    (fs_column only — id_field_map never creates 'ignored' rows).
    """
    status = row[5]
    if status == "ignored":
        raise HTTPException(
            status_code=409,
            detail="This is a column-ignore marker, not an asset mapping — use POST .../unignore instead.",
        )


def _resolve_reader_file(reader: str) -> Optional[str]:
    """Resolve the reader's currently uploaded file path, or None if absent."""
    settings = settings_manager.load_settings()
    _resolved_dir, file_path, _fallback, _resolved_files = _resolve_source_file(settings, reader)
    if not file_path or not Path(file_path).exists():
        return None
    return file_path


def _read_fs_sheet(file_path: str, sheet_name: str = _FS_SHEET_NAME):
    """Read a Financial Summary sheet exactly as the reader does (header=3).

    sheet_name defaults to 资产负债 (fs_column); the ie_column scan/preview
    passes 月度收支 (see _SHEET_COLUMN_KINDS).
    """
    import pandas as pd  # noqa: PLC0415 — lazy, avoids a module-level pandas import

    return pd.read_excel(file_path, sheet_name=sheet_name, header=_FS_HEADER_ROW, engine="openpyxl")


def _strip_keys(merged: Dict[str, Any]) -> Dict[str, Any]:
    """Strip-normalize a merged mapping's keys for column-header comparison.

    The 月度收支 header row has at least one column with a trailing space
    ('参考_美元汇率 ' — visible in the live payload), and a hand-edited Excel
    header can gain or lose whitespace at any time. The scan strips the sheet
    header before comparing, so the mapping side must be stripped too or an
    already-mapped column would be reported as an actionable candidate.
    Mirrors src.services.investment_contributions.load_ie_column_mapping.
    """
    return {str(k).strip(): v for k, v in merged.items()}


def _scan_ie_aggregate_checks(
    db: DatabaseConnector, merged: Optional[Dict[str, Any]] = None, months: int = _IE_AGGREGATE_CHECK_MONTHS
) -> "list[AggregateCheckOut]":
    """Best-effort Excel-aggregate cross-check for the ie_column list response.

    Delegates to src.services.ie_ledger.validate_ie_totals (which also logs each
    divergence at WARNING, so it is visible without the UI). Bounded to the most
    recent `months` ledger rows to keep the list endpoint cheap. Never raises —
    a health signal, never a gate.
    """
    try:
        from src.services.ie_ledger import validate_ie_totals  # noqa: PLC0415 — lazy

        divergences = validate_ie_totals(db, months=months, mapping=merged)
        return [
            AggregateCheckOut(
                month=d["month"], column=d["column"], excel_value=d["excel_value"],
                derived_value=d["derived_value"], delta=d["delta"],
            )
            for d in divergences
        ]
    except Exception as e:  # noqa: BLE001 — best-effort, never blocks the list
        logger.debug("ie_column aggregate cross-check failed (non-blocking): %s", e)
        return []


def _scan_reader_unmapped_columns(
    db: DatabaseConnector, reader: str, kind: str, merged: Optional[Dict[str, Any]] = None
) -> list[UnmappedColumnOut]:
    """Best-effort unmapped-column scan against the reader's current file.

    Sheet-column kinds only (fs_column -> 资产负债, ie_column -> 月度收支; see
    _SHEET_COLUMN_KINDS). Never raises — returns [] if the file is missing/
    unreadable, matching the "never break the list" contract used by the
    /settings/sources unmapped_count chip this reuses
    (src.services.reader_mappings.scan_unmapped_columns).

    For ie_column this is the Rule-12 half of the workstream: a 月度收支 column
    the owner adds and nobody maps used to vanish out of gross_invested
    silently; now it comes back as `category='candidate'`.
    """
    try:
        sheet_name = _SHEET_COLUMN_KINDS.get(kind)
        if sheet_name is None:
            return []
        file_path = _resolve_reader_file(reader)
        if not file_path:
            return []
        sheet_df = _read_fs_sheet(file_path, sheet_name)
        merged = merged if merged is not None else load_reader_mappings(db, reader, kind)
        ignored_keys = _strip_keys(get_ignored_map_keys(db, reader, kind))
        scanned = scan_unmapped_columns(
            list(sheet_df.columns), _strip_keys(merged), ignored_keys=ignored_keys
        )
        return [UnmappedColumnOut(**c) for c in scanned]
    except Exception as e:  # noqa: BLE001 — best-effort, never blocks the caller
        logger.debug("Unmapped-column scan failed for %s/%s (non-blocking): %s", reader, kind, e)
        return []


# ---------------------------------------------------------------------------
# ie_column (月度收支 column semantics — plan 2026-08-01 WS-A) helpers
# ---------------------------------------------------------------------------


def _validate_ie_column_value(
    value: Dict[str, Any],
) -> "tuple[str, Optional[str], str, Optional[str], Optional[dict]]":
    """Validate an ie_column map_value payload; return
    (role, bucket, currency, group, validates).

    The vocabulary lives in src.database.mapping_seeds (IE_ROLES /
    IE_ROLE_BUCKETS / IE_CURRENCIES / IE_GROUPS) — the same constants the V82
    seed and the ledger service use, so a value accepted here is always one
    src.services.ie_ledger understands.

    Guardrails (each one is a silent-failure this kind exists to prevent):
      - role='invested' REQUIRES a bucket. An invested column with no bucket
        would contribute to no destination and vanish out of gross_invested —
        precisely the bug WS-A removes.
      - bucket must be one the role can carry (a destination bucket for
        invested/redemption; 'inflow'/'outflow' for pass_through, naming which
        end of the round trip it is; nothing for
        income/expense/computed/reference/ignored). The short-lived
        'total_income' bucket was retired on 2026-08-01 with the Excel-aggregate
        design it belonged to (migration V84), and role='income' now carries no
        bucket at all, so it cannot be resurrected from the UI.
      - currency must be 'CNY' or 'USD', and a 'USD' column contributes to no
        total anywhere (the owner applies FX in Excel — see IEColumn).
    """
    role = str(value.get("role", "") or "").strip()
    if role not in IE_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"value.role '{role}' is not an allowed ie_column role — allowed: {sorted(IE_ROLES)}",
        )

    raw_bucket = value.get("bucket")
    bucket: Optional[str] = None
    if raw_bucket is not None and str(raw_bucket).strip() != "":
        bucket = str(raw_bucket).strip()

    allowed_buckets = IE_ROLE_BUCKETS.get(role, frozenset())
    if bucket is not None and bucket not in allowed_buckets:
        raise HTTPException(
            status_code=422,
            detail=(
                f"value.bucket '{bucket}' is not valid for role '{role}' — allowed: "
                f"{sorted(allowed_buckets) or 'null only'}"
            ),
        )
    if role == "invested" and bucket is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "value.bucket is required for role 'invested' — an invested column with no "
                f"destination bucket would contribute to nothing. Allowed: {sorted(allowed_buckets)}"
            ),
        )

    currency = str(value.get("currency", "") or "").strip()
    if currency not in IE_CURRENCIES:
        raise HTTPException(
            status_code=422,
            detail=f"value.currency '{currency}' must be one of {sorted(IE_CURRENCIES)}",
        )
    validates = _validate_ie_validates(value.get("validates"), role)
    group = _validate_ie_group(value.get("group"), role)
    return role, bucket, currency, group, validates


def _validate_ie_validates(raw: Any, role: str) -> "Optional[dict]":
    """Validate a `validates` cross-check target: {"roles": [...], "groups": [...]}.

    Only a `computed` column may carry one — it declares which LEAF roles/groups
    that Excel aggregate should equal, so src.services.ie_ledger can warn when
    the owner's SUM range and Huinsight's derived total disagree. Declaring a new
    aggregate check is therefore a mapping edit, not a code change.
    """
    if raw is None:
        return None
    if role != "computed":
        raise HTTPException(
            status_code=422,
            detail=(
                "value.validates is only valid on role 'computed' — it declares what an "
                "Excel aggregate column should equal"
            ),
        )
    if not isinstance(raw, dict) or not set(raw).issubset({"roles", "groups"}):
        raise HTTPException(
            status_code=422,
            detail='value.validates must be {"roles": [...], "groups": [...]} (either key optional)',
        )
    roles = [str(r) for r in (raw.get("roles") or [])]
    groups = [str(g).strip() for g in (raw.get("groups") or [])]
    bad_roles = [r for r in roles if r not in IE_ROLES]
    if bad_roles:
        raise HTTPException(
            status_code=422, detail=f"value.validates.roles {bad_roles} not in {sorted(IE_ROLES)}"
        )
    if any(not g for g in groups):
        raise HTTPException(status_code=422, detail="value.validates.groups must be non-empty strings")
    if not roles and not groups:
        raise HTTPException(
            status_code=422,
            detail="value.validates must name at least one role or group (an empty target checks nothing)",
        )
    return {"roles": roles, "groups": groups}


def _validate_ie_group(raw: Any, role: str) -> Optional[str]:
    """Validate a LEAF column's `group` tag (which Excel subtotal it belongs to).

    Non-empty; IE_GROUPS lists the tags in use today but the field is kept open
    so the owner can introduce a subtotal group from the UI — the cross-check is
    driven by whatever tags exist, never by column-name prefixes.
    """
    if raw is None or str(raw).strip() == "":
        return None
    group = str(raw).strip()
    if role == "computed":
        raise HTTPException(
            status_code=422,
            detail=(
                "value.group is for LEAF columns — a 'computed' aggregate declares "
                "value.validates instead"
            ),
        )
    if group not in IE_GROUPS:
        logger.info("ie_column: new subtotal group %r (known: %s)", group, sorted(IE_GROUPS))
    return group


def _ie_column_json(
    role: str, bucket: Optional[str], currency: str, group: Optional[str], validates: Optional[dict]
) -> str:
    """Serialize an ie_column map_value, omitting the two optional
    cross-validation fields when unset (matching the V82 seed's shape)."""
    payload: Dict[str, Any] = {"role": role, "bucket": bucket, "currency": currency}
    if group is not None:
        payload["group"] = group
    if validates is not None:
        payload["validates"] = validates
    return json.dumps(payload, ensure_ascii=False)


def _prepare_ie_column_create(
    writable: DatabaseConnector, reader: str, expected_kind: str, map_key: str, value: Dict[str, Any]
) -> str:
    role, bucket, currency, group, validates = _validate_ie_column_value(value)
    merged = load_reader_mappings(writable, reader, expected_kind)
    if map_key.strip() in {str(k).strip() for k in merged}:
        raise HTTPException(
            status_code=422,
            detail=f"map_key '{map_key}' already has an active mapping for reader '{reader}'",
        )
    return _ie_column_json(role, bucket, currency, group, validates)


def _apply_ie_column_patch(
    writable: DatabaseConnector, reader: str, row: tuple, old_value: dict, value: Dict[str, Any]
) -> dict:
    """Patch role/bucket/currency/group/validates on an ie_column row.

    No holdings/transactions reference guard applies (an ie_column mapping
    produces no asset_id — it only decides how a ledger column is summed), but
    the value vocabulary is re-checked against the FULL post-patch value, not
    just the changed field, so flipping `role` cannot leave behind a `bucket`,
    `group` or `validates` that the new role may not carry. Note
    `bucket: null` is a legitimate explicit value, so presence in the body —
    not truthiness — decides whether it is being changed.
    """
    candidate = dict(old_value)
    for field in ("role", "bucket", "currency", "group", "validates"):
        if field in value:
            candidate[field] = value[field]
    role, bucket, currency, group, validates = _validate_ie_column_value(candidate)
    return json.loads(_ie_column_json(role, bucket, currency, group, validates))


# ---------------------------------------------------------------------------
# id_field_map (WS-B) helpers
# ---------------------------------------------------------------------------


def _load_reader_cfg(reader: str):
    """Load & parse an id_field_map reader's YAML config (gold/insurance/rsu)."""
    from src.sources.reader_config import load_reader_config  # noqa: PLC0415 — lazy, pydantic+yaml

    return load_reader_config(_READER_YAML_PATH[reader])


def _id_template_fields(reader: str) -> "set[str]":
    """Union of id_template placeholder names across all of a reader's sheets
    (e.g. gold -> {"asset_name", "account"}; insurance -> {"product_name",
    "policy_name"}; rsu -> {"asset_name"}). Used to validate that a
    create/patch map_key's field component is a real id_template placeholder
    for this reader, not an arbitrary string.
    """
    cfg = _load_reader_cfg(reader)
    fields: "set[str]" = set()
    for sheet_cfg in cfg.parsing.sheets if cfg.parsing else []:
        if sheet_cfg.id_template:
            fields.update(re.findall(r"\{(\w+)\}", sheet_cfg.id_template))
    return fields


def _extract_field_labels(reader_cfg, file_path: str) -> "Dict[str, list]":
    """Best-effort: read a reader's currently uploaded file and extract, for
    each id_template placeholder field declared in its YAML, the raw label
    values (or melted column-header labels) currently present.

    Two source shapes are handled, matching how the field reaches the
    id_template in the config-driven engine:
      - rename-based (e.g. gold's asset_name/account, insurance's
        product_name, rsu's asset_name): the field is a `rename` TARGET —
        labels are that column's unique cell values.
      - melt-based (e.g. insurance premiums' policy_name): the field is the
        sheet's `melt.var_name` — labels are the sheet's OTHER column
        headers (the wide-format policy names), excluding the id_var/date
        column and blank/"Unnamed" pandas-default headers.

    Never raises — a sheet this can't read or resolve is silently skipped
    (best-effort scan, not a hard validation).
    """
    import pandas as pd  # noqa: PLC0415 — lazy, pandas-heavy

    field_labels: "Dict[str, list]" = {}
    parsing = reader_cfg.parsing
    if not parsing:
        return field_labels

    for sheet_cfg in parsing.sheets:
        if not sheet_cfg.id_template:
            continue
        fields = set(re.findall(r"\{(\w+)\}", sheet_cfg.id_template))
        try:
            raw_df = pd.read_excel(
                file_path, sheet_name=sheet_cfg.name, header=sheet_cfg.header_row, engine="openpyxl"
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("_extract_field_labels: could not read sheet %r: %s", sheet_cfg.name, e)
            continue

        if sheet_cfg.strip_column_names:
            raw_df.columns = [str(c).strip() for c in raw_df.columns]

        if sheet_cfg.melt and sheet_cfg.melt.var_name in fields:
            var_field = sheet_cfg.melt.var_name
            id_var = sheet_cfg.melt.id_var
            labels = [
                str(c).strip()
                for c in raw_df.columns
                if str(c).strip() and str(c).strip() != id_var and not str(c).startswith("Unnamed")
            ]
            field_labels.setdefault(var_field, []).extend(labels)

        if sheet_cfg.rename:
            for raw_col, mapped_col in sheet_cfg.rename.items():
                if mapped_col in fields and raw_col in raw_df.columns:
                    vals = raw_df[raw_col].dropna().astype(str).str.strip()
                    field_labels.setdefault(mapped_col, []).extend(v for v in vals if v)

    return field_labels


def _scan_id_field_map_unmapped(
    db: DatabaseConnector, reader: str, merged: Optional[Dict[str, Any]] = None
) -> list[UnmappedColumnOut]:
    """Best-effort unmapped-label scan against an id_field_map reader's
    current file. Reuses UnmappedColumnOut's shape (column='field:label',
    category always 'candidate') so the response stays uniform with
    fs_column's list — see scan_unmapped_id_field_map_labels for the
    underlying pandas-free classifier.
    """
    try:
        file_path = _resolve_reader_file(reader)
        if not file_path:
            return []
        reader_cfg = _load_reader_cfg(reader)
        field_labels = _extract_field_labels(reader_cfg, file_path)
        merged = merged if merged is not None else load_reader_mappings(db, reader, "id_field_map")
        scanned = scan_unmapped_id_field_map_labels(field_labels, merged)
        return [
            UnmappedColumnOut(column=item["map_key"], ignored_native=False, category="candidate", mapping_id=None)
            for item in scanned
            if not item["mapped"]
        ]
    except Exception as e:  # noqa: BLE001 — best-effort, never blocks the caller
        logger.debug("id_field_map unmapped-label scan failed for %s (non-blocking): %s", reader, e)
        return []


def _validate_id_field_map_key(reader: str, map_key: str) -> None:
    """map_key must be 'field:label' with field a real id_template placeholder
    for this reader (e.g. gold: asset_name/account)."""
    field, sep, label = map_key.partition(":")
    if not sep or not field or not label:
        raise HTTPException(
            status_code=422,
            detail=f"map_key must be 'field:label' for reader '{reader}', got '{map_key}'",
        )
    valid_fields = _id_template_fields(reader)
    if field not in valid_fields:
        raise HTTPException(
            status_code=422,
            detail=(
                f"field '{field}' is not a declared id_template field for reader '{reader}' "
                f"(valid: {sorted(valid_fields)})"
            ),
        )


def _validate_id_field_map_code(raw_code: Any) -> str:
    """code must be a non-empty ASCII-safe string with no whitespace — it
    becomes a literal segment of a template-built asset_id."""
    code = str(raw_code if raw_code is not None else "").strip()
    if not code:
        raise HTTPException(status_code=422, detail="value.code must not be empty")
    if not code.isascii() or any(ch.isspace() for ch in code):
        raise HTTPException(
            status_code=422,
            detail="value.code must be ASCII-safe with no whitespace (it becomes an asset_id path segment)",
        )
    return code


def _id_field_map_affected_count(db: DatabaseConnector, reader: str, code: str) -> "tuple[int, int]":
    """Conservative heuristic guard for archive/patch/delete on an
    id_field_map row: count holdings/transactions rows whose asset_id starts
    with one of the reader's declared asset_prefixes AND contains `code` as a
    substring.

    A label:code mapping is a SEGMENT of a template-built asset_id (e.g.
    "GOLD_{asset_name}_{account}"), not a full asset_id on its own — there is
    no single asset_id to look up the way fs_column's rename/archive/delete
    guards do. This LIKE-pattern heuristic may over-count if `code` happens
    to appear inside an unrelated segment of another asset_id under the same
    prefix (e.g. a coincidental substring match) — an accepted, documented
    trade-off (see docs/api-specs/reader-mappings.md), erring toward a false
    409 (forcing an owner to double-check) rather than silently allowing an
    archive/delete that breaks live holdings.
    """
    if not code:
        return (0, 0)
    reader_cfg = _load_reader_cfg(reader)
    prefixes = reader_cfg.identity.asset_prefixes or [""]
    holdings_count = 0
    tx_count = 0
    for prefix in prefixes:
        like_pattern = f"{prefix}%{code}%"
        holdings_count += db.execute(
            "SELECT COUNT(*) FROM holdings WHERE asset_id LIKE ?", [like_pattern]
        ).fetchone()[0]
        tx_count += db.execute(
            "SELECT COUNT(*) FROM transactions WHERE asset_id LIKE ?", [like_pattern]
        ).fetchone()[0]
    return holdings_count, tx_count


# ---------------------------------------------------------------------------
# WS-C vocab (schwab known_etf/symbol_norm/action_map, cn_fund type_map) helpers
# ---------------------------------------------------------------------------


def _validate_symbol_token(raw: Any, what: str) -> str:
    """A ticker/symbol token: non-empty, ASCII, no whitespace (it becomes part
    of a canonical asset_id, e.g. US_ETF_{ticker})."""
    token = str(raw if raw is not None else "").strip()
    if not token:
        raise HTTPException(status_code=422, detail=f"{what} must not be empty")
    if not token.isascii() or any(ch.isspace() for ch in token):
        raise HTTPException(
            status_code=422,
            detail=f"{what} must be ASCII-safe with no whitespace (it becomes part of a canonical asset_id)",
        )
    return token


def _validate_vocab_value(kind: str, value: Dict[str, Any]) -> str:
    """Validate a vocab kind's map_value payload; return the canonical JSON to
    store.

    - known_etf: value is FIXED at {"etf": true} — anything else 422s (there
      is no "etf: false" state; removing a ticker is archive, not a value).
    - symbol_norm: {"to": <non-empty ASCII no-whitespace symbol>}.
    - action_map / type_map: {"type": <member of ALLOWED_TRANSACTION_TYPES>} —
      the WS-C guardrail (highest blast radius per the plan): a typo'd type
      would silently corrupt cash-flow classification downstream, so it hard
      422s listing the allowed values.
    - 'transfer' (Attribution & Flows WS-3.1, V79) is a kind-scoped exception:
      it is a Schwab-only pseudo-type — resolved to transfer_out/transfer_in
      by quantity sign inside schwab_transactions_from_csv — so it is a valid
      action_map target but NOT a valid type_map target: no other reader hook
      resolves it, so a literal 'transfer' would persist on (e.g.) CN-fund
      rows and no downstream consumer understands it. The enum itself stays
      unchanged (it correctly describes action_map's domain); the restriction
      is applied here per kind.
    """
    if kind == "known_etf":
        if value.get("etf") is not True or set(value.keys()) != {"etf"}:
            raise HTTPException(
                status_code=422,
                detail='known_etf value is fixed: {"etf": true} (archive the row to un-classify a ticker)',
            )
        return json.dumps({"etf": True})
    if kind == "symbol_norm":
        to = _validate_symbol_token(value.get("to"), "value.to")
        return json.dumps({"to": to})
    # action_map / type_map
    tx_type = str(value.get("type", "") or "").strip()
    if tx_type == "transfer" and kind != "action_map":
        raise HTTPException(
            status_code=422,
            detail=(
                "value.type 'transfer' is a Schwab-only pseudo-type (resolved to "
                "transfer_out/transfer_in by quantity sign in the Schwab transactions "
                "hook) — it is only valid for action_map, not type_map; use "
                "'transfer_in' or 'transfer_out' directly"
            ),
        )
    if tx_type not in ALLOWED_TRANSACTION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"value.type '{tx_type}' is not an allowed transaction_type — "
                f"allowed: {sorted(ALLOWED_TRANSACTION_TYPES)}"
            ),
        )
    return json.dumps({"type": tx_type})


def _vocab_affected_count(
    db: DatabaseConnector, kind: str, map_key: str, row_value: dict
) -> "tuple[int, int]":
    """Conservative reference-count guard for archive/patch/delete on a WS-C
    vocab row (same design intent as _id_field_map_affected_count — err toward
    a false 409 rather than silently breaking live rows):

    - known_etf ticker T (the map_key): exact check on asset_id 'US_ETF_{T}' —
      archiving/deleting means future transactions classify T as US_STK_{T}
      (the unknown-ticker default), splitting identity from historical
      US_ETF_ rows.
    - symbol_norm K -> {"to": V}: exact checks on '{prefix}{V}' for every
      canonical Schwab prefix — the normalized target V is what appears in
      stored asset_ids. (Exact equality, not LIKE, so the underscore-wildcard
      caveat from the id_field_map heuristic doesn't apply here.)
    - action_map / type_map: (0, 0) — raw action/type labels are NOT persisted
      on transaction rows (only the mapped transaction_type is), so no
      reference check is possible; archiving is inherently safe (future rows
      fall back to 'other', the documented unknown-label behavior) and delete
      has nothing to guard. Documented in docs/api-specs/reader-mappings.md C3.
    """
    if kind == "known_etf":
        target_ids = [f"US_ETF_{map_key}"] if map_key else []
    elif kind == "symbol_norm":
        to = str(row_value.get("to", "") or "")
        if not to:
            return (0, 0)
        target_ids = [f"{prefix}{to}" for prefix in _SCHWAB_CANONICAL_PREFIXES]
    else:  # action_map / type_map — no asset_id derivation, nothing to check
        return (0, 0)

    holdings_count = 0
    tx_count = 0
    for asset_id in target_ids:
        h, t = _asset_row_counts(db, asset_id)
        holdings_count += h
        tx_count += t
    return holdings_count, tx_count


def _read_schwab_txn_values(column: str) -> "Optional[list]":
    """Read the schwab reader's currently resolved LATEST transactions CSV
    and return the raw values of `column` ('Symbol' or 'Action'), or None if
    no transactions file is resolved. Best-effort: the sync path concatenates
    ALL matching transactions CSVs (select: all), but the scan only needs a
    representative sample of current vocabulary usage — the latest file
    (`_resolve_source_file`'s resolved_files['transactions'], the same
    resolution GET /settings/sources shows) is sufficient and cheap. Never
    raises past the caller's fail-safe wrapper."""
    import pandas as pd  # noqa: PLC0415 — lazy

    settings = settings_manager.load_settings()
    _dir, _primary, _fb, resolved_files = _resolve_source_file(settings, "schwab")
    txn_path = resolved_files.get("transactions")
    if not txn_path or not Path(txn_path).exists():
        return None
    df = pd.read_csv(txn_path)
    if column not in df.columns:
        return []
    return list(df[column].dropna())


def _read_cn_fund_type_values() -> "Optional[list]":
    """Read the cn_fund workbook's 基金交易记录 sheet and return the raw
    操作类型 values, or None if no file is resolved."""
    import pandas as pd  # noqa: PLC0415 — lazy

    file_path = _resolve_reader_file("cn_fund")
    if not file_path:
        return None
    df = pd.read_excel(file_path, sheet_name=_CN_FUND_TXN_SHEET, engine="openpyxl")
    if _CN_FUND_TYPE_COLUMN not in df.columns:
        return []
    return list(df[_CN_FUND_TYPE_COLUMN].dropna())


def _vocab_file_values(reader: str, kind: str) -> "Optional[list]":
    """Raw file values a vocab kind maps over: schwab Symbol (known_etf,
    symbol_norm), schwab Action (action_map), cn_fund 操作类型 (type_map)."""
    if reader == "schwab":
        column = "Action" if kind == "action_map" else "Symbol"
        return _read_schwab_txn_values(column)
    if reader == "cn_fund":
        return _read_cn_fund_type_values()
    return None


def _scan_vocab_unmapped(
    db: DatabaseConnector, reader: str, kind: str, merged: Optional[Dict[str, Any]] = None
) -> "list[UnmappedColumnOut]":
    """Best-effort unmapped-value scan for the GET-list amber strip. Candidates
    are surfaced ONLY for action_map/type_map (an unmapped action/label melts
    to 'other' — a genuine gap worth surfacing). known_etf/symbol_norm return
    [] here: an unmapped symbol is the NORMAL case (most tickers are stocks
    and need no normalization), and listing every ticker would recreate the
    cries-wolf problem A4.1 fixed for fs_column. The preview endpoint still
    shows the full mapped/unmapped scan for every vocab kind. Never raises.
    """
    if kind not in ("action_map", "type_map"):
        return []
    try:
        values = _vocab_file_values(reader, kind)
        if values is None:
            return []
        merged = merged if merged is not None else load_reader_mappings(db, reader, kind)
        scanned = scan_unmapped_vocab_values(values, merged, kind)
        return [
            UnmappedColumnOut(column=item["value"], ignored_native=False, category="candidate", mapping_id=None)
            for item in scanned
            if not item["mapped"]
        ]
    except Exception as e:  # noqa: BLE001 — best-effort, never blocks the caller
        logger.debug("vocab unmapped scan failed for %s/%s (non-blocking): %s", reader, kind, e)
        return []


def _asset_id_conflict(db: DatabaseConnector, reader: str, asset_id: str) -> Optional[str]:
    """Return the conflicting source_system name if asset_id is already used by
    holdings/transactions rows from a DIFFERENT reader's source_system, else None.

    (asset_registry has no source_system column — canonical_id is its PK — so
    the actual cross-reader authority signal lives on holdings/transactions.)
    fs_column only (id_field_map has no single-asset_id create path to guard).
    """
    own_source_system = _READER_SOURCE_SYSTEM[reader]
    row = db.execute(
        """
        SELECT source_system FROM holdings
         WHERE asset_id = ? AND source_system IS NOT NULL AND source_system != ?
        UNION
        SELECT source_system FROM transactions
         WHERE asset_id = ? AND source_system IS NOT NULL AND source_system != ?
        LIMIT 1
        """,
        [asset_id, own_source_system, asset_id, own_source_system],
    ).fetchone()
    return row[0] if row else None


def _asset_row_counts(db: DatabaseConnector, asset_id: str) -> "tuple[int, int]":
    holdings_count = db.execute(
        "SELECT COUNT(*) FROM holdings WHERE asset_id = ?", [asset_id]
    ).fetchone()[0]
    tx_count = db.execute(
        "SELECT COUNT(*) FROM transactions WHERE asset_id = ?", [asset_id]
    ).fetchone()[0]
    return holdings_count, tx_count


def _write_audit(
    db: DatabaseConnector, mapping_id: int, action: str, old_value: Optional[str], new_value: Optional[str]
) -> None:
    db.execute(
        "INSERT INTO reader_mapping_audit (mapping_id, action, old_value, new_value) VALUES (?, ?, ?, ?)",
        [mapping_id, action, old_value, new_value],
    )


# ---------------------------------------------------------------------------
# 1. GET list
# ---------------------------------------------------------------------------


@router.get("/{reader}/mappings", response_model=MappingListResponse)
async def list_mappings(
    reader: str,
    kind: Optional[str] = Query(default=None),
    db: DatabaseConnector = Depends(get_db),
):
    """List all reader_mappings rows (active + archived) for a reader, ordered
    by sort_order then id, plus unmapped-column/label detection against the
    currently uploaded file. Multi-kind readers (schwab) require kind=."""
    kind = _resolve_kind(reader, kind)
    try:
        # 'ignored' rows (ADR-023 A4.1, fs_column only) are column-ignore
        # markers, not real asset mappings — map_value='{}' would fail
        # MappingOut validation. They surface only via unmapped_columns
        # (category='ignored') below.
        rows = db.execute(
            f"{_MAPPING_SELECT} WHERE reader_key = ? AND mapping_kind = ? AND status != 'ignored' "
            "ORDER BY sort_order NULLS LAST, id",
            [reader, kind],
        ).fetchall()
        mappings = [_row_to_out(r) for r in rows]
        merged = load_reader_mappings(db, reader, kind)
        aggregate_checks: list[AggregateCheckOut] = []
        if kind == "ie_column":
            aggregate_checks = _scan_ie_aggregate_checks(db, merged=merged)
        if kind in _SHEET_COLUMN_KINDS:
            unmapped = _scan_reader_unmapped_columns(db, reader, kind, merged=merged)
        elif kind == "id_field_map":
            unmapped = _scan_id_field_map_unmapped(db, reader, merged=merged)
        else:  # WS-C vocab kinds
            unmapped = _scan_vocab_unmapped(db, reader, kind, merged=merged)
        return MappingListResponse(
            reader=reader,
            mapping_kind=kind,
            mappings=mappings,
            defaults_only=len(rows) == 0,
            unmapped_columns=unmapped,
            aggregate_checks=aggregate_checks,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list_mappings failed")
        return api_error_response(e, context="reader_mappings_list")


# ---------------------------------------------------------------------------
# 2. POST create
# ---------------------------------------------------------------------------


def _prepare_fs_column_create(
    writable: DatabaseConnector, reader: str, expected_kind: str, map_key: str, value: Dict[str, Any]
) -> str:
    asset_id = str(value.get("asset_id", "")).strip()
    asset_name = value.get("asset_name")
    currency = value.get("currency")
    if not asset_id:
        raise HTTPException(status_code=422, detail="value.asset_id must not be empty")
    if asset_name is None or currency is None:
        raise HTTPException(
            status_code=422, detail="value.asset_name and value.currency are required for fs_column"
        )
    if currency != "CNY":
        raise HTTPException(
            status_code=422,
            detail=(
                "fs_column currency must be 'CNY' — the Financial Summary Excel stores "
                "owner-converted CNY values in every column (the asset_id _USD/_HKD suffix "
                "is traceability only, not the stored currency)."
            ),
        )
    merged = load_reader_mappings(writable, reader, expected_kind)
    if map_key in merged:
        raise HTTPException(
            status_code=422,
            detail=f"map_key '{map_key}' already has an active mapping for reader '{reader}'",
        )
    existing_asset_ids = {v[0] for v in merged.values()}
    if asset_id in existing_asset_ids:
        raise HTTPException(
            status_code=422,
            detail=f"asset_id '{asset_id}' is already used by another active mapping",
        )
    conflict_source = _asset_id_conflict(writable, reader, asset_id)
    if conflict_source:
        raise HTTPException(
            status_code=409,
            detail=(
                f"asset_id '{asset_id}' already has holdings/transactions from a "
                f"different source_system ('{conflict_source}') — choose a different asset_id."
            ),
        )
    return json.dumps({"asset_id": asset_id, "asset_name": asset_name, "currency": currency}, ensure_ascii=False)


def _prepare_id_field_map_create(
    writable: DatabaseConnector, reader: str, expected_kind: str, map_key: str, value: Dict[str, Any]
) -> str:
    _validate_id_field_map_key(reader, map_key)
    code = _validate_id_field_map_code(value.get("code"))
    merged = load_reader_mappings(writable, reader, expected_kind)
    if map_key in merged:
        raise HTTPException(
            status_code=422,
            detail=f"map_key '{map_key}' already has an active mapping for reader '{reader}'",
        )
    return json.dumps({"code": code}, ensure_ascii=False)


def _prepare_vocab_create(
    writable: DatabaseConnector, reader: str, expected_kind: str, map_key: str, value: Dict[str, Any]
) -> "tuple[str, str]":
    """WS-C vocab create validation: kind-specific value schema (incl. the
    action_map/type_map ALLOWED_TRANSACTION_TYPES enum guardrail) + the
    standard active-duplicate check. Ticker/symbol map_keys must be
    asset_id-safe and are UPPERCASED (the schwab symbol normalizer uppercases
    before lookup — a lowercase key would silently never match); action/type
    raw labels are free text (Schwab actions have spaces, CN Fund labels are
    Chinese). Returns (normalized_map_key, map_value_json)."""
    if expected_kind in ("known_etf", "symbol_norm"):
        map_key = _validate_symbol_token(map_key, "map_key").upper()
    map_value_json = _validate_vocab_value(expected_kind, value)
    merged = load_reader_mappings(writable, reader, expected_kind)
    if map_key in merged:
        raise HTTPException(
            status_code=422,
            detail=f"map_key '{map_key}' already has an active mapping for reader '{reader}'",
        )
    return map_key, map_value_json


@router.post("/{reader}/mappings", response_model=MappingOut, status_code=201)
async def create_mapping(reader: str, body: MappingCreateRequest, db: DatabaseConnector = Depends(get_db)):
    expected_kind = _resolve_kind(reader, body.kind)
    map_key = body.map_key.strip()
    if not map_key:
        raise HTTPException(status_code=422, detail="map_key must not be empty")

    writable = None
    try:
        writable = _open_writable(db)
        if expected_kind == "fs_column":
            map_value_json = _prepare_fs_column_create(writable, reader, expected_kind, map_key, body.value)
        elif expected_kind == "ie_column":
            map_value_json = _prepare_ie_column_create(writable, reader, expected_kind, map_key, body.value)
        elif expected_kind == "id_field_map":
            map_value_json = _prepare_id_field_map_create(writable, reader, expected_kind, map_key, body.value)
        else:  # WS-C vocab kinds
            map_key, map_value_json = _prepare_vocab_create(writable, reader, expected_kind, map_key, body.value)

        # The table's natural UNIQUE key is (reader_key, mapping_kind, map_key)
        # REGARDLESS of status — every code-default map_key already has a DB
        # row from the V75/V77 seed. So "create a mapping for a map_key whose
        # only row is archived" (the plan's archive+create account-closure
        # flow) cannot be a plain INSERT — it would violate the UNIQUE
        # constraint. Reactivate the archived row in place instead (still
        # fully audited).
        existing_row = writable.execute(
            "SELECT id, status, sort_order FROM reader_mappings "
            "WHERE reader_key = ? AND mapping_kind = ? AND map_key = ?",
            [reader, expected_kind, map_key],
        ).fetchone()

        if existing_row is not None:
            # The kind-specific prepare_*_create above already ruled out
            # status == 'active' (map_key in merged), so any row found here
            # must be archived — reactivate it.
            new_id, _old_status, existing_sort_order = existing_row
            old_value_json = writable.execute(
                "SELECT map_value FROM reader_mappings WHERE id = ?", [new_id]
            ).fetchone()[0]
            sort_order = existing_sort_order if existing_sort_order is not None else 0
            writable.execute(
                "UPDATE reader_mappings SET status = 'active', map_value = ?, "
                "sort_order = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [map_value_json, sort_order, new_id],
            )
            _write_audit(writable, new_id, "create", old_value_json, map_value_json)
        else:
            next_sort = writable.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM reader_mappings "
                "WHERE reader_key = ? AND mapping_kind = ?",
                [reader, expected_kind],
            ).fetchone()[0]
            writable.execute(
                "INSERT INTO reader_mappings (reader_key, mapping_kind, map_key, map_value, status, sort_order) "
                "VALUES (?, ?, ?, ?, 'active', ?)",
                [reader, expected_kind, map_key, map_value_json, next_sort],
            )
            new_id = writable.execute(
                "SELECT id FROM reader_mappings WHERE reader_key = ? AND mapping_kind = ? AND map_key = ?",
                [reader, expected_kind, map_key],
            ).fetchone()[0]
            _write_audit(writable, new_id, "create", None, map_value_json)
        mark_dirty()
        row = _fetch_mapping_row(writable, reader, (expected_kind,), new_id)
        return _row_to_out(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("create_mapping failed")
        return api_error_response(e, context="reader_mappings_create")
    finally:
        if writable and writable is not db:
            writable.close()


# ---------------------------------------------------------------------------
# 3. PATCH edit
# ---------------------------------------------------------------------------


def _apply_fs_column_patch(
    writable: DatabaseConnector, reader: str, row: tuple, old_value: dict, new_value: dict, value: Dict[str, Any]
) -> dict:
    if "asset_id" in value and value["asset_id"] is not None:
        new_asset_id = str(value["asset_id"]).strip()
        if not new_asset_id:
            raise HTTPException(status_code=422, detail="value.asset_id must not be empty")
        if new_asset_id != old_value["asset_id"]:
            holdings_count, _tx_count = _asset_row_counts(writable, old_value["asset_id"])
            if holdings_count > 0:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"asset_id '{old_value['asset_id']}' has {holdings_count} holdings rows — "
                        "archive this mapping and create a new one instead of renaming asset_id."
                    ),
                )
            merged = load_reader_mappings(writable, reader, "fs_column")
            existing_asset_ids = {v[0] for k, v in merged.items() if k != row[3]}
            if new_asset_id in existing_asset_ids:
                raise HTTPException(
                    status_code=422,
                    detail=f"asset_id '{new_asset_id}' is already used by another active mapping",
                )
            conflict_source = _asset_id_conflict(writable, reader, new_asset_id)
            if conflict_source:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"asset_id '{new_asset_id}' already has holdings/transactions from a "
                        f"different source_system ('{conflict_source}')."
                    ),
                )
            new_value["asset_id"] = new_asset_id

    if "asset_name" in value and value["asset_name"] is not None:
        new_value["asset_name"] = value["asset_name"]
    return new_value


def _apply_id_field_map_patch(
    writable: DatabaseConnector, reader: str, row: tuple, old_value: dict, new_value: dict, value: Dict[str, Any]
) -> dict:
    if "code" in value and value["code"] is not None:
        new_code = _validate_id_field_map_code(value["code"])
        old_code = old_value.get("code", "")
        if new_code != old_code:
            holdings_count, tx_count = _id_field_map_affected_count(writable, reader, old_code)
            if holdings_count > 0 or tx_count > 0:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot change code: {holdings_count} holdings / {tx_count} transaction "
                        f"asset_ids for reader '{reader}' contain the current code '{old_code}' — "
                        "archive this mapping and create a new one instead."
                    ),
                )
            new_value["code"] = new_code
    return new_value


def _apply_vocab_patch(
    writable: DatabaseConnector, row_kind: str, row: tuple, old_value: dict, new_value: dict, value: Dict[str, Any]
) -> dict:
    """WS-C vocab patch:
    - known_etf: value is fixed {"etf": true} — any value payload other than
      exactly that 422s (there is nothing to edit; archive to remove).
    - symbol_norm: changing `to` when the OLD normalized target already has
      holdings/transactions rows -> 409 (mirrors the C2 code-rename guard) —
      archive + create instead.
    - action_map/type_map: `type` freely editable, validated against
      ALLOWED_TRANSACTION_TYPES (no reference guard possible — raw labels
      aren't persisted; see _vocab_affected_count).
    """
    if not value:
        return new_value
    if row_kind == "known_etf":
        # Only a no-op {"etf": true} payload is accepted (idempotent patch);
        # anything else has no meaning for this kind.
        _validate_vocab_value("known_etf", value)
        return new_value
    if row_kind == "symbol_norm":
        if "to" in value and value["to"] is not None:
            new_to = _validate_symbol_token(value["to"], "value.to").upper()
            old_to = str(old_value.get("to", "") or "")
            if new_to != old_to:
                holdings_count, tx_count = _vocab_affected_count(writable, "symbol_norm", row[3], old_value)
                if holdings_count > 0 or tx_count > 0:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Cannot change normalization target: {holdings_count} holdings / "
                            f"{tx_count} transaction rows reference asset_ids built from '{old_to}' — "
                            "archive this mapping and create a new one instead."
                        ),
                    )
                new_value["to"] = new_to
        return new_value
    # action_map / type_map
    if "type" in value and value["type"] is not None:
        validated_json = _validate_vocab_value(row_kind, {"type": value["type"]})
        new_value["type"] = json.loads(validated_json)["type"]
    return new_value


@router.patch("/{reader}/mappings/{mapping_id}", response_model=MappingOut)
async def patch_mapping(
    reader: str, mapping_id: int, body: MappingPatchRequest, db: DatabaseConnector = Depends(get_db)
):
    kinds = _reader_kinds(reader)
    writable = None
    try:
        writable = _open_writable(db)
        row = _fetch_mapping_row(writable, reader, kinds, mapping_id)
        _reject_if_ignored(row)
        row_kind = row[2]
        old_value_json = row[4]
        old_value = json.loads(old_value_json)
        new_value = dict(old_value)
        value = body.value or {}

        if row_kind == "fs_column":
            new_value = _apply_fs_column_patch(writable, reader, row, old_value, new_value, value)
        elif row_kind == "ie_column":
            new_value = _apply_ie_column_patch(writable, reader, row, old_value, value)
        elif row_kind == "id_field_map":
            new_value = _apply_id_field_map_patch(writable, reader, row, old_value, new_value, value)
        else:  # WS-C vocab kinds
            new_value = _apply_vocab_patch(writable, row_kind, row, old_value, new_value, value)

        if body.sort_order is not None:
            writable.execute(
                "UPDATE reader_mappings SET sort_order = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [body.sort_order, mapping_id],
            )

        new_value_json = json.dumps(new_value, ensure_ascii=False)
        writable.execute(
            "UPDATE reader_mappings SET map_value = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [new_value_json, mapping_id],
        )
        _write_audit(writable, mapping_id, "update", old_value_json, new_value_json)
        mark_dirty()
        updated_row = _fetch_mapping_row(writable, reader, kinds, mapping_id)
        return _row_to_out(updated_row)
    except HTTPException:
        raise
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("patch_mapping failed")
        return api_error_response(e, context="reader_mappings_patch")
    finally:
        if writable and writable is not db:
            writable.close()


# ---------------------------------------------------------------------------
# 4. Archive / Restore
# ---------------------------------------------------------------------------


@router.post("/{reader}/mappings/{mapping_id}/archive", response_model=ArchiveResponse)
async def archive_mapping(reader: str, mapping_id: int, db: DatabaseConnector = Depends(get_db)):
    kinds = _reader_kinds(reader)
    writable = None
    try:
        writable = _open_writable(db)
        row = _fetch_mapping_row(writable, reader, kinds, mapping_id)
        _reject_if_ignored(row)
        row_kind = row[2]
        old_value_json = row[4]
        old_status = row[5]

        writable.execute(
            "UPDATE reader_mappings SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [mapping_id],
        )
        _write_audit(
            writable, mapping_id, "archive",
            json.dumps({"status": old_status}), json.dumps({"status": "archived"}),
        )

        if row_kind == "fs_column":
            asset_id = json.loads(old_value_json)["asset_id"]
            holdings_count, _tx_count = _asset_row_counts(writable, asset_id)
            deactivate_hint = (
                {
                    "asset_id": asset_id,
                    "endpoint": f"/taxonomy/assets/{asset_id}",
                    "method": "DELETE",
                    "note": "Chain this call to also shadow the asset's holdings (account closure).",
                }
                if holdings_count > 0
                else None
            )
        elif row_kind == "ie_column":
            # An ie_column mapping produces no asset_id — it only decides how a
            # 月度收支 ledger column is summed. Archiving one makes the column
            # contribute to nothing (the pre-V82 behaviour for any column that
            # wasn't one of the six hardcoded literals), which is exactly what
            # archiving should mean here. Nothing to count, nothing to chain.
            holdings_count = 0
            deactivate_hint = None
        elif row_kind == "id_field_map":
            # A label:code mapping is a template SEGMENT, not one asset_id, so
            # there's no single deactivation target to chain into (unlike
            # fs_column). asset_has_holdings still reports the conservative
            # LIKE-pattern-affected count.
            code = json.loads(old_value_json).get("code", "")
            holdings_count, _tx_count = _id_field_map_affected_count(writable, reader, code)
            deactivate_hint = None
        else:  # WS-C vocab kinds — archiving restores the exact legacy
            # unknown-value behavior for future syncs (ticker -> US_STK_*,
            # symbol -> slash-to-dash fallback, action/type -> 'other').
            # asset_has_holdings reports the exact-asset_id reference count
            # (known_etf/symbol_norm; always False for action_map/type_map —
            # see _vocab_affected_count). deactivate_hint is always null.
            holdings_count, _tx_count = _vocab_affected_count(
                writable, row_kind, row[3], json.loads(old_value_json)
            )
            deactivate_hint = None

        mark_dirty()
        updated_row = _fetch_mapping_row(writable, reader, kinds, mapping_id)
        return ArchiveResponse(
            mapping=_row_to_out(updated_row),
            asset_has_holdings=holdings_count > 0,
            deactivate_hint=deactivate_hint,
        )
    except HTTPException:
        raise
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("archive_mapping failed")
        return api_error_response(e, context="reader_mappings_archive")
    finally:
        if writable and writable is not db:
            writable.close()


@router.post("/{reader}/mappings/{mapping_id}/restore", response_model=MappingOut)
async def restore_mapping(reader: str, mapping_id: int, db: DatabaseConnector = Depends(get_db)):
    kinds = _reader_kinds(reader)
    writable = None
    try:
        writable = _open_writable(db)
        row = _fetch_mapping_row(writable, reader, kinds, mapping_id)
        _reject_if_ignored(row)
        expected_kind = row[2]
        old_status = row[5]
        map_key = row[3]

        # Defensive only: the table's UNIQUE(reader_key, mapping_kind, map_key)
        # constraint (regardless of status) means two rows can never actually
        # share a map_key today — create_mapping() reactivates the existing
        # archived row in place rather than inserting a second one (see
        # docs/api-specs/reader-mappings.md "Reactivation note"). Kept as a
        # cheap guard in case that invariant ever changes.
        conflict = writable.execute(
            "SELECT id FROM reader_mappings WHERE reader_key = ? AND mapping_kind = ? "
            "AND map_key = ? AND status = 'active' AND id != ?",
            [reader, expected_kind, map_key, mapping_id],
        ).fetchone()
        if conflict:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot restore: another active mapping already uses map_key '{map_key}' "
                    "for this reader. Archive that one first."
                ),
            )

        writable.execute(
            "UPDATE reader_mappings SET status = 'active', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [mapping_id],
        )
        _write_audit(
            writable, mapping_id, "restore",
            json.dumps({"status": old_status}), json.dumps({"status": "active"}),
        )
        mark_dirty()
        updated_row = _fetch_mapping_row(writable, reader, (expected_kind,), mapping_id)
        return _row_to_out(updated_row)
    except HTTPException:
        raise
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("restore_mapping failed")
        return api_error_response(e, context="reader_mappings_restore")
    finally:
        if writable and writable is not db:
            writable.close()


# ---------------------------------------------------------------------------
# 5. DELETE
# ---------------------------------------------------------------------------


@router.delete("/{reader}/mappings/{mapping_id}", response_model=DeleteResponse)
async def delete_mapping(reader: str, mapping_id: int, db: DatabaseConnector = Depends(get_db)):
    kinds = _reader_kinds(reader)
    writable = None
    try:
        writable = _open_writable(db)
        row = _fetch_mapping_row(writable, reader, kinds, mapping_id)
        _reject_if_ignored(row)
        row_kind = row[2]
        old_value_json = row[4]

        if row_kind == "fs_column":
            asset_id = json.loads(old_value_json)["asset_id"]
            holdings_count, tx_count = _asset_row_counts(writable, asset_id)
            ref_kind, ref = "asset_id", asset_id
        elif row_kind == "ie_column":
            # No asset_id/holdings derive from an ie_column row (see the
            # archive branch) — nothing to reference-guard.
            holdings_count, tx_count = 0, 0
            ref_kind, ref = "map_key", row[3]
        elif row_kind == "id_field_map":
            code = json.loads(old_value_json).get("code", "")
            holdings_count, tx_count = _id_field_map_affected_count(writable, reader, code)
            ref_kind, ref = "code", code
        else:  # WS-C vocab kinds — exact-asset_id reference guard for
            # known_etf/symbol_norm; vacuously (0, 0) for action_map/type_map
            # (raw labels aren't persisted — nothing to check; see
            # _vocab_affected_count's docstring).
            old_value = json.loads(old_value_json)
            holdings_count, tx_count = _vocab_affected_count(writable, row_kind, row[3], old_value)
            ref_kind, ref = "map_key", row[3]

        if holdings_count > 0 or tx_count > 0:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot delete: {ref_kind} '{ref}' has {holdings_count} holdings and "
                    f"{tx_count} transaction rows. Archive this mapping instead."
                ),
            )
        writable.execute("DELETE FROM reader_mappings WHERE id = ?", [mapping_id])
        _write_audit(writable, mapping_id, "delete", old_value_json, None)
        mark_dirty()
        if row_kind == "fs_column":
            return DeleteResponse(deleted=mapping_id, asset_id=ref)
        return DeleteResponse(deleted=mapping_id, code=ref)
    except HTTPException:
        raise
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("delete_mapping failed")
        return api_error_response(e, context="reader_mappings_delete")
    finally:
        if writable and writable is not db:
            writable.close()


# ---------------------------------------------------------------------------
# 6. Preview (read-only, no writes)
# ---------------------------------------------------------------------------


def _preview_fs_column(reader: str, expected_kind: str, body: Optional[PreviewRequest], db: DatabaseConnector):
    """Dry-run: resolve the reader's currently uploaded file, run the same
    read (header=3) + melt hook the sync path uses, and report per-mapping
    match stats plus unmapped columns. Never writes; read-only DB connection."""
    merged = load_reader_mappings(db, reader, expected_kind)
    if body and body.proposed:
        for item in body.proposed:
            merged[item.map_key] = (item.value.asset_id, item.value.asset_name, item.value.currency)

    file_path = _resolve_reader_file(reader)
    if not file_path:
        return PreviewResponse(
            reader=reader, mapping_kind=expected_kind, file_path=None, results=[], unmapped_columns=[]
        )

    sheet_df = _read_fs_sheet(file_path)
    from src.sources.reader_hooks import melt_financial_summary_holdings  # lazy — pandas-heavy module

    holdings_df = melt_financial_summary_holdings(sheet_df, {"fs_asset_mappings": merged})

    results: list[PreviewColumnResult] = []
    for map_key, (asset_id, _asset_name, _currency) in merged.items():
        column_found = map_key in sheet_df.columns
        sub = holdings_df[holdings_df["asset_id"] == asset_id] if not holdings_df.empty else holdings_df
        nonzero_rows = len(sub)
        latest_value: Optional[float] = None
        latest_date: Optional[str] = None
        if nonzero_rows:
            # melt_financial_summary_holdings already filters out NaN/zero
            # market_value rows, so latest_row["market_value"] is guaranteed
            # non-null here.
            sub_sorted = sub.sort_values("snapshot_date")
            latest_row = sub_sorted.iloc[-1]
            latest_value = float(latest_row["market_value"])
            sd = latest_row["snapshot_date"]
            latest_date = sd.strftime("%Y-%m-%d") if hasattr(sd, "strftime") else str(sd)
        results.append(
            PreviewColumnResult(
                map_key=map_key,
                column_found=column_found,
                nonzero_rows=nonzero_rows,
                latest_value=latest_value,
                latest_date=latest_date,
            )
        )

    ignored_keys = get_ignored_map_keys(db, reader, expected_kind)
    scanned = scan_unmapped_columns(list(sheet_df.columns), merged, ignored_keys=ignored_keys)
    unmapped_columns = [UnmappedColumnOut(**c) for c in scanned]

    return PreviewResponse(
        reader=reader,
        mapping_kind=expected_kind,
        file_path=file_path,
        results=results,
        unmapped_columns=unmapped_columns,
    )


def _preview_ie_column(reader: str, expected_kind: str, db: DatabaseConnector):
    """Dry-run for the 月度收支 column semantics (plan 2026-08-01 WS-A).

    Reads the reader's currently uploaded workbook's 月度收支 sheet the same way
    the sync path does (header=3) and reports, per mapped column, whether the
    column is present and how many months carry a non-zero value plus the
    latest one — the same PreviewColumnResult shape fs_column uses, so the UI
    needs no second response model. Read-only; no `proposed` overlay (mirrors
    the WS-B/WS-C preview scope).

    A column mapped `currency='USD'` still reports its own stats here (that is
    what the sheet contains); those values contribute to no total anywhere —
    see src.services.investment_contributions.
    """
    merged = load_reader_mappings(db, reader, expected_kind)
    file_path = _resolve_reader_file(reader)
    if not file_path:
        return PreviewResponse(
            reader=reader, mapping_kind=expected_kind, file_path=None, results=[], unmapped_columns=[]
        )

    sheet_df = _read_fs_sheet(file_path, _IE_SHEET_NAME)
    import pandas as pd  # noqa: PLC0415 — lazy, pandas-heavy

    columns_by_stripped = {str(c).strip(): c for c in sheet_df.columns}
    date_column = columns_by_stripped.get("日期")

    results: list[PreviewColumnResult] = []
    for map_key in merged:
        column = columns_by_stripped.get(str(map_key).strip())
        if column is None:
            results.append(
                PreviewColumnResult(map_key=map_key, column_found=False, nonzero_rows=0)
            )
            continue
        values = pd.to_numeric(sheet_df[column], errors="coerce").fillna(0.0)
        nonzero_mask = values != 0.0
        nonzero_rows = int(nonzero_mask.sum())
        latest_value: Optional[float] = None
        latest_date: Optional[str] = None
        if nonzero_rows:
            idx = values[nonzero_mask].index[-1]
            latest_value = float(values.loc[idx])
            if date_column is not None:
                raw_date = sheet_df.loc[idx, date_column]
                latest_date = (
                    raw_date.strftime("%Y-%m-%d") if hasattr(raw_date, "strftime") else str(raw_date)
                )
        results.append(
            PreviewColumnResult(
                map_key=map_key,
                column_found=True,
                nonzero_rows=nonzero_rows,
                latest_value=latest_value,
                latest_date=latest_date,
            )
        )

    ignored_keys = _strip_keys(get_ignored_map_keys(db, reader, expected_kind))
    scanned = scan_unmapped_columns(
        list(sheet_df.columns), _strip_keys(merged), ignored_keys=ignored_keys
    )
    unmapped_columns = [UnmappedColumnOut(**c) for c in scanned]

    return PreviewResponse(
        reader=reader,
        mapping_kind=expected_kind,
        file_path=file_path,
        results=results,
        unmapped_columns=unmapped_columns,
    )


def _preview_id_field_map(reader: str, expected_kind: str, db: DatabaseConnector):
    """Dry-run for gold/insurance/rsu (WS-B): scan the reader's currently
    uploaded file for id-source label values and report mapped-vs-unmapped
    against the merged (defaults + DB overrides) id_field_map. Read-only, no
    proposed-override merge (unlike fs_column's preview) — WS-B scope."""
    merged = load_reader_mappings(db, reader, expected_kind)
    file_path = _resolve_reader_file(reader)
    if not file_path:
        return IdFieldMapPreviewResponse(
            reader=reader, mapping_kind=expected_kind, file_path=None, items=[], unmapped_columns=[]
        )

    reader_cfg = _load_reader_cfg(reader)
    field_labels = _extract_field_labels(reader_cfg, file_path)
    scanned = scan_unmapped_id_field_map_labels(field_labels, merged)
    items = [IdFieldMapPreviewItem(**item) for item in scanned]
    unmapped_columns = [
        UnmappedColumnOut(column=item.map_key, ignored_native=False, category="candidate", mapping_id=None)
        for item in items
        if not item.mapped
    ]
    return IdFieldMapPreviewResponse(
        reader=reader, mapping_kind=expected_kind, file_path=file_path, items=items, unmapped_columns=unmapped_columns
    )


def _preview_vocab(reader: str, expected_kind: str, db: DatabaseConnector):
    """Dry-run for the WS-C vocab kinds: scan the reader's currently resolved
    file (schwab transactions CSV Symbol/Action columns; cn_fund 基金交易记录
    操作类型 column) and report mapped-vs-unmapped against the merged
    (defaults + DB overrides) vocabulary. Read-only, no proposed-overlay
    support (mirrors the WS-B preview scope). unmapped_columns candidates are
    surfaced only for action_map/type_map — see _scan_vocab_unmapped."""
    merged = load_reader_mappings(db, reader, expected_kind)
    values = _vocab_file_values(reader, expected_kind)
    if values is None:
        return VocabPreviewResponse(
            reader=reader, mapping_kind=expected_kind, file_path=None, items=[], unmapped_columns=[]
        )

    # Report the actually-resolved file path for the modal subtitle.
    if reader == "schwab":
        settings = settings_manager.load_settings()
        _dir, _primary, _fb, resolved_files = _resolve_source_file(settings, "schwab")
        file_path = resolved_files.get("transactions")
    else:
        file_path = _resolve_reader_file(reader)

    scanned = scan_unmapped_vocab_values(values, merged, expected_kind)
    items = [VocabPreviewItem(**item) for item in scanned]
    if expected_kind in ("action_map", "type_map"):
        unmapped_columns = [
            UnmappedColumnOut(column=item.value, ignored_native=False, category="candidate", mapping_id=None)
            for item in items
            if not item.mapped
        ]
    else:
        # known_etf/symbol_norm: an unmapped symbol is the NORMAL case (most
        # tickers are stocks needing no entry) — no candidates surfaced, only
        # the full mapped/unmapped item list above.
        unmapped_columns = []
    return VocabPreviewResponse(
        reader=reader, mapping_kind=expected_kind, file_path=file_path, items=items, unmapped_columns=unmapped_columns
    )


@router.post("/{reader}/mappings/preview")
async def preview_mappings(
    reader: str,
    kind: Optional[str] = Query(default=None),
    body: Optional[PreviewRequest] = Body(default=None),
    db: DatabaseConnector = Depends(get_db),
):
    """Dry-run preview — response shape depends on mapping_kind:
    PreviewResponse for fs_column and ie_column, IdFieldMapPreviewResponse for
    id_field_map, VocabPreviewResponse for the WS-C vocab kinds (no single
    response_model is declared on the route for this reason). Multi-kind
    readers require kind= unless they declare a default (financial_summary ->
    fs_column)."""
    expected_kind = _resolve_kind(reader, kind)
    try:
        if expected_kind == "fs_column":
            return _preview_fs_column(reader, expected_kind, body, db)
        if expected_kind == "ie_column":
            return _preview_ie_column(reader, expected_kind, db)
        if expected_kind == "id_field_map":
            return _preview_id_field_map(reader, expected_kind, db)
        return _preview_vocab(reader, expected_kind, db)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("preview_mappings failed")
        return api_error_response(e, context="reader_mappings_preview")


# ---------------------------------------------------------------------------
# 7. Ignore column / Unignore (ADR-023 A4.1) — fs_column only
# ---------------------------------------------------------------------------


def _require_fs_column(reader: str) -> str:
    """ignore-column/unignore are fs_column-only (an 'unmapped Excel column'
    concept that doesn't apply to id_field_map's label scanning or the WS-C
    vocab kinds)."""
    kinds = _reader_kinds(reader)
    if "fs_column" not in kinds:
        raise HTTPException(
            status_code=404,
            detail=(
                f"ignore-column/unignore is only supported for fs_column readers, "
                f"not '{reader}' ({sorted(kinds)})."
            ),
        )
    return "fs_column"


@router.post("/{reader}/mappings/ignore-column", response_model=UnmappedColumnOut, status_code=201)
async def ignore_column(reader: str, body: IgnoreColumnRequest, db: DatabaseConnector = Depends(get_db)):
    """Mark a currently-unmapped column as 'ignored' — an owner decision that
    this column is never melted into holdings (structural non-asset column
    the automatic native/computed/liability rules didn't catch, e.g. a stray
    label or a reader-covered informational duplicate). Upserts a
    reader_mappings row with status='ignored', map_value='{}' (reuses the
    same archived-row reactivation pattern as create_mapping — the table's
    UNIQUE(reader_key, mapping_kind, map_key) constraint applies regardless
    of status)."""
    expected_kind = _require_fs_column(reader)
    map_key = body.map_key.strip()
    if not map_key:
        raise HTTPException(status_code=422, detail="map_key must not be empty")

    writable = None
    try:
        writable = _open_writable(db)
        merged = load_reader_mappings(writable, reader, expected_kind)
        if map_key in merged:
            raise HTTPException(
                status_code=422,
                detail=f"map_key '{map_key}' already has an active mapping for reader '{reader}' — archive it first.",
            )

        existing_row = writable.execute(
            "SELECT id, sort_order FROM reader_mappings "
            "WHERE reader_key = ? AND mapping_kind = ? AND map_key = ?",
            [reader, expected_kind, map_key],
        ).fetchone()

        if existing_row is not None:
            mapping_id, existing_sort_order = existing_row
            old_value_json = writable.execute(
                "SELECT map_value FROM reader_mappings WHERE id = ?", [mapping_id]
            ).fetchone()[0]
            writable.execute(
                "UPDATE reader_mappings SET status = 'ignored', map_value = '{}', "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [mapping_id],
            )
            _write_audit(writable, mapping_id, "ignore", old_value_json, "{}")
        else:
            next_sort = writable.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM reader_mappings "
                "WHERE reader_key = ? AND mapping_kind = ?",
                [reader, expected_kind],
            ).fetchone()[0]
            writable.execute(
                "INSERT INTO reader_mappings (reader_key, mapping_kind, map_key, map_value, status, sort_order) "
                "VALUES (?, ?, ?, '{}', 'ignored', ?)",
                [reader, expected_kind, map_key, next_sort],
            )
            mapping_id = writable.execute(
                "SELECT id FROM reader_mappings WHERE reader_key = ? AND mapping_kind = ? AND map_key = ?",
                [reader, expected_kind, map_key],
            ).fetchone()[0]
            _write_audit(writable, mapping_id, "ignore", None, "{}")
        mark_dirty()
        return UnmappedColumnOut(column=map_key, ignored_native=False, category="ignored", mapping_id=mapping_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("ignore_column failed")
        return api_error_response(e, context="reader_mappings_ignore_column")
    finally:
        if writable and writable is not db:
            writable.close()


@router.post("/{reader}/mappings/{mapping_id}/unignore", response_model=UnignoreResponse)
async def unignore_column(reader: str, mapping_id: int, db: DatabaseConnector = Depends(get_db)):
    """Delete an 'ignored' row outright — the owner changed their mind and
    this column should go back to being scanned normally (it will reappear in
    unmapped_columns, likely as 'candidate', on the next scan).

    Deliberately NOT folded into POST .../restore: restore flips status back
    to 'active', but an ignored row's map_value is '{}' (no asset_id/
    asset_name/currency) — reactivating it as 'active' would produce an
    invalid mapping. A plain delete is the clean un-ignore (see
    docs/api-specs/reader-mappings.md 'ignore-column'/'unignore' section)."""
    expected_kind = _require_fs_column(reader)
    writable = None
    try:
        writable = _open_writable(db)
        row = _fetch_mapping_row(writable, reader, (expected_kind,), mapping_id)
        map_key, status = row[3], row[5]
        if status != "ignored":
            raise HTTPException(
                status_code=422,
                detail=f"mapping {mapping_id} is not an ignored column (status='{status}').",
            )
        writable.execute("DELETE FROM reader_mappings WHERE id = ?", [mapping_id])
        _write_audit(writable, mapping_id, "unignore", "{}", None)
        mark_dirty()
        return UnignoreResponse(unignored=mapping_id, map_key=map_key)
    except HTTPException:
        raise
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("unignore_column failed")
        return api_error_response(e, context="reader_mappings_unignore")
    finally:
        if writable and writable is not db:
            writable.close()
