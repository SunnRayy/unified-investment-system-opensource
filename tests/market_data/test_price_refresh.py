from datetime import date, datetime, timedelta
from typing import Optional
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_db
from src.api.main import app
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.market_data.fetchers.base import UnsupportedCodeError
from src.market_data.fetchers.types import RealtimeQuote
from src.market_data.fetchers.yfinance_fetcher import fetch_fx_rates
from src.market_data.service import MarketDataService


def _make_quote(code: str, price: float, as_of: date = date(2026, 3, 27)) -> RealtimeQuote:
    return RealtimeQuote(
        code=code,
        price=price,
        change_pct=None,
        volume=None,
        timestamp=datetime(2026, 3, 27, 15, 30, 0),
        source="test_feed",
        as_of_date=as_of,
    )


TEST_FX_RATES = {"USD": 7.0, "HKD": 0.9}


def _seed_holdings(conn: DatabaseConnector, asset_ids: list[str]) -> None:
    rows = [
        ("2026-03-27", asset_id, "test_source", 1.0, False)
        for asset_id in asset_ids
    ]
    conn.executemany(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, source_system, quantity, is_shadow
        ) VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )


def _seed_holding(
    conn: DatabaseConnector,
    asset_id: str,
    *,
    quantity: float = 1.0,
    market_price_unit: Optional[float] = None,
    cost_price_unit: Optional[float] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, source_system, quantity, is_shadow,
            market_price_unit, cost_price_unit
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-03-27",
            asset_id,
            "test_source",
            quantity,
            False,
            market_price_unit,
            cost_price_unit,
        ),
    )


@pytest.fixture
def db_conn():
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    conn.run_migrations()
    yield conn
    conn.close()


@pytest.fixture
def api_client():
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    conn.run_migrations()

    def override_get_db():
        return conn

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        conn.close()


def test_refresh_happy_path(db_conn):
    _seed_holdings(db_conn, ["US_STK_AMZN", "US_ETF_SPY"])
    service = MarketDataService()
    fx_rates = {"USD": 7.1234, "HKD": 0.9123}

    with patch.object(service, "get_realtime_quote") as mock_quote, patch(
        "src.sync.dsa_sync._update_from_dsa", return_value=4
    ) as mock_update:
        mock_quote.side_effect = lambda asset_id: {
            "US_STK_AMZN": _make_quote("US_STK_AMZN", 201.25),
            "US_ETF_SPY": _make_quote("US_ETF_SPY", 510.75),
        }[asset_id]

        result = service.refresh_portfolio_prices(db_conn, fx_rates=fx_rates)

    rows = db_conn.execute(
        "SELECT code, close, date FROM market_daily ORDER BY code"
    ).fetchall()

    assert result["refreshed"] == 2
    assert result["skipped"] == 0
    assert result["errors"] == 0
    assert result["holdings_updated"] == 4
    assert result["fx_rates"] == fx_rates
    assert sorted(result["refreshed_assets"], key=lambda item: item["asset_id"]) == [
        {
            "asset_id": "US_ETF_SPY",
            "code": "SPY",
            "market": "us",
            "price": 510.75,
            "as_of_date": "2026-03-27",
            "source": "test_feed",
        },
        {
            "asset_id": "US_STK_AMZN",
            "code": "AMZN",
            "market": "us",
            "price": 201.25,
            "as_of_date": "2026-03-27",
            "source": "test_feed",
        },
    ]
    assert result["skipped_assets"] == []
    assert result["error_assets"] == []
    assert rows == [
        ("AMZN", 201.25, date(2026, 3, 27)),
        ("SPY", 510.75, date(2026, 3, 27)),
    ]
    mock_update.assert_called_once_with(db_conn, fx_rates)


def test_cn_fund_included(db_conn):
    _seed_holdings(db_conn, ["CN_FUND_900008", "US_STK_AMZN"])
    service = MarketDataService()

    with patch.object(service, "get_realtime_quote") as mock_quote, patch(
        "src.sync.dsa_sync._update_from_dsa", return_value=0
    ):
        mock_quote.side_effect = lambda asset_id: {
            "CN_FUND_900008": _make_quote("CN_FUND_900008", 1.234),
            "US_STK_AMZN": _make_quote("US_STK_AMZN", 200.0),
        }[asset_id]
        result = service.refresh_portfolio_prices(db_conn, fx_rates=TEST_FX_RATES)

    rows = db_conn.execute(
        "SELECT code, close, date FROM market_daily ORDER BY code"
    ).fetchall()

    assert result["refreshed"] == 2
    assert mock_quote.call_count == 2
    assert [(code, float(close), as_of) for code, close, as_of in rows] == [
        ("900008", 1.234, date(2026, 3, 27)),
        ("AMZN", 200.0, date(2026, 3, 27)),
    ]


