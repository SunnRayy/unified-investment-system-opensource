"""Asset ID normalization utilities.

Handles the challenge of different asset ID formats across data sources:
- PIS SQLite: Sometimes stores as `311` (short)
- DSA SQLite: Stores as `110020` (full 6-digit)
- Excel: May store as numeric (311) or text ('311')

This module normalizes all numeric fund codes to 6-digit format.
"""

from typing import Union
import pandas as pd


def normalize_asset_id(asset_id: Union[str, int, None]) -> str:
    """
    Normalize an asset ID to standard format.

    Rules:
    - Numeric IDs 1-6 digits: pad to 6 digits (198 -> 000198)
    - Numeric IDs 7+ digits: keep as-is
    - Non-numeric IDs: keep as-is (e.g., Ins_xxx, CASH_CNY)
    - None/empty: return empty string

    Args:
        asset_id: The asset ID to normalize

    Returns:
        Normalized asset ID as string
    """
    if asset_id is None:
        return ""

    # Convert to string
    asset_str = str(asset_id).strip()

    if not asset_str:
        return ""

    # Check if purely numeric
    if asset_str.isdigit():
        # Pad to 6 digits if shorter
        if len(asset_str) <= 6:
            return asset_str.zfill(6)

    # Non-numeric or already 7+ digits - return as-is
    return asset_str


class AssetIdNormalizer:
    """Class for normalizing asset IDs in DataFrames."""

    def normalize_column(self, df: pd.DataFrame, column: str) -> pd.DataFrame:
        """
        Normalize an asset ID column in a DataFrame.

        Args:
            df: Input DataFrame
            column: Name of the column containing asset IDs

        Returns:
            DataFrame with normalized asset ID column
        """
        df = df.copy()
        df[column] = df[column].apply(normalize_asset_id)
        return df
