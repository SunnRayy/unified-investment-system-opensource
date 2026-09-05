"""Tests for integrity check #6 (shadow_mutual_exclusion) — C3.2 exemptions.

Verifies the two exemptions added in C3.2:
  (a) Zero-qty co-authority tombstone rows (is_shadow=TRUE, quantity=0) → check PASSES.
  (b) Qty-bearing broker row superseded by a Consolidated row → check PASSES.
  (c) Qty-bearing reader row shadowed without supersession → check FAILS (original Gold/Insurance
      protection preserved).
  (d) Fully-shadowed Gold reader row (qty>0, no Consolidated) → check FAILS.

Style mirrors test_integrity_check_19.py and test_integrity_gate_blocking.py.
"""
import pytest
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.validation.data_integrity_gate import _check_shadow_mutual_exclusion


@pytest.fixture
def conn():
    """In-memory DB with full schema for check #6."""
    db = DatabaseConnector(":memory:")
    initialize_schema(db)
    yield db
    db.close()


def _insert_holding(db, *, snapshot_date, asset_id, asset_name="Test Asset",
                    asset_type="ETF", quantity=10.0, unit="share",
                    cost_price_unit=100.0, market_price_unit=110.0,
                    market_value=1100.0, currency="USD", account="Test",
                    source_system, is_shadow=False, price_source=None):
    db.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit, market_value,
            currency, account, source_system, is_shadow, price_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit, market_value,
            currency, account, source_system, is_shadow, price_source,
        ),
    )


# ---------------------------------------------------------------------------
# Test 1: Zero-qty tombstone is_shadow=TRUE → check PASSES (exemption a)
# ---------------------------------------------------------------------------

def test_zero_qty_coauthority_tombstone_passes(conn):
    """A zero-qty Schwab tombstone at the latest source date marked is_shadow=TRUE
    should be EXEMPT from check #6. The check must PASS.

    This is the C3.2 tombstone written by `_shadow_coauthority_tombstone` when an asset
    transfers from Schwab to IBKR via ACAT.
    """
    # The tombstone itself: qty=0, is_shadow=TRUE at latest Schwab date
    _insert_holding(
        conn,
        snapshot_date="2026-06-16",
        asset_id="US_ETF_VOO",
        asset_name="Vanguard S&P 500 ETF",
        quantity=0,        # zero-qty tombstone
        market_value=0,
        source_system="Schwab_CSV",
        is_shadow=True,
        price_source="coauthority_tombstone",
    )

    result = _check_shadow_mutual_exclusion(conn)
    assert result.passed, (
        f"Check #6 should PASS for zero-qty tombstone (exempt). Got: {result.details}"
    )


# ---------------------------------------------------------------------------
# Test 2: Qty-bearing reader row is_shadow=TRUE, no Consolidated → check FAILS
# ---------------------------------------------------------------------------

def test_qty_bearing_reader_shadowed_no_consolidated_fails(conn):
    """A qty-bearing Schwab row at the latest source date marked is_shadow=TRUE,
    with NO Consolidated supersession, is a real pipeline bug. Check must FAIL.

    This preserves the original protection: broken shadow logic must be caught.
    """
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="US_ETF_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=100.0,    # qty-bearing — NOT exempt
        source_system="Schwab_CSV",
        is_shadow=True,    # incorrectly shadowed
    )

    result = _check_shadow_mutual_exclusion(conn)
    assert not result.passed, (
        "Check #6 should FAIL for qty-bearing reader row shadowed without Consolidated supersession. "
        f"Got: {result.details}"
    )
    assert "Schwab_CSV" in result.details or "reader rows" in result.details.lower() or "qty-bearing" in result.details.lower()


# ---------------------------------------------------------------------------
# Test 3: Qty-bearing Schwab row is_shadow=TRUE + Consolidated row is_shadow=FALSE → PASSES (exemption b)
# ---------------------------------------------------------------------------

def test_consolidated_supersession_passes(conn):
    """A qty-bearing Schwab US_ETF_SGOV row is_shadow=TRUE because a Consolidated row
    supersedes it (C3.4 pattern, dormant now but tested here).
    Check #6 should PASS — exemption (b).
    """
    # Schwab SGOV: qty-bearing, shadowed — but a Consolidated row supersedes it
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="US_ETF_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=100.0,
        source_system="Schwab_CSV",
        is_shadow=True,    # shadowed by Consolidated
    )
    # Consolidated SGOV: active (is_shadow=FALSE) — this is the superseding row
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="US_ETF_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=150.0,    # merged qty (Schwab + IBKR)
        source_system="Consolidated",
        is_shadow=False,   # active
    )

    result = _check_shadow_mutual_exclusion(conn)
    assert result.passed, (
        "Check #6 should PASS when a Consolidated row supersedes the shadowed reader row. "
        f"Got: {result.details}"
    )


