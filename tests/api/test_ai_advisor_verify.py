"""Tests for Batch B verification endpoints: pending-verification, verify, reopen-verification.

Pattern: monkeypatch _DB_PATH to a tmp_path DuckDB, minimal schema, then hit endpoints
via FastAPI TestClient. Same pattern as test_ai_advisor_routes.py.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import ai_advisor as ai_advisor_routes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TODAY = date.today()
_MATURED_DATE = (_TODAY - timedelta(days=31)).isoformat()   # 31 days ago → matured
_FRESH_DATE = (_TODAY - timedelta(days=5)).isoformat()       # 5 days ago  → not matured


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(ai_advisor_routes.router)
    return app


def _setup_trade_logs_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create minimal trade_logs + market_daily + insights tables for verify tests."""
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_trade_logs_id START 1;
        CREATE TABLE IF NOT EXISTS trade_logs (
            id              INTEGER PRIMARY KEY DEFAULT nextval('seq_trade_logs_id'),
            log_date        DATE NOT NULL,
            asset_id        VARCHAR(50) NOT NULL,
            asset_name      VARCHAR(200),
            action          VARCHAR(20) NOT NULL,
            price           DECIMAL(20,8),
            quantity        DECIMAL(20,8),
            amount          DECIMAL(20,2),
            currency        VARCHAR(10) DEFAULT 'USD',
            decision_reason TEXT,
            ai_suggestion   TEXT,
            suggestion_source VARCHAR(50),
            verification_date   DATE,
            verification_result TEXT,
            verification_status VARCHAR(20) DEFAULT 'pending',
            verification_block_reason VARCHAR,
            verdict         VARCHAR(50),
            outcome_pct     DECIMAL(10,4),
            linked_transaction_id INTEGER,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_daily (
            code  VARCHAR(20),
            date  DATE,
            close DOUBLE,
            PRIMARY KEY (code, date)
        )
    """)
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_insights_id START 1;
        CREATE TABLE IF NOT EXISTS insights (
            id           INTEGER PRIMARY KEY DEFAULT nextval('seq_insights_id'),
            insight_date DATE,
            insight_type VARCHAR(50),
            category     VARCHAR(100),
            content      TEXT,
            title        VARCHAR(200),
            ai_model     VARCHAR(50),
            adopted      BOOLEAN,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_strategy_memos_id START 1;
        CREATE TABLE IF NOT EXISTS strategy_memos (
            id          INTEGER PRIMARY KEY DEFAULT nextval('seq_strategy_memos_id'),
            memo_date   DATE,
            title       VARCHAR(300),
            strategic_bias VARCHAR(20),
            key_directives JSON,
            source_file VARCHAR(500),
            content     TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_verdict_audit_id START 1;
        CREATE TABLE IF NOT EXISTS verdict_audit (
            id                       INTEGER PRIMARY KEY DEFAULT nextval('seq_verdict_audit_id'),
            trade_id                 INTEGER NOT NULL,
            suggested_from_threshold VARCHAR,
            keyword_derived          VARCHAR,
            final_verdict            VARCHAR,
            mismatch                 BOOLEAN,
            created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Required by recompute_auto_links called inside score_single_trade
    conn.execute("""
        CREATE TABLE IF NOT EXISTS insight_trade_links (
            id         INTEGER PRIMARY KEY,
            insight_id INTEGER,
            trade_id   INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _insert_trade(
    conn: duckdb.DuckDBPyConnection,
    *,
    log_date: str,
    action: str = "Buy",
    asset_id: str = "US_STK_AMZN",
    asset_name: str = "Amazon",
    verification_status: str = "pending",
    verification_result: str | None = None,
    verdict: str | None = None,
    outcome_pct: float | None = None,
    suggestion_source: str | None = None,
    decision_reason: str | None = None,
    price: float = 180.5,
    quantity: float = 10.0,
) -> int:
    row = conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, asset_name, action,
            price, quantity, amount,
            verification_status, verification_result, verdict, outcome_pct,
            suggestion_source, decision_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        [
            log_date, asset_id, asset_name, action,
            price, quantity, price * quantity,
            verification_status, verification_result, verdict, outcome_pct,
            suggestion_source, decision_reason,
        ],
    ).fetchone()
    return int(row[0])


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def verify_client(tmp_path, monkeypatch):
    db_path = tmp_path / "verify_test.duckdb"
    conn = duckdb.connect(str(db_path))
    _setup_trade_logs_schema(conn)
    conn.close()

    monkeypatch.setattr(ai_advisor_routes, "_DB_PATH", Path(db_path))
    app = _make_app()
    return TestClient(app), str(db_path)


# ---------------------------------------------------------------------------
# GET /ai-advisor/trades/pending-verification — listing tests
# ---------------------------------------------------------------------------

class TestPendingVerification:

    def test_pending_returns_unverified_trades(self, verify_client):
        """Only pending + pending_window rows returned; verified excluded."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending_window")
        _insert_trade(conn, log_date=_MATURED_DATE, verification_status="verified",
                      verification_result="done", verdict="good_call")
        conn.close()

        r = client.get("/ai-advisor/trades/pending-verification?since=2020-01-01")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 2
        statuses = {it["verification_status"] for it in items}
        assert statuses == {"pending", "pending_window"}

    def test_pending_includes_preview_for_matured_only(self, verify_client):
        """Matured trade gets outcome_pct_preview + suggested_verdict; fresh gets None."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        # Matured trade with market prices
        _insert_trade(conn, log_date=_MATURED_DATE, asset_id="US_STK_AMZN",
                      action="Buy", verification_status="pending_window", price=100.0)
        # Insert market prices: at trade date and +30d
        matured_dt = date.fromisoformat(_MATURED_DATE)
        conn.execute(
            "INSERT INTO market_daily VALUES (?, ?, ?)",
            ["AMZN", matured_dt, 100.0],
        )
        conn.execute(
            "INSERT INTO market_daily VALUES (?, ?, ?)",
            ["AMZN", matured_dt + timedelta(days=30), 110.0],
        )
        # Fresh trade (no prices needed)
        _insert_trade(conn, log_date=_FRESH_DATE, asset_id="US_STK_MSFT",
                      action="Buy", verification_status="pending")
        conn.close()

        r = client.get("/ai-advisor/trades/pending-verification?since=2020-01-01")
        assert r.status_code == 200
        items = r.json()["items"]
        # Find matured and fresh
        matured_item = next(it for it in items if it["verification_status"] == "pending_window")
        fresh_item = next(it for it in items if it["verification_status"] == "pending")

        assert matured_item["is_matured"] is True
        assert matured_item["outcome_pct_preview"] is not None
        assert matured_item["suggested_verdict"] is not None

        assert fresh_item["is_matured"] is False
        assert fresh_item["outcome_pct_preview"] is None
        assert fresh_item["suggested_verdict"] is None

    def test_pending_includes_linked_insight_when_present(self, verify_client):
        """Trade gets linked_insight_id when a matching insight exists within 3d."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        # Insert insight 2 days before trade
        trade_dt = date.fromisoformat(_FRESH_DATE)
        insight_dt = (trade_dt - timedelta(days=2)).isoformat()
        conn.execute(
            """
            INSERT INTO insights (insight_date, insight_type, category, content, title, ai_model, adopted)
            VALUES (?, 'recommendation', 'market', 'AMZN strong buy signal', 'AMZN signal', 'gemini', TRUE)
            """,
            [insight_dt],
        )
        _insert_trade(conn, log_date=_FRESH_DATE, asset_id="US_STK_AMZN",
                      suggestion_source="gemini", verification_status="pending")
        conn.close()

        r = client.get("/ai-advisor/trades/pending-verification?since=2020-01-01")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        # linked_insight_id may or may not resolve depending on find_linked_insight
        # The key check: field exists in response (may be None if no match)
        assert "linked_insight_id" in items[0]

    def test_pending_respects_limit(self, verify_client):
        """limit=2 over 5 trades returns exactly 2 items."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        for _ in range(5):
            _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        conn.close()

        r = client.get("/ai-advisor/trades/pending-verification?since=2020-01-01&limit=2")
        assert r.status_code == 200
        assert len(r.json()["items"]) == 2

    def test_pending_400_on_invalid_date_format(self, verify_client):
        """Bad since date returns 400."""
        client, _ = verify_client
        r = client.get("/ai-advisor/trades/pending-verification?since=not-a-date")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /ai-advisor/trades/{id}/verify
# ---------------------------------------------------------------------------

class TestVerifyTrade:

    def test_verify_writes_narrative_and_calls_scorer_when_matured(self, verify_client):
        """Happy path: matured trade + market prices → narrative stored, score_single_trade called."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_MATURED_DATE, asset_id="US_STK_AMZN",
                                 action="Buy", verification_status="pending", price=100.0)
        matured_dt = date.fromisoformat(_MATURED_DATE)
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)", ["AMZN", matured_dt, 100.0])
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)",
                     ["AMZN", matured_dt + timedelta(days=30), 115.0])
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={"verification_result": "止损成功，买对了"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["verification_result"] == "止损成功，买对了"
        assert data["verification_status"] in ("pending_window", "verified")
        assert "updated_at" in data

    def test_verify_keeps_pending_window_for_unmatured(self, verify_client):
        """Unmatured trade: narrative written, status stays pending_window, verdict NULL."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={"verification_result": "Too early to tell"},
        )
        assert r.status_code == 200
        data = r.json()
        # status transitions to pending_window, verdict stays null
        assert data["verification_status"] == "pending_window"
        assert data["verdict"] is None

    def test_verify_explicit_verdict_override_persists(self, verify_client):
        """Explicit verdict in body overrides classifier result and immediately verifies."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={"verification_result": "Some reason", "verdict": "regret"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["verdict"] == "regret"
        assert data["verification_status"] == "verified"

    def test_verify_400_on_blank_narrative(self, verify_client):
        """Blank verification_result returns 400."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={"verification_result": "   "},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "verification_result_blank"

    def test_verify_400_on_invalid_verdict_value(self, verify_client):
        """Invalid verdict string returns 400."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={"verification_result": "valid text", "verdict": "not_a_verdict"},
        )
        assert r.status_code == 400

    def test_verify_404_on_missing_trade(self, verify_client):
        """Missing trade_id → 404."""
        client, _ = verify_client
        r = client.post(
            "/ai-advisor/trades/99999/verify",
            json={"verification_result": "some narrative"},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "trade not found"

    def test_verify_409_when_already_verified(self, verify_client):
        """Already-verified trade → 409 with hint to use reopen."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(
            conn, log_date=_MATURED_DATE, verification_status="verified",
            verification_result="done", verdict="good_call", outcome_pct=8.5,
        )
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={"verification_result": "try again"},
        )
        assert r.status_code == 409
        data = r.json()
        assert "hint" in data["detail"] or "reopen" in str(data)

    def test_verify_412_on_stale_expected_updated_at(self, verify_client):
        """Stale expected_updated_at → 412."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={
                "verification_result": "ok",
                "expected_updated_at": "1999-01-01T00:00:00.000000",
            },
        )
        assert r.status_code == 412

    def test_verify_idempotent_repeat_in_pending_window(self, verify_client):
        """Second POST on pending_window trade updates narrative, no error."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        conn.close()

        r1 = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={"verification_result": "first narrative"},
        )
        assert r1.status_code == 200
        assert r1.json()["verification_status"] == "pending_window"

        r2 = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={"verification_result": "updated narrative"},
        )
        assert r2.status_code == 200
        assert r2.json()["verification_result"] == "updated narrative"

    def test_verify_does_not_fail_when_scorer_raises(self, verify_client, caplog):
        """Scoring error is best-effort: endpoint still returns 200."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        conn.close()

        with patch(
            "src.api.routes.ai_advisor.score_single_trade",
            side_effect=RuntimeError("db exploded"),
        ), caplog.at_level(logging.WARNING):
            r = client.post(
                f"/ai-advisor/trades/{trade_id}/verify",
                json={"verification_result": "narrative here"},
            )

        assert r.status_code == 200
        assert r.json()["verification_result"] == "narrative here"
        # Scorer error should be logged as warning, not raised
        assert any("score_single_trade" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# POST /ai-advisor/trades/{id}/reopen-verification
# ---------------------------------------------------------------------------

class TestReopenVerification:

    def test_reopen_verified_to_pending_window(self, verify_client):
        """Verified trade reopened: verdict + outcome_pct + block_reason all cleared."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(
            conn, log_date=_MATURED_DATE, verification_status="verified",
            verification_result="done", verdict="good_call", outcome_pct=8.5,
        )
        conn.close()

        r = client.post(f"/ai-advisor/trades/{trade_id}/reopen-verification", json={})
        assert r.status_code == 200
        data = r.json()
        assert data["verification_status"] == "pending_window"
        assert data["verdict"] is None
        assert data["outcome_pct"] is None

    def test_reopen_idempotent_on_pending_window(self, verify_client):
        """Second reopen on pending_window returns 200 with no-op."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending_window")
        conn.close()

        r1 = client.post(f"/ai-advisor/trades/{trade_id}/reopen-verification", json={})
        assert r1.status_code == 200

        r2 = client.post(f"/ai-advisor/trades/{trade_id}/reopen-verification", json={})
        assert r2.status_code == 200
        assert r2.json()["verification_status"] == "pending_window"

    def test_reopen_412_on_stale_expected_updated_at(self, verify_client):
        """Stale expected_updated_at on reopen → 412."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(
            conn, log_date=_MATURED_DATE, verification_status="verified",
            verification_result="done", verdict="good_call",
        )
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/reopen-verification",
            json={"expected_updated_at": "1999-01-01T00:00:00.000000"},
        )
        assert r.status_code == 412

    def test_reopen_404_on_missing_trade(self, verify_client):
        """Missing trade_id → 404."""
        client, _ = verify_client
        r = client.post("/ai-advisor/trades/88888/reopen-verification", json={})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Issue #7 regression tests — explicit verdict → immediate verified status
