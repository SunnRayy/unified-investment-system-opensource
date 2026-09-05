
import pytest
from unittest.mock import MagicMock, patch
from src.api.routes.data import get_dashboard_kpi

@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


def test_kpi_net_worth_uses_latest_per_asset_cte(mock_db):
    """KPI net_worth comes from latest-per-asset CTE (single SUM row)."""

    # New query returns a single row: (total_val,)
    mock_db.execute.return_value.fetchone.return_value = (105000.0,)

    with patch('src.api.routes.data.load_config') as mock_conf:
        mock_conf.return_value = {}
        with patch('src.api.routes.data.calculate_portfolio_risk', return_value={}):
            import asyncio
            result = asyncio.run(get_dashboard_kpi(db=mock_db))

            assert result['net_worth'] == 105000.0


def test_kpi_pnl_24h_is_none(mock_db):
    """pnl_24h is always None — ambiguous with mixed snapshot dates (known limitation)."""

    mock_db.execute.return_value.fetchone.return_value = (100000.0,)

    with patch('src.api.routes.data.load_config'), \
         patch('src.api.routes.data.calculate_portfolio_risk', return_value={}):

        import asyncio
        result = asyncio.run(get_dashboard_kpi(db=mock_db))

        # pnl_24h is intentionally None after the date management fix:
        # with mixed snapshot dates per reader, "previous" total is ambiguous.
        assert result['pnl_24h'] is None


def test_kpi_net_worth_zero_when_no_holdings(mock_db):
    """net_worth is 0.0 when the CTE returns no rows."""

    mock_db.execute.return_value.fetchone.return_value = None

    with patch('src.api.routes.data.load_config'), \
         patch('src.api.routes.data.calculate_portfolio_risk', return_value={}):

        import asyncio
        result = asyncio.run(get_dashboard_kpi(db=mock_db))

        assert result['net_worth'] == 0.0
        assert result['pnl_24h'] is None