def test_non_refreshable_excluded(db_conn):
    _seed_holdings(db_conn, ["CASH_USD", "INS_安泰人生", "Property_阳光花园", "US_STK_AMZN"])
    service = MarketDataService()

    with patch.object(
        service,
        "get_realtime_quote",
        return_value=_make_quote("US_STK_AMZN", 200.0),
    ) as mock_quote, patch("src.sync.dsa_sync._update_from_dsa", return_value=0):
        result = service.refresh_portfolio_prices(db_conn, fx_rates=TEST_FX_RATES)

    assert result["refreshed"] == 1
    assert result["skipped"] == 0
    assert mock_quote.call_count == 1
    assert mock_quote.call_args[0][0] == "US_STK_AMZN"


def test_fixed_nav_money_market_skipped_without_error(db_conn):
    _seed_holding(
        db_conn,
        "CN_FUND_900005",
        quantity=39691.95,
        market_price_unit=1.0,
        cost_price_unit=1.0,
    )
    db_conn.execute(
        """
        INSERT INTO asset_registry (canonical_id, display_name, asset_class)
        VALUES ('CN_FUND_900005', 'Money Market Fund', 'Money Market')
        """
    )
    service = MarketDataService()

    with patch.object(service, "get_realtime_quote") as mock_quote, patch(
        "src.sync.dsa_sync._update_from_dsa", return_value=0
    ):
        result = service.refresh_portfolio_prices(db_conn, fx_rates=TEST_FX_RATES)

    assert result["refreshed"] == 0
    assert result["skipped"] == 1
    assert result["errors"] == 0
    assert result["skipped_assets"] == [
        {
            "asset_id": "CN_FUND_900005",
            "market": "cn_fund",
            "reason": "fixed-nav money market",
        }
    ]
    assert result["error_assets"] == []
    mock_quote.assert_not_called()


def test_unsupported_skipped(db_conn):
    _seed_holdings(db_conn, ["US_STK_AMZN"])
    service = MarketDataService()

    with patch.object(
        service, "get_realtime_quote", side_effect=UnsupportedCodeError("unsupported")
    ), patch("src.sync.dsa_sync._update_from_dsa", return_value=0):
        result = service.refresh_portfolio_prices(db_conn, fx_rates=TEST_FX_RATES)

    row_count = db_conn.execute("SELECT COUNT(*) FROM market_daily").fetchone()[0]
    assert result["skipped"] == 1
    assert result["refreshed"] == 0
    assert row_count == 0
    assert result["skipped_assets"] == [
        {
            "asset_id": "US_STK_AMZN",
            "market": "us",
            "reason": "unsupported",
        }
    ]


def test_fetch_error_counted(db_conn):
    _seed_holdings(db_conn, ["US_STK_AMZN", "US_ETF_SPY"])
    service = MarketDataService()

    def _quote_side_effect(asset_id: str):
        if asset_id == "US_STK_AMZN":
            raise UnsupportedCodeError("unsupported")
        return None

    with patch.object(service, "get_realtime_quote", side_effect=_quote_side_effect), patch(
        "src.sync.dsa_sync._update_from_dsa", return_value=0
    ):
        result = service.refresh_portfolio_prices(db_conn, fx_rates=TEST_FX_RATES)

    assert result["refreshed"] == 0
    assert result["skipped"] == 1
    assert result["errors"] == 1
    assert result["error_assets"] == [
        {
            "asset_id": "US_ETF_SPY",
            "market": "us",
            "reason": "quote unavailable",
        }
    ]