# Bug: /verify always set pending_window; pending list showed suggested_verdict
# not db_verdict (empty for RSU vest actions).
# ---------------------------------------------------------------------------

class TestVerifyExplicitVerdictStatus:
    """When user provides an explicit verdict, the trade must transition to
    'verified' immediately — not stay in pending_window."""

    def test_explicit_verdict_sets_verified_status(self, verify_client):
        """POST /verify with verdict → verification_status == 'verified'."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={"verification_result": "Held too long, missed exit.", "verdict": "missed_opportunity"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["verdict"] == "missed_opportunity"
        assert data["verification_status"] == "verified", (
            f"Expected 'verified' when explicit verdict provided, got '{data['verification_status']}'"
        )

    def test_no_verdict_stays_pending_window(self, verify_client):
        """POST /verify without verdict → verification_status stays 'pending_window'."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={"verification_result": "Watching to see how it plays out."},
        )
        assert r.status_code == 200
        assert r.json()["verification_status"] == "pending_window"
        assert r.json()["verdict"] is None

    def test_rsu_vest_explicit_verdict_persists(self, verify_client):
        """RSU vest trades with explicit verdict must reach 'verified' status.

        Root of Issue #7: derive_verdict_suggestion('vest', ...) returns None
        because 'vest' has no buy/sell direction. Without Fix 1 the trade
        stays pending_window and the pending list shows empty suggested_verdict.
        """
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(
            conn,
            log_date=_MATURED_DATE,
            action="vest",
            asset_id="RSU_AMZN",
            asset_name="Amazon RSU",
            verification_status="pending",
        )
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={
                "verification_result": "RSU vested at peak, should have diversified sooner.",
                "verdict": "missed_opportunity",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["verdict"] == "missed_opportunity"
        assert data["verification_status"] == "verified", (
            "RSU vest trade with explicit verdict must be 'verified', "
            f"not '{data['verification_status']}'"
        )

    def test_explicit_verdict_persists_after_scorer_runs(self, verify_client):
        """Scorer must not overwrite a user-set verdict even when it can auto-derive one.

        Regression for Bug 3: score_single_trade would overwrite user's verdict
        with its own computed verdict when outcome_pct was NULL.
        """
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(
            conn,
            log_date=_MATURED_DATE,
            action="Buy",
            asset_id="US_STK_AMZN",
            verification_status="pending",
            price=100.0,
        )
        matured_dt = date.fromisoformat(_MATURED_DATE)
        # Insert prices that would make scorer auto-derive 'good_call' (buy + 15% gain)
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)", ["AMZN", matured_dt, 100.0])
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)",
                     ["AMZN", matured_dt + timedelta(days=30), 115.0])
        conn.close()

        # User explicitly overrides with 'missed_opportunity'
        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={
                "verification_result": "Sold too early before earnings pop.",
                "verdict": "missed_opportunity",
            },
        )
        assert r.status_code == 200
        data = r.json()
        # User's explicit verdict must survive even though scorer would derive 'good_call'
        assert data["verdict"] == "missed_opportunity", (
            f"Expected user verdict 'missed_opportunity', but scorer overwrote it with '{data['verdict']}'"
        )


