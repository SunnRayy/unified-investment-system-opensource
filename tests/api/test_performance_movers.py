"""Tests for GET /performance/movers — price-ratio top movers endpoint (GitHub #27).

Rules:
- Never touches the real DB (uses tmp_path DuckDB fixture)
- Per-asset MAX(snapshot_date) — never global MAX
- is_shadow=FALSE enforced
- extract_symbol maps asset_id → market_daily.code
"""
import asyncio
import datetime as dt

import duckdb
import pytest

from src.api.routes.performance import get_movers


# ---------------------------------------------------------------------------
# Adapter (same pattern as test_performance_period.py)
# ---------------------------------------------------------------------------


class DuckDBAdapter:
    def __init__(self, connection: duckdb.DuckDBPyConnection):
        self.connection = connection

    def execute(self, query, params=None):
        if params is None:
            return self.connection.execute(query)
        return self.connection.execute(query, params)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_CREATE_HOLDINGS = """
    CREATE TABLE holdings (
        snapshot_date DATE,
        asset_id VARCHAR,
        asset_name VARCHAR,
        quantity DOUBLE,
        cost_price_unit DOUBLE,
        market_price_unit DOUBLE,
        market_value DOUBLE,
        currency VARCHAR,
        source_system VARCHAR,
        is_shadow BOOLEAN
    )
"""

_CREATE_MARKET_DAILY = """
    CREATE TABLE market_daily (
        id INTEGER PRIMARY KEY,
        code VARCHAR(20),
        date DATE NOT NULL,
        open DECIMAL(20,4),
        high DECIMAL(20,4),
        low DECIMAL(20,4),
        close DECIMAL(20,4),
        volume DECIMAL(30,2),
        amount DECIMAL(30,2),
        pct_chg DECIMAL(10,4),
        ma5 DECIMAL(20,4),
        ma10 DECIMAL(20,4),
        ma20 DECIMAL(20,4),
        pe_ttm DECIMAL(20,4),
        pb DECIMAL(20,4),
        data_source VARCHAR(50),
        UNIQUE(code, date)
    )
"""

_CREATE_ASSET_REGISTRY = """
    CREATE TABLE asset_registry (
        canonical_id VARCHAR,
        asset_class VARCHAR,
        asset_subclass VARCHAR,
        is_rebalanceable BOOLEAN
    )
"""

_CREATE_TAXONOMY_CLASSES = """
    CREATE TABLE taxonomy_classes (
        id INTEGER PRIMARY KEY,
        name VARCHAR,
        parent_id INTEGER,
        is_rebalanceable BOOLEAN
    )
"""


def _add_holding(conn, asset_id, name, mv, snapshot_date, is_shadow=False):
    conn.execute(
        "INSERT INTO holdings VALUES (?, ?, ?, 1, 100, 100, ?, 'CNY', 'TEST', ?)",
        [snapshot_date, asset_id, name, mv, is_shadow],
    )


def _add_price(conn, row_id, code, price_date, close):
    conn.execute(
        "INSERT INTO market_daily (id, code, date, close) VALUES (?, ?, ?, ?)",
        [row_id, code, price_date, close],
    )


# ---------------------------------------------------------------------------
# Base fixture — priced MSFT + unpriced NOPRICE + thin ONE_CLOSE
# ---------------------------------------------------------------------------

