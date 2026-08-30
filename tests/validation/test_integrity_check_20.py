"""Tests for integrity check #20: insight_trade_links orphan detection (V5.10.0)."""
from src.database.connector import DatabaseConnector
from src.validation.data_integrity_gate import _check_insight_trade_links_no_orphans


def _make_db() -> DatabaseConnector:
    """In-memory DB with insight_trade_links + minimal insights/trade_logs."""
    db = DatabaseConnector(":memory:")

    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_insights_id START 1")
    db.execute("""
        CREATE TABLE IF NOT EXISTS insights (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_insights_id'),
            insight_date DATE NOT NULL,
            insight_type VARCHAR(50) NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_trade_logs_id START 1")
    db.execute("""
        CREATE TABLE IF NOT EXISTS trade_logs (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_trade_logs_id'),
            log_date DATE NOT NULL,
            asset_id VARCHAR(50) NOT NULL,
            action VARCHAR(20) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_insight_trade_links_id START 1")
    db.execute("""
        CREATE TABLE IF NOT EXISTS insight_trade_links (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_insight_trade_links_id'),
            insight_id INTEGER NOT NULL,
            trade_id INTEGER NOT NULL,
            link_type VARCHAR NOT NULL,
            confidence DECIMAL(3,2),
            rationale VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(insight_id, trade_id)
        )
    """)
    return db


def test_no_orphans_passes():
    """All link rows reference valid insight + trade → check passes."""
    db = _make_db()
    db.execute("INSERT INTO insights (insight_date, insight_type, content) VALUES ('2026-01-01', 'signal', 'x')")
    db.execute("INSERT INTO trade_logs (log_date, asset_id, action) VALUES ('2026-01-02', 'US_STK_AAPL', 'BUY')")
    db.execute("INSERT INTO insight_trade_links (insight_id, trade_id, link_type) VALUES (1, 1, 'auto_source')")

    result = _check_insight_trade_links_no_orphans(db)
    assert result.passed, f"Should pass with valid refs: {result.details}"
    db.close()


def test_orphaned_insight_id_fails():
    """Link row with non-existent insight_id → violation."""
    db = _make_db()
    # No insight row inserted — orphan link
    db.execute("INSERT INTO trade_logs (log_date, asset_id, action) VALUES ('2026-01-02', 'US_STK_AAPL', 'BUY')")
    db.execute("INSERT INTO insight_trade_links (insight_id, trade_id, link_type) VALUES (999, 1, 'auto_source')")

    result = _check_insight_trade_links_no_orphans(db)
    assert not result.passed, "Should fail: orphaned insight_id"
    assert "orphaned insight_id" in result.details.lower() or "insight" in result.details.lower()
    db.close()


def test_orphaned_trade_id_fails():
    """Link row with non-existent trade_id → violation."""
    db = _make_db()
    db.execute("INSERT INTO insights (insight_date, insight_type, content) VALUES ('2026-01-01', 'signal', 'x')")
    # No trade_logs row inserted — orphan link
    db.execute("INSERT INTO insight_trade_links (insight_id, trade_id, link_type) VALUES (1, 999, 'auto_source')")

    result = _check_insight_trade_links_no_orphans(db)
    assert not result.passed, "Should fail: orphaned trade_id"
    assert "orphaned trade_id" in result.details.lower() or "trade" in result.details.lower()
    db.close()


def test_empty_link_table_passes():
    """Empty insight_trade_links → trivially passes (no orphans)."""
    db = _make_db()
    result = _check_insight_trade_links_no_orphans(db)
    assert result.passed, "Empty link table should pass"
    db.close()


def test_skips_gracefully_when_table_absent():
    """If insight_trade_links doesn't exist → check is skipped (passes)."""
    db = DatabaseConnector(":memory:")
    # No tables created at all
    result = _check_insight_trade_links_no_orphans(db)
    assert result.passed, "Check should skip gracefully when table is absent"
    assert "skipped" in result.details.lower()
    db.close()
