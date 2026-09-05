"""Tests for co-authority tombstone shadow logic (C3.2).

Covers the ACAT-transfer gap: when an asset moves from Schwab to IBKR via ACAT,
Schwab simply omits it from the next CSV — there is no sell transaction.
`_shadow_coauthority_tombstone` detects this pattern and prunes the stale Schwab row.
"""

import pytest
from datetime import date

pytestmark = pytest.mark.pipeline

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.sync.phases._shadow import _shadow_coauthority_tombstone

AS_OF = date(2026, 6, 16)


@pytest.fixture
def connector():
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    yield conn
    conn.close()


def _insert_holding(connector, *, snapshot_date, asset_id, asset_name, asset_type="ETF",
                    quantity=10.0, unit="share", cost_price_unit=100.0,
                    market_price_unit=110.0, market_value=1100.0,
                    currency="USD", account="Test", source_system, is_shadow=False):
    """Helper to insert a single holding row."""
    connector.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit, market_value,
            currency, account, source_system, is_shadow
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit, market_value,
            currency, account, source_system, is_shadow,
        ),
    )


# ---------------------------------------------------------------------------
# Test 1: Full-transfer prunes Schwab + writes tombstone
# ---------------------------------------------------------------------------

def test_full_transfer_prunes_schwab_and_writes_tombstone(connector):
    """Schwab US_ETF_VOO absent from latest Schwab file (2026-06-14) but present in older snapshot
    (2026-05-23). IBKR VOO still active. Phase should:
    - Shadow the stale 05-23 Schwab VOO row
    - Write a Schwab VOO tombstone at 2026-06-16 (as_of_date)
    - Leave the IBKR VOO row untouched (is_shadow=FALSE)
    - Leave Schwab BRKB at 06-14 untouched
    """
    # Schwab: older VOO (pre-transfer) + current BRKB (still at Schwab)
    _insert_holding(
        connector,
        snapshot_date="2026-05-23",
        asset_id="US_ETF_VOO",
        asset_name="Vanguard S&P 500 ETF",
        source_system="Schwab_CSV",
    )
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="US_STK_BRKB",
        asset_name="Berkshire Hathaway B",
        asset_type="Stock",
        source_system="Schwab_CSV",
    )

    # IBKR: VOO now lives here
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="US_ETF_VOO",
        asset_name="Vanguard S&P 500 ETF",
        source_system="Broker_IBKR",
    )

    result = _shadow_coauthority_tombstone(connector, as_of_date=AS_OF)

    # Should have shadowed exactly 1 stale Schwab VOO row
    assert result == 1, f"Expected 1 stale row shadowed, got {result}"

    # The 05-23 Schwab VOO row must be shadowed
    schwab_voo_old = connector.execute(
        "SELECT is_shadow FROM holdings WHERE asset_id='US_ETF_VOO' AND source_system='Schwab_CSV' AND snapshot_date='2026-05-23'"
    ).fetchone()
    assert schwab_voo_old is not None, "Old Schwab VOO row should exist"
    assert schwab_voo_old[0] is True, "Old Schwab VOO row should be is_shadow=TRUE"

    # A tombstone row must exist at as_of_date with quantity=0 and is_shadow=TRUE
    tombstone = connector.execute(
        """
        SELECT quantity, is_shadow, price_source
        FROM holdings
        WHERE asset_id='US_ETF_VOO' AND source_system='Schwab_CSV' AND snapshot_date=?
        """,
        (AS_OF,),
    ).fetchone()
    assert tombstone is not None, "Tombstone row should have been written for Schwab VOO"
    qty, is_shadow, price_source = tombstone
    assert qty == 0, f"Tombstone quantity should be 0, got {qty}"
    assert is_shadow is True, "Tombstone should be is_shadow=TRUE"
    assert price_source == "coauthority_tombstone", f"price_source should be 'coauthority_tombstone', got {price_source}"

    # IBKR VOO must remain untouched (is_shadow=FALSE)
    ibkr_voo = connector.execute(
        "SELECT is_shadow FROM holdings WHERE asset_id='US_ETF_VOO' AND source_system='Broker_IBKR'"
    ).fetchone()
    assert ibkr_voo is not None, "IBKR VOO row should exist"
    assert ibkr_voo[0] is False, "IBKR VOO should remain is_shadow=FALSE"

    # Schwab BRKB at 06-14 must remain untouched (it is in the current file)
    schwab_brkb = connector.execute(
        "SELECT is_shadow FROM holdings WHERE asset_id='US_STK_BRKB' AND source_system='Schwab_CSV'"
    ).fetchone()
    assert schwab_brkb is not None, "Schwab BRKB row should exist"
    assert schwab_brkb[0] is False, "Schwab BRKB should remain is_shadow=FALSE"


# ---------------------------------------------------------------------------
# Test 2: Present-in-both sources (SGOV) — NOT dropped
# ---------------------------------------------------------------------------

