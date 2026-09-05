"""WS-B3 — do the already-broken Broker_IBKR rows self-heal on the next sync?

The plan assumed yes ("IBKR transactions are re-read from the Flex report each
sync"). Verified against the real ``_replace_transactions``, that is only HALF true:

  * ACATS transfer rows DO heal. The stale rows have ``amount_gross IS NULL``; the
    fixed hook now emits ``0.0``; the incremental DELETE matches on
    ``COALESCE(amount_gross, 0) = COALESCE(?, 0)`` → ``0 = 0`` → the stale row is
    deleted and replaced.

  * TRADE rows (buy/sell) DO NOT heal — they DUPLICATE. The stale row has
    ``amount_gross IS NULL`` (COALESCE → 0) while the corrected row carries the real
    figure (e.g. -6439.68), so the DELETE key does not match, nothing is deleted, and
    the INSERT adds a second row. The DB then holds both the ¥0.00 phantom and the
    correct trade → double-counted FIFO cost basis and a phantom cash flow.

``Broker_IBKR`` is deliberately NOT in ``_FULL_REPLACE_SOURCES`` (it is a date-range
download, not a complete maintained log), so full-replace is not the remedy.

RESOLVED (lead, 2026-08-01): remedy (a) shipped in ``_replace_transactions`` — a
targeted purge of ``source_system='Broker_IBKR' AND transaction_type IN ('buy','sell')
AND amount_gross IS NULL`` runs before the incremental DELETE, mirroring the existing
``CN_Fund_Excel`` retype self-heal. Idempotent; a no-op once none remain. It cannot
match a post-fix row because the hook always computes a numeric amount. Transfer rows
are deliberately out of scope — they heal in place, as the second class below asserts.

Live scope at the time of writing: exactly one such row in the local mirror —
2026-07-17 US_STK_SGOV buy 64 @ amount_net 0.00.

HARD CONSTRAINT: in-memory DuckDB only; the real DB is never opened.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List

import duckdb
import pandas as pd
import pytest

pytestmark = pytest.mark.pipeline

from src.sync.phases._ingest import _FULL_REPLACE_SOURCES, _replace_transactions

_IBKR = "Broker_IBKR"

_TX_COLS = [
    "transaction_date", "asset_id", "asset_name", "transaction_type",
    "quantity", "price_unit", "amount_gross", "amount_net", "commission_fee",
    "currency", "account", "memo", "source_system",
]


def _new_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute(Path("src/database/schema.sql").read_text(encoding="utf-8"))
    conn.execute(
        "ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS verification_status "
        "VARCHAR(20) DEFAULT 'pending'"
    )
    return conn


class _FakeConnector:
    """DatabaseConnector-shaped shim over an in-memory connection."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn

    def execute(self, query: str, params=()) -> duckdb.DuckDBPyConnection:
        return self._conn.execute(query, list(params))

    def executemany(self, query: str, params_list: list) -> None:
        self._conn.executemany(query, params_list)


