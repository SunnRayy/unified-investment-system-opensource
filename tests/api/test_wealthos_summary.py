
import pytest
from unittest.mock import MagicMock, patch
from src.api.routes.data import get_wealthos_summary
import asyncio

@pytest.fixture
def mock_db():
    return MagicMock()

def test_wealthos_summary_with_xirr(mock_db):
    """Test get_wealthos_summary includes valid XIRR and Lifetime Gain."""
    
    mock_db.execute.return_value.fetchone.return_value = (50,) # total count
    
    with patch('src.api.routes.performance.get_performance_summary') as mock_perf_summary:
        mock_perf_summary.return_value = {
            "total_lifetime_pl": 125000.0,
            "net_worth": 225000.0,
            "total_cost_basis": 100000.0,
            "asset_count": 10
        }
        with patch('src.api.routes.data.calculate_portfolio_xirr') as mock_xirr:
            mock_xirr.return_value = 0.155  # 15.5%
            
            result = asyncio.run(get_wealthos_summary(include_non_rebalanceable=False, db=mock_db))
            
            assert result['total_lifetime_gain'] == 125000.0
            assert result['lifetime_gain_pct'] == 125.0
            assert result['annualized_return'] == 15.5
            assert result['active_asset_count'] == 10
            assert result['total_asset_count'] == 50

def test_wealthos_summary_no_xirr(mock_db):
    """Test get_wealthos_summary handles missing XIRR gracefully."""
    
    mock_db.execute.return_value.fetchone.return_value = (50,) # total count
    
    with patch('src.api.routes.performance.get_performance_summary') as mock_perf_summary:
        mock_perf_summary.return_value = {
            "total_lifetime_pl": 125000.0,
            "net_worth": 200000.0,
            "total_cost_basis": 100000.0,
            "asset_count": 10
        }
        with patch('src.api.routes.data.calculate_portfolio_xirr') as mock_xirr:
            mock_xirr.return_value = None
            
            result = asyncio.run(get_wealthos_summary(include_non_rebalanceable=False, db=mock_db))
            
            assert result['annualized_return'] is None


def test_wealthos_summary_calls_performance_summary_with_db_kwarg(mock_db):
    """Regression: get_wealthos_summary must pass db as keyword, not positional period."""

    observed = {"args": None, "kwargs": None}

    async def _fake_get_performance_summary(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return {
            "net_worth": 1000.0,
            "total_cost_basis": 900.0,
            "total_lifetime_pl": 100.0,
            "asset_count": 3,
        }

    mock_db.execute.return_value.fetchone.return_value = (10,)

    with patch('src.api.routes.performance.get_performance_summary', side_effect=_fake_get_performance_summary):
        with patch('src.api.routes.data.calculate_portfolio_xirr') as mock_xirr:
            mock_xirr.return_value = 0.12
            result = asyncio.run(get_wealthos_summary(include_non_rebalanceable=False, db=mock_db))

    assert observed["args"] == ()
    assert observed["kwargs"] == {
        "period": "all_time",
        "exclude_non_balanceable": False,
        "include_non_rebalanceable": False,
        "db": mock_db
    }
    assert result["total_lifetime_gain"] == 100.0
    assert result["active_asset_count"] == 3
