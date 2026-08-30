import pytest
from src.financial_analysis.risk_calculator import calculate_portfolio_risk

def test_risk_calculation_balanced():
    # 50% Equity, 50% Bonds
    weights = {'股票': 0.5, '固定收益': 0.5}
    metrics = calculate_portfolio_risk(weights)
    
    # Equity Vol 18%, Bond Vol 6% -> Avg 12%
    assert metrics['volatility'] == pytest.approx(12.0)
    assert metrics['volatility_status'] in ["LOW", "MED", "HIGH"]
    assert metrics['sharpe'] > 0

def test_risk_calculation_conservative():
    # 100% Cash
    weights = {'现金': 1.0}
    metrics = calculate_portfolio_risk(weights)
    
    # Cash Vol 0.5%
    assert metrics['volatility'] == pytest.approx(0.5)
    assert metrics['volatility_status'] == "LOW"

def test_risk_calculation_empty():
    metrics = calculate_portfolio_risk({})
    assert metrics['volatility'] == 0
