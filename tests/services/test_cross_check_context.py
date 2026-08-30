"""Tests for build_cross_check_context() — V5.8.0 Batch C Step 6."""

from __future__ import annotations

from datetime import date, timedelta
from src.database.connector import DatabaseConnector


# ---------------------------------------------------------------------------
# In-memory DB helper
# ---------------------------------------------------------------------------

def _setup_db() -> DatabaseConnector:
    """Create in-memory DB with all required tables for cross-check context."""
    db = DatabaseConnector(":memory:")
    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_trade_logs_id START 1")
    db.execute("""
        CREATE TABLE IF NOT EXISTS trade_logs (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_trade_logs_id'),
            log_date DATE NOT NULL,
            asset_id VARCHAR(50) NOT NULL,
            asset_name VARCHAR(200),
            action VARCHAR(20) NOT NULL,
            price DECIMAL(20,8),
            quantity DECIMAL(20,8),
            suggestion_source VARCHAR(50),
            verdict VARCHAR(50),
            outcome_pct DECIMAL(10,4),
            verification_result VARCHAR(100),
            verification_status VARCHAR(20) DEFAULT 'pending',
            verification_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            verification_block_reason VARCHAR
        )
    """)
    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_insights_id START 1")
    db.execute("""
        CREATE TABLE IF NOT EXISTS insights (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_insights_id'),
            insight_date DATE NOT NULL,
            insight_type VARCHAR(50),
            category VARCHAR(100),
            content TEXT NOT NULL,
            adopted BOOLEAN,
            ai_model VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            title VARCHAR(200)
        )
    """)
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_returns_insights_with_dedup_linked_trades():
    """Dedup query should pick exactly one trade per (insight, trade) pair.

    Fixture: 1 insight + 2 trades both within ±3d with the same suggestion_source.
    The trade closer in time is preferred; on equal distance, deterministic by id DESC.
    """
    db = _setup_db()
    from src.services.ai_advisor.context_builder import build_cross_check_context

    period_start = date(2026, 3, 1)
    period_end = date(2026, 3, 30)

    # Insert insight on 2026-03-10 using model "gemini"
    db.execute(
        """INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model, title)
           VALUES (?, 'recommendation', 'strategy', 'Buy AAPL now', TRUE, 'gemini', 'Buy AAPL')""",
        [str(date(2026, 3, 10))],
    )
    db.execute("SELECT MAX(id) FROM insights").fetchone()  # confirm insert succeeded

    # Trade 1: 1 day after insight (distance 1) — should be preferred
    db.execute(
        """INSERT INTO trade_logs (log_date, asset_id, action, suggestion_source, verdict, outcome_pct,
                                   verification_result, verification_status, verification_date)
           VALUES (?, 'US_STK_AAPL', 'Buy', 'gemini', 'good_call', 8.5,
                   'Worked great', 'verified', ?)""",
        [str(date(2026, 3, 11)), str(date(2026, 3, 11))],
    )
    db.execute("SELECT MAX(id) FROM trade_logs").fetchone()  # confirm insert succeeded

    # Trade 2: 2 days after insight (distance 2) — should be deprioritized
    db.execute(
        """INSERT INTO trade_logs (log_date, asset_id, action, suggestion_source, verdict, outcome_pct,
                                   verification_result, verification_status, verification_date)
           VALUES (?, 'US_STK_MSFT', 'Buy', 'gemini', 'good_call', 5.0,
                   'Also good', 'verified', ?)""",
        [str(date(2026, 3, 12)), str(date(2026, 3, 12))],
    )

    result = build_cross_check_context(db, period_start, period_end)
    assert "error" not in result, f"Unexpected error: {result}"
    assert len(result["insights"]) == 1
    insight = result["insights"][0]

    # Both trades should be linked (different trade IDs, not duplicated entries)
    linked_trade_ids = [t["id"] for t in insight["linked_trades"]]
    assert len(linked_trade_ids) == len(set(linked_trade_ids)), "Duplicate trade IDs in linked_trades"


