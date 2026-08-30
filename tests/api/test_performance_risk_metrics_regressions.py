"""Regression tests for /performance/risk-metrics route behavior."""

import json

import duckdb
import pytest
from unittest.mock import MagicMock, patch

from src.api.routes.performance import get_risk_metrics
from src.financial_analysis.metrics import calculate_portfolio_metrics


class DuckDBAdapter:
    def __init__(self, connection: duckdb.DuckDBPyConnection):
        self.connection = connection

    def execute(self, query, params=None):
        if params is None:
            return self.connection.execute(query)
        return self.connection.execute(query, params)


@pytest.mark.asyncio
async def test_risk_metrics_supports_legacy_exclude_param():
    """Backward-compatible query param path should still work."""
    mock_db = MagicMock()
    expected = {
        "max_drawdown": 8.69,
        "sharpe_ratio": 1.63,
        "sortino_ratio": 4.27,
        "calmar_ratio": 3.94,
        "volatility_annual": 17.32,
        "total_return": 500.19,
        "data_points": 74,
    }

    with patch("src.api.routes.performance.fetch_included_asset_ids", return_value=["A"]), patch(
        "src.api.routes.performance.calculate_portfolio_metrics", return_value=expected
    ) as mock_calc:
        result = await get_risk_metrics(
            period="all_time",
            include_non_rebalanceable=None,
            exclude_non_balanceable=True,
            db=mock_db,
        )

    assert result == expected
    _, kwargs = mock_calc.call_args
    assert kwargs["exclude_non_balanceable"] is True
    assert kwargs["include_asset_ids"] == ["A"]


@pytest.mark.asyncio
async def test_risk_metrics_exception_returns_null_shaped_payload():
    """Exception fallback should preserve stable response shape without error key."""
    mock_db = MagicMock()

    with patch(
        "src.api.routes.performance.calculate_portfolio_metrics",
        side_effect=RuntimeError("boom"),
    ):
        result = await get_risk_metrics(
            period="all_time",
            include_non_rebalanceable=False,
            db=mock_db,
        )

    assert result == {
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "max_drawdown": None,
        "calmar_ratio": None,
        "volatility_annual": None,
        "total_return": None,
        "data_points": 0,
    }


@pytest.mark.asyncio
async def test_risk_metrics_route_matches_metrics_calculation_for_fixture(tmp_path):
    """Route output should match calculate_portfolio_metrics on same fixture DB."""
    db_path = tmp_path / "risk_metrics_fixture.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE balance_sheet_monthly (
            snapshot_date DATE,
            payload JSON
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            market_value DOUBLE,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        INSERT INTO balance_sheet_monthly VALUES
        ('2026-01-01', ?),
        ('2026-02-01', ?),
        ('2026-03-01', ?)
        """,
        [
            json.dumps({"合计总资产": 1000.0}),
            json.dumps({"合计总资产": 1100.0}),
            json.dumps({"合计总资产": 1050.0}),
        ],
    )
    conn.execute(
        """
        INSERT INTO holdings VALUES
        ('2026-03-10', 'EQ_A', 1150.0, FALSE)
        """
    )

    db = DuckDBAdapter(conn)
    expected = calculate_portfolio_metrics(db, start_date=None, exclude_non_balanceable=False)
    actual = await get_risk_metrics(
        period="all_time",
        include_non_rebalanceable=True,
        db=db,
    )
    conn.close()

    assert actual == expected