def test_dedup(db_conn):
    _seed_holdings(db_conn, ["US_STK_AMZN", "RSU_AMZN"])
    service = MarketDataService()

    with patch.object(service, "get_realtime_quote", return_value=_make_quote("US_STK_AMZN", 205.5)) as mock_quote, patch(
        "src.sync.dsa_sync._update_from_dsa", return_value=0
    ):
        result = service.refresh_portfolio_prices(db_conn, fx_rates=TEST_FX_RATES)

    row = db_conn.execute("SELECT code, close FROM market_daily").fetchone()
    assert result["refreshed"] == 1
    assert mock_quote.call_count == 1
    assert row == ("AMZN", 205.5)


def test_upsert_conflict(db_conn):
    _seed_holdings(db_conn, ["US_STK_AMZN"])
    service = MarketDataService()

    with patch.object(service, "get_realtime_quote") as mock_quote, patch(
        "src.sync.dsa_sync._update_from_dsa", return_value=0
    ):
        mock_quote.side_effect = [
            _make_quote("US_STK_AMZN", 200.0),
            _make_quote("US_STK_AMZN", 210.0),
        ]
        service.refresh_portfolio_prices(db_conn, fx_rates=TEST_FX_RATES)
        service.refresh_portfolio_prices(db_conn, fx_rates=TEST_FX_RATES)

    row = db_conn.execute(
        "SELECT COUNT(*), MAX(close) FROM market_daily WHERE code = 'AMZN'"
    ).fetchone()
    assert row == (1, 210.0)


def test_fetch_fx_rates_success():
    """When yfinance returns both USD and HKD, those values are used directly.
    Google Finance fallback must NOT be called."""
    def _ticker_side_effect(symbol: str):
        return SimpleNamespace(
            fast_info={
                "lastPrice": {
                    "USDCNY=X": 7.2345,
                    "HKDCNY=X": 0.9234,
                }[symbol]
            }
        )

    with patch(
        "src.market_data.fetchers.yfinance_fetcher.yfinance.Ticker",
        side_effect=_ticker_side_effect,
    ) as mock_ticker, patch(
        "src.data_manager.connectors.google_finance_connector.GoogleFinanceConnector.get_exchange_rate",
    ) as mock_gf:
        result = fetch_fx_rates()

    assert result == {"USD": 7.2345, "HKD": 0.9234}
    assert mock_ticker.call_count == 2
    # Both currencies resolved by yfinance — Google Finance must not be called
    mock_gf.assert_not_called()


def test_fetch_fx_rates_partial_yfinance_falls_back_to_google():
    """When yfinance only returns USD but not HKD, HKD should fall back to Google Finance.

    The Google Finance connector is imported lazily inside fetch_fx_rates(), so we
    patch it at the source module (google_finance_connector), not on yfinance_fetcher.
    """
    def _ticker_side_effect(symbol: str):
        if symbol == "USDCNY=X":
            return SimpleNamespace(fast_info={"lastPrice": 7.1234})
        # HKDCNY=X returns empty fast_info → no price
        return SimpleNamespace(fast_info={})

    mock_connector = SimpleNamespace(get_exchange_rate=lambda f, t: 0.9111 if f == "HKD" else None)

    with patch(
        "src.market_data.fetchers.yfinance_fetcher.yfinance.Ticker",
        side_effect=_ticker_side_effect,
    ), patch(
        "src.data_manager.connectors.google_finance_connector.get_google_finance_connector",
        return_value=mock_connector,
    ):
        result = fetch_fx_rates()

    assert result["USD"] == 7.1234   # from yfinance
    assert result["HKD"] == 0.9111   # from Google Finance fallback


def test_fetch_fx_rates_fallback():
    """When both yfinance and Google Finance fail, hard-coded defaults are returned.

    The Google Finance connector is imported lazily, so we patch at its source module.
    """
    with patch(
        "src.market_data.fetchers.yfinance_fetcher.yfinance.Ticker",
        side_effect=RuntimeError("boom"),
    ), patch(
        "src.data_manager.connectors.google_finance_connector.get_google_finance_connector",
        side_effect=RuntimeError("gf also dead"),
    ):
        result = fetch_fx_rates()

    assert result == {"USD": 7.0, "HKD": 0.9}


# ---------------------------------------------------------------------------
# T3 — refresh_prices_for_asset_ids
# ---------------------------------------------------------------------------