# ---------------------------------------------------------------------------
# Test 4: Fully-shadowed Gold reader row (qty>0, no Consolidated) → check FAILS
# ---------------------------------------------------------------------------

def test_gold_insurance_fully_shadowed_fails(conn):
    """Gold_Excel row with qty>0 and is_shadow=TRUE, no Consolidated supersession.
    This is the original protection: Gold/Insurance rows should never be fully shadowed
    unless a Consolidated row exists. Check must FAIL.
    """
    _insert_holding(
        conn,
        snapshot_date="2026-06-10",
        asset_id="ALTS_Paper_Gold",
        asset_name="Paper Gold",
        asset_type="Gold",
        quantity=50.0,     # qty-bearing Gold row
        currency="CNY",
        account="Gold",
        source_system="Gold_Excel",
        is_shadow=True,    # should NOT be shadowed without Consolidated
    )

    result = _check_shadow_mutual_exclusion(conn)
    assert not result.passed, (
        "Check #6 should FAIL for a qty-bearing Gold_Excel row shadowed without Consolidated. "
        f"Got: {result.details}"
    )


# ---------------------------------------------------------------------------
# Test 5 (FIX 1 regression): zero-qty tombstone at a later date must NOT hide
# a real mis-shadow at the source's actual file date.
# ---------------------------------------------------------------------------

def test_tombstone_does_not_hide_real_misshadow(conn):
    """FIX 1 regression: a zero-qty tombstone at 2026-06-16 must NOT shift the
    latest_source_sync window so that a genuinely mis-shadowed qty-bearing row at
    2026-06-14 escapes detection.

    Setup:
      (a) Schwab US_ETF_SGOV at 2026-06-14: qty=100, is_shadow=TRUE, NO Consolidated row
          → this is a real pipeline bug (check #6 should catch it).
      (b) Schwab US_ETF_VOO at 2026-06-16: qty=0, is_shadow=TRUE, price_source=coauthority_tombstone
          → this is a legitimate tombstone written by C3.2.

    Without FIX 1, the CTE would compute latest_date='2026-06-16' for Schwab_CSV
    (because the tombstone row is at 06-16), so the JOIN only inspects 06-16 rows.
    The mis-shadowed SGOV row at 06-14 is never seen → check wrongly PASSES.

    With FIX 1, the CTE filters out qty=0 rows, so latest_date='2026-06-14', and
    the SGOV mis-shadow at 06-14 IS caught → check correctly FAILS.
    """
    # (a) Genuine mis-shadow: qty-bearing Schwab SGOV at real file date 2026-06-14
    _insert_holding(
        conn,
        snapshot_date="2026-06-14",
        asset_id="US_ETF_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=100.0,        # qty-bearing — should never be is_shadow=TRUE without Consolidated
        source_system="Schwab_CSV",
        is_shadow=True,        # the real bug we want to detect
    )
    # (b) Legitimate C3.2 tombstone: zero-qty Schwab VOO at a LATER date 2026-06-16
    _insert_holding(
        conn,
        snapshot_date="2026-06-16",
        asset_id="US_ETF_VOO",
        asset_name="Vanguard S&P 500 ETF",
        quantity=0,            # zero-qty tombstone — exempt from check #6
        market_value=0,
        source_system="Schwab_CSV",
        is_shadow=True,
        price_source="coauthority_tombstone",
    )

    result = _check_shadow_mutual_exclusion(conn)
    # The check must FAIL: the SGOV mis-shadow at 06-14 must be detected despite the
    # 06-16 tombstone. Without FIX 1 the latest_source_sync CTE would see 06-16 as the
    # window date and miss the 06-14 SGOV row entirely.
    assert not result.passed, (
        "Check #6 must FAIL: the qty-bearing SGOV mis-shadow at 2026-06-14 must still be "
        "detected even though a zero-qty tombstone at 2026-06-16 exists for the same source. "
        f"Got: {result.details}"
    )
    assert "Schwab_CSV" in result.details or "qty-bearing" in result.details.lower() or "reader rows" in result.details.lower()
