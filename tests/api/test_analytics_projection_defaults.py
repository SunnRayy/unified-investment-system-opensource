"""Regression test for BUG 4a (2026-07-25 owner UI review):

GET /analytics/projection/defaults's avg_monthly_investment_12m / _36m used to
anchor their trailing windows to `date.today()`. ADR-025 §2 established that
the FS Excel ledger (income_expense_monthly) lags real time by 1-2 months, so
"today minus 365/1095 days" silently excludes the most recent, most relevant
data whenever the DB hasn't been synced very recently — worse, on a DB whose
newest row is old (e.g. a stale local mirror), the old anchor could exclude
ALL data and silently report 0.0 with no error.

Fixed: anchor both windows to the latest DATA month present in
income_expense_monthly, via contributions_summary_v2()'s own window
derivation (window_start_month) — never date.today().

Uses an on-disk temp DuckDB (never data/unified.duckdb — see CLAUDE.md
Database Safety Rules), following the pattern in tests/api/test_analytics.py.
"""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.analytics import router as analytics_router
from src.database.connector import DatabaseConnector
from src.database.schema import bootstrap_database

app = FastAPI()
app.include_router(analytics_router)


@pytest.fixture()
def defaults_client():
    from src.api.dependencies import get_db, get_writable_db

    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
        db_path = f.name
    os.unlink(db_path)

    bootstrap_conn = DatabaseConnector(db_path)
    bootstrap_database(bootstrap_conn)

    # Seed income_expense_monthly with 15 consecutive months, all dated in
    # 2020-2021 — far outside any "today minus 365/1095 days" window computed
    # at real test-run time, but the ONLY data present. If the window anchor
    # is still date.today()-based, avg_monthly_investment_12m/_36m come back
    # 0.0 (bug); anchored to the latest DATA month, they come back 10000.0.
    months = [
        ("2020-01-01", "2020-01"), ("2020-02-01", "2020-02"), ("2020-03-01", "2020-03"),
        ("2020-04-01", "2020-04"), ("2020-05-01", "2020-05"), ("2020-06-01", "2020-06"),
        ("2020-07-01", "2020-07"), ("2020-08-01", "2020-08"), ("2020-09-01", "2020-09"),
        ("2020-10-01", "2020-10"), ("2020-11-01", "2020-11"), ("2020-12-01", "2020-12"),
        ("2021-01-01", "2021-01"), ("2021-02-01", "2021-02"), ("2021-03-01", "2021-03"),
    ]
    for record_key, month in months:
        payload = {"投资理财_股票基金_天天基金": 10000, "收入_主动收入_工资": 30000}
        bootstrap_conn.execute(
            "INSERT INTO income_expense_monthly (record_key, transaction_date, payload) VALUES (?, ?, ?)",
            [f"fs-{month}", record_key, json.dumps(payload)],
        )
    bootstrap_conn.close()

    def override_get_db():
        conn = DatabaseConnector(db_path, read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    def override_get_writable_db():
        conn = DatabaseConnector(db_path, read_only=False)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_writable_db] = override_get_writable_db

    yield TestClient(app)

    app.dependency_overrides.clear()
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass


@pytest.fixture()
def empty_defaults_client():
    """Same wiring as defaults_client but with an empty income_expense_monthly."""
    from src.api.dependencies import get_db, get_writable_db

    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
        db_path = f.name
    os.unlink(db_path)

    bootstrap_conn = DatabaseConnector(db_path)
    bootstrap_database(bootstrap_conn)
    bootstrap_conn.close()

    def override_get_db():
        conn = DatabaseConnector(db_path, read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    def override_get_writable_db():
        conn = DatabaseConnector(db_path, read_only=False)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_writable_db] = override_get_writable_db

    yield TestClient(app)

    app.dependency_overrides.clear()
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass


def test_avg_monthly_investment_anchors_to_latest_data_month_not_today(defaults_client):
    """The only data present is 2020-2021 — stale relative to real test-run
    time. Both averages must be computed from that data (10000.0), not 0.0
    from a date.today()-anchored filter that would exclude everything."""
    resp = defaults_client.get("/analytics/projection/defaults")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["avg_monthly_investment_12m"] == pytest.approx(10000.0), (
        "avg_monthly_investment_12m must anchor to the latest DATA month "
        "(2021-03), not date.today() — got "
        f"{body['avg_monthly_investment_12m']} (0.0 means the old "
        "date.today()-anchored bug regressed)"
    )
    assert body["avg_monthly_investment_36m"] == pytest.approx(10000.0), (
        "avg_monthly_investment_36m must anchor to the latest DATA month, "
        f"not date.today() — got {body['avg_monthly_investment_36m']}"
    )


