"""Pydantic models for config-driven reader engine (B1/B2).

Each source has a YAML file under config/readers/<key>.yaml.
These models validate + expose the config; the engine in
config_driven_reader.py executes the declarative ops.

Snapshot-date strategies:
  file_mtime     — implemented; uses os.path.getmtime (matches legacy gold/insurance).
  read_timestamp — implemented; uses datetime.now() at read time (matches legacy RSU
                   which builds SourceData with datetime.now() and no snapshot_date
                   in metadata — transformer falls back to read_timestamp).
  header_regex   — implemented; reads raw header line(s) from the positions CSV
                   and extracts the date via regex (matches legacy SchwabReader
                   _parse_positions_date; falls back to read_timestamp when no match).
  column         — reserved; engine raises NotImplementedError.
  cell           — reserved; engine raises NotImplementedError.
  filename_regex — reserved; engine raises NotImplementedError.

Named-hook fields (B2, all optional — absent means no behaviour change):
  pre_read_hook                — side-effect pre-read hook (e.g. raw processor).
  holdings_hook                — after transactions DF is built, derive holdings by
                                 calling the named hook instead of the normal sheet
                                 path.  Hook must be registered in reader_hooks.py.
  holdings_from_sheet_hook     — like holdings_hook but receives data.holdings.
  transactions_from_sheet_hook — mirror of holdings_from_sheet_hook for transactions.
  post_transactions_hook       — reserved for a future sitting.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

class IdentityConfig(BaseModel):
    source_key: str
    source_system: str

    # -------------------------------------------------------------------------
    # DISPLAY LABEL CONFLICT NOTE (B1 registry extension)
    #
    # Two different display-name uses exist across the codebase and they diverge
    # for CN Fund:
    #   • display_label  — full UI label, e.g. "黄金 (Paper Gold)".
    #                      Used in settings.py _READER_LABEL_MAP (key→source_system
    #                      label shown in the settings page).
    #   • display_name   — short English name, e.g. "Gold".
    #                      Used in operations._SOURCE_DISPLAY_NAMES for the
    #                      freshness panel.
    #   • account_name   — default account string returned by _default_account(),
    #                      e.g. "CN Fund" (differs from display_name "CN Funds"
    #                      for cn_fund).  Optional; defaults to display_name.
    #
    # The CN Fund conflict ("CN Funds" vs "CN Fund") is the ONLY divergence
    # found in this survey.  All other sources have identical display_name and
    # account_name.  account_name is therefore Optional[str] = None (registry
    # falls back to display_name when absent).
    # -------------------------------------------------------------------------
    display_label: str
    display_name: str
    account_name: Optional[str] = None   # falls back to display_name if None

    asset_prefixes: List[str] = Field(default_factory=list)
    allowed_extensions: List[str] = Field(default_factory=list)
    category: Literal["reader", "historical"]
    validator: Optional[str] = None


# ---------------------------------------------------------------------------
# Snapshot date
# ---------------------------------------------------------------------------

class SnapshotDateConfig(BaseModel):
    strategy: Literal["file_mtime", "read_timestamp", "header_regex", "column", "cell", "filename_regex"]


# ---------------------------------------------------------------------------
# Sheet-level filter row
# ---------------------------------------------------------------------------

class FilterRowConfig(BaseModel):
    column: str
    op: Literal["ne", "eq", "gt"]
    value: Any


# ---------------------------------------------------------------------------
# Melt directive (insurance-style wide → long)
# ---------------------------------------------------------------------------

class MeltConfig(BaseModel):
    id_var: str
    var_name: str
    value_name: str
    rename_id_var_to: str
    # min_value: rows where value.fillna(0) > min_value are kept
    min_value: float = 0.0


# ---------------------------------------------------------------------------
# Value map for a single column
# ---------------------------------------------------------------------------

class ValueMapConfig(BaseModel):
    map: Dict[str, Any] = Field(default_factory=dict)
    fillna: Optional[str] = None
    # If set, result written to target_column instead of the source column.
    target_column: Optional[str] = None


# ---------------------------------------------------------------------------
# Sheet config
# ---------------------------------------------------------------------------

class SheetConfig(BaseModel):
    name: str
    target: Literal["holdings", "transactions"]
    rename: Dict[str, str] = Field(default_factory=dict)

    # passed to pd.read_excel(header=...); default 0 keeps gold/insurance/rsu unchanged
    header_row: int = 0

    # strips all object-typed columns (whitespace on cell values)
    strip_whitespace_columns: bool = False
    # strips whitespace from column *names* (e.g. "日期       " → "日期")
    strip_column_names: bool = False

    # CSV-specific fields (B2 Schwab — format: csv only)
    # glob pattern within data_dir to find matching CSV file(s)
    file_glob: Optional[str] = None
    # "latest" = sorted(matches)[-1]; "all" = concat all matches
    select: Literal["latest", "all"] = "all"
    # number of rows to skip before the header (passed to pd.read_csv skiprows=)
    skiprows: int = 0

    # applied BEFORE rename — filters rows where column op value is True
    filter_rows: List[FilterRowConfig] = Field(default_factory=list)

    melt: Optional[MeltConfig] = None

    # column → ValueMapConfig
    value_maps: Dict[str, ValueMapConfig] = Field(default_factory=dict)

    # Template for canonical_id / asset_id, e.g. "GOLD_{asset_name}_{account}"
    id_template: Optional[str] = None
    # placeholder → {raw_value → code}; used when building id_template
    id_field_maps: Dict[str, Dict[str, str]] = Field(default_factory=dict)

    # constant columns added after all transforms: column → value
    constants: Dict[str, Any] = Field(default_factory=dict)

    # copy_column: target → source (only-if-target-missing)
    copy_column: Optional[Dict[str, str]] = None

    # Ordered list of output columns.  Emitted as intersection:
    #   [c for c in output_columns if c in df.columns]
    output_columns: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Wizard config (import-adapter convergence — A1)
# ---------------------------------------------------------------------------

class WizardConfig(BaseModel):
    column_mapping: Dict[str, str] = {}   # {dst_field: src_column}
    fx_rate: Optional[float] = None
    import_type: str = "holdings"          # "holdings" | "transactions"


# ---------------------------------------------------------------------------
# Parsing config
# ---------------------------------------------------------------------------

class ParsingConfig(BaseModel):
    """Declarative parsing pipeline configuration.

    Hook fields (B2 — all Optional, default None = no behaviour change):
      pre_read_hook:          reserved for future use.
      holdings_hook:          when set, the named hook in reader_hooks.py is
                              called with (transactions_df, metadata) after the
                              transactions DataFrame is built, and its return
                              value replaces the normal sheet-based holdings
                              path.  Gold, insurance, and all current non-RSU
                              sources leave this None and are unaffected.
      post_transactions_hook: reserved for future use.
    """

    format: Literal["excel", "csv", "flex_csv"]
    snapshot_date: SnapshotDateConfig
    sheets: List[SheetConfig]

    # -----------------------------------------------------------------------
    # Named-hook fields (B2)
    # -----------------------------------------------------------------------
    # reserved — pre-read transformation before sheets are parsed
    pre_read_hook: Optional[str] = None
    # derive holdings from the transactions DF via a named hook instead of
    # the declarative sheet path
    holdings_hook: Optional[str] = None
    # like holdings_hook but the hook receives data.holdings (the raw sheet)
    # instead of the transactions DF; for sources whose holdings derive from
    # a balance sheet via a dict-melt, e.g. Financial Summary.
    # Mutually exclusive with holdings_hook in practice.
    holdings_from_sheet_hook: Optional[str] = None
    # mirror of holdings_from_sheet_hook for the transactions side; the hook
    # receives the raw transactions sheet DF and returns the final transactions
    # DataFrame.  Default None ⇒ no behaviour change.
    transactions_from_sheet_hook: Optional[str] = None
    # reserved — post-processing on the transactions DF
    post_transactions_hook: Optional[str] = None

    # Wizard config: generic tabular source described by column_mapping + fx_rate
    # (import-adapter convergence — A1).  None = disabled (all existing sources unaffected).
    wizard: Optional[WizardConfig] = None


# ---------------------------------------------------------------------------
# Top-level reader config
# ---------------------------------------------------------------------------

class ReaderConfig(BaseModel):
    identity: IdentityConfig
    parsing: Optional[ParsingConfig] = None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_reader_config(path: Path) -> ReaderConfig:
    """Load and validate a reader YAML config file."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return ReaderConfig.model_validate(raw)
