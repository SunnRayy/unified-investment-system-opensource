"""Unit tests for the provisional pending-trade overlay in build_compass_allocation().

Tests use an in-memory DuckDB connection following the pattern in:
  tests/api/test_compass_summary.py
  tests/services/test_compass_allocation_exception.py

Coverage:
  (a) include_pending=False returns plain list — identical to current behavior.
  (b) A pending Buy increases the asset class's provisional_value / provisional_pct.
  (c) A pending Sell decreases the asset class's provisional_value / provisional_pct.
  (d) Verified trades are NOT double-counted (they are already in holdings).
  (e) A USD-denominated trade is converted to CNY before the overlay is applied.
"""
from __future__ import annotations

import duckdb

from src.services.compass_allocation import build_compass_allocation


# ── helpers ──────────────────────────────────────────────────────────────────


class DuckDBAdapter:
    """Thin adapter so the DuckDB connection quacks like DatabaseConnector."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.connection = conn

    def execute(self, query: str, params=None):
        if params is None:
            return self.connection.execute(query)
        return self.connection.execute(query, params)


def _build_db(tmp_path, *, trade_rows: list[tuple] | None = None) -> DuckDBAdapter:
    """Create a minimal in-memory DuckDB with the tables required by
    build_compass_allocation() and _compute_pending_overlay().

    Schema subset:
      - holdings(snapshot_date, asset_id, market_value, is_shadow)
      - asset_registry(canonical_id, asset_class, is_rebalanceable)
      - taxonomy_classes(id, name, parent_id, is_rebalanceable, level)
      - risk_profile_allocations / risk_profiles / taxonomy_classes (for targets)
      - trade_logs(id, asset_id, action, quantity, price, amount, currency,
                   verification_status, linked_transaction_id)

    Holdings:
      - Equity (stock1): 500_000 CNY  (top: Equity, sub: US Equity)
      - Fixed Income (bond1): 300_000 CNY (top: Fixed Income, sub: CN Bonds)

    trade_rows override — list of tuples:
      (id, asset_id, action, quantity, price, amount, currency, verification_status)
    """
    conn = duckdb.connect(str(tmp_path / "test.duckdb"))
    db = DuckDBAdapter(conn)

    conn.execute("""
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id      VARCHAR,
            asset_name    VARCHAR,
            quantity      DOUBLE,
            market_value  DOUBLE,
            currency      VARCHAR,
            source_system VARCHAR,
            is_shadow     BOOLEAN
        )
    """)
    conn.execute("""
        CREATE TABLE asset_registry (
            canonical_id     VARCHAR,
            asset_class      VARCHAR,
            is_rebalanceable BOOLEAN
        )
    """)
    conn.execute("""
        CREATE TABLE taxonomy_classes (
            id               INTEGER,
            name             VARCHAR,
            parent_id        INTEGER,
            is_rebalanceable BOOLEAN,
            level            INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE risk_profiles (
            id        INTEGER,
            name      VARCHAR,
            is_active BOOLEAN
        )
    """)
    conn.execute("""
        CREATE TABLE risk_profile_allocations (
            id         INTEGER,
            profile_id INTEGER,
            class_id   INTEGER,
            target_pct DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE trade_logs (
            id                  INTEGER,
            asset_id            VARCHAR,
            action              VARCHAR,
            quantity            DOUBLE,
            price               DOUBLE,
            amount              DOUBLE,
            currency            VARCHAR,
            verification_status VARCHAR,
            linked_transaction_id INTEGER
        )
    """)

    # taxonomy: Equity (id=1, root) → US Equity (id=2, parent=1)
    #           Fixed Income (id=3, root) → CN Bonds (id=4, parent=3)
    conn.execute("""
        INSERT INTO taxonomy_classes VALUES
        (1, 'Equity',        NULL, TRUE, 0),
        (2, 'US Equity',     1,    TRUE, 1),
        (3, 'Fixed Income',  NULL, TRUE, 0),
        (4, 'CN Bonds',      3,    TRUE, 1)
    """)

    conn.execute("""
        INSERT INTO asset_registry VALUES
        ('stock1', 'US Equity', TRUE),
        ('bond1',  'CN Bonds',  TRUE)
    """)

    # Two holdings, same snapshot date
    conn.execute("""
        INSERT INTO holdings VALUES
        ('2026-06-01', 'stock1', 'Test Stock', 10, 500000, 'CNY', 'Schwab_CSV', FALSE),
        ('2026-06-01', 'bond1',  'Test Bond',   5, 300000, 'CNY', 'CN_Fund_Excel', FALSE)
    """)

    # Trade logs — insert caller-supplied rows (or leave empty)
    if trade_rows:
        for row in trade_rows:
            conn.execute(
                "INSERT INTO trade_logs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", list(row)
            )

    return db


# ── test (a): include_pending=False returns plain list ────────────────────────


def test_include_pending_false_returns_plain_list(tmp_path):
    """include_pending=False must return a plain list, not a dict envelope."""
    db = _build_db(tmp_path)
    result = build_compass_allocation(db, include_pending=False)
    assert isinstance(result, list), "Expected plain list when include_pending=False"


def test_truthy_non_true_include_pending_returns_plain_list(tmp_path):
    """Regression (#13): a truthy-but-not-True value (e.g. a FastAPI Query(default=False)
    object leaking through a direct route-function call) must NOT flip the return shape to
    an envelope. Only a literal True opts into the provisional overlay. This guards
    /compass/markdown and any other direct caller of the route function.
    """
    db = _build_db(tmp_path)

    class _TruthyQuerySentinel:
        def __bool__(self) -> bool:  # truthy, like a Query object
            return True

    result = build_compass_allocation(db, include_pending=_TruthyQuerySentinel())
    assert isinstance(result, list), (
        "A truthy-non-True include_pending must return a plain list, not an envelope dict"
    )


def test_include_pending_false_identical_to_default(tmp_path):
    """include_pending=False result must equal calling without the parameter."""
    db = _build_db(tmp_path)
    default_result = build_compass_allocation(db)
    explicit_false = build_compass_allocation(db, include_pending=False)
    assert default_result == explicit_false, (
        "include_pending=False must produce byte-for-byte identical output to the default call"
    )


def test_include_pending_false_no_provisional_fields(tmp_path):
    """Rows from include_pending=False must NOT contain provisional_* keys."""
    db = _build_db(tmp_path)
    result = build_compass_allocation(db, include_pending=False)
    assert isinstance(result, list)
    for row in result:
        assert "provisional_value" not in row, f"Unexpected provisional_value in row: {row}"
        assert "provisional_pct" not in row
        assert "provisional_delta_cny" not in row


# ── test (b): pending Buy increases provisional value/pct ────────────────────


def test_pending_buy_increases_provisional_value(tmp_path, monkeypatch):
    """A pending Buy on stock1 (Equity) must increase provisional_value for Equity."""
    # Patch currency service so the test is hermetic
    monkeypatch.setattr(
        "src.services.compass_allocation.get_today_usd_cny_rate",
        lambda: 7.0,
    )

    trade_rows = [
        # id, asset_id, action, quantity, price, amount, currency, verification_status, linked_tx_id
        (1, "stock1", "Buy", 10.0, None, 50000.0, "CNY", "pending", None),
    ]
    db = _build_db(tmp_path, trade_rows=trade_rows)
    result = build_compass_allocation(db, include_pending=True)

    assert isinstance(result, dict), "Expected dict envelope when include_pending=True"
    assert "allocation" in result and "meta" in result

    meta = result["meta"]
    assert meta["is_provisional"] is True
    assert meta["pending_trade_count"] == 1

    alloc = result["allocation"]
    equity_rows = [r for r in alloc if r["asset_class"] == "Equity (股票)" and r["is_top_level"]]
    assert equity_rows, "Expected top-level Equity row"
    eq = equity_rows[0]

    # current_value must be unchanged (verified base = 500_000)
    assert eq["current_value"] == 500_000.0

    # provisional_value = 500_000 + 50_000 = 550_000
    assert eq["provisional_value"] == 550_000.0
    assert eq["provisional_delta_cny"] == 50_000.0

    # provisional_pct must be > current_pct
    assert eq["provisional_pct"] > eq["current_pct"]


def test_pending_buy_via_quantity_price(tmp_path, monkeypatch):
    """A pending Buy specified as quantity*price (no amount) is computed correctly."""
    monkeypatch.setattr(
        "src.services.compass_allocation.get_today_usd_cny_rate",
        lambda: 7.0,
    )

    trade_rows = [
        # amount=None → quantity*price = 5 * 10000 = 50000
        (2, "stock1", "Buy", 5.0, 10000.0, None, "CNY", "pending_window", None),
    ]
    db = _build_db(tmp_path, trade_rows=trade_rows)
    result = build_compass_allocation(db, include_pending=True)

    alloc = result["allocation"]
    eq = next(r for r in alloc if r["asset_class"] == "Equity (股票)" and r["is_top_level"])
    assert eq["provisional_delta_cny"] == 50_000.0


# ── test (c): pending Sell decreases provisional value/pct ───────────────────


def test_pending_sell_decreases_provisional_value(tmp_path, monkeypatch):
    """A pending Sell on bond1 (Fixed Income) must decrease provisional_value."""
    monkeypatch.setattr(
        "src.services.compass_allocation.get_today_usd_cny_rate",
        lambda: 7.0,
    )

    trade_rows = [
        (3, "bond1", "Sell", None, None, 100000.0, "CNY", "pending", None),
    ]
    db = _build_db(tmp_path, trade_rows=trade_rows)
    result = build_compass_allocation(db, include_pending=True)

    alloc = result["allocation"]
    fi_rows = [r for r in alloc if r["asset_class"] == "Fixed Income (固定收益)" and r["is_top_level"]]
    assert fi_rows, "Expected top-level Fixed Income row"
    fi = fi_rows[0]

    # provisional_value = 300_000 - 100_000 = 200_000
    assert fi["provisional_value"] == 200_000.0
    assert fi["provisional_delta_cny"] == -100_000.0
    assert fi["provisional_pct"] < fi["current_pct"]


# ── test (d): verified trades NOT double-counted ─────────────────────────────


def test_verified_trades_not_included_in_overlay(tmp_path, monkeypatch):
    """Trades with verification_status='verified' must NOT appear in the overlay."""
    monkeypatch.setattr(
        "src.services.compass_allocation.get_today_usd_cny_rate",
        lambda: 7.0,
    )

    trade_rows = [
        # Verified trade — already in holdings, must be excluded
        (4, "stock1", "Buy", None, None, 999999.0, "CNY", "verified", 42),
        # Blocked trade — also excluded
        (5, "stock1", "Buy", None, None, 999999.0, "CNY", "verification_blocked", None),
        # This one is pending and must be included
        (6, "bond1", "Buy", None, None, 10000.0, "CNY", "pending", None),
    ]
    db = _build_db(tmp_path, trade_rows=trade_rows)
    result = build_compass_allocation(db, include_pending=True)

    assert result["meta"]["pending_trade_count"] == 1  # only the pending bond buy

    alloc = result["allocation"]
    # Equity must have zero delta (verified + blocked trades excluded)
    eq = next(r for r in alloc if r["asset_class"] == "Equity (股票)" and r["is_top_level"])
    assert eq["provisional_delta_cny"] == 0.0
    assert eq["provisional_value"] == eq["current_value"]

    # Fixed Income must show the +10_000 delta
    fi = next(r for r in alloc if r["asset_class"] == "Fixed Income (固定收益)" and r["is_top_level"])
    assert fi["provisional_delta_cny"] == 10_000.0


# ── test (e): USD trade converted to CNY ─────────────────────────────────────


def test_usd_pending_trade_converted_to_cny(tmp_path, monkeypatch):
    """A USD-denominated pending Buy must be converted to CNY using get_today_usd_cny_rate()."""
    fx_rate = 7.25

    # Patch the function at the module level where it is called
    monkeypatch.setattr(
        "src.services.compass_allocation.get_today_usd_cny_rate",
        lambda: fx_rate,
    )

    usd_amount = 10_000.0
    expected_cny = usd_amount * fx_rate  # 72_500 CNY

    trade_rows = [
        # USD Buy for stock1 (Equity)
        (7, "stock1", "Buy", None, None, usd_amount, "USD", "pending", None),
    ]
    db = _build_db(tmp_path, trade_rows=trade_rows)
    result = build_compass_allocation(db, include_pending=True)

    alloc = result["allocation"]
    eq = next(r for r in alloc if r["asset_class"] == "Equity (股票)" and r["is_top_level"])

    assert abs(eq["provisional_delta_cny"] - expected_cny) < 0.01, (
        f"Expected CNY delta {expected_cny}, got {eq['provisional_delta_cny']}"
    )
    assert abs(eq["provisional_value"] - (500_000.0 + expected_cny)) < 0.01


# ── test: meta fields when no pending trades ─────────────────────────────────


def test_include_pending_no_pending_trades(tmp_path):
    """include_pending=True with no pending trades returns envelope with count=0."""
    db = _build_db(tmp_path)  # no trade_rows → trade_logs empty
    result = build_compass_allocation(db, include_pending=True)

    assert isinstance(result, dict)
    assert result["meta"]["pending_trade_count"] == 0
    assert result["meta"]["is_provisional"] is True

    for row in result["allocation"]:
        assert row["provisional_delta_cny"] == 0.0
        assert row["provisional_value"] == row["current_value"]
