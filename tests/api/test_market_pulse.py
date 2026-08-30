import pytest
from unittest.mock import MagicMock, patch
from src.api.routes.data import get_dashboard_kpi

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (100000,)
    db.execute.return_value.fetchall.return_value = []
    return db

@patch('src.api.routes.data._db_exists', return_value=True)
def test_market_pulse_bullish(mock_exists, mock_db):
    with patch('src.api.routes.data.load_config') as mock_load_config, \
         patch('src.api.routes.data.sqlite3.connect') as mock_sqlite:
        
        mock_load_config.return_value = {
            'subsystems': {
                'daily_stock_analysis': {
                    'path': '/tmp',
                    'data_sources': {'market_db': 'market.db'}
                }
            }
        }
        
        mock_conn = MagicMock()
        mock_sqlite.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = [
            ('2025-01-02', 4100.0),
            ('2025-01-01', 4000.0)
        ]
        
        import asyncio
        result = asyncio.run(get_dashboard_kpi(db=mock_db))
        
        assert result['market_pulse'] == 2.5
        assert result['market_pulse_sentiment'] == "Bullish"

@patch('src.api.routes.data._db_exists', return_value=True)
def test_market_pulse_bearish(mock_exists, mock_db):
    with patch('src.api.routes.data.load_config') as mock_load_config, \
         patch('src.api.routes.data.sqlite3.connect') as mock_sqlite:
         
        mock_load_config.return_value = {
            'subsystems': { 'daily_stock_analysis': { 'path': '/tmp', 'data_sources': {'market_db': 'market.db'} } }
        }
        mock_conn = MagicMock()
        mock_sqlite.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = [
            ('2025-01-02', 3900.0),
            ('2025-01-01', 4000.0)
        ]
        
        import asyncio
        result = asyncio.run(get_dashboard_kpi(db=mock_db))
        
        assert result['market_pulse'] == -2.5
        assert result['market_pulse_sentiment'] == "Bearish"
