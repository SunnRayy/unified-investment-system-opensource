"""Integration-style tests for portfolio attribution calculation."""

import logging
from unittest.mock import patch

import duckdb
import pytest

from src.financial_analysis.attribution import calculate_portfolio_attribution


class DuckDBAdapter:
    def __init__(self, connection: duckdb.DuckDBPyConnection):
        self.connection = connection

    def execute(self, query, params=None):
        if params is None:
            return self.connection.execute(query)
        return self.connection.execute(query, params)


@pytest.fixture
def attribution_db(tmp_path):
    db_path = tmp_path / "attribution_calc.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            market_value DOUBLE,
            cost_price_unit DOUBLE,
            quantity DOUBLE,
            currency VARCHAR,
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
        CREATE TABLE risk_profiles (
            id INTEGER,
            is_active BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE risk_profile_allocations (
            profile_id INTEGER,
            class_id INTEGER,
            target_pct DOUBLE
        )
        """
    )

    try:
        yield DuckDBAdapter(conn)
    finally:
        conn.close()


@patch("src.financial_analysis.attribution.get_today_usd_cny_rate", return_value=7.0)
def test_cash_mixed_null_cost_rows_are_not_overstated(mock_fx, attribution_db):
    """Cash class with NULL costs must not produce inflated return."""
    attribution_db.execute(
        """
        INSERT INTO taxonomy_classes VALUES
        (1, 'Cash', NULL)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO asset_registry VALUES
        ('CASH_A', 'Cash'),
        ('CASH_B', 'Cash')
        """
    )
    attribution_db.execute(
        """
        INSERT INTO holdings VALUES
        ('2026-03-10', 'CASH_A', 50.0, 50.0, 1.0, 'CNY', FALSE),
        ('2026-03-10', 'CASH_B', 50.0, NULL, 1.0, 'CNY', FALSE)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO risk_profiles VALUES (1, TRUE)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO risk_profile_allocations VALUES
        (1, 1, 100.0)
        """
    )

    result = calculate_portfolio_attribution(attribution_db)
    cash = next(c for c in result["classes"] if c["class"] == "Cash")
    assert cash["portfolio_return"] == 0.0
    assert result["portfolio_return"] == 0.0


@patch("src.financial_analysis.attribution.get_today_usd_cny_rate", return_value=7.0)
def test_cash_all_null_cost_rows_return_zero_not_nan(mock_fx, attribution_db):
    """All-NULL cash costs should safely yield zero return."""
    attribution_db.execute(
        """
        INSERT INTO taxonomy_classes VALUES
        (1, 'Cash', NULL)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO asset_registry VALUES
        ('CASH_A', 'Cash'),
        ('CASH_B', 'Cash')
        """
    )
    attribution_db.execute(
        """
        INSERT INTO holdings VALUES
        ('2026-03-10', 'CASH_A', 80.0, NULL, 1.0, 'CNY', FALSE),
        ('2026-03-10', 'CASH_B', 20.0, NULL, 1.0, 'CNY', FALSE)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO risk_profiles VALUES (1, TRUE)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO risk_profile_allocations VALUES
        (1, 1, 100.0)
        """
    )

    result = calculate_portfolio_attribution(attribution_db)
    cash = next(c for c in result["classes"] if c["class"] == "Cash")
    assert cash["portfolio_return"] == 0.0
    assert result["portfolio_return"] == 0.0


@patch("src.financial_analysis.attribution.get_today_usd_cny_rate", return_value=7.0)
def test_benchmark_subclass_targets_roll_up_to_top_class(mock_fx, attribution_db):
    """Benchmark return should be non-zero when subclass targets map to top class."""
    attribution_db.execute(
        """
        INSERT INTO taxonomy_classes VALUES
        (1, 'Equity', NULL),
        (2, 'CN Equity', 1)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO asset_registry VALUES
        ('EQ_A', 'Equity')
        """
    )
    attribution_db.execute(
        """
        INSERT INTO holdings VALUES
        ('2026-03-10', 'EQ_A', 110.0, 100.0, 1.0, 'CNY', FALSE)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO risk_profiles VALUES (1, TRUE)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO risk_profile_allocations VALUES
        (1, 2, 100.0)
        """
    )

    result = calculate_portfolio_attribution(attribution_db)
    assert result["benchmark_return"] > 0.09
    equity = next(c for c in result["classes"] if c["class"] == "Equity")
    assert equity["benchmark_weight"] == 1.0


@patch("src.financial_analysis.attribution.get_today_usd_cny_rate", return_value=7.0)
def test_warns_when_portfolio_and_benchmark_classes_do_not_intersect(
    mock_fx, attribution_db, caplog
):
    """Original failure mode should be detectable via logs."""
    attribution_db.execute(
        """
        INSERT INTO taxonomy_classes VALUES
        (1, 'Equity', NULL),
        (2, 'CN Equity', NULL)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO asset_registry VALUES
        ('EQ_A', 'Equity')
        """
    )
    attribution_db.execute(
        """
        INSERT INTO holdings VALUES
        ('2026-03-10', 'EQ_A', 110.0, 100.0, 1.0, 'CNY', FALSE)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO risk_profiles VALUES (1, TRUE)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO risk_profile_allocations VALUES
        (1, 2, 100.0)
        """
    )

    with caplog.at_level(logging.WARNING):
        result = calculate_portfolio_attribution(attribution_db)

    assert result is not None
    assert "empty class intersection" in caplog.text.lower()


@patch("src.financial_analysis.attribution.get_today_usd_cny_rate", return_value=7.0)
def test_attribution_usd_asset_cost_basis_uses_fx(mock_fx, attribution_db):
    """USD asset cost_basis must be multiplied by FX rate before comparing to CNY market_value.

    Setup: 1 share of a USD Equity, cost_price_unit=100 USD, market_value=770 CNY (≈ 110 USD @ 7).
    Expected: cost_basis_cny = 100 * 1 * 7.0 = 700 CNY.
    Return = (770 - 700) / 700 ≈ 0.10  (not (770 - 100) / 100 = 6.7 which is the bug).
    """
    attribution_db.execute(
        """
        INSERT INTO taxonomy_classes VALUES
        (1, 'Equity', NULL),
        (2, 'US Equity', 1)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO asset_registry VALUES
        ('US_STK_AAPL', 'US Equity')
        """
    )
    # market_value in CNY (770 CNY ≈ 110 USD @ 7.0), cost_price_unit in USD (100 USD/share)
    attribution_db.execute(
        """
        INSERT INTO holdings VALUES
        ('2026-03-10', 'US_STK_AAPL', 770.0, 100.0, 1.0, 'USD', FALSE)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO risk_profiles VALUES (1, TRUE)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO risk_profile_allocations VALUES
        (1, 1, 100.0)
        """
    )

    result = calculate_portfolio_attribution(attribution_db)
    assert result is not None
    equity = next(c for c in result["classes"] if c["class"] == "Equity")

    # cost_basis_cny = 100 USD * 1 share * 7.0 fx = 700 CNY
    # return = (770 - 700) / 700 ≈ 0.10
    # The buggy path would give: cost_basis = 100 (raw USD), return = (770 - 100) / 100 = 6.7
    assert abs(equity["portfolio_return"] - 0.10) < 0.01, (
        f"Expected ~10% return for USD asset with FX, got {equity['portfolio_return']:.4f}. "
        "Bug: cost_basis computed in raw USD instead of CNY."
    )
    # Confirm the bug value is NOT present
    assert equity["portfolio_return"] < 1.0, (
        f"Return of {equity['portfolio_return']:.2f} looks like raw USD/CNY mix (bug)."
    )


@patch("src.financial_analysis.attribution.get_today_usd_cny_rate", return_value=7.0)
def test_attribution_cny_asset_unchanged(mock_fx, attribution_db):
    """CNY assets must be unaffected by the FX fix — cost_basis stays cost_price_unit * quantity.

    Setup: 10 shares of a CNY equity, cost_price_unit=50 CNY, market_value=600 CNY.
    Expected: cost_basis_cny = 50 * 10 = 500 CNY. Return = (600 - 500) / 500 = 0.20.
    """
    attribution_db.execute(
        """
        INSERT INTO taxonomy_classes VALUES
        (1, 'Equity', NULL),
        (2, 'CN Equity', 1)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO asset_registry VALUES
        ('CN_FUND_A', 'CN Equity')
        """
    )
    attribution_db.execute(
        """
        INSERT INTO holdings VALUES
        ('2026-03-10', 'CN_FUND_A', 600.0, 50.0, 10.0, 'CNY', FALSE)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO risk_profiles VALUES (1, TRUE)
        """
    )
    attribution_db.execute(
        """
        INSERT INTO risk_profile_allocations VALUES
        (1, 1, 100.0)
        """
    )

    result = calculate_portfolio_attribution(attribution_db)
    assert result is not None
    equity = next(c for c in result["classes"] if c["class"] == "Equity")

    # cost_basis = 50 CNY/share * 10 shares = 500 CNY (no FX adjustment for CNY)
    # return = (600 - 500) / 500 = 0.20
    assert abs(equity["portfolio_return"] - 0.20) < 0.001, (
        f"Expected 20% return for CNY equity, got {equity['portfolio_return']:.4f}."
    )
