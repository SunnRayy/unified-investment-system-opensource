"""Config-engine smoke tests for FS transform output (B5 — legacy financial_summary_transformer deleted).

The legacy financial_summary_transformer was deleted in Workstream B5.
The synthetic-ID logic is now inlined in financial_summary_sync.py.
The melt logic is in reader_hooks.melt_financial_summary_holdings (via config engine).
These tests verify the public contract of both paths.
"""
import pytest
import pandas as pd
from datetime import datetime
from pathlib import Path

pytestmark = pytest.mark.pipeline

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "readers"
FS_FIXTURE = FIXTURE_DIR / "Financial_Summary_new.xlsx"
CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "readers"
FS_YAML = CONFIG_DIR / "financial_summary.yaml"


class TestFinancialSummarySyncTransformer:
    """Verify the FS sync's transform functions (inlined in financial_summary_sync)."""

    def test_transform_holdings_preserves_columns(self):
        from src.sync.financial_summary_sync import _transform_balance_sheet
        holdings = pd.DataFrame({
            "Date": [datetime(2025, 1, 1)],
            "Total Assets": [100000],
            "Net Worth": [80000],
        })
        df = _transform_balance_sheet(holdings)
        assert "Total Assets" in df.columns
        assert "asset_id" in df.columns
        assert df["asset_id"].iloc[0].startswith("BS_")
        assert "source_system" in df.columns
        assert df["source_system"].iloc[0] == "Financial_Summary"

    def test_transform_transactions_preserves_columns(self):
        from src.sync.financial_summary_sync import _transform_income_expense
        transactions = pd.DataFrame({
            "Month": [datetime(2025, 1, 1)],
            "Income": [5000],
            "Savings": [3000],
        })
        df = _transform_income_expense(transactions)
        assert "Income" in df.columns
        assert "asset_id" in df.columns
        assert df["asset_id"].iloc[0].startswith("IE_")
        assert "source_system" in df.columns

    def test_transform_holdings_asset_id_unique(self):
        from src.sync.financial_summary_sync import _transform_balance_sheet
        holdings = pd.DataFrame({
            "Date": [datetime(2025, 1, 1), datetime(2025, 1, 1)],
            "Total Assets": [100000, 120000],
        })
        df = _transform_balance_sheet(holdings)
        assert len(df["asset_id"].unique()) == 2

    def test_empty_returns_empty(self):
        from src.sync.financial_summary_sync import _transform_balance_sheet, _transform_income_expense
        assert _transform_balance_sheet(pd.DataFrame()).empty
        assert _transform_income_expense(pd.DataFrame()).empty


class TestFSAssetMapping:
    """FS_ASSET_MAPPING in reader_hooks is the sole source of truth (B5)."""

    def test_mapping_has_all_expected_ids(self):
        from src.sources.reader_hooks import FS_ASSET_MAPPING
        expected_ids = {
            "CASH_Cash_CNY", "CASH_Deposit_BOB_CNY", "CASH_Deposit_BOC_CNY",
            "CASH_Deposit_BOC_USD", "CASH_Deposit_CMB_CNY", "CASH_Deposit_Chase_USD",
            "CASH_Deposit_Discover_USD", "CASH_Deposit_ICBC_CNY",
            "CASH_Deposit_HSBC_HKD", "CASH_Deposit_HSBC_USD",
            "Pension_Personal", "Property_阳光花园", "Wealth_CMB",
        }
        actual_ids = {v[0] for v in FS_ASSET_MAPPING.values()}
        assert actual_ids == expected_ids

    def test_config_engine_produces_expected_ids(self):
        """Config engine must produce exactly the 10 FS asset IDs on the fixture.

        No metadata is injected here, so the melt falls back to reader_hooks.py's
        hardcoded FS_ASSET_MAPPING. In this public export, mapping_seeds.py is the
        persona-safe twin (tools/release/mapping_seeds.public.py) whose
        固定资产_房产_阳光花园 key already matches the persona fixture's column name
        exactly, so Property_阳光花园 IS produced via this bare path (unlike the
        owner's private repo, where the real dict and the persona fixture diverge —
        see docs/plans/2026-08-16-ws1-swap-impact.md §3.1 for that case).
        """
        from src.sources.config_driven_reader import ConfigDrivenReader
        from src.sources.reader_config import load_reader_config
        cfg = load_reader_config(FS_YAML)
        reader = ConfigDrivenReader(cfg)
        data = reader.read(FS_FIXTURE)
        holdings_df, _ = reader.transform(data)
        expected_ids = {
            "CASH_Cash_CNY", "CASH_Deposit_BOB_CNY", "CASH_Deposit_BOC_CNY",
            "CASH_Deposit_BOC_USD", "CASH_Deposit_CMB_CNY", "CASH_Deposit_Chase_USD",
            "CASH_Deposit_Discover_USD", "Pension_Personal", "Property_阳光花园", "Wealth_CMB",
        }
        actual_ids = set(holdings_df["asset_id"].unique())
        assert actual_ids == expected_ids, (
            f"FS asset ID mismatch.\nExpected: {sorted(expected_ids)}\nGot: {sorted(actual_ids)}"
        )

    def test_usd_deposit_currency_label_is_cny(self):
        """FS USD deposits store CNY values, so their currency label must be 'CNY' (B3 fix).

        The FS Excel has the owner enter USD×rate (already CNY) in those columns.
        Using 'USD' would cause double-conversion in XIRR and attribution routes.
        """
        from src.sources.reader_hooks import FS_ASSET_MAPPING

        usd_deposit_ids = {
            "CASH_Deposit_BOC_USD",
            "CASH_Deposit_Chase_USD",
            "CASH_Deposit_Discover_USD",
        }
        for col, (asset_id, _name, currency) in FS_ASSET_MAPPING.items():
            if asset_id in usd_deposit_ids:
                assert currency == "CNY", (
                    f"{asset_id}: expected currency='CNY' (value stored in CNY), "
                    f"got '{currency}'"
                )


