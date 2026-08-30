"""Tests for merged-ledger FIFO cost basis backfill for co-authority assets (C3.3).

Reproduces the real VOO full-transfer scenario:
  - Schwab buys VOO, later transfers all 27 shares to IBKR via ACAT.
  - After transfer, IBKR holding has cost_price_unit=0 (transferred-in, unknown cost).
  - The merged FIFO must reconstruct cost from BOTH brokers' transaction ledger.
  - Acceptance gate: IBKR VOO cost_price_unit > 0 after backfill.
"""
import pytest

pytestmark = pytest.mark.pipeline

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.sync.phases._post_reader import _backfill_fifo_cost_basis


@pytest.fixture
def connector():
    """In-memory DB with full schema."""
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    yield conn
    conn.close()


def _insert_holding(connector, *, snapshot_date, asset_id, asset_name, asset_type="ETF",
                    quantity, unit="share", cost_price_unit=None,
                    market_price_unit=500.0, market_value=None,
                    currency="USD", account="IBKR", source_system, is_shadow=False):
    """Helper to insert a single holding row."""
    if market_value is None:
        market_value = quantity * market_price_unit * 7.1  # rough CNY
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


def _insert_tx(connector, *, transaction_date, asset_id, source_system,
               transaction_type, quantity, price_unit=0.0, amount_net=0.0,
               currency="USD"):
    """Helper to insert a transaction row."""
    connector.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name,
            transaction_type, quantity, price_unit, amount_gross, amount_net,
            commission_fee, currency, account, memo, source_system
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'Test', NULL, ?)
        """,
        (
            transaction_date, asset_id, asset_id,
            transaction_type, quantity, price_unit, abs(amount_net), amount_net,
            currency, source_system,
        ),
    )


# ---------------------------------------------------------------------------
# Test 1: Full VOO transfer Schwab→IBKR — IBKR cost must be > 0 after backfill
# ---------------------------------------------------------------------------

def test_voo_full_transfer_ibkr_cost_is_positive(connector):
    """Reproduce the live VOO scenario (verified against real data in C3 design validation).

    Transaction history (all Schwab_CSV):
      buy  27 @ ~599  (amount_net = -16173.81)
      sell  6
      other -21       (Security Transfer / ACAT, amount_net = 0)

    IBKR receives:
      transfer_in 21  (amount_net = 0)

    Holdings after ACAT:
      IBKR VOO  qty=21, cost_price_unit=0   (active, is_shadow=FALSE)
      Schwab VOO qty=27, is_shadow=TRUE       (stale/tombstone, post-C3.2)

    After backfill:
      merged FIFO = buy 27 → sell 6 (FIFO consumes 6 of lot-1) → 21 remaining
      cost remaining = (16173.81 * (27-6)/27) ≈ 12579.63 → per unit ≈ 599 USD
      IBKR VOO cost_price_unit must be in [550, 650].
    """
    # Schwab holding (stale/shadowed — C3.2 tombstone state)
    _insert_holding(
        connector,
        snapshot_date="2026-05-23",
        asset_id="US_STK_VOO",
        asset_name="Vanguard S&P 500 ETF",
        quantity=27.0,
        cost_price_unit=None,
        market_price_unit=599.0,
        market_value=27.0 * 599.0 * 7.1,
        currency="USD",
        account="Schwab",
        source_system="Schwab_CSV",
        is_shadow=True,   # already tombstoned by C3.2
    )
    # IBKR holding (active — transferred-in, cost=0)
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="US_STK_VOO",
        asset_name="Vanguard S&P 500 ETF",
        quantity=21.0,
        cost_price_unit=0.0,  # transferred-in: cost unknown at broker level
        market_price_unit=600.0,
        market_value=21.0 * 600.0 * 7.1,
        currency="USD",
        account="IBKR",
        source_system="Broker_IBKR",
        is_shadow=False,
    )

    # Transactions: full VOO history
    _insert_tx(
        connector,
        transaction_date="2024-01-15",
        asset_id="US_STK_VOO",
        source_system="Schwab_CSV",
        transaction_type="buy",
        quantity=27.0,
        price_unit=599.0,
        amount_net=-16173.81,
        currency="USD",
    )
    _insert_tx(
        connector,
        transaction_date="2025-03-10",
        asset_id="US_STK_VOO",
        source_system="Schwab_CSV",
        transaction_type="sell",
        quantity=6.0,
        price_unit=610.0,
        amount_net=3660.0,
        currency="USD",
    )
    # Schwab ACAT transfer-out (maps to 'other' in Schwab normalizer)
    _insert_tx(
        connector,
        transaction_date="2026-05-24",
        asset_id="US_STK_VOO",
        source_system="Schwab_CSV",
        transaction_type="other",
        quantity=-21.0,
        price_unit=0.0,
        amount_net=0.0,
        currency="USD",
    )
    # IBKR ACAT transfer-in
    _insert_tx(
        connector,
        transaction_date="2026-05-24",
        asset_id="US_STK_VOO",
        source_system="Broker_IBKR",
        transaction_type="transfer_in",
        quantity=21.0,
        price_unit=0.0,
        amount_net=0.0,
        currency="USD",
    )

    updated = _backfill_fifo_cost_basis(connector)

    assert updated >= 1, f"Expected at least 1 holding updated, got {updated}"

    row = connector.execute(
        """
        SELECT cost_price_unit FROM holdings
        WHERE asset_id = 'US_STK_VOO'
          AND source_system = 'Broker_IBKR'
          AND is_shadow = FALSE
        """
    ).fetchone()

    assert row is not None, "IBKR VOO holding must exist"
    cost = float(row[0])
    assert cost > 0, (
        f"IBKR VOO cost_price_unit must be > 0 (acceptance gate C3.3). Got {cost}. "
        "Merged-ledger FIFO failed to reconstruct cost from Schwab buy lots."
    )
    assert 550 <= cost <= 650, (
        f"IBKR VOO cost_price_unit should be approx $599 (buy 27 @ 599, sell 6 FIFO, "
        f"21 remaining). Got {cost:.4f}. Expected range [550, 650]."
    )


# ---------------------------------------------------------------------------
# Test 2: Partial SGOV transfer — both Schwab and IBKR holdings active after transfer
# ---------------------------------------------------------------------------

def test_sgov_partial_transfer_ibkr_cost_is_positive(connector):
    """SGOV partially transferred: Schwab keeps some shares, IBKR gets some.

    Transaction history:
      Schwab buy 1068 @ ~100
      Schwab sell 414.88
      Schwab other -200  (ACAT transfer-out of 200 shares)
      IBKR transfer_in 200

    Holdings:
      Schwab SGOV: 453.12 remaining (active, is_shadow=FALSE), has Schwab cost from reader
      IBKR SGOV:   200 shares, cost_price_unit=0 (active, is_shadow=FALSE)

    After backfill:
      IBKR SGOV cost_price_unit must be > 0 (merged FIFO reconstructs from Schwab buy lots).
    """
    # Schwab holding — still active with some cost already provided (reader sets it)
    # To test the backfill, set Schwab cost to NULL too (or set it; backfill skips non-NULL)
    # For IBKR: cost=0 → should get nulled then recomputed via merged FIFO
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="US_ETF_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=453.12,
        cost_price_unit=None,  # reader-NULL → backfill will compute
        market_price_unit=100.50,
        market_value=453.12 * 100.50 * 7.1,
        currency="USD",
        account="Schwab",
        source_system="Schwab_CSV",
        is_shadow=False,
    )
    # IBKR SGOV holding (cost=0, transferred-in)
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="US_ETF_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=200.0,
        cost_price_unit=0.0,  # cost unknown
        market_price_unit=100.50,
        market_value=200.0 * 100.50 * 7.1,
        currency="USD",
        account="IBKR",
        source_system="Broker_IBKR",
        is_shadow=False,
    )

    # Transactions
    _insert_tx(
        connector,
        transaction_date="2024-06-01",
        asset_id="US_ETF_SGOV",
        source_system="Schwab_CSV",
        transaction_type="buy",
        quantity=1068.0,
        price_unit=100.0,
        amount_net=-106800.0,
        currency="USD",
    )
    _insert_tx(
        connector,
        transaction_date="2025-09-15",
        asset_id="US_ETF_SGOV",
        source_system="Schwab_CSV",
        transaction_type="sell",
        quantity=414.88,
        price_unit=100.10,
        amount_net=41548.49,
        currency="USD",
    )
    _insert_tx(
        connector,
        transaction_date="2026-05-20",
        asset_id="US_ETF_SGOV",
        source_system="Schwab_CSV",
        transaction_type="other",
        quantity=-200.0,
        price_unit=0.0,
        amount_net=0.0,
        currency="USD",
    )
    _insert_tx(
        connector,
        transaction_date="2026-05-20",
        asset_id="US_ETF_SGOV",
        source_system="Broker_IBKR",
        transaction_type="transfer_in",
        quantity=200.0,
        price_unit=0.0,
        amount_net=0.0,
        currency="USD",
    )

    _backfill_fifo_cost_basis(connector)

    row = connector.execute(
        """
        SELECT cost_price_unit FROM holdings
        WHERE asset_id = 'US_ETF_SGOV'
          AND source_system = 'Broker_IBKR'
          AND is_shadow = FALSE
        """
    ).fetchone()

    assert row is not None, "IBKR SGOV holding must exist"
    cost = float(row[0])
    assert cost > 0, (
        f"IBKR SGOV cost_price_unit must be > 0 after merged-ledger FIFO backfill. "
        f"Got {cost}. The FIFO must include Schwab buy lots even though IBKR is the holding source."
    )
    assert 90 <= cost <= 110, (
        f"IBKR SGOV cost_price_unit should be near $100 (buy price). Got {cost:.4f}."
    )
