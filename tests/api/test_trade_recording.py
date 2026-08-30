"""Tests for trade recording endpoints in ai_advisor routes."""

import duckdb
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path

from src.api.routes import ai_advisor as ai_advisor_routes


TRADE_LOGS_DDL = """
CREATE TABLE trade_logs (
    id INTEGER PRIMARY KEY,
    log_date DATE,
    asset_id VARCHAR,
    asset_name VARCHAR,
    action VARCHAR,
    price DOUBLE,
    quantity DOUBLE,
    amount DOUBLE,
    currency VARCHAR(10),
    decision_reason TEXT,
    suggestion_source VARCHAR,
    linked_memo_id INTEGER,
    verification_status VARCHAR(20) DEFAULT 'pending'
);
"""

ASSET_REGISTRY_DDL = """
CREATE TABLE asset_registry (
    canonical_id VARCHAR PRIMARY KEY,
    display_name VARCHAR,
    asset_class VARCHAR,
    base_currency VARCHAR
);
"""

STRATEGY_MEMOS_DDL = """
CREATE TABLE strategy_memos (
    id INTEGER PRIMARY KEY,
    memo_date DATE,
    title VARCHAR,
    strategic_bias VARCHAR,
    key_directives TEXT,
    source_file VARCHAR,
    content TEXT
);
"""

SEQUENCE_DDL = """
CREATE SEQUENCE IF NOT EXISTS trade_logs_id_seq START 1;
"""


def _make_client(tmp_path, monkeypatch, seed_assets=None, seed_trades=None, seed_memos=None):
    """Create a test client with a temp DuckDB containing the required schema."""
    db_path = tmp_path / "test_trades.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(ASSET_REGISTRY_DDL)
    conn.execute(STRATEGY_MEMOS_DDL)
    conn.execute("""
        CREATE SEQUENCE trade_logs_id_seq START 1;
    """)
    conn.execute("""
        CREATE TABLE trade_logs (
            id INTEGER DEFAULT nextval('trade_logs_id_seq') PRIMARY KEY,
            log_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            action VARCHAR,
            price DOUBLE,
            quantity DOUBLE,
            amount DOUBLE,
            currency VARCHAR(10),
            decision_reason TEXT,
            suggestion_source VARCHAR,
            linked_memo_id INTEGER,
            verification_status VARCHAR(20) DEFAULT 'pending'
        );
    """)

    if seed_assets:
        for asset in seed_assets:
            conn.execute(
                "INSERT INTO asset_registry (canonical_id, display_name, asset_class, base_currency) VALUES (?, ?, ?, ?)",
                asset,
            )

    if seed_trades:
        for trade in seed_trades:
            conn.execute(
                """INSERT INTO trade_logs
                   (log_date, asset_id, asset_name, action, price, quantity, amount, currency, decision_reason, suggestion_source, linked_memo_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                trade,
            )

    if seed_memos:
        for memo in seed_memos:
            conn.execute(
                """INSERT INTO strategy_memos
                   (id, memo_date, title, strategic_bias, key_directives, source_file, content)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                memo,
            )

    conn.close()

    monkeypatch.setattr(ai_advisor_routes, "_DB_PATH", Path(db_path))
    app = FastAPI()
    app.include_router(ai_advisor_routes.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Assets search tests
# ---------------------------------------------------------------------------

def test_search_assets_returns_results(tmp_path, monkeypatch):
    client = _make_client(
        tmp_path,
        monkeypatch,
        seed_assets=[("US_STK_MSFT", "Microsoft Corp", "US Equity", "USD")],
    )
    resp = client.get("/ai-advisor/assets/search?q=MSFT")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["assets"]) == 1
    assert data["assets"][0]["asset_id"] == "US_STK_MSFT"
    assert data["assets"][0]["display_name"] == "Microsoft Corp"
    assert data["assets"][0]["base_currency"] == "USD"


def test_search_assets_min_2_chars(tmp_path, monkeypatch):
    client = _make_client(
        tmp_path,
        monkeypatch,
        seed_assets=[("US_STK_MSFT", "Microsoft Corp", "US Equity", "USD")],
    )
    resp = client.get("/ai-advisor/assets/search?q=M")
    assert resp.status_code == 200
    assert resp.json() == {"assets": []}