def test_avg_monthly_investment_zero_when_no_data(empty_defaults_client):
    """Empty income_expense_monthly (window_start_month is None) must yield
    0.0, not raise — contributions_summary_v2's own empty-series contract."""
    resp = empty_defaults_client.get("/analytics/projection/defaults")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["avg_monthly_investment_12m"] == 0.0
    assert body["avg_monthly_investment_36m"] == 0.0


def test_avg_monthly_investment_excludes_usd_suffixed_columns(tmp_path):
    """ADR-025 §3: 投资理财_股票基金_Schawab_USD is the SAME money as
    ...Schawab (CNY), recorded in dollars. Summing both adds raw USD into a
    CNY total. Regression for the 2026-07-25 finding: Projections reported
    ~¥120K/mo vs ~¥110K/mo actual (¥10,000/mo overstatement).
    """
    import json as _json
    import duckdb
    from src.financial_analysis.projection_defaults import avg_monthly_investment

    conn = duckdb.connect(str(tmp_path / "t.duckdb"))
    conn.execute(
        "CREATE TABLE income_expense_monthly (record_key VARCHAR, transaction_date DATE, payload VARCHAR)"
    )
    payload = {
        "投资理财_股票基金_天天基金": 10_000.0,
        "投资理财_股票基金_Schawab": 20_000.0,      # CNY
        "投资理财_股票基金_Schawab_USD": 3_000.0,    # SAME money, in USD — must be ignored
    }
    conn.execute(
        "INSERT INTO income_expense_monthly VALUES (?, ?, ?)",
        ["ie_1", "2026-01-01", _json.dumps(payload)],
    )

    result = avg_monthly_investment(conn, "2025-01-01")
    assert result == 30_000.0, (
        f"expected 30000 (10000 + 20000 CNY, USD column excluded), got {result}"
    )
    conn.close()


# ── Decision 3 (2026-07-25-cash-flow-classification-completion.md):
#    /analytics/projection/defaults exposes suggested_contribution_run_rate,
#    reusing north_star_glide._contribution_run_rate rather than duplicating
#    its formula. ───────────────────────────────────────────────────────────

def test_suggested_contribution_run_rate_matches_glide_path_run_rate(defaults_client):
    """The endpoint's suggested_contribution_run_rate must be the exact same
    value the glide path uses — same window (last 12 of the 15 seeded
    months), same (net_external_ttm + rsu_retained_ttm) / 12 formula.

    Fixture data: 12 of the 15 months in the trailing-12 window each carry
    投资理财_股票基金_天天基金=10000 / 总收入合计=30000, no redemptions, no RSU
    transactions in this DB — so net_external_ttm=120000, rsu_retained=0,
    run_rate = 120000/12 = 10000.0. The sanity guard is not tripped: the
    fixture's transaction_date values (2020-2021) fall outside
    _trailing_12m_gross_income's `today - 365d` window, so that guard input
    is None and is skipped by construction.
    """
    with patch("src.services.rsu_contributions.get_today_usd_cny_rate", return_value=7.0):
        resp = defaults_client.get("/analytics/projection/defaults")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "suggested_contribution_run_rate" in body
    assert body["suggested_contribution_run_rate"] == pytest.approx(10000.0), (
        "expected the glide-path run-rate (120000 net_external_ttm + 0 RSU "
        f"retained) / 12 = 10000.0, got {body['suggested_contribution_run_rate']}"
    )


def test_suggested_contribution_run_rate_none_when_unavailable(empty_defaults_client):
    """Empty income_expense_monthly → contributions_summary_v2's window is
    None → _contribution_run_rate status is "no contribution data available"
    → the endpoint must report None, never 0.0 or a fabricated value."""
    resp = empty_defaults_client.get("/analytics/projection/defaults")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["suggested_contribution_run_rate"] is None
