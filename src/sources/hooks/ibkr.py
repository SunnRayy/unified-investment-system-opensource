"""IBKR Flex Query hooks (Program OSR WS-2 mechanical split).

Extracted verbatim from src/sources/reader_hooks.py (pre-split, 1,578 lines) —
see src/sources/hooks/__init__.py for the aggregation and
src/sources/reader_hooks.py for the backward-compatible re-export shim.

IMPORT CONSTRAINT (mirrors src.sources.registry — unchanged from the
pre-split module): stdlib + pandas only at module level, PLUS one sibling
import from hooks.schwab (see below) — that module obeys the same
stdlib+pandas-only constraint and never imports upward into config_driven_reader
/ sync / api, so this does not reintroduce the cycle the constraint guards
against. It is the same in-package function call the pre-split module already
made (both hooks lived in one file); only the import boundary is new.
"""
from __future__ import annotations

import pandas as pd

from src.sources.hooks.schwab import _schwab_normalize_to_canonical_id


def ibkr_holdings_from_flex(
    sheet_df: pd.DataFrame,
    metadata: dict,
) -> pd.DataFrame:
    """Derive IBKR holdings from Flex Query POST + CRTT sections.

    Args:
        sheet_df: Raw POST DataFrame (may be empty; hooks use metadata instead).
        metadata: Engine metadata including 'flex_sections', 'snapshot_date',
            'account_id' (set by the flex branch in config_driven_reader.read()).

    Returns:
        Holdings DataFrame with columns:
            [asset_id, quantity, market_price_unit, market_value,
             cost_price_unit, gain_dollar, gain_percent,
             snapshot_date, source_system, account]
    """
    from datetime import datetime as _dt
    from src.market_data.fetchers.yfinance_fetcher import fetch_fx_rates
    from src.data_manager.currency_converter import get_default_usd_cny_rate

    sections = metadata.get("flex_sections", {})
    post_df = sections.get("POST", pd.DataFrame())
    crtt_df = sections.get("CRTT", pd.DataFrame())

    snapshot_date = metadata.get("snapshot_date") or _dt.now().strftime("%Y-%m-%d")
    # Settings-driven default (Program OSR WS-2 step 3, historical default 7.0).
    fx = float(fetch_fx_rates().get("USD", get_default_usd_cny_rate()))
    account_id = metadata.get("account_id", "")
    account = f"IBKR_{account_id}"
    # ADR-023 WS-C: IBKR is co-authority with Schwab and reuses
    # _schwab_normalize_to_canonical_id directly — the SAME merged
    # symbol_norm vocabulary (reader_key='schwab') must reach it too.
    symbol_norm = metadata.get("schwab_symbol_norm")

    rows = []

    # --- POST positions ---
    if not post_df.empty:
        for _, row in post_df.iterrows():
            sym = str(row.get("Symbol", "")).strip()
            if not sym:
                continue
            # Broker-agnostic IDs: match Schwab HOLDINGS canonical (US_STK_*) so the
            # same asset shares one asset_id across brokers (co-authority / FIFO merge).
            # IBKR Flex POST has no security_type, so pass "" → US_STK_ fallback, which
            # is exactly what Schwab's holdings normalizer yields for SGOV/VOO/IEF.
            # (ADR-016: real data uses US_STK_SGOV, not US_ETF_SGOV.)
            asset_id = _schwab_normalize_to_canonical_id(sym, "", symbol_norm)
            try:
                quantity = float(row.get("Quantity", 0))
            except (ValueError, TypeError):
                quantity = 0.0
            try:
                market_price_unit = float(row.get("MarkPrice", 0))
            except (ValueError, TypeError):
                market_price_unit = 0.0
            try:
                market_value = float(row.get("PositionValueInBase", 0)) * fx
            except (ValueError, TypeError):
                market_value = 0.0
            try:
                cost_price_unit = float(row.get("CostBasisPrice", 0))
            except (ValueError, TypeError):
                cost_price_unit = 0.0
            try:
                gain_dollar = float(row.get("FifoPnlUnrealized", 0))
            except (ValueError, TypeError):
                gain_dollar = 0.0
            rows.append({
                "asset_id": asset_id,
                "quantity": quantity,
                "market_price_unit": market_price_unit,
                "market_value": market_value,
                "cost_price_unit": cost_price_unit,
                "gain_dollar": gain_dollar,
                "gain_percent": 0.0,
                "snapshot_date": snapshot_date,
                "source_system": "Broker_IBKR",
                "account": account,
            })

    # --- CRTT cash (BASE_SUMMARY row) ---
    if not crtt_df.empty and "CurrencyPrimary" in crtt_df.columns:
        base_mask = crtt_df["CurrencyPrimary"].str.strip() == "BASE_SUMMARY"
        if base_mask.any():
            cash_row = crtt_df[base_mask].iloc[0]
            try:
                ending = float(cash_row.get("EndingCash", 0))
            except (ValueError, TypeError):
                ending = 0.0
            if ending != 0.0:
                rows.append({
                    "asset_id": "CASH_USD",
                    "quantity": 1.0,
                    "market_price_unit": ending,
                    "market_value": ending * fx,
                    "cost_price_unit": ending,
                    "gain_dollar": 0.0,
                    "gain_percent": 0.0,
                    "snapshot_date": snapshot_date,
                    "source_system": "Broker_IBKR",
                    "account": account,
                })

    if not rows:
        return pd.DataFrame(columns=[
            "asset_id", "quantity", "market_price_unit", "market_value",
            "cost_price_unit", "gain_dollar", "gain_percent",
            "snapshot_date", "source_system", "account",
        ])

    result = pd.DataFrame(rows)
    output_cols = [
        "asset_id", "quantity", "market_price_unit", "market_value",
        "cost_price_unit", "gain_dollar", "gain_percent",
        "snapshot_date", "source_system", "account",
    ]
    return result[[c for c in output_cols if c in result.columns]]


