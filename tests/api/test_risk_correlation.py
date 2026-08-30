import asyncio
from datetime import date, timedelta

import duckdb


class DuckDBAdapter:
    def __init__(self, connection: duckdb.DuckDBPyConnection):
        self.connection = connection

    def execute(self, query, params=None):
        if params is None:
            return self.connection.execute(query)
        return self.connection.execute(query, params)


def _create_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE holdings (
            asset_id VARCHAR,
            snapshot_date DATE,
            market_value DOUBLE,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE asset_registry (
            canonical_id VARCHAR,
            asset_class VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE taxonomy_classes (
            id INTEGER,
            name VARCHAR,
            parent_id INTEGER
        )
        """
    )

    conn.execute(
        """
        INSERT INTO taxonomy_classes VALUES
            (1, 'Equity', NULL),
            (2, 'Fixed Income', NULL),
            (3, 'Alternative', NULL),
            (11, 'CN Equity', 1),
            (12, 'US Equity', 1),
            (21, 'US Bonds', 2),
            (31, 'Crypto', 3)
        """
    )
    conn.execute(
        """
        INSERT INTO asset_registry VALUES
            ('CN_STOCK_000001', 'CN Equity'),
            ('US_STK_AAPL', 'US Equity'),
            ('US_BOND_TLT', 'US Bonds'),
            ('CRYPTO_BTC', 'Crypto')
        """
    )


def _insert_series(
    conn: duckdb.DuckDBPyConnection,
    asset_id: str,
    start: date,
    values: list[float],
    *,
    is_shadow: bool = False,
) -> None:
    rows = []
    for i, value in enumerate(values):
        rows.append((asset_id, start + timedelta(days=i), float(value), is_shadow))
    conn.executemany("INSERT INTO holdings VALUES (?, ?, ?, ?)", rows)


def _make_sparse_db() -> DuckDBAdapter:
    conn = duckdb.connect(":memory:")
    _create_schema(conn)
    conn.execute(
        """
        INSERT INTO holdings VALUES
            ('CN_STOCK_000001', '2026-01-01', 100.0, FALSE),
            ('CN_STOCK_000001', '2026-01-02', 110.0, FALSE),
            ('US_BOND_TLT',     '2026-01-05', 200.0, FALSE),
            ('US_BOND_TLT',     '2026-01-06', 210.0, FALSE)
        """
    )
    return DuckDBAdapter(conn)


def _make_dense_db_with_shadow_fallback() -> DuckDBAdapter:
    conn = duckdb.connect(":memory:")
    _create_schema(conn)
    _insert_series(conn, "CN_STOCK_000001", date(2026, 1, 1), [100, 108, 104, 116, 113, 121, 119, 123, 125, 129, 131, 134])
    _insert_series(conn, "US_BOND_TLT", date(2026, 1, 1), [200, 201, 198, 204, 205, 207, 208, 209, 210, 211, 213, 214])
    _insert_series(conn, "CN_STOCK_000001", date(2025, 12, 31), [98], is_shadow=True)
    _insert_series(conn, "US_BOND_TLT", date(2025, 12, 31), [199], is_shadow=True)
    return DuckDBAdapter(conn)


def _make_jump_outlier_db() -> DuckDBAdapter:
    """Two classes have synchronized structural jumps; robust pipeline should down-weight them."""
    conn = duckdb.connect(":memory:")
    _create_schema(conn)
    start = date(2026, 1, 1)

    cash_values = [100, 101, 102, 103, 104, 520, 530, 535, 538, 2200, 2205, 2210, 2215]
    eq_values = [200, 202, 204, 206, 208, 900, 915, 925, 935, 3800, 3810, 3820, 3835]
    bond_values = [300, 301, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 312]

    _insert_series(conn, "CN_STOCK_000001", start, eq_values)
    _insert_series(conn, "US_STK_AAPL", start, eq_values)
    _insert_series(conn, "US_BOND_TLT", start, bond_values)
    _insert_series(conn, "CRYPTO_BTC", start, cash_values)
    return DuckDBAdapter(conn)


def _make_mad_zero_db() -> DuckDBAdapter:
    """Near-constant class with two extreme jumps, checking MAD=0 handling."""
    conn = duckdb.connect(":memory:")
    _create_schema(conn)
    start = date(2026, 1, 1)

    cash_values = [
        100, 100, 100, 100, 100,
        360, 360, 360, 360, 360,
        1300, 1300, 1300, 1300, 1300,
    ]
    eq_values = [
        200, 202, 204, 206, 208,
        210, 212, 214, 216, 218,
        220, 222, 224, 226, 228,
    ]
    bond_values = [
        300, 301, 302, 303, 304,
        305, 306, 307, 308, 309,
        310, 311, 312, 313, 314,
    ]

    _insert_series(conn, "CRYPTO_BTC", start, cash_values)      # Crypto class
    _insert_series(conn, "US_STK_AAPL", start, eq_values)       # US Equity
    _insert_series(conn, "US_BOND_TLT", start, bond_values)     # US Bonds
    return DuckDBAdapter(conn)


def _make_per_class_masking_db() -> DuckDBAdapter:
    """Class A jumps on date T; B/C should still keep T for their pair overlap."""
    conn = duckdb.connect(":memory:")
    _create_schema(conn)
    start = date(2026, 1, 1)

    class_a = [100, 102, 104, 106, 108, 800, 810, 820, 830, 840, 850]
    class_b = [200, 204, 208, 212, 216, 220, 224, 228, 232, 236, 240]
    class_c = [300, 303, 306, 309, 312, 320, 324, 328, 332, 336, 340]

    _insert_series(conn, "CRYPTO_BTC", start, class_a)          # Alternative
    _insert_series(conn, "US_STK_AAPL", start, class_b)         # Equity
    _insert_series(conn, "US_BOND_TLT", start, class_c)         # Fixed Income
    return DuckDBAdapter(conn)


def _cell(result: dict, row_asset: str, col_asset: str):
    row = next(r for r in result["matrix"] if r["asset"] == row_asset)
    return row["correlations"][col_asset]


def _off_diag_values(result: dict):
    values = []
    for row in result["matrix"]:
        for col_asset, cell in row["correlations"].items():
            if col_asset == row["asset"] or cell is None:
                continue
            values.append(cell["value"])
    return values


def test_risk_correlation_sparse_returns_null_pairs_with_metadata():
    from src.api.routes.data import get_risk_correlation

    result = asyncio.run(
        get_risk_correlation(
            level="top",
            include_non_rebalanceable=True,
            db=_make_sparse_db(),
        )
    )

    assert result["method"] == "empirical_holdings"
    assert result["min_overlap_periods"] == 8
    assert result["total_pairs"] > 0
    assert result["insufficient_pairs"] > 0
    assert any(v is None for v in _off_diag_values(result))


def test_risk_correlation_dense_returns_non_null_offdiag():
    from src.api.routes.data import get_risk_correlation

    result = asyncio.run(
        get_risk_correlation(
            level="top",
            include_non_rebalanceable=True,
            db=_make_dense_db_with_shadow_fallback(),
        )
    )

    off_diag = _off_diag_values(result)
    assert len(off_diag) > 0
    assert any(v is not None for v in off_diag)


def test_risk_correlation_top_and_sub_have_different_labels():
    from src.api.routes.data import get_risk_correlation

    db = _make_dense_db_with_shadow_fallback()
    top = asyncio.run(get_risk_correlation(level="top", include_non_rebalanceable=True, db=db))
    sub = asyncio.run(get_risk_correlation(level="sub", include_non_rebalanceable=True, db=db))

    assert set(top["assets"]) == {"Equity", "Fixed Income"}
    assert set(sub["assets"]) == {"CN Equity", "US Bonds"}
    assert set(top["assets"]) != set(sub["assets"])


def test_risk_correlation_outlier_sensitivity_reduced():
    from src.api.routes.data import get_risk_correlation

    db = _make_jump_outlier_db()
    result = asyncio.run(
        get_risk_correlation(
            level="sub",
            include_non_rebalanceable=True,
            db=db,
        )
    )

    cell = _cell(result, "Crypto", "US Equity")
    assert cell is not None
    assert cell["value"] is not None
    assert result["excluded_jump_points_count"] > 0

    # Raw Pearson on unfiltered returns should be more extreme.
    raw_rows = db.execute(
        """
        SELECT snapshot_date, asset_class, SUM(market_value) AS total_value
        FROM (
            SELECT
                h.snapshot_date,
                r.asset_class,
                h.market_value
            FROM holdings h
            JOIN asset_registry r ON h.asset_id = r.canonical_id
            WHERE h.is_shadow = FALSE
        ) t
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).fetchall()
    pivot_data: dict = {}
    for snapshot_date, asset_class, total_value in raw_rows:
        pivot_data.setdefault(snapshot_date, {})[asset_class] = float(total_value or 0.0)
    import pandas as pd
    import numpy as np

    raw_pivot = pd.DataFrame(pivot_data).T.sort_index()
    raw_returns = raw_pivot.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    raw_pair = raw_returns[["Crypto", "US Equity"]].dropna()
    raw_corr = raw_pair["Crypto"].corr(raw_pair["US Equity"])
    assert abs(cell["value"]) <= abs(raw_corr) - 0.05


def test_risk_correlation_mad_zero_does_not_overflag():
    from src.api.routes.data import get_risk_correlation

    result = asyncio.run(
        get_risk_correlation(
            level="sub",
            include_non_rebalanceable=True,
            db=_make_mad_zero_db(),
        )
    )

    excluded_by_class = result["excluded_jump_points_by_class"]
    assert excluded_by_class.get("Crypto", 0) == 2
    assert excluded_by_class.get("US Equity", 0) == 0
    assert excluded_by_class.get("US Bonds", 0) == 0


def test_risk_correlation_per_class_masking_not_global():
    from src.api.routes.data import get_risk_correlation

    result = asyncio.run(
        get_risk_correlation(
            level="top",
            include_non_rebalanceable=True,
            db=_make_per_class_masking_db(),
        )
    )

    eq_bond_cell = _cell(result, "Equity", "Fixed Income")
    assert eq_bond_cell is not None
    assert eq_bond_cell["overlap"] >= 10
