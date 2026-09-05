"""Tests for stale reader shadow logic."""

import pytest

pytestmark = pytest.mark.pipeline

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.sync.orchestrator import _shadow_stale_reader_holdings


@pytest.fixture
def connector():
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    yield conn
    conn.close()


def test_does_not_shadow_recent_lagging_reader_asset_within_7_days(connector):
    """Lagging reader assets (e.g., QDII T+1/T+2) should stay active."""
    connector.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit, market_value,
            currency, account, source_system, is_shadow
        ) VALUES
        ('2026-02-27', 'CN_FUND_FRESH', 'Fresh Fund', 'Fund',
         10, 'share', 10, 10, 100, 'CNY', 'CN Fund', 'CN_Fund_Excel', FALSE),
        ('2026-02-26', 'CN_FUND_QDII_LAG', 'Lagging QDII', 'Fund',
         20, 'share', 10, 10, 200, 'CNY', 'CN Fund', 'CN_Fund_Excel', FALSE)
        """
    )
    connector.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, amount_net, commission_fee,
            currency, account, memo, source_system, is_provisional
        ) VALUES
        ('2026-02-20', 'CN_FUND_QDII_LAG', 'Lagging QDII', 'buy',
         20, 10, -200, -200, 0, 'CNY', 'CN Fund', NULL, 'CN_Fund_Excel', FALSE)
        """
    )

    _shadow_stale_reader_holdings(connector)

    qdii_shadow = connector.execute(
        """
        SELECT is_shadow
        FROM holdings
        WHERE asset_id='CN_FUND_QDII_LAG' AND snapshot_date='2026-02-26'
        """
    ).fetchone()[0]
    assert qdii_shadow is False


def test_shadows_fully_liquidated_reader_asset_older_than_7_days(connector):
    """Truly stale and fully liquidated reader asset should be shadowed."""
    connector.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit, market_value,
            currency, account, source_system, is_shadow
        ) VALUES
        ('2026-02-27', 'CN_FUND_FRESH', 'Fresh Fund', 'Fund',
         10, 'share', 10, 10, 100, 'CNY', 'CN Fund', 'CN_Fund_Excel', FALSE),
        ('2026-02-15', 'CN_FUND_OLD_CLOSED', 'Old Closed Fund', 'Fund',
         30, 'share', 10, 10, 300, 'CNY', 'CN Fund', 'CN_Fund_Excel', FALSE)
        """
    )
    connector.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, amount_net, commission_fee,
            currency, account, memo, source_system, is_provisional
        ) VALUES
        ('2026-02-10', 'CN_FUND_OLD_CLOSED', 'Old Closed Fund', 'buy',
         30, 10, -300, -300, 0, 'CNY', 'CN Fund', NULL, 'CN_Fund_Excel', FALSE),
        ('2026-02-16', 'CN_FUND_OLD_CLOSED', 'Old Closed Fund', 'sell',
         30, 10, 300, 300, 0, 'CNY', 'CN Fund', NULL, 'CN_Fund_Excel', FALSE)
        """
    )

    _shadow_stale_reader_holdings(connector)

    old_shadow = connector.execute(
        """
        SELECT is_shadow
        FROM holdings
        WHERE asset_id='CN_FUND_OLD_CLOSED' AND snapshot_date='2026-02-15'
        """
    ).fetchone()[0]
    assert old_shadow is True