def test_refresh_prices_for_asset_ids_upserts_known_asset(db_conn):
    """refresh_prices_for_asset_ids upserts a market_daily row for a fetchable asset."""
    service = MarketDataService()
    quote = _make_quote("US_STK_AMZN", 210.5, as_of=date(2026, 7, 3))

    with patch.object(service, "get_realtime_quote", return_value=quote):
        n = service.refresh_prices_for_asset_ids(db_conn, ["US_STK_AMZN"])

    assert n == 1
    row = db_conn.execute("SELECT code, close, date FROM market_daily").fetchone()
    assert row == ("AMZN", 210.5, date(2026, 7, 3))


def test_refresh_prices_for_asset_ids_skips_unsupported(db_conn):
    """UnsupportedCodeError during quote fetch → skip, return 0, never raise."""
    service = MarketDataService()

    with patch.object(service, "get_realtime_quote", side_effect=UnsupportedCodeError("nope")):
        n = service.refresh_prices_for_asset_ids(db_conn, ["US_STK_AMZN"])

    assert n == 0
    count = db_conn.execute("SELECT COUNT(*) FROM market_daily").fetchone()[0]
    assert count == 0


def test_refresh_prices_for_asset_ids_never_raises(db_conn):
    """All failures are swallowed — method must never propagate an exception."""
    service = MarketDataService()

    # Simulate detect_market raising (unsupported prefix)
    with patch.object(service, "get_realtime_quote", side_effect=RuntimeError("network down")):
        # Should not raise
        n = service.refresh_prices_for_asset_ids(db_conn, ["US_STK_AMZN", "US_ETF_SPY"])

    # Both failed → 0 upserted
    assert n == 0


def test_refresh_prices_for_asset_ids_empty_list(db_conn):
    """Empty asset list → returns 0 without any DB or fetch calls."""
    service = MarketDataService()

    with patch.object(service, "get_realtime_quote") as mock_quote:
        n = service.refresh_prices_for_asset_ids(db_conn, [])

    assert n == 0
    mock_quote.assert_not_called()


def test_refresh_prices_for_asset_ids_dedup(db_conn):
    """Two asset_ids that resolve to the same code → only one upsert."""
    service = MarketDataService()
    quote = _make_quote("US_STK_AMZN", 205.0, as_of=date(2026, 7, 3))

    with patch.object(service, "get_realtime_quote", return_value=quote) as mock_quote:
        n = service.refresh_prices_for_asset_ids(db_conn, ["US_STK_AMZN", "RSU_AMZN"])

    # Only one upsert because both resolve to code "AMZN"
    assert n == 1
    assert mock_quote.call_count == 1


def test_refresh_prices_for_asset_ids_dedup_is_attempted_once_per_run(db_conn):
    """Regression (code-review fix 9): dedup means ATTEMPTED once per run — a failed
    quote for one alias must not trigger a duplicate network fetch via another alias
    resolving to the same code."""
    service = MarketDataService()

    with patch.object(service, "get_realtime_quote", return_value=None) as mock_quote:
        n = service.refresh_prices_for_asset_ids(db_conn, ["US_STK_AMZN", "RSU_AMZN"])

    assert n == 0
    # Only ONE fetch attempt despite two aliases: the code is marked seen before fetching.
    assert mock_quote.call_count == 1, (
        f"Expected 1 fetch attempt (dedup before fetch); got {mock_quote.call_count}"
    )


def test_refresh_prices_for_asset_ids_skips_fixed_nav_money_market(db_conn):
    """Regression (code-review fix 9): the fixed-NAV money-market guard from
    refresh_portfolio_prices is mirrored — funds pinned at NAV 1.0 are not live-fetched."""
    _seed_holding(
        db_conn,
        "CN_FUND_900005",
        quantity=39691.95,
        market_price_unit=1.0,
        cost_price_unit=1.0,
    )
    db_conn.execute(
        """
        INSERT INTO asset_registry (canonical_id, display_name, asset_class)
        VALUES ('CN_FUND_900005', 'Money Market Fund', 'Money Market')
        """
    )
    service = MarketDataService()

    with patch.object(service, "get_realtime_quote") as mock_quote:
        n = service.refresh_prices_for_asset_ids(db_conn, ["CN_FUND_900005"])

    assert n == 0
    mock_quote.assert_not_called()


