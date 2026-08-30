"""Tests for Balance Sheet API endpoints."""
import json
import inspect
from unittest.mock import MagicMock


def _make_db_mock(bs_rows=None, bs_dates=None):
    """Create a mock DB that returns balance sheet data."""
    db = MagicMock()

    def execute_side_effect(query, params=None):
        mock_result = MagicMock()
        q = query.strip().lower()
        if "select distinct snapshot_date" in q:
            mock_result.fetchall.return_value = bs_dates or []
        elif "select record_key, snapshot_date, payload" in q:
            mock_result.fetchall.return_value = bs_rows or []
        elif "select count" in q:
            mock_result.fetchone.return_value = (len(bs_rows) if bs_rows else 0,)
        else:
            mock_result.fetchall.return_value = []
            mock_result.fetchone.return_value = (0,)
        return mock_result

    db.execute.side_effect = execute_side_effect
    return db


def test_balance_sheet_summary_returns_latest_snapshot():
    """GET /api/balance-sheet/summary returns the latest balance sheet snapshot."""
    from src.api.routes.balance_sheet import get_balance_sheet_summary

    import asyncio

    payload = json.dumps({
        "asset_id": "BS_TOTAL_ASSETS",
        "source_system": "Financial_Summary",
        "Total_Assets_Calc_CNY": 5000000.0,
        "Net_Worth_Calc_CNY": 4500000.0,
        "Total_Liabilities_Calc_CNY": 500000.0,
    })
    db = _make_db_mock(
        bs_rows=[("BS_TOTAL_ASSETS", "2026-01-01", payload)],
        bs_dates=[("2026-01-01",), ("2025-12-01",)],
    )

    result = asyncio.run(get_balance_sheet_summary(db=db))
    assert "rows" in result
    assert "snapshot_count" in result


def test_balance_sheet_history_returns_trend():
    """GET /api/balance-sheet/history returns monthly net worth trend."""
    from src.api.routes.balance_sheet import get_balance_sheet_history

    import asyncio

    rows = [
        ("BS_NET_WORTH", "2025-12-01", json.dumps({"Net_Worth_Calc_CNY": 4000000.0})),
        ("BS_NET_WORTH", "2026-01-01", json.dumps({"Net_Worth_Calc_CNY": 4500000.0})),
    ]
    db = _make_db_mock(bs_rows=rows)
    result = asyncio.run(get_balance_sheet_history(db=db))
    assert isinstance(result, list) or "snapshots" in result


def test_balance_sheet_history_default_limit_is_72():
    """Default history window should cover full imported timeline (72 months)."""
    from src.api.routes.balance_sheet import get_balance_sheet_history

    limit_default = inspect.signature(get_balance_sheet_history).parameters["limit"].default
    assert limit_default.default == 72