def ibkr_transactions_from_flex(
    sheet_df: pd.DataFrame,
    metadata: dict,
) -> pd.DataFrame:
    """Derive IBKR transactions from Flex Query TRNT (trades) + TRFR (transfers).

    Args:
        sheet_df: Raw TRNT DataFrame (may be empty; hooks use metadata instead).
        metadata: Engine metadata including 'flex_sections'.

    Returns:
        Transactions DataFrame with columns:
            [asset_id, transaction_date, transaction_type, quantity,
             price_unit, amount_gross, commission_fee, memo, source_system]

    Column-name contract (WS-B fix, 2026-08-01): these are the INGEST names — the
    ones ``src/sync/phases/_ingest.py::_normalize_transactions_df`` actually reads
    (``TRANSACTIONS_INSERT_COLUMNS``).  The other readers emit reader-local aliases
    (Schwab/CN Fund/Gold: ``price`` / ``amount`` / ``fees`` / ``description``; RSU:
    ``price_usd`` / ``amount_usd`` / ``fees_usd``; Insurance: ``payment_date``) and
    rely on a per-source ``rename_map`` inside that function.
    **Broker_IBKR has no such branch**, so emitting aliases silently dropped every
    money field
    (``amount_gross=None`` → ``amount_net = 0 - 0 = 0.00``, ``price_unit=NULL``,
    commission and memo lost).  Emit the contract names directly rather than adding a
    fourth alias dialect.  ``tests/sync/test_reader_ingest_column_contract.py`` is the
    structural guard: it probes every reader for silently-dropped columns.
    """
    sections = metadata.get("flex_sections", {})
    trnt_df = sections.get("TRNT", pd.DataFrame())
    trfr_df = sections.get("TRFR", pd.DataFrame())
    # ADR-023 WS-C: same co-authority symbol_norm vocabulary as holdings above.
    symbol_norm = metadata.get("schwab_symbol_norm")

    rows = []

    # --- TRNT: actual trades (buys / sells) ---
    if not trnt_df.empty:
        for _, row in trnt_df.iterrows():
            sym = str(row.get("Symbol", "")).strip()
            if not sym:
                continue
            # Broker-agnostic IDs: match Schwab HOLDINGS canonical (US_STK_*) so the
            # same asset shares one asset_id across brokers (co-authority / FIFO merge).
            # IBKR Flex POST has no security_type, so pass "" → US_STK_ fallback, which
            # is exactly what Schwab's holdings normalizer yields for SGOV/VOO/IEF.
            # (ADR-016: real data uses US_STK_SGOV, not US_ETF_SGOV.)
            asset_id = _schwab_normalize_to_canonical_id(sym, "", symbol_norm)

            # TradeDate column → YYYY-MM-DD
            raw_date = str(row.get("TradeDate", "")).strip()
            if raw_date:
                try:
                    from datetime import datetime as _dt
                    transaction_date = _dt.strptime(raw_date[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
                except ValueError:
                    transaction_date = raw_date[:10]
            else:
                from datetime import datetime as _dt
                transaction_date = _dt.now().strftime("%Y-%m-%d")

            buy_sell = str(row.get("Buy/Sell", "")).strip().upper()
            transaction_type = "buy" if buy_sell.startswith("BUY") else "sell"

            try:
                quantity = abs(float(row.get("Quantity", 0)))
            except (ValueError, TypeError):
                quantity = 0.0
            try:
                price = float(row.get("TradePrice", 0))
            except (ValueError, TypeError):
                price = 0.0
            try:
                commission_fee = abs(float(row.get("IBCommission", 0)))
            except (ValueError, TypeError):
                commission_fee = 0.0

            # amount_net sign convention (AGENTS.md Rule 26) — match Schwab_CSV
            # exactly, do not invent a fourth dialect:
            #   buy  → amount_gross NEGATIVE (cash out), sell → POSITIVE (cash in).
            # _ingest derives amount_net = amount_gross - commission_fee, which is
            # correct under this convention in both directions: a buy becomes more
            # negative by the fee, a sell becomes less positive by the fee.
            if transaction_type == "buy":
                amount_gross = -(quantity * price)
            else:
                amount_gross = quantity * price

            rows.append({
                "asset_id": asset_id,
                "transaction_date": transaction_date,
                "transaction_type": transaction_type,
                "quantity": quantity,
                "price_unit": price,
                "amount_gross": amount_gross,
                "commission_fee": commission_fee,
                "memo": f"IBKR trade {sym}",
                "source_system": "Broker_IBKR",
            })

    # --- TRFR: ACATS transfers (transfer_in / transfer_out — never buy/sell) ---
    if not trfr_df.empty:
        for _, row in trfr_df.iterrows():
            sym = str(row.get("Symbol", "")).strip()
            if not sym:
                continue
            # Broker-agnostic IDs: match Schwab HOLDINGS canonical (US_STK_*) so the
            # same asset shares one asset_id across brokers (co-authority / FIFO merge).
            # IBKR Flex POST has no security_type, so pass "" → US_STK_ fallback, which
            # is exactly what Schwab's holdings normalizer yields for SGOV/VOO/IEF.
            # (ADR-016: real data uses US_STK_SGOV, not US_ETF_SGOV.)
            asset_id = _schwab_normalize_to_canonical_id(sym, "", symbol_norm)

            # DateTime column preferred; fall back to ReportDate
            raw_date = str(row.get("DateTime", "") or row.get("ReportDate", "")).strip()
            if raw_date:
                try:
                    from datetime import datetime as _dt
                    transaction_date = _dt.strptime(raw_date[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
                except ValueError:
                    transaction_date = raw_date[:10]
            else:
                from datetime import datetime as _dt
                transaction_date = _dt.now().strftime("%Y-%m-%d")

            direction = str(row.get("Direction", "")).strip().upper()
            transaction_type = "transfer_in" if direction == "IN" else "transfer_out"

            try:
                quantity = abs(float(row.get("Quantity", 0)))
            except (ValueError, TypeError):
                quantity = 0.0

            # ACATS share transfers move shares, never cash: price_unit /
            # amount_gross / commission_fee are legitimately 0 here. This is the
            # SAME shape Schwab records for its matching transfer_out legs — do not
            # "fix" these to non-zero.
            rows.append({
                "asset_id": asset_id,
                "transaction_date": transaction_date,
                "transaction_type": transaction_type,
                "quantity": quantity,
                "price_unit": 0.0,
                "amount_gross": 0.0,
                "commission_fee": 0.0,
                "memo": f"IBKR ACATS {direction} {sym}",
                "source_system": "Broker_IBKR",
            })

    if not rows:
        return pd.DataFrame(columns=[
            "asset_id", "transaction_date", "transaction_type",
            "quantity", "price_unit", "amount_gross", "commission_fee",
            "memo", "source_system",
        ])

    result = pd.DataFrame(rows)
    output_cols = [
        "asset_id", "transaction_date", "transaction_type",
        "quantity", "price_unit", "amount_gross", "commission_fee",
        "memo", "source_system",
    ]
    return result[[c for c in output_cols if c in result.columns]]
