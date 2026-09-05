"""Loader for UI-managed reader mappings (ADR-023 / Reader Mapping Management WS-A).

`load_reader_mappings(connector, reader_key, kind)` merges the code-level
defaults (single source of truth: ``src.database.mapping_seeds``) with any
DB-persisted overrides in the `reader_mappings` table:

  - DB rows with ``status='active'`` overlay/override the default at the same
    ``map_key`` (add a new mapping, or edit an existing one).
  - DB rows with ``status='archived'`` or ``status='ignored'`` REMOVE the key
    from the merged dict entirely. ``archived`` is the "account closure" UX
    (a mapping that used to produce a real asset_id, now retired). ``ignored``
    (ADR-023 A4.1) is a column that never had a real asset mapping and never
    will — the owner reviewed it and marked it "not melted by design" (e.g.
    an FS column that's an informational duplicate of data another reader
    already owns). Both stop the key from reaching the melt hook; the
    distinction only matters to the unmapped-column scan's `category` field.
  - If the `reader_mappings` table does not exist yet (pre-migration DB) or
    the query otherwise fails, this returns the code defaults unchanged —
    it must never raise, since it sits on the sync hot path.

Kind-specific JSON decoding: ``fs_column`` rows store
``{"asset_id": ..., "asset_name": ..., "currency": ...}`` in `map_value`;
this loader deserializes that to the ``(asset_id, asset_name, currency)``
tuple shape the FS melt hook expects (matching the code-default shape).
``ie_column`` rows (the FS 月度收支 sheet's column semantics, plan
2026-08-01 WS-A / migration V82) store ``{"role", "bucket", "currency"}``
and decode to an ``IEColumn`` NamedTuple.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, Tuple

from src.database.mapping_seeds import (
    FS_ASSET_MAPPING_SEED,
    ID_FIELD_MAP_SEEDS,
    IE_COLUMN_SEED,
    IEColumn,
    SCHWAB_KNOWN_ETFS_SEED,
    SCHWAB_SYMBOL_NORMALIZATIONS_SEED,
    SCHWAB_ACTION_MAPPING_SEED,
    CN_FUND_TYPE_MAP_SEED,
)

logger = logging.getLogger(__name__)

# ADR-023 WS-C — the fixed transaction_type enum action_map/type_map values
# must belong to. No single canonical enum exists elsewhere in the codebase
# (grepped schema.sql, source_format_validator.py, xirr.py/twr.py's
# OUTFLOW/INFLOW_TRANSACTION_TYPES — the latter two are cash-flow-classification
# subsets, not exhaustive); this is the union of every transaction_type
# literal actually produced by the reader/hook pipeline today (Schwab action
# map, CN Fund/Gold/RSU type maps, RSU vest, insurance premium, transfer
# in/out, interest, plus the universal 'other' fallback).
#
# 'transfer' (Attribution & Flows WS-3.1, V79) is the one exception: a
# pseudo-type, not a real per-row transaction_type. Schwab's 'Security
# Transfer' action is directionally ambiguous (one label covers both ACAT
# legs), so it is seeded as action_map -> 'transfer' and immediately resolved
# to 'transfer_out'/'transfer_in' by quantity sign inside
# schwab_transactions_from_csv (src/sources/reader_hooks.py) — it is never
# persisted on a transactions row. Allowed here (not a dedicated allowance)
# so the same enum drives both API validation (_validate_vocab_value) and the
# UI's action_map dropdown (ux-command-center's ALLOWED_TRANSACTION_TYPES
# mirror) without a second code path.
ALLOWED_TRANSACTION_TYPES: "frozenset[str]" = frozenset({
    "buy", "sell", "dividend", "dividend_cash", "dividend_reinvest", "reinvest_dividend",
    "tax_adjustment", "stock_split", "transfer_in", "transfer_out", "transfer", "vest", "rsu_vest",
    "premium_payment", "adjustment_buy", "interest", "other",
})

# (reader_key, mapping_kind) -> default dict (code constant, single source of truth).
# Values are pre-decoded to the SAME shape _KIND_DECODERS produces from a DB
# row's JSON map_value, so the merge in load_reader_mappings() below is a
# plain dict overlay regardless of whether a key's value came from a default
# or a DB override.
def _legacy_defaults() -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Byte-for-byte the historical _DEFAULTS construction — read directly
    from src.database.mapping_seeds. Used whenever $UIS_SEED_PROFILE is
    unset, which is every deployment today, including production. Must
    never change except in lockstep with mapping_seeds.py itself."""
    return {
        ("financial_summary", "fs_column"): FS_ASSET_MAPPING_SEED,
        # WS-A (plan 2026-08-01) — 月度收支 column semantics, V82.
        ("financial_summary", "ie_column"): IE_COLUMN_SEED,
        ("gold", "id_field_map"): ID_FIELD_MAP_SEEDS.get("gold", {}),
        ("insurance", "id_field_map"): ID_FIELD_MAP_SEEDS.get("insurance", {}),
        ("rsu", "id_field_map"): ID_FIELD_MAP_SEEDS.get("rsu", {}),
        # ADR-023 WS-C
        ("schwab", "known_etf"): {ticker: True for ticker in SCHWAB_KNOWN_ETFS_SEED},
        ("schwab", "symbol_norm"): dict(SCHWAB_SYMBOL_NORMALIZATIONS_SEED),
        ("schwab", "action_map"): dict(SCHWAB_ACTION_MAPPING_SEED),
        ("cn_fund", "type_map"): dict(CN_FUND_TYPE_MAP_SEED),
    }


