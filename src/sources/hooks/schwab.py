"""Schwab CSV hooks (Program OSR WS-2 mechanical split).

Extracted verbatim from src/sources/reader_hooks.py (pre-split, 1,578 lines) —
see src/sources/hooks/__init__.py for the aggregation and
src/sources/reader_hooks.py for the backward-compatible re-export shim.

IMPORT CONSTRAINT (mirrors src.sources.registry — unchanged from the
pre-split module): stdlib + pandas only at module level. Lazy imports inside
a function body are allowed.
"""
from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from src.database.mapping_seeds import (
    SCHWAB_KNOWN_ETFS_SEED,
    SCHWAB_SYMBOL_NORMALIZATIONS_SEED,
    SCHWAB_ACTION_MAPPING_SEED,
)

# ---------------------------------------------------------------------------
# Schwab helpers — verbatim copies from schwab_reader.py / schwab_transformer.py
# (no src.* imports allowed at module level — cycle guard)
# ---------------------------------------------------------------------------

# ADR-023 WS-C: the literal data for the four constants below now lives in
# src.database.mapping_seeds (single source of truth shared with the
# reader_mappings DB seed migration V78 and the runtime loader). These are
# re-exports/derived copies — same names/shapes as before (plain dict/set) —
# so every existing consumer stays unaffected; the hooks below accept an
# optional override dict/set (sourced from metadata, i.e. a DB-managed
# reader_mappings merge) and fall back to these module constants when none is
# supplied, preserving exact legacy behavior byte-for-byte.

# Schwab compound-ticker normalizations (BRK/B → BRK-B, etc.)
_SCHWAB_SYMBOL_NORMALIZATIONS: Dict[str, str] = dict(SCHWAB_SYMBOL_NORMALIZATIONS_SEED)

# Schwab action → Huinsight transaction type
_SCHWAB_ACTION_MAPPING: Dict[str, str] = dict(SCHWAB_ACTION_MAPPING_SEED)

# Known Schwab ETFs for transaction-symbol normalization (no security_type in txns CSV).
_SCHWAB_KNOWN_ETFS = set(SCHWAB_KNOWN_ETFS_SEED)

# Column aliases in Schwab CSV (verbatim from schwab_reader.COLUMN_ALIASES)
_SCHWAB_COLUMN_ALIASES: Dict[str, str] = {
    'Asset Type': 'Security Type',
}


def _schwab_normalize_symbol(symbol: str, symbol_norm: "Optional[Dict[str, str]]" = None) -> str:
    """Normalize Schwab ticker: BRK/B → BRK-B, slash → dash.

    `symbol_norm` (ADR-023 WS-C): the merged (defaults + DB overrides)
    symbol_norm vocabulary, sourced from metadata by the calling hook — falls
    back to the module default `_SCHWAB_SYMBOL_NORMALIZATIONS` when None
    (byte-identical legacy behavior for every call site that doesn't pass an
    override).
    """
    s = str(symbol).upper().strip()
    norm_map = symbol_norm if symbol_norm is not None else _SCHWAB_SYMBOL_NORMALIZATIONS
    if s in norm_map:
        return norm_map[s]
    return s.replace('/', '-')


def _schwab_map_action(action: str, action_map: "Optional[Dict[str, str]]" = None) -> str:
    """Map Schwab action string to Huinsight transaction type.

    `action_map` (ADR-023 WS-C): merged action_map vocabulary from metadata;
    falls back to `_SCHWAB_ACTION_MAPPING` when None.
    """
    m = action_map if action_map is not None else _SCHWAB_ACTION_MAPPING
    return m.get(action, 'other')


def _schwab_normalize_to_canonical_id(
    symbol: str, security_type: str, symbol_norm: "Optional[Dict[str, str]]" = None
) -> str:
    """Convert Schwab symbol + security_type to canonical asset_id.

    Security type substring rules (verbatim from SchwabReader._normalize_to_canonical_id):
      'ETF' in upper → US_ETF_*
      'Common Stock' / 'Equity' → US_STK_*
      'BOND' → US_BND_*
      'FUND' or 'MUTUAL' → US_FUND_*
      'Option' / 'Options' → US_OPT_*
      fallback → US_STK_*

    `symbol_norm` (ADR-023 WS-C): threaded through to `_schwab_normalize_symbol`
    — also used by IBKR's co-authority holdings/transactions hooks, which call
    this function directly with their OWN metadata's schwab_symbol_norm so the
    same merged vocabulary reaches both brokers.
    """
    symbol = _schwab_normalize_symbol(symbol, symbol_norm)
    security_type = str(security_type).strip()
    security_type_upper = security_type.upper()

    if 'ETF' in security_type_upper:
        return f'US_ETF_{symbol}'
    elif security_type in ('Common Stock', 'Equity'):
        return f'US_STK_{symbol}'
    elif 'BOND' in security_type_upper:
        return f'US_BND_{symbol}'
    elif 'FUND' in security_type_upper or 'MUTUAL' in security_type_upper:
        return f'US_FUND_{symbol}'
    elif security_type in ('Option', 'Options'):
        return f'US_OPT_{symbol}'
    else:
        return f'US_STK_{symbol}'


