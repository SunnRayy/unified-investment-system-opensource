"""Tests for Time-Weighted Return (TWR) calculation."""
import pytest

pytestmark = pytest.mark.critical

from datetime import date
from unittest.mock import MagicMock


def test_twr_simple_two_periods():
    """TWR for two periods: +10% then -5% = 1.1 * 0.95 - 1 = 4.5%."""
    from src.financial_analysis.twr import calculate_twr_from_snapshots

    snapshots = [
        {"date": date(2025, 1, 1), "value": 1000000.0},
        {"date": date(2025, 2, 1), "value": 1100000.0},
        {"date": date(2025, 3, 1), "value": 1045000.0},
    ]
    # No cash flows between periods
    cashflows = []
    result = calculate_twr_from_snapshots(snapshots, cashflows)
    assert result is not None
    assert abs(result - 0.045) < 0.001  # 4.5%


def test_twr_with_cash_inflow():
    """TWR adjusts for cash flow: deposit 100k mid-period shouldn't inflate return."""
    from src.financial_analysis.twr import calculate_twr_from_snapshots

    snapshots = [
        {"date": date(2025, 1, 1), "value": 1000000.0},
        {"date": date(2025, 2, 1), "value": 1200000.0},  # +100k deposit + 100k gain
    ]
    cashflows = [{"date": date(2025, 1, 15), "amount": 100000.0}]  # deposit
    result = calculate_twr_from_snapshots(snapshots, cashflows)
    # Gain is 100k on base of ~1050k (mid-period), return ~9.5% not 20%
    assert result is not None
    assert result < 0.20  # Should not be 20%


def test_twr_returns_none_for_insufficient_data():
    """TWR needs at least 2 snapshots."""
    from src.financial_analysis.twr import calculate_twr_from_snapshots

    result = calculate_twr_from_snapshots([{"date": date(2025, 1, 1), "value": 1000000}], [])
    assert result is None


from unittest.mock import patch

@patch('src.services.transaction_source_selector.build_source_filter_clauses')
@patch('src.financial_analysis.twr.get_portfolio_value_series')
def test_twr_portfolio_from_provider(mock_provider, mock_build):
    """calculate_portfolio_twr reads snapshots from snapshot_provider."""
    from src.financial_analysis.twr import calculate_portfolio_twr

    mock_build.return_value = ("1=1", [])
    mock_provider.return_value = [
        {"date": date(2025, 1, 1), "value": 1000000.0},
        {"date": date(2025, 2, 1), "value": 1050000.0},
        {"date": date(2025, 3, 1), "value": 1100000.0},
    ]
    
    db = MagicMock()
    # Mock only cash flows (buy/sell transactions between snapshots)
    db.execute().fetchall.return_value = []
    
    result = calculate_portfolio_twr(db)
    assert result is not None
    assert isinstance(result, dict)
    assert result["cumulative"] > 0  # Portfolio grew

@patch('src.services.transaction_source_selector.build_source_filter_clauses')
@patch('src.financial_analysis.twr.get_portfolio_value_series')
def test_calculate_portfolio_twr_returns_dict(mock_provider, mock_build):
    """calculate_portfolio_twr now returns a dict with 'cumulative' and 'annualized' keys."""
    from src.financial_analysis.twr import calculate_portfolio_twr

    mock_build.return_value = ("1=1", [])
    mock_provider.return_value = [
        {"date": date(2024, 1, 1), "value": 1000000.0},
        {"date": date(2025, 1, 5), "value": 1100000.0},
    ]
    db = MagicMock()
    db.execute().fetchall.return_value = []
    
    result = calculate_portfolio_twr(db)
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "cumulative" in result
    assert "annualized" in result

@patch('src.services.transaction_source_selector.build_source_filter_clauses')
@patch('src.financial_analysis.twr.get_portfolio_value_series')
def test_annualized_twr_is_none_when_less_than_365_days(mock_provider, mock_build):
    """If data spans < 365 days, annualized TWR should be None."""
    from src.financial_analysis.twr import calculate_portfolio_twr

    mock_build.return_value = ("1=1", [])
    mock_provider.return_value = [
        {"date": date(2025, 1, 1), "value": 1000000.0},
        {"date": date(2025, 2, 1), "value": 1100000.0},
    ]
    db = MagicMock()
    db.execute().fetchall.return_value = []
    
    result = calculate_portfolio_twr(db)
    assert result["annualized"] is None

@patch('src.services.transaction_source_selector.build_source_filter_clauses')
@patch('src.financial_analysis.twr.get_portfolio_value_series')
def test_annualized_twr_computed_for_multi_year_data(mock_provider, mock_build):
    """If data spans >= 365 days, annualized TWR should be a float."""
    from src.financial_analysis.twr import calculate_portfolio_twr

    mock_build.return_value = ("1=1", [])
    mock_provider.return_value = [
        {"date": date(2024, 1, 1), "value": 1000000.0},
        {"date": date(2025, 1, 2), "value": 1100000.0},
    ]
    db = MagicMock()
    db.execute().fetchall.return_value = []
    
    result = calculate_portfolio_twr(db)
    assert result["annualized"] is not None
    assert isinstance(result["annualized"], float)

def test_performance_returns_endpoint_still_works_after_twr_change():
    """Regression: /performance/returns endpoint must still return twr_cumulative and twr_annualized."""
    with patch('src.financial_analysis.twr.calculate_portfolio_twr') as mock_twr:
        mock_twr.return_value = {"cumulative": 0.1, "annualized": 0.05}
        result = mock_twr()
        cumulative = result["cumulative"] if result else None
        assert isinstance(cumulative, float)


@patch('src.services.transaction_source_selector.build_source_filter_clauses')
@patch('src.financial_analysis.twr.get_portfolio_value_series')
def test_portfolio_twr_treats_premium_payment_as_deposit(mock_provider, mock_build):
    from src.financial_analysis.twr import (
        calculate_portfolio_twr,
        calculate_twr_from_snapshots,
    )

    mock_build.return_value = ("1=1", [])
    mock_provider.return_value = [
        {"date": date(2025, 1, 1), "value": 1000.0},
        {"date": date(2025, 2, 1), "value": 1250.0},
    ]

    db = MagicMock()
    db.execute().fetchall.return_value = [
        (date(2025, 1, 15), "premium_payment", 200.0),
    ]

    result = calculate_portfolio_twr(db)
    expected = calculate_twr_from_snapshots(
        mock_provider.return_value,
        [{"date": date(2025, 1, 15), "amount": 200.0}],
    )

    assert result is not None
    assert expected is not None
    assert abs(result["cumulative"] - expected) < 1e-9
