import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_db
from src.api.main import app
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema


def _seed_holdings(conn: DatabaseConnector, rows: list[tuple[str, str, float, bool]]) -> None:
    conn.executemany(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, source_system, quantity, is_shadow
        ) VALUES (?, ?, 'test_source', ?, ?)
        """,
        rows,
    )


@pytest.fixture
def api_client(tmp_path):
    conn = DatabaseConnector(str(tmp_path / "market_data_status.duckdb"))
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


def test_get_status_returns_never_when_no_refresh(api_client):
    conn = app.dependency_overrides[get_db]()
    _seed_holdings(
        conn,
        [
            ("2026-03-27", "US_STK_AMZN", 1.0, False),
            ("2026-03-27", "US_ETF_SPY", 2.0, False),
            ("2026-03-27", "RSU_NVDA", 3.0, False),
            ("2026-03-27", "CN_FUND_900008", 4.0, False),
            ("2026-03-27", "CN_FUND_900003", 5.0, True),
        ],
    )

    response = api_client.get("/market-data/status")

    assert response.status_code == 200
    assert response.json() == {
        "last_refresh": None,
        "providers": [
            {"market": "cn_fund", "fetcher": "akshare", "asset_count": 1, "status": "active"},
            {"market": "us", "fetcher": "yfinance", "asset_count": 3, "status": "active"},
        ],
        "staleness": "never",
    }


def test_refresh_persists_to_sync_state(api_client):
    conn = app.dependency_overrides[get_db]()

    with patch(
        "src.api.routes.market_data.MarketDataService.refresh_portfolio_prices",
        return_value={
            "refreshed": 8,
            "skipped": 15,
            "errors": 0,
            "holdings_updated": 41,
            "fx_rates": {"USD": 7.1234, "HKD": 0.9123},
            "refreshed_assets": [
                {
                    "asset_id": "US_STK_AMZN",
                    "code": "AMZN",
                    "market": "us",
                    "price": 201.25,
                    "as_of_date": "2026-03-27",
                    "source": "yfinance",
                }
            ],
            "skipped_assets": [],
            "error_assets": [],
        },
    ):
        response = api_client.post("/market-data/refresh")

    assert response.status_code == 200
    payload = response.json()

    stored = conn.execute(
        "SELECT value FROM sync_state WHERE key = 'market_data_last_refresh'"
    ).fetchone()
    assert stored is not None

    persisted = json.loads(stored[0])
    assert persisted == payload
    assert persisted["timestamp"]
    assert persisted["refreshed_assets"][0]["asset_id"] == "US_STK_AMZN"
    assert persisted["fx_rates"]["USD"] == 7.1234


@pytest.mark.parametrize(
    ("hours_ago", "expected"),
    [
        (1, "fresh"),
        (8, "aging"),
        (30, "stale"),
    ],
)
def test_staleness_computation(api_client, hours_ago, expected):
    conn = app.dependency_overrides[get_db]()
    timestamp = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    conn.execute(
        """
        INSERT INTO sync_state (key, value)
        VALUES ('market_data_last_refresh', ?)
        """,
        (json.dumps({"timestamp": timestamp, "refreshed": 1, "skipped": 0, "errors": 0, "holdings_updated": 1}),),
    )

    response = api_client.get("/market-data/status")

    assert response.status_code == 200
    body = response.json()
    assert body["staleness"] == expected
    assert body["last_refresh"]["timestamp"] == timestamp