def _schwab_normalize_transaction_symbol(
    symbol,
    symbol_norm: "Optional[Dict[str, str]]" = None,
    known_etfs: "Optional[set]" = None,
) -> str:
    """Normalize transaction symbol → canonical ID (no security_type in txns CSV).

    `known_etfs` (ADR-023 WS-C): merged known_etf vocabulary (a set of
    tickers) from metadata; falls back to `_SCHWAB_KNOWN_ETFS` when None.
    """
    if pd.isna(symbol) or symbol == '':
        return 'UNKNOWN'
    sym = _schwab_normalize_symbol(str(symbol), symbol_norm)
    etfs = known_etfs if known_etfs is not None else _SCHWAB_KNOWN_ETFS
    if sym in etfs:
        return f'US_ETF_{sym}'
    return f'US_STK_{sym}'


def _schwab_parse_dollar(value) -> float:
    """Parse '$6,440.00' or '$-1.20' → float."""
    if pd.isna(value) or value == '--' or value == '':
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace('$', '').replace(',', '').strip()
    if cleaned == '--' or cleaned == '':
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _schwab_parse_percent(value) -> float:
    """Parse '17.73%' → float."""
    if pd.isna(value) or value == '--' or value == '':
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace('%', '').strip()
    if cleaned == '--' or cleaned == '':
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _schwab_parse_number(value) -> float:
    """Parse numeric value, treating '--' as 0."""
    if pd.isna(value) or value == '--' or value == '':
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(',', ''))
    except ValueError:
        return 0.0


def _schwab_parse_date(value) -> str:
    """Parse MM/DD/YYYY → YYYY-MM-DD; return as-is if unparseable (F1 preserved)."""
    from datetime import datetime as _dt
    if pd.isna(value) or value == '':
        return ''
    try:
        return _dt.strptime(str(value).strip(), '%m/%d/%Y').strftime('%Y-%m-%d')
    except ValueError:
        return str(value)


# ---------------------------------------------------------------------------
# Schwab hooks (B2 sitting #4a)
# ---------------------------------------------------------------------------


