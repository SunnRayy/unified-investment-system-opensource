"""CN Fund hooks (Program OSR WS-2 mechanical split).

Extracted verbatim from src/sources/reader_hooks.py (pre-split, 1,578 lines) —
see src/sources/hooks/__init__.py for the aggregation and
src/sources/reader_hooks.py for the backward-compatible re-export shim.

IMPORT CONSTRAINT (mirrors src.sources.registry — unchanged from the
pre-split module): stdlib + pandas only at module level. Lazy imports inside
a function body are allowed (e.g. cn_fund_raw_process).
"""
from __future__ import annotations

import logging
from typing import Dict

import pandas as pd

from src.database.mapping_seeds import CN_FUND_TYPE_MAP_SEED

# Logger name pinned to the pre-split module path ("src.sources.reader_hooks"),
# not __name__, so log output stays byte-identical across the WS-2 split —
# tests/sources/test_cn_fund_raw_processor.py's
# TestUploadFailureNonBlocking.test_upload_failure_is_non_blocking filters
# caplog by this exact logger name.
logger = logging.getLogger("src.sources.reader_hooks")


def normalize_fund_code(raw_code) -> str:
    """Normalize a CN Fund code to canonical CN_FUND_ format.

    Handles: int (198), float (198.0), str ("198"), str ("000198")

    Returns: "CN_FUND_110020"
    """
    if pd.isna(raw_code):
        return "CN_FUND_UNKNOWN"
    code_str = str(raw_code).strip()
    if '.' in code_str:
        code_str = code_str.split('.')[0]
    code_str = code_str.zfill(6)
    return f"CN_FUND_{code_str}"


_TRANSACTION_COL_MAP: Dict[str, str] = {
    '交易日期': 'transaction_date',
    '基金代码': 'raw_fund_code',
    '基金名称': 'asset_name',
    '操作类型': 'raw_type',
    '交易金额': 'amount',
    '交易份额': 'quantity',
    '交易时基金单位净值': 'price',
    '手续费': 'fees',
    '交易原因': 'memo',
}

# ADR-023 WS-C: the literal data now lives in src.database.mapping_seeds
# (CN_FUND_TYPE_MAP_SEED) — single source of truth shared with the
# reader_mappings DB seed (migration V78) and the runtime loader. This is a
# re-export/derived copy, same name/shape as before (plain dict) so every
# existing consumer (including tests/sources/test_cn_fund_raw_processor.py's
# direct import) is unaffected.
_CN_FUND_TYPE_MAP: Dict[str, str] = dict(CN_FUND_TYPE_MAP_SEED)


# ---------------------------------------------------------------------------
# CN Fund hooks (B2 sitting #3)
# ---------------------------------------------------------------------------


