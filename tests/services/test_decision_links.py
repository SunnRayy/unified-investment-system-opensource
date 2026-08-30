"""Tests for src/services/decision_links.py — insight_trade_links CRUD and auto-upsert."""
import pytest
from datetime import date
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema


def _fresh_db(tmp_path, name="test_links.duckdb"):
    db_path = tmp_path / name
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    conn.run_migrations()
    return conn


def _seed_insight(conn, insight_id, ai_model, insight_date):
    conn.execute(
        """INSERT INTO insights (id, insight_date, insight_type, content, ai_model, adopted, created_at)
           VALUES (?, ?, 'trade_signal', 'test content', ?, TRUE, CURRENT_TIMESTAMP)""",
        [insight_id, str(insight_date), ai_model],
    )


def _seed_trade(conn, trade_id, suggestion_source, log_date, asset_id="US_STK_AAPL"):
    conn.execute(
        """INSERT INTO trade_logs (id, log_date, asset_id, asset_name, action,
                                   suggestion_source, created_at)
           VALUES (?, ?, ?, 'Apple', 'BUY', ?, CURRENT_TIMESTAMP)""",
        [trade_id, str(log_date), asset_id, suggestion_source],
    )


# ── recompute_auto_links ───────────────────────────────────────────────────

def test_recompute_auto_links_creates_link_for_matching_pair(tmp_path):
    from src.services.decision_links import recompute_auto_links

    conn = _fresh_db(tmp_path, "auto_link.duckdb")
    _seed_insight(conn, 1, "gemini-2.5-flash", date(2026, 1, 10))
    _seed_trade(conn, 1, "Gemini-2.5-Flash", date(2026, 1, 11))  # 1-day gap, source matches (case-insensitive)

    count = recompute_auto_links(conn)

    rows = conn.execute("SELECT insight_id, trade_id, link_type, confidence FROM insight_trade_links").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 1  # insight_id
    assert rows[0][1] == 1  # trade_id
    assert rows[0][2] == "auto_source"
    assert float(rows[0][3]) == pytest.approx(0.75, abs=0.01)  # 1.0 - 1/4 = 0.75
    assert count == 1
    conn.close()


def test_recompute_auto_links_skips_outside_3_day_window(tmp_path):
    from src.services.decision_links import recompute_auto_links

    conn = _fresh_db(tmp_path, "window.duckdb")
    _seed_insight(conn, 1, "gemini-2.5-flash", date(2026, 1, 10))
    _seed_trade(conn, 1, "gemini-2.5-flash", date(2026, 1, 15))  # 5-day gap — outside window

    recompute_auto_links(conn)

    count = conn.execute("SELECT COUNT(*) FROM insight_trade_links").fetchone()[0]
    assert count == 0
    conn.close()


def test_recompute_auto_links_skips_source_mismatch(tmp_path):
    from src.services.decision_links import recompute_auto_links

    conn = _fresh_db(tmp_path, "mismatch.duckdb")
    _seed_insight(conn, 1, "gemini-2.5-flash", date(2026, 1, 10))
    _seed_trade(conn, 1, "deepseek", date(2026, 1, 11))  # different source

    recompute_auto_links(conn)

    count = conn.execute("SELECT COUNT(*) FROM insight_trade_links").fetchone()[0]
    assert count == 0
    conn.close()


def test_recompute_auto_links_is_idempotent(tmp_path):
    from src.services.decision_links import recompute_auto_links

    conn = _fresh_db(tmp_path, "idem_links.duckdb")
    _seed_insight(conn, 1, "gemini-2.5-flash", date(2026, 1, 10))
    _seed_trade(conn, 1, "gemini-2.5-flash", date(2026, 1, 10))  # same day

    recompute_auto_links(conn)
    recompute_auto_links(conn)  # second call must not duplicate

    count = conn.execute("SELECT COUNT(*) FROM insight_trade_links").fetchone()[0]
    assert count == 1
    conn.close()


