"""The Decision Hub's headline count must agree with the list underneath it.

Round 2 §3: `/decisions` showed "Total Decisions: 0, Adoption Rate: 0%,
Pending Actions: 0, Drift Alerts: 0" directly above a Timeline tab listing real
executed trades — Sell US_STK_IEFA, Buy US_STK_VTI, correctly dated.

The tile was computing `total_insights + ai_trades_total`. `ai_trades_total`
counts only trades attributable to the AI, so a portfolio whose trades arrived
through a broker import scores zero on it — while the timeline lists every
trade in display scope, plus drift alerts the tile never counted at all. One
number, two independent derivations, rendered adjacent.

This is the project's two-sources signature. A shared helper
(`count_timeline_decisions`) is the fix; this file is what stops the next
fourth query from quietly reintroducing the gap.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_db
from src.api.main import app
from src.database.connector import DatabaseConnector
from src.database.schema import bootstrap_database


@pytest.fixture
def db(tmp_path):
    conn = DatabaseConnector(str(tmp_path / "decisions.duckdb"))
    bootstrap_database(conn)
    yield conn
    conn.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _seed_broker_trades(db, n=3):
    """Trades with no AI attribution — the exact population the old tile missed."""
    for i in range(n):
        db.execute(
            """
            INSERT INTO trade_logs
                (id, log_date, asset_id, action, quantity, price, amount,
                 suggestion_source, decision_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'broker_import', ?)
            """,
            [
                100 + i,
                f"2026-08-{10 + i:02d}",
                "US_STK_VTI",
                "Buy",
                10,
                250.0,
                2500.0,
                f"IBKR trade {i}",
            ],
        )


def test_stats_total_matches_the_timeline_population(client, db):
    _seed_broker_trades(db, n=3)
    db.execute(
        "INSERT INTO insights (id, insight_date, insight_type, title, content, category) "
        "VALUES (1, '2026-08-01', 'observation', 'An insight', 'body', 'observation')"
    )

    stats = client.get("/decisions/stats").json()
    timeline = client.get("/decisions/timeline?limit=200").json()

    rendered = len(timeline["items"])
    assert stats["total_decisions"] == rendered, (
        f"stats says {stats['total_decisions']} decisions, the timeline renders "
        f"{rendered} — a count contradicted by the list directly beneath it is "
        "the most trust-corrosive thing the app can show"
    )


def test_broker_trades_are_counted_even_though_the_ai_gets_no_credit(client, db):
    """The specific regression. Trades with no AI attribution are real
    decisions; they are simply not the AI's decisions."""
    _seed_broker_trades(db, n=4)

    stats = client.get("/decisions/stats").json()

    assert stats["ai_trades_total"] == 0, (
        "fixture no longer produces unattributed trades, so this test would "
        "pass without exercising the bug"
    )
    assert stats["total_decisions"] == 4, stats
    assert stats["timeline_counts"]["trade"] == 4, stats


def test_drift_alerts_count_towards_the_total(client, db):
    """The old sum omitted this population entirely."""
    db.execute(
        """
        INSERT INTO deviation_actions
            (id, detected_date, created_at, asset_class, deviation_pct,
             tolerance_pct, status)
        VALUES (1, '2026-08-20', '2026-08-20', 'Equity', 7.5, 5.0, 'open')
        """
    )

    stats = client.get("/decisions/stats").json()

    assert stats["timeline_counts"]["drift"] == 1, stats
    assert stats["total_decisions"] == 1, stats


def test_empty_database_agrees_at_zero(client):
    """A genuine zero must still be reachable — the fix must not turn every
    empty install into a phantom count."""
    stats = client.get("/decisions/stats").json()
    timeline = client.get("/decisions/timeline").json()

    assert stats["total_decisions"] == 0
    assert timeline["items"] == []


def test_total_is_not_capped_by_the_timeline_page_size(client, db):
    """`limit` is a page size, not a fact about the data."""
    _seed_broker_trades(db, n=12)

    stats = client.get("/decisions/stats").json()
    timeline = client.get("/decisions/timeline?limit=5").json()

    assert len(timeline["items"]) == 5
    assert stats["total_decisions"] == 12, (
        "the headline total tracked the page size instead of the data"
    )
