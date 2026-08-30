
from datetime import date
from unittest.mock import MagicMock
from src.financial_analysis.snapshot_provider import get_portfolio_value_series

def test_returns_balance_sheet_values_sorted_by_date():
    # Setup mock DB
    db = MagicMock()
    
    # First call to execute() should be for balance_sheet_monthly
    # Second call should be for current holdings
    db.execute.side_effect = [
        # Balance sheet call
        MagicMock(fetchall=lambda: [
            ('2026-02-01', '{"合计总资产": 2792179.64}'),
            ('2026-01-01', '{"合计总资产": 2820000.00}'),
        ]),
        # Holdings call (empty)
        MagicMock(fetchone=lambda: (None, None))
    ]
    
    result = get_portfolio_value_series(db)
    
    assert len(result) == 2
    assert result[0]['date'] == date(2026, 1, 1)
    assert result[1]['date'] == date(2026, 2, 1)
    assert result[0]['value'] == 2820000.00
    assert result[1]['value'] == 2792179.64

def test_appends_current_holdings_point():
    db = MagicMock()
    # Mock BS (1 point)
    # Mock Holdings (1 point)
    db.execute.side_effect = [
        # Balance sheet call
        MagicMock(fetchall=lambda: [
            ('2026-02-01', '{"合计总资产": 2792179.64}'),
        ]),
        # Holdings call
        MagicMock(fetchone=lambda: ('2026-03-09', 249320.00))
    ]
    
    result = get_portfolio_value_series(db)
    
    assert len(result) == 2
    assert result[0]['date'] == date(2026, 2, 1)
    assert result[1]['date'] == date(2026, 3, 9)
    assert result[1]['value'] == 249320.00

def test_deduplicates_overlapping_dates():
    db = MagicMock()
    # Mock BS and Holdings with SAME DATE
    db.execute.side_effect = [
        # Balance sheet call
        MagicMock(fetchall=lambda: [
            ('2026-03-09', '{"合计总资产": 100.0}'), # Older/less granular info
        ]),
        # Holdings call
        MagicMock(fetchone=lambda: ('2026-03-09', 249320.00))
    ]
    
    result = get_portfolio_value_series(db)
    
    assert len(result) == 1
    assert result[0]['date'] == date(2026, 3, 9)
    assert result[0]['value'] == 249320.00

def test_filters_out_non_balanceable_assets():
    db = MagicMock()
    # BS payload with property and insurance
    db.execute.side_effect = [
        # Balance sheet call
        MagicMock(fetchall=lambda: [
            ('2026-02-01', '{"合计总资产": 1000.0, "Property_Home": 200.0, "Insurance_Peace": 100.0}'),
        ]),
        # Holdings call (already filtered via include_asset_ids in real code, but let's test the toggle)
        MagicMock(fetchone=lambda: ('2026-03-09', 249320.00))
    ]
    
    # When exclude_non_balanceable is True
    result = get_portfolio_value_series(db, exclude_non_balanceable=True)
    
    assert len(result) == 2
    # BS value should be 1000 - 200 - 100 = 700
    assert result[0]['value'] == 700.0
    assert result[1]['value'] == 249320.00

def test_filters_by_start_date():
    db = MagicMock()
    # Mock BS (1 point, date >= 2026-02-01)
    # Mock Holdings (0 points)
    db.execute.side_effect = [
        # Balance sheet call
        MagicMock(fetchall=lambda: [
            ('2026-02-01', '{"合计总资产": 2792179.64}'),
        ]),
        # Holdings call
        MagicMock(fetchone=lambda: (None, None))
    ]
    
    result = get_portfolio_value_series(db, start_date="2026-02-01")
    
    assert len(result) == 1
    assert result[0]['date'] == date(2026, 2, 1)

def test_empty_balance_sheet_falls_back_gracefully():
    db = MagicMock()
    # Mock BS (0 points)
    # Mock Holdings (1 point)
    db.execute.side_effect = [
        # Balance sheet call
        MagicMock(fetchall=lambda: []),
        # Holdings call
        MagicMock(fetchone=lambda: ('2026-03-09', 249320.00))
    ]
    
    result = get_portfolio_value_series(db)
    
    assert len(result) == 1
    assert result[0]['date'] == date(2026, 3, 9)
    assert result[0]['value'] == 249320.00

