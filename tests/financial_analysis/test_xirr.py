
import duckdb
import pytest

pytestmark = pytest.mark.critical

from datetime import date, timedelta
from src.financial_analysis.xirr import calculate_xirr, calculate_portfolio_xirr
from unittest.mock import MagicMock, patch

def test_xirr_simple_case():
    """Test XIRR with a simple known case."""
    # Invest 1000 on Jan 1, Receive 1100 on Jan 1 next year. Return should be 10%.
    cashflows = [
        (date(2023, 1, 1), -1000.0), # Outflow
        (date(2024, 1, 1), 1100.0)   # Inflow
    ]
    result = calculate_xirr(cashflows)
    assert result is not None
    assert abs(result - 0.10) < 0.0001

def test_xirr_with_dividends():
    """Test XIRR with interim dividend."""
    # Invest 1000 on Jan 1
    # Receive 50 dividend on July 1
    # Receive 1050 on Jan 1 next year
    # Total return > 10%
    cashflows = [
        (date(2023, 1, 1), -1000.0),
        (date(2023, 7, 1), 50.0),
        (date(2024, 1, 1), 1050.0)
    ]
    result = calculate_xirr(cashflows)
    assert result is not None
    assert result > 0.10

def test_xirr_no_solution():
    """Test XIRR where no solution exists (e.g. all outlfows)."""
    cashflows = [
        (date(2023, 1, 1), -1000.0),
        (date(2024, 1, 1), -100.0)
    ]
    result = calculate_xirr(cashflows)
    assert result is None # Should handle gracefully

def test_xirr_converges_at_high_return_rate():
    """Previously failed with upper=5.0 — should now converge."""
    from datetime import date
    cashflows = [(date(2024, 1, 1), -10.0), (date(2025, 1, 1), 700.0)]
    result = calculate_xirr(cashflows)
    assert result is not None
    assert result > 5.0

@patch('src.services.transaction_source_selector.build_source_filter_clauses')
def test_portfolio_xirr_integration(mock_build):
    """Test calculate_portfolio_xirr with mocked DB."""
    mock_build.return_value = ("1=1", [])
    
    mock_db = MagicMock()
    
    # Mock transactions: 
    # 1. Buy 1000 on T-365
    # 2. Dividend 50 on T-180
    # 3. Sell 0 (Holding) -> Terminal Value needed
    
    today = date.today()
    one_year_ago = today - timedelta(days=365)
    half_year_ago = today - timedelta(days=182)
    
    # Transactions: (date, type, amount_net)
    # Using 'amount_net' for simplicity. 
    # For BUY, amount_net is cost (positive in DB, but outflow for XIRR). 
    # Wait, existing schema says amount_net for BUY is positive? Reference: transactions table.
    # Usually BUY = money spent.
    # We need to clarify sign convention in the implementation.
    # Assumption: DB stores positive amounts. XIRR function handles signs.
    
    mock_db.execute.return_value.fetchall.side_effect = [
        # 1. DISTINCT asset_ids query
        [("A1",)],
        # 2. Transactions query
        [
            ("A1", one_year_ago, 'buy', 1000.0, 'CNY'),
            ("A1", half_year_ago, 'dividend', 50.0, 'CNY'),
        ],
        # 3. Holdings value query (Terminal Value)
        [("A1", "CNY", 0.0, 1100.0)] # Current value
    ]
    
    # We expect XIRR roughly 15% (1000 -> 1100 + 50 mid-year)
    rate = calculate_portfolio_xirr(mock_db)
    
    assert rate is not None
    assert rate > 0.10
    assert rate < 0.20


@patch('src.services.transaction_source_selector.build_source_filter_clauses')
def test_portfolio_xirr_treats_premium_payment_as_outflow(mock_build):
    mock_build.return_value = ("1=1", [])

    mock_db = MagicMock()
    today = date.today()
    one_year_ago = today - timedelta(days=365)
    half_year_ago = today - timedelta(days=182)

    mock_db.execute.return_value.fetchall.side_effect = [
        [("A1",)],
        [
            ("A1", one_year_ago, "buy", 1000.0, "CNY"),
            ("A1", half_year_ago, "premium_payment", 200.0, "CNY"),
        ],
        [("A1", "CNY", 0.0, 1350.0)],
    ]

    rate = calculate_portfolio_xirr(mock_db)
    expected = calculate_xirr(
        [
            (one_year_ago, -1000.0),
            (half_year_ago, -200.0),
            (today, 1350.0),
        ]
    )

    assert rate is not None
    assert expected is not None
    assert abs(rate - expected) < 1e-9


