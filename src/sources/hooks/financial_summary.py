"""Financial Summary balance-sheet melt hook (Program OSR WS-2 mechanical split).

Extracted verbatim from src/sources/reader_hooks.py (pre-split, 1,578 lines) —
see src/sources/hooks/__init__.py for the aggregation and
src/sources/reader_hooks.py for the backward-compatible re-export shim.

IMPORT CONSTRAINT (mirrors src.sources.registry — unchanged from the
pre-split module): stdlib + pandas only at module level. Lazy imports inside
a function body are allowed.

  Exception (verified acyclic, ADR-023): ``src.database.mapping_seeds`` is a
  standalone stdlib-only data module with zero further imports and is never
  imported by src.database.connector's callers back into src.sources — so
  importing it here at module level does not create a cycle. It is the single
  source of truth for the FS asset-mapping default, shared with the
  reader_mappings DB seed migration.

NOTE — .iloc[3:] (the "fs-triple-trim" balance-sheet header trim below) is
carried over UNCHANGED. Program OSR treats it as out of scope for this split
(docs/known-issues.md §fs-triple-trim tracks the pending fix on its own
branch) — this file only relocates the existing logic, byte-for-byte.
"""
from __future__ import annotations

import logging
from typing import Set, Tuple

import pandas as pd

from src.database.mapping_seeds import FS_ASSET_MAPPING_SEED

# Logger name pinned to the pre-split module path ("src.sources.reader_hooks"),
# not __name__, so log output and any log-name-filtered test/monitoring stay
# byte-identical across the WS-2 split (see also hooks/cn_fund.py).
logger = logging.getLogger("src.sources.reader_hooks")

# ---------------------------------------------------------------------------
# ASSET_MAPPING for Financial Summary balance-sheet melt
# Maps balance-sheet column name → (asset_id, asset_name, currency)
# Public alias FS_ASSET_MAPPING is the single source of truth for the CODE
# DEFAULT (fresh-DB bootstrap + reader_mappings seed source). The actual dict
# now lives in src.database.mapping_seeds (ADR-023 / WS-A) so the
# reader_mappings DB-seed migration can share it without duplication.
# ---------------------------------------------------------------------------

_FS_ASSET_MAPPING = FS_ASSET_MAPPING_SEED

# Public alias — the single source of truth for FS balance-sheet asset mapping
# (replaces deleted src.sources.financial_summary_transformer.ASSET_MAPPING).
FS_ASSET_MAPPING = _FS_ASSET_MAPPING

# Blast-radius alarm for the trailing-blank tombstone rule below.  The owner's
# 78-month history has never had more than ONE mapped column go blank-then-return
# in the same month (measured 2026-08-02), so a melt that would zero out more
# than this many assets at once is far more likely to be a half-entered newest
# row than a genuine simultaneous closure of that many accounts.  We still emit
# the tombstones (a silent no-op is the very failure class this fix exists to
# kill) but we say so loudly in the sync log.
FS_TOMBSTONE_BLAST_RADIUS_WARN = 3

# Column used by the FS balance sheet as the per-row snapshot date.
_FS_DATE_COLUMN = "日期"


