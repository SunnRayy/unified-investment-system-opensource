"""Tests for src/services/position_lots.py (Fix 1, 2026-07-10 fix-request).

Property-based test: for every active asset in a multi-asset fixture,
scan_value_traps unrealized_return_pct == unrealized_from_holdings_row(...)
with the same holdings-row inputs.  Verifies the single-formula guarantee.

Additional unit tests cover FIFO lot replay, weighted average, and the
¥900013 case (cost 3.9266 vs price 2.7730 → −29.4%).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional


from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.position_lots import (
    current_lot_cost,
    open_lots,
    unrealized_from_holdings_row,
    unrealized_return_current_lots,
)
from src.services.value_trap import _unrealized_return_pct
from src.services.verification_config import VerificationConfig


def _make_db() -> DatabaseConnector:
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    return conn


# ── Helpers ──────────────────────────────────────────────────────────────────

def _insert_holding(
    conn: DatabaseConnector,
    asset_id: str,
    name: str,
    cost_price_unit: float,
    market_price_unit: float,
    quantity: float = 100.0,
    currency: str = "CNY",
    snapshot_date: Optional[str] = None,
) -> None:
    if snapshot_date is None:
        snapshot_date = (date.today() - timedelta(days=1)).isoformat()
    market_value = market_price_unit * quantity
    if currency == "USD":
        # Caller provides market_price_unit in USD; market_value stored in CNY (×7.2 stub)
        market_value = market_price_unit * quantity * 7.2
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, quantity, cost_price_unit,
             market_price_unit, market_value, currency, source_system, is_shadow)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'test', FALSE)
        """,
        [snapshot_date, asset_id, name, quantity,
         cost_price_unit, market_price_unit, market_value, currency],
    )