@patch('src.services.transaction_source_selector.build_source_filter_clauses')
def test_portfolio_xirr_treats_dividend_cash_as_inflow(mock_build):
    mock_build.return_value = ("1=1", [])

    mock_db = MagicMock()
    today = date.today()
    one_year_ago = today - timedelta(days=365)
    half_year_ago = today - timedelta(days=182)

    mock_db.execute.return_value.fetchall.side_effect = [
        [("A1",)],
        [
            ("A1", one_year_ago, "buy", 1000.0, "CNY"),
            ("A1", half_year_ago, "dividend_cash", 50.0, "CNY"),
        ],
        [("A1", "CNY", 0.0, 1100.0)],
    ]

    rate = calculate_portfolio_xirr(mock_db)
    expected = calculate_xirr(
        [
            (one_year_ago, -1000.0),
            (half_year_ago, 50.0),
            (today, 1100.0),
        ]
    )

    assert rate is not None
    assert expected is not None
    assert abs(rate - expected) < 1e-9


class DuckDBAdapter:
    def __init__(self, connection: duckdb.DuckDBPyConnection):
        self.connection = connection

    def execute(self, query, params=None):
        if params is None:
            return self.connection.execute(query)
        return self.connection.execute(query, params)


def test_portfolio_xirr_include_asset_ids_excludes_insurance_premiums(tmp_path):
    db_path = tmp_path / "xirr_filter.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            quantity DOUBLE,
            market_price_unit DOUBLE,
            market_value DOUBLE,
            currency VARCHAR,
            source_system VARCHAR,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            transaction_date DATE,
            asset_id VARCHAR,
            transaction_type VARCHAR,
            amount_net DOUBLE,
            currency VARCHAR,
            source_system VARCHAR,
            is_provisional BOOLEAN
        )
        """
    )
    conn.execute(
        """
        INSERT INTO holdings VALUES
            ('2026-01-01', 'EQ1', 1.0, 1300.0, 1300.0, 'CNY', 'TEST', FALSE),
            ('2026-01-01', 'INS1', 1.0, 400.0, 400.0, 'CNY', 'TEST', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO transactions VALUES
            ('2025-01-01', 'EQ1', 'buy', 1000.0, 'CNY', 'TEST', FALSE),
            ('2025-01-01', 'INS1', 'premium_payment', 500.0, 'CNY', 'TEST', FALSE)
        """
    )

    db = DuckDBAdapter(conn)
    xirr_all = calculate_portfolio_xirr(db)
    xirr_equity_only = calculate_portfolio_xirr(db, include_asset_ids=["EQ1"])
    conn.close()

    assert xirr_all is not None
    assert xirr_equity_only is not None
    assert xirr_all != xirr_equity_only
    assert xirr_equity_only > xirr_all


@patch('src.services.transaction_source_selector.build_source_filter_clauses')
@patch('src.financial_analysis.xirr.get_currency_service')
def test_portfolio_xirr_uses_constant_fx_for_mixed_usd_and_cny_assets(mock_currency_service, mock_build, tmp_path):
    mock_build.return_value = ("1=1", [])
    mock_currency_service.return_value.get_latest_rate.return_value = 7.0

    db_path = tmp_path / "xirr_mixed_constant_fx.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            quantity DOUBLE,
            market_price_unit DOUBLE,
            market_value DOUBLE,
            currency VARCHAR,
            source_system VARCHAR,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            transaction_date DATE,
            asset_id VARCHAR,
            transaction_type VARCHAR,
            amount_net DOUBLE,
            currency VARCHAR,
            source_system VARCHAR,
            is_provisional BOOLEAN
        )
        """
    )

    today = date.today()
    one_year_ago = today - timedelta(days=365)
    half_year_ago = today - timedelta(days=182)

    conn.execute(
        """
        INSERT INTO holdings VALUES
            (?, 'US_STK_SGOV', 10.0, 102.0, 7140.0, 'USD', 'Schwab_CSV', FALSE),
            (?, 'CN_FUND_ABC', 1.0, 770.0, 770.0, 'CNY', 'CN_Fund_Excel', FALSE)
        """,
        [today, today],
    )
    conn.execute(
        """
        INSERT INTO transactions VALUES
            (?, 'US_STK_SGOV', 'buy', 1000.0, 'USD', 'Schwab_CSV', FALSE),
            (?, 'US_STK_SGOV', 'dividend_cash', 20.0, 'USD', 'Schwab_CSV', FALSE),
            (?, 'CN_FUND_ABC', 'buy', 700.0, 'CNY', 'CN_Fund_Excel', FALSE)
        """,
        [one_year_ago, half_year_ago, one_year_ago],
    )

    db = DuckDBAdapter(conn)
    rate = calculate_portfolio_xirr(db, include_asset_ids=["US_STK_SGOV", "CN_FUND_ABC"])
    expected = calculate_xirr(
        [
            (one_year_ago, -7000.0),
            (half_year_ago, 140.0),
            (one_year_ago, -700.0),
            (today, 7140.0),
            (today, 770.0),
        ]
    )
    conn.close()

    assert rate is not None
    assert expected is not None
    assert abs(rate - expected) < 1e-9
