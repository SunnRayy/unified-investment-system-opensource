"""Tests for integrity check #16: unmatched_security_transfer (advisory).

Derived from the position_lots ACAT double-count bug (VOO/IEF/SGOV over-counted
lots). A transfer_in/transfer_out leg with no same-asset counterpart within a
7-day window is surfaced so a human can confirm the gap is a source-lag artifact
(self-heals next sync) rather than a genuine data problem.
"""
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.validation.data_integrity_gate import (
    BLOCKING_CHECKS,
    INTEGRITY_CHECKS,
    _check_unmatched_security_transfer,
)


def _make_db() -> DatabaseConnector:
    db = DatabaseConnector(":memory:")
    initialize_schema(db)
    return db


def _insert_tx(db, tx_date, asset_id, tx_type, quantity, amount_net=0.0, source="Schwab_CSV", provisional=False):
    db.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, transaction_type, quantity, amount_net,
             currency, source_system, is_provisional)
        VALUES (?, ?, ?, ?, ?, 'USD', ?, ?)
        """,
        [tx_date, asset_id, tx_type, quantity, amount_net, source, provisional],
    )


def test_matched_pair_passes():
    """Real Jun-8/Jun-9 shape: transfer_in +200 then transfer_out -200 next day — passes."""
    db = _make_db()
    _insert_tx(db, "2026-06-08", "US_STK_VOO", "transfer_in", 200, source="IBKR_Flex")
    _insert_tx(db, "2026-06-09", "US_STK_VOO", "transfer_out", -200, source="Schwab_CSV")

    result = _check_unmatched_security_transfer(db)
    assert result.passed, f"Matched pair should pass: {result.details}"
    db.close()


def test_orphan_transfer_out_fails():
    """A transfer_out with no matching transfer_in within 7 days → violation."""
    db = _make_db()
    _insert_tx(db, "2026-06-09", "US_STK_IEF", "transfer_out", -172, source="Schwab_CSV")

    result = _check_unmatched_security_transfer(db)
    assert not result.passed, "Orphan transfer_out should fail"
    assert "US_STK_IEF" in result.details
    db.close()


def test_orphan_transfer_in_fails():
    """A transfer_in with no matching transfer_out within 7 days → violation."""
    db = _make_db()
    _insert_tx(db, "2026-06-08", "US_STK_SGOV", "transfer_in", 553.07, source="IBKR_Flex")

    result = _check_unmatched_security_transfer(db)
    assert not result.passed, "Orphan transfer_in should fail"
    assert "US_STK_SGOV" in result.details
    db.close()


def test_qty_bearing_non_zero_amount_transfer_out_ignored():
    """transfer_out with a real (non-trivial) amount_net is out of scope — must not
    be flagged even though it has no counterpart (it's a different economic event,
    e.g. a mislabeled cash transfer, not an ACAT security transfer)."""
    db = _make_db()
    _insert_tx(db, "2026-06-09", "US_STK_AAPL", "transfer_out", -10, amount_net=1500.00, source="Schwab_CSV")

    result = _check_unmatched_security_transfer(db)
    assert result.passed, f"Non-trivial-amount transfer_out must be out of scope: {result.details}"
    db.close()


def test_provisional_rows_excluded():
    """Provisional (not-yet-confirmed) transfer legs must not trigger the check."""
    db = _make_db()
    _insert_tx(db, "2026-06-09", "US_STK_QQQ", "transfer_out", -50, source="Schwab_CSV", provisional=True)

    result = _check_unmatched_security_transfer(db)
    assert result.passed, f"Provisional-only rows should not be flagged: {result.details}"
    db.close()


def test_empty_table_passes():
    db = _make_db()
    result = _check_unmatched_security_transfer(db)
    assert result.passed
    db.close()


def test_registered_in_checks_but_not_blocking():
    names = [name for name, _ in INTEGRITY_CHECKS]
    assert "unmatched_security_transfer" in names
    assert "unmatched_security_transfer" not in BLOCKING_CHECKS
