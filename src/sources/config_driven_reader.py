"""Config-driven reader engine (B1).

Executes declarative SheetConfig ops to produce DataFrames that are
byte-identical to the legacy gold/insurance pipelines.

Op order per sheet (must not change — equality gate relies on it):
  1. strip_column_names
  2. strip_whitespace_columns
  3. filter_rows  (applied BEFORE rename, exactly like legacy)
  4. rename
  5. melt
  6. value_maps
  7. id_template  (build canonical_id from row fields via id_field_maps)
  8. constants
  9. copy_column  (only-if-target-missing)

transform() then adds:
  - asset_id = canonical_id
  - source_system constant from identity config
  - snapshot_date from metadata when missing in df
  - output_columns ordering (intersection, preserving order)

The engine is strictly source-agnostic: anything source-specific (e.g.
insurance's cash_value → market_value copy) must be expressed in the
source's YAML via the declarative directives, never as engine code.
"""
from __future__ import annotations

import csv
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

import src.validation.source_format_validator as _sfv

from src.sources.base import (
    BaseSourceReader,
    SourceData,
    ValidationResult,
    READ_STATUS_KEY,
    READ_STATUS_OK,
    READ_STATUS_DISABLED,
    READ_STATUS_NO_DATA_DIR,
    READ_STATUS_SOURCE_MISSING,
    READ_STATUS_VALIDATION_FAILED,
)
from src.sources.reader_config import (
    MeltConfig,
    ReaderConfig,
    SheetConfig,
)

logger = logging.getLogger(__name__)