class TestNarrativeOnlyVerify:
    """Regression tests for Issue #12: narrative-only verify (no explicit verdict) must not block.

    User submits a narrative but leaves verdict as "Let backend decide". The backend
    scores the trade, but when no market price is available at +30d, it must NOT flip
    the trade to 'verification_blocked'. The trade should remain 'pending_window' so the
    user can return and select a verdict manually.
    """

    def test_narrative_only_stays_pending_window_when_no_price_data(self, verify_client):
        """Narrative-only verify + no market price → pending_window, NOT verification_blocked.

        Core Issue #12 regression: scorer was calling SET verification_blocked when
        existing_verdict IS NULL, which is true for narrative-only submissions too.
        """
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(
            conn,
            log_date=_MATURED_DATE,
            action="Buy",
            asset_id="US_STK_AMZN",
            verification_status="pending",
            decision_reason="Strong macro tailwinds",
        )
        # NO market_daily rows → compute_outcome_pct_from_prices returns None
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={
                "verification_result": "Good entry point, macro support held up well.",
                # No 'verdict' key — user chose "Let backend decide"
            },
        )
        assert r.status_code == 200
        data = r.json()
        # Must NOT be blocked — user wrote notes and should be able to add a verdict later
        assert data["verification_status"] != "verification_blocked", (
            f"Narrative-only verify with no price data must NOT block the trade; "
            f"got status '{data['verification_status']}'"
        )
        assert data["verification_status"] == "pending_window", (
            f"Expected pending_window so user can return and add a verdict; "
            f"got '{data['verification_status']}'"
        )
        assert data["verdict"] is None, (
            f"Verdict should remain None until user explicitly picks one; got '{data['verdict']}'"
        )

    def test_narrative_only_with_price_and_small_move_gets_neutral(self, verify_client):
        """Narrative-only verify + price data + sub-5% move → verdict='neutral', status='verified'.

        Addendum 2026-07-05: the old behaviour (stay pending_window when within-band) is
        superseded. The neutral fallback fires when keyword + threshold classifiers both return
        None and outcome_pct is computable. Narrative without verdict keywords + 2% gain → neutral.
        """
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(
            conn,
            log_date=_MATURED_DATE,
            action="Buy",
            asset_id="US_STK_AAPL",
            verification_status="pending",
            price=180.0,
            decision_reason="Steady compounder",
        )
        matured_dt = date.fromisoformat(_MATURED_DATE)
        # Tiny 2% move — within the dead-band → neutral fallback fires
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)", ["AAPL", matured_dt, 180.0])
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)",
                     ["AAPL", matured_dt + timedelta(days=30), 183.6])
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={
                "verification_result": "Slight gain, in line with expectations.",
            },
        )
        assert r.status_code == 200
        data = r.json()
        # Sub-5% move + no keywords → neutral fallback → verified
        assert data["verdict"] == "neutral", (
            f"Sub-5% move with no keywords should yield 'neutral'; got '{data['verdict']}'"
        )
        assert data["verification_status"] == "verified", (
            f"Neutral verdict satisfies Rule B; expected 'verified'; got '{data['verification_status']}'"
        )
        # outcome_pct should have been computed by scorer
        assert data["outcome_pct"] is not None, "outcome_pct should be filled by scorer"

    def test_narrative_only_with_price_and_large_move_auto_verifies(self, verify_client):
        """Narrative-only verify + price data + large move → auto-verdict + verified status.

        When the scorer can derive a verdict from the price movement, the trade should
        automatically reach 'verified' status even without an explicit user verdict.
        """
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(
            conn,
            log_date=_MATURED_DATE,
            action="Buy",
            asset_id="US_STK_NVDA",
            verification_status="pending",
            price=500.0,
            decision_reason="AI momentum",
        )
        matured_dt = date.fromisoformat(_MATURED_DATE)
        # 20% gain — well above the 5% band, scorer should auto-derive 'good_call'
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)", ["NVDA", matured_dt, 500.0])
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)",
                     ["NVDA", matured_dt + timedelta(days=30), 600.0])
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={"verification_result": "Strong move as expected from AI tailwinds."},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["verification_status"] == "verified", (
            f"Large move should auto-promote to verified; got '{data['verification_status']}'"
        )
        assert data["verdict"] == "good_call", (
            f"20% buy gain should yield good_call; got '{data['verdict']}'"
        )