@pytest.fixture
def base_db(tmp_path):
    """
    Holdings:
      US_STK_MSFT  — mv=200,000  MSFT prices: [today-35d @180, today-15d @195, today @200]
      US_STK_NOPRICE — mv=50,000   no market_daily rows → excluded
      US_STK_ONECLS — mv=30,000    only 1 market_daily row → excluded (<2 closes)
    """
    db_path = tmp_path / "movers_base.duckdb"
    conn = duckdb.connect(str(db_path))
    for ddl in [_CREATE_HOLDINGS, _CREATE_MARKET_DAILY, _CREATE_ASSET_REGISTRY, _CREATE_TAXONOMY_CLASSES]:
        conn.execute(ddl)

    today = dt.date.today()
    p35 = today - dt.timedelta(days=35)
    p15 = today - dt.timedelta(days=15)

    # taxonomy
    conn.execute("INSERT INTO taxonomy_classes VALUES (1, 'Equity (股票)', NULL, TRUE)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (2, 'US Equity', 1, TRUE)")

    # MSFT
    _add_holding(conn, "US_STK_MSFT", "Microsoft", 200_000, today)
    conn.execute("INSERT INTO asset_registry VALUES ('US_STK_MSFT', 'US Equity', 'US Equity', TRUE)")
    _add_price(conn, 1, "MSFT", p35, 180.0)
    _add_price(conn, 2, "MSFT", p15, 195.0)
    _add_price(conn, 3, "MSFT", today, 200.0)

    # Unpriced
    _add_holding(conn, "US_STK_NOPRICE", "Unpriced", 50_000, today)
    conn.execute("INSERT INTO asset_registry VALUES ('US_STK_NOPRICE', 'US Equity', 'US Equity', TRUE)")

    # Only one close
    _add_holding(conn, "US_STK_ONECLS", "OneCls", 30_000, today)
    conn.execute("INSERT INTO asset_registry VALUES ('US_STK_ONECLS', 'US Equity', 'US Equity', TRUE)")
    _add_price(conn, 4, "ONECLS", today, 50.0)

    try:
        yield DuckDBAdapter(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests — priced asset window math
# ---------------------------------------------------------------------------

def test_priced_asset_pct_change_and_pl_impact(base_db):
    """p_now=200, p_then=180 (latest close ≤ window_start = today-30d → today-35d qualifies).

    pct_change = (200/180 - 1) * 100 ≈ 11.1111
    pl_impact  = 200_000 * (1 - 180/200) = 200_000 * 0.1 = 20_000
    """
    result = asyncio.run(get_movers(window="30d", level="asset", limit=10, db=base_db))

    movers = result["movers"]
    assert len(movers) == 1, f"expected 1 mover, got {len(movers)}"
    m = movers[0]
    assert m["key"] == "US_STK_MSFT"
    assert abs(m["pct_change"] - 11.1111) < 0.01, f"pct_change={m['pct_change']}"
    assert abs(m["pl_impact_cny"] - 20_000.0) < 0.5, f"pl_impact={m['pl_impact_cny']}"
    assert m["window_covered"] is True


def test_pl_impact_sign_negative_for_price_decline(tmp_path):
    """Price declined over window → pl_impact_cny negative."""
    db_path = tmp_path / "neg.duckdb"
    conn = duckdb.connect(str(db_path))
    for ddl in [_CREATE_HOLDINGS, _CREATE_MARKET_DAILY, _CREATE_ASSET_REGISTRY, _CREATE_TAXONOMY_CLASSES]:
        conn.execute(ddl)

    today = dt.date.today()
    conn.execute("INSERT INTO taxonomy_classes VALUES (1, 'Equity (股票)', NULL, TRUE)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (2, 'US Equity', 1, TRUE)")
    _add_holding(conn, "US_STK_AMZN", "Amazon", 100_000, today)
    conn.execute("INSERT INTO asset_registry VALUES ('US_STK_AMZN', 'US Equity', 'US Equity', TRUE)")
    # p_then=200 (35 days ago), p_now=180 (today) → decline
    _add_price(conn, 1, "AMZN", today - dt.timedelta(days=35), 200.0)
    _add_price(conn, 2, "AMZN", today, 180.0)

    result = asyncio.run(get_movers(window="30d", level="asset", limit=10, db=DuckDBAdapter(conn)))
    conn.close()

    assert len(result["movers"]) == 1
    m = result["movers"][0]
    assert m["pct_change"] < 0, "price declined → pct_change should be negative"
    assert m["pl_impact_cny"] < 0, "price declined → pl_impact_cny should be negative"


# ---------------------------------------------------------------------------
# Tests — unpriced asset exclusion
# ---------------------------------------------------------------------------

def test_unpriced_asset_excluded_and_counted(base_db):
    """Assets without market_daily rows (and <2 closes) are excluded, not silently dropped."""
    result = asyncio.run(get_movers(window="30d", level="asset", limit=10, db=base_db))

    # NOPRICE (0 closes) + ONECLS (1 close) = 2 excluded
    assert result["excluded_unpriced_count"] == 2
    keys = {m["key"] for m in result["movers"]}
    assert "US_STK_NOPRICE" not in keys
    assert "US_STK_ONECLS" not in keys


# ---------------------------------------------------------------------------
# Tests — partial coverage flag (window_covered=False)
# ---------------------------------------------------------------------------

def test_partial_coverage_sets_window_covered_false(tmp_path):
    """Asset has closes only AFTER window_start → window_covered=False, uses earliest close."""
    db_path = tmp_path / "partial.duckdb"
    conn = duckdb.connect(str(db_path))
    for ddl in [_CREATE_HOLDINGS, _CREATE_MARKET_DAILY, _CREATE_ASSET_REGISTRY, _CREATE_TAXONOMY_CLASSES]:
        conn.execute(ddl)

    today = dt.date.today()
    conn.execute("INSERT INTO taxonomy_classes VALUES (1, 'Equity (股票)', NULL, TRUE)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (2, 'US Equity', 1, TRUE)")

    # NEW asset with closes only in the last 5 days (well within 30d window, no close <= window_start)
    _add_holding(conn, "US_STK_NEW", "NewAsset", 50_000, today)
    conn.execute("INSERT INTO asset_registry VALUES ('US_STK_NEW', 'US Equity', 'US Equity', TRUE)")
    # window_start = today - 30d; add closes from today-5d and today
    _add_price(conn, 1, "NEW", today - dt.timedelta(days=5), 90.0)   # earliest → p_then
    _add_price(conn, 2, "NEW", today, 100.0)                          # latest → p_now

    result = asyncio.run(get_movers(window="30d", level="asset", limit=10, db=DuckDBAdapter(conn)))
    conn.close()

    assert len(result["movers"]) == 1
    m = result["movers"][0]
    assert m["window_covered"] is False, "no close ≤ window_start → partial coverage"
    # p_then = earliest = 90, p_now = 100
    assert abs(m["pct_change"] - 11.1111) < 0.01
    assert abs(m["pl_impact_cny"] - 5_000.0) < 0.5


# ---------------------------------------------------------------------------
# Tests — class-level aggregation
# ---------------------------------------------------------------------------

@pytest.fixture
def two_asset_db(tmp_path):
    """
    Two US Equity assets:
      US_STK_A: mv=100_000, p_then=100, p_now=110  → pct=10, impact=+9090.91
      US_STK_B: mv=200_000, p_then=100, p_now=80   → pct=-20, impact=-50000
    Both map to 'US Equity' sub_class → 'Equity (股票)' top_class.
    """
    db_path = tmp_path / "two_asset.duckdb"
    conn = duckdb.connect(str(db_path))
    for ddl in [_CREATE_HOLDINGS, _CREATE_MARKET_DAILY, _CREATE_ASSET_REGISTRY, _CREATE_TAXONOMY_CLASSES]:
        conn.execute(ddl)

    today = dt.date.today()
    p35 = today - dt.timedelta(days=35)

    conn.execute("INSERT INTO taxonomy_classes VALUES (1, 'Equity (股票)', NULL, TRUE)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (2, 'US Equity', 1, TRUE)")

    for aid, name, mv, p_then, p_now, row_id_base in [
        ("US_STK_A", "AssetA", 100_000, 100.0, 110.0, 1),
        ("US_STK_B", "AssetB", 200_000, 100.0, 80.0, 3),
    ]:
        _add_holding(conn, aid, name, mv, today)
        conn.execute(f"INSERT INTO asset_registry VALUES ('{aid}', 'US Equity', 'US Equity', TRUE)")
        sym = aid.split("_")[2]  # 'A' or 'B'
        _add_price(conn, row_id_base, sym, p35, p_then)
        _add_price(conn, row_id_base + 1, sym, today, p_now)

    try:
        yield DuckDBAdapter(conn)
    finally:
        conn.close()


def test_class_aggregation_sums_impacts(two_asset_db):
    """top_class level: pl_impact = Σ individual impacts."""
    result = asyncio.run(get_movers(window="30d", level="top_class", limit=10, db=two_asset_db))

    movers = result["movers"]
    assert len(movers) == 1
    g = movers[0]

    # impact_A = 100_000 * (1 - 100/110) = 100_000/11 ≈ 9090.91
    impact_a = 100_000 * (1 - 100 / 110)
    # impact_B = 200_000 * (1 - 100/80) = 200_000 * (-0.25) = -50_000
    impact_b = 200_000 * (1 - 100 / 80)
    expected_sum = impact_a + impact_b
    assert abs(g["pl_impact_cny"] - expected_sum) < 1.0, (
        f"expected sum≈{expected_sum:.2f}, got {g['pl_impact_cny']}"
    )
    assert g["asset_count"] == 2


def test_class_aggregation_weighted_pct(two_asset_db):
    """pct_change at class level = Σ impact / Σ(mv_now × p_then/p_now) × 100."""
    result = asyncio.run(get_movers(window="30d", level="top_class", limit=10, db=two_asset_db))
    g = result["movers"][0]

    impact_a = 100_000 * (1 - 100 / 110)
    impact_b = 200_000 * (1 - 100 / 80)
    mv_then_a = 100_000 - impact_a   # = 100_000 * (100/110)
    mv_then_b = 200_000 - impact_b   # = 200_000 * (100/80)
    expected_pct = (impact_a + impact_b) / (mv_then_a + mv_then_b) * 100
    assert abs(g["pct_change"] - expected_pct) < 0.01, (
        f"expected pct≈{expected_pct:.4f}, got {g['pct_change']}"
    )


def test_sub_class_level_includes_top_class_field(two_asset_db):
    """sub_class level rows include both sub_class and top_class fields."""
    result = asyncio.run(get_movers(window="30d", level="sub_class", limit=10, db=two_asset_db))
    assert len(result["movers"]) == 1
    g = result["movers"][0]
    assert "top_class" in g
    assert "sub_class" in g


# ---------------------------------------------------------------------------
# Tests — limit + sort by ABS(impact)
# ---------------------------------------------------------------------------

def test_limit_and_sort_by_abs_impact(tmp_path):
    """Results are sorted |pl_impact_cny| DESC and capped by limit."""
    db_path = tmp_path / "limit.duckdb"
    conn = duckdb.connect(str(db_path))
    for ddl in [_CREATE_HOLDINGS, _CREATE_MARKET_DAILY, _CREATE_ASSET_REGISTRY, _CREATE_TAXONOMY_CLASSES]:
        conn.execute(ddl)

    today = dt.date.today()
    p35 = today - dt.timedelta(days=35)

    conn.execute("INSERT INTO taxonomy_classes VALUES (1, 'Equity (股票)', NULL, TRUE)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (2, 'US Equity', 1, TRUE)")

    # 3 assets: impacts 5k, 30k, 20k → sorted order: 30k, 20k, 5k
    assets = [
        ("US_STK_SM", "Small", 50_000, 100.0, 110.0),   # impact ≈ 4545
        ("US_STK_LG", "Large", 300_000, 100.0, 110.0),  # impact ≈ 27272
        ("US_STK_MD", "Medium", 200_000, 100.0, 110.0), # impact ≈ 18181
    ]
    rid = 1
    for aid, name, mv, pt, pn in assets:
        _add_holding(conn, aid, name, mv, today)
        conn.execute(f"INSERT INTO asset_registry VALUES ('{aid}', 'US Equity', 'US Equity', TRUE)")
        sym = aid.split("_")[2]
        _add_price(conn, rid, sym, p35, pt)
        _add_price(conn, rid + 1, sym, today, pn)
        rid += 2

    result = asyncio.run(get_movers(window="30d", level="asset", limit=2, db=DuckDBAdapter(conn)))
    conn.close()

    movers = result["movers"]
    assert len(movers) == 2
    # First must be the largest absolute impact
    assert movers[0]["key"] == "US_STK_LG"
    assert movers[1]["key"] == "US_STK_MD"


# ---------------------------------------------------------------------------
# Tests — 422 on bad params
# ---------------------------------------------------------------------------

def test_invalid_window_returns_422(base_db):
    """Unknown window value → 422 ApiErrorResponse."""
    result = asyncio.run(get_movers(window="99y", level="asset", limit=10, db=base_db))
    # api_error_response returns an ApiErrorResponse (JSONResponse subclass)
    assert hasattr(result, "status_code"), "expected an error response object"
    assert result.status_code == 422


def test_invalid_level_returns_422(base_db):
    """Unknown level value → 422 ApiErrorResponse."""
    result = asyncio.run(get_movers(window="30d", level="galaxy", limit=10, db=base_db))
    assert hasattr(result, "status_code"), "expected an error response object"
    assert result.status_code == 422


# ---------------------------------------------------------------------------
# Tests — response shape
# ---------------------------------------------------------------------------

def test_response_shape(base_db):
    """Response includes required top-level keys."""
    result = asyncio.run(get_movers(window="30d", level="asset", limit=10, db=base_db))
    for key in ("window", "window_start", "level", "movers", "excluded_unpriced_count"):
        assert key in result, f"missing key: {key}"
    assert result["window"] == "30d"
    assert result["level"] == "asset"


def test_shadow_holdings_excluded(tmp_path):
    """is_shadow=TRUE holdings must never appear in movers."""
    db_path = tmp_path / "shadow.duckdb"
    conn = duckdb.connect(str(db_path))
    for ddl in [_CREATE_HOLDINGS, _CREATE_MARKET_DAILY, _CREATE_ASSET_REGISTRY, _CREATE_TAXONOMY_CLASSES]:
        conn.execute(ddl)

    today = dt.date.today()
    p35 = today - dt.timedelta(days=35)

    conn.execute("INSERT INTO taxonomy_classes VALUES (1, 'Equity (股票)', NULL, TRUE)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (2, 'US Equity', 1, TRUE)")

    # One real holding, one shadow
    _add_holding(conn, "US_STK_REAL", "Real", 100_000, today, is_shadow=False)
    _add_holding(conn, "US_STK_SHADOW", "Shadow", 500_000, today, is_shadow=True)
    conn.execute("INSERT INTO asset_registry VALUES ('US_STK_REAL', 'US Equity', 'US Equity', TRUE)")
    conn.execute("INSERT INTO asset_registry VALUES ('US_STK_SHADOW', 'US Equity', 'US Equity', TRUE)")
    _add_price(conn, 1, "REAL", p35, 100.0)
    _add_price(conn, 2, "REAL", today, 110.0)
    _add_price(conn, 3, "SHADOW", p35, 100.0)
    _add_price(conn, 4, "SHADOW", today, 110.0)

    result = asyncio.run(get_movers(window="30d", level="asset", limit=10, db=DuckDBAdapter(conn)))
    conn.close()

    keys = {m["key"] for m in result["movers"]}
    assert "US_STK_SHADOW" not in keys
    assert "US_STK_REAL" in keys