def cn_fund_raw_process(file_path, metadata: dict):
    """Pre-read hook: run the CN Fund raw processor to update the workbook.

    Mirrors the non-blocking try/except in cn_fund_sync.py (lines 57-65).
    The raw processor writes back to the workbook (wb.save()) — this hook
    MUST NEVER be invoked against test fixtures (disable by setting
    cfg.parsing.pre_read_hook = None in tests).

    Args:
        file_path: Path to the CN Fund Excel workbook.
        metadata: Engine metadata dict (unused here — side-effect only).

    Returns:
        None (side-effect only).
    """
    import logging as _logging
    _logger = _logging.getLogger("src.sources.reader_hooks")
    try:
        from src.sources.cn_fund_raw_processor import process_all  # lazy — avoids cycle
        raw_result = process_all(file_path)
        if raw_result.new_transactions > 0 or raw_result.new_holdings > 0:
            _logger.info(
                "CN Fund raw processor: %d new transactions, %d new holding snapshots",
                raw_result.new_transactions,
                raw_result.new_holdings,
            )
            # GCS write-back (cloud mode only): re-upload the mutated workbook so the
            # persisted processed tabs are not stale on next Cloud Run container start.
            import os as _os
            from pathlib import Path as _Path
            bucket = _os.environ.get("UIS_GCS_BUCKET")
            cloud_dir = _os.environ.get("UIS_FINANCE_DIR")
            if bucket and cloud_dir:
                _file = _Path(file_path).resolve()
                _root = _Path(cloud_dir).resolve()
                if not _file.is_relative_to(_root):
                    _logger.debug(
                        "CN Fund GCS write-back skipped: file %s is not under UIS_FINANCE_DIR %s",
                        file_path,
                        cloud_dir,
                    )
                else:
                    reader_name = _Path(file_path).parent.name
                    try:
                        from src.storage.gcs import upload_source_to_gcs  # lazy
                        upload_source_to_gcs(bucket, reader_name, str(file_path))
                        _logger.info(
                            "CN Fund processed tabs written back to gs://%s/sources/%s/",
                            bucket,
                            reader_name,
                        )
                    except Exception as exc:
                        _logger.warning(
                            "CN Fund processed-tab GCS write-back failed (non-blocking): %s",
                            exc,
                        )
    except Exception as exc:
        _logger.warning("CN Fund raw processor failed (non-blocking): %s", exc)


def cn_fund_holdings_from_sheet(
    sheet_df: pd.DataFrame,
    metadata: dict,
) -> pd.DataFrame:
    """Derive CN Fund holdings from the 基金持仓汇总 raw sheet.

    Fuses the legacy CNFundReader.read() holdings path with transform_holdings():
      1. QDII window: filter to [max_date - 2d, max_date] on Snapshot_Date.
      2. Sort descending by Snapshot_Date.
      3. drop_duplicates(["Asset_ID"], keep="first") → latest per asset.
      4. normalize_fund_code → canonical_id.
      5. Rename columns to Huinsight schema.
      6. asset_id = canonical_id, source_system = "CN_Fund_Excel".
      7. Select final output columns (existing only).

    Empty input → pd.DataFrame().
    Snapshot_Date must NOT use a global MAX — uses the 2-day window exactly.

    Args:
        sheet_df: Raw 基金持仓汇总 sheet as read by pd.read_excel (no header arg).
        metadata: Engine metadata dict (not used for snapshot_date — each row
            carries per-row Snapshot_Date).

    Returns:
        Holdings DataFrame with columns:
            [asset_id, quantity, market_price_unit, market_value,
             snapshot_date, source_system, asset_name, asset_type]
            (filtered to those present in data).
    """
    from datetime import timedelta  # stdlib only

    if sheet_df is None or sheet_df.empty:
        return pd.DataFrame()

    df = sheet_df.copy()

    # 1–3. QDII window + dedup (verbatim port of CNFundReader.read() holdings path)
    if "Snapshot_Date" in df.columns and not df.empty:
        max_date = df["Snapshot_Date"].max()
        min_date = max_date - timedelta(days=2)
        df = df[
            (df["Snapshot_Date"] >= min_date) &
            (df["Snapshot_Date"] <= max_date)
        ].copy()
        df = df.sort_values("Snapshot_Date", ascending=False)

    if "Asset_ID" in df.columns and not df.empty:
        df = df.drop_duplicates(subset=["Asset_ID"], keep="first")

    # 4. Normalize fund code → canonical_id
    if "Asset_ID" in df.columns:
        df = df.copy()
        df["canonical_id"] = df["Asset_ID"].apply(normalize_fund_code)

    # 5. Rename columns to Huinsight internal names
    df = df.rename(columns={
        "Asset_Name": "asset_name",
        "Asset_Type_Raw": "asset_type",
        "Quantity": "quantity",
        "Market_Price_Unit": "market_price_unit",
        "Market_Value_Raw": "market_value",
        "Snapshot_Date": "snapshot_date",
    })

    # 6. asset_id = canonical_id, source_system
    if "canonical_id" in df.columns:
        df["asset_id"] = df["canonical_id"]
    df["source_system"] = "CN_Fund_Excel"

    # 7. Select and order output columns (intersection, preserving order)
    output_cols = [
        "asset_id",
        "quantity",
        "market_price_unit",
        "market_value",
        "snapshot_date",
        "source_system",
        "asset_name",
        "asset_type",
    ]
    cols = [c for c in output_cols if c in df.columns]
    return df[cols]


