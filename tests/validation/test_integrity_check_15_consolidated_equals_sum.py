"""Tests for integrity check #15 (consolidated_equals_sum — C3.4).

Verifies:
  (a) Passes trivially when 0 Consolidated rows exist.
  (b) Passes when a Consolidated row's market_value and quantity match the sum of the
      contributing co-authority broker rows' latest values (within tolerance).
  (c) Passes for a CASH_ Consolidated row where only mv is checked (qty=1 sentinel is exempt).
  (d) Fails when a Consolidated row's market_value diverges from the broker sum.
  (e) Fails when a non-cash Consolidated row's quantity diverges from the broker sum.

Style mirrors tests/validation/test_integrity_check_6_coauthority.py.
"""
import pytest
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.validation.data_integrity_gate import _check_consolidated_equals_sum


@pytest.fixture
def conn():
    """In-memory DB with full schema for check #15."""
    db = DatabaseConnector(":memory:")
    initialize_schema(db)
    yield db
    db.close()


def _insert_holding(db, *, snapshot_date, asset_id, asset_name="Test Asset",
                    asset_type="ETF", quantity=10.0, unit="share",
                    cost_price_unit=100.0, market_price_unit=110.0,
                    market_value=1100.0, currency="USD", account="Test",
                    source_system, is_shadow=False, authority_source=None,
                    price_source=None):
    db.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit, market_value,
            currency, account, source_system, is_shadow, authority_source, price_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit, market_value,
            currency, account, source_system, is_shadow,
            authority_source or source_system, price_source,
        ),
    )


# ---------------------------------------------------------------------------
# Test (a): No Consolidated rows → trivially PASSES
# ---------------------------------------------------------------------------

def test_passes_trivially_with_no_consolidated_rows(conn):
    """Check #15 must PASS when no Consolidated rows exist (pre-C3.4 DB or no co-authority assets)."""
    # Active Schwab and IBKR broker rows — no Consolidated row
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="US_STK_SGOV",
        source_system="Schwab_CSV",
        quantity=453.122,
        market_value=45312.2,
    )
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="US_STK_SGOV",
        source_system="Broker_IBKR",
        quantity=200.0,
        market_value=20000.0,
    )

    result = _check_consolidated_equals_sum(conn)
    assert result.passed, (
        f"Check #15 should PASS trivially when 0 Consolidated rows exist. Got: {result.details}"
    )


# ---------------------------------------------------------------------------
# Test (b): Valid Consolidated row (securities) — PASSES
# ---------------------------------------------------------------------------

def test_valid_consolidated_security_passes(conn):
    """A Consolidated SGOV row whose qty=Σqty and mv=Σmv (within tolerance) → PASSES."""
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="US_STK_SGOV",
        source_system="Schwab_CSV",
        quantity=453.122,
        market_value=45312.2,
        is_shadow=True,
    )
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="US_STK_SGOV",
        source_system="Broker_IBKR",
        quantity=200.0,
        market_value=20000.0,
        is_shadow=True,
    )
    _insert_holding(
        conn,
        snapshot_date="2026-06-16",  # as_of_date (>= broker dates)
        asset_id="US_STK_SGOV",
        source_system="Consolidated",
        authority_source="Consolidated",
        account="Multi-broker",
        quantity=653.122,            # == 453.122 + 200.0
        market_value=65312.2,        # == 45312.2 + 20000.0
        is_shadow=False,
        price_source="consolidated",
    )

    result = _check_consolidated_equals_sum(conn)
    assert result.passed, (
        f"Check #15 should PASS for a valid Consolidated security row. Got: {result.details}"
    )


# ---------------------------------------------------------------------------
# Test (c): Cash Consolidated (qty sentinel, mv only checked) — PASSES
# ---------------------------------------------------------------------------