def schwab_holdings_from_csv(
    positions_df: pd.DataFrame,
    metadata: dict,
) -> pd.DataFrame:
    """Derive Schwab holdings from the raw positions CSV DataFrame.

    Fuses SchwabReader._read_positions() POST-read logic with
    schwab_transformer.transform_holdings() — byte-identical to the legacy chain.

    Steps:
      1. Apply Asset Type → Security Type column alias.
      2. Extract cash balance from 'Cash' row → local var.
      3. Drop cash rows and summary (Total) rows from holdings.
      4. Parse $ / % / number columns.
      5. Normalize symbol + security_type → canonical_id → asset_id.
      6. Apply FX: market_value × settings-driven USD→CNY rate (historical
         default 7.0 — see currency_converter.get_default_usd_cny_rate());
         market_price_unit/cost_price_unit native USD.
      7. Reinsert cash as CASH_USD row.
      8. snapshot_date from metadata['positions_date'] (date obj) or today.
      9. Output columns in legacy order.

    F1 MoneyLink date-drop bug is NOT fixed here — it lives downstream
    in _normalize_transactions_df and is out of scope for 4a.

    Args:
        positions_df: Raw positions DataFrame as read by pd.read_csv(skiprows=2).
        metadata: Engine metadata dict; expects 'positions_date' (datetime.date)
            set by the header_regex strategy in config_driven_reader.

    Returns:
        Holdings DataFrame with columns matching schwab_transformer.transform_holdings:
            [asset_id, quantity, market_price_unit, market_value,
             cost_price_unit, gain_dollar, gain_percent,
             snapshot_date, source_system]
    """
    import re as _re
    from datetime import datetime as _dt

    if positions_df is None or positions_df.empty:
        return pd.DataFrame()

    df = positions_df.copy()

    # 1. Apply column alias (Asset Type → Security Type)
    for old_col, new_col in _SCHWAB_COLUMN_ALIASES.items():
        if old_col in df.columns and new_col not in df.columns:
            df = df.rename(columns={old_col: new_col})

    # Require Symbol column to proceed
    if 'Symbol' not in df.columns:
        return pd.DataFrame()

    # 2. Extract cash balance from 'Cash' row
    cash_balance = 0.0
    cash_mask = df['Symbol'].str.contains('Cash', case=False, na=False)
    if cash_mask.any():
        cash_row = df[cash_mask].iloc[0]
        cash_balance = _schwab_parse_dollar(cash_row.get('Mkt Val (Market Value)', '0'))

    # 3. Drop cash rows and summary rows (containing "TOTAL")
    df = df[~df['Symbol'].str.contains('Cash', case=False, na=False)].copy()
    # Drop summary rows: non-null symbol that contains TOTAL with a word boundary
    def _is_summary(sym):
        if pd.isna(sym):
            return False
        s = str(sym).strip().upper()
        return bool(s and "TOTAL" in s and _re.search(r"\sTOTAL\b", s))
    df = df[~df['Symbol'].apply(_is_summary)].copy()

    if df.empty and cash_balance == 0:
        return pd.DataFrame()

    # snapshot_date from metadata positions_date (set by header_regex) or today
    positions_date = metadata.get('positions_date')
    snapshot_date = (
        positions_date.strftime('%Y-%m-%d')
        if positions_date is not None
        else _dt.now().strftime('%Y-%m-%d')
    )

    # 4–6. Parse columns and build rows
    # Fetch live USD→CNY rate once per hook invocation for the cash row.
    # Non-cash stock rows use the settings-driven default (Program OSR WS-2
    # step 3 — historical default 7.0) because they are corrected downstream
    # by _update_from_dsa; cash has no market price and is never corrected,
    # so this stamp is final.
    from src.market_data.fetchers.yfinance_fetcher import fetch_fx_rates as _fetch_fx_rates
    from src.data_manager.currency_converter import get_default_usd_cny_rate
    _default_usd_cny_rate = get_default_usd_cny_rate()
    _cash_fx = float(_fetch_fx_rates().get("USD", _default_usd_cny_rate))

    # ADR-023 WS-C: merged symbol_norm vocabulary injected via metadata by the
    # orchestrator (src.services.reader_mappings.load_reader_mappings) — None
    # (key absent) falls back to the module default inside the helper.
    symbol_norm = metadata.get("schwab_symbol_norm")

    rows = []
    for _, row in df.iterrows():
        qty = _schwab_parse_number(row.get('Qty (Quantity)', 0))
        cost_basis_total = _schwab_parse_dollar(row.get('Cost Basis', 0))
        cost_price_unit = (cost_basis_total / qty) if qty > 0 else 0.0
        market_value_usd = _schwab_parse_dollar(row.get('Mkt Val (Market Value)', 0))
        price = _schwab_parse_dollar(row.get('Price', 0))
        gain_dollar = _schwab_parse_dollar(row.get('Gain $ (Gain/Loss $)', 0))
        gain_percent = _schwab_parse_percent(row.get('Gain % (Gain/Loss %)', 0))
        symbol = str(row.get('Symbol', ''))
        security_type = str(row.get('Security Type', ''))
        canonical_id = _schwab_normalize_to_canonical_id(symbol, security_type, symbol_norm)

        rows.append({
            'asset_id': canonical_id,
            'quantity': qty,
            'market_price_unit': price,
            'market_value': market_value_usd * _default_usd_cny_rate,
            'cost_price_unit': cost_price_unit,
            'gain_dollar': gain_dollar,
            'gain_percent': gain_percent,
            'snapshot_date': snapshot_date,
            'source_system': 'Schwab_CSV',
        })

    # 7. Reinsert cash as CASH_USD row
    if cash_balance != 0:
        rows.append({
            'asset_id': 'CASH_USD',
            'quantity': 1.0,
            'market_price_unit': float(cash_balance),
            'market_value': float(cash_balance) * _cash_fx,
            'cost_price_unit': float(cash_balance),
            'gain_dollar': 0.0,
            'gain_percent': 0.0,
            'snapshot_date': snapshot_date,
            'source_system': 'Schwab_CSV',
        })

    if not rows:
        return pd.DataFrame()

    # 9. Output columns in exact legacy transform_holdings order
    output_cols = [
        'asset_id', 'quantity', 'market_price_unit', 'market_value',
        'cost_price_unit', 'gain_dollar', 'gain_percent',
        'snapshot_date', 'source_system',
    ]
    result = pd.DataFrame(rows)
    return result[[c for c in output_cols if c in result.columns]]


