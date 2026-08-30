"""Tests for src/api/routes/attribution.py (Attribution & Flows Program WS-1).

In-memory DuckDB via initialize_schema + run_migrations (never a bare,
schema-less connector; never the real production DB).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.attribution import HISTORY_FLOOR_MONTH


# ── Calendar-relative fixture dates ───────────────────────────────────────────
# POST /attribution/recompute derives its window from date.today(), so seeding
# fixed calendar months made these tests rot: data pinned to 2026-06 fell out of
# a months=2 window the moment the wall clock rolled into 2026-08, and the suite
# went red with no code change. Seed relative to "last complete month" instead —
# that is the month a months=2 recompute always covers, whatever today is.
def _add_months(d: date, n: int) -> date:
    total = d.year * 12 + (d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


THIS_MONTH = date.today().replace(day=1)
PREV_MONTH = _add_months(THIS_MONTH, -1)
PREV_MONTH_STR = PREV_MONTH.strftime("%Y-%m")
# Last day of the month before PREV_MONTH, and last day of PREV_MONTH.
MV_START_DATE = PREV_MONTH - timedelta(days=1)
MV_END_DATE = THIS_MONTH - timedelta(days=1)
TXN_DATE = PREV_MONTH + timedelta(days=9)


@pytest.fixture
def client():
    test_conn = DatabaseConnector(":memory:")
    initialize_schema(test_conn)
    test_conn.run_migrations()

    def override_get_db():
        return test_conn

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), test_conn
    app.dependency_overrides.clear()
    test_conn.close()


def _seed(conn):
    conn.execute(
        "INSERT INTO asset_registry (canonical_id, display_name, asset_class) VALUES ('TEST_STK', 'Test Stock', 'Equity')"
    )
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, quantity, market_price_unit,
             market_value, currency, source_system, is_shadow)
        VALUES (?, 'TEST_STK', 'TEST_STK', 100, 10, 1000, 'CNY', 'test', FALSE)
        """,
        [str(MV_START_DATE)],
    )
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, quantity,
             price_unit, amount_net, currency, source_system, is_provisional)
        VALUES (?, 'TEST_STK', 'TEST_STK', 'buy', 20, 11, 220, 'CNY', 'test', FALSE)
        """,
        [str(TXN_DATE)],
    )
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, quantity, market_price_unit,
             market_value, currency, source_system, is_shadow)
        VALUES (?, 'TEST_STK', 'TEST_STK', 120, 12, 1440, 'CNY', 'test', FALSE)
        """,
        [str(MV_END_DATE)],
    )


def test_monthly_before_history_floor_returns_400(client):
    test_client, _conn = client
    resp = test_client.get("/attribution/monthly", params={"month": "2025-12"})
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body


def test_monthly_invalid_level_returns_400(client):
    test_client, _conn = client
    resp = test_client.get("/attribution/monthly", params={"month": "2026-06", "level": "bogus"})
    assert resp.status_code == 400


def test_monthly_bad_format_returns_400(client):
    test_client, _conn = client
    resp = test_client.get("/attribution/monthly", params={"month": "not-a-month"})
    assert resp.status_code == 400


def test_asset_unknown_returns_404(client):
    test_client, _conn = client
    resp = test_client.get("/attribution/asset/DOES_NOT_EXIST")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body


def test_recompute_then_monthly_roundtrip(client):
    test_client, conn = client
    _seed(conn)

    resp = test_client.post("/attribution/recompute", json={"months": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["months_recomputed"] >= 1

    resp2 = test_client.get(
        "/attribution/monthly", params={"month": PREV_MONTH_STR, "level": "asset"}
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["month"] == PREV_MONTH_STR
    rows = [r for r in body2["rows"] if r["asset_id"] == "TEST_STK"]
    assert len(rows) == 1
    assert rows[0]["delta"] == pytest.approx(440.0)

    resp3 = test_client.get("/attribution/asset/TEST_STK", params={"months": 3})
    assert resp3.status_code == 200
    body3 = resp3.json()
    assert body3["asset_id"] == "TEST_STK"
    assert len(body3["months"]) >= 1
    assert "events" in body3["months"][0]

    resp4 = test_client.get("/attribution/summary", params={"months": 3})
    assert resp4.status_code == 200
    body4 = resp4.json()
    assert isinstance(body4["months"], list)


def test_recompute_zero_months_returns_400(client):
    test_client, _conn = client
    resp = test_client.post("/attribution/recompute", json={"months": 0})
    assert resp.status_code == 400


# ── Item B: month_to range param (2026-07-20 owner round-2 review) ─────────

def test_monthly_range_month_to_aggregates(client):
    test_client, conn = client
    _seed(conn)  # TEST_STK, PREV_MONTH only (mv_start 1000 -> mv_end 1440)

    resp = test_client.post("/attribution/recompute", json={"months": 6})
    assert resp.status_code == 200

    # Widest range the months=6 recompute window can cover, floored at HISTORY_FLOOR_MONTH.
    range_from = max(_add_months(PREV_MONTH, -4), HISTORY_FLOOR_MONTH)
    range_from_str = range_from.strftime("%Y-%m")

    resp2 = test_client.get(
        "/attribution/monthly",
        params={"month": range_from_str, "month_to": PREV_MONTH_STR, "level": "asset"},
    )
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["month"] == f"{range_from_str}..{PREV_MONTH_STR}"
    rows = [r for r in body["rows"] if r["asset_id"] == "TEST_STK"]
    assert len(rows) == 1
    assert rows[0]["mv_end"] == pytest.approx(1440.0)


def test_monthly_range_month_to_before_month_returns_400(client):
    test_client, _conn = client
    resp = test_client.get(
        "/attribution/monthly", params={"month": "2026-06", "month_to": "2026-01"}
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body


def test_monthly_range_month_to_bad_format_returns_400(client):
    test_client, _conn = client
    resp = test_client.get(
        "/attribution/monthly", params={"month": "2026-06", "month_to": "not-a-month"}
    )
    assert resp.status_code == 400


def test_monthly_range_month_to_before_history_floor_returns_400(client):
    test_client, _conn = client
    resp = test_client.get(
        "/attribution/monthly", params={"month": "2026-01", "month_to": "2025-12"}
    )
    assert resp.status_code == 400
