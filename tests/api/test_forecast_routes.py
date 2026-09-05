"""Tests for GET /forecast/levers (R-2,
docs/plans/2026-07-25-forecast-planning-redesign.md).

In-memory DuckDB via initialize_schema, wired through src.api.main.app via
the get_db dependency override — the same pattern as
tests/api/test_north_star_routes.py. Never connects to data/unified.duckdb.

Route-level parity (mounted under both / and /api/) is already covered
generically by tests/api/test_cloud_run_api_prefix.py::test_every_router_has_api_prefixed_parity
via ALL_ROUTERS — no per-router duplicate needed here.
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_db
from src.api.main import app
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema


@pytest.fixture
def client():
    test_conn = DatabaseConnector(":memory:")
    initialize_schema(test_conn)

    def override_get_db():
        return test_conn

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), test_conn
    app.dependency_overrides.clear()
    test_conn.close()


def _seed_net_worth(conn, value: float) -> None:
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, quantity, market_value, currency, source_system, is_shadow)
        VALUES (?, 'US_STK_EQ', 'US_STK_EQ', 1, ?, 'CNY', 'test', FALSE)
        """,
        [date.today().isoformat(), value],
    )


def _month_start_n_ago(today: date, n: int) -> date:
    year = today.year
    month = today.month - n
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _seed_run_rate(conn, monthly_amount: float, months: int = 12) -> None:
    today = date.today()
    for i in range(months):
        month = _month_start_n_ago(today, i)
        payload = {"投资理财_股票基金_天天基金": monthly_amount, "收入_主动收入_工资": monthly_amount * 10.0}
        conn.execute(
            "INSERT INTO income_expense_monthly (record_key, transaction_date, payload) VALUES (?, ?, ?)",
            [f"ie_{month.isoformat()}", month.isoformat(), json.dumps(payload)],
        )


def test_forecast_levers_returns_200_with_full_shape(client):
    test_client, conn = client
    _seed_net_worth(conn, 3_269_850.0)
    _seed_run_rate(conn, 30_670.0)

    with patch(
        "src.financial_analysis.projection_defaults.suggested_return_basis", return_value=0.108
    ), patch(
        "src.financial_analysis.metrics.calculate_portfolio_metrics",
        return_value={"volatility_annual": 17.9},
    ):
        resp = test_client.get("/forecast/levers")

    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body.keys()) == {"base", "levers", "combined", "goal"}
    assert set(body["levers"].keys()) == {"savings", "return", "volatility"}
    assert body["base"]["expected_return"] == pytest.approx(0.108)
    assert body["base"]["years_to_target"] is not None
    # W-1 (goal resolver): goal is the full resolver dict; empty goals table
    # in this fixture DB -> config fallback, target must match base['target'].
    for key in ("target_amount", "source", "goal_id", "name", "target_date", "fallback_reason"):
        assert key in body["goal"], f"goal missing key {key}"
    assert body["goal"]["target_amount"] == pytest.approx(body["base"]["target"])


def test_forecast_levers_no_query_params_matches_baseline(client):
    """W-2 backward-compat, at the HTTP layer: GET /forecast/levers with no
    query string must be byte-for-byte identical to the same call repeated
    (i.e. the new optional-param machinery does not perturb the default
    path at all)."""
    test_client, conn = client
    _seed_net_worth(conn, 3_269_850.0)
    _seed_run_rate(conn, 30_670.0)

    with patch(
        "src.financial_analysis.projection_defaults.suggested_return_basis", return_value=0.108
    ), patch(
        "src.financial_analysis.metrics.calculate_portfolio_metrics",
        return_value={"volatility_annual": 17.9},
    ):
        resp1 = test_client.get("/forecast/levers")
        resp2 = test_client.get("/forecast/levers")

    assert resp1.status_code == 200 and resp2.status_code == 200
    assert resp1.json() == resp2.json()
    assert "applied" not in resp1.json()
    assert "crossing_years" in resp1.json()["base"]


def test_forecast_levers_slider_params_add_row_and_applied_block(client):
    test_client, conn = client
    _seed_net_worth(conn, 3_269_850.0)
    _seed_run_rate(conn, 30_670.0)

    with patch(
        "src.financial_analysis.projection_defaults.suggested_return_basis", return_value=0.108
    ), patch(
        "src.financial_analysis.metrics.calculate_portfolio_metrics",
        return_value={"volatility_annual": 17.9},
    ):
        resp = test_client.get("/forecast/levers?savings_pct=25&return_pp=2.0&volatility_pp=3.0")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] == {"savings_pct": 25.0, "return_pp": 2.0, "volatility_pp": 3.0}
    assert len(body["levers"]["savings"]) == 4
    assert len(body["levers"]["return"]) == 3
    assert len(body["levers"]["volatility"]) == 3


@pytest.mark.parametrize(
    "query",
    [
        "savings_pct=61",     # > 60 max
        "savings_pct=-1",     # < 0 min
        "return_pp=6.5",      # > 6 max
        "return_pp=-0.1",     # < 0 min
        "volatility_pp=10.1", # > 10 max
        "volatility_pp=-1",   # < 0 min
    ],
)
def test_forecast_levers_out_of_range_slider_param_422s(client, query):
    test_client, conn = client
    _seed_net_worth(conn, 3_000_000.0)
    resp = test_client.get(f"/forecast/levers?{query}")
    assert resp.status_code == 422, resp.text


def test_forecast_levers_never_500s_on_empty_db(client):
    """Empty DB (no holdings, no income_expense_monthly, no TWR history) must
    still return 200 with nulls where data is unavailable — Rule 12: never
    a silent empty-200 that masks a real crash, but also never a 500 for
    genuinely-absent data."""
    test_client, _conn = client
    resp = test_client.get("/forecast/levers")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["base"]["current_nw"] == 0.0
    assert body["base"]["expected_return"] is None
    assert body["base"]["years_to_target"] is None