def test_api_endpoint(api_client):
    with patch(
        "src.api.routes.market_data.MarketDataService.refresh_portfolio_prices",
        return_value={
            "refreshed": 2,
            "skipped": 0,
            "errors": 0,
            "holdings_updated": 3,
            "fx_rates": {"USD": 7.1111, "HKD": 0.9111},
        },
    ):
        response = api_client.post("/market-data/refresh")

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {
        "refreshed",
        "skipped",
        "errors",
        "holdings_updated",
        "fx_rates",
        "timestamp",
    }


# ---------------------------------------------------------------------------
# backfill_trade_window_prices
# ---------------------------------------------------------------------------

def _make_market_daily_db() -> DatabaseConnector:
    """In-memory DB with market_daily and trade_logs tables."""
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    conn.run_migrations()
    return conn


def _ohlcv_df(code: str, rows: list[tuple]) -> pd.DataFrame:
    """Return a DataFrame shaped like get_ohlcv output (date,close,source,...)."""
    return pd.DataFrame([
        {"date": d, "open": c, "high": c, "low": c,
         "close": c, "volume": 100, "pct_chg": 0.0, "source": "yfinance"}
        for d, c in rows
    ])


def _fund_df(rows: list[tuple]) -> pd.DataFrame:
    """Return a DataFrame shaped like get_market_data output (date,close,currency)."""
    return pd.DataFrame([
        {"date": d, "close": c, "currency": "CNY"}
        for d, c in rows
    ])


def test_backfill_both_windows_present_skips(db_conn):
    """If baseline AND end-window closes are already in market_daily → no fetch."""
    service = MarketDataService()
    log_date = date.today() - timedelta(days=40)
    end_date = min(log_date + timedelta(days=30), date.today())

    # Seed both windows
    db_conn.execute(
        "INSERT INTO market_daily (code, date, close, data_source) VALUES (?, ?, ?, ?)",
        ("AAPL", log_date, 180.0, "yfinance"),
    )
    db_conn.execute(
        "INSERT INTO market_daily (code, date, close, data_source) VALUES (?, ?, ?, ?)",
        ("AAPL", end_date, 185.0, "yfinance"),
    )

    with patch.object(service, "get_ohlcv") as mock_ohlcv, \
         patch.object(service, "get_market_data") as mock_gmd:
        n = service.backfill_trade_window_prices(
            db_conn, [("US_STK_AAPL", log_date)]
        )

    assert n == 0, "Both windows present → no fetch should happen"
    mock_ohlcv.assert_not_called()
    mock_gmd.assert_not_called()


def test_backfill_baseline_missing_fetches_and_upserts(db_conn):
    """Baseline window missing → get_ohlcv called, rows upserted to market_daily."""
    service = MarketDataService()
    log_date = date.today() - timedelta(days=40)
    baseline_date = log_date - timedelta(days=3)
    end_close_date = log_date + timedelta(days=28)

    # Only seed end window, not baseline
    db_conn.execute(
        "INSERT INTO market_daily (code, date, close, data_source) VALUES (?, ?, ?, ?)",
        ("AAPL", end_close_date, 185.0, "yfinance"),
    )

    historical_rows = [(baseline_date, 178.0), (log_date, 180.0)]
    mock_df = _ohlcv_df("AAPL", historical_rows)

    with patch.object(service, "get_ohlcv", return_value=mock_df) as mock_ohlcv:
        n = service.backfill_trade_window_prices(
            db_conn, [("US_STK_AAPL", log_date)]
        )

    assert n == 1, f"Expected 1 code fetched; got {n}"
    mock_ohlcv.assert_called_once()

    # Rows must be in market_daily
    count = db_conn.execute(
        "SELECT COUNT(*) FROM market_daily WHERE code = 'AAPL' AND date = ?",
        (baseline_date,),
    ).fetchone()[0]
    assert count == 1, f"Baseline close must be upserted; got count={count}"


def test_backfill_cn_fund_uses_get_market_data(db_conn):
    """CN fund asset → get_market_data (scraper) used, not get_ohlcv."""
    service = MarketDataService()
    log_date = date.today() - timedelta(days=40)

    fund_rows = [(log_date - timedelta(days=5), 1.10), (log_date, 1.12)]
    mock_df = _fund_df(fund_rows)

    with patch.object(service, "get_market_data", return_value=mock_df) as mock_gmd, \
         patch.object(service, "get_ohlcv") as mock_ohlcv:
        n = service.backfill_trade_window_prices(
            db_conn, [("CN_FUND_900002", log_date)]
        )

    assert n == 1, f"Expected 1 code fetched; got {n}"
    mock_gmd.assert_called_once_with("CN_FUND_900002", log_date - timedelta(days=7))
    mock_ohlcv.assert_not_called()


