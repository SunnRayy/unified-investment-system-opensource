"""RSU holdings-derivation hook (Program OSR WS-2 mechanical split).

Extracted verbatim from src/sources/reader_hooks.py (pre-split, 1,578 lines) —
see src/sources/hooks/__init__.py for the aggregation and
src/sources/reader_hooks.py for the backward-compatible re-export shim.

IMPORT CONSTRAINT (mirrors src.sources.registry — unchanged from the
pre-split module): stdlib + pandas only at module level. Lazy imports inside
a function body are allowed.
"""
from __future__ import annotations

import pandas as pd


def derive_rsu_holdings(
    transactions_df: pd.DataFrame,
    metadata: dict,
) -> pd.DataFrame:
    """Derive RSU holdings from transactions.

    Ports rsu_transformer.transform_holdings exactly:
      - groupby asset_id (canonical_id → asset_id already set by engine)
      - net_qty = quantity.sum()
      - keep only net_qty > 0
      - latest row by transaction_date → price_usd  (market_price_unit)
      - market_value = net_qty * price_usd * usd_to_cny (settings-driven
        USD→CNY rate, historical default 7.0 — see
        currency_converter.get_default_usd_cny_rate(), Program OSR WS-2 step 3)
      - currency = "USD"
      - source_system = "RSU_Excel"
      - snapshot_date from metadata["snapshot_date"] with fallback to today

    Args:
        transactions_df: Processed transactions DataFrame from the engine.
            Must contain columns: asset_id, quantity, transaction_date,
            price_usd.
        metadata: Engine metadata dict — expects "snapshot_date" key
            (YYYY-MM-DD string set by file_mtime strategy).

    Returns:
        Holdings DataFrame with columns:
            asset_id, quantity, market_price_unit, market_value,
            currency, source_system, snapshot_date
    """
    if transactions_df.empty:
        return pd.DataFrame()

    if "asset_id" not in transactions_df.columns:
        return pd.DataFrame()

    df = transactions_df.copy()

    # snapshot_date: prefer metadata (set by the config engine's snapshot-date
    # strategy — read_timestamp for RSU); fall back to today, matching the
    # legacy rsu_transformer read_timestamp fallback.
    from datetime import datetime as _dt  # stdlib only — see import constraint
    snapshot_date = metadata.get(
        "snapshot_date",
        _dt.now().strftime("%Y-%m-%d"),
    )

    # Settings-driven default (Program OSR WS-2 step 3) — lazy import per this
    # module's own stdlib+pandas-only constraint (see module docstring).
    from src.data_manager.currency_converter import get_default_usd_cny_rate
    usd_to_cny = get_default_usd_cny_rate()
    holdings_rows = []

    for asset_id, group in df.groupby("asset_id"):
        net_qty = group["quantity"].sum()
        if net_qty > 0:
            latest_tx = group.sort_values("transaction_date").iloc[-1]
            price_usd = latest_tx["price_usd"]
            holdings_rows.append(
                {
                    "asset_id": asset_id,
                    "quantity": net_qty,
                    "market_price_unit": price_usd,
                    "market_value": net_qty * price_usd * usd_to_cny,
                    "currency": "USD",
                    "source_system": "RSU_Excel",
                    "snapshot_date": snapshot_date,
                }
            )

    if not holdings_rows:
        return pd.DataFrame()

    return pd.DataFrame(holdings_rows)
