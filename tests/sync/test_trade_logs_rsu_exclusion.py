"""Tests for RSU exclusion from the AI-advisor decision ledger (trade_logs).

RSU vest + auto sell-to-cover are non-discretionary events and must never
be imported into trade_logs. These tests verify:
  - RSU transactions are not imported going forward.
  - Previously auto-imported RSU entries are removed on the next backfill.
  - Manually recorded RSU entries are preserved.
  - Non-RSU entries are not affected.
  - The backfill is idempotent.
"""

from __future__ import annotations

import pytest
from datetime import date
from pathlib import Path

import duckdb

pytestmark = pytest.mark.pipeline


def _new_conn():
    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute(
        "ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS verification_status VARCHAR(20) DEFAULT 'pending'"
    )
    return conn


def _insert_transaction(conn, asset_id: str, tx_type: str, source_system: str, tx_date: date | None = None) -> int:
    d = tx_date or date.today()
    return conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, source_system, is_provisional
        ) VALUES (?, ?, ?, ?, 10, 100.0, 1000.0, ?, FALSE)
        RETURNING id
        """,
        [d, asset_id, asset_id, tx_type, source_system],
    ).fetchone()[0]


def _insert_trade_log(conn, asset_id: str, action: str, suggestion_source: str | None, tx_date: date | None = None) -> int:
    d = tx_date or date.today()
    return conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, suggestion_source)
        VALUES (?, ?, ?, ?)
        RETURNING id
        """,
        [d, asset_id, action, suggestion_source],
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Test 1: RSU sell is NOT imported; Schwab buy IS imported
# ---------------------------------------------------------------------------
def test_rsu_sell_not_imported_schwab_buy_is_imported():
    from src.sync.trade_linker import backfill_trade_logs_from_transactions

    conn = _new_conn()
    _insert_transaction(conn, "US_STK_AAPL", "BUY", "Schwab_CSV")
    _insert_transaction(conn, "RSU_GOOG", "SELL", "RSU_Excel")  # non-discretionary — must be excluded

    summary = backfill_trade_logs_from_transactions(conn)

    imported_assets = [
        r[0]
        for r in conn.execute("SELECT asset_id FROM trade_logs ORDER BY asset_id").fetchall()
    ]

    assert "US_STK_AAPL" in imported_assets, "Schwab buy must be imported"
    assert "RSU_GOOG" not in imported_assets, "RSU sell must never be imported"
    assert summary["inserted"] == 1
    assert summary["rsu_ledger_removed"] == 0  # no pre-existing RSU log to remove


# ---------------------------------------------------------------------------
# Test 2: Existing imported RSU trade_log is removed on next backfill
# ---------------------------------------------------------------------------
def test_existing_imported_rsu_trade_log_removed():
    from src.sync.trade_linker import backfill_trade_logs_from_transactions

    conn = _new_conn()
    _insert_trade_log(conn, "RSU_GOOG", "Sell", "imported")

    # Verify the row exists before the backfill
    pre = conn.execute("SELECT id FROM trade_logs WHERE asset_id = 'RSU_GOOG'").fetchall()
    assert len(pre) == 1

    summary = backfill_trade_logs_from_transactions(conn)

    remaining = conn.execute(
        "SELECT id FROM trade_logs WHERE asset_id = 'RSU_GOOG'"
    ).fetchall()

    assert remaining == [], "Previously auto-imported RSU log must be removed"
    assert summary["rsu_ledger_removed"] == 1


# ---------------------------------------------------------------------------
# Test 3: Manual RSU trade_log is PRESERVED
# ---------------------------------------------------------------------------
def test_manual_rsu_trade_log_preserved():
    from src.sync.trade_linker import backfill_trade_logs_from_transactions

    conn = _new_conn()
    _insert_trade_log(conn, "RSU_AMZN", "Sell", "manual")

    summary = backfill_trade_logs_from_transactions(conn)

    remaining = conn.execute(
        "SELECT suggestion_source FROM trade_logs WHERE asset_id = 'RSU_AMZN'"
    ).fetchall()

    assert len(remaining) == 1, "Manual RSU log must survive cleanup"
    assert remaining[0][0] == "manual"
    assert summary["rsu_ledger_removed"] == 0


# ---------------------------------------------------------------------------
# Test 4: NULL-source RSU trade_log is removed (treated as auto-generated)
# ---------------------------------------------------------------------------
def test_null_source_rsu_trade_log_removed():
    from src.sync.trade_linker import backfill_trade_logs_from_transactions

    conn = _new_conn()
    # Explicitly insert NULL suggestion_source (can't use helper which passes the value)
    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, suggestion_source)
        VALUES (?, 'RSU_AMZN', 'Sell', NULL)
        """,
        [date.today()],
    )

    summary = backfill_trade_logs_from_transactions(conn)

    remaining = conn.execute(
        "SELECT id FROM trade_logs WHERE asset_id = 'RSU_AMZN'"
    ).fetchall()

    assert remaining == [], "NULL-source RSU log must be removed (treated as auto-generated)"
    assert summary["rsu_ledger_removed"] == 1


# ---------------------------------------------------------------------------
# Test 5: Non-RSU imported trade_log is NOT touched by the cleanup
# ---------------------------------------------------------------------------
def test_non_rsu_imported_trade_log_untouched():
    from src.sync.trade_linker import backfill_trade_logs_from_transactions

    conn = _new_conn()
    _insert_trade_log(conn, "US_STK_MSFT", "Sell", "imported")

    summary = backfill_trade_logs_from_transactions(conn)

    remaining = conn.execute(
        "SELECT suggestion_source FROM trade_logs WHERE asset_id = 'US_STK_MSFT'"
    ).fetchall()

    assert len(remaining) == 1, "Non-RSU imported log must not be deleted by RSU cleanup"
    assert remaining[0][0] == "imported"
    assert summary["rsu_ledger_removed"] == 0


# ---------------------------------------------------------------------------
# Test 6: Idempotency — second run inserts nothing new and removes nothing new
# ---------------------------------------------------------------------------
def test_backfill_is_idempotent():
    from src.sync.trade_linker import backfill_trade_logs_from_transactions

    conn = _new_conn()
    _insert_transaction(conn, "US_STK_AAPL", "BUY", "Schwab_CSV")
    _insert_trade_log(conn, "RSU_GOOG", "Sell", "imported")  # will be removed on first run

    summary1 = backfill_trade_logs_from_transactions(conn)

    # First run: RSU log removed, AAPL imported
    assert summary1["inserted"] == 1
    assert summary1["rsu_ledger_removed"] == 1

    count_after_first = conn.execute("SELECT COUNT(*) FROM trade_logs").fetchone()[0]

    summary2 = backfill_trade_logs_from_transactions(conn)

    count_after_second = conn.execute("SELECT COUNT(*) FROM trade_logs").fetchone()[0]

    assert summary2["inserted"] == 0, "Second run must not import duplicate rows"
    assert summary2["rsu_ledger_removed"] == 0, "Second run must not remove any new rows"
    assert count_after_second == count_after_first, "Trade log count must be stable across runs"