def test_backfill_max_fetches_cap(db_conn):
    """max_fetches=1 → only one code fetched even when multiple trades need backfill."""
    service = MarketDataService()
    log_date = date.today() - timedelta(days=40)

    mock_df = _ohlcv_df("AAPL", [(log_date, 180.0)])

    trades = [
        ("US_STK_AAPL", log_date),
        ("US_ETF_SPY", log_date),
    ]

    with patch.object(service, "get_ohlcv", return_value=mock_df) as mock_ohlcv:
        n = service.backfill_trade_window_prices(db_conn, trades, max_fetches=1)

    assert n == 1, f"Expected exactly 1 fetch with max_fetches=1; got {n}"
    assert mock_ohlcv.call_count == 1


def test_backfill_dedup_per_code(db_conn):
    """Two (asset_id, log_date) pairs with the same raw code → only one fetch."""
    service = MarketDataService()
    log_date = date.today() - timedelta(days=40)

    # US_STK_AMZN and RSU_AMZN both resolve to code "AMZN"
    mock_df = _ohlcv_df("AMZN", [(log_date, 200.0)])

    with patch.object(service, "get_ohlcv", return_value=mock_df) as mock_ohlcv:
        n = service.backfill_trade_window_prices(
            db_conn, [("US_STK_AMZN", log_date), ("RSU_AMZN", log_date)]
        )

    assert mock_ohlcv.call_count == 1, (
        f"Same raw code 'AMZN' must be fetched only once; call_count={mock_ohlcv.call_count}"
    )
    assert n == 1


def test_backfill_fetch_error_continues_not_raise(db_conn):
    """get_ohlcv raising → method continues to next trade, never raises, returns 0."""
    service = MarketDataService()
    log_date = date.today() - timedelta(days=40)

    with patch.object(service, "get_ohlcv", side_effect=RuntimeError("network down")):
        # Must not raise
        n = service.backfill_trade_window_prices(
            db_conn, [("US_STK_AAPL", log_date)]
        )

    assert n == 0, "Fetch error must return 0, not raise"


def test_backfill_unsupported_code_skipped(db_conn):
    """UnsupportedCodeError from _detect_market → silently skip, return 0."""
    service = MarketDataService()
    log_date = date.today() - timedelta(days=40)

    # CASH_USD has no market detection → UnsupportedCodeError
    with patch.object(service, "get_ohlcv") as mock_ohlcv:
        n = service.backfill_trade_window_prices(
            db_conn, [("CASH_USD", log_date)]
        )

    assert n == 0
    mock_ohlcv.assert_not_called()


def test_backfill_empty_trades_returns_zero(db_conn):
    """Empty trades list → returns 0 without any fetcher call."""
    service = MarketDataService()

    with patch.object(service, "get_ohlcv") as mock_ohlcv, \
         patch.object(service, "get_market_data") as mock_gmd:
        n = service.backfill_trade_window_prices(db_conn, [])

    assert n == 0
    mock_ohlcv.assert_not_called()
    mock_gmd.assert_not_called()


def test_backfill_upsert_conflict_updates_close(db_conn):
    """ON CONFLICT DO UPDATE: existing market_daily row gets close updated."""
    service = MarketDataService()
    log_date = date.today() - timedelta(days=40)

    # Seed with old value
    db_conn.execute(
        "INSERT INTO market_daily (code, date, close, data_source) VALUES (?, ?, ?, ?)",
        ("AAPL", log_date - timedelta(days=3), 170.0, "old"),
    )

    # Backfill returns updated price for same date
    mock_df = _ohlcv_df("AAPL", [(log_date - timedelta(days=3), 175.0)])

    with patch.object(service, "get_ohlcv", return_value=mock_df):
        service.backfill_trade_window_prices(db_conn, [("US_STK_AAPL", log_date)])

    row = db_conn.execute(
        "SELECT close FROM market_daily WHERE code='AAPL' AND date=?",
        (log_date - timedelta(days=3),),
    ).fetchone()
    assert row is not None
    assert abs(float(row[0]) - 175.0) < 0.01, f"Close should be updated to 175.0; got {row[0]}"