def cn_fund_transactions_from_sheet(
    sheet_df: pd.DataFrame,
    metadata: dict,
) -> pd.DataFrame:
    """Derive CN Fund transactions from the 基金交易记录 raw sheet.

    Fuses the legacy CNFundReader.read() transactions path with
    transform_transactions():
      1. Rename columns via _TRANSACTION_COL_MAP.
      2. Filter to mapped column names (existing only).
      3. normalize_fund_code(raw_fund_code) → canonical_id → asset_id.
      4. Resolve transaction_type with memo override (mirrors
         _resolve_transaction_type logic inline):
           if memo and '现金分红' in memo → 'dividend_cash'
           elif '红利再投资' in memo → 'dividend_reinvest'
           else _CN_FUND_TYPE_MAP.get(raw_type, 'other')
      5. source_system = "CN_Fund_Excel".
      6. Select final output columns (existing only).

    Empty input → pd.DataFrame().

    Args:
        sheet_df: Raw 基金交易记录 sheet as read by pd.read_excel.
        metadata: Engine metadata dict (unused).

    Returns:
        Transactions DataFrame with columns:
            [asset_id, transaction_date, transaction_type, quantity,
             price, amount, fees, source_system, memo]
            (filtered to those present in data).
    """
    if sheet_df is None or sheet_df.empty:
        return pd.DataFrame()

    df = sheet_df.copy()

    # 1. Rename columns
    df = df.rename(columns=_TRANSACTION_COL_MAP)

    # 2. Filter to mapped values (existing only)
    txn_cols_to_keep = list(_TRANSACTION_COL_MAP.values())
    existing_cols = [c for c in txn_cols_to_keep if c in df.columns]
    df = df[existing_cols].copy()

    # 3. Normalize fund code → canonical_id → asset_id
    if "raw_fund_code" in df.columns:
        df["canonical_id"] = df["raw_fund_code"].apply(normalize_fund_code)
        df["asset_id"] = df["canonical_id"]

    # 4. Resolve transaction type (verbatim inline port of _resolve_transaction_type)
    if "raw_type" in df.columns:
        # ADR-023 WS-C: merged type_map vocabulary injected via metadata by
        # the orchestrator — None (key absent) falls back to the module
        # default, preserving exact legacy behavior.
        _injected_type_map = metadata.get("cn_fund_type_map")
        type_map = _injected_type_map if _injected_type_map is not None else _CN_FUND_TYPE_MAP
        if "memo" in df.columns:
            def _resolve(row):
                memo_str = str(row["memo"]) if pd.notna(row["memo"]) else ""
                raw_type_str = str(row["raw_type"]) if pd.notna(row["raw_type"]) else ""
                if '现金分红' in memo_str:
                    return 'dividend_cash'
                if '红利再投资' in memo_str:
                    return 'dividend_reinvest'
                return type_map.get(raw_type_str, 'other')
            df["transaction_type"] = df.apply(_resolve, axis=1)
        else:
            df["transaction_type"] = df["raw_type"].map(type_map).fillna("other")

    # 5. source_system
    df["source_system"] = "CN_Fund_Excel"

    # 6. Select and order output columns (intersection, preserving order)
    output_cols = [
        "asset_id",
        "transaction_date",
        "transaction_type",
        "quantity",
        "price",
        "amount",
        "fees",
        "source_system",
        "memo",
    ]
    cols = [c for c in output_cols if c in df.columns]
    return df[cols]