def _get_defaults() -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(reader_key, mapping_kind) -> default dict — the runtime baseline
    load_reader_mappings() overlays DB rows on top of.

    Program OSR WS-3b two-step opt-in rollout: ONE explicit trigger,
    $UIS_SEED_PROFILE. Unset (every deployment today, incl. production) ->
    _legacy_defaults(), unchanged. Set -> the seed-pack loader. Deliberately
    NOT a filesystem-presence check on a seeds/<profile>/ directory — that
    would be an invisible-state trap (a directory silently changing sync
    behavior with no signal). See seeds/README.md and the production-safety
    analysis in docs/plans/2026-08-16-ws1-swap-impact.md §5.

    Recomputed on every call (not cached at import time) so tests can
    monkeypatch the env var per-test/per-session without import-order games.
    """
    profile = os.environ.get("UIS_SEED_PROFILE")
    if not profile:
        return _legacy_defaults()
    from src.database.seed_loader import load_seed_pack  # noqa: PLC0415 — lazy, avoids import cost when unused
    return load_seed_pack(profile).reader_mappings


def _decode_fs_column(map_value: str) -> Tuple[str, str, str]:
    """Deserialize an fs_column map_value JSON payload to the (asset_id, asset_name, currency) tuple."""
    payload = json.loads(map_value)
    return (payload["asset_id"], payload["asset_name"], payload["currency"])


def _decode_ie_column(map_value: str) -> IEColumn:
    """Deserialize an ie_column map_value JSON payload to an IEColumn.

    Shape: ``{"role": ..., "bucket": ... | null, "currency": ...,
    "group"?: ..., "validates"?: {...}}`` (plan 2026-08-01 WS-A / migration
    V82). `role` and `currency` are required — a row missing either is a
    malformed override, and load_reader_mappings logs + skips it rather than
    letting a half-decoded value reach the ledger math. `bucket` is optional
    (null for columns that need no grouping); `group` / `validates` are the
    cross-validation fields (leaf subtotal tag / aggregate target) and are
    optional — a row written before they existed decodes with both None and is
    back-filled from the code seed by
    src.services.ie_ledger.load_ie_column_mapping.
    """
    payload = json.loads(map_value)
    role = payload["role"]
    currency = payload["currency"]
    bucket = payload.get("bucket")
    group = payload.get("group")
    validates = payload.get("validates")
    return IEColumn(
        role=role,
        bucket=bucket,
        currency=currency,
        group=group,
        validates=validates if isinstance(validates, dict) else None,
    )


def _decode_id_field_map(map_value: str) -> str:
    """Deserialize an id_field_map map_value JSON payload ({"code": ...}) to the code string."""
    return json.loads(map_value)["code"]


def _decode_known_etf(map_value: str) -> bool:
    """Deserialize a known_etf map_value JSON payload ({"etf": true}) to a bool.

    ADR-023 WS-C. An archived row REMOVES the key from the merged dict (see
    load_reader_mappings's status handling below) — there is no "etf: false"
    value in practice, but decoding defensively via .get() costs nothing.
    """
    return bool(json.loads(map_value).get("etf", False))


def _decode_symbol_norm(map_value: str) -> str:
    """Deserialize a symbol_norm map_value JSON payload ({"to": ...}) to the target symbol string."""
    return json.loads(map_value)["to"]


def _decode_type_value(map_value: str) -> str:
    """Deserialize an action_map/type_map map_value JSON payload ({"type": ...})
    to the transaction_type string. Shared by both kinds — same shape."""
    return json.loads(map_value)["type"]


# mapping_kind -> decoder(map_value: str) -> value shape expected by the hook.
# Kinds with no decoder registered are passed through as the raw stored string.
_KIND_DECODERS: Dict[str, Callable[[str], Any]] = {
    "fs_column": _decode_fs_column,
    "ie_column": _decode_ie_column,
    "id_field_map": _decode_id_field_map,
    "known_etf": _decode_known_etf,
    "symbol_norm": _decode_symbol_norm,
    "action_map": _decode_type_value,
    "type_map": _decode_type_value,
}


def nest_id_field_map(flat: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    """Convert a flat {"field:label": code} dict (the map_key shape stored in
    `reader_mappings` for mapping_kind='id_field_map') into the nested
    {field: {label: code}} shape the config-driven engine expects — matches
    `src.sources.reader_config.SheetConfig.id_field_maps`'s shape exactly, so
    it can be passed straight through as
    `ConfigDrivenReader(cfg, id_field_maps_override=...)`.
    """
    nested: Dict[str, Dict[str, str]] = {}
    for map_key, code in flat.items():
        field, sep, label = map_key.partition(":")
        if not sep:
            # Malformed map_key (the API validates 'field:label' on every
            # write, so this should never happen in practice) — skip
            # defensively rather than raise on the sync hot path.
            logger.warning(
                "nest_id_field_map: malformed map_key %r (expected 'field:label') — skipping",
                map_key,
            )
            continue
        nested.setdefault(field, {})[label] = code
    return nested


def load_id_field_maps(connector: Any, reader_key: str) -> Dict[str, Dict[str, str]]:
    """Load the merged (defaults + DB overrides) id_field_map mappings for a
    reader, nested as {field: {label: code}} — the shape
    `ConfigDrivenReader(..., id_field_maps_override=...)` expects.

    Thin wrapper over ``load_reader_mappings(connector, reader_key,
    "id_field_map")`` (flat "field:label" -> code) + `nest_id_field_map`.
    Kept as a separate entry point so sync-path callers (the orchestrator,
    which needs the nested engine shape) don't have to re-derive the nesting
    themselves, while API/scan callers that want the flat map_key form can
    keep calling `load_reader_mappings` directly.
    """
    flat = load_reader_mappings(connector, reader_key, "id_field_map")
    return nest_id_field_map(flat)


def load_reader_mappings(connector: Any, reader_key: str, kind: str) -> Dict[str, Any]:
    """Load merged reader mappings: code defaults overlaid by DB rows.

    Args:
        connector: A DatabaseConnector (or any object exposing `.execute()`
            returning a DuckDB-style cursor) — expected to be the sync's own
            connection so no second connection is opened mid-sync.
        reader_key: e.g. 'financial_summary'.
        kind: e.g. 'fs_column'.

    Returns:
        dict keyed by map_key (e.g. Excel column name), value shape depends
        on `kind` (fs_column -> (asset_id, asset_name, currency) tuple).
        Always returns at least the code defaults, even if the table is
        missing or the query fails.
    """
    result: Dict[str, Any] = dict(_get_defaults().get((reader_key, kind), {}))
    decoder = _KIND_DECODERS.get(kind)

    try:
        rows = connector.execute(
            """
            SELECT map_key, map_value, status
            FROM reader_mappings
            WHERE reader_key = ? AND mapping_kind = ?
            """,
            [reader_key, kind],
        ).fetchall()
    except Exception as e:
        logger.debug(
            "load_reader_mappings(%s, %s): reader_mappings table unavailable, "
            "using code defaults only (%s)",
            reader_key, kind, e,
        )
        return result

    for map_key, map_value, status in rows:
        if status in ("archived", "ignored"):
            # 'ignored' rows (ADR-023 A4.1 — a column the owner has reviewed
            # and decided is never melted, e.g. an informational duplicate of
            # data another reader already owns) behave like 'archived' here:
            # removed from the merged dict, never fed to the melt hook.
            result.pop(map_key, None)
            continue
        if decoder is not None:
            try:
                result[map_key] = decoder(map_value)
            except Exception as e:
                logger.warning(
                    "load_reader_mappings(%s, %s): could not decode map_value "
                    "for key %r — skipping this override (%s)",
                    reader_key, kind, map_key, e,
                )
                continue
        else:
            result[map_key] = map_value

    return result


# -----------------------------------------------------------------------------
# Unmapped-column detection (ADR-023 / WS-A A3)
#
# Shared, pandas-free heuristic used by BOTH:
#   - src.api.routes.settings._compute_fs_unmapped_count (the /settings/sources
#     "unmapped_count" amber-chip field — cheap per-source scan)
#   - src.api.routes.reader_mappings (the mappings list + preview endpoints)
# living here (not in either route module) keeps the two call sites from having
# to import from each other and risking a cycle.
# -----------------------------------------------------------------------------

_NATIVE_CURRENCY_SUFFIXES = ("_USD", "_HKD")
_COMPUTED_PREFIXES = ("合计",)
_COMPUTED_SUBSTRINGS = ("比例", "资产负债率")
_COMPUTED_EXACT = ("USD Rate",)
_LIABILITY_PREFIXES = ("短期负债", "长期负债", "其他负债")

# category values, in classification precedence order (see scan_unmapped_columns):
#   ignored   — an explicit reader_mappings row with status='ignored' exists for
#               this map_key (owner decision, e.g. FS_IGNORED_COLUMNS_SEED).
#   native    — currency-sibling column of a CNY-converted mapped column
#               (header ends in _USD/_HKD). Structural rule.
#   computed  — a total/ratio column (合计* / *比例* / *资产负债率* / "USD Rate").
#               Structural rule.
#   liability — a liability column (短期负债_*/长期负债_*/其他负债*) that the
#               Balance Sheet report reads separately and intentionally does
#               NOT melt into holdings. Structural rule.
#   candidate — none of the above: a genuinely actionable gap. Only this
#               category counts toward the amber-chip unmapped_count.
_CATEGORIES = ("ignored", "native", "computed", "liability", "candidate")


def _is_computed(col_str: str) -> bool:
    if col_str.startswith(_COMPUTED_PREFIXES):
        return True
    if any(sub in col_str for sub in _COMPUTED_SUBSTRINGS):
        return True
    return col_str in _COMPUTED_EXACT


def get_ignored_map_keys(connector: Any, reader_key: str, kind: str) -> "dict[str, int]":
    """Return {map_key: row_id} for every status='ignored' row on (reader_key, kind).

    Feeds the `category: 'ignored'` classification in scan_unmapped_columns —
    the id is carried through as `mapping_id` so the UI's "Unignore" action
    (POST .../mappings/{id}/unignore) has something to call; ignored rows are
    otherwise invisible to the regular mappings list (see
    src.api.routes.reader_mappings.list_mappings). A plain dict also works
    everywhere a set was previously expected (membership via `in`).
    Never raises (mirrors load_reader_mappings's missing-table fallback) —
    this is a best-effort input to a hint chip, never a hard dependency.
    """
    try:
        rows = connector.execute(
            "SELECT map_key, id FROM reader_mappings "
            "WHERE reader_key = ? AND mapping_kind = ? AND status = 'ignored'",
            [reader_key, kind],
        ).fetchall()
    except Exception as e:
        logger.debug(
            "get_ignored_map_keys(%s, %s): reader_mappings table unavailable (%s)",
            reader_key, kind, e,
        )
        return {}
    return {map_key: row_id for map_key, row_id in rows}


def scan_unmapped_columns(
    sheet_columns: Any,
    merged: Dict[str, Any],
    date_column: str = "日期",
    ignored_keys: "dict[str, int] | set[str] | None" = None,
) -> "list[Dict[str, Any]]":
    """Classify raw sheet column headers against a merged reader-mapping dict.

    Heuristic (deliberately simple — see docs/plans/2026-07-18-reader-mapping-management.md
    and its A4.1 refinement): applied in this precedence order per column —
      0. skip the date column (exact match) and blank/pandas-default headers
         (empty string, or a header starting with "Unnamed" — pandas' fallback
         name for a blank header cell) — these are not asset columns, and are
         not reported at all (not even as a category).
      1. skip any column already present as an (active) key in `merged` — a
         real, actionable mapping already exists.
      2. `category='ignored'` — an explicit reader_mappings row with
         status='ignored' exists for this map_key (an owner decision about
         this specific column — see `get_ignored_map_keys`).
      3. `category='native'` (`ignored_native=True`, kept for backward
         compat) — header ends in "_USD"/"_HKD": a native-currency sibling of
         a CNY-converted mapped column (e.g. "美元存款_中行_USD" alongside the
         mapped "美元存款_中行").
      4. `category='computed'` — a total/ratio column: starts with "合计", or
         contains "比例" or "资产负债率", or equals "USD Rate".
      5. `category='liability'` — starts with "短期负债"/"长期负债"/"其他负债":
         the Balance Sheet report reads these separately and intentionally
         does NOT melt them into holdings.
      6. `category='candidate'` — everything else: a genuinely actionable
         gap. This is the ONLY category that should count toward the
         amber-chip unmapped_count.

    This is a best-effort scan, not exhaustive precision — the UI surfaces
    the result as a hint chip, not a hard validation error.

    Args:
        sheet_columns: iterable of raw column header values (e.g.
            ``sheet_df.columns`` — accepts any stringifiable iterable so
            callers don't need a pandas dependency just to call this).
        merged: the merged (defaults + DB overrides) mapping dict as returned
            by ``load_reader_mappings`` — only its keys are used here.
        date_column: the sheet's date column header (financial_summary: "日期").
        ignored_keys: {map_key: mapping_row_id} for status='ignored' rows (see
            ``get_ignored_map_keys``) — a plain set also works (membership
            only; `mapping_id` is then omitted). Defaults to empty.

    Returns:
        List of ``{"column": str, "ignored_native": bool, "category": str,
        "mapping_id": int | None}`` dicts, in the same order as
        `sheet_columns`. `mapping_id` is only non-null for `category='ignored'`
        entries (needed by the UI's "Unignore" action, which deletes the row
        by id — ignored rows are otherwise excluded from the regular
        mappings list, see src.api.routes.reader_mappings.list_mappings).
    """
    ignored_keys = ignored_keys if ignored_keys is not None else {}
    mapped_keys = set(merged.keys())
    out: "list[Dict[str, Any]]" = []
    for col in sheet_columns:
        col_str = str(col).strip()
        if not col_str or col_str == date_column or col_str.startswith("Unnamed"):
            continue
        if col_str in mapped_keys:
            continue

        mapping_id = None
        if col_str in ignored_keys:
            category = "ignored"
            if isinstance(ignored_keys, dict):
                mapping_id = ignored_keys[col_str]
        elif col_str.endswith(_NATIVE_CURRENCY_SUFFIXES):
            category = "native"
        elif _is_computed(col_str):
            category = "computed"
        elif col_str.startswith(_LIABILITY_PREFIXES):
            category = "liability"
        else:
            category = "candidate"

        out.append(
            {
                "column": col_str,
                "ignored_native": category == "native",
                "category": category,
                "mapping_id": mapping_id,
            }
        )
    return out


# -----------------------------------------------------------------------------
# id_field_map label scanning (ADR-023 WS-B)
#
# Mirrors scan_unmapped_columns's pandas-free design: the caller (API route)
# does the file read with pandas and passes plain label values in here.
# id_field_map has no native/computed/liability/ignored categories (those are
# fs_column-specific, structural rules over Financial Summary Excel column
# headers) — a label is either mapped or a candidate. See
# docs/api-specs/reader-mappings.md.
# -----------------------------------------------------------------------------


def scan_unmapped_id_field_map_labels(
    field_labels: "Dict[str, Any]",
    merged_flat: Dict[str, Any],
) -> "list[Dict[str, Any]]":
    """Classify raw (field, label) pairs found in a reader's current file
    against the merged (defaults + DB overrides) flat id_field_map dict.

    Args:
        field_labels: {field_name: [raw label values found in the file]} —
            e.g. {"asset_name": ["纸黄金", "黄金ETF"], "account": ["招行", "工行"]}.
            Values may repeat (e.g. one row per snapshot date) — de-duplicated
            here, first-encounter order preserved.
        merged_flat: the merged flat dict as returned by
            ``load_reader_mappings(connector, reader_key, "id_field_map")`` —
            keys are "field:label" map_keys, values are code strings.

    Returns:
        List of ``{"field": str, "label": str, "map_key": str, "mapped":
        bool, "code": str | None}`` for every unique (field, label) pair
        found in the file, blank/NaN-ish values excluded. Only
        ``mapped=False`` entries are genuinely actionable gaps (the
        unmapped_count / amber-chip candidates for these readers).
    """
    out: "list[Dict[str, Any]]" = []
    seen: "set[Tuple[str, str]]" = set()
    for field, labels in field_labels.items():
        for raw in labels:
            label = str(raw).strip()
            if not label or label.lower() in ("nan", "none"):
                continue
            key = (field, label)
            if key in seen:
                continue
            seen.add(key)
            map_key = f"{field}:{label}"
            mapped = map_key in merged_flat
            out.append(
                {
                    "field": field,
                    "label": label,
                    "map_key": map_key,
                    "mapped": mapped,
                    "code": merged_flat.get(map_key) if mapped else None,
                }
            )
    return out


# -----------------------------------------------------------------------------
# WS-C vocab value scanning (schwab known_etf/symbol_norm/action_map, cn_fund
# type_map)
#
# Mirrors scan_unmapped_id_field_map_labels's design: the caller (API route)
# reads the current CSV/Excel with pandas and passes plain string values in
# here. Every vocab kind is either mapped or a candidate — no
# native/computed/liability/ignored categories (fs_column-specific).
# -----------------------------------------------------------------------------


def scan_unmapped_vocab_values(
    values: "list[Any]",
    merged: Dict[str, Any],
    kind: str,
) -> "list[Dict[str, Any]]":
    """Classify raw values (tickers, raw actions, raw CN Fund operation labels)
    found in a reader's current file against the merged vocab dict.

    Args:
        values: raw string values found in the file (e.g. every unique
            Symbol in the Schwab transactions CSV for known_etf/symbol_norm,
            every unique Action for action_map, every unique 操作类型 for
            cn_fund's type_map). Values may repeat — de-duplicated here,
            first-encounter order preserved.
        merged: the merged (defaults + DB overrides) dict as returned by
            ``load_reader_mappings(connector, reader_key, kind)`` — already
            decoded to the kind's final value shape (bool for known_etf, str
            for symbol_norm/action_map/type_map).
        kind: one of 'known_etf', 'symbol_norm', 'action_map', 'type_map' —
            only used to shape the ``mapped_value`` field consistently.

    Returns:
        List of ``{"value": str, "mapped": bool, "mapped_value": dict | None}``
        for every unique value found, blank/NaN-ish values excluded. Only
        ``mapped=False`` entries are genuinely actionable gaps.
    """
    out: "list[Dict[str, Any]]" = []
    seen: "set[str]" = set()
    for raw in values:
        value = str(raw).strip()
        if not value or value.lower() in ("nan", "none"):
            continue
        if value in seen:
            continue
        seen.add(value)
        mapped = value in merged
        mapped_value: "Dict[str, Any] | None" = None
        if mapped:
            decoded = merged[value]
            if kind == "known_etf":
                mapped_value = {"etf": bool(decoded)}
            elif kind == "symbol_norm":
                mapped_value = {"to": decoded}
            else:  # action_map / type_map
                mapped_value = {"type": decoded}
        out.append({"value": value, "mapped": mapped, "mapped_value": mapped_value})
    return out
