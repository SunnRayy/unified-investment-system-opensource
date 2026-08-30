"""Tests for historical portfolio risk metrics."""
from datetime import date
from unittest.mock import MagicMock


def test_calculate_returns_from_values():
    """Simple returns from portfolio value series."""
    from src.financial_analysis.metrics import calculate_returns

    values = [100.0, 110.0, 104.5, 115.0]
    returns = calculate_returns(values)
    assert len(returns) == 3
    assert abs(returns[0] - 0.10) < 0.001  # +10%
    assert abs(returns[1] - (-0.05)) < 0.001  # -5%
    assert abs(returns[2] - 0.1004785) < 0.001  # +10.05%


def test_calculate_returns_empty():
    """Returns empty list for insufficient data."""
    from src.financial_analysis.metrics import calculate_returns

    assert calculate_returns([]) == []
    assert calculate_returns([100.0]) == []


def test_max_drawdown():
    """Max drawdown from peak to trough."""
    from src.financial_analysis.metrics import max_drawdown

    values = [100.0, 120.0, 90.0, 110.0]  # Peak 120, trough 90 = -25%
    dd = max_drawdown(values)
    assert abs(dd - 0.25) < 0.001


def test_max_drawdown_no_drawdown():
    """No drawdown for monotonically increasing series."""
    from src.financial_analysis.metrics import max_drawdown

    values = [100.0, 110.0, 120.0, 130.0]
    dd = max_drawdown(values)
    assert dd == 0.0


def test_sharpe_ratio():
    """Sharpe ratio = (mean_return - rfr) / std_return, annualized."""
    from src.financial_analysis.metrics import sharpe_ratio

    # Monthly returns of 1% with 2% std, rfr = 3%/year
    returns = [0.01] * 12
    sr = sharpe_ratio(returns, risk_free_rate=0.03, periods_per_year=12)
    # Mean annualized = 12%, std annualized = 2% * sqrt(12) ≈ 6.93%
    # Sharpe = (12% - 3%) / 6.93% ≈ 1.30
    assert sr is not None
    assert sr > 1.0


def test_sharpe_ratio_insufficient_data():
    """Sharpe ratio returns None for < 2 data points."""
    from src.financial_analysis.metrics import sharpe_ratio

    assert sharpe_ratio([], risk_free_rate=0.03) is None
    assert sharpe_ratio([0.01], risk_free_rate=0.03) is None


def test_sortino_ratio():
    """Sortino ratio uses downside deviation only."""
    from src.financial_analysis.metrics import sortino_ratio

    # Mix of positive and negative returns
    returns = [0.05, -0.02, 0.03, -0.04, 0.06, -0.01]
    sr = sortino_ratio(returns, risk_free_rate=0.03, periods_per_year=12)
    assert sr is not None


def test_calmar_ratio():
    """Calmar ratio = annualized return / max drawdown."""
    from src.financial_analysis.metrics import calmar_ratio

    values = [100.0, 110.0, 90.0, 105.0, 120.0]
    cr = calmar_ratio(values, periods_per_year=12)
    assert cr is not None
    # Checking plan content:
    # 178:     cr = calmar_ratio(values, periods_per_year=12)
    # 179:     assert cr is not None
    # 180:     assert cr > 0  # Ended higher, so positive
    # I should use 'cr'
    assert cr is not None
    assert cr > 0


def test_calmar_ratio_no_drawdown():
    """Calmar ratio returns None if no drawdown."""
    from src.financial_analysis.metrics import calmar_ratio

    values = [100.0, 110.0, 120.0]
    cr = calmar_ratio(values, periods_per_year=12)
    assert cr is None  # Division by zero, undefined


from unittest.mock import patch

@patch('src.financial_analysis.metrics.get_portfolio_value_series')
def test_calculate_portfolio_metrics(mock_provider):
    """Integration: calculate all metrics from DB snapshots."""
    from src.financial_analysis.metrics import calculate_portfolio_metrics

    # Return monthly net worth snapshots
    mock_provider.return_value = [
        {"date": date(2025, 1, 1), "value": 1000000.0},
        {"date": date(2025, 2, 1), "value": 1050000.0},
        {"date": date(2025, 3, 1), "value": 1020000.0},
        {"date": date(2025, 4, 1), "value": 1080000.0},
        {"date": date(2025, 5, 1), "value": 1100000.0},
        {"date": date(2025, 6, 1), "value": 1070000.0},
    ]

    db = MagicMock()
    result = calculate_portfolio_metrics(db)
    assert "max_drawdown" in result
    assert "sharpe_ratio" in result
    assert "sortino_ratio" in result
    assert "calmar_ratio" in result
    assert "volatility_annual" in result
    assert "total_return" in result


def test_calmar_uses_twr_numerator_not_deposit_inflated_simple_return():
    """Calmar must = annualized TWR / |max drawdown| (the same TWR shown in the
    AI-advisor context), NOT the deposit-inflated simple return. The simple-return
    numerator made Calmar ~4-5 instead of ~1-2 (owner report 2026-06-28)."""
    from unittest.mock import patch
    import src.financial_analysis.metrics as M

    # Series 100 -> 110 -> 100 -> 130: peak-to-trough drawdown = (110-100)/110 = 9.09%.
    series = [{"value": 100.0}, {"value": 110.0}, {"value": 100.0}, {"value": 130.0}]

    with patch.object(M, "get_portfolio_value_series", return_value=series), patch(
        "src.financial_analysis.twr.calculate_portfolio_twr",
        return_value={"annualized": 0.20, "cumulative": 0.30},
    ):
        m = M.calculate_portfolio_metrics(MagicMock(), exclude_non_balanceable=True)

    dd_pct = m["max_drawdown"]
    assert dd_pct == round((110 - 100) / 110 * 100, 2)  # 9.09
    # Calmar reconciles with the TWR numerator and the displayed max drawdown.
    assert m["calmar_ratio"] == round(0.20 / (dd_pct / 100), 2)  # ~2.2
    # And is NOT the old deposit-inflated value (simple return 30% over 3 periods
    # annualized would push Calmar well above 15).
    assert m["calmar_ratio"] < 5.0


def test_calmar_is_none_when_twr_unavailable():
    """If TWR can't be computed (e.g. no transactions table), Calmar is None
    rather than silently reverting to the inflated simple-return value."""
    from unittest.mock import patch
    import src.financial_analysis.metrics as M

    series = [{"value": 100.0}, {"value": 110.0}, {"value": 100.0}]
    with patch.object(M, "get_portfolio_value_series", return_value=series), patch(
        "src.financial_analysis.twr.calculate_portfolio_twr",
        side_effect=RuntimeError("no transactions table"),
    ):
        m = M.calculate_portfolio_metrics(MagicMock())
    assert m["calmar_ratio"] is None
    assert m["max_drawdown"] is not None  # other metrics still computed