def _insert_tx(
    conn: DatabaseConnector,
    asset_id: str,
    tx_date: str,
    tx_type: str,
    quantity: float,
    price_unit: float,
    amount_net: float = 0.0,
    source_system: str = "test",
) -> None:
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type,
             quantity, price_unit, amount_net, currency, source_system, is_provisional)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'CNY', ?, FALSE)
        """,
        [tx_date, asset_id, asset_id, tx_type, quantity, price_unit, amount_net, source_system],
    )


# ── FIFO open_lots ────────────────────────────────────────────────────────────

def test_open_lots_empty_when_no_transactions():
    conn = _make_db()
    assert open_lots(conn, "UNKNOWN") == []
    conn.close()


def test_open_lots_single_buy():
    conn = _make_db()
    _insert_tx(conn, "FUND_A", "2024-01-01", "buy", 100.0, 3.5)
    lots = open_lots(conn, "FUND_A")
    assert len(lots) == 1
    assert abs(lots[0]["quantity"] - 100.0) < 1e-6
    assert abs(lots[0]["price_unit"] - 3.5) < 1e-6
    conn.close()


def test_open_lots_fifo_sell_consumes_oldest_lot():
    conn = _make_db()
    _insert_tx(conn, "FUND_A", "2022-01-01", "buy", 100.0, 4.0)
    _insert_tx(conn, "FUND_A", "2023-01-01", "buy", 200.0, 3.5)
    _insert_tx(conn, "FUND_A", "2024-06-01", "sell", 80.0, 2.7)

    lots = open_lots(conn, "FUND_A")
    # Oldest lot (100 @ 4.0) partially consumed: 100-80 = 20 remaining
    # Second lot (200 @ 3.5) untouched
    assert len(lots) == 2
    assert abs(lots[0]["price_unit"] - 4.0) < 1e-6
    assert abs(lots[0]["quantity"] - 20.0) < 1e-6
    assert abs(lots[1]["price_unit"] - 3.5) < 1e-6
    assert abs(lots[1]["quantity"] - 200.0) < 1e-6
    conn.close()


def test_open_lots_fully_sold_returns_empty():
    conn = _make_db()
    _insert_tx(conn, "FUND_X", "2022-01-01", "buy", 100.0, 5.0)
    _insert_tx(conn, "FUND_X", "2024-01-01", "sell", 100.0, 4.0)
    lots = open_lots(conn, "FUND_X")
    assert lots == []
    conn.close()


# ── ACAT pair-aware exclusion (2026-07-19 double-count fix) ──────────────────
# Verified live bug: transfer_in was in _BUY_TYPES with no consuming leg for
# transfer_out, so an ACAT'd position (e.g. VOO 42 vs 21 actually held) got a
# second lot on top of the original broker's buy lot.

def test_acat_pair_does_not_add_extra_lot():
    """Original buy on broker A, then a same-asset ACAT transfer (out of A,
    into B) with matching qty within 7 days: the transfer_in leg must NOT
    create a new lot — the original buy lot already represents the position,
    and it must still be the only open lot afterward (net qty == original buy)."""
    conn = _make_db()
    _insert_tx(conn, "US_STK_VOO", "2024-01-01", "buy", 21.0, 400.0, source_system="Schwab_CSV")
    _insert_tx(conn, "US_STK_VOO", "2024-06-08", "transfer_out", -21.0, 0.0, source_system="Schwab_CSV")
    _insert_tx(conn, "US_STK_VOO", "2024-06-09", "transfer_in", 21.0, 0.0, source_system="IBKR_Flex")

    lots = open_lots(conn, "US_STK_VOO")
    total_qty = sum(lot["quantity"] for lot in lots)
    # Only the original buy lot — the paired transfer_in must be excluded.
    assert len(lots) == 1
    assert abs(total_qty - 21.0) < 1e-6
    assert abs(lots[0]["price_unit"] - 400.0) < 1e-6
    conn.close()


def test_unpaired_transfer_in_still_adds_lot():
    """CN-fund 超级转换 conversion case: the in-leg has no same-asset
    transfer_out counterpart (the out-leg redeemed a DIFFERENT asset_id) —
    current behavior (add a lot) must be preserved."""
    conn = _make_db()
    _insert_tx(conn, "CN_FUND_900001", "2024-03-01", "transfer_in", 500.0, 2.0, source_system="CN_Fund_Excel")

    lots = open_lots(conn, "CN_FUND_900001")
    assert len(lots) == 1
    assert abs(lots[0]["quantity"] - 500.0) < 1e-6
    assert abs(lots[0]["price_unit"] - 2.0) < 1e-6
    conn.close()


def test_acat_pair_outside_7day_window_not_excluded():
    """A transfer_in more than 7 days from any same-asset transfer_out is NOT
    a pair — must still add a lot (avoids over-eager exclusion)."""
    conn = _make_db()
    _insert_tx(conn, "US_STK_IEF", "2024-01-01", "buy", 172.0, 95.0, source_system="Schwab_CSV")
    _insert_tx(conn, "US_STK_IEF", "2024-06-01", "transfer_out", -172.0, 0.0, source_system="Schwab_CSV")
    _insert_tx(conn, "US_STK_IEF", "2024-06-20", "transfer_in", 172.0, 0.0, source_system="IBKR_Flex")  # 19 days later

    lots = open_lots(conn, "US_STK_IEF")
    total_qty = sum(lot["quantity"] for lot in lots)
    # Original buy lot (172) + the unpaired transfer_in lot (172) = 344 —
    # correctly reflects that this is NOT recognized as an ACAT pair.
    assert abs(total_qty - 344.0) < 1e-6
    conn.close()


# ── current_lot_cost ──────────────────────────────────────────────────────────

def test_current_lot_cost_weighted_average():
    conn = _make_db()
    # Two lots: 100 @ 4.0 and 200 @ 3.5
    # Weighted avg = (100*4.0 + 200*3.5) / 300 = (400 + 700) / 300 = 1100/300 = 3.6667
    _insert_tx(conn, "FUND_B", "2022-01-01", "buy", 100.0, 4.0)
    _insert_tx(conn, "FUND_B", "2023-01-01", "buy", 200.0, 3.5)
    info = current_lot_cost(conn, "FUND_B")
    assert info is not None
    assert abs(info["avg_cost"] - (100 * 4.0 + 200 * 3.5) / 300) < 1e-4
    assert abs(info["open_qty"] - 300.0) < 1e-6
    assert len(info["lots"]) == 2
    conn.close()


def test_current_lot_cost_returns_none_for_no_transactions():
    conn = _make_db()
    assert current_lot_cost(conn, "MISSING") is None
    conn.close()


# ── unrealized_return_current_lots ────────────────────────────────────────────

def test_unrealized_return_current_lots_900013_case():
    """Lead's verified case: cost 3.9266, price 2.7730 → -29.4%"""
    conn = _make_db()
    # Simulate weighted avg of 3.9266 via a single-lot simplification
    _insert_tx(conn, "CN_FUND_900013", "2020-01-01", "buy", 45387.76, 3.9266)
    pct = unrealized_return_current_lots(conn, "CN_FUND_900013", current_price=2.7730)
    assert pct is not None
    # (2.7730 - 3.9266) / 3.9266 * 100 = -29.378...
    assert abs(pct - (-29.38)) < 0.5
    conn.close()


def test_unrealized_return_current_lots_returns_none_no_lots():
    conn = _make_db()
    result = unrealized_return_current_lots(conn, "NO_LOTS", 2.7)
    assert result is None
    conn.close()


# ── unrealized_from_holdings_row ─────────────────────────────────────────────

def test_unrealized_from_holdings_row_cny_basic(monkeypatch):
    """CNY fund: (market_price - cost_price) / cost_price * 100."""
    # cost_price_unit=4.0, market_price_unit=3.0, quantity=100
    # cost_basis_cny = 4.0 * 100 = 400
    # market_value = 3.0 * 100 = 300 (in CNY)
    # unrealized_cny = 300 - 400 = -100
    # return_pct = -100/400 * 100 = -25.0
    result = unrealized_from_holdings_row(
        market_value=300.0,
        quantity=100.0,
        cost_price_unit=4.0,
        market_price_unit=3.0,
        currency="CNY",
        top_class="CN Equity",
        sub_class="CN Equity",
        today_fx=7.2,
    )
    assert result is not None
    assert abs(result - (-25.0)) < 0.01