class TestHSBCMultiCurrencyColumns:
    """HSBC Hong Kong multi-currency account columns (added 2026年7月).

    FS Excel gained 4 new 资产负债 columns: HKD存款_HSBC_HKD (native HKD, must
    stay IGNORED), HKD存款_HSBC (CNY-converted, MUST be read), 美元存款_HSBC_USD
    (native USD, must stay IGNORED), 美元存款_HSBC (CNY-converted, MUST be read).
    """

    def _make_sheet_row(self, **overrides):
        row = {
            "日期": pd.Timestamp("2026-07-31"),
            "HKD存款_HSBC_HKD": 10000.0,
            "HKD存款_HSBC": 1234.5,
            "美元存款_HSBC_USD": 500.0,
            "美元存款_HSBC": 3600.0,
        }
        row.update(overrides)
        return pd.DataFrame([row])

    def test_cny_columns_melt_into_holdings(self):
        """The two CNY-converted HSBC columns must melt into holdings rows
        with no further conversion applied (value stored as-is, currency CNY)."""
        from src.sources.reader_hooks import melt_financial_summary_holdings

        sheet_df = self._make_sheet_row()
        result = melt_financial_summary_holdings(sheet_df, metadata={})

        hkd_row = result[result["asset_id"] == "CASH_Deposit_HSBC_HKD"]
        usd_row = result[result["asset_id"] == "CASH_Deposit_HSBC_USD"]

        assert len(hkd_row) == 1
        assert hkd_row["market_value"].iloc[0] == 1234.5
        assert hkd_row["currency"].iloc[0] == "CNY"

        assert len(usd_row) == 1
        assert usd_row["market_value"].iloc[0] == 3600.0
        assert usd_row["currency"].iloc[0] == "CNY"

    def test_native_currency_columns_produce_no_rows(self):
        """The native HKD存款_HSBC_HKD / 美元存款_HSBC_USD columns are NOT in
        FS_ASSET_MAPPING and must never be melted into holdings, even though
        they are present in the sheet with non-zero values."""
        from src.sources.reader_hooks import melt_financial_summary_holdings

        sheet_df = self._make_sheet_row()
        result = melt_financial_summary_holdings(sheet_df, metadata={})

        # Only the two whitelisted CNY-value asset_ids should appear —
        # nothing derived from the native-currency columns.
        assert set(result["asset_id"].unique()) == {
            "CASH_Deposit_HSBC_HKD",
            "CASH_Deposit_HSBC_USD",
        }
        # Neither native value (10000.0 HKD, 500.0 USD) should leak through
        # as a market_value anywhere in the output.
        assert 10000.0 not in result["market_value"].values
        assert 500.0 not in result["market_value"].values

    def test_empty_values_produce_no_rows_but_explicit_zero_does(self):
        """Absent/NaN columns still produce no rows; an explicit 0 now does.

        Contract change (P1 fix, 2026-08-01): the melt used to drop `== 0` with
        the same filter as NaN, so a mapped column the owner deliberately zeroed
        emitted nothing and its previous non-zero row stayed the asset's latest
        snapshot forever. NaN still means "not recorded" (no row); a literal 0
        now means "this balance is empty" and emits a zero-value tombstone row.
        See tests/sources/test_fs_blank_column_tombstone.py.
        """
        from src.sources.reader_hooks import melt_financial_summary_holdings

        # Case 1: columns absent entirely (pre-July-2026 sheet shape)
        sheet_df_missing = pd.DataFrame([{"日期": pd.Timestamp("2026-06-30")}])
        result_missing = melt_financial_summary_holdings(sheet_df_missing, metadata={})
        assert result_missing.empty or "CASH_Deposit_HSBC_HKD" not in set(
            result_missing.get("asset_id", pd.Series(dtype=object))
        )

        # Case 2: column present but NaN with no prior non-zero value in this
        # sheet — "not recorded", no row.
        sheet_df_nan = self._make_sheet_row(
            **{"HKD存款_HSBC": float("nan"), "美元存款_HSBC": 0}
        )
        result_nan = melt_financial_summary_holdings(sheet_df_nan, metadata={})
        assert "CASH_Deposit_HSBC_HKD" not in set(result_nan.get("asset_id", []))

        # Case 3: explicit 0 — emitted as a zero-value / zero-quantity row.
        zero_rows = result_nan[result_nan["asset_id"] == "CASH_Deposit_HSBC_USD"]
        assert len(zero_rows) == 1
        assert zero_rows["market_value"].iloc[0] == 0
        assert zero_rows["quantity"].iloc[0] == 0.0
