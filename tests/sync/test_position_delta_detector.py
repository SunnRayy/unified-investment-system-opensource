"""Tests for position_delta_detector.

All tests use an in-memory DuckDB database — no real file-based DB.
"""

from datetime import date

import pytest

from src.database.connector import DatabaseConnector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_db():
    """In-memory DuckDB with full schema + migrations applied."""
    from src.database.schema import initialize_schema
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)  # creates holdings table and all other tables
    connector.run_migrations()    # adds price_source, position_deltas, etc.
    yield connector
    connector.close()


def _insert_holding(connector, asset_id, quantity, source_system, snapshot_date=None, is_shadow=False):
    """Helper to insert a holding row for testing."""
    if snapshot_date is None:
        snapshot_date = date.today()
    connector.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit,
            market_value, currency, account, source_system, is_shadow
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (snapshot_date, asset_id, source_system) DO UPDATE SET
            quantity = EXCLUDED.quantity,
            is_shadow = EXCLUDED.is_shadow
        """,
        (snapshot_date, asset_id, "Test Asset", "Stock",
         quantity, "shares", 10.0, 20.0, quantity * 20.0, "USD",
         "TestAcct", source_system, is_shadow),
    )


# ---------------------------------------------------------------------------
# capture_pre_sync_snapshot
# ---------------------------------------------------------------------------

def test_capture_snapshot_returns_active_holdings(mem_db):
    from src.sync.position_delta_detector import capture_pre_sync_snapshot

    today = date.today()
    _insert_holding(mem_db, "US_STK_AAPL", 100.0, "Schwab_CSV", snapshot_date=today)
    _insert_holding(mem_db, "US_STK_MSFT", 50.0, "Schwab_CSV", snapshot_date=today)

    snapshot = capture_pre_sync_snapshot(mem_db, "Schwab_CSV")
    assert set(snapshot.keys()) == {"US_STK_AAPL", "US_STK_MSFT"}
    assert snapshot["US_STK_AAPL"][0] == 100.0
    assert snapshot["US_STK_AAPL"][1] == today
    assert snapshot["US_STK_MSFT"][0] == 50.0
    assert snapshot["US_STK_MSFT"][1] == today


def test_capture_snapshot_excludes_shadow_rows(mem_db):
    from src.sync.position_delta_detector import capture_pre_sync_snapshot

    _insert_holding(mem_db, "US_STK_AAPL", 100.0, "Schwab_CSV")
    _insert_holding(mem_db, "US_STK_MSFT", 50.0, "Schwab_CSV", is_shadow=True)

    snapshot = capture_pre_sync_snapshot(mem_db, "Schwab_CSV")
    assert "US_STK_MSFT" not in snapshot
    assert "US_STK_AAPL" in snapshot
    # Value is now a (qty, snapshot_date) tuple
    assert snapshot["US_STK_AAPL"][0] == 100.0


def test_capture_snapshot_excludes_zero_qty(mem_db):
    from src.sync.position_delta_detector import capture_pre_sync_snapshot

    _insert_holding(mem_db, "US_STK_AAPL", 0.0, "Schwab_CSV")

    snapshot = capture_pre_sync_snapshot(mem_db, "Schwab_CSV")
    assert snapshot == {}  # zero qty excluded


def test_capture_snapshot_source_isolated(mem_db):
    from src.sync.position_delta_detector import capture_pre_sync_snapshot

    _insert_holding(mem_db, "US_STK_AAPL", 100.0, "Schwab_CSV")
    _insert_holding(mem_db, "CN_FUND_900008", 1000.0, "CN_Fund_Excel")

    schwab = capture_pre_sync_snapshot(mem_db, "Schwab_CSV")
    cn = capture_pre_sync_snapshot(mem_db, "CN_Fund_Excel")

    assert set(schwab.keys()) == {"US_STK_AAPL"}
    assert set(cn.keys()) == {"CN_FUND_900008"}
    # Values are (qty, snapshot_date) tuples
    assert schwab["US_STK_AAPL"][0] == 100.0
    assert cn["CN_FUND_900008"][0] == 1000.0


def test_capture_snapshot_empty_db(mem_db):
    from src.sync.position_delta_detector import capture_pre_sync_snapshot

    snapshot = capture_pre_sync_snapshot(mem_db, "Schwab_CSV")
    assert snapshot == {}


# ---------------------------------------------------------------------------
# detect_and_persist_deltas
# ---------------------------------------------------------------------------

def test_detect_new_position(mem_db):
    """Asset in post-state but not in pre-snapshot → delta_qty > 0."""
    from src.sync.position_delta_detector import detect_and_persist_deltas

    # Insert new position (no pre-snapshot)
    _insert_holding(mem_db, "US_STK_AAPL", 100.0, "Schwab_CSV")

    pre_snapshot = {}  # empty — position is new
    deltas = detect_and_persist_deltas(mem_db, "Schwab_CSV", pre_snapshot, date.today())

    assert len(deltas) == 1
    assert deltas[0]["asset_id"] == "US_STK_AAPL"
    assert deltas[0]["old_qty"] == 0.0
    assert deltas[0]["new_qty"] == 100.0
    assert deltas[0]["delta_qty"] == 100.0


def test_detect_closed_position(mem_db):
    """Asset in pre-snapshot but no longer in post-state → delta_qty < 0."""
    from src.sync.position_delta_detector import detect_and_persist_deltas

    # No current holding for AAPL (closed position)
    old_date = date(2025, 1, 1)
    pre_snapshot = {"US_STK_AAPL": (100.0, old_date)}
    deltas = detect_and_persist_deltas(mem_db, "Schwab_CSV", pre_snapshot, date.today())

    assert len(deltas) == 1
    assert deltas[0]["asset_id"] == "US_STK_AAPL"
    assert deltas[0]["old_qty"] == 100.0
    assert deltas[0]["new_qty"] == 0.0
    assert deltas[0]["delta_qty"] == -100.0


def test_detect_quantity_change(mem_db):
    """Asset present in both but with different qty → delta is the difference."""
    from src.sync.position_delta_detector import detect_and_persist_deltas

    _insert_holding(mem_db, "US_STK_AAPL", 150.0, "Schwab_CSV")

    pre_snapshot = {"US_STK_AAPL": (100.0, date(2025, 1, 1))}
    deltas = detect_and_persist_deltas(mem_db, "Schwab_CSV", pre_snapshot, date.today())

    assert len(deltas) == 1
    assert abs(deltas[0]["delta_qty"] - 50.0) < 1e-6


def test_detect_no_change(mem_db):
    """Same qty before and after → no delta persisted."""
    from src.sync.position_delta_detector import detect_and_persist_deltas

    _insert_holding(mem_db, "US_STK_AAPL", 100.0, "Schwab_CSV")

    pre_snapshot = {"US_STK_AAPL": (100.0, date.today())}
    deltas = detect_and_persist_deltas(mem_db, "Schwab_CSV", pre_snapshot, date.today())

    assert len(deltas) == 0


def test_detect_persists_to_db(mem_db):
    """Deltas should be queryable from position_deltas table."""
    from src.sync.position_delta_detector import detect_and_persist_deltas

    _insert_holding(mem_db, "US_STK_AAPL", 100.0, "Schwab_CSV")

    detect_and_persist_deltas(mem_db, "Schwab_CSV", {}, date.today())

    rows = mem_db.execute("SELECT COUNT(*) FROM position_deltas").fetchone()
    assert rows[0] == 1


def test_detect_rerun_idempotent(mem_db):
    """Running detect twice with the same inputs should not create duplicate rows."""
    from src.sync.position_delta_detector import detect_and_persist_deltas

    _insert_holding(mem_db, "US_STK_AAPL", 100.0, "Schwab_CSV")

    snap_date = date.today()
    detect_and_persist_deltas(mem_db, "Schwab_CSV", {}, snap_date)
    detect_and_persist_deltas(mem_db, "Schwab_CSV", {}, snap_date)  # re-run

    rows = mem_db.execute("SELECT COUNT(*) FROM position_deltas").fetchone()
    assert rows[0] == 1, "Duplicate delta should be silently ignored on re-run"


def test_detect_confirmed_defaults_false(mem_db):
    """Newly detected deltas should have confirmed=FALSE."""
    from src.sync.position_delta_detector import detect_and_persist_deltas

    _insert_holding(mem_db, "US_STK_AAPL", 100.0, "Schwab_CSV")
    detect_and_persist_deltas(mem_db, "Schwab_CSV", {}, date.today())

    row = mem_db.execute("SELECT confirmed FROM position_deltas LIMIT 1").fetchone()
    assert row[0] is False or row[0] == 0


def test_detect_multiple_assets(mem_db):
    """Multiple changed assets are all detected."""
    from src.sync.position_delta_detector import detect_and_persist_deltas

    _insert_holding(mem_db, "US_STK_AAPL", 100.0, "Schwab_CSV")
    _insert_holding(mem_db, "US_STK_MSFT", 200.0, "Schwab_CSV")

    # Pre-snapshot had different qtys
    old_date = date(2025, 1, 1)
    pre_snapshot = {
        "US_STK_AAPL": (80.0, old_date),
        "US_STK_TSLA": (50.0, old_date),  # TSLA was closed
    }

    deltas = detect_and_persist_deltas(mem_db, "Schwab_CSV", pre_snapshot, date.today())

    asset_ids = {d["asset_id"] for d in deltas}
    assert "US_STK_AAPL" in asset_ids    # qty changed 80→100
    assert "US_STK_MSFT" in asset_ids    # new position
    assert "US_STK_TSLA" in asset_ids    # closed position
