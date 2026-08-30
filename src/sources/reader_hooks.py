"""Backward-compatible re-export shim (Program OSR WS-2 mechanical split).

Until this split, this module directly held every named hook for the
config-driven reader engine (B2) — 1,578 lines covering RSU, Financial
Summary, CN Fund, Schwab, IBKR, and Wizard. Those implementations now live in
src/sources/hooks/{rsu,financial_summary,cn_fund,schwab,ibkr,gold_insurance,
wizard}.py, one file per reader family — see src/sources/hooks/__init__.py
for the aggregation that builds HOOKS / get_hook from them.

This module re-exports every one of those names unchanged so every existing
`from src.sources.reader_hooks import X` (and `import src.sources.reader_hooks
as rh; rh.X`) keeps working with zero behavior change — hook signatures,
constant values, and the get_hook() error message are all frozen. New code
should prefer importing directly from src.sources.hooks (or its submodules)
going forward; this module is kept for compatibility, not as the primary
surface.

IMPORT CONSTRAINT (mirrors registry.py, unchanged from before the split):
  This module — transitively, via src.sources.hooks — imports ONLY stdlib
  and pandas at module level. It NEVER imports from src.sources.*, src.sync.*,
  src.api.*, etc. Those layers may import this module, so any reverse import
  would create a cycle.

Registry API:
  HOOKS: Dict[str, Callable]      — name → hook function
  get_hook(name) -> Callable      — raises KeyError with clear message if missing

Hook signature contract:
  fn(transactions_df: pd.DataFrame, metadata: dict) -> pd.DataFrame
  The returned DataFrame represents derived holdings.
"""
from __future__ import annotations

from src.sources.hooks import (
    HOOKS,
    FS_ASSET_MAPPING,
    FS_TOMBSTONE_BLAST_RADIUS_WARN,
    _FS_ASSET_MAPPING,
    _FS_DATE_COLUMN,
    _fs_trailing_blank_tombstones,
    _CN_FUND_TYPE_MAP,
    _TRANSACTION_COL_MAP,
    _SCHWAB_ACTION_MAPPING,
    _SCHWAB_COLUMN_ALIASES,
    _SCHWAB_KNOWN_ETFS,
    _SCHWAB_SYMBOL_NORMALIZATIONS,
    _WIZARD_DATE_FIELDS,
    _WIZARD_NUMERIC_FIELDS,
    _schwab_map_action,
    _schwab_normalize_symbol,
    _schwab_normalize_to_canonical_id,
    _schwab_normalize_transaction_symbol,
    _schwab_parse_date,
    _schwab_parse_dollar,
    _schwab_parse_number,
    _schwab_parse_percent,
    cn_fund_holdings_from_sheet,
    cn_fund_raw_process,
    cn_fund_transactions_from_sheet,
    derive_rsu_holdings,
    get_hook,
    ibkr_holdings_from_flex,
    ibkr_transactions_from_flex,
    melt_financial_summary_holdings,
    normalize_fund_code,
    schwab_holdings_from_csv,
    schwab_transactions_from_csv,
    wizard_holdings_from_sheet,
    wizard_transactions_from_sheet,
)

__all__ = [
    "HOOKS",
    "get_hook",
    "derive_rsu_holdings",
    "FS_ASSET_MAPPING",
    "FS_TOMBSTONE_BLAST_RADIUS_WARN",
    "_FS_ASSET_MAPPING",
    "_FS_DATE_COLUMN",
    "_fs_trailing_blank_tombstones",
    "melt_financial_summary_holdings",
    "normalize_fund_code",
    "cn_fund_raw_process",
    "cn_fund_holdings_from_sheet",
    "cn_fund_transactions_from_sheet",
    "_CN_FUND_TYPE_MAP",
    "_TRANSACTION_COL_MAP",
    "schwab_holdings_from_csv",
    "schwab_transactions_from_csv",
    "_SCHWAB_ACTION_MAPPING",
    "_SCHWAB_COLUMN_ALIASES",
    "_SCHWAB_KNOWN_ETFS",
    "_SCHWAB_SYMBOL_NORMALIZATIONS",
    "_schwab_map_action",
    "_schwab_normalize_symbol",
    "_schwab_normalize_to_canonical_id",
    "_schwab_normalize_transaction_symbol",
    "_schwab_parse_date",
    "_schwab_parse_dollar",
    "_schwab_parse_number",
    "_schwab_parse_percent",
    "ibkr_holdings_from_flex",
    "ibkr_transactions_from_flex",
    "wizard_holdings_from_sheet",
    "wizard_transactions_from_sheet",
    "_WIZARD_DATE_FIELDS",
    "_WIZARD_NUMERIC_FIELDS",
]
