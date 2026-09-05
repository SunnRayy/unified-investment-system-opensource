"""Tests for wizard_holdings_from_sheet and wizard_transactions_from_sheet hooks (A1).

Hermetic — no file I/O, no DB, no external imports beyond pandas/pytest/stdlib.
All tests call the hook functions directly with in-memory DataFrames.

Coverage:
  1. Holdings: USD row FX applied; CNY row unchanged; snapshot_date injected.
  2. Holdings: snapshot_date mapped explicitly — not overwritten with today.
  3. Transactions: amount_gross FX applied for non-CNY rows.
  4. Empty DataFrame input → empty output.
  5. Empty mapping → empty output.
  6. WizardConfig pydantic model — defaults and optional fx_rate.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

pytestmark = pytest.mark.pipeline

from src.sources.reader_hooks import wizard_holdings_from_sheet, wizard_transactions_from_sheet
from src.sources.reader_config import ParsingConfig, WizardConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _holdings_meta(fx_rate=7.1):
    return {
        "wizard_column_mapping": {
            "asset_id": "Symbol",
            "quantity": "Qty",
            "market_value": "Value",
            "currency": "Ccy",
        },
        "wizard_fx_rate": fx_rate,
        "wizard_import_type": "holdings",
    }


def _sample_holdings_df():
    return pd.DataFrame([
        {"Symbol": "AAPL", "Qty": "10", "Value": "1000.00", "Ccy": "USD"},
        {"Symbol": "CN_FUND_900001", "Qty": "500", "Value": "5000.00", "Ccy": "CNY"},
    ])


# ---------------------------------------------------------------------------
# Test 1: Basic holdings — FX on USD, passthrough for CNY, snapshot_date injected
# ---------------------------------------------------------------------------

def test_wizard_holdings_fx_and_snapshot_date():
    df = _sample_holdings_df()
    meta = _holdings_meta(fx_rate=7.1)
    result = wizard_holdings_from_sheet(df, meta)

    assert not result.empty
    assert set(["asset_id", "quantity", "market_value", "currency", "snapshot_date"]).issubset(
        set(result.columns)
    ), f"Missing expected columns. Got: {list(result.columns)}"

    # USD row (index 0): market_value should be 1000.00 * 7.1
    usd_row = result[result["asset_id"] == "AAPL"].iloc[0]
    assert abs(usd_row["market_value"] - 1000.0 * 7.1) < 1e-9, (
        f"USD row market_value expected {1000.0 * 7.1}, got {usd_row['market_value']}"
    )

    # CNY row (index 1): market_value unchanged (no FX applied)
    cny_row = result[result["asset_id"] == "CN_FUND_900001"].iloc[0]
    assert abs(cny_row["market_value"] - 5000.0) < 1e-9, (
        f"CNY row market_value expected 5000.0, got {cny_row['market_value']}"
    )

    # snapshot_date injected as today (string)
    today_str = str(date.today())
    for _, row in result.iterrows():
        assert row["snapshot_date"] == today_str, (
            f"snapshot_date expected {today_str!r}, got {row['snapshot_date']!r}"
        )


# ---------------------------------------------------------------------------
# Test 2: Holdings where snapshot_date IS mapped — must not be overwritten
# ---------------------------------------------------------------------------

def test_wizard_holdings_snapshot_date_from_mapping():
    raw_df = pd.DataFrame([
        {"Symbol": "TSLA", "Qty": "5", "Value": "200.00", "Ccy": "USD", "Date": "2026-06-15"},
    ])
    meta = {
        "wizard_column_mapping": {
            "asset_id": "Symbol",
            "quantity": "Qty",
            "market_value": "Value",
            "currency": "Ccy",
            "snapshot_date": "Date",
        },
        "wizard_fx_rate": 7.2,
        "wizard_import_type": "holdings",
    }
    result = wizard_holdings_from_sheet(raw_df, meta)

    assert len(result) == 1
    row = result.iloc[0]
    # snapshot_date must be the parsed value ("2026-06-15"), NOT today
    assert row["snapshot_date"] == "2026-06-15", (
        f"snapshot_date must be mapped value '2026-06-15', got {row['snapshot_date']!r}"
    )
    # FX applied (USD)
    assert abs(row["market_value"] - 200.0 * 7.2) < 1e-9


# ---------------------------------------------------------------------------
# Test 3: Transactions — amount_gross FX applied for non-CNY rows
# ---------------------------------------------------------------------------

def test_wizard_transactions_fx_on_amount_gross():
    raw_df = pd.DataFrame([
        {"TxDate": "2026-06-01", "Sym": "MSFT", "Amt": "500.00", "Fee": "2.50", "Cur": "USD"},
        {"TxDate": "2026-06-02", "Sym": "CF_001", "Amt": "1200.00", "Fee": "0.00", "Cur": "CNY"},
    ])
    meta = {
        "wizard_column_mapping": {
            "asset_id": "Sym",
            "transaction_date": "TxDate",
            "amount_gross": "Amt",
            "commission_fee": "Fee",
            "currency": "Cur",
        },
        "wizard_fx_rate": 7.3,
        "wizard_import_type": "transactions",
    }
    result = wizard_transactions_from_sheet(raw_df, meta)

    assert not result.empty
    assert set(["asset_id", "transaction_date", "amount_gross", "commission_fee", "currency"]).issubset(
        set(result.columns)
    )

    # USD row: amount_gross and commission_fee multiplied by fx_rate
    usd_row = result[result["asset_id"] == "MSFT"].iloc[0]
    assert abs(usd_row["amount_gross"] - 500.0 * 7.3) < 1e-9
    assert abs(usd_row["commission_fee"] - 2.5 * 7.3) < 1e-9

    # CNY row: no FX
    cny_row = result[result["asset_id"] == "CF_001"].iloc[0]
    assert abs(cny_row["amount_gross"] - 1200.0) < 1e-9
    assert abs(cny_row["commission_fee"] - 0.0) < 1e-9

    # transaction_date parsed correctly
    assert usd_row["transaction_date"] == "2026-06-01"
    assert cny_row["transaction_date"] == "2026-06-02"

    # No snapshot_date injected in transactions
    assert "snapshot_date" not in result.columns


# ---------------------------------------------------------------------------
# Test 4: Empty DataFrame → empty output
# ---------------------------------------------------------------------------

def test_wizard_holdings_empty_df():
    result = wizard_holdings_from_sheet(pd.DataFrame(), _holdings_meta())
    assert result.empty


def test_wizard_transactions_empty_df():
    meta = {
        "wizard_column_mapping": {"asset_id": "Sym", "amount_gross": "Amt"},
        "wizard_fx_rate": 7.1,
        "wizard_import_type": "transactions",
    }
    result = wizard_transactions_from_sheet(pd.DataFrame(), meta)
    assert result.empty


# ---------------------------------------------------------------------------
# Test 5: Empty mapping → empty output
# ---------------------------------------------------------------------------

def test_wizard_holdings_empty_mapping():
    df = _sample_holdings_df()
    meta = {"wizard_column_mapping": {}, "wizard_fx_rate": 7.1, "wizard_import_type": "holdings"}
    result = wizard_holdings_from_sheet(df, meta)
    assert result.empty


# ---------------------------------------------------------------------------
# Test 6: WizardConfig pydantic model
# ---------------------------------------------------------------------------

def test_wizard_config_defaults():
    wc = WizardConfig()
    assert wc.column_mapping == {}
    assert wc.fx_rate is None
    assert wc.import_type == "holdings"


def test_wizard_config_in_parsing_config():
    """WizardConfig can be embedded in ParsingConfig as optional field."""
    # Build a minimal ParsingConfig that has wizard set
    # (we use model_validate to avoid constructing the full YAML tree)
    raw = {
        "format": "csv",
        "snapshot_date": {"strategy": "read_timestamp"},
        "sheets": [],
        "wizard": {
            "column_mapping": {"asset_id": "Symbol", "market_value": "Value"},
            "fx_rate": 7.1,
            "import_type": "holdings",
        },
    }
    pc = ParsingConfig.model_validate(raw)
    assert pc.wizard is not None
    assert pc.wizard.fx_rate == 7.1
    assert pc.wizard.column_mapping["asset_id"] == "Symbol"
    assert pc.wizard.import_type == "holdings"


def test_wizard_config_optional_none():
    """wizard=None is the default — existing sources unaffected."""
    raw = {
        "format": "excel",
        "snapshot_date": {"strategy": "file_mtime"},
        "sheets": [],
    }
    pc = ParsingConfig.model_validate(raw)
    assert pc.wizard is None
