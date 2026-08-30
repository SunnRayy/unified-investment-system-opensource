"""Attribution & Flows WS-3.1 (V79) — Schwab 'Security Transfer' re-import
non-duplication through _replace_transactions.

Split out of test_replace_transactions_full_replace.py (file-size guard) —
shares its fixture helpers (schema bootstrap, _FakeConnector, tx-df builder).

Once a Schwab 'Security Transfer' row is healed to transfer_out/transfer_in
(by the V79 migration UPDATE, or natively by the reader hook's pseudo-type
resolution on any subsequent sync), a later Schwab sync — which now ALSO
produces transfer_out/transfer_in for this action via the same resolution —
must supersede the existing row through the normal incremental
delete-then-insert natural key (transaction_date, asset_id, transaction_type,
amount_gross, source_system), not duplicate it. This is exactly why V79 ships
the action_map seed and the heal UPDATE in the SAME migration (see
src/database/connector.py's V79 comment and the mapping_seeds.py
'Security Transfer' comment) — unlike the CN Fund self-heal case
(test_replace_transactions_full_replace.py::TestCNFundRetypeSelfHeal), no
special purge step is needed here because BOTH sides of the natural key
already agree on 'transfer_out'/'transfer_in' post-heal.
"""
from datetime import date
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd
import pytest

pytestmark = pytest.mark.pipeline

from src.sync.phases._ingest import _replace_transactions

_SCHWAB_SRC = "Schwab_CSV"

_TX_COLS = [
    "transaction_date", "asset_id", "asset_name", "transaction_type",
    "quantity", "price_unit", "amount_gross", "amount_net", "commission_fee",
    "currency", "account", "memo", "source_system",
]


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


class TestSchwabSecurityTransferPostHealReimportNoDup:
    def test_healed_transfer_out_row_reimport_no_duplicate(self):
        conn = _new_conn()
        fc = _FakeConnector(conn)

        d = date(2026, 6, 9)
        asset = "US_STK_VOO"
        # Simulates the post-V79-heal state: previously 'other', now 'transfer_out'.
        _seed_tx(conn, transaction_date=d, asset_id=asset, transaction_type="transfer_out",
                 quantity=-21.0, source_system=_SCHWAB_SRC, amount_gross=0.0)
        assert _count(conn, _SCHWAB_SRC) == 1

        # A subsequent Schwab sync re-derives the SAME row — the hook now also
        # resolves 'Security Transfer' to transfer_out (quantity < 0).
        new_tx = _make_tx_df([
            {"transaction_date": d, "asset_id": asset, "transaction_type": "transfer_out",
             "quantity": -21.0, "amount_gross": 0.0, "amount_net": 0.0,
             "source_system": _SCHWAB_SRC},
        ])
        count = _replace_transactions(fc, new_tx)
        assert count == 1

        rows = conn.execute(
            "SELECT transaction_type, quantity FROM transactions WHERE source_system=? AND asset_id=?",
            [_SCHWAB_SRC, asset],
        ).fetchall()
        assert len(rows) == 1, f"expected single row (no duplicate), got {rows}"
        assert rows[0][0] == "transfer_out"
        assert float(rows[0][1]) == pytest.approx(-21.0)

    def test_healed_transfer_in_row_reimport_no_duplicate(self):
        """Symmetric case: positive-quantity ACAT-in leg."""
        conn = _new_conn()
        fc = _FakeConnector(conn)

        d = date(2026, 6, 9)
        asset = "US_STK_AAPL"
        _seed_tx(conn, transaction_date=d, asset_id=asset, transaction_type="transfer_in",
                 quantity=10.0, source_system=_SCHWAB_SRC, amount_gross=0.0)
        assert _count(conn, _SCHWAB_SRC) == 1

        new_tx = _make_tx_df([
            {"transaction_date": d, "asset_id": asset, "transaction_type": "transfer_in",
             "quantity": 10.0, "amount_gross": 0.0, "amount_net": 0.0,
             "source_system": _SCHWAB_SRC},
        ])
        count = _replace_transactions(fc, new_tx)
        assert count == 1

        rows = conn.execute(
            "SELECT transaction_type, quantity FROM transactions WHERE source_system=? AND asset_id=?",
            [_SCHWAB_SRC, asset],
        ).fetchall()
        assert len(rows) == 1, f"expected single row (no duplicate), got {rows}"
        assert rows[0][0] == "transfer_in"
        assert float(rows[0][1]) == pytest.approx(10.0)