def test_recompute_auto_links_confidence_at_day_0(tmp_path):
    from src.services.decision_links import recompute_auto_links

    conn = _fresh_db(tmp_path, "conf0.duckdb")
    _seed_insight(conn, 1, "gemini-2.5-flash", date(2026, 1, 10))
    _seed_trade(conn, 1, "gemini-2.5-flash", date(2026, 1, 10))  # 0-day gap

    recompute_auto_links(conn)

    row = conn.execute("SELECT confidence FROM insight_trade_links").fetchone()
    assert float(row[0]) == pytest.approx(1.0, abs=0.01)
    conn.close()


def test_recompute_auto_links_filters_by_trade_id(tmp_path):
    from src.services.decision_links import recompute_auto_links

    conn = _fresh_db(tmp_path, "filter_trade.duckdb")
    _seed_insight(conn, 1, "gemini-2.5-flash", date(2026, 1, 10))
    _seed_trade(conn, 1, "gemini-2.5-flash", date(2026, 1, 10))
    _seed_trade(conn, 2, "gemini-2.5-flash", date(2026, 1, 11))

    recompute_auto_links(conn, trade_id=1)

    rows = conn.execute("SELECT trade_id FROM insight_trade_links ORDER BY trade_id").fetchall()
    assert [r[0] for r in rows] == [1]  # only trade 1 linked
    conn.close()


def test_recompute_auto_links_filters_by_insight_id(tmp_path):
    from src.services.decision_links import recompute_auto_links

    conn = _fresh_db(tmp_path, "filter_insight.duckdb")
    _seed_insight(conn, 1, "gemini-2.5-flash", date(2026, 1, 10))
    _seed_insight(conn, 2, "gemini-2.5-flash", date(2026, 1, 12))
    _seed_trade(conn, 1, "gemini-2.5-flash", date(2026, 1, 10))

    recompute_auto_links(conn, insight_id=1)

    rows = conn.execute("SELECT insight_id FROM insight_trade_links").fetchall()
    assert [r[0] for r in rows] == [1]  # only insight 1 linked
    conn.close()


# ── add_manual_link ────────────────────────────────────────────────────────

def test_add_manual_link_inserts_with_full_confidence(tmp_path):
    from src.services.decision_links import add_manual_link

    conn = _fresh_db(tmp_path, "manual.duckdb")
    link_id = add_manual_link(conn, insight_id=10, trade_id=20, rationale="手动关联")

    row = conn.execute(
        "SELECT insight_id, trade_id, link_type, confidence, rationale FROM insight_trade_links WHERE id = ?",
        [link_id],
    ).fetchone()
    assert row[0] == 10
    assert row[1] == 20
    assert row[2] == "manual"
    assert float(row[3]) == pytest.approx(1.0, abs=0.01)
    assert row[4] == "手动关联"
    conn.close()


def test_add_manual_link_idempotent_on_duplicate(tmp_path):
    from src.services.decision_links import add_manual_link

    conn = _fresh_db(tmp_path, "manual_idem.duckdb")
    add_manual_link(conn, insight_id=5, trade_id=6, rationale="first")
    add_manual_link(conn, insight_id=5, trade_id=6, rationale="second")  # same pair

    count = conn.execute("SELECT COUNT(*) FROM insight_trade_links").fetchone()[0]
    assert count == 1, "Duplicate manual link should not create a second row"
    conn.close()


# ── remove_link ────────────────────────────────────────────────────────────

def test_remove_link_deletes_the_row(tmp_path):
    from src.services.decision_links import add_manual_link, remove_link

    conn = _fresh_db(tmp_path, "remove.duckdb")
    link_id = add_manual_link(conn, insight_id=1, trade_id=1, rationale="to remove")

    remove_link(conn, link_id)

    count = conn.execute("SELECT COUNT(*) FROM insight_trade_links WHERE id = ?", [link_id]).fetchone()[0]
    assert count == 0
    conn.close()


def test_remove_link_nonexistent_is_silent(tmp_path):
    from src.services.decision_links import remove_link

    conn = _fresh_db(tmp_path, "remove_noop.duckdb")
    remove_link(conn, 9999)  # should not raise
    conn.close()
