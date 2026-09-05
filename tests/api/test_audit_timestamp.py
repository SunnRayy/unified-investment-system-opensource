from unittest.mock import MagicMock
from datetime import datetime
from src.api.routes.data import get_audit_summary

def test_audit_timestamp_iso_format():
    mock_db = MagicMock()
    # Mock return values for the sequence of distinct SQL queries:
    # 1. Total logs
    # 2. Last sync timestamp
    # 3. Unresolved conflicts
    # 4. Circuit breaker status
    
    # We use side_effect to return different values for each execute call
    # Each execute return a cursor-like object (MagicMock) on which fetchone is called
    
    mock_cursor_1 = MagicMock()
    mock_cursor_1.fetchone.return_value = (10,)
    
    mock_cursor_2 = MagicMock()
    mock_cursor_2.fetchone.return_value = (datetime(2025, 1, 1, 12, 0, 0),)
    
    mock_cursor_3 = MagicMock()
    mock_cursor_3.fetchone.return_value = (5,)
    
    mock_cursor_4 = MagicMock()
    mock_cursor_4.fetchone.return_value = ("READY",)
    
    mock_db.execute.side_effect = [
        mock_cursor_1,
        mock_cursor_2,
        mock_cursor_3,
        mock_cursor_4
    ]
    
    import asyncio
    result = asyncio.run(get_audit_summary(mock_db))
    
    assert result['last_sync_timestamp'] == "2025-01-01T12:00:00"
    assert result['total_logs'] == 10