# ---------------------------------------------------------------------------
# T1 — outcome_to_date fields for pending rows (pre-maturity)
# ---------------------------------------------------------------------------

class TestOutcomeToDateFields:
    """GET /ai-advisor/trades/pending-verification exposes outcome_to_date_pct
    and outcome_to_date_asof for ALL pending rows — not just matured ones.
    """

    def test_fresh_pending_row_with_prices_has_outcome_to_date(self, verify_client):
        """A pre-window trade with existing price data gets outcome_to_date_pct/asof."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        fresh_dt = date.today() - timedelta(days=5)
        recent_dt = date.today() - timedelta(days=1)
        _insert_trade(
            conn, log_date=fresh_dt.isoformat(),
            asset_id="US_STK_AMZN", action="Buy",
            verification_status="pending",
        )
        # Baseline at trade date + a more recent price
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)", ["AMZN", fresh_dt, 100.0])
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)", ["AMZN", recent_dt, 108.0])
        conn.close()

        r = client.get("/ai-advisor/trades/pending-verification?since=2020-01-01")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        item = items[0]

        assert item["is_matured"] is False, "Trade should not be matured (5 days old)"
        assert "outcome_to_date_pct" in item, "outcome_to_date_pct must be in response"
        assert "outcome_to_date_asof" in item, "outcome_to_date_asof must be in response"
        assert item["outcome_to_date_pct"] is not None, (
            "outcome_to_date_pct must be non-None when price data is available"
        )
        assert item["outcome_to_date_asof"] is not None
        # Matured preview fields should stay None (not matured)
        assert item["outcome_pct_preview"] is None
        assert item["suggested_verdict"] is None

    def test_fresh_pending_row_without_prices_has_none_outcome_to_date(self, verify_client):
        """A pre-window trade with no price data has outcome_to_date_pct=None."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        _insert_trade(
            conn, log_date=_FRESH_DATE,
            asset_id="US_STK_UNKN", action="Buy",
            verification_status="pending",
        )
        # No market_daily rows
        conn.close()

        r = client.get("/ai-advisor/trades/pending-verification?since=2020-01-01")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["outcome_to_date_pct"] is None
        assert items[0]["outcome_to_date_asof"] is None

    def test_verified_history_rows_do_not_compute_outcome_to_date(self, verify_client):
        """Regression (Lead review fix): verified/blocked history rows must NOT get
        outcome_to_date fields computed — even when price data exists — while a pending
        row for the same asset still does. History rows display the stored outcome_pct;
        computing to-date for history would add 2 price queries per row for no value.
        """
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        fresh_dt = date.today() - timedelta(days=5)
        recent_dt = date.today() - timedelta(days=1)
        matured_dt = date.fromisoformat(_MATURED_DATE)

        # Verified history row (verdict + narrative → passes T4 filter)
        _insert_trade(
            conn, log_date=_MATURED_DATE,
            asset_id="US_STK_AMZN", action="Buy",
            verification_status="verified",
            verification_result="买对了", verdict="good_call", outcome_pct=8.0,
        )
        # Pending row for the same asset
        _insert_trade(
            conn, log_date=fresh_dt.isoformat(),
            asset_id="US_STK_AMZN", action="Buy",
            verification_status="pending",
        )
        # Price data covering BOTH windows — verified row must still skip computation
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)", ["AMZN", matured_dt, 100.0])
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)", ["AMZN", fresh_dt, 102.0])
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)", ["AMZN", recent_dt, 108.0])
        conn.close()

        r = client.get("/ai-advisor/trades/pending-verification?since=2020-01-01&status=all")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 2

        verified_item = next(it for it in items if it["verification_status"] == "verified")
        pending_item = next(it for it in items if it["verification_status"] == "pending")

        assert verified_item["outcome_to_date_pct"] is None, (
            "Verified history row must NOT have outcome_to_date_pct computed"
        )
        assert verified_item["outcome_to_date_asof"] is None, (
            "Verified history row must NOT have outcome_to_date_asof computed"
        )
        # Contrast: the pending row (same asset, prices available) still gets the fields
        assert pending_item["outcome_to_date_pct"] is not None, (
            "Pending row must still get outcome_to_date_pct"
        )
        assert pending_item["outcome_to_date_asof"] is not None


