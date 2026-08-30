"""Tests for co-authority merged-ledger logic in select_transaction_sources (C3.3 RISK-1).

Co-authority assets (US_STK_*/US_ETF_*/CASH_USD → Schwab_CSV + Broker_IBKR) must return
ALL authority sources that have transactions — NOT just the latest-holding source. Without
this, a Schwab→IBKR ACAT transfer drops Schwab buy lots → IBKR cost basis $0.
"""
import duckdb
import pytest

pytestmark = pytest.mark.pipeline

from src.services.transaction_source_selector import select_transaction_sources


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn():
    """Create a raw DuckDB in-memory connection with the minimal tables needed."""
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            source_system VARCHAR,
            is_shadow BOOLEAN DEFAULT FALSE,
            quantity DOUBLE DEFAULT 1.0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            transaction_date DATE,
            asset_id VARCHAR,
            source_system VARCHAR,
            transaction_type VARCHAR,
            quantity DOUBLE,
            price_unit DOUBLE,
            amount_net DOUBLE,
            currency VARCHAR DEFAULT 'USD',
            is_provisional BOOLEAN DEFAULT FALSE
        )
        """
    )
    return conn


class _FakeDB:
    """Thin wrapper so select_transaction_sources can call db.execute()."""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=None):
        if params:
            return self._conn.execute(query, params)
        return self._conn.execute(query)


# ---------------------------------------------------------------------------
# Test 1: co-authority merged — IBKR surviving, Schwab tx present → returns both
# ---------------------------------------------------------------------------

def test_coauthority_merged_returns_both_sources():
    """US_STK_VOO transferred Schwab→IBKR via ACAT.

    State after transfer:
    - Schwab row is_shadow=TRUE (tombstone/stale — pruned in C3.2)
    - IBKR row is_shadow=FALSE (active)
    - Transactions: Schwab has the buy; IBKR has the transfer_in

    select_transaction_sources must return ['Broker_IBKR', 'Schwab_CSV'] (sorted) so the
    FIFO calculator sees BOTH brokers' ledger and reconstructs the correct cost basis.
    """
    conn = _make_conn()

    # IBKR is the only active (non-shadow) holding
    conn.execute(
        "INSERT INTO holdings VALUES ('2026-06-14', 'US_STK_VOO', 'Broker_IBKR', FALSE, 21.0)"
    )
    # Schwab row is shadowed (post-tombstone / ACAT)
    conn.execute(
        "INSERT INTO holdings VALUES ('2026-05-23', 'US_STK_VOO', 'Schwab_CSV', TRUE, 27.0)"
    )

    # Schwab buy — the original purchase lots
    conn.execute(
        """
        INSERT INTO transactions VALUES
        ('2024-01-15', 'US_STK_VOO', 'Schwab_CSV', 'buy', 27.0, 399.77, -10793.79, 'USD', FALSE)
        """
    )
    # IBKR transfer_in — the ACAT receipt (non-realizing)
    conn.execute(
        """
        INSERT INTO transactions VALUES
        ('2026-05-24', 'US_STK_VOO', 'Broker_IBKR', 'transfer_in', 21.0, 0.0, 0.0, 'USD', FALSE)
        """
    )

    db = _FakeDB(conn)
    result = select_transaction_sources(db, "US_STK_VOO")

    assert result == ["Broker_IBKR", "Schwab_CSV"], (
        f"Co-authority VOO must return both broker sources for merged FIFO, got {result}. "
        "IBKR-only would drop Schwab lots and compute cost=$0."
    )
    assert "Schwab_CSV" in result, "Schwab_CSV must be included even though its holding is shadowed"
    assert "Broker_IBKR" in result, "Broker_IBKR must be included (it holds the surviving position)"


# ---------------------------------------------------------------------------
# Test 2: single-authority CN fund — unchanged behavior
# ---------------------------------------------------------------------------

def test_single_authority_cn_fund_returns_own_source():
    """CN_FUND_900015 is CN_Fund_Excel only (single-authority). Must still return that source."""
    conn = _make_conn()

    conn.execute(
        "INSERT INTO holdings VALUES ('2026-06-14', 'CN_FUND_900015', 'CN_Fund_Excel', FALSE, 1000.0)"
    )
    conn.execute(
        """
        INSERT INTO transactions VALUES
        ('2025-03-01', 'CN_FUND_900015', 'CN_Fund_Excel', 'buy', 1000.0, 8.0, -8000.0, 'CNY', FALSE)
        """
    )

    db = _FakeDB(conn)
    result = select_transaction_sources(db, "CN_FUND_900015")

    assert result == ["CN_Fund_Excel"], (
        f"Single-authority CN fund must return only CN_Fund_Excel, got {result}"
    )


# ---------------------------------------------------------------------------
# Test 3: co-authority with only one broker's tx → returns that one source
# ---------------------------------------------------------------------------

def test_coauthority_only_schwab_tx_returns_schwab():
    """US_STK_BRKB has co-authority rule but only Schwab has transactions (not transferred yet).
    Should return ['Schwab_CSV'] — the intersection of rule_authorities and tx_sources.
    """
    conn = _make_conn()

    conn.execute(
        "INSERT INTO holdings VALUES ('2026-06-14', 'US_STK_BRKB', 'Schwab_CSV', FALSE, 100.0)"
    )
    conn.execute(
        """
        INSERT INTO transactions VALUES
        ('2025-01-01', 'US_STK_BRKB', 'Schwab_CSV', 'buy', 100.0, 350.0, -35000.0, 'USD', FALSE)
        """
    )

    db = _FakeDB(conn)
    result = select_transaction_sources(db, "US_STK_BRKB")

    assert result == ["Schwab_CSV"], (
        f"Co-authority asset with only Schwab tx must return ['Schwab_CSV'], got {result}"
    )


# ---------------------------------------------------------------------------
# Test 4: co-authority asset with Schwab + PIS tx → PIS excluded
# ---------------------------------------------------------------------------

def test_coauthority_excludes_legacy_pis_source():
    """US_STK_AAPL has Schwab_CSV + PIS (legacy) transactions.
    PIS is in LEGACY_TRANSACTION_SOURCES; co-authority branch returns only the
    authority sources (Schwab_CSV, Broker_IBKR) present in tx_sources — PIS is not
    a declared authority and must not appear.
    """
    conn = _make_conn()

    conn.execute(
        "INSERT INTO holdings VALUES ('2026-06-14', 'US_STK_AAPL', 'Schwab_CSV', FALSE, 50.0)"
    )
    conn.execute(
        """
        INSERT INTO transactions VALUES
        ('2024-06-01', 'US_STK_AAPL', 'Schwab_CSV', 'buy', 50.0, 180.0, -9000.0, 'USD', FALSE),
        ('2023-01-01', 'US_STK_AAPL', 'PIS', 'buy', 50.0, 150.0, -7500.0, 'USD', FALSE)
        """
    )

    db = _FakeDB(conn)
    result = select_transaction_sources(db, "US_STK_AAPL")

    assert "PIS" not in result, (
        f"PIS must be excluded from co-authority asset result, got {result}"
    )
    assert "Schwab_CSV" in result, f"Schwab_CSV must be included, got {result}"
    # Broker_IBKR has no transactions, so it won't appear — intersection with tx_sources
    assert result == ["Schwab_CSV"], (
        f"Only Schwab_CSV has co-authority tx (PIS excluded), got {result}"
    )
