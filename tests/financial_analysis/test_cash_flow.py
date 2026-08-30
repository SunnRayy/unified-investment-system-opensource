"""Tests for cash flow trend analysis."""
import json
from datetime import date
from unittest.mock import MagicMock


def test_parse_monthly_cash_flows():
    """Parse income_expense_monthly rows into monthly totals."""
    from src.financial_analysis.cash_flow import parse_monthly_cash_flows

    rows = [
        ("income_salary", date(2025, 1, 1), json.dumps({"category": "Salary", "amount": 25000.0, "type": "income"})),
        ("income_bonus", date(2025, 1, 1), json.dumps({"category": "Bonus", "amount": 5000.0, "type": "income"})),
        ("expense_rent", date(2025, 1, 1), json.dumps({"category": "Rent", "amount": 8000.0, "type": "expense"})),
        ("income_salary", date(2025, 2, 1), json.dumps({"category": "Salary", "amount": 25000.0, "type": "income"})),
        ("expense_rent", date(2025, 2, 1), json.dumps({"category": "Rent", "amount": 8000.0, "type": "expense"})),
    ]

    result = parse_monthly_cash_flows(rows)
    assert len(result) == 2  # 2 months
    assert result[0]["month"] == "2025-01"
    assert result[0]["total_income"] == 30000.0
    assert result[0]["total_expense"] == 8000.0
    assert result[0]["net"] == 22000.0


def test_calculate_trends():
    """Calculate trend statistics from monthly data."""
    from src.financial_analysis.cash_flow import calculate_trends

    monthly = [
        {"month": "2025-01", "total_income": 30000, "total_expense": 10000, "net": 20000},
        {"month": "2025-02", "total_income": 31000, "total_expense": 11000, "net": 20000},
        {"month": "2025-03", "total_income": 32000, "total_expense": 12000, "net": 20000},
        {"month": "2025-04", "total_income": 33000, "total_expense": 13000, "net": 20000},
        {"month": "2025-05", "total_income": 34000, "total_expense": 14000, "net": 20000},
        {"month": "2025-06", "total_income": 35000, "total_expense": 15000, "net": 20000},
    ]

    trends = calculate_trends(monthly)
    assert "avg_income" in trends
    assert "avg_expense" in trends
    assert "avg_net" in trends
    assert "savings_rate" in trends
    assert trends["avg_income"] > 0
    assert 0 < trends["savings_rate"] <= 100


def test_calculate_trends_empty():
    """Trends return zeros for empty input."""
    from src.financial_analysis.cash_flow import calculate_trends

    trends = calculate_trends([])
    assert trends["avg_income"] == 0
    assert trends["avg_expense"] == 0
    assert trends["avg_net"] == 0


def test_get_cash_flow_analysis_from_db():
    """Integration: fetch from DB and compute trends."""
    from src.financial_analysis.cash_flow import get_cash_flow_analysis

    db = MagicMock()
    db.execute.return_value.fetchall.return_value = [
        ("income_salary", date(2025, 1, 1), json.dumps({"category": "Salary", "amount": 25000.0, "type": "income"})),
        ("expense_rent", date(2025, 1, 1), json.dumps({"category": "Rent", "amount": 8000.0, "type": "expense"})),
        ("income_salary", date(2025, 2, 1), json.dumps({"category": "Salary", "amount": 25000.0, "type": "income"})),
        ("expense_rent", date(2025, 2, 1), json.dumps({"category": "Rent", "amount": 8000.0, "type": "expense"})),
        ("income_salary", date(2025, 3, 1), json.dumps({"category": "Salary", "amount": 26000.0, "type": "income"})),
        ("expense_rent", date(2025, 3, 1), json.dumps({"category": "Rent", "amount": 8500.0, "type": "expense"})),
    ]

    result = get_cash_flow_analysis(db)
    assert "monthly" in result
    assert "trends" in result
    assert len(result["monthly"]) == 3