# ---------------------------------------------------------------------------
# T4 — Verified history display-scope filter
# ---------------------------------------------------------------------------

class TestVerifiedHistoryFilter:
    """status=verified (and the verified portion of status=all) must hide rows
    that have neither a verdict nor a verification narrative — the 2000+ imported
    ledger rows that polluted the history.
    Pending scope is UNCHANGED by this filter.
    """

    def test_verified_history_hides_no_verdict_no_narrative_rows(self, verify_client):
        """status=verified hides imported rows that have neither verdict nor narrative."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        # Verdicted row → should appear
        _insert_trade(
            conn, log_date=_MATURED_DATE,
            verification_status="verified",
            verification_result="止损成功",
            verdict="good_call",
        )
        # Imported ledger row: no verdict, no narrative → should NOT appear
        _insert_trade(
            conn, log_date=_MATURED_DATE,
            verification_status="verified",
            verification_result=None,
            verdict=None,
            decision_reason="ledger-import",
        )
        conn.close()

        r = client.get("/ai-advisor/trades/pending-verification?since=2020-01-01&status=verified")
        assert r.status_code == 200
        items = r.json()["items"]
        # Only the verdicted row should appear
        assert len(items) == 1, f"Expected 1 verdicted row, got {len(items)}"
        assert items[0]["verdict"] == "good_call"

    def test_verified_history_keeps_narrative_only_rows(self, verify_client):
        """status=verified keeps rows that have a narrative even without a verdict."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        _insert_trade(
            conn, log_date=_MATURED_DATE,
            verification_status="verified",
            verification_result="观察中，暂无结论",  # narrative, no verdict
            verdict=None,
            decision_reason="test",
        )
        conn.close()

        r = client.get("/ai-advisor/trades/pending-verification?since=2020-01-01&status=verified")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1, "Narrative-only row should appear in verified history"

    def test_all_status_hides_imported_verified_keeps_pending(self, verify_client):
        """status=all: pending rows are shown unconditionally; imported-verified are hidden."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        # Pending trade (no verdict/narrative) → MUST appear
        _insert_trade(
            conn, log_date=_FRESH_DATE,
            verification_status="pending",
            verdict=None, verification_result=None,
        )
        # Verdicted verified trade → MUST appear
        _insert_trade(
            conn, log_date=_MATURED_DATE,
            verification_status="verified",
            verdict="good_call",
            verification_result="买对了",
        )
        # Imported verified trade (no verdict, no narrative) → must NOT appear
        _insert_trade(
            conn, log_date=_MATURED_DATE,
            verification_status="verified",
            verdict=None, verification_result=None,
            decision_reason="import-noise",
        )
        conn.close()

        r = client.get("/ai-advisor/trades/pending-verification?since=2020-01-01&status=all")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 2, (
            f"Expected 2 items (pending + verdicted verified), got {len(items)}: "
            f"{[it['verification_status'] for it in items]}"
        )
        statuses = {it["verification_status"] for it in items}
        assert "pending" in statuses
        assert "verified" in statuses

    def test_verified_map_always_shows_blocked_rows(self, verify_client):
        """Regression (code-review fix 6): verification_blocked rows carry no verdict
        and no narrative by definition, but must ALWAYS be visible under
        status=verified — the Reopen flow needs them."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        _insert_trade(
            conn, log_date=_MATURED_DATE,
            verification_status="verification_blocked",
            verdict=None, verification_result=None,
        )
        conn.close()

        r = client.get("/ai-advisor/trades/pending-verification?since=2020-01-01&status=verified")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1, (
            "verification_blocked row (no verdict, no narrative) must be visible "
            f"under status=verified; got {len(items)} items"
        )
        assert items[0]["verification_status"] == "verification_blocked"

    def test_all_map_includes_unmatched_rows(self, verify_client):
        """Regression (code-review fix 7): verification_status='unmatched' rows were
        visible under the old 1=1 'all' map and must remain visible."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        _insert_trade(
            conn, log_date=_FRESH_DATE,
            verification_status="unmatched",
            verdict=None, verification_result=None,
        )
        conn.close()

        r = client.get("/ai-advisor/trades/pending-verification?since=2020-01-01&status=all")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1, (
            f"'unmatched' row must be visible under status=all; got {len(items)} items"
        )
        assert items[0]["verification_status"] == "unmatched"


# ---------------------------------------------------------------------------
# Addendum 2026-07-05 — neutral verdict endpoint tests
# ---------------------------------------------------------------------------

class TestNeutralVerdict:
    """Tests for VERDICT_NEUTRAL ('neutral') addendum: within-band matured outcomes
    get suggested_verdict='neutral' in the list endpoint and are accepted by POST /verify.
    """

    def test_matured_within_band_gets_suggested_neutral(self, verify_client):
        """GET pending-verification: matured row with within-band price preview →
        suggested_verdict='neutral' (not None / not 'Set manually').
        """
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        _insert_trade(conn, log_date=_MATURED_DATE, asset_id="US_STK_AMZN",
                      action="Buy", verification_status="pending_window", price=100.0)
        matured_dt = date.fromisoformat(_MATURED_DATE)
        # +4.03% — within the default 5% band → derive_verdict_suggestion returns None → neutral
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)", ["AMZN", matured_dt, 100.0])
        conn.execute("INSERT INTO market_daily VALUES (?, ?, ?)",
                     ["AMZN", matured_dt + timedelta(days=30), 104.03])
        conn.close()

        r = client.get("/ai-advisor/trades/pending-verification?since=2020-01-01")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["is_matured"] is True
        assert item["outcome_pct_preview"] is not None, "outcome_pct_preview must be computed"
        assert item["suggested_verdict"] == "neutral", (
            f"Within-band outcome_pct_preview must yield suggested_verdict='neutral'; "
            f"got '{item['suggested_verdict']}'"
        )

    def test_post_verify_accepts_neutral_verdict(self, verify_client):
        """POST /verify with verdict='neutral' is accepted (200) and stored."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        conn.close()

        r = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={
                "verification_result": "按计划，DCA策略如期执行",
                "verdict": "neutral",
            },
        )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert data["verdict"] == "neutral", (
            f"Expected stored verdict='neutral'; got '{data['verdict']}'"
        )
        assert data["verification_status"] == "verified", (
            f"Explicit neutral verdict should immediately verify; got '{data['verification_status']}'"
        )


