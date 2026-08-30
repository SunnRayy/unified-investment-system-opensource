"""Tests for src/api/routes/value_trap.py (PRD 2026-07-07 F2, Batch B3).

In-memory DuckDB via initialize_schema (never a bare, schema-less connector).

Fix 2 tests (2026-07-10): memo linkage states, confirm-no-memo endpoint,
  linkage_ack gate for ruling on unresolved assets.
Fix 3 tests (2026-07-10): AI draft prompt constraints — reduced context
  excludes loss numbers; system prompt contains inadmissibility clause;
  linked memo surfaces falsification_summary; unresolved linkage mandates
  "confirm" language.
Fix 4 tests (2026-07-10): Hold ruling requires next_review_date.
"""
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
def client(monkeypatch):
    """TestClient wired to an in-memory DB, with FX lookup patched to avoid
    real network calls (all fixture holdings below are CNY-denominated, but
    scan_value_traps() unconditionally calls get_today_usd_cny_rate())."""
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)

    test_conn = DatabaseConnector(":memory:")
    initialize_schema(test_conn)
    # Program OSR WS-3c: memo_registry/memo_asset_map seeds moved out of
    # schema.sql into the seed-pack system — test session runs under
    # $UIS_SEED_PROFILE=example (tests/conftest.py), so this populates the
    # persona's example memos (2026-Q2-EX2 links CN_FUND_110020/161725).
    seed_demo_content(test_conn)

    def override_get_db():
        return test_conn

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), test_conn
    app.dependency_overrides.clear()
    test_conn.close()


def _insert_holding(
    conn: DatabaseConnector,
    asset_id: str,
    name: str,
    loss_pct: float,
    *,
    # Dynamic near-today default — a hardcoded date ages past the 7-day
    # staleness window and silently flips scan tests to deferred_unreliable.
    snapshot_date: str | None = None,
    cost_price_unit: float = 10.0,
    quantity: float = 1000.0,
    currency: str = "CNY",
) -> None:
    if snapshot_date is None:
        snapshot_date = (datetime.now() - timedelta(days=1)).date().isoformat()
    market_value = round(quantity * cost_price_unit * (1 + loss_pct / 100.0), 2)
    market_price_unit = market_value / quantity if quantity else 0.0
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, quantity, cost_price_unit,
             market_price_unit, market_value, currency, source_system, is_shadow)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'test', FALSE)
        """,
        [snapshot_date, asset_id, name, quantity, cost_price_unit,
         market_price_unit, market_value, currency],
    )


def _insert_review(
    conn: DatabaseConnector,
    asset_id: str,
    *,
    status: str = "open",
    trigger_threshold_pct: float = -25.0,
    unrealized_return_pct: float = -30.0,
    opened_at=None,
) -> int:
    opened_at = opened_at or datetime.now()
    conn.execute(
        """
        INSERT INTO value_trap_reviews
            (asset_id, asset_name, status, trigger_threshold_pct,
             unrealized_return_pct, opened_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [asset_id, asset_id, status, trigger_threshold_pct, unrealized_return_pct, opened_at],
    )
    return conn.execute(
        "SELECT id FROM value_trap_reviews WHERE asset_id = ? ORDER BY id DESC LIMIT 1",
        [asset_id],
    ).fetchone()[0]


# ── Test 4: liquidate without adversarial_ack -> 422 ────────────────────────

def test_liquidate_without_adversarial_ack_returns_422(client):
    test_client, conn = client
    review_id = _insert_review(conn, "CN_FUND_900014")

    resp = test_client.put(
        f"/reviews/value-trap/{review_id}",
        json={
            "thesis_restated": "still a long-term hold",
            "falsification_check": "checked",
            "would_buy_today": "no",
            "ruling": "liquidate",
            "adversarial_ack": False,
        },
    )
    assert resp.status_code == 422
    assert "adversarial_ack" in resp.json()["detail"]

    # Row must remain untouched (still open, no ruling persisted).
    row = conn.execute(
        "SELECT status, ruling FROM value_trap_reviews WHERE id = ?", [review_id]
    ).fetchone()
    assert row[0] == "open"
    assert row[1] is None


