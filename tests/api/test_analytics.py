"""Tests for Goals API endpoints — POST /goals persists, DELETE /goals removes.

P0 regression guard: verifies that POST /goals and DELETE /goals actually write to
the database (the bug was both endpoints used get_db read_only=True, silently failing).
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.analytics import router as analytics_router
from src.database.connector import DatabaseConnector
from src.database.schema import bootstrap_database


# ---------------------------------------------------------------------------
# App + fixture
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(analytics_router)


@pytest.fixture()
def goals_client():
    """
    Spin up a temp on-disk DuckDB bootstrapped with full schema (including goals
    table created by Migration 13), then wire both get_db and get_writable_db
    to connections pointing at that file.

    An on-disk file (not :memory:) is required so DuckDB allows a simultaneous
    read-only and a read-write connection without conflict — the same pattern
    used by test_strategy_memos_crud.py.
    """
    from src.api.dependencies import get_db, get_writable_db

    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
        db_path = f.name

    # Remove the empty placeholder — DatabaseConnector will create it fresh.
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

    yield TestClient(app), db_path

    app.dependency_overrides.clear()
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_post_goals_persists_row(goals_client):
    """POST /analytics/goals must insert a row that is visible in a direct DB query."""
    client, db_path = goals_client

    payload = {
        "name": "Retirement Fund",
        "target_amount": 5000000.0,
        "target_date": "2035-01-01",
        "current_amount": 100000.0,
        "monthly_contribution": 5000.0,
        "goal_type": "retirement",
    }
    resp = client.post("/analytics/goals", json=payload)
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code} — {resp.text}"

    body = resp.json()
    assert "error" not in body, f"Route returned error: {body.get('error')}"
    assert body.get("id") is not None, "Response missing 'id' field"
    goal_id = body["id"]

    # Verify the row was actually written to the database.
    verify_conn = DatabaseConnector(db_path, read_only=True)
    row = verify_conn.execute(
        "SELECT name, target_amount FROM goals WHERE id = ?", [goal_id]
    ).fetchone()
    verify_conn.close()

    assert row is not None, f"No row found in goals table for id={goal_id}"
    assert row[0] == "Retirement Fund"
    assert float(row[1]) == 5000000.0


def test_delete_goals_removes_row(goals_client):
    """DELETE /analytics/goals/{id} must remove the row from the database."""
    client, db_path = goals_client

    # Insert a row directly so we have a known ID to delete.
    setup_conn = DatabaseConnector(db_path, read_only=False)
    setup_conn.execute("""
        INSERT INTO goals (name, target_amount, target_date, current_amount,
                           monthly_contribution, goal_type, status)
        VALUES ('Education Fund', 200000.0, '2030-06-01', 0.0, 1000.0, 'education', 'active')
    """)
    row_id = setup_conn.execute("SELECT MAX(id) FROM goals").fetchone()[0]
    setup_conn.close()

    assert row_id is not None, "Setup insert did not create a row"

    resp = client.delete(f"/analytics/goals/{row_id}")
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code} — {resp.text}"

    body = resp.json()
    assert "error" not in body, f"Route returned error: {body.get('error')}"
    assert body.get("success") is True, f"Expected success=True, got: {body}"

    # Verify the row is gone.
    verify_conn = DatabaseConnector(db_path, read_only=True)
    row = verify_conn.execute(
        "SELECT id FROM goals WHERE id = ?", [row_id]
    ).fetchone()
    verify_conn.close()

    assert row is None, f"Row id={row_id} still exists in goals table after DELETE"


# ---------------------------------------------------------------------------
# Owner-reported defect (2026-07-26): Goals card showed different current/
# monthly than the Your Path tab because GET /analytics/goals served frozen
# `current_amount`/`monthly_contribution` columns instead of live data.
# These tests guard the fix — the `live` block must always equal a direct
# call to the SAME source functions, so a future change can't silently
# re-fork the two tabs again.
# ---------------------------------------------------------------------------


def test_get_goals_live_block_matches_source_functions(goals_client):
    """GET /analytics/goals `live` block must equal _default_net_worth /
    _contribution_run_rate called directly against the same DB — never a
    second, independently-derived figure."""
    from src.services.north_star_glide import _contribution_run_rate, _default_net_worth

    client, db_path = goals_client

    setup_conn = DatabaseConnector(db_path, read_only=False)
    setup_conn.execute("""
        INSERT INTO goals (name, target_amount, target_date, current_amount,
                           monthly_contribution, goal_type, status)
        VALUES ('FIRE', 20000000.0, '2040-01-01', 3000000.0, 10000.0, 'retirement', 'active')
    """)
    setup_conn.close()

    resp = client.get("/analytics/goals")
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code} — {resp.text}"
    body = resp.json()
    assert len(body) == 1
    goal = body[0]

    assert "live" in goal, "GET /analytics/goals must return a `live` sub-object"

    verify_conn = DatabaseConnector(db_path, read_only=True)
    expected_current = _default_net_worth(verify_conn)
    expected_monthly, expected_status = _contribution_run_rate(verify_conn)
    verify_conn.close()

    assert goal["live"]["current_amount"] == round(expected_current, 2)
    assert goal["live"]["monthly_contribution"] == (
        round(expected_monthly, 2) if expected_monthly is not None else None
    )
    assert goal["live"]["run_rate_status"] == expected_status

    # The legacy stored columns must still be present (backward compat) but
    # must NOT silently equal the live block by coincidence-masking bug —
    # this fixture's stored values (3,000,000 / 10,000) are deliberately the
    # owner's reported stale figures, distinct from an empty test DB's live
    # observations (0 / unavailable), so a regression that reads the stored
    # columns instead of live would be caught by the assertions above.
    assert goal["current_amount"] == 3000000.0
    assert goal["monthly_contribution"] == 10000.0


def test_put_goals_updates_editable_fields_only(goals_client):
    """PUT /analytics/goals/{id} must update name/target_amount/target_date/
    goal_type/status/notes and persist to the DB, but must NEVER write
    current_amount/monthly_contribution (those are live-derived, not
    editable — accepting them as inputs would recreate the two-sources bug
    this endpoint exists to fix)."""
    client, db_path = goals_client

    setup_conn = DatabaseConnector(db_path, read_only=False)
    setup_conn.execute("""
        INSERT INTO goals (name, target_amount, target_date, current_amount,
                           monthly_contribution, goal_type, status)
        VALUES ('FIRE', 20000000.0, '2040-01-01', 3000000.0, 10000.0, 'retirement', 'active')
    """)
    row_id = setup_conn.execute("SELECT MAX(id) FROM goals").fetchone()[0]
    setup_conn.close()

    resp = client.put(
        f"/analytics/goals/{row_id}",
        json={"name": "FIRE (updated)", "target_amount": 22000000.0, "target_date": "2041-06-01"},
    )
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code} — {resp.text}"
    body = resp.json()
    assert "error" not in body, f"Route returned error: {body.get('error')}"
    assert body["name"] == "FIRE (updated)"
    assert body["target_amount"] == 22000000.0
    assert body["target_date"] == "2041-06-01"

    verify_conn = DatabaseConnector(db_path, read_only=True)
    row = verify_conn.execute(
        "SELECT name, target_amount, target_date, current_amount, monthly_contribution "
        "FROM goals WHERE id = ?",
        [row_id],
    ).fetchone()
    verify_conn.close()

    assert row[0] == "FIRE (updated)"
    assert float(row[1]) == 22000000.0
    assert str(row[2]) == "2041-06-01"
    # Legacy stored columns must be untouched by the edit — PUT never writes them.
    assert float(row[3]) == 3000000.0
    assert float(row[4]) == 10000.0


def test_goal_probability_never_defaults_to_hardcoded_constants(goals_client):
    """GET /analytics/goals/{id}/probability must not silently fall back to
    the old hardcoded 0.07/0.15 — on a DB with no market/income history the
    live basis is unavailable, and the endpoint must say so explicitly
    rather than fabricate a probability from made-up constants."""
    client, db_path = goals_client

    setup_conn = DatabaseConnector(db_path, read_only=False)
    setup_conn.execute("""
        INSERT INTO goals (name, target_amount, target_date, current_amount,
                           monthly_contribution, goal_type, status)
        VALUES ('FIRE', 20000000.0, '2040-01-01', 3000000.0, 10000.0, 'retirement', 'active')
    """)
    row_id = setup_conn.execute("SELECT MAX(id) FROM goals").fetchone()[0]
    setup_conn.close()

    resp = client.get(f"/analytics/goals/{row_id}/probability")
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code} — {resp.text}"
    body = resp.json()

    assert body["probability"] is None
    assert body["status"] == "unavailable"
    assert body.get("reason")