def test_present_in_both_sources_not_tombstoned(connector):
    """US_ETF_SGOV active in Schwab at BOTH 2026-05-23 AND 2026-06-14 (the latest).
    The 06-14 row is in current_assets, so the 05-23 row is NOT a tombstone candidate
    for this phase (it's an older snapshot, but the asset is still active in the latest file).
    Assert: no tombstone written; 06-14 row stays active.
    """
    _insert_holding(
        connector,
        snapshot_date="2026-05-23",
        asset_id="US_ETF_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        source_system="Schwab_CSV",
    )
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="US_ETF_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        source_system="Schwab_CSV",
    )

    result = _shadow_coauthority_tombstone(connector, as_of_date=AS_OF)

    # Phase should find no dropped candidates for SGOV
    assert result == 0, f"Expected 0 rows shadowed, got {result}"

    # The 06-14 Schwab SGOV row must remain active
    sgov_latest = connector.execute(
        "SELECT is_shadow FROM holdings WHERE asset_id='US_ETF_SGOV' AND source_system='Schwab_CSV' AND snapshot_date='2026-06-14'"
    ).fetchone()
    assert sgov_latest is not None
    assert sgov_latest[0] is False, "Latest Schwab SGOV row should remain is_shadow=FALSE"

    # No tombstone row should exist for SGOV
    tombstone_count = connector.execute(
        "SELECT COUNT(*) FROM holdings WHERE asset_id='US_ETF_SGOV' AND source_system='Schwab_CSV' AND snapshot_date=?",
        (AS_OF,),
    ).fetchone()[0]
    assert tombstone_count == 0, f"No tombstone should have been written for SGOV, got {tombstone_count}"


# ---------------------------------------------------------------------------
# Test 3: Single-authority source (CN fund) not tombstoned
# ---------------------------------------------------------------------------

def test_single_authority_source_not_tombstoned(connector):
    """CN_Fund_Excel is a single-authority source (not in the co-authority broker set).
    A CN fund dropping between snapshots must NOT be tombstoned by this phase.
    """
    # CN fund present in old snapshot, absent from latest
    _insert_holding(
        connector,
        snapshot_date="2026-05-01",
        asset_id="CN_FUND_X",
        asset_name="Test CN Fund",
        asset_type="Fund",
        currency="CNY",
        account="CN Fund",
        source_system="CN_Fund_Excel",
    )
    # Latest CN_Fund_Excel snapshot (has a different fund, not CN_FUND_X)
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="CN_FUND_Y",
        asset_name="Another CN Fund",
        asset_type="Fund",
        currency="CNY",
        account="CN Fund",
        source_system="CN_Fund_Excel",
    )

    result = _shadow_coauthority_tombstone(connector, as_of_date=AS_OF)

    # Phase should NOT tombstone CN fund assets (CN_Fund_Excel not in co-authority broker set)
    assert result == 0, f"Expected 0 rows shadowed for single-authority CN fund source, got {result}"

    # The old CN_FUND_X row must remain untouched
    cn_fund_row = connector.execute(
        "SELECT is_shadow FROM holdings WHERE asset_id='CN_FUND_X' AND source_system='CN_Fund_Excel'"
    ).fetchone()
    assert cn_fund_row is not None
    assert cn_fund_row[0] is False, "CN_FUND_X should remain is_shadow=FALSE (not a co-authority broker source)"

    # No tombstone should exist
    tombstone_count = connector.execute(
        "SELECT COUNT(*) FROM holdings WHERE asset_id='CN_FUND_X' AND price_source='coauthority_tombstone'"
    ).fetchone()[0]
    assert tombstone_count == 0, "No tombstone should have been written for CN fund"


# ---------------------------------------------------------------------------
# Test 4: Idempotency — running twice does not create duplicate tombstone
# ---------------------------------------------------------------------------

def test_idempotency_no_duplicate_tombstone(connector):
    """Running the phase twice must not create a second tombstone row.
    The NOT EXISTS guard in the INSERT ensures idempotency.
    """
    # Same setup as Test 1: Schwab VOO dropped from latest file
    _insert_holding(
        connector,
        snapshot_date="2026-05-23",
        asset_id="US_ETF_VOO",
        asset_name="Vanguard S&P 500 ETF",
        source_system="Schwab_CSV",
    )
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="US_STK_BRKB",
        asset_name="Berkshire Hathaway B",
        asset_type="Stock",
        source_system="Schwab_CSV",
    )
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="US_ETF_VOO",
        asset_name="Vanguard S&P 500 ETF",
        source_system="Broker_IBKR",
    )

    # First run
    result1 = _shadow_coauthority_tombstone(connector, as_of_date=AS_OF)
    assert result1 == 1, f"First run should shadow 1 row, got {result1}"

    # Second run — old row is already shadowed, tombstone already exists
    result2 = _shadow_coauthority_tombstone(connector, as_of_date=AS_OF)
    assert result2 == 0, f"Second run should shadow 0 rows (already done), got {result2}"

    # Exactly 1 tombstone row must exist (not duplicated)
    tombstone_count = connector.execute(
        """
        SELECT COUNT(*) FROM holdings
        WHERE asset_id='US_ETF_VOO' AND source_system='Schwab_CSV' AND snapshot_date=?
        """,
        (AS_OF,),
    ).fetchone()[0]
    assert tombstone_count == 1, f"Exactly 1 tombstone row should exist, got {tombstone_count}"
