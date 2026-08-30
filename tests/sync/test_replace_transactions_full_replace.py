"""Tests for _replace_transactions full-replace behaviour (RSU_Excel source).

RSU_Excel uses a full-source-replace strategy: delete ALL existing RSU_Excel rows
then insert the full set from the sheet. This self-corrects orphans left by prior
value-corrections (e.g. sell +5.85 → sell -5.85 that an incremental delete-by-value
key could not match).

Schwab_CSV / Broker_IBKR keep incremental behaviour (date-range downloads, not full
history). All tests use in-memory DuckDB; the real DB is never touched.
"""
import pytest
from datetime import date
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

pytestmark = pytest.mark.pipeline


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _new_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute(
        "ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS verification_status VARCHAR(20) DEFAULT 'pending'"
    )
    return conn


class _FakeConnector:
    """Thin shim exposing the DatabaseConnector interface against an in-memory conn."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn

    def execute(self, query: str, params=()) -> duckdb.DuckDBPyConnection:
        return self._conn.execute(query, list(params))

    def executemany(self, query: str, params_list: list) -> None:
        self._conn.executemany(query, params_list)


_RSU_SRC = "RSU_Excel"
_SCHWAB_SRC = "Schwab_CSV"

_TX_COLS = [
    "transaction_date", "asset_id", "asset_name", "transaction_type",
    "quantity", "price_unit", "amount_gross", "amount_net", "commission_fee",
    "currency", "account", "memo", "source_system",
]


def _make_tx_df(rows: list) -> pd.DataFrame:
    defaults = {
        "asset_name": "Test Asset", "price_unit": None, "amount_gross": None,
        "amount_net": 0.0, "commission_fee": 0.0, "currency": "USD",
        "account": "Test Account", "memo": None,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows], columns=_TX_COLS)


def _seed_tx(
    conn: duckdb.DuckDBPyConnection,
    *,
    transaction_date: date,
    asset_id: str,
    transaction_type: str,
    quantity: float,
    source_system: str,
    memo: Optional[str] = None,
    amount_gross: Optional[float] = None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, amount_gross, amount_net, commission_fee,
            currency, account, memo, source_system
        ) VALUES (?, ?, 'Test Asset', ?, ?, ?, 0, 0, 'USD', 'Test Account', ?, ?)
        RETURNING id
        """,
        [transaction_date, asset_id, transaction_type, quantity, amount_gross, memo, source_system],
    ).fetchone()
    return int(row[0])