# String values that mean "missing" after .astype(str) / str() conversion.
# strip_whitespace_columns does .astype(str) on object columns, which turns
# real NaN cells into the literal string 'nan'.  We must treat those as missing
# too so the identity guard catches them on both the pre- and post-strip paths.
_MISSING_STR_VALUES: frozenset = frozenset({"", "nan", "None"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _empty_result(read_status: str) -> Dict[str, Any]:
    """Empty reader payload carrying WHY it is empty (task #16).

    The three "no rows" exits below (disabled / no data_dir / artifact missing)
    look identical to a genuinely empty workbook at the DataFrame level.  The
    status is the only thing that tells them apart downstream.
    """
    return {
        "holdings": pd.DataFrame(),
        "transactions": pd.DataFrame(),
        READ_STATUS_KEY: read_status,
    }


def _apply_filter_rows(df: pd.DataFrame, filter_rows) -> pd.DataFrame:
    """Apply filter_rows directives (before rename)."""
    for fr in filter_rows:
        col = fr.column
        if col not in df.columns:
            continue
        if fr.op == "ne":
            df = df[df[col] != fr.value].copy()
        elif fr.op == "eq":
            df = df[df[col] == fr.value].copy()
        elif fr.op == "gt":
            df = df[pd.to_numeric(df[col], errors="coerce") > fr.value].copy()
    return df


def _apply_melt(df: pd.DataFrame, melt_cfg: MeltConfig) -> pd.DataFrame:
    """Melt wide → long, then filter and rename id_var column."""
    id_var = melt_cfg.id_var
    policy_cols = [c for c in df.columns if c != id_var]
    melted = df.melt(
        id_vars=[id_var],
        value_vars=policy_cols,
        var_name=melt_cfg.var_name,
        value_name=melt_cfg.value_name,
    )
    # Filter: rows where value.fillna(0) > min_value
    melted = melted[melted[melt_cfg.value_name].fillna(0) > melt_cfg.min_value].copy()
    # Rename id_var column
    melted = melted.rename(columns={id_var: melt_cfg.rename_id_var_to})
    return melted


def _apply_value_maps(df: pd.DataFrame, value_maps) -> pd.DataFrame:
    """Apply value_map directives: map + optional fillna + optional target_column."""
    for src_col, vmc in value_maps.items():
        if src_col not in df.columns:
            continue
        mapped = df[src_col].map(vmc.map)
        if vmc.fillna is not None:
            mapped = mapped.fillna(vmc.fillna)
        dest_col = vmc.target_column if vmc.target_column else src_col
        df[dest_col] = mapped
    return df


def _build_id_from_template(
    row: pd.Series,
    template: str,
    id_field_maps: Dict[str, Dict[str, str]],
) -> Optional[str]:
    """Expand id_template placeholders using id_field_maps with raw-value fallback.

    E.g. template="GOLD_{asset_name}_{account}"
    Placeholder "asset_name" looks up id_field_maps["asset_name"].get(raw, raw).

    Returns None when any placeholder's raw value is NaN/empty (real NaN, the
    string 'nan' or 'None' produced by .astype(str), or the empty string).
    The caller must filter out rows where None is returned.
    """
    # Find all {placeholder} tokens
    placeholders = re.findall(r"\{(\w+)\}", template)
    result = template
    for ph in placeholders:
        raw = row.get(ph, "")
        # Check real NaN / pd.NA / np.nan before stringifying.
        try:
            if pd.isna(raw):
                return None
        except (TypeError, ValueError):
            pass
        # Check string representations produced by .astype(str) or str().
        raw_str = str(raw).strip()
        if raw_str in _MISSING_STR_VALUES:
            return None
        field_map = id_field_maps.get(ph, {})
        code = field_map.get(raw_str, raw_str)
        result = result.replace(f"{{{ph}}}", code)
    return result


def _apply_id_template(
    df: pd.DataFrame,
    sheet_cfg: SheetConfig,
    id_field_maps_override: "Dict[str, Dict[str, str]] | None" = None,
) -> pd.DataFrame:
    """Build canonical_id column from id_template.

    Any row where a placeholder expanded from a NaN/empty raw value (real NaN,
    string 'nan'/'None'/'') is dropped with a WARNING log.  A legitimately-
    empty identity cannot produce a meaningful canonical_id, so dropping is
    always correct.  Good rows are completely unaffected.

    Args:
        id_field_maps_override: ADR-023 WS-B — when not None, REPLACES
            ``sheet_cfg.id_field_maps`` entirely for this call (the merged
            defaults+DB-overrides dict from
            ``src.services.reader_mappings.load_id_field_maps``, built once
            per sync and shared across all of a reader's sheets). None (the
            default) preserves the exact legacy behavior — YAML-declared
            ``sheet_cfg.id_field_maps`` only.
    """
    if not sheet_cfg.id_template:
        return df
    df = df.copy()
    maps = id_field_maps_override if id_field_maps_override is not None else sheet_cfg.id_field_maps
    df["canonical_id"] = df.apply(
        lambda row: _build_id_from_template(
            row, sheet_cfg.id_template, maps  # type: ignore[arg-type]
        ),
        axis=1,
    )
    # Filter rows where identity couldn't be resolved (helper returned None → NaN).
    mask_bad = df["canonical_id"].isna()
    if mask_bad.any():
        for _, bad_row in df[mask_bad].iterrows():
            non_null = {
                k: v
                for k, v in bad_row.items()
                if k != "canonical_id"
                and pd.notna(v)
                and str(v).strip() not in _MISSING_STR_VALUES
            }
            logger.warning(
                "Skipping row with incomplete identity fields for template %r: %s",
                sheet_cfg.id_template,
                non_null,
            )
        df = df[~mask_bad].copy()
    return df


# ---------------------------------------------------------------------------
# Sheet processing
# ---------------------------------------------------------------------------

def _process_sheet(
    df: pd.DataFrame,
    sheet_cfg: SheetConfig,
    id_field_maps_override: "Dict[str, Dict[str, str]] | None" = None,
) -> pd.DataFrame:
    """Apply all declarative ops to a raw sheet DataFrame."""
    if df.empty:
        return df

    # 1. strip_column_names
    if sheet_cfg.strip_column_names:
        df.columns = [str(c).strip() for c in df.columns]

    # 2. strip_whitespace_columns (strip object column cell values)
    if sheet_cfg.strip_whitespace_columns:
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].astype(str).str.strip()

    # 3. filter_rows (before rename — exactly like legacy InsuranceReader)
    if sheet_cfg.filter_rows:
        df = _apply_filter_rows(df, sheet_cfg.filter_rows)

    # 4. rename
    if sheet_cfg.rename:
        df = df.rename(columns=sheet_cfg.rename)

    # 5. melt
    if sheet_cfg.melt:
        df = _apply_melt(df, sheet_cfg.melt)

    # 6. value_maps
    if sheet_cfg.value_maps:
        df = _apply_value_maps(df, sheet_cfg.value_maps)

    # 7. id_template → canonical_id
    df = _apply_id_template(df, sheet_cfg, id_field_maps_override)

    # 8. constants
    for col, val in sheet_cfg.constants.items():
        df[col] = val

    # 9. copy_column (only-if-target-missing)
    if sheet_cfg.copy_column:
        for target_col, src_col in sheet_cfg.copy_column.items():
            if target_col not in df.columns and src_col in df.columns:
                df[target_col] = df[src_col]

    return df


# ---------------------------------------------------------------------------
# Transform (post-read stage)
# ---------------------------------------------------------------------------

def _transform_sheet(
    df: pd.DataFrame,
    sheet_cfg: SheetConfig,
    source_system: str,
    snapshot_date_str: str,
) -> pd.DataFrame:
    """Add asset_id, source_system, snapshot_date, then select output_columns.

    Mirrors gold_transformer.py and insurance_transformer.py exactly:
    - asset_id = canonical_id (if present)
    - source_system constant
    - snapshot_date from metadata when not already in df
    - Insurance: cash_value → market_value copy-if-missing
    - output_columns: [c for c in cols if c in df.columns]
    """
    if df.empty:
        return df

    # asset_id from canonical_id
    if "canonical_id" in df.columns:
        df["asset_id"] = df["canonical_id"]

    df["source_system"] = source_system

    if "snapshot_date" not in df.columns:
        df["snapshot_date"] = snapshot_date_str

    # output_columns intersection (preserving order — matches legacy transformer)
    if sheet_cfg.output_columns:
        return df[[c for c in sheet_cfg.output_columns if c in df.columns]]

    return df


# ---------------------------------------------------------------------------
# Flex CSV parser
# ---------------------------------------------------------------------------

def _parse_flex_sections(path: Path) -> "dict[str, pd.DataFrame]":
    """Parse an IBKR Flex Query multi-section CSV into a dict of DataFrames.

    Each section is bounded by BOS/<CODE> … EOS/<CODE> rows.
    The row immediately after BOS is the column header; subsequent rows
    until EOS are data rows.  Empty sections (e.g. TRNT) → DataFrame with
    the header columns but zero rows.

    Outer markers (BOF, BOA, EOA, EOF) are ignored.
    Returns a dict keyed by section code, e.g. {"POST": df, "CRTT": df, ...}.
    """
    sections: "dict[str, pd.DataFrame]" = {}

    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        rows = list(reader)

    i = 0
    while i < len(rows):
        row = rows[i]
        if not row:
            i += 1
            continue
        marker = row[0]
        if marker == "BOS":
            code = row[1] if len(row) > 1 else ""
            # Next row is the header
            i += 1
            if i >= len(rows):
                break
            header = rows[i]
            # Collect data rows until EOS
            data_rows = []
            i += 1
            while i < len(rows):
                r = rows[i]
                if r and r[0] == "EOS":
                    i += 1  # consume EOS row
                    break
                data_rows.append(r)
                i += 1
            if data_rows:
                # Align row lengths to header length
                n_cols = len(header)
                aligned = []
                for dr in data_rows:
                    if len(dr) < n_cols:
                        dr = dr + [""] * (n_cols - len(dr))
                    elif len(dr) > n_cols:
                        dr = dr[:n_cols]
                    aligned.append(dr)
                df = pd.DataFrame(aligned, columns=header)
            else:
                df = pd.DataFrame(columns=header)
            sections[code] = df
        else:
            i += 1

    return sections


# ---------------------------------------------------------------------------
# ConfigDrivenReader
# ---------------------------------------------------------------------------

class ConfigDrivenReader(BaseSourceReader):
    """Generic reader that executes a ReaderConfig declarative pipeline."""

    def __init__(
        self,
        reader_cfg: ReaderConfig,
        id_field_maps_override: "Dict[str, Dict[str, str]] | None" = None,
    ) -> None:
        """
        Args:
            id_field_maps_override: ADR-023 WS-B — UI-managed id_field_map
                override (see src.services.reader_mappings.load_id_field_maps),
                nested {field: {label: code}}. When not None, REPLACES every
                sheet's YAML-declared id_field_maps for id_template resolution
                during read(). id_template resolution happens inside read()
                (building canonical_id), BEFORE sync_config_source's
                extra_metadata merge into SourceData.metadata (which only runs
                between read() and transform()) — so the override must be
                supplied at construction time, not via metadata, to actually
                reach id-resolution. None (the default) preserves the exact
                legacy behavior for every source that doesn't pass one.
        """
        self._cfg = reader_cfg
        self._id_field_maps_override = id_field_maps_override

    # ------------------------------------------------------------------
    # BaseSourceReader interface
    # ------------------------------------------------------------------

    def read(self, file_path: Path) -> SourceData:
        """Read the source file and apply all sheet ops.

        For format='excel': file_path is the workbook path (existing behaviour).
        For format='csv': file_path is the DATA DIRECTORY; each sheet_cfg defines
          a file_glob + select strategy to locate individual CSV files.

        Matches legacy semantics:
        - Missing file/dir → SourceData with empty DataFrames.
        - Sheet read exception → empty DataFrame + WARNING log.
        - file mtime → metadata["snapshot_date"] = "%Y-%m-%d".
        - header_regex → reads raw header of positions CSV before skiprows.
        """
        source_key = self._cfg.identity.source_key
        parsing = self._cfg.parsing

        # For CSV/flex_csv format, file_path is the data directory (not a single file).
        # For Excel format, we still check the file exists.
        is_csv = parsing is not None and parsing.format in ("csv", "flex_csv")
        is_flex = parsing is not None and parsing.format == "flex_csv"

        if not is_csv and not file_path.exists():
            return SourceData(
                source_key,
                datetime.now(),
                pd.DataFrame(),
                pd.DataFrame(),
                {},
            )
        if is_csv and not file_path.is_dir():
            return SourceData(
                source_key,
                datetime.now(),
                pd.DataFrame(),
                pd.DataFrame(),
                {},
            )

        # Resolve snapshot_date from the configured strategy.
        strategy = parsing.snapshot_date.strategy if parsing else "file_mtime"

        # For header_regex we need the positions file path first; resolve below.
        snapshot_date_str: str = ""

        if strategy == "file_mtime":
            snapshot_date_str = datetime.fromtimestamp(
                os.path.getmtime(file_path)
            ).strftime("%Y-%m-%d")
        elif strategy == "read_timestamp":
            # Mirrors legacy RSUReader: SourceData.read_timestamp = datetime.now();
            # rsu_transformer falls back to read_timestamp when metadata has no
            # "snapshot_date" key.  So snapshot_date = today (the sync date).
            snapshot_date_str = datetime.now().strftime("%Y-%m-%d")
        elif strategy == "header_regex":
            # Will be resolved after we identify the positions file below.
            snapshot_date_str = ""
        else:
            raise NotImplementedError(
                f"Snapshot strategy '{strategy}' is not implemented. "
                "Supported: 'file_mtime', 'read_timestamp', 'header_regex'."
            )

        holdings_df = pd.DataFrame()
        transactions_df = pd.DataFrame()
        _flex_metadata_extra: "dict | None" = None

        if parsing:
            # pre_read_hook — side-effect only (e.g. CN Fund raw processor).
            # Runs BEFORE the sheet-read loop so organized sheets are up to date.
            # NEVER invoke in tests: disable by setting cfg.parsing.pre_read_hook = None.
            if parsing.pre_read_hook:
                from src.sources.reader_hooks import get_hook  # lazy import — avoids cycle
                get_hook(parsing.pre_read_hook)(file_path, {})

            if is_flex:
                # Flex CSV mode: file_path is data_dir; all sections are in ONE file.
                # Use the first sheet's file_glob to locate the flex file.
                flex_glob = None
                for sheet_cfg in parsing.sheets:
                    if sheet_cfg.file_glob:
                        flex_glob = sheet_cfg.file_glob
                        break

                if not flex_glob:
                    logger.warning(
                        "ConfigDrivenReader[%s]: flex_csv format but no file_glob in any sheet — leaving empty",
                        source_key,
                    )
                else:
                    matches = sorted(file_path.glob(flex_glob))
                    if not matches:
                        logger.warning(
                            "ConfigDrivenReader[%s]: no files match glob '%s' in %s — leaving empty",
                            source_key,
                            flex_glob,
                            file_path,
                        )
                    else:
                        chosen = matches[-1]
                        sections = _parse_flex_sections(chosen)

                        # Snapshot date from POST ReportDate column (take max).
                        post_df = sections.get("POST", pd.DataFrame())
                        if not post_df.empty and "ReportDate" in post_df.columns:
                            try:
                                snapshot_date_str = (
                                    pd.to_datetime(post_df["ReportDate"]).max().strftime("%Y-%m-%d")
                                )
                            except Exception:  # noqa: BLE001
                                snapshot_date_str = datetime.now().strftime("%Y-%m-%d")
                        else:
                            snapshot_date_str = datetime.now().strftime("%Y-%m-%d")

                        # Holdings = POST section; transactions = TRNT section.
                        holdings_df = sections.get("POST", pd.DataFrame())
                        transactions_df = sections.get("TRNT", pd.DataFrame())

                        # Resolve account_id from POST ClientAccountID or ACCT section.
                        account_id = ""
                        if not post_df.empty and "ClientAccountID" in post_df.columns:
                            account_id = str(post_df["ClientAccountID"].iloc[0])
                        else:
                            acct_df = sections.get("ACCT", pd.DataFrame())
                            if not acct_df.empty and "ClientAccountID" in acct_df.columns:
                                account_id = str(acct_df["ClientAccountID"].iloc[0])

                        # Stash all sections in metadata for hooks to use.
                        # Merged into the final metadata dict below.
                        _flex_metadata_extra = {
                            "flex_sections": sections,
                            "account_id": account_id,
                        }

            elif is_csv:
                # CSV mode: file_path is data_dir; each sheet_cfg has file_glob + select.
                # The holdings sheet (select='latest') is used for header_regex resolution.
                holdings_file: "Path | None" = None

                for sheet_cfg in parsing.sheets:
                    if not sheet_cfg.file_glob:
                        logger.warning(
                            "ConfigDrivenReader[%s]: CSV sheet '%s' has no file_glob — skipping",
                            source_key,
                            sheet_cfg.name,
                        )
                        continue

                    matches = sorted(file_path.glob(sheet_cfg.file_glob))
                    if not matches:
                        logger.warning(
                            "ConfigDrivenReader[%s]: no files match glob '%s' in %s",
                            source_key,
                            sheet_cfg.file_glob,
                            file_path,
                        )
                        raw_df = pd.DataFrame()
                    elif sheet_cfg.select == "latest":
                        chosen = matches[-1]
                        if sheet_cfg.target == "holdings":
                            holdings_file = chosen
                        try:
                            raw_df = pd.read_csv(chosen, skiprows=sheet_cfg.skiprows)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "ConfigDrivenReader[%s]: failed to read CSV '%s': %s",
                                source_key, chosen, exc,
                            )
                            raw_df = pd.DataFrame()
                    else:  # "all"
                        dfs = []
                        for f in matches:
                            try:
                                dfs.append(pd.read_csv(f, skiprows=sheet_cfg.skiprows))
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "ConfigDrivenReader[%s]: failed to read CSV '%s': %s",
                                    source_key, f, exc,
                                )
                        raw_df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

                    processed = _process_sheet(raw_df, sheet_cfg, self._id_field_maps_override)

                    if sheet_cfg.target == "holdings":
                        holdings_df = processed
                    elif sheet_cfg.target == "transactions":
                        transactions_df = processed

                # Resolve header_regex snapshot_date from the positions (holdings) file.
                if strategy == "header_regex":
                    import re as _re
                    resolved = False
                    if holdings_file and holdings_file.exists():
                        try:
                            first_line = holdings_file.read_text(encoding="utf-8", errors="replace").splitlines()[0]
                            # Try MM/DD/YYYY
                            m = _re.search(
                                r'as of\s+\d+:\d+\s+[AP]M\s+ET,\s+(\d{2}/\d{2}/\d{4})',
                                first_line,
                            )
                            if m:
                                snapshot_date_str = datetime.strptime(m.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
                                resolved = True
                            else:
                                # Try YYYY/MM/DD
                                m = _re.search(
                                    r'as of\s+\d+:\d+\s+[AP]M\s+ET,\s+(\d{4}/\d{2}/\d{2})',
                                    first_line,
                                )
                                if m:
                                    snapshot_date_str = datetime.strptime(m.group(1), "%Y/%m/%d").strftime("%Y-%m-%d")
                                    resolved = True
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "ConfigDrivenReader[%s]: header_regex date parse failed: %s",
                                source_key, exc,
                            )
                    if not resolved:
                        logger.warning(
                            "ConfigDrivenReader[%s]: header_regex: could not parse date from "
                            "positions header; falling back to read_timestamp",
                            source_key,
                        )
                        snapshot_date_str = datetime.now().strftime("%Y-%m-%d")

            else:
                # Excel mode (existing behaviour — unchanged).
                for sheet_cfg in parsing.sheets:
                    try:
                        raw_df = pd.read_excel(
                            file_path, sheet_name=sheet_cfg.name, engine="openpyxl",
                            header=sheet_cfg.header_row,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "ConfigDrivenReader[%s]: failed to read sheet '%s' from %s: %s",
                            source_key,
                            sheet_cfg.name,
                            file_path,
                            exc,
                        )
                        raw_df = pd.DataFrame()

                    processed = _process_sheet(raw_df, sheet_cfg, self._id_field_maps_override)

                    if sheet_cfg.target == "holdings":
                        holdings_df = processed
                    elif sheet_cfg.target == "transactions":
                        transactions_df = processed

        metadata: dict = {
            "holdings_count": len(holdings_df),
            "transactions_count": len(transactions_df),
        }
        # Merge flex-specific metadata (sections, account_id) when in flex_csv mode.
        if _flex_metadata_extra is not None:
            metadata.update(_flex_metadata_extra)
        if snapshot_date_str:
            metadata["snapshot_date"] = snapshot_date_str
        # Wizard config: stash fields so hooks can read them (A1 import-adapter convergence).
        if parsing and parsing.wizard is not None:
            wizard = parsing.wizard
            metadata["wizard_column_mapping"] = wizard.column_mapping
            metadata["wizard_fx_rate"] = wizard.fx_rate
            metadata["wizard_import_type"] = wizard.import_type
        if strategy == "header_regex" and snapshot_date_str:
            # Also expose as positions_date (datetime.date) for transform_holdings compat.
            try:
                import datetime as _dtmod
                metadata["positions_date"] = _dtmod.datetime.strptime(
                    snapshot_date_str, "%Y-%m-%d"
                ).date()
            except Exception:  # noqa: BLE001
                pass

        return SourceData(
            source_key,
            datetime.now(),
            holdings_df,
            transactions_df,
            metadata,
        )

    def validate(self, data: SourceData) -> ValidationResult:
        """Reproduce legacy warning patterns."""
        warnings_list: list[str] = []
        stats = {
            "holdings_count": len(data.holdings),
            "transactions_count": len(data.transactions),
        }

        if data.holdings.empty:
            warnings_list.append("No holdings data found")

        if data.transactions.empty:
            warnings_list.append("No transactions data found")

        # Warn on unknown-type rows — mirrors GoldReader.validate()
        for sheet_cfg in (self._cfg.parsing.sheets if self._cfg.parsing else []):
            for src_col, vmc in sheet_cfg.value_maps.items():
                dest_col = vmc.target_column if vmc.target_column else src_col
                if vmc.fillna is not None:
                    # Check in the appropriate df
                    df = (
                        data.holdings
                        if sheet_cfg.target == "holdings"
                        else data.transactions
                    )
                    if dest_col in df.columns:
                        unknown = df[df[dest_col] == vmc.fillna]
                        if len(unknown) > 0:
                            warnings_list.append(
                                f"Found {len(unknown)} transactions with unknown type"
                            )

        return ValidationResult(is_valid=True, warnings=warnings_list, stats=stats)

    # ------------------------------------------------------------------
    # Transform (called externally, like gold_transformer / insurance_transformer)
    # ------------------------------------------------------------------

    def transform(
        self, data: SourceData
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Apply transformer stage: add asset_id, source_system, snapshot_date,
        then filter to output_columns.

        When parsing.holdings_hook is set (e.g. RSU), holdings are derived by
        calling the named hook with (transactions_df, metadata) INSTEAD of the
        normal sheet-based holdings path.  Gold, insurance, and all sources
        that leave holdings_hook=None are completely unaffected.

        Returns (holdings_df, transactions_df).
        """
        source_system = self._cfg.identity.source_system
        snapshot_date_str = data.metadata.get(
            "snapshot_date", data.read_timestamp.strftime("%Y-%m-%d")
        )
        parsing = self._cfg.parsing

        if not parsing:
            return pd.DataFrame(), pd.DataFrame()

        holdings_df = pd.DataFrame()
        transactions_df = pd.DataFrame()

        for sheet_cfg in parsing.sheets:
            if sheet_cfg.target == "holdings":
                src_df = data.holdings
            else:
                src_df = data.transactions

            if src_df.empty:
                continue

            transformed = _transform_sheet(
                src_df.copy(),
                sheet_cfg,
                source_system,
                snapshot_date_str,
            )
            if sheet_cfg.target == "holdings":
                holdings_df = transformed
            else:
                transactions_df = transformed

        # ------------------------------------------------------------------
        # Named holdings hook (B2) — overrides the declarative sheet path.
        # Only active when parsing.holdings_hook is set (e.g. "derive_rsu_holdings").
        # Gold, insurance, and all other current sources have holdings_hook=None
        # and follow the normal path above without any code-path change.
        # ------------------------------------------------------------------
        if parsing.holdings_hook:
            from src.sources.reader_hooks import get_hook  # lazy import — avoids cycle for sources that don't use it
            hook_fn = get_hook(parsing.holdings_hook)
            holdings_df = hook_fn(transactions_df, data.metadata)

        # ------------------------------------------------------------------
        # Balance-sheet holdings hook (B2) — mutually exclusive with holdings_hook.
        # Receives data.holdings (the raw sheet DF, NOT the transactions DF) so
        # the hook can perform its own dict-melt logic (e.g. Financial Summary).
        # The hook emits final columns + per-row snapshot_date itself — the engine
        # does NOT apply snapshot-stamping or output_columns on the result.
        # ------------------------------------------------------------------
        if parsing.holdings_from_sheet_hook:
            from src.sources.reader_hooks import get_hook  # lazy import — avoids cycle
            hook_fn = get_hook(parsing.holdings_from_sheet_hook)
            holdings_df = hook_fn(data.holdings, data.metadata)

        # ------------------------------------------------------------------
        # Transactions-from-sheet hook (B2 sitting #3) — mirrors
        # holdings_from_sheet_hook but for the transactions side.
        # Receives data.transactions (the raw sheet DF) and returns the final
        # transactions DataFrame.  Only active when set (e.g. CN Fund).
        # Gold, insurance, RSU, FS and all sources that leave this None are
        # completely unaffected.
        # ------------------------------------------------------------------
        if parsing.transactions_from_sheet_hook:
            from src.sources.reader_hooks import get_hook  # lazy import — avoids cycle
            hook_fn = get_hook(parsing.transactions_from_sheet_hook)
            transactions_df = hook_fn(data.transactions, data.metadata)

        return holdings_df, transactions_df


# ---------------------------------------------------------------------------
# sync_config_source — mirrors sync_gold() contract
# ---------------------------------------------------------------------------

def sync_config_source(
    config: Dict[str, Any],
    reader_cfg: ReaderConfig,
    extra_metadata: "Dict[str, Any] | None" = None,
) -> Dict[str, pd.DataFrame]:
    """Generic sync entry-point mirroring src/sync/gold_sync.py.

    Steps:
      1. enabled check via source_registry.<source_key>
      2. data_dir with finance_dir fallback
      3. file_patterns workbook lookup
      4. missing-file warning → empty dict
      5. format validation via identity.validator (if set) — warn-only, never blocking;
         mirrors legacy gold_sync.py / insurance_sync.py semantics exactly.
      6. reader.read
      7. reader.transform
      8. info log

    Args:
        extra_metadata: Optional dict merged into the reader's metadata
            (SourceData.metadata) AFTER read() and BEFORE transform().
            Used by the orchestrator to inject sync-connection-scoped data
            the reader engine has no direct access to (e.g. UI-managed
            reader mappings loaded from the DB — ADR-023 / WS-A). Hooks
            read it via ``metadata.get(...)`` with a hardcoded fallback, so
            callers that don't pass this are completely unaffected.

            Special key ``"id_field_maps_override"`` (ADR-023 WS-B, nested
            {field: {label: code}}) is consumed BEFORE read() — passed to
            ``ConfigDrivenReader`` at construction — because id_template
            resolution happens inside read(), earlier than this dict's
            normal read()-then-transform() merge point (see
            ``ConfigDrivenReader.__init__``'s docstring). It is still merged
            into ``source_data.metadata`` afterwards like every other key,
            so it's introspectable by tests/hooks, but that merge is not
            what makes it take effect.
    """
    source_key = reader_cfg.identity.source_key
    type_config = config.get("source_registry", {}).get(source_key, {})

    if not type_config.get("enabled", False):
        logger.info("%s sync disabled", source_key)
        return _empty_result(READ_STATUS_DISABLED)

    data_dir = type_config.get("data_dir")

    # Fallback to finance_dir (iCloud Finance directory)
    if not data_dir:
        data_dir = config.get("finance_dir") or config.get("sources", {}).get(
            "pis", {}
        ).get("finance_dir")
        if not data_dir:
            logger.warning(
                "%s data_dir not configured and no finance_dir available", source_key
            )
            return _empty_result(READ_STATUS_NO_DATA_DIR)

    data_path = Path(str(data_dir))

    # Task #16: the artifact must be positively verified before an empty result
    # may be read as "the owner holds nothing here".  Any doubt keeps the status
    # at something other than READ_STATUS_OK.
    read_status = READ_STATUS_OK

    # CSV/flex_csv format: pass the data directory directly to reader.read().
    # Excel format: locate the single workbook file.
    parsing = reader_cfg.parsing
    is_csv = parsing is not None and parsing.format in ("csv", "flex_csv")

    if is_csv:
        # For CSV format, the reader receives the data directory.
        read_path = data_path
        if not read_path.is_dir():
            logger.warning("%s data_dir not found: %s", source_key, read_path)
            return _empty_result(READ_STATUS_SOURCE_MISSING)
        # Format validation for CSV: validate first matching positions file.
        validator_name = reader_cfg.identity.validator
        if validator_name:
            positions_sheet = next(
                (s for s in (parsing.sheets if parsing else []) if s.target == "holdings"),
                None,
            )
            if positions_sheet and positions_sheet.file_glob:
                matches = sorted(data_path.glob(positions_sheet.file_glob))
                validate_path = matches[-1] if matches else None
            else:
                validate_path = None
            if validate_path:
                validator_fn = getattr(_sfv, validator_name, None)
                if callable(validator_fn):
                    validation = validator_fn(validate_path)
                    if not validation.is_valid:
                        logger.warning(
                            "%s format validation failed: %s", source_key, validation.warnings
                        )
                        read_status = READ_STATUS_VALIDATION_FAILED
                else:
                    logger.warning(
                        "%s: no format validator '%s' found — skipping validation",
                        source_key,
                        validator_name,
                    )
                    read_status = READ_STATUS_VALIDATION_FAILED
            else:
                # A declared validator with nothing to validate against means the
                # positions file itself is absent — unverified, not "empty".
                read_status = READ_STATUS_SOURCE_MISSING
    else:
        workbook_name = type_config.get("file_patterns", {}).get(
            "workbook",
            f"{source_key}.xlsx",
        )
        read_path = data_path / workbook_name

        if not read_path.exists():
            logger.warning("%s workbook not found: %s", source_key, read_path)
            return _empty_result(READ_STATUS_SOURCE_MISSING)

        # Format validation — mirrors legacy sync semantics: warn, never block.
        # The validator name is declared in the identity config (e.g. "validate_gold_format").
        # If absent or unresolvable, log a warning and continue (Rule 12: never silent skip).
        #
        # Task #16: a failed (or unresolvable) validator still does not block the
        # ingest — but it DOES withdraw the source's licence to zero itself, since
        # a malformed workbook is exactly the shape a half-uploaded file takes.
        validator_name = reader_cfg.identity.validator
        if validator_name:
            validator_fn = getattr(_sfv, validator_name, None)
            if callable(validator_fn):
                validation = validator_fn(read_path)
                if not validation.is_valid:
                    logger.warning(
                        "%s format validation failed: %s", source_key, validation.warnings
                    )
                    read_status = READ_STATUS_VALIDATION_FAILED
            else:
                logger.warning(
                    "%s: no format validator '%s' found — skipping validation",
                    source_key,
                    validator_name,
                )
                read_status = READ_STATUS_VALIDATION_FAILED

    id_field_maps_override = extra_metadata.get("id_field_maps_override") if extra_metadata else None
    reader = ConfigDrivenReader(reader_cfg, id_field_maps_override=id_field_maps_override)
    source_data = reader.read(read_path)
    if extra_metadata:
        source_data.metadata.update(extra_metadata)
    holdings_df, transactions_df = reader.transform(source_data)

    logger.info(
        "%s config-driven sync: %d holdings, %d transactions (read_status=%s)",
        source_key,
        len(holdings_df),
        len(transactions_df),
        read_status,
    )

    return {
        "holdings": holdings_df,
        "transactions": transactions_df,
        READ_STATUS_KEY: read_status,
    }
