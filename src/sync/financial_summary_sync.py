"""Financial Summary sync module — reads Excel and returns DataFrames.

Returns:
- balance_sheet: 资产负债 — monthly asset snapshots (all assets).
- income_expense: 月度收支 — monthly 收入/开支/消费/投资 (income, expense, investment flows).
- holdings / transactions: same as above (backward compatibility).

Config-driven engine only (B5 — legacy FinancialSummaryReader/transformer deleted).
The raw balance_sheet and income_expense DataFrames are read directly with pandas
(same logic as the legacy FinancialSummaryReader.read()).  No DB writes here.
"""
import re
from pathlib import Path
from typing import Dict, Any
import pandas as pd
import logging

from src.validation.source_format_validator import validate_financial_summary_format

logger = logging.getLogger(__name__)


def _normalize_id_part(value) -> str:
    """Normalize a row value into a stable ID-safe segment."""
    if pd.isna(value):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    text = re.sub(r"[^\w一-鿿]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    return text.upper()


def _add_synthetic_asset_ids(df: pd.DataFrame, prefix: str, preferred_cols: list) -> pd.DataFrame:
    """Add deterministic synthetic asset IDs (ported from legacy FS transformer)."""
    work_df = df.copy()
    id_cols = [c for c in preferred_cols if c in work_df.columns]
    if not id_cols:
        fallback = [c for c in work_df.columns if c not in ("asset_id", "source_system")]
        id_cols = fallback[:2]
    else:
        id_cols = id_cols[:2]

    ids = []
    seen: dict = {}
    for idx, row in work_df.iterrows():
        parts = [_normalize_id_part(row.get(col)) for col in id_cols]
        parts = [p for p in parts if p]
        base = f"{prefix}_{'_'.join(parts)}" if parts else f"{prefix}_ROW_{idx + 1}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        ids.append(base if count == 1 else f"{base}_{count}")
    work_df["asset_id"] = ids
    return work_df


def _read_fs_sheet(file_path: Path, sheet_name: str) -> pd.DataFrame:
    """Read a Financial Summary sheet with header at row 4 (0-indexed 3)."""
    try:
        df = pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl", header=3)
        df = df.dropna(how="all")
        if len(df) > 3:
            df = df.iloc[3:].reset_index(drop=True)
        return df
    except Exception as exc:
        logger.warning(f"Financial Summary: could not read sheet '{sheet_name}': {exc}")
        return pd.DataFrame()


def _transform_balance_sheet(df: pd.DataFrame) -> pd.DataFrame:
    """Add synthetic IDs and source_system to the 资产负债 DataFrame."""
    if df.empty:
        return pd.DataFrame()
    df = _add_synthetic_asset_ids(
        df,
        prefix="BS",
        preferred_cols=["Item", "项目", "Category", "类别", "SubCategory", "科目", "Date", "Month", "日期"],
    )
    df["source_system"] = "Financial_Summary"
    invalid = ["US_Fund_Portfolio", "AccountTotal", "合计", "Total Assets"]
    df = df[~df["asset_id"].str.contains("|".join(invalid), na=False)]
    return df


def _transform_income_expense(df: pd.DataFrame) -> pd.DataFrame:
    """Add synthetic IDs and source_system to the 月度收支 DataFrame."""
    if df.empty:
        return pd.DataFrame()
    df = _add_synthetic_asset_ids(
        df,
        prefix="IE",
        preferred_cols=["Item", "项目", "Category", "类别", "SubCategory", "科目", "Type", "类型", "Date", "Month", "日期"],
    )
    df["source_system"] = "Financial_Summary"
    invalid = ["US_Fund_Portfolio", "AccountTotal", "合计"]
    df = df[~df["asset_id"].str.contains("|".join(invalid), na=False)]
    return df


def sync_financial_summary(config: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
    """Sync Financial Summary Excel: 资产负债 (balance sheet) + 月度收支 (income/expense/investment)."""
    type_config = config.get('source_registry', {}).get('financial_summary', {})
    empty = {'holdings': pd.DataFrame(), 'transactions': pd.DataFrame(),
             'balance_sheet': pd.DataFrame(), 'income_expense': pd.DataFrame()}

    if not type_config.get('enabled', False):
        logger.info("Financial Summary sync disabled")
        return empty

    data_dir = type_config.get('data_dir')
    if not data_dir:
        data_dir = config.get('finance_dir') or config.get('sources', {}).get('pis', {}).get('finance_dir')
        if not data_dir:
            logger.warning("Financial Summary data_dir not configured and no finance_dir available")
            return empty

    data_path = Path(data_dir)
    workbook_name = type_config.get('file_patterns', {}).get(
        'workbook', 'Financial Summary_new.xlsx'
    )
    workbook_path = data_path / workbook_name

    if not workbook_path.exists():
        logger.warning(f"Financial Summary workbook not found: {workbook_path}")
        return empty

    validation = validate_financial_summary_format(workbook_path)
    if not validation.is_valid:
        logger.warning(f"Financial Summary validation failed: {validation.warnings}")

    balance_sheet_df = _transform_balance_sheet(_read_fs_sheet(workbook_path, "资产负债"))
    income_expense_df = _transform_income_expense(_read_fs_sheet(workbook_path, "月度收支"))

    logger.info(
        f"Financial Summary sync: {len(balance_sheet_df)} balance_sheet (资产负债), "
        f"{len(income_expense_df)} income_expense (月度收支)"
    )

    return {
        'holdings': balance_sheet_df,
        'transactions': income_expense_df,
        'balance_sheet': balance_sheet_df,
        'income_expense': income_expense_df,
    }
