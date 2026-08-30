"""Tests for the additive open_value_trap_review field on /wealthos/assets (WS2 F2.4).

Uses near-today dynamic dates to avoid stale fixture issues (HANDOVER.md caveat).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _today_str() -> str:
    return date.today().isoformat()


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture
def client(monkeypatch):
    """In-memory DB wired into the FastAPI app; FX rate patched to avoid network."""
    monkeypatch.setattr(
        "src.services.currency.get_today_usd_cny_rate",
        lambda: 7.2,
    )

    test_conn = DatabaseConnector(":memory:")
    initialize_schema(test_conn)

    def override_get_db():
        return test_conn

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), test_conn
    app.dependency_overrides.clear()
    test_conn.close()


def _insert_holding(conn: DatabaseConnector, asset_id: str, *, snapshot_date: str | None = None) -> None:
    snap = snapshot_date or _today_str()
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, quantity, cost_price_unit,
             market_price_unit, market_value, currency, source_system, is_shadow)
        VALUES (?, ?, ?, 100.0, 10.0, 9.0, 900.0, 'CNY', 'test', FALSE)
        """,
        [snap, asset_id, asset_id],
    )
    # Insert a matching buy transaction so the asset appears in all_asset_ids
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type,
             quantity, price_unit, currency, source_system)
        VALUES (?, ?, ?, 'buy', 100.0, 10.0, 'CNY', 'test')
        """,
        [snap, asset_id, asset_id],
    )


def _insert_open_review(conn: DatabaseConnector, asset_id: str) -> None:
    conn.execute(
        """
        INSERT INTO value_trap_reviews
            (asset_id, asset_name, status, trigger_threshold_pct, unrealized_return_pct, opened_at)
        VALUES (?, ?, 'open', -25.0, -30.0, CURRENT_TIMESTAMP)
        """,
        [asset_id, asset_id],
    )


def _insert_ruled_review(conn: DatabaseConnector, asset_id: str) -> None:
    conn.execute(
        """
        INSERT INTO value_trap_reviews
            (asset_id, asset_name, status, trigger_threshold_pct, unrealized_return_pct,
             ruling, opened_at, last_reviewed_at)
        VALUES (?, ?, 'ruled', -25.0, -30.0, 'hold_with_thesis', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [asset_id, asset_id],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_asset_with_open_review_has_badge_true(client):
    """An asset with status='open' review → open_value_trap_review=True."""
    test_client, conn = client
    _insert_holding(conn, "CN_FUND_TEST1")
    _insert_open_review(conn, "CN_FUND_TEST1")

    resp = test_client.get("/wealthos/assets", params={"include_non_rebalanceable": True})
    assert resp.status_code == 200

    all_assets = resp.json().get("assets", []) + resp.json().get("non_rebalanceable_assets", [])
    matched = [a for a in all_assets if a["code"] == "CN_FUND_TEST1"]
    assert matched, "CN_FUND_TEST1 not found in wealthos/assets response"
    assert matched[0]["open_value_trap_review"] is True


def test_asset_with_ruled_review_has_badge_false(client):
    """An asset with status='ruled' review → open_value_trap_review=False."""
    test_client, conn = client
    _insert_holding(conn, "CN_FUND_TEST2")
    _insert_ruled_review(conn, "CN_FUND_TEST2")

    resp = test_client.get("/wealthos/assets", params={"include_non_rebalanceable": True})
    assert resp.status_code == 200

    all_assets = resp.json().get("assets", []) + resp.json().get("non_rebalanceable_assets", [])
    matched = [a for a in all_assets if a["code"] == "CN_FUND_TEST2"]
    assert matched, "CN_FUND_TEST2 not found in wealthos/assets response"
    assert matched[0]["open_value_trap_review"] is False


def test_asset_with_no_review_has_badge_false(client):
    """An asset with no review at all → open_value_trap_review=False."""
    test_client, conn = client
    _insert_holding(conn, "CN_FUND_NO_REVIEW")

    resp = test_client.get("/wealthos/assets", params={"include_non_rebalanceable": True})
    assert resp.status_code == 200

    all_assets = resp.json().get("assets", []) + resp.json().get("non_rebalanceable_assets", [])
    matched = [a for a in all_assets if a["code"] == "CN_FUND_NO_REVIEW"]
    assert matched, "CN_FUND_NO_REVIEW not found in wealthos/assets response"
    assert matched[0]["open_value_trap_review"] is False
