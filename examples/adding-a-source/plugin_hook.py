"""Worked example — Program OSR WS-2. See docs/adding-a-source.md.

A hook registered entirely OUTSIDE src/sources/ — this is the only
executable file involved in adding "Demo Broker" as a data source; the rest
is reader.yaml (this same directory) declaring how to read it.

To use in a real Huinsight install, copy this file to plugins/hooks/demo_broker.py
(see plugins/README.md) — it is discovered and imported automatically the
first time the sync engine looks up 'demo_broker_holdings_from_csv'.
"""
from __future__ import annotations

import pandas as pd

from src.sources.hooks import register_hook


def demo_broker_holdings_from_csv(sheet_df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
    """Turn Demo Broker's raw CSV rows (Symbol, Quantity, Price) into Huinsight
    holdings rows.

    Hook signature: (sheet_df, metadata) -> pd.DataFrame — the same contract
    every built-in *_from_csv hook uses (see src/sources/hooks/schwab.py for
    a fuller real-world example: FX conversion, cash-row handling, etc.).
    """
    if sheet_df is None or sheet_df.empty:
        return pd.DataFrame()

    snapshot_date = metadata.get("snapshot_date")
    rows = []
    for _, row in sheet_df.iterrows():
        symbol = str(row.get("Symbol", "")).strip()
        if not symbol:
            continue
        quantity = float(row.get("Quantity", 0) or 0)
        price = float(row.get("Price", 0) or 0)
        rows.append(
            {
                "asset_id": f"DEMO_{symbol}",
                "asset_name": symbol,
                "quantity": quantity,
                "market_price_unit": price,
                "market_value": quantity * price,
                "currency": "CNY",
                "snapshot_date": snapshot_date,
                "source_system": "Demo_Broker_CSV",
            }
        )
    return pd.DataFrame(rows)


register_hook("demo_broker_holdings_from_csv", demo_broker_holdings_from_csv)