def _seed_broken_row(
    conn: duckdb.DuckDBPyConnection,
    *,
    transaction_date: date,
    asset_id: str,
    transaction_type: str,
    quantity: float,
) -> None:
    """The exact shape the pre-fix hook produced: amount_gross NULL, amount_net 0.00."""
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, amount_net, commission_fee,
            currency, account, memo, source_system
        ) VALUES (?, ?, ?, ?, ?, NULL, NULL, 0.00, 0.0, 'USD', 'IBKR', NULL, ?)
        """,
        [transaction_date, asset_id, asset_id, transaction_type, quantity, _IBKR],
    )


def _tx_df(rows: List[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=_TX_COLS)


def _ibkr_rows(conn: duckdb.DuckDBPyConnection) -> list:
    return conn.execute(
        "SELECT transaction_type, amount_gross, amount_net FROM transactions "
        "WHERE source_system = ? ORDER BY amount_net",
        [_IBKR],
    ).fetchall()


class TestIbkrReplaceStrategy:
    def test_ibkr_is_not_full_replace(self):
        """Documented invariant — IBKR is a date-range download, not a full log."""
        assert _IBKR not in _FULL_REPLACE_SOURCES


class TestTransferRowsSelfHeal:
    def test_stale_null_transfer_is_replaced_not_duplicated(self):
        conn = _new_conn()
        _seed_broken_row(
            conn,
            transaction_date=date(2026, 6, 8),
            asset_id="US_STK_VOO",
            transaction_type="transfer_in",
            quantity=21.0,
        )

        corrected = _tx_df([{
            "transaction_date": date(2026, 6, 8), "asset_id": "US_STK_VOO",
            "asset_name": "US_STK_VOO", "transaction_type": "transfer_in",
            "quantity": 21.0, "price_unit": 0.0, "amount_gross": 0.0,
            "amount_net": 0.0, "commission_fee": 0.0, "currency": "USD",
            "account": "IBKR", "memo": "IBKR ACATS IN VOO", "source_system": _IBKR,
        }])
        _replace_transactions(_FakeConnector(conn), corrected)

        rows = _ibkr_rows(conn)
        assert len(rows) == 1, (
            f"ACATS transfer must heal in place (NULL and 0.0 both COALESCE to 0), "
            f"got {rows}"
        )
        assert rows[0][0] == "transfer_in"
        assert float(rows[0][2]) == 0.0


class TestTradeRowsSelfHeal:
    """WS-B3 remedy: _replace_transactions purges stale amount_gross IS NULL trades."""

    @staticmethod
    def _run() -> list:
        conn = _new_conn()
        _seed_broken_row(
            conn,
            transaction_date=date(2026, 7, 17),
            asset_id="US_STK_SGOV",
            transaction_type="buy",
            quantity=64.0,
        )
        corrected = _tx_df([{
            "transaction_date": date(2026, 7, 17), "asset_id": "US_STK_SGOV",
            "asset_name": "US_STK_SGOV", "transaction_type": "buy",
            "quantity": 64.0, "price_unit": 100.62, "amount_gross": -6439.68,
            "amount_net": -6440.03, "commission_fee": 0.35, "currency": "USD",
            "account": "IBKR", "memo": "IBKR trade SGOV", "source_system": _IBKR,
        }])
        _replace_transactions(_FakeConnector(conn), corrected)
        return _ibkr_rows(conn)

    def test_stale_zero_trade_row_is_purged(self):
        """The stale amount_net=0.00 row must not survive alongside the correction.

        Without the purge the incremental DELETE (keyed on COALESCE(amount_gross,0))
        cannot match the stale NULL row against the corrected -6439.68, so the INSERT
        adds a second row — double-counting FIFO cost basis by the full trade value.
        """
        rows = self._run()
        assert len(rows) == 1, (
            f"Expected the stale row to be purged, leaving only the correction; got {rows}"
        )
        assert float(rows[0][2]) == pytest.approx(-6440.03)

    def test_purge_is_idempotent_on_a_clean_table(self):
        """Re-running against already-correct rows is a no-op, not a delete."""
        conn = _new_conn()
        corrected = _tx_df([{
            "transaction_date": date(2026, 7, 17), "asset_id": "US_STK_SGOV",
            "asset_name": "US_STK_SGOV", "transaction_type": "buy",
            "quantity": 64.0, "price_unit": 100.62, "amount_gross": -6439.68,
            "amount_net": -6440.03, "commission_fee": 0.35, "currency": "USD",
            "account": "IBKR", "memo": "IBKR trade SGOV", "source_system": _IBKR,
        }])
        _replace_transactions(_FakeConnector(conn), corrected)
        _replace_transactions(_FakeConnector(conn), corrected)
        rows = _ibkr_rows(conn)
        assert len(rows) == 1, rows
        assert float(rows[0][2]) == pytest.approx(-6440.03)
