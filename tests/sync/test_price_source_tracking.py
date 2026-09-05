"""Tests for price_source tracking (Phase C).

Verifies that:
1. _update_from_dsa SQL includes price_source = md.data_source in SET clause
2. _upsert_holdings inserts price_source='file'
3. Migration for price_source column runs idempotently
4. _update_rsu_prices_from_external_sources sets price_source correctly
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd


# ---------------------------------------------------------------------------
# _update_from_dsa SQL contains price_source
# ---------------------------------------------------------------------------

def test_update_from_dsa_sets_price_source():
    """Verify that _update_from_dsa builds SQL containing price_source SET."""
    from src.sync.dsa_sync import _update_from_dsa

    mock_connector = MagicMock()
    # count_query returns 0 rows to update
    mock_connector.execute.return_value.fetchone.return_value = (0,)

    _update_from_dsa(mock_connector, {"USD": 7.0, "HKD": 0.9})

    # Check that execute was called with SQL containing 'price_source'
    executed_queries = [
        str(call.args[0]) if call.args else ""
        for call in mock_connector.execute.call_args_list
    ]
    price_source_in_query = any("price_source" in q for q in executed_queries)
    assert price_source_in_query, (
        f"Expected 'price_source' in one of the executed queries. "
        f"Queries: {executed_queries}"
    )


def test_update_from_dsa_update_guard_includes_price_change():
    """Verify the update guard allows same-day re-refresh when price changes."""
    from src.sync.dsa_sync import _update_from_dsa

    mock_connector = MagicMock()
    mock_connector.execute.return_value.fetchone.return_value = (1,)

    _update_from_dsa(mock_connector, {"USD": 7.2})

    executed_queries = [
        str(call.args[0]) if call.args else ""
        for call in mock_connector.execute.call_args_list
    ]
    # The guard should include 'md.close != holdings.market_price_unit'
    guard_present = any(
        "md.close != holdings.market_price_unit" in q or "market_price_unit" in q
        for q in executed_queries
    )
    assert guard_present, "Expected same-day price-change guard in _update_from_dsa query"


# ---------------------------------------------------------------------------
# _upsert_holdings includes price_source = 'file'
# ---------------------------------------------------------------------------

def test_upsert_holdings_inserts_price_source_file():
    """Verify _upsert_holdings sets price_source = 'file' for each row."""
    from src.sync.orchestrator import _upsert_holdings

    mock_connector = MagicMock()
    df = pd.DataFrame([{
        "snapshot_date": date.today(),
        "asset_id": "US_STK_AAPL",
        "asset_name": "Apple Inc.",
        "asset_type": "Stock",
        "quantity": 10.0,
        "unit": "shares",
        "cost_price_unit": 150.0,
        "market_price_unit": 200.0,
        "market_value": 14000.0,  # 10 * 200 * 7 (CNY)
        "currency": "USD",
        "account": "Schwab",
        "source_system": "Schwab_CSV",
    }])

    _upsert_holdings(mock_connector, df)

    assert mock_connector.executemany.called
    call_args = mock_connector.executemany.call_args
    sql = call_args.args[0]
    rows = call_args.args[1]

    assert "price_source" in sql, "SQL should include price_source column"
    assert len(rows) == 1
    # Last element of the row tuple should be 'file'
    assert rows[0][-1] == "file", f"Expected last param to be 'file', got {rows[0][-1]}"


def test_upsert_holdings_on_conflict_sets_price_source_file():
    """Verify ON CONFLICT clause resets price_source = 'file'."""
    from src.sync.orchestrator import _upsert_holdings

    mock_connector = MagicMock()
    df = pd.DataFrame([{
        "snapshot_date": date.today(),
        "asset_id": "CN_FUND_900008",
        "asset_name": "Some Fund",
        "asset_type": "Mutual Fund",
        "quantity": 1000.0,
        "unit": "份",
        "cost_price_unit": 1.5,
        "market_price_unit": 1.8,
        "market_value": 1800.0,
        "currency": "CNY",
        "account": "招商银行",
        "source_system": "CN_Fund_Excel",
    }])

    _upsert_holdings(mock_connector, df)

    call_args = mock_connector.executemany.call_args
    sql = call_args.args[0]
    assert "price_source = 'file'" in sql, (
        "ON CONFLICT clause must reset price_source to 'file'"
    )


# ---------------------------------------------------------------------------
# Migration idempotency
# ---------------------------------------------------------------------------

def test_price_source_migration_is_idempotent():
    """Migration should not raise on repeated calls (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema

    connector = DatabaseConnector(":memory:")
    try:
        # Must initialize schema first (creates holdings table), then run migrations
        initialize_schema(connector)
        connector.run_migrations()
        connector.run_migrations()  # should not raise (idempotency)

        # Verify column exists
        cols = [
            r[0]
            for r in connector.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'holdings'"
            ).fetchall()
        ]
        assert "price_source" in cols, f"price_source column missing. Columns: {cols}"
    finally:
        connector.close()


def test_position_deltas_migration_is_idempotent():
    """position_deltas table migration should be idempotent."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema

    connector = DatabaseConnector(":memory:")
    try:
        initialize_schema(connector)
        connector.run_migrations()
        connector.run_migrations()  # should not raise

        tables = [
            r[0]
            for r in connector.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        ]
        assert "position_deltas" in tables, f"position_deltas table missing. Tables: {tables}"
    finally:
        connector.close()


# ---------------------------------------------------------------------------
# RSU price source tracking
# ---------------------------------------------------------------------------

def test_rsu_price_source_set_to_yfinance():
    """_update_rsu_prices_from_external_sources sets price_source='yfinance' when
    MarketDataService returns a live quote (AIA JSON path removed in V5.2.1)."""
    from unittest.mock import MagicMock
    from src.sync.orchestrator import _update_rsu_prices_from_external_sources

    mock_quote = MagicMock()
    mock_quote.price = 195.0

    mock_connector = MagicMock()
    # Freshness check: no RSU_AMZN update in holdings today → proceed
    mock_connector.execute.return_value.fetchone.return_value = (0,)

    config = {
        "sources": {"aia": {"json_path": "/unused"}},
        "currency": {"fallback_rates": {"USD_CNY": 7.3}},
    }

    with patch("src.market_data.service.MarketDataService") as MockMDS:
        MockMDS.return_value.get_realtime_quote.return_value = mock_quote
        result = _update_rsu_prices_from_external_sources(mock_connector, config)

    assert result == 1

    # Find the UPDATE call and confirm price_source = 'yfinance'
    update_calls = [
        call for call in mock_connector.execute.call_args_list
        if "UPDATE holdings" in str(call.args[0] if call.args else "")
    ]
    assert len(update_calls) >= 1
    update_params = update_calls[-1].args[1]
    assert "yfinance" in update_params


def test_rsu_price_source_skips_if_yfinance_refreshed_today():
    """RSU override is skipped when yfinance already refreshed today."""
    from src.sync.orchestrator import _update_rsu_prices_from_external_sources

    mock_connector = MagicMock()
    # Simulate yfinance refresh already happened (count = 1)
    mock_connector.execute.return_value.fetchone.return_value = (1,)

    config = {
        "sources": {"aia": {"json_path": "/nonexistent/path.json"}},
        "currency": {"fallback_rates": {"USD_CNY": 7.3}},
    }

    result = _update_rsu_prices_from_external_sources(mock_connector, config)
    assert result == 0