def test_liquidate_with_adversarial_ack_saves(client):
    test_client, conn = client
    # Insert a memo so linkage is 'linked' (no linkage_ack needed).
    conn.execute(
        "INSERT INTO memo_registry (memo_id, title, status) VALUES (?, ?, 'active')",
        ["MEMO_900014", "Fund 900014 thesis"],
    )
    conn.execute(
        "INSERT INTO memo_asset_map (memo_id, asset_id) VALUES (?, ?)",
        ["MEMO_900014", "CN_FUND_900014"],
    )
    review_id = _insert_review(conn, "CN_FUND_900014")

    resp = test_client.put(
        f"/reviews/value-trap/{review_id}",
        json={
            "thesis_restated": "thesis broken",
            "falsification_check": "failed",
            "would_buy_today": "no",
            "ruling": "liquidate",
            "adversarial_ack": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ruled"
    assert body["ruling"] == "liquidate"
    assert body["adversarial_ack"] is True


# ── F2.2: hold_with_thesis sets the escalation-ladder next threshold ───────

def test_hold_with_thesis_sets_next_trigger_threshold(client):
    test_client, conn = client
    # Use confirmed_none so no linkage_ack needed.
    conn.execute(
        "INSERT INTO asset_memo_confirmations (asset_id, confirmed_no_memo) VALUES (?, TRUE)",
        ["CN_FUND_900014"],
    )
    review_id = _insert_review(conn, "CN_FUND_900014", trigger_threshold_pct=-25.0)

    resp = test_client.put(
        f"/reviews/value-trap/{review_id}",
        json={
            "thesis_restated": "thesis intact",
            "falsification_check": "checked, still valid",
            "would_buy_today": "yes",
            "ruling": "hold_with_thesis",
            "next_review_date": "2026-10-01",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ruled"
    assert body["last_ruling"] == "hold_with_thesis"
    assert float(body["next_trigger_threshold_pct"]) == -35.0


def test_invalid_ruling_returns_422(client):
    test_client, conn = client
    review_id = _insert_review(conn, "CN_FUND_900014")
    resp = test_client.put(
        f"/reviews/value-trap/{review_id}",
        json={"ruling": "not_a_real_ruling"},
    )
    assert resp.status_code == 422


# ── Test 5: overdue (>14d) open review flagged in GET list ─────────────────

def test_overdue_open_review_flagged_in_list(client):
    test_client, conn = client
    _insert_review(
        conn, "CN_FUND_900014", opened_at=datetime.now() - timedelta(days=15)
    )
    _insert_review(
        conn, "CN_FUND_900011", opened_at=datetime.now() - timedelta(days=2)
    )

    resp = test_client.get("/reviews/value-trap", params={"status": "open"})
    assert resp.status_code == 200
    rows = resp.json()
    by_asset = {r["asset_id"]: r for r in rows}
    assert by_asset["CN_FUND_900014"]["overdue"] is True
    assert by_asset["CN_FUND_900014"]["days_open"] >= 15
    assert by_asset["CN_FUND_900011"]["overdue"] is False


def test_pending_count_endpoint(client):
    test_client, conn = client
    _insert_review(conn, "CN_FUND_900014", opened_at=datetime.now() - timedelta(days=20))
    _insert_review(conn, "CN_FUND_900011", opened_at=datetime.now() - timedelta(days=1))
    _insert_review(conn, "CN_FUND_900010", status="ruled", opened_at=datetime.now() - timedelta(days=40))

    resp = test_client.get("/reviews/value-trap/pending-count")
    assert resp.status_code == 200
    body = resp.json()
    assert body["open"] == 2
    assert body["overdue"] == 1


def test_scan_endpoint_opens_review(client):
    test_client, conn = client
    _insert_holding(conn, "CN_FUND_900014", "Fund 900014", -40.9)
    _insert_holding(conn, "US_STK_FBTC", "FBTC", -30.0)  # ratio, excluded

    resp = test_client.post("/reviews/value-trap/scan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["hits"] == 1
    assert body["opened"] == 1
    assert body["skipped_bucket"] == 1

    listed = test_client.get("/reviews/value-trap", params={"status": "open"}).json()
    assert {r["asset_id"] for r in listed} == {"CN_FUND_900014"}


# ── WS2: GET /{id}/context endpoint ─────────────────────────────────────────

def _insert_trade_log(
    conn: DatabaseConnector,
    asset_id: str,
    *,
    action: str = "buy",
    log_date: str = "2026-07-01",
    quantity: float = 500.0,
    price: float = 10.0,
    rule_bucket: str | None = "value",
    memo_id: str | None = None,
    verification_status: str = "pending",
) -> int:
    conn.execute(
        """
        INSERT INTO trade_logs
            (log_date, asset_id, action, quantity, price,
             rule_bucket, memo_id, verification_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [log_date, asset_id, action, quantity, price,
         rule_bucket, memo_id, verification_status],
    )
    return conn.execute(
        "SELECT id FROM trade_logs WHERE asset_id = ? ORDER BY id DESC LIMIT 1",
        [asset_id],
    ).fetchone()[0]


def test_context_endpoint_returns_position_and_loss(client):
    # Use an asset not in the memo seed data (CN_FUND_900014 is seeded as linked
    # to 2026-Q2-007; use a neutral test asset instead).
    test_client, conn = client
    asset_id = "CN_FUND_SHAPE_TEST"
    _insert_holding(conn, asset_id, "Shape Test Fund", -30.0, snapshot_date="2026-07-05")
    review_id = _insert_review(conn, asset_id, unrealized_return_pct=-30.0)

    resp = test_client.get(f"/reviews/value-trap/{review_id}/context")
    assert resp.status_code == 200
    body = resp.json()

    # Top-level shape
    assert body["review_id"] == review_id
    assert body["asset_id"] == asset_id

    # Position section
    pos = body["position"]
    assert pos is not None
    assert pos["qty"] > 0
    assert pos["cost_price_unit"] > 0
    assert pos["market_price_unit"] > 0
    assert pos["market_value"] > 0
    assert pos["snapshot_date"] == "2026-07-05"
    assert pos["currency"] == "CNY"

    # Loss section
    loss = body["loss"]
    assert loss["unrealized_return_pct"] == pytest.approx(-30.0, abs=0.1)
    assert loss["trigger_threshold_pct"] == -25.0
    assert loss["days_open"] is not None and loss["days_open"] >= 0

    # Fix 2: memo_linkage replaces originating_memo; unresolved for this asset
    assert "memo_linkage" in body
    assert body["memo_linkage"]["state"] == "unresolved"
    assert body["memo_linkage"]["display_text"] is not None
    assert "no memo on record" not in body["memo_linkage"]["display_text"]

    # Decision history: empty
    assert isinstance(body["decision_history"], list)

    # Case file
    assert body["case_file"]["asset_id"] == asset_id


# ── Fix 2: memo linkage state tests ─────────────────────────────────────────

def test_context_endpoint_linked_memo_shows_state_and_summary(client):
    """110020 fixture: seeded 2026-Q2-EX2 (persona pack) appears in context with falsification_summary."""
    test_client, conn = client
    asset_id = "CN_FUND_110020"
    _insert_holding(conn, asset_id, "Fund 110020", -29.4, snapshot_date="2026-07-05")
    review_id = _insert_review(conn, asset_id, unrealized_return_pct=-29.4)

    resp = test_client.get(f"/reviews/value-trap/{review_id}/context")
    assert resp.status_code == 200
    body = resp.json()

    ml = body["memo_linkage"]
    assert ml["state"] == "linked"
    memo_ids = [m["memo_id"] for m in ml["memos"]]
    assert "2026-Q2-EX2" in memo_ids
    # Falsification summary must be present (seeded from the example seed pack)
    q2_ex2 = next(m for m in ml["memos"] if m["memo_id"] == "2026-Q2-EX2")
    assert q2_ex2["falsification_summary"] is not None
    assert len(q2_ex2["falsification_summary"]) > 0
    # display_text is None for linked state
    assert ml["display_text"] is None


def test_context_endpoint_unresolved_shows_backfill_warning(client):
    """Asset with no memo and no confirmation renders the unresolved copy; never 'no memo on record'."""
    test_client, conn = client
    asset_id = "CN_FUND_UNLINKED_TEST"
    _insert_holding(conn, asset_id, "Unlinked Fund", -28.0, snapshot_date="2026-07-05")
    review_id = _insert_review(conn, asset_id)

    resp = test_client.get(f"/reviews/value-trap/{review_id}/context")
    assert resp.status_code == 200
    body = resp.json()

    ml = body["memo_linkage"]
    assert ml["state"] == "unresolved"
    assert ml["memos"] == []
    assert ml["display_text"] is not None
    # The exact PRD-required string
    assert "Memo linkage not backfilled" in ml["display_text"]
    # The forbidden string must NOT appear unless confirmed_none
    assert "no memo on record" not in ml["display_text"].lower()


def test_context_endpoint_confirmed_none_shows_no_memo(client):
    """After owner confirms no memo, state is confirmed_none and 'no memo on record' is allowed."""
    test_client, conn = client
    asset_id = "CN_FUND_CONFIRMED_NONE"
    conn.execute(
        "INSERT INTO asset_memo_confirmations (asset_id, confirmed_no_memo) VALUES (?, TRUE)",
        [asset_id],
    )
    _insert_holding(conn, asset_id, "Confirmed-none Fund", -27.0, snapshot_date="2026-07-05")
    review_id = _insert_review(conn, asset_id)

    resp = test_client.get(f"/reviews/value-trap/{review_id}/context")
    assert resp.status_code == 200
    body = resp.json()

    ml = body["memo_linkage"]
    assert ml["state"] == "confirmed_none"
    assert ml["memos"] == []
    assert ml["display_text"] is not None
    # "no memo on record" or similar is expected here (the confirmed-none path)
    # The display_text is set to "No memo on record (confirmed by owner)" in the backend.
    assert "confirmed" in ml["display_text"].lower() or "no memo" in ml["display_text"].lower()


# ── Fix 2: confirm-no-memo endpoint ─────────────────────────────────────────

def test_confirm_no_memo_endpoint_sets_flag(client):
    test_client, conn = client
    asset_id = "CN_FUND_NEW_ASSET"

    resp = test_client.put(f"/reviews/value-trap/assets/{asset_id}/confirm-no-memo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["asset_id"] == asset_id
    assert body["confirmed_no_memo"] is True

    # DB record must exist
    row = conn.execute(
        "SELECT confirmed_no_memo FROM asset_memo_confirmations WHERE asset_id = ?",
        [asset_id],
    ).fetchone()
    assert row is not None
    assert row[0] is True


def test_confirm_no_memo_endpoint_is_idempotent(client):
    """Second call must not error and must leave confirmed_no_memo = TRUE."""
    test_client, conn = client
    asset_id = "CN_FUND_IDEMPOTENT"

    resp1 = test_client.put(f"/reviews/value-trap/assets/{asset_id}/confirm-no-memo")
    assert resp1.status_code == 200
    resp2 = test_client.put(f"/reviews/value-trap/assets/{asset_id}/confirm-no-memo")
    assert resp2.status_code == 200
    assert resp2.json()["confirmed_no_memo"] is True


# ── Fix 2: ruling linkage_ack gate ──────────────────────────────────────────

def test_ruling_unresolved_without_linkage_ack_returns_422(client):
    """Unresolved linkage + no linkage_ack must 422."""
    test_client, conn = client
    asset_id = "CN_FUND_UNRESOLVED_RULING"
    review_id = _insert_review(conn, asset_id)

    resp = test_client.put(
        f"/reviews/value-trap/{review_id}",
        json={
            "ruling": "hold_with_thesis",
            "next_review_date": "2026-10-01",
            "linkage_ack": False,
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "unresolved" in detail.lower() or "linkage" in detail.lower()

    # Row must remain open (no ruling persisted).
    row = conn.execute(
        "SELECT status FROM value_trap_reviews WHERE id = ?", [review_id]
    ).fetchone()
    assert row[0] == "open"


def test_ruling_unresolved_with_linkage_ack_saves(client):
    """Unresolved linkage + linkage_ack=True must succeed."""
    test_client, conn = client
    asset_id = "CN_FUND_UNRESOLVED_ACK"
    review_id = _insert_review(conn, asset_id)

    resp = test_client.put(
        f"/reviews/value-trap/{review_id}",
        json={
            "ruling": "hold_with_thesis",
            "next_review_date": "2026-10-01",
            "linkage_ack": True,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ruled"


def test_ruling_linked_asset_no_linkage_ack_needed(client):
    """Linked asset must not require linkage_ack."""
    test_client, conn = client
    asset_id = "US_STK_VOO"
    # VOO is seeded as linked via the example seed pack (2026-Q2-EX1)
    review_id = _insert_review(conn, asset_id)

    resp = test_client.put(
        f"/reviews/value-trap/{review_id}",
        json={
            "ruling": "hold_with_thesis",
            "next_review_date": "2026-10-01",
            # linkage_ack intentionally omitted — linked assets don't need it
        },
    )
    assert resp.status_code == 200


# ── Fix 4: no-default ruling / Hold requires next_review_date ───────────────

def test_hold_without_next_review_date_returns_422(client):
    """Fix 4: Hold ruling without next_review_date must 422."""
    test_client, conn = client
    # Use confirmed_none so no linkage_ack needed.
    conn.execute(
        "INSERT INTO asset_memo_confirmations (asset_id, confirmed_no_memo) VALUES (?, TRUE)",
        ["CN_FUND_HOLD_NO_DATE"],
    )
    review_id = _insert_review(conn, "CN_FUND_HOLD_NO_DATE")

    resp = test_client.put(
        f"/reviews/value-trap/{review_id}",
        json={
            "ruling": "hold_with_thesis",
            # next_review_date intentionally omitted
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "next_review_date" in detail


def test_hold_with_next_review_date_saves(client):
    """Hold with next_review_date must save successfully."""
    test_client, conn = client
    conn.execute(
        "INSERT INTO asset_memo_confirmations (asset_id, confirmed_no_memo) VALUES (?, TRUE)",
        ["CN_FUND_HOLD_WITH_DATE"],
    )
    review_id = _insert_review(conn, "CN_FUND_HOLD_WITH_DATE", trigger_threshold_pct=-25.0)

    resp = test_client.put(
        f"/reviews/value-trap/{review_id}",
        json={
            "ruling": "hold_with_thesis",
            "next_review_date": "2026-10-01",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ruled"
    assert body["next_review_date"] is not None


# ── WS2: GET /{id}/context — existing coverage (updated for Fix 2 shape) ────

def test_context_endpoint_decision_history_up_to_five(client):
    test_client, conn = client
    asset_id = "CN_FUND_HISTORY_TEST"
    _insert_holding(conn, asset_id, "History Test Fund", -30.0, snapshot_date="2026-07-05")
    review_id = _insert_review(conn, asset_id)

    # Insert 7 trade logs — expect only the last 5 returned
    for i in range(7):
        _insert_trade_log(conn, asset_id, log_date=f"2026-0{i // 3 + 1}-{15 - i:02d}")

    resp = test_client.get(f"/reviews/value-trap/{review_id}/context")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["decision_history"]) == 5


def test_context_endpoint_404_for_missing_review(client):
    test_client, _ = client
    resp = test_client.get("/reviews/value-trap/99999/context")
    assert resp.status_code == 404


# ── WS2: POST /{id}/draft endpoint ──────────────────────────────────────────

def test_draft_endpoint_503_when_no_llm_key(client, monkeypatch):
    """Draft endpoint returns 503 with a clear message when no LLM key is set."""
    test_client, conn = client
    asset_id = "CN_FUND_900014"
    review_id = _insert_review(conn, asset_id)

    # Ensure no LLM keys are visible to is_available()
    monkeypatch.setattr(
        "src.services.llm_client.LLMClient.is_available",
        lambda self: False,
    )

    resp = test_client.post(f"/reviews/value-trap/{review_id}/draft")
    assert resp.status_code == 503
    body = resp.json()
    detail = body.get("detail", "")
    assert "LLM" in detail or "API key" in detail or "key" in detail.lower()


def test_draft_endpoint_returns_three_fields_when_mocked(client, monkeypatch):
    """Draft endpoint calls LLM client and returns the three draft fields."""
    test_client, conn = client
    asset_id = "CN_FUND_900014"
    _insert_holding(conn, asset_id, "Fund 900014", -30.0, snapshot_date="2026-07-05")
    review_id = _insert_review(conn, asset_id)

    # Mock LLMClient: is_available=True, complete() returns pre-canned JSON
    class _MockResponse:
        model_used = "mock-model"
        content_json = {
            "thesis_draft": "Original thesis: high-growth fund. May not hold given -30% loss.",
            "falsification_draft": "Thesis breaks if NAV declines further >10%. Not yet triggered.",
            "buy_today_draft": "Would not initiate fresh position at current loss level.",
        }

    monkeypatch.setattr(
        "src.services.llm_client.LLMClient.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "src.services.llm_client.LLMClient.complete",
        lambda self, **kwargs: _MockResponse(),
    )

    resp = test_client.post(f"/reviews/value-trap/{review_id}/draft")
    assert resp.status_code == 200
    body = resp.json()
    assert "thesis_draft" in body
    assert "falsification_draft" in body
    assert "buy_today_draft" in body
    assert body["model"] == "mock-model"
    assert "Original thesis" in body["thesis_draft"]


def test_draft_endpoint_404_for_missing_review(client, monkeypatch):
    """Draft endpoint returns 404 when review_id does not exist."""
    test_client, _ = client
    monkeypatch.setattr(
        "src.services.llm_client.LLMClient.is_available",
        lambda self: True,
    )
    resp = test_client.post("/reviews/value-trap/99999/draft")
    assert resp.status_code == 404


# ── Fix 3: prompt constraint tests (mock-LLM, inspect outbound prompt) ──────

class _PromptCapture:
    """Mock LLMClient that captures the system_prompt and user_prompt for assertion."""
    model_used = "mock-model"
    content_json = {
        "thesis_draft": "Thesis placeholder",
        "falsification_draft": "Falsification placeholder",
        "buy_today_draft": "Buy today placeholder",
    }
    captured_system_prompt: str = ""
    captured_user_prompt: str = ""

    def __call__(self, **kwargs):
        _PromptCapture.captured_system_prompt = kwargs.get("system_prompt", "")
        _PromptCapture.captured_user_prompt = kwargs.get("user_prompt", "")
        return self


def test_draft_prompt_excludes_loss_pct_for_900013(client, monkeypatch):
    """Fix 3: outbound context must not contain the -29.4 loss figure."""
    test_client, conn = client
    asset_id = "CN_FUND_900013"
    # Use the PRD reference snapshot: cost 3.9266, price 2.7730 → -29.4%
    _insert_holding(
        conn, asset_id, "Fund 900013", -29.4,
        cost_price_unit=3.9266, quantity=10000.0,
        snapshot_date="2026-07-05",
    )
    review_id = _insert_review(
        conn, asset_id, unrealized_return_pct=-29.4, trigger_threshold_pct=-25.0
    )

    capture = _PromptCapture()

    monkeypatch.setattr("src.services.llm_client.LLMClient.is_available", lambda self: True)
    monkeypatch.setattr("src.services.llm_client.LLMClient.complete", capture)

    resp = test_client.post(f"/reviews/value-trap/{review_id}/draft")
    assert resp.status_code == 200

    # The user_prompt (context JSON) must not contain the loss percentage.
    user_prompt = _PromptCapture.captured_user_prompt
    assert "-29.4" not in user_prompt, (
        "Fix 3 violation: loss pct '-29.4' found in LLM user_prompt — "
        "loss numbers must be stripped from the context passed to the LLM"
    )

    # The user_prompt must also not contain the trigger threshold value
    assert "-25.0" not in user_prompt and "-25" not in user_prompt, (
        "Fix 3 violation: trigger threshold found in LLM user_prompt"
    )


def test_draft_prompt_system_forbids_price_as_falsification(client, monkeypatch):
    """Fix 3: system prompt must contain the inadmissibility clause."""
    test_client, conn = client
    asset_id = "CN_FUND_900013"
    _insert_holding(conn, asset_id, "Fund 900013", -29.4, snapshot_date="2026-07-05")
    review_id = _insert_review(conn, asset_id, unrealized_return_pct=-29.4)

    capture = _PromptCapture()
    monkeypatch.setattr("src.services.llm_client.LLMClient.is_available", lambda self: True)
    monkeypatch.setattr("src.services.llm_client.LLMClient.complete", capture)

    resp = test_client.post(f"/reviews/value-trap/{review_id}/draft")
    assert resp.status_code == 200

    system_prompt = _PromptCapture.captured_system_prompt
    # The system prompt must explicitly mark price/loss as inadmissible
    inadmissible_marker = "INADMISSIBLE" in system_prompt or "inadmissible" in system_prompt.lower()
    assert inadmissible_marker, (
        "Fix 3 violation: system prompt does not contain inadmissibility clause"
    )
    # Must also forbid ruling recommendations
    no_ruling_marker = (
        "NO ruling recommendation" in system_prompt
        or "no ruling" in system_prompt.lower()
        or "ruling is the owner" in system_prompt.lower()
    )
    assert no_ruling_marker, (
        "Fix 3 violation: system prompt does not forbid ruling recommendations"
    )


def test_draft_prompt_includes_memo_when_linked_q2_007(client, monkeypatch):
    """Fix 3: when 2026-Q2-EX2 is linked (110020), its falsification_summary appears in prompt."""
    test_client, conn = client
    asset_id = "CN_FUND_110020"
    _insert_holding(conn, asset_id, "Fund 110020", -29.4, snapshot_date="2026-07-05")
    review_id = _insert_review(conn, asset_id, unrealized_return_pct=-29.4)

    capture = _PromptCapture()
    monkeypatch.setattr("src.services.llm_client.LLMClient.is_available", lambda self: True)
    monkeypatch.setattr("src.services.llm_client.LLMClient.complete", capture)

    resp = test_client.post(f"/reviews/value-trap/{review_id}/draft")
    assert resp.status_code == 200

    # The system prompt or user prompt must mention 2026-Q2-EX2
    combined = _PromptCapture.captured_system_prompt + _PromptCapture.captured_user_prompt
    assert "2026-Q2-EX2" in combined, (
        "Fix 3 violation: linked memo 2026-Q2-EX2 not included in the prompt"
    )
    # The falsification_summary text must appear somewhere in the combined prompt
    # (seeded value contains "falsification conditions")
    assert "falsification" in combined.lower(), (
        "Fix 3 violation: memo falsification_summary not included in the prompt"
    )


def test_draft_prompt_mandates_confirm_language_when_unresolved(client, monkeypatch):
    """Fix 3: unresolved linkage → system prompt mandates the 'confirm whether a memo exists' language."""
    test_client, conn = client
    asset_id = "CN_FUND_UNRESOLVED_DRAFT"
    _insert_holding(conn, asset_id, "Unresolved Draft Fund", -28.0, snapshot_date="2026-07-05")
    review_id = _insert_review(conn, asset_id, unrealized_return_pct=-28.0)

    capture = _PromptCapture()
    monkeypatch.setattr("src.services.llm_client.LLMClient.is_available", lambda self: True)
    monkeypatch.setattr("src.services.llm_client.LLMClient.complete", capture)

    resp = test_client.post(f"/reviews/value-trap/{review_id}/draft")
    assert resp.status_code == 200

    system_prompt = _PromptCapture.captured_system_prompt
    # Must instruct the LLM to output the "memo linkage not backfilled / confirm" language
    confirm_marker = (
        "confirm whether a memo exists" in system_prompt.lower()
        or "memo linkage not backfilled" in system_prompt.lower()
    )
    assert confirm_marker, (
        "Fix 3 violation: system prompt does not mandate 'confirm whether a memo exists' "
        "language for unresolved-linkage assets"
    )
    # Must NOT claim the thesis is undocumented
    assert "not formally documented" not in system_prompt.lower(), (
        "Fix 3 violation: system prompt uses the forbidden 'not formally documented' claim"
    )


# ── R2-6: AI draft — forbid cost-basis anchoring in would-buy-today ─────────

def test_draft_prompt_r26_system_contains_would_buy_today_inadmissibility(client, monkeypatch):
    """R2-6(a): system prompt must contain the would-buy-today inadmissibility clause.

    Specifically: must reference the would-buy-today section AND mark the owner's
    historical purchase prices / average cost / entry points as inadmissible.
    """
    test_client, conn = client
    asset_id = "CN_FUND_R26_SYSTEM"
    _insert_holding(conn, asset_id, "R2-6 System Test Fund", -30.0, snapshot_date="2026-07-05")
    review_id = _insert_review(conn, asset_id, unrealized_return_pct=-30.0)

    capture = _PromptCapture()
    monkeypatch.setattr("src.services.llm_client.LLMClient.is_available", lambda self: True)
    monkeypatch.setattr("src.services.llm_client.LLMClient.complete", capture)

    resp = test_client.post(f"/reviews/value-trap/{review_id}/draft")
    assert resp.status_code == 200

    system_prompt = _PromptCapture.captured_system_prompt

    # Must reference the would-buy-today section in a constraint
    assert (
        "would-buy-today" in system_prompt.lower()
        or "would you buy today" in system_prompt.lower()
    ), (
        "R2-6 violation: system prompt has no constraint referencing the 'would-buy-today' section"
    )

    # Must mark something as inadmissible in that section
    assert "inadmissible" in system_prompt.lower(), (
        "R2-6 violation: system prompt does not contain the inadmissibility clause "
        "for the would-buy-today section"
    )

    # Must explicitly name the forbidden anchors (purchase prices, average cost, or entry points)
    forbidden_named = any(phrase in system_prompt.lower() for phrase in [
        "purchase price",
        "average cost",
        "entry point",
        "cost basis",
        "avg cost",
    ])
    assert forbidden_named, (
        "R2-6 violation: system prompt does not name the forbidden cost-basis anchors "
        "(purchase prices / average cost / entry points) in the would-buy-today constraint"
    )

    # Must instruct to cite valuation evidence instead
    valuation_anchor = any(phrase in system_prompt.lower() for phrase in [
        "valuation percentile",
        "valuation evidence",
        "valuation framework",
    ])
    assert valuation_anchor, (
        "R2-6 violation: system prompt does not direct the model to use valuation evidence "
        "in the would-buy-today section"
    )

    # Must specify the data-gap fallback
    assert "would-buy-today assessment requires data" in system_prompt.lower(), (
        "R2-6 violation: system prompt does not specify the data-gap fallback phrase "
        "for the would-buy-today section"
    )


def test_draft_prompt_r26_context_excludes_cost_price_unit_for_900013(client, monkeypatch):
    """R2-6(b): reduced user-prompt context must not contain cost_price_unit or its
    value for the 900013-style fixture (cost basis 3.9266 must not anchor the LLM).

    Approach: strip (consistent with Fix 3 stripping the loss object).
    """
    test_client, conn = client
    asset_id = "CN_FUND_900013"
    # PRD reference fixture: avg cost 3.9266, 10 000 units
    _insert_holding(
        conn, asset_id, "Fund 900013", -29.4,
        cost_price_unit=3.9266, quantity=10000.0,
        snapshot_date="2026-07-05",
    )
    review_id = _insert_review(
        conn, asset_id, unrealized_return_pct=-29.4, trigger_threshold_pct=-25.0
    )

    capture = _PromptCapture()
    monkeypatch.setattr("src.services.llm_client.LLMClient.is_available", lambda self: True)
    monkeypatch.setattr("src.services.llm_client.LLMClient.complete", capture)

    resp = test_client.post(f"/reviews/value-trap/{review_id}/draft")
    assert resp.status_code == 200

    user_prompt = _PromptCapture.captured_user_prompt

    # The key must not appear in the context JSON (strip approach, consistent with Fix 3).
    # Check for the JSON key pattern "cost_price_unit": rather than the bare word — the
    # user_prompt NOTE header may mention the field name in prose (that's acceptable),
    # but it must not appear as a JSON key in the context payload.
    assert '"cost_price_unit":' not in user_prompt, (
        "R2-6 violation: 'cost_price_unit' JSON key found in LLM user_prompt context — "
        "avg cost must be stripped from the reduced context (same as Fix 3 stripped loss)"
    )

    # The actual value must not appear either
    assert "3.9266" not in user_prompt, (
        "R2-6 violation: cost basis value '3.9266' found in LLM user_prompt"
    )

    # avg_cost (from lot_detail) is already not in reduced_ctx — but assert defensively
    assert '"avg_cost":' not in user_prompt, (
        "R2-6 violation: 'avg_cost' JSON key found in LLM user_prompt"
    )


# ── R2-1: Context endpoint exposes price_date, price, freshness ──────────────

def test_context_endpoint_exposes_price_date_and_freshness(client):
    """R2-1: position block must include price_date, price, freshness."""
    test_client, conn = client
    asset_id = "CN_FUND_R2_FRESH"
    _insert_holding(conn, asset_id, "R2 Fresh Fund", -30.0, snapshot_date="2026-07-09")
    review_id = _insert_review(conn, asset_id, unrealized_return_pct=-30.0)

    resp = test_client.get(f"/reviews/value-trap/{review_id}/context")
    assert resp.status_code == 200
    body = resp.json()

    pos = body["position"]
    assert pos is not None
    # R2-1 fields
    assert "price_date" in pos, "position must include price_date (R2-1)"
    assert "price" in pos, "position must include price (R2-1)"
    assert "freshness" in pos, "position must include freshness verdict (R2-1)"

    freshness = pos["freshness"]
    assert "fresh" in freshness
    assert "freshness_class" in freshness
    assert "price_date" in freshness
    # price should match market_price_unit
    assert pos["price"] == pytest.approx(pos["market_price_unit"])


# ── R2-1: Ruling blocked on stale price ─────────────────────────────────────

def _insert_holding_stale(
    conn: DatabaseConnector,
    asset_id: str,
    *,
    days_stale: int = 14,
) -> None:
    """Insert a holding with a stale snapshot to trigger the freshness gate."""
    stale = (datetime.now() - timedelta(days=days_stale)).date().isoformat()
    market_value = round(1000.0 * 10.0 * 0.7, 2)  # -30% loss
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, quantity, cost_price_unit,
             market_price_unit, market_value, currency, source_system, is_shadow)
        VALUES (?, ?, ?, 1000.0, 10.0, 7.0, ?, 'CNY', 'test', FALSE)
        """,
        [stale, asset_id, asset_id, market_value],
    )


def test_ruling_blocked_when_price_is_stale(client):
    """R2-1: PUT ruling on asset with 14-day-old price must return 422."""
    test_client, conn = client
    asset_id = "CN_FUND_STALE_RULING"
    _insert_holding_stale(conn, asset_id, days_stale=14)
    # confirmed_none so linkage_ack gate doesn't interfere
    conn.execute(
        "INSERT INTO asset_memo_confirmations (asset_id, confirmed_no_memo) VALUES (?, TRUE)",
        [asset_id],
    )
    review_id = _insert_review(conn, asset_id)

    resp = test_client.put(
        f"/reviews/value-trap/{review_id}",
        json={
            "ruling": "hold_with_thesis",
            "next_review_date": "2026-10-01",
        },
    )
    assert resp.status_code == 422
    detail = resp.json().get("detail", "")
    assert "stale" in detail.lower() or "refresh" in detail.lower(), (
        f"422 detail should mention staleness/refresh, got: {detail!r}"
    )


def test_ruling_allowed_when_price_is_fresh(client):
    """R2-1: PUT ruling on asset with today's price must succeed."""
    test_client, conn = client
    asset_id = "CN_FUND_FRESH_RULING"
    # Insert fresh holding (yesterday)
    _insert_holding(conn, asset_id, "Fresh Fund", -30.0)
    conn.execute(
        "INSERT INTO asset_memo_confirmations (asset_id, confirmed_no_memo) VALUES (?, TRUE)",
        [asset_id],
    )
    review_id = _insert_review(conn, asset_id)

    resp = test_client.put(
        f"/reviews/value-trap/{review_id}",
        json={
            "ruling": "hold_with_thesis",
            "next_review_date": "2026-10-01",
        },
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"


# ── R2-3: lot_detail and decision_history_total ──────────────────────────────

def _insert_transaction(
    conn: DatabaseConnector,
    asset_id: str,
    tx_date: str,
    tx_type: str,
    quantity: float,
    price_unit: float,
) -> None:
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type,
             quantity, price_unit, currency, source_system, is_provisional)
        VALUES (?, ?, ?, ?, ?, ?, 'CNY', 'test', FALSE)
        """,
        [tx_date, asset_id, asset_id, tx_type, quantity, price_unit],
    )


def test_context_endpoint_lot_detail_and_decision_history_total(client):
    """R2-3: context endpoint returns lot_detail + decision_history_total.

    Fixture: 3 buys at different prices, 1 sell consuming the oldest lot.
    After sell:
        lot 1 (200 @ 3.5) + lot 2 (150 @ 3.0) remain — 350 units total
        avg_cost = (200*3.5 + 150*3.0) / 350 = 1150/350 ≈ 3.2857

    holdings.cost_price_unit set to the same value → reconciles within 0.0001.
    7 trade_logs inserted; context returns 5 rows + decision_history_total=7.
    """
    test_client, conn = client
    asset_id = "CN_FUND_LOT_R2_TEST"

    # Weighted avg cost after FIFO sell
    after_sell_avg = (200 * 3.5 + 150 * 3.0) / 350  # ≈ 3.2857

    # Holdings row: cost_price_unit matches lot-average exactly
    _insert_holding(
        conn, asset_id, "Lot R2 Test Fund",
        -15.0,  # unrealized pct; not the focus of this test
        cost_price_unit=after_sell_avg,
        quantity=350.0,
        snapshot_date=(datetime.now() - timedelta(days=1)).date().isoformat(),
    )

    # Transactions: 3 buys, then a sell consuming the first lot entirely
    _insert_transaction(conn, asset_id, "2020-01-01", "buy", 100.0, 4.0)
    _insert_transaction(conn, asset_id, "2021-01-01", "buy", 200.0, 3.5)
    _insert_transaction(conn, asset_id, "2022-01-01", "buy", 150.0, 3.0)
    _insert_transaction(conn, asset_id, "2023-06-01", "sell", 100.0, 2.5)  # wipes lot 1

    review_id = _insert_review(conn, asset_id)

    # Insert 7 trade_logs — context must show 5 rows but total=7
    for i in range(7):
        _insert_trade_log(conn, asset_id, log_date=f"2026-06-{i + 1:02d}")

    resp = test_client.get(f"/reviews/value-trap/{review_id}/context")
    assert resp.status_code == 200
    body = resp.json()

    # decision_history capped at 5; decision_history_total is the full count
    assert len(body["decision_history"]) == 5
    assert body["decision_history_total"] == 7

    # lot_detail present and correct
    lot_detail = body["lot_detail"]
    assert lot_detail is not None, "lot_detail must not be None when transactions exist"
    assert lot_detail["open_lot_count"] == 2, (
        f"Expected 2 open lots after FIFO sell, got {lot_detail['open_lot_count']}"
    )
    assert abs(lot_detail["open_qty"] - 350.0) < 1e-4
    assert lot_detail["truncated"] is False
    assert len(lot_detail["lots"]) == 2

    # Weighted avg reconciles to holdings cost_price_unit within 0.0001
    assert abs(lot_detail["avg_cost"] - after_sell_avg) < 0.0001, (
        f"lot_detail.avg_cost {lot_detail['avg_cost']:.6f} does not reconcile to "
        f"holdings cost_price_unit {after_sell_avg:.6f} within 0.0001"
    )


def test_context_endpoint_lot_detail_null_when_no_transactions(client):
    """R2-3: lot_detail is null when the asset has no transaction history."""
    test_client, conn = client
    asset_id = "CN_FUND_NO_TX_TEST"
    _insert_holding(conn, asset_id, "No TX Fund", -26.0, snapshot_date="2026-07-05")
    review_id = _insert_review(conn, asset_id)

    resp = test_client.get(f"/reviews/value-trap/{review_id}/context")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lot_detail"] is None
    assert body["decision_history_total"] == 0


def test_context_endpoint_decision_history_total_zero_when_no_logs(client):
    """R2-3: decision_history_total is 0 when no trade_logs exist for the asset."""
    test_client, conn = client
    asset_id = "CN_FUND_ZERO_LOGS"
    _insert_holding(conn, asset_id, "Zero Logs Fund", -28.0, snapshot_date="2026-07-05")
    review_id = _insert_review(conn, asset_id)

    resp = test_client.get(f"/reviews/value-trap/{review_id}/context")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision_history_total"] == 0
    assert body["decision_history"] == []