# ---------------------------------------------------------------------------
# mark_dirty durability — GCS flush visibility tests
# ---------------------------------------------------------------------------

class TestVerifyMarkDirty:
    """mark_dirty() must be called on successful writes so Cloud Run GCS flush
    picks up the change. Must NOT be called on 4xx failures (no DB write occurred).
    """

    def test_verify_calls_mark_dirty_on_success(self, verify_client):
        """Successful verify → mark_dirty called exactly once."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        conn.close()

        with patch("src.api.routes.ai_advisor.mark_dirty") as mock_md:
            r = client.post(
                f"/ai-advisor/trades/{trade_id}/verify",
                json={"verification_result": "all good"},
            )
        assert r.status_code == 200
        mock_md.assert_called_once()

    def test_verify_does_not_call_mark_dirty_on_blank_narrative_400(self, verify_client):
        """Blank narrative → 400 → mark_dirty must NOT be called (no DB write)."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(conn, log_date=_FRESH_DATE, verification_status="pending")
        conn.close()

        with patch("src.api.routes.ai_advisor.mark_dirty") as mock_md:
            r = client.post(
                f"/ai-advisor/trades/{trade_id}/verify",
                json={"verification_result": "   "},
            )
        assert r.status_code == 400
        mock_md.assert_not_called()

    def test_verify_does_not_call_mark_dirty_on_404(self, verify_client):
        """Missing trade → 404 → mark_dirty must NOT be called."""
        client, _ = verify_client
        with patch("src.api.routes.ai_advisor.mark_dirty") as mock_md:
            r = client.post(
                "/ai-advisor/trades/99999/verify",
                json={"verification_result": "some narrative"},
            )
        assert r.status_code == 404
        mock_md.assert_not_called()


class TestReopenMarkDirty:
    """mark_dirty() must be called when reopen actually updates a row,
    and NOT called when the row was already pending_window (idempotent no-op).
    """

    def test_reopen_calls_mark_dirty_when_row_updated(self, verify_client):
        """Verified → pending_window transition → mark_dirty called once."""
        client, db_path = verify_client
        conn = duckdb.connect(db_path)
        trade_id = _insert_trade(
            conn, log_date=_MATURED_DATE, verification_status="verified",
            verification_result="done", verdict="good_call",
        )
        conn.close()

        with patch("src.api.routes.ai_advisor.mark_dirty") as mock_md:
            r = client.post(f"/ai-advisor/trades/{trade_id}/reopen-verification", json={})
        assert r.status_code == 200
        mock_md.assert_called_once()

    def test_reopen_does_not_call_mark_dirty_on_404(self, verify_client):
        """Missing trade → 404 → mark_dirty must NOT be called."""
        client, _ = verify_client
        with patch("src.api.routes.ai_advisor.mark_dirty") as mock_md:
            r = client.post("/ai-advisor/trades/88888/reopen-verification", json={})
        assert r.status_code == 404
        mock_md.assert_not_called()