def test_valid_consolidated_cash_passes_mv_only(conn):
    """A CASH_USD Consolidated row with qty=1 (sentinel) but correct mv=Σmv → PASSES.
    The check must NOT flag qty=1 ≠ Σqty (cash qty is intentionally a sentinel, not a sum).
    """
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="CASH_USD",
        asset_type="Cash",
        source_system="Schwab_CSV",
        quantity=1.0,
        market_price_unit=5000.0,
        market_value=35000.0,
        is_shadow=True,
    )
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="CASH_USD",
        asset_type="Cash",
        source_system="Broker_IBKR",
        quantity=1.0,
        market_price_unit=1000.0,
        market_value=7000.0,
        is_shadow=True,
    )
    _insert_holding(
        conn,
        snapshot_date="2026-06-16",
        asset_id="CASH_USD",
        asset_type="Cash",
        source_system="Consolidated",
        authority_source="Consolidated",
        account="Multi-broker",
        quantity=1.0,          # sentinel — NOT sum(1.0 + 1.0 = 2.0)
        market_value=42000.0,  # == 35000.0 + 7000.0
        is_shadow=False,
        price_source="consolidated",
    )

    result = _check_consolidated_equals_sum(conn)
    assert result.passed, (
        f"Check #15 should PASS for a CASH_ Consolidated row with qty=1 sentinel. Got: {result.details}"
    )


# ---------------------------------------------------------------------------
# Test (d): Consolidated mv diverges from broker sum — FAILS
# ---------------------------------------------------------------------------

def test_mismatched_mv_consolidated_fails(conn):
    """A Consolidated row whose market_value does NOT match Σ broker mv → FAILS.
    This guards against a broker row being missed or the consolidation phase computing
    a wrong sum (would produce a corrupt net worth).
    """
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="US_STK_SGOV",
        source_system="Schwab_CSV",
        quantity=453.122,
        market_value=45312.2,
        is_shadow=True,
    )
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="US_STK_SGOV",
        source_system="Broker_IBKR",
        quantity=200.0,
        market_value=20000.0,
        is_shadow=True,
    )
    # Deliberately wrong mv (off by >1% and >1.0)
    _insert_holding(
        conn,
        snapshot_date="2026-06-16",
        asset_id="US_STK_SGOV",
        source_system="Consolidated",
        authority_source="Consolidated",
        account="Multi-broker",
        quantity=653.122,
        market_value=99999.0,   # WRONG — should be 65312.2
        is_shadow=False,
        price_source="consolidated",
    )

    result = _check_consolidated_equals_sum(conn)
    assert not result.passed, (
        "Check #15 should FAIL when Consolidated market_value diverges from broker sum. "
        f"Got: {result.details}"
    )
    assert "US_STK_SGOV" in result.details or "mismatched" in result.details.lower()


# ---------------------------------------------------------------------------
# Test (e): Consolidated qty diverges from broker sum (non-cash) — FAILS
# ---------------------------------------------------------------------------

def test_mismatched_qty_consolidated_security_fails(conn):
    """A non-cash Consolidated row whose quantity does NOT match Σ broker qty → FAILS.
    Quantity mismatch means the consolidation incorrectly included or missed a broker lot.
    """
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="US_STK_SGOV",
        source_system="Schwab_CSV",
        quantity=453.122,
        market_value=45312.2,
        is_shadow=True,
    )
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="US_STK_SGOV",
        source_system="Broker_IBKR",
        quantity=200.0,
        market_value=20000.0,
        is_shadow=True,
    )
    # Deliberately wrong qty (off by >>1%)
    _insert_holding(
        conn,
        snapshot_date="2026-06-16",
        asset_id="US_STK_SGOV",
        source_system="Consolidated",
        authority_source="Consolidated",
        account="Multi-broker",
        quantity=999.0,      # WRONG — should be 653.122
        market_value=65312.2,
        is_shadow=False,
        price_source="consolidated",
    )

    result = _check_consolidated_equals_sum(conn)
    assert not result.passed, (
        "Check #15 should FAIL when Consolidated quantity diverges from broker sum. "
        f"Got: {result.details}"
    )
    assert "US_STK_SGOV" in result.details or "mismatched" in result.details.lower()