def schwab_transactions_from_csv(
    txns_df: pd.DataFrame,
    metadata: dict,
) -> pd.DataFrame:
    """Derive Schwab transactions from the raw transactions CSV DataFrame.

    Fuses SchwabReader._read_transactions() POST-read logic with
    schwab_transformer.transform_transactions() and the drop_duplicates
    call in sync_schwab — byte-identical to the full legacy sync chain.

    Steps:
      1. Parse date, symbol, action columns from raw CSV DF.
      2. Normalize symbol → canonical_id (known-ETF list, no security_type).
      3. Map action → transaction_type.
      4. Split reinvest_dividend → 2 rows (dividend + buy).
      5. Passthrough all other transaction types.
      6. drop_duplicates(subset=[...], keep='last') — mirrors sync_schwab.
      7. Output columns in legacy transform_transactions order.

    F1 MoneyLink date-drop bug PRESERVED: MoneyLink dates like
    '04/13/2026 as of 04/10/2026' fail _parse_date and come through as-is.
    That filtering is downstream in _normalize_transactions_df (out of scope).

    Args:
        txns_df: Raw transactions DataFrame as read by pd.read_csv(skiprows=0).
        metadata: Engine metadata dict (unused for transactions).

    Returns:
        Transactions DataFrame with columns:
            [asset_id, transaction_date, transaction_type, quantity,
             price, amount, fees, description, source_system]
        after drop_duplicates.
    """
    if txns_df is None or txns_df.empty:
        return pd.DataFrame()

    df = txns_df.copy()

    # Required columns check (mirrors _read_transactions)
    required = ['Date', 'Action', 'Symbol', 'Amount']
    if not all(c in df.columns for c in required):
        return pd.DataFrame()

    # ADR-023 WS-C: merged vocabularies injected via metadata by the
    # orchestrator — None (key absent) falls back to the module defaults
    # inside each helper, preserving exact legacy behavior.
    symbol_norm = metadata.get("schwab_symbol_norm")
    known_etfs = metadata.get("schwab_known_etf")
    action_map = metadata.get("schwab_action_map")

    rows = []
    for _, row in df.iterrows():
        date_str = _schwab_parse_date(row.get('Date', ''))
        action = str(row.get('Action', '')) if pd.notna(row.get('Action')) else ''
        symbol = row.get('Symbol', '')
        description = str(row.get('Description', '')) if pd.notna(row.get('Description')) else ''
        quantity = _schwab_parse_number(row.get('Quantity', 0))
        price = _schwab_parse_dollar(row.get('Price', 0))
        fees = _schwab_parse_dollar(row.get('Fees & Comm', 0))
        amount = _schwab_parse_dollar(row.get('Amount', 0))

        canonical_id = _schwab_normalize_transaction_symbol(symbol, symbol_norm, known_etfs)
        transaction_type = _schwab_map_action(action, action_map)

        # ADR-023 WS-3.1 (V79): 'transfer' is a pseudo-type — 'Security
        # Transfer' is directionally ambiguous (one Schwab action label covers
        # both ACAT legs), so the action_map targets the pseudo-type and it is
        # resolved here by quantity sign, immediately, before any row is ever
        # appended. It never reaches `rows` as literal 'transfer'.
        if transaction_type == 'transfer':
            transaction_type = 'transfer_out' if quantity < 0 else 'transfer_in'

        if transaction_type == 'reinvest_dividend':
            # Split reinvest into 2 rows: dividend + buy (Decision 9)
            original_amount = amount
            dividend_amount = abs(original_amount)

            # Row 1: Dividend received
            rows.append({
                'asset_id': canonical_id,
                'transaction_date': date_str,
                'transaction_type': 'dividend',
                'quantity': 0.0,
                'price': 0.0,
                'amount': dividend_amount,
                'fees': 0.0,
                'description': description + ' (reinvested)',
                'source_system': 'Schwab_CSV',
            })
            # Row 2: Buy with reinvested funds
            rows.append({
                'asset_id': canonical_id,
                'transaction_date': date_str,
                'transaction_type': 'buy',
                'quantity': quantity,
                'price': price,
                'amount': -dividend_amount,
                'fees': fees,
                'description': description + ' (reinvest buy)',
                'source_system': 'Schwab_CSV',
            })
        else:
            rows.append({
                'asset_id': canonical_id,
                'transaction_date': date_str,
                'transaction_type': transaction_type,
                'quantity': quantity,
                'price': price,
                'amount': amount,
                'fees': fees,
                'description': description,
                'source_system': 'Schwab_CSV',
            })

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)

    # drop_duplicates — mirrors sync_schwab() final step
    result = result.drop_duplicates(
        subset=['transaction_date', 'asset_id', 'transaction_type', 'amount', 'source_system'],
        keep='last',
    )

    # Output columns in exact legacy transform_transactions order
    output_cols = [
        'asset_id', 'transaction_date', 'transaction_type',
        'quantity', 'price', 'amount', 'fees', 'description', 'source_system',
    ]
    return result[[c for c in output_cols if c in result.columns]]