def _fs_trailing_blank_tombstones(
    df: pd.DataFrame,
    fs_asset_mappings: dict,
) -> "Tuple[Set[Tuple[object, str]], Set[str]]":
    """Find blank FS cells that mean "this balance is now zero", not "no data".

    Background (P1, 2026-08-01): the melt used to drop every null/zero cell to
    keep the holdings table lean.  A mapped column that goes blank therefore
    emitted no row at all, so the asset's last non-zero row stayed the latest
    snapshot and kept counting in net worth forever — the ``uis-failure-classes``
    "invisible states" shape (absence indistinguishable from "no update").
    ``_shadow_stale_reader_holdings`` cannot catch it: it keys on a row's age
    relative to the source's own latest snapshot, and the phantom row IS at the
    source's latest snapshot date.

    Scope — deliberately narrow.  Only the **trailing** run of blank cells that
    follows a column's last non-zero value is treated as an affirmative zero:

      * Interior blanks are left alone.  They are common (13 of them in
        ``美元存款_中行`` alone) and they are followed by a real value, so the
        column self-corrects; tombstoning them would rewrite ~1000 rows of
        history for no current-state benefit.
      * A column that never carried a non-zero value produces no tombstones,
        so newly-mapped columns with years of leading blanks stay cheap.
      * Every row of the trailing run is tombstoned, not just the last one.
        The last one is what repairs current net worth (it must become the
        per-asset MAX snapshot so ``_shadow_stale_historical_holdings`` shadows
        the phantom); the earlier ones repair point-in-time history, which is
        equally wrong today — e.g. ``CASH_Deposit_BOC_USD`` carries a stale
        2026-06 row as well as the 2026-07 one.

    Returns:
        (tombstone_cells, missing_columns) where ``tombstone_cells`` is a set of
        ``(row_index_label, column_name)`` pairs whose blank cell must emit a
        zero-value row, and ``missing_columns`` is the set of mapped columns
        absent from the sheet entirely (reported, never tombstoned — see the
        caller).
    """
    tombstone_cells: Set[Tuple[object, str]] = set()
    missing_columns: Set[str] = {
        col for col in fs_asset_mappings if col not in df.columns
    }

    if _FS_DATE_COLUMN not in df.columns:
        return tombstone_cells, missing_columns

    # "Trailing" is by DATE, not by sheet row order — a resorted or appended
    # workbook must not change which cells count as the tail.
    dated = df[df[_FS_DATE_COLUMN].notna()]
    if dated.empty:
        return tombstone_cells, missing_columns
    dated = dated.sort_values(_FS_DATE_COLUMN, kind="stable")
    labels = list(dated.index)

    for col in fs_asset_mappings:
        if col not in dated.columns:
            continue
        values = dated[col].tolist()

        last_nonzero = -1
        for i, value in enumerate(values):
            if pd.notna(value) and value != 0:
                last_nonzero = i
        if last_nonzero < 0:
            # Never held → nothing to zero out. This is what keeps a mapped
            # column that is blank for its whole history from emitting 78 rows.
            continue

        for i in range(last_nonzero + 1, len(values)):
            # Explicit zeros are already emitted as real rows by the melt (an
            # explicit 0 is the owner saying "this is zero"); only genuine
            # blanks need synthesizing.
            if pd.isna(values[i]):
                tombstone_cells.add((labels[i], col))

    return tombstone_cells, missing_columns


