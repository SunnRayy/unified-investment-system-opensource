"""Tests for src/api/routes/manual_pnl.py (#7, plan §C.4).

Uses a tmp_path DuckDB through bootstrap_database (schema + ALL migrations,
including V86) — never touches data/unified.duckdb.

The write path carries three obligations beyond "it stores the number":
an audit row per write, `mark_dirty()` so the cloud flushes the change to GCS
(the V7.3.0 lesson — owner edits that never reached GCS looked like they had
saved), and a 400 on an override with neither figure, which would otherwise be a
silent no-op indistinguishable from no override.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_db, get_writable_db
from src.api.main import app
from src.database.connector import DatabaseConnector
from src.database.schema import bootstrap_database

pytestmark = pytest.mark.pipeline

URL = "/holdings/{aid}/manual-pnl"
BOND = "Bond_CMB_CNY"
MMF = "CASH_MMF_TEST"


@pytest.fixture
def db(tmp_path):
    connector = DatabaseConnector(str(tmp_path / "manual_pnl_api.duckdb"))
    bootstrap_database(connector)
    connector.execute(
        "INSERT INTO asset_registry (canonical_id, display_name, asset_class) VALUES (?, ?, ?)",
        [BOND, "招行固收债券", "CN Bonds"],
    )
    connector.execute(
        "INSERT INTO asset_registry (canonical_id, display_name, asset_class) VALUES (?, ?, ?)",
        [MMF, "货币基金", "Money Market"],
    )
    yield connector
    connector.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_writable_db] = lambda: db
    # mark_dirty() talks to the GCS flush layer; assert the call, not the upload.
    with patch("src.api.routes.manual_pnl.mark_dirty") as mock_dirty:
        c = TestClient(app)
        c.mock_dirty = mock_dirty
        yield c
    app.dependency_overrides.clear()


def _audit(db, asset_id):
    return db.execute(
        "SELECT action, old_value, new_value FROM manual_asset_pnl_audit "
        "WHERE asset_id = ? ORDER BY id",
        [asset_id],
    ).fetchall()


# ── upsert ────────────────────────────────────────────────────────────────────

def test_put_creates_an_override(client, db):
    r = client.put(URL.format(aid=BOND), json={
        "cost_basis_cny": 185000.00,
        "realized_pnl_cny": 4200.00,
        "as_of_date": "2026-08-01",
        "memo": "招行债券 coupon to date",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["asset_id"] == BOND
    assert body["cost_basis_cny"] == pytest.approx(185000.00)
    assert body["realized_pnl_cny"] == pytest.approx(4200.00)
    assert body["memo"] == "招行债券 coupon to date"
    assert body["superseded"] is False
    assert body["cost_affects_unrealized"] is True

    stored = db.execute(
        "SELECT cost_basis_cny, realized_pnl_cny FROM manual_asset_pnl WHERE asset_id = ?",
        [BOND],
    ).fetchone()
    assert float(stored[0]) == pytest.approx(185000.00)


def test_put_writes_an_audit_row_and_marks_dirty(client, db):
    client.put(URL.format(aid=BOND), json={"realized_pnl_cny": 4200.00})
    rows = _audit(db, BOND)
    assert len(rows) == 1
    action, old_value, new_value = rows[0]
    assert action == "create"
    assert old_value is None
    assert json.loads(new_value)["realized_pnl_cny"] == pytest.approx(4200.00)
    assert client.mock_dirty.called, "mark_dirty() not called — a cloud write would not flush to GCS"


def test_put_twice_updates_and_records_the_before_state(client, db):
    client.put(URL.format(aid=BOND), json={"realized_pnl_cny": 4200.00})
    r = client.put(URL.format(aid=BOND), json={"realized_pnl_cny": 5100.00, "memo": "Q3 coupon"})
    assert r.status_code == 200
    assert r.json()["realized_pnl_cny"] == pytest.approx(5100.00)

    # Still ONE row (asset_id is the primary key) ...
    assert db.execute("SELECT COUNT(*) FROM manual_asset_pnl").fetchone()[0] == 1
    # ... and the audit trail keeps the prior value.
    rows = _audit(db, BOND)
    assert [r[0] for r in rows] == ["create", "update"]
    assert json.loads(rows[1][1])["realized_pnl_cny"] == pytest.approx(4200.00)
    assert json.loads(rows[1][2])["realized_pnl_cny"] == pytest.approx(5100.00)


def test_put_accepts_cost_only_and_realized_only(client):
    assert client.put(URL.format(aid=BOND), json={"cost_basis_cny": 185000.0}).status_code == 200
    assert client.put(URL.format(aid=MMF), json={"realized_pnl_cny": 1850.0}).status_code == 200


def test_put_rejects_an_override_with_neither_figure(client, db):
    r = client.put(URL.format(aid=BOND), json={"memo": "just a note"})
    assert r.status_code == 400
    assert "cost_basis_cny" in r.json()["detail"]
    assert db.execute("SELECT COUNT(*) FROM manual_asset_pnl").fetchone()[0] == 0


def test_put_rejects_an_unknown_asset(client):
    r = client.put(URL.format(aid="NOPE_NOT_AN_ASSET"), json={"realized_pnl_cny": 1.0})
    assert r.status_code == 404


def test_cash_equivalent_reports_that_cost_does_not_move_unrealized(client):
    """Engine rule §C.1.1: a cash balance has no price basis, so a logged cost is
    stored but yields no unrealized gain. The response must say so, or the UI
    would show a cost the P&L silently ignores."""
    r = client.put(URL.format(aid=MMF), json={"cost_basis_cny": 90000.0})
    assert r.status_code == 200
    assert r.json()["cost_affects_unrealized"] is False


# ── delete ────────────────────────────────────────────────────────────────────

def test_delete_clears_the_override_and_audits_the_old_value(client, db):
    client.put(URL.format(aid=BOND), json={"cost_basis_cny": 185000.0, "realized_pnl_cny": 4200.0})
    r = client.delete(URL.format(aid=BOND))
    assert r.status_code == 200
    assert r.json() == {"asset_id": BOND, "deleted": True}
    assert db.execute("SELECT COUNT(*) FROM manual_asset_pnl").fetchone()[0] == 0

    rows = _audit(db, BOND)
    assert [r[0] for r in rows] == ["create", "delete"]
    assert json.loads(rows[1][1])["cost_basis_cny"] == pytest.approx(185000.0)
    assert rows[1][2] is None, "a cleared override must remain reconstructible from the audit trail"


def test_delete_missing_override_is_404(client):
    assert client.delete(URL.format(aid=BOND)).status_code == 404


# ── list ──────────────────────────────────────────────────────────────────────

def test_list_is_empty_then_reflects_writes(client):
    assert client.get("/holdings/manual-pnl").json() == []
    client.put(URL.format(aid=BOND), json={"realized_pnl_cny": 4200.0})
    client.put(URL.format(aid=MMF), json={"realized_pnl_cny": 1850.0})

    body = client.get("/holdings/manual-pnl").json()
    assert [row["asset_id"] for row in body] == sorted([BOND, MMF])
    by_id = {row["asset_id"]: row for row in body}
    assert by_id[MMF]["cost_affects_unrealized"] is False
    assert by_id[BOND]["cost_affects_unrealized"] is True


# ── staleness: the buy/sell case ──────────────────────────────────────────────

def _set_balance(db, asset_id, value, snapshot="2026-08-01"):
    db.execute("DELETE FROM holdings WHERE asset_id = ?", [asset_id])
    db.execute(
        """INSERT INTO holdings
           (asset_id, snapshot_date, quantity, market_value, currency, source_system, is_shadow)
           VALUES (?, ?, 1.0, ?, 'CNY', 'Financial_Summary_Excel', FALSE)""",
        [asset_id, snapshot, value],
    )


def test_logging_stamps_the_balance_it_was_entered_against(client, db):
    _set_balance(db, BOND, 200_000.00)
    r = client.put(URL.format(aid=BOND), json={"cost_basis_cny": 195_000.00})
    assert r.json()["market_value_at_log"] == pytest.approx(200_000.00)
    assert r.json()["value_looks_stale"] is False


def test_buying_more_marks_the_logged_cost_stale(client, db):
    """The scenario this exists for: ¥50K added to a ¥200K bond. The logged cost
    still says ¥195K, so without a prompt the new principal reads as ¥50K of
    profit the owner never made."""
    _set_balance(db, BOND, 200_000.00)
    client.put(URL.format(aid=BOND), json={"cost_basis_cny": 195_000.00})

    _set_balance(db, BOND, 250_000.00)          # bought 50K more
    row = next(r for r in client.get("/holdings/manual-pnl").json() if r["asset_id"] == BOND)

    assert row["value_looks_stale"] is True
    assert row["value_move_pct"] == pytest.approx(25.0)
    assert row["current_market_value"] == pytest.approx(250_000.00)
    assert row["market_value_at_log"] == pytest.approx(200_000.00)
    # The stored cost is NOT adjusted — guessing the owner's new cost would be
    # inventing a number, which is the phantom in a different costume.
    assert row["cost_basis_cny"] == pytest.approx(195_000.00)


def test_selling_part_marks_the_logged_cost_stale(client, db):
    _set_balance(db, BOND, 200_000.00)
    client.put(URL.format(aid=BOND), json={"cost_basis_cny": 195_000.00})

    _set_balance(db, BOND, 150_000.00)          # sold 50K
    row = next(r for r in client.get("/holdings/manual-pnl").json() if r["asset_id"] == BOND)
    assert row["value_looks_stale"] is True
    assert row["value_move_pct"] == pytest.approx(-25.0)


def test_interest_accrual_does_not_trip_the_stale_flag(client, db):
    """A bond ticking up ¥109 on ¥200K is the profit working as intended, not a
    deposit. The threshold has to tell those apart or the warning is noise."""
    _set_balance(db, BOND, 200_000.00)
    client.put(URL.format(aid=BOND), json={"cost_basis_cny": 200_000.00})

    _set_balance(db, BOND, 200_109.00)
    row = next(r for r in client.get("/holdings/manual-pnl").json() if r["asset_id"] == BOND)
    assert row["value_looks_stale"] is False
    assert row["value_move_pct"] == pytest.approx(0.05, abs=0.01)


def test_relogging_clears_the_stale_flag(client, db):
    _set_balance(db, BOND, 200_000.00)
    client.put(URL.format(aid=BOND), json={"cost_basis_cny": 195_000.00})
    _set_balance(db, BOND, 250_000.00)

    # Owner updates the cost to include the new money.
    r = client.put(URL.format(aid=BOND), json={"cost_basis_cny": 245_000.00})
    assert r.json()["value_looks_stale"] is False
    assert r.json()["market_value_at_log"] == pytest.approx(250_000.00)


def test_realized_only_override_is_never_marked_stale(client, db):
    """A running profit total is not invalidated by a later deposit — only a
    whole-position COST is."""
    _set_balance(db, BOND, 200_000.00)
    client.put(URL.format(aid=BOND), json={"realized_pnl_cny": 4_200.00})
    _set_balance(db, BOND, 250_000.00)

    row = next(r for r in client.get("/holdings/manual-pnl").json() if r["asset_id"] == BOND)
    assert row["value_looks_stale"] is False


def test_list_flags_a_superseded_override(client, db):
    """An asset the reader ledger has taken over: the engine ignores the override,
    so the API must surface it as superseded rather than imply it is live."""
    client.put(URL.format(aid=BOND), json={"realized_pnl_cny": 4200.0})
    db.execute(
        """INSERT INTO transactions
           (asset_id, transaction_date, transaction_type, quantity, price_unit,
            amount_net, currency, source_system)
           VALUES (?, DATE '2026-07-01', 'buy', 10, 100, 1000, 'CNY', 'Schwab_CSV')""",
        [BOND],
    )
    row = next(r for r in client.get("/holdings/manual-pnl").json() if r["asset_id"] == BOND)
    assert row["superseded"] is True