def _count(conn: duckdb.DuckDBPyConnection, source_system: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE source_system = ?", [source_system]
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Import under test
# ---------------------------------------------------------------------------
from src.sync.phases._ingest import _replace_transactions, _FULL_REPLACE_SOURCES


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFullReplaceSources:
    def test_rsu_excel_is_full_replace(self):
        assert "RSU_Excel" in _FULL_REPLACE_SOURCES

    def test_schwab_not_full_replace(self):
        assert "Schwab_CSV" not in _FULL_REPLACE_SOURCES

    def test_ibkr_not_full_replace(self):
        assert "Broker_IBKR" not in _FULL_REPLACE_SOURCES


class TestRSUCorrectionPurgesStaleRow:
    """Test 1 — RSU correction removes the stale +5.85 orphan."""

    def test_stale_orphan_is_purged(self):
        conn = _new_conn()
        fc = _FakeConnector(conn)

        d = date(2025, 3, 10)
        asset = "RSU_GOOG"
        # Seed stale, incorrect row (sell +5.85)
        _seed_tx(conn, transaction_date=d, asset_id=asset, transaction_type="sell",
                 quantity=5.85, source_system=_RSU_SRC)

        # New sheet: vest +13 and corrected sell -5.85
        new_tx = _make_tx_df([
            {"transaction_date": date(2025, 1, 5), "asset_id": asset,
             "transaction_type": "vest", "quantity": 13.0, "source_system": _RSU_SRC},
            {"transaction_date": d, "asset_id": asset,
             "transaction_type": "sell", "quantity": -5.85, "source_system": _RSU_SRC},
        ])

        count = _replace_transactions(fc, new_tx)
        assert count == 2

        rows = conn.execute(
            "SELECT transaction_type, quantity FROM transactions WHERE source_system='RSU_Excel' ORDER BY transaction_type"
        ).fetchall()
        assert len(rows) == 2
        sell = [r for r in rows if r[0] == "sell"]
        vest = [r for r in rows if r[0] == "vest"]
        assert len(sell) == 1 and float(sell[0][1]) == pytest.approx(-5.85)
        assert len(vest) == 1 and float(vest[0][1]) == pytest.approx(13.0)


class TestRSUFullReplaceDropsPriorRows:
    """Test 2 — full-replace removes ALL prior RSU rows, even those not in new set."""

    def test_prior_rows_not_in_new_set_are_removed(self):
        conn = _new_conn()
        fc = _FakeConnector(conn)

        asset = "RSU_AMZN"
        for i in range(3):
            _seed_tx(conn, transaction_date=date(2024, 1, i + 1), asset_id=asset,
                     transaction_type="vest", quantity=float(10 + i), source_system=_RSU_SRC)

        assert _count(conn, _RSU_SRC) == 3

        new_tx = _make_tx_df([
            {"transaction_date": date(2026, 1, 1), "asset_id": asset,
             "transaction_type": "vest", "quantity": 50.0, "source_system": _RSU_SRC},
            {"transaction_date": date(2026, 2, 1), "asset_id": asset,
             "transaction_type": "sell", "quantity": -20.0, "source_system": _RSU_SRC},
        ])

        count = _replace_transactions(fc, new_tx)
        assert count == 2
        assert _count(conn, _RSU_SRC) == 2


class TestSchwabIncrementalBehaviourPreserved:
    """Test 3 — Schwab_CSV keeps incremental behaviour (old row survives)."""

    def test_schwab_old_row_survives_when_not_in_new_batch(self):
        conn = _new_conn()
        fc = _FakeConnector(conn)

        # Seed an existing Schwab row with a unique key
        _seed_tx(conn, transaction_date=date(2025, 10, 5), asset_id="US_STK_AAPL",
                 transaction_type="buy", quantity=10.0, amount_gross=1950.0,
                 source_system=_SCHWAB_SRC)

        assert _count(conn, _SCHWAB_SRC) == 1

        # New batch: a different date/asset — key does not overlap with old row
        new_tx = _make_tx_df([
            {"transaction_date": date(2026, 1, 10), "asset_id": "US_STK_MSFT",
             "transaction_type": "buy", "quantity": 5.0, "amount_gross": 2100.0,
             "source_system": _SCHWAB_SRC},
        ])

        _replace_transactions(fc, new_tx)

        # Old row must still exist — Schwab is NOT full-replaced
        assert _count(conn, _SCHWAB_SRC) == 2, (
            "Old Schwab row was unexpectedly deleted (must stay incremental)"
        )


class TestEmptyDfGuard:
    """Test 4 — empty tx_df returns 0 and deletes nothing (guard preserved)."""

    def test_empty_df_is_noop(self):
        conn = _new_conn()
        fc = _FakeConnector(conn)

        _seed_tx(conn, transaction_date=date(2024, 6, 1), asset_id="RSU_AMZN",
                 transaction_type="vest", quantity=48.0, source_system=_RSU_SRC)

        result = _replace_transactions(fc, pd.DataFrame(columns=_TX_COLS))

        assert result == 0
        assert _count(conn, _RSU_SRC) == 1  # seed row untouched


class TestTradeLogLinkReset:
    """Test 5 — trade_log linked_transaction_id is set to NULL for deleted RSU tx."""

    def test_trade_log_link_nulled_on_rsu_replace(self):
        conn = _new_conn()
        fc = _FakeConnector(conn)

        tx_id = _seed_tx(conn, transaction_date=date(2025, 5, 1), asset_id="RSU_AMZN",
                          transaction_type="vest", quantity=48.0, source_system=_RSU_SRC)

        conn.execute(
            "INSERT INTO trade_logs (log_date, asset_id, action, linked_transaction_id, verification_status)"
            " VALUES (?, 'RSU_AMZN', 'Buy', ?, 'verified')",
            [date(2025, 5, 1), tx_id],
        )

        # Confirm link is set
        assert conn.execute(
            "SELECT linked_transaction_id FROM trade_logs WHERE linked_transaction_id = ?", [tx_id]
        ).fetchone() is not None

        # Replace with a different RSU row (old tx is deleted)
        new_tx = _make_tx_df([
            {"transaction_date": date(2026, 3, 1), "asset_id": "RSU_AMZN",
             "transaction_type": "vest", "quantity": 52.0, "source_system": _RSU_SRC},
        ])
        _replace_transactions(fc, new_tx)

        assert _count(conn, _RSU_SRC) == 1
        assert float(conn.execute(
            "SELECT quantity FROM transactions WHERE source_system='RSU_Excel'"
        ).fetchone()[0]) == pytest.approx(52.0)

        null_link = conn.execute("SELECT linked_transaction_id FROM trade_logs").fetchone()[0]
        assert null_link is None, (
            f"Expected trade_log linked_transaction_id=NULL after RSU replace, got {null_link}"
        )


_CN_SRC = "CN_Fund_Excel"


class TestCNFundRetypeSelfHeal:
    """V7.1.7 — CN Fund 卖基金/超级转换份额调减 rows mis-imported as 'other' are
    purged on re-sync (identified by the Chinese label in memo) so the corrected
    'sell'/'transfer_out' row inserts without duplicating."""

    def test_stale_other_sell_is_purged_no_duplicate(self):
        conn = _new_conn()
        fc = _FakeConnector(conn)

        d = date(2026, 6, 22)
        asset = "CN_FUND_900001"
        # Stale row: 卖基金 imported as 'other' before the fix
        _seed_tx(conn, transaction_date=d, asset_id=asset, transaction_type="other",
                 quantity=1.0, source_system=_CN_SRC, memo="卖基金", amount_gross=44106.95)

        # Corrected reader now yields 'sell' for the same transaction
        new_tx = _make_tx_df([
            {"transaction_date": d, "asset_id": asset, "transaction_type": "sell",
             "quantity": 1.0, "amount_gross": 44106.95, "currency": "CNY",
             "memo": "手动卖出", "source_system": _CN_SRC},
        ])
        _replace_transactions(fc, new_tx)

        rows = conn.execute(
            "SELECT transaction_type FROM transactions WHERE source_system=?", [_CN_SRC]
        ).fetchall()
        assert len(rows) == 1, f"expected single row (no duplicate), got {rows}"
        assert rows[0][0] == "sell"

    def test_stale_other_transfer_reduction_is_purged(self):
        conn = _new_conn()
        fc = _FakeConnector(conn)
        d = date(2026, 6, 5)
        asset = "CN_FUND_900006"
        _seed_tx(conn, transaction_date=d, asset_id=asset, transaction_type="other",
                 quantity=1.0, source_system=_CN_SRC, memo="超级转换份额调减", amount_gross=61.03)
        new_tx = _make_tx_df([
            {"transaction_date": d, "asset_id": asset, "transaction_type": "transfer_out",
             "quantity": 1.0, "amount_gross": 61.03, "currency": "CNY",
             "memo": "超级转换份额调减", "source_system": _CN_SRC},
        ])
        _replace_transactions(fc, new_tx)
        rows = conn.execute(
            "SELECT transaction_type FROM transactions WHERE source_system=?", [_CN_SRC]
        ).fetchall()
        assert len(rows) == 1 and rows[0][0] == "transfer_out"

    def test_genuine_other_row_is_not_purged(self):
        """An 'other' row whose memo is NOT in the recovered set must be left alone."""
        conn = _new_conn()
        fc = _FakeConnector(conn)
        keep = _seed_tx(conn, transaction_date=date(2026, 5, 1), asset_id="CN_FUND_999999",
                        transaction_type="other", quantity=1.0, source_system=_CN_SRC,
                        memo="某种未知类型", amount_gross=100.0)
        # Unrelated incoming CN fund row triggers the self-heal pass
        new_tx = _make_tx_df([
            {"transaction_date": date(2026, 6, 1), "asset_id": "CN_FUND_900001",
             "transaction_type": "buy", "quantity": 1.0, "amount_gross": 50.0,
             "currency": "CNY", "source_system": _CN_SRC},
        ])
        _replace_transactions(fc, new_tx)
        still_there = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE id=?", [keep]
        ).fetchone()[0]
        assert still_there == 1, "genuine 'other' row must not be purged"