def test_search_assets_escapes_wildcards(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    # A query with % should not raise an error — it should just return empty
    resp = client.get("/ai-advisor/assets/search?q=%25")
    assert resp.status_code == 200
    assert "assets" in resp.json()


# ---------------------------------------------------------------------------
# Create trade tests
# ---------------------------------------------------------------------------

def test_create_trade_with_amount(tmp_path, monkeypatch):
    client = _make_client(
        tmp_path,
        monkeypatch,
        seed_assets=[("US_STK_AAPL", "Apple Inc", "US Equity", "USD")],
    )
    resp = client.post(
        "/ai-advisor/trades",
        json={
            "log_date": "2026-03-20",
            "asset_id": "US_STK_AAPL",
            "action": "Buy",
            "amount": 5000.0,
            "currency": "USD",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["asset_id"] == "US_STK_AAPL"
    assert data["amount"] == 5000.0
    assert data["suggestion_source"] == "manual"
    assert data["verification_status"] == "pending"
    assert data["id"] is not None


def test_create_trade_with_price_and_quantity(tmp_path, monkeypatch):
    client = _make_client(
        tmp_path,
        monkeypatch,
        seed_assets=[("US_STK_AAPL", "Apple Inc", "US Equity", "USD")],
    )
    resp = client.post(
        "/ai-advisor/trades",
        json={
            "log_date": "2026-03-20",
            "asset_id": "US_STK_AAPL",
            "action": "Buy",
            "price": 150.0,
            "quantity": 10.0,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["amount"] == 1500.0  # price * quantity
    assert data["price"] == 150.0
    assert data["quantity"] == 10.0


def test_create_trade_validates_amount_or_price_qty(tmp_path, monkeypatch):
    client = _make_client(
        tmp_path,
        monkeypatch,
        seed_assets=[("US_STK_AAPL", "Apple Inc", "US Equity", "USD")],
    )
    resp = client.post(
        "/ai-advisor/trades",
        json={
            "log_date": "2026-03-20",
            "asset_id": "US_STK_AAPL",
            "action": "Buy",
            # No amount, no price+qty
        },
    )
    assert resp.status_code == 422
    assert "amount" in resp.json()["detail"] or "price" in resp.json()["detail"]


def test_create_trade_allows_unknown_asset_id_with_defaults(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    resp = client.post(
        "/ai-advisor/trades",
        json={
            "log_date": "2026-03-20",
            "asset_id": "NONEXISTENT_ASSET",
            "action": "Buy",
            "amount": 1000.0,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["asset_id"] == "NONEXISTENT_ASSET"
    assert resp.json()["asset_name"] == "NONEXISTENT_ASSET"
    assert resp.json()["currency"] == "USD"


def test_create_trade_defaults_currency_from_asset(tmp_path, monkeypatch):
    client = _make_client(
        tmp_path,
        monkeypatch,
        seed_assets=[("CN_FUND_001", "华夏蓝筹基金", "CN Equity", "CNY")],
    )
    resp = client.post(
        "/ai-advisor/trades",
        json={
            "log_date": "2026-03-20",
            "asset_id": "CN_FUND_001",
            "action": "Buy",
            "amount": 10000.0,
            # No currency provided — should default to "CNY" from asset_registry
        },
    )
    assert resp.status_code == 201
    assert resp.json()["currency"] == "CNY"


def test_create_trade_stores_explicit_memo_link(tmp_path, monkeypatch):
    client = _make_client(
        tmp_path,
        monkeypatch,
        seed_assets=[("US_STK_AAPL", "Apple Inc", "US Equity", "USD")],
        seed_memos=[
            (
                7,
                "2026-03-18",
                "Apple accumulation memo",
                "neutral",
                '["Build starter position"]',
                None,
                "# Apple accumulation memo",
            )
        ],
    )

    resp = client.post(
        "/ai-advisor/trades",
        json={
            "log_date": "2026-03-20",
            "asset_id": "US_STK_AAPL",
            "action": "Buy",
            "amount": 5000.0,
            "memo_id": 7,
        },
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["linked_memo_id"] == 7

    list_resp = client.get("/ai-advisor/trades")
    assert list_resp.status_code == 200
    assert list_resp.json()["trades"][0]["linked_memo_id"] == 7


def test_create_trade_best_effort_scoring_failure_does_not_break_response(tmp_path, monkeypatch):
    calls = []

    def boom(db, trade_id):
        calls.append(trade_id)
        raise RuntimeError("score failed")

    monkeypatch.setattr(ai_advisor_routes, "score_single_trade", boom, raising=False)
    client = _make_client(
        tmp_path,
        monkeypatch,
        seed_assets=[("US_STK_AAPL", "Apple Inc", "US Equity", "USD")],
    )
    resp = client.post(
        "/ai-advisor/trades",
        json={
            "log_date": "2026-03-20",
            "asset_id": "US_STK_AAPL",
            "action": "Buy",
            "amount": 5000.0,
        },
    )

    assert resp.status_code == 201
    trade_id = resp.json()["id"]
    assert calls == [trade_id]

    conn = duckdb.connect(str(ai_advisor_routes._DB_PATH), read_only=True)
    try:
        stored = conn.execute(
            "SELECT asset_id, suggestion_source FROM trade_logs WHERE id = ?",
            [trade_id],
        ).fetchone()
    finally:
        conn.close()

    assert stored == ("US_STK_AAPL", "manual")


# ---------------------------------------------------------------------------
# List trades tests
# ---------------------------------------------------------------------------

def test_list_trades(tmp_path, monkeypatch):
    client = _make_client(
        tmp_path,
        monkeypatch,
        seed_assets=[("US_STK_AAPL", "Apple Inc", "US Equity", "USD")],
    )
    # Create a trade first
    client.post(
        "/ai-advisor/trades",
        json={
            "log_date": "2026-03-20",
            "asset_id": "US_STK_AAPL",
            "action": "Sell",
            "amount": 3000.0,
            "decision_reason": "Profit taking",
        },
    )

    resp = client.get("/ai-advisor/trades")
    assert resp.status_code == 200
    trades = resp.json()["trades"]
    assert len(trades) >= 1
    found = next((t for t in trades if t["asset_id"] == "US_STK_AAPL"), None)
    assert found is not None
    assert found["action"] == "Sell"
    assert found["decision_reason"] == "Profit taking"


# ---------------------------------------------------------------------------
# Delete trade tests
# ---------------------------------------------------------------------------

def test_delete_trade_manual(tmp_path, monkeypatch):
    client = _make_client(
        tmp_path,
        monkeypatch,
        seed_assets=[("US_STK_AAPL", "Apple Inc", "US Equity", "USD")],
    )
    # Create a trade
    create_resp = client.post(
        "/ai-advisor/trades",
        json={
            "log_date": "2026-03-20",
            "asset_id": "US_STK_AAPL",
            "action": "Buy",
            "amount": 1000.0,
        },
    )
    assert create_resp.status_code == 201
    trade_id = create_resp.json()["id"]

    # Delete it
    del_resp = client.delete(f"/ai-advisor/trades/{trade_id}")
    assert del_resp.status_code == 204


def test_delete_trade_403_non_manual(tmp_path, monkeypatch):
    client = _make_client(
        tmp_path,
        monkeypatch,
        seed_assets=[("US_STK_AAPL", "Apple Inc", "US Equity", "USD")],
        seed_trades=[
            ("2026-03-01", "US_STK_AAPL", "Apple Inc", "Buy", 150.0, 5.0, 750.0, "USD", "AI suggestion", "AIA", None),
        ],
    )
    # Get the id of the inserted trade
    list_resp = client.get("/ai-advisor/trades")
    trades = list_resp.json()["trades"]
    assert len(trades) == 1
    trade_id = trades[0]["id"]

    del_resp = client.delete(f"/ai-advisor/trades/{trade_id}")
    assert del_resp.status_code == 403
    assert "suggestion_source='AIA'" in del_resp.json()["detail"]


def test_delete_trade_404_missing(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    resp = client.delete("/ai-advisor/trades/99999")
    assert resp.status_code == 404
    assert "99999" in resp.json()["detail"]
