"""Tests for Income/Expense API endpoints."""
import json
from unittest.mock import MagicMock
import asyncio


def _make_db_mock(ie_rows=None, ie_dates=None):
    db = MagicMock()

    def execute_side_effect(query, params=None):
        mock_result = MagicMock()
        q = query.strip().lower()
        if "select distinct" in q and "transaction_date" in q:
            mock_result.fetchall.return_value = ie_dates or []
        elif "select record_key" in q:
            mock_result.fetchall.return_value = ie_rows or []
        elif "select count" in q:
            mock_result.fetchone.return_value = (len(ie_rows) if ie_rows else 0,)
        else:
            mock_result.fetchall.return_value = []
            mock_result.fetchone.return_value = (0,)
        return mock_result

    db.execute.side_effect = execute_side_effect
    return db


def test_income_expense_summary_returns_latest():
    from src.api.routes.income_expense import get_income_expense_summary

    payload = json.dumps({"类型": "收入", "金额": 50000.0})
    db = _make_db_mock(
        ie_rows=[("IE_SALARY", "2026-01-01", payload)],
        ie_dates=[("2026-01-01",)],
    )
    result = asyncio.run(get_income_expense_summary(db=db))
    assert "rows" in result
    assert "total_rows" in result


def test_income_expense_history_groups_by_month():
    from src.api.routes.income_expense import get_income_expense_history

    rows = [
        ("IE_SALARY", "2025-12-01", json.dumps({"金额": 45000})),
        ("IE_RENT", "2025-12-01", json.dumps({"金额": -8000})),
        ("IE_SALARY", "2026-01-01", json.dumps({"金额": 50000})),
    ]
    db = _make_db_mock(ie_rows=rows)
    result = asyncio.run(get_income_expense_history(db=db))
    assert "months" in result