def test_unrealized_from_holdings_row_returns_none_zero_cost(monkeypatch):
    """cost_price_unit = 0.0 → cost_basis_cny = 0 → must return None (not divide-by-zero)."""
    result = unrealized_from_holdings_row(
        market_value=1000.0,
        quantity=100.0,
        cost_price_unit=0.0,
        market_price_unit=10.0,
        currency="CNY",
        top_class="CN Equity",
        sub_class="CN Equity",
        today_fx=7.2,
    )
    assert result is None


def test_unrealized_from_holdings_row_900013_fixture():
    """Reproduces the lead-verified 900013 case through the shared function."""
    result = unrealized_from_holdings_row(
        market_value=45387.76 * 2.7730,
        quantity=45387.76,
        cost_price_unit=3.9266,
        market_price_unit=2.7730,
        currency="CNY",
        top_class="CN Equity",
        sub_class="CN Equity",
        today_fx=7.2,
    )
    assert result is not None
    # (2.7730 - 3.9266) / 3.9266 * 100
    expected = (2.7730 - 3.9266) / 3.9266 * 100
    assert abs(result - expected) < 0.01
    assert abs(result - (-29.38)) < 0.5


# ── Property test: scan == shared function ────────────────────────────────────

def _make_cfg_threshold(threshold_pct: float) -> VerificationConfig:
    """Build a minimal VerificationConfig with a custom threshold."""
    cfg = load_config_default()
    cfg.value_trap.trigger_threshold_pct = threshold_pct
    return cfg


def load_config_default() -> VerificationConfig:
    from src.services.verification_config import load_verification_config
    return load_verification_config()


def test_property_scan_and_shared_function_agree(monkeypatch):
    """For every active asset in a multi-asset fixture, the loss % computed by
    scan_value_traps equals unrealized_from_holdings_row with the same inputs.

    This is the Fix 1 single-formula guarantee: both consumers must agree.
    """
    conn = _make_db()
    FX = 7.2
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: FX)

    # Multi-asset fixture: different currencies, loss levels, asset types
    assets = [
        # (asset_id, cost, market_factor, qty, currency)
        ("CN_FUND_900013", 3.9266, 2.7730 / 3.9266, 45387.76, "CNY"),  # -29.4%
        ("CN_FUND_900014", 2.0, 0.591, 10000.0, "CNY"),  # -40.9%
        ("US_STK_MSFT", 300.0, 0.95, 200.0, "USD"),  # -5%
    ]
    today = (date.today() - timedelta(days=1)).isoformat()
    for asset_id, cost, mkt_factor, qty, currency in assets:
        market_price = cost * mkt_factor
        market_value = qty * market_price
        if currency == "USD":
            market_value *= FX
        conn.execute(
            """
            INSERT INTO holdings
                (snapshot_date, asset_id, asset_name, quantity, cost_price_unit,
                 market_price_unit, market_value, currency, source_system, is_shadow)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'test', FALSE)
            """,
            [today, asset_id, asset_id, qty, cost, market_price, market_value, currency],
        )

    # Fetch same rows the scan reads (per-asset latest CTE)
    rows = conn.execute(
        """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_date
            FROM holdings WHERE is_shadow = FALSE
            GROUP BY asset_id
        )
        SELECT
            h.asset_id, MAX(h.asset_name), COALESCE(MAX(r.asset_class), 'Unknown'),
            SUM(h.market_value), SUM(h.quantity),
            MAX(h.cost_price_unit), MAX(h.market_price_unit), MAX(h.currency),
            MAX(lpa.latest_date), MAX(h.price_updated_at)
        FROM holdings h
        JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
        WHERE h.is_shadow = FALSE
        GROUP BY h.asset_id
        HAVING SUM(h.market_value) > 0 AND SUM(h.quantity) > 0
        """
    ).fetchall()

    for row in rows:
        # What the scan computes (now via unrealized_from_holdings_row)
        scan_pct = _unrealized_return_pct(row, FX)

        # Direct call to the shared function with the same inputs
        _, _, asset_class, market_value, quantity, cost_price_unit, market_price_unit, currency, *_ = row
        direct_pct = unrealized_from_holdings_row(
            market_value=float(market_value or 0.0),
            quantity=float(quantity or 0.0),
            cost_price_unit=float(cost_price_unit or 0.0),
            market_price_unit=float(market_price_unit or 0.0),
            currency=str(currency or "CNY"),
            top_class=str(asset_class or ""),
            sub_class=str(asset_class or ""),
            today_fx=FX,
        )

        # Property: they must agree exactly (same function under the hood)
        assert scan_pct == direct_pct, (
            f"Asset {row[0]}: scan={scan_pct} != direct={direct_pct}"
        )

    conn.close()
