# tests/test_dsa_sync.py
"""Tests for DSA market data sync with asset ID normalization."""

import pytest

pytestmark = pytest.mark.pipeline

import sqlite3


@pytest.fixture
def mock_dsa_db(tmp_path):
    """Create temporary DSA SQLite database."""
    db_path = tmp_path / "stock_analysis.db"
    conn = sqlite3.connect(db_path)

    conn.execute("""
        CREATE TABLE stock_daily (
            id INTEGER PRIMARY KEY,
            code VARCHAR(10) NOT NULL,
            date DATE NOT NULL,
            open FLOAT,
            high FLOAT,
            low FLOAT,
            close FLOAT,
            volume FLOAT,
            amount FLOAT,
            pct_chg FLOAT,
            data_source VARCHAR(50)
        )
    """)

    conn.executemany("""
        INSERT INTO stock_daily (code, date, open, high, low, close, volume, amount, pct_chg, data_source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        ('900002', '2023-01-01', 4.0, 4.1, 3.9, 4.05, 1000000, 4050000, 1.25, 'akshare'),
        ('900002', '2023-01-02', 4.05, 4.2, 4.0, 4.15, 1100000, 4565000, 2.47, 'akshare'),
        ('900011', '2023-01-01', 1.2, 1.25, 1.18, 1.22, 500000, 610000, 1.67, 'akshare'),
    ])

    conn.commit()
    conn.close()
    return db_path


def _insert_holding(
    connector,
    asset_id,
    quantity,
    currency="CNY",
    price_updated_at=None,
    market_price_unit=None,
    market_value=None,
    is_shadow=False,
):
    connector.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type, quantity,
            market_price_unit, market_value, currency, source_system, is_shadow,
            price_updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-01-20",
            asset_id,
            asset_id,
            "Test",
            quantity,
            market_price_unit,
            market_value,
            currency,
            "PIS",
            is_shadow,
            price_updated_at,
        ),
    )


def test_sync_market_data(mock_dsa_db):
    """Test syncing market data to unified database."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema
    from src.sync.dsa_sync import sync_market_data

    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    config = {
        'subsystems': {
            'daily_stock_analysis': {
                'path': str(mock_dsa_db.parent),
                'data_sources': {'market_db': 'stock_analysis.db'}
            }
        }
    }

    count = sync_market_data(connector, config)
    assert count == 3

    # Verify data
    result = connector.execute(
        "SELECT close FROM market_daily WHERE code = '900002' AND date = '2023-01-02'"
    ).fetchone()
    assert float(result[0]) == 4.15


def test_sync_market_data_normalizes_codes(mock_dsa_db):
    """Test that stock codes are normalized during sync."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema
    from src.sync.dsa_sync import sync_market_data

    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    config = {
        'subsystems': {
            'daily_stock_analysis': {
                'path': str(mock_dsa_db.parent),
                'data_sources': {'market_db': 'stock_analysis.db'}
            }
        }
    }

    sync_market_data(connector, config)

    # Verify codes are properly stored (6-digit format)
    result = connector.execute(
        "SELECT DISTINCT code FROM market_daily ORDER BY code"
    ).fetchall()
    codes = [r[0] for r in result]
    assert '900002' in codes
    assert '900011' in codes


def test_sync_market_data_upsert(mock_dsa_db):
    """Test that sync handles duplicate records via upsert."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema
    from src.sync.dsa_sync import sync_market_data

    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    config = {
        'subsystems': {
            'daily_stock_analysis': {
                'path': str(mock_dsa_db.parent),
                'data_sources': {'market_db': 'stock_analysis.db'}
            }
        }
    }

    # Sync twice
    count1 = sync_market_data(connector, config)
    count2 = sync_market_data(connector, config)

    # Both syncs should report records processed
    assert count1 == 3
    assert count2 == 3

    # But total records should still be 3 (no duplicates)
    result = connector.execute("SELECT COUNT(*) FROM market_daily").fetchone()
    assert result[0] == 3


def test_sync_market_data_missing_db(tmp_path):
    """Test handling of missing DSA database."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema
    from src.sync.dsa_sync import sync_market_data

    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    config = {
        'subsystems': {
            'daily_stock_analysis': {
                'path': str(tmp_path),
                'data_sources': {'market_db': 'nonexistent.db'}
            }
        }
    }

    count = sync_market_data(connector, config)
    assert count == 0


def test_sync_market_data_missing_config():
    """Test handling of missing DSA configuration."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema
    from src.sync.dsa_sync import sync_market_data

    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    config = {'subsystems': {}}

    count = sync_market_data(connector, config)
    assert count == 0


def test_sync_cn_funds_uses_canonical_id_and_stores_short_code(mock_dsa_db):
    """_sync_cn_funds must query asset_registry by canonical_id (not asset_id) and
    store data in market_daily with the short fund code (e.g. '900002'), not the
    full canonical ID ('CN_FUND_900002')."""
    from datetime import date as date_type
    from unittest.mock import patch
    import pandas as pd
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema
    from src.sync.dsa_sync import sync_market_data

    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    # Seed asset_registry with a CN fund using the canonical_id column
    connector.execute("""
        INSERT INTO asset_registry (canonical_id, display_name, asset_class)
        VALUES ('CN_FUND_900002', 'Test CN Fund', 'Equity')
    """)

    config = {
        'subsystems': {
            'daily_stock_analysis': {
                'path': str(mock_dsa_db.parent),
                'data_sources': {'market_db': 'stock_analysis.db'}
            }
        }
    }

    mock_df = pd.DataFrame([{
        'date': date_type(2026, 2, 26),
        'close': 1.234,
        'currency': 'CNY'
    }])

    with patch('src.sync.dsa_sync.MarketDataService') as MockService:
        MockService.return_value.get_market_data.return_value = mock_df
        # Should not raise BinderException about missing asset_id column
        sync_market_data(connector, config)

    # Data should be stored with short code '900002', not 'CN_FUND_900002'
    row = connector.execute(
        "SELECT code, close FROM market_daily WHERE code = '900002' AND date = '2026-02-26'"
    ).fetchone()
    assert row is not None, "CN fund data should be stored with short code '900002'"
    assert float(row[1]) == pytest.approx(1.234)

    # Confirm the full canonical ID was NOT used as the code
    bad_row = connector.execute(
        "SELECT code FROM market_daily WHERE code = 'CN_FUND_900002'"
    ).fetchone()
    assert bad_row is None, "canonical ID should not appear as market_daily.code"


def test_update_holdings_prices_returns_dsa_key(tmp_path):
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema
    from src.sync.dsa_sync import update_holdings_prices

    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    result = update_holdings_prices(connector, fx_rates={"USD": 7.0, "HKD": 0.9})

    assert set(result.keys()) == {"dsa"}


# ---------------------------------------------------------------------------
# Freshness guard: reader-first authority — never overwrite a reader-provided
# price with a market_daily close that is OLDER than the holding's snapshot_date.
# ---------------------------------------------------------------------------

def _setup_freshness_db():
    """Return an in-memory DuckDB connector with schema initialised."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema

    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    return connector


def _seed_gold_holding(connector, snapshot_date: str, price_unit: float, market_value: float):
    """Insert a gold holding (ALTS_Paper_Gold) with given snapshot_date and price."""
    connector.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type, quantity,
            market_price_unit, market_value, currency, source_system, is_shadow,
            price_updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_date,
            "ALTS_Paper_Gold",
            "Paper Gold",
            "Commodity",
            270.5,          # quantity in grams
            price_unit,
            market_value,
            "CNY",
            "Gold_Excel",
            False,
            None,           # price_updated_at: NULL so the close-diff branch can fire
        ),
    )


def _seed_market_daily(connector, market_date: str, close: float):
    """Insert a market_daily row for code='Gold'."""
    connector.execute(
        """
        INSERT INTO market_daily (code, date, close, data_source)
        VALUES (?, ?, ?, ?)
        """,
        ("Gold", market_date, close, "akshare"),
    )


def test_freshness_guard_stale_market_does_not_overwrite_reader_price():
    """market_daily.date OLDER than holdings.snapshot_date must NOT update the row.

    Scenario: reader (Gold Excel) embedded price 910.8 on 2026-07-06; market_daily
    only has 2026-07-01 close 888.61.  The freshness guard must block the update so
    the reader price is preserved.
    """
    from src.sync.dsa_sync import update_holdings_prices

    connector = _setup_freshness_db()
    _seed_gold_holding(connector, snapshot_date="2026-07-06", price_unit=910.8, market_value=246431.0)
    _seed_market_daily(connector, market_date="2026-07-01", close=888.61)

    result = update_holdings_prices(connector, fx_rates={"USD": 7.0, "HKD": 0.9})

    # Count must be 0 — stale market data must not match
    assert result["dsa"] == 0, "stale market_daily must not trigger an update"

    row = connector.execute(
        "SELECT market_price_unit, market_value FROM holdings WHERE asset_id = 'ALTS_Paper_Gold'"
    ).fetchone()
    assert row is not None
    assert float(row[0]) == pytest.approx(910.8), "reader price must be preserved"
    assert float(row[1]) == pytest.approx(246431.0), "reader market_value must be preserved"


def test_freshness_guard_fresh_market_updates_price():
    """market_daily.date >= holdings.snapshot_date must update the row as normal.

    Scenario: reader embedded price on 2026-07-01; market_daily has 2026-07-06
    close 907.77 (fresher).  The update must fire.
    """
    from src.sync.dsa_sync import update_holdings_prices

    connector = _setup_freshness_db()
    QUANTITY = 270.5
    READER_PRICE = 888.61
    FRESH_CLOSE = 907.77
    _seed_gold_holding(
        connector,
        snapshot_date="2026-07-01",
        price_unit=READER_PRICE,
        market_value=QUANTITY * READER_PRICE,
    )
    _seed_market_daily(connector, market_date="2026-07-06", close=FRESH_CLOSE)

    result = update_holdings_prices(connector, fx_rates={"USD": 7.0, "HKD": 0.9})

    assert result["dsa"] == 1, "fresh market_daily must trigger an update"

    row = connector.execute(
        "SELECT market_price_unit, market_value FROM holdings WHERE asset_id = 'ALTS_Paper_Gold'"
    ).fetchone()
    assert row is not None
    assert float(row[0]) == pytest.approx(FRESH_CLOSE), "price must be updated to fresh close"
    expected_mv = QUANTITY * FRESH_CLOSE  # CNY asset — rate 1.0
    assert float(row[1]) == pytest.approx(expected_mv, rel=1e-4), "market_value must reflect fresh close"
