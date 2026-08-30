"""Tests for src/api/routes/governance.py (PRD 2026-07-07 F4.3/F4.4/F4.6,
Batch B5). In-memory DuckDB via initialize_schema (never a bare, schema-less
connector)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.database.seed_loader import seed_demo_content


@pytest.fixture
def client():
    test_conn = DatabaseConnector(":memory:")
    initialize_schema(test_conn)
    # Program OSR WS-3c: data_fixes seeds moved out of schema.sql into the
    # seed-pack system — the test session runs under $UIS_SEED_PROFILE=example
    # (tests/conftest.py), so this populates the persona's 3 example entries.
    seed_demo_content(test_conn)

    def override_get_db():
        return test_conn

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), test_conn
    app.dependency_overrides.clear()
    test_conn.close()


def test_get_metrics_returns_seeded_catalog(client):
    test_client, _conn = client
    resp = test_client.get("/governance/metrics")
    assert resp.status_code == 200
    keys = {row["metric_key"] for row in resp.json()}
    assert "buffett_indicator" in keys
    assert "vix" in keys


def test_list_data_fixes_default_open(client):
    test_client, _conn = client
    resp = test_client.get("/governance/data-fixes")
    assert resp.status_code == 200
    body = resp.json()
    # 2 open seeds in the example pack (seeds/example/data_fixes.yaml).
    assert len(body["items"]) == 2
    assert all(item["status"] == "open" for item in body["items"])
    assert "overdue_count" in body


def test_list_data_fixes_done_filter(client):
    test_client, _conn = client
    resp = test_client.get("/governance/data-fixes", params={"status": "done"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert all(item["status"] == "done" for item in body["items"])


def test_list_data_fixes_all_filter_returns_all_seeds(client):
    test_client, _conn = client
    resp = test_client.get("/governance/data-fixes", params={"status": "all"})
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 3


def test_create_data_fix_default_due_at_from_fast_metric(client):
    test_client, conn = client
    before = datetime.now()
    resp = test_client.post(
        "/governance/data-fixes",
        json={"title": "New FX issue", "metric_key": "fx_usd_cny"},
    )
    assert resp.status_code == 200
    body = resp.json()
    due_at = datetime.fromisoformat(body["due_at"])
    # fx_usd_cny is 'fast' -> default due_at = now + 7d.
    assert (due_at - before).days in (6, 7)
    assert body["status"] == "open"


def test_create_data_fix_default_due_at_from_slow_metric(client):
    test_client, _conn = client
    before = datetime.now()
    resp = test_client.post(
        "/governance/data-fixes",
        json={"title": "New CSI issue", "metric_key": "csi500_pe"},
    )
    assert resp.status_code == 200
    body = resp.json()
    due_at = datetime.fromisoformat(body["due_at"])
    assert (due_at - before).days in (29, 30)


def test_create_data_fix_default_due_at_no_metric_key_defaults_slow(client):
    test_client, _conn = client
    before = datetime.now()
    resp = test_client.post("/governance/data-fixes", json={"title": "No metric key"})
    assert resp.status_code == 200
    body = resp.json()
    due_at = datetime.fromisoformat(body["due_at"])
    assert (due_at - before).days in (29, 30)
    assert body["due_at"] is not None  # never NULL


def test_create_data_fix_explicit_due_at_parses(client):
    test_client, _conn = client
    resp = test_client.post(
        "/governance/data-fixes",
        json={"title": "Explicit due", "due_at": "2026-08-01T00:00:00"},
    )
    assert resp.status_code == 200
    assert resp.json()["due_at"].startswith("2026-08-01")


def test_create_data_fix_bad_due_at_returns_422(client):
    test_client, _conn = client
    resp = test_client.post(
        "/governance/data-fixes",
        json={"title": "Bad due", "due_at": "not-a-date"},
    )
    assert resp.status_code == 422


def test_overdue_listing_sorted_due_at_asc(client):
    test_client, conn = client
    now = datetime.now()
    conn.execute(
        "INSERT INTO data_fixes (title, opened_at, due_at, status) VALUES (?, ?, ?, 'open')",
        ["Overdue A", now - timedelta(days=20), now - timedelta(days=5)],
    )
    conn.execute(
        "INSERT INTO data_fixes (title, opened_at, due_at, status) VALUES (?, ?, ?, 'open')",
        ["Overdue B", now - timedelta(days=40), now - timedelta(days=15)],
    )
    resp = test_client.get("/governance/data-fixes", params={"status": "overdue"})
    assert resp.status_code == 200
    body = resp.json()
    titles = [item["title"] for item in body["items"]]
    assert "Overdue A" in titles and "Overdue B" in titles
    # sorted due_at ASC -> Overdue B (further in the past) comes first.
    assert titles.index("Overdue B") < titles.index("Overdue A")
    assert body["overdue_count"] >= 2


def test_update_status_transitions_and_closed_at(client):
    test_client, conn = client
    fix_id = conn.execute(
        "SELECT id FROM data_fixes WHERE title = 'Example: FX source freshness'"
    ).fetchone()[0]

    resp = test_client.put(f"/governance/data-fixes/{fix_id}", json={"status": "done"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert body["closed_at"] is not None

    resp2 = test_client.put(f"/governance/data-fixes/{fix_id}", json={"status": "open"})
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "open"
    assert resp2.json()["closed_at"] is None


def test_update_status_unknown_id_404(client):
    test_client, _conn = client
    resp = test_client.put("/governance/data-fixes/999999", json={"status": "done"})
    assert resp.status_code == 404


def test_update_status_bad_status_422(client):
    test_client, conn = client
    fix_id = conn.execute("SELECT id FROM data_fixes LIMIT 1").fetchone()[0]
    resp = test_client.put(f"/governance/data-fixes/{fix_id}", json={"status": "bogus"})
    assert resp.status_code == 422