def test_caps_oversized_period_returns_error_dict():
    """Period > 90 days returns error dict with period_too_large key."""
    db = _setup_db()
    from src.services.ai_advisor.context_builder import build_cross_check_context

    period_start = date(2026, 1, 1)
    period_end = date(2026, 4, 11)  # 100 days

    result = build_cross_check_context(db, period_start, period_end)
    assert result.get("error") == "period_too_large"
    assert result["max_days"] == 90
    assert result["current_days"] == (period_end - period_start).days


def test_caps_too_many_insights_returns_error_dict():
    """51 insights in period returns error dict with too_many_insights key."""
    db = _setup_db()
    from src.services.ai_advisor.context_builder import build_cross_check_context

    period_start = date(2026, 3, 1)
    period_end = date(2026, 4, 20)  # 50 days, well within 90d cap

    # Insert 51 insights
    for i in range(51):
        db.execute(
            """INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model, title)
               VALUES (?, 'recommendation', 'strategy', ?, TRUE, 'gemini', ?)""",
            [str(date(2026, 3, 1) + timedelta(days=(i % 45))), f"Insight body {i}", f"Insight {i}"],
        )

    result = build_cross_check_context(db, period_start, period_end)
    assert result.get("error") == "too_many_insights"
    assert result["max_insights"] == 50
    assert result["current"] >= 51


def test_per_insight_aggregates_outcomes():
    """Aggregates good_calls=2, regrets=1, avg_outcome_pct correctly for 3 linked trades."""
    db = _setup_db()
    from src.services.ai_advisor.context_builder import build_cross_check_context

    period_start = date(2026, 3, 1)
    period_end = date(2026, 3, 30)

    # Insert one insight
    db.execute(
        """INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model, title)
           VALUES (?, 'recommendation', 'strategy', 'Buy BABA', TRUE, 'gemini', 'Buy BABA')""",
        [str(date(2026, 3, 10))],
    )

    # 3 linked trades: 2 good_call (outcome_pct 10.0 and 6.0), 1 regret (-8.0)
    for outcome_pct, verdict in [(10.0, "good_call"), (6.0, "good_call"), (-8.0, "regret")]:
        db.execute(
            """INSERT INTO trade_logs (log_date, asset_id, action, suggestion_source, verdict, outcome_pct,
                                       verification_result, verification_status, verification_date)
               VALUES (?, 'US_STK_BABA', 'Buy', 'gemini', ?, ?,
                       'Verified', 'verified', ?)""",
            [str(date(2026, 3, 11)), verdict, outcome_pct, str(date(2026, 3, 11))],
        )

    result = build_cross_check_context(db, period_start, period_end)
    assert "error" not in result, f"Unexpected error: {result}"
    assert len(result["insights"]) == 1
    summary = result["insights"][0]["summary"]
    assert summary["good_calls"] == 2
    assert summary["regrets"] == 1
    # avg_outcome_pct = (10.0 + 6.0 + -8.0) / 3 = 2.666...
    assert summary["avg_outcome_pct"] is not None
    assert abs(summary["avg_outcome_pct"] - (10.0 + 6.0 - 8.0) / 3) < 0.01


def test_includes_unadopted_insights_for_missed_opportunity_analysis():
    """Insights with adopted=False are included so the LLM can assess missed opportunities."""
    db = _setup_db()
    from src.services.ai_advisor.context_builder import build_cross_check_context

    period_start = date(2026, 3, 1)
    period_end = date(2026, 3, 30)

    # Adopted insight
    db.execute(
        """INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model, title)
           VALUES (?, 'recommendation', 'strategy', 'Buy TSLA', TRUE, 'gemini', 'Buy TSLA')""",
        [str(date(2026, 3, 5))],
    )
    # NOT adopted insight — should still appear
    db.execute(
        """INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model, title)
           VALUES (?, 'recommendation', 'strategy', 'Sell NVDA', FALSE, 'gemini', 'Sell NVDA')""",
        [str(date(2026, 3, 8))],
    )

    result = build_cross_check_context(db, period_start, period_end)
    assert "error" not in result, f"Unexpected error: {result}"
    assert len(result["insights"]) == 2
    adopted_flags = [i["adopted"] for i in result["insights"]]
    assert True in adopted_flags
    assert False in adopted_flags