def melt_financial_summary_holdings(
    sheet_df: pd.DataFrame,
    metadata: dict,
) -> pd.DataFrame:
    """Derive holdings from the Financial Summary 资产负债 balance sheet.

    Reproduces the legacy chain:
      FinancialSummaryReader.read() trim → melt_balance_sheet_to_holdings()

    The hook receives the raw sheet DF as read by the config engine with
    header=3 (i.e. pd.read_excel(header=3)).  It re-applies the same
    reader trim the legacy FinancialSummaryReader does before running the
    dict-melt that produces one holdings row per (date, asset) pair.

    Hook signature: (sheet_df, metadata) → pd.DataFrame.
    stdlib + pandas only (no src.* imports — cycle guard).

    Args:
        sheet_df: Raw balance-sheet DataFrame as produced by
            pd.read_excel(header=3), before any trim.
        metadata: Engine metadata dict (not used for snapshot_date —
            each row carries its own '日期' date). May carry
            ``fs_asset_mappings`` — a merged (code defaults + DB overrides)
            column→(asset_id, asset_name, currency) dict loaded by
            ``src.services.reader_mappings.load_reader_mappings`` at the
            orchestrator level (ADR-023 / WS-A). Falls back to the hardcoded
            ``_FS_ASSET_MAPPING`` default when absent (e.g. legacy callers,
            direct ConfigDrivenReader tests that don't inject it).

    Zero-value rows (P1 fix, 2026-08-01) — the melt emits an explicit
    ``market_value = 0`` / ``quantity = 0`` row in two cases, because "this
    balance is now zero" must be representable or the asset's last non-zero row
    stays its latest snapshot forever:
      1. the workbook cell literally contains 0 (an affirmative owner entry), and
      2. the cell is blank and sits in the trailing run of blanks following the
         column's last non-zero value — see ``_fs_trailing_blank_tombstones``.
    Interior blanks and never-filled columns still emit nothing.

    Returns:
        One holdings row per (date, mapped-and-populated asset) pair, with
        columns: snapshot_date, asset_id, asset_name, asset_type, quantity,
            unit, cost_price_unit, market_price_unit, market_value,
            currency, account, source_system.
        snapshot_date values are pandas Timestamps (datetime64[ns]).
    """
    if sheet_df.empty:
        return pd.DataFrame()

    # -----------------------------------------------------------------------
    # Re-apply the legacy FinancialSummaryReader.read() trim.
    # With header=3, rows 0–2 are the grouping-label rows above the actual
    # data.  Drop all-NaN rows, then skip the first 3 rows (group labels).
    # -----------------------------------------------------------------------
    df = sheet_df.dropna(how="all")
    if len(df) > 3:
        df = df.iloc[3:].reset_index(drop=True)

    # -----------------------------------------------------------------------
    # Dict-melt — verbatim port of melt_balance_sheet_to_holdings().
    # fs_asset_mappings: merged (code defaults + DB overrides) dict injected
    # by the orchestrator via metadata; falls back to the hardcoded default.
    # -----------------------------------------------------------------------
    _injected_mappings = metadata.get("fs_asset_mappings")
    fs_asset_mappings = _injected_mappings if _injected_mappings is not None else _FS_ASSET_MAPPING

    tombstone_cells, missing_columns = _fs_trailing_blank_tombstones(df, fs_asset_mappings)

    rows = []
    tombstoned_assets: Set[str] = set()
    for idx, row in df.iterrows():
        snapshot_date = row.get(_FS_DATE_COLUMN)
        if pd.isna(snapshot_date):
            continue

        for col, (asset_id, asset_name, currency) in fs_asset_mappings.items():
            if col in row:
                market_value = row[col]
                if pd.isna(market_value):
                    # A blank cell is "not recorded" — no row — UNLESS it sits in
                    # the trailing run after the column's last non-zero value, in
                    # which case it is an affirmative "this balance is now zero"
                    # and must emit a tombstone so the previous row stops being
                    # the asset's latest snapshot. See _fs_trailing_blank_tombstones.
                    if (idx, col) not in tombstone_cells:
                        continue
                    market_value = 0.0
                    tombstoned_assets.add(asset_id)

                # An explicit 0 in the workbook is DATA, not noise: it is the
                # owner's manual, immediate way to say "this account is empty".
                # It used to be dropped by the same lean-table filter as NaN,
                # which is why entering 0 did not fix the phantom either.
                is_zero = market_value == 0

                rows.append(
                    {
                        "snapshot_date": snapshot_date,
                        "asset_id": asset_id,
                        "asset_name": asset_name,
                        "asset_type": (
                            "cash"
                            if "CASH_" in asset_id
                            else "property" if "Property_" in asset_id else "investment"
                        ),
                        # qty 0 marks "no position" — the same shape the broker
                        # zero-qty tombstone uses (_shadow_coauthority_tombstone).
                        "quantity": 0.0 if is_zero else 1.0,
                        "unit": "unit",
                        "cost_price_unit": None,
                        "market_price_unit": market_value,
                        "market_value": market_value,
                        "currency": currency,
                        "account": "Financial_Summary",
                        "source_system": "Financial_Summary_Excel",
                    }
                )

    if tombstoned_assets:
        message = (
            "melt_financial_summary_holdings: emitted zero-value tombstones for "
            "%d asset(s) whose mapped column is blank after its last non-zero "
            "value: %s"
        )
        args = (len(tombstoned_assets), sorted(tombstoned_assets))
        if len(tombstoned_assets) > FS_TOMBSTONE_BLAST_RADIUS_WARN:
            logger.warning(
                message + " — that is more than %d at once, which usually means the "
                "newest sheet row is only half entered rather than that this many "
                "accounts closed. The tombstones were still written (they self-correct "
                "on the next sync once the row is filled in); verify the workbook.",
                *args,
                FS_TOMBSTONE_BLAST_RADIUS_WARN,
            )
        else:
            logger.info(message, *args)

    if missing_columns:
        # A mapped column that vanished from the sheet is NOT tombstoned: a
        # rename, a reordered export or an older copy of the workbook is
        # indistinguishable from a deletion, and zeroing a live asset on that
        # signal is far more damaging than leaving it stale. Surface it instead
        # so the state is visible rather than silent.
        logger.warning(
            "melt_financial_summary_holdings: %d mapped column(s) absent from the "
            "balance sheet — no rows emitted and NO tombstone written (a missing "
            "column may be a rename, not a closure): %s",
            len(missing_columns),
            sorted(missing_columns),
        )

    return pd.DataFrame(rows)
