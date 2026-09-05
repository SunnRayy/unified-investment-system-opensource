"""Tests for verification_service.compute_verification_report — neutral verdict addendum.

Focuses on:
  - verdict_hit_rate denominator excludes neutral (decisive-only)
  - verdict_breakdown gains 'neutrals' column
"""
from __future__ import annotations

from datetime import date, timedelta

from src.database.connector import DatabaseConnector
from src.services.verification_service import compute_verification_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_db() -> DatabaseConnector:
    """In-memory DuckDB with the minimal schema required by compute_verification_report."""
    db = DatabaseConnector(":memory:")

    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_trade_logs_id START 1")
    db.execute("""
        CREATE TABLE trade_logs (
            id                  INTEGER PRIMARY KEY DEFAULT nextval('seq_trade_logs_id'),
            log_date            DATE NOT NULL,
            asset_id            VARCHAR(50) NOT NULL,
            action              VARCHAR(20) NOT NULL,
            verdict             VARCHAR(50),
            outcome_pct         DECIMAL(10,4),
            verification_status VARCHAR(20) DEFAULT 'verified',
            verification_result TEXT,
            suggestion_source   VARCHAR(50),
            ai_suggestion       TEXT,
            decision_reason     TEXT,
            linked_transaction_id INTEGER,
            linked_memo_id      INTEGER,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_insights_id START 1")
    db.execute("""
        CREATE TABLE insights (
            id           INTEGER PRIMARY KEY DEFAULT nextval('seq_insights_id'),
            insight_date DATE,
            category     VARCHAR(100),
            content      TEXT,
            title        VARCHAR(200),
            ai_model     VARCHAR(50),
            adopted      BOOLEAN,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.execute("""
        CREATE TABLE verification_logs (
            id                   INTEGER PRIMARY KEY,
            verification_date    DATE,
            verification_type    VARCHAR(50),
            period_start         DATE,
            period_end           DATE,
            adoption_rate        DOUBLE,
            max_allocation_drift DOUBLE,
            total_insights       INTEGER,
            generated_by         VARCHAR(50),
            portfolio_return     DOUBLE,
            benchmark_return     DOUBLE,
            alpha                DOUBLE,
            created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.execute("""
        CREATE TABLE market_daily (
            code  VARCHAR(20),
            date  DATE,
            close DOUBLE,
            PRIMARY KEY (code, date)
        )
    """)

    db.execute("""
        CREATE TABLE asset_registry (
            asset_id      VARCHAR(50) PRIMARY KEY,
            asset_class   VARCHAR(50),
            is_rebalanceable BOOLEAN DEFAULT TRUE
        )
    """)

    db.execute("""
        CREATE TABLE taxonomy_classes (
            id              INTEGER PRIMARY KEY,
            name            VARCHAR(100),
            parent_id       INTEGER,
            is_rebalanceable BOOLEAN DEFAULT TRUE
        )
    """)

    return db


def _insert_trade(
    db: DatabaseConnector,
    *,
    verdict: str | None,
    log_date: date | None = None,
    asset_id: str = "US_STK_TEST",
    action: str = "buy",
) -> None:
    ld = log_date or (date.today() - timedelta(days=60))
    db.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, verdict, verification_status)
        VALUES (?, ?, ?, ?, 'verified')
        """,
        (ld, asset_id, action, verdict),
    )


# ---------------------------------------------------------------------------
# Tests — hit rate excludes neutral from denominator
# ---------------------------------------------------------------------------

def test_verdict_hit_rate_excludes_neutral_from_denominator():
    """1 good_call + 1 neutral → hit_rate = 100.0, not 50.0.

    Neutral is in neither the numerator nor the denominator of verdict_hit_rate.
    It is a resolved verdict that should not penalise the hit-rate KPI.
    """
    db = _setup_db()
    _insert_trade(db, verdict="good_call")
    _insert_trade(db, verdict="neutral")

    report = compute_verification_report(db)

    assert report["verdict_hit_rate"] == 100.0, (
        f"Expected 100.0 (1 good_call / 1 decisive), "
        f"got {report['verdict_hit_rate']} (neutral must not be in denominator)"
    )
    assert report["good_calls"] == 1
    # total_scored = decisive count
    assert report["total_scored"] == 1, (
        "total_scored should be decisive count only (neutral excluded)"
    )
    db.close()


def test_verdict_hit_rate_pure_decisives():
    """Sanity: 2 good_call + 1 regret → hit_rate = 66.7%."""
    db = _setup_db()
    _insert_trade(db, verdict="good_call")
    _insert_trade(db, verdict="good_call")
    _insert_trade(db, verdict="regret")

    report = compute_verification_report(db)

    assert report["verdict_hit_rate"] == pytest.approx(66.7, abs=0.1), (
        f"Expected ~66.7%, got {report['verdict_hit_rate']}"
    )
    assert report["total_scored"] == 3
    db.close()


def test_verdict_hit_rate_all_neutral_is_none():
    """All neutral + no decisive verdicts → hit_rate = None (not 0.0 or 100.0)."""
    db = _setup_db()
    _insert_trade(db, verdict="neutral")
    _insert_trade(db, verdict="neutral")

    report = compute_verification_report(db)

    assert report["verdict_hit_rate"] is None, (
        f"Hit rate must be None when denominator (decisive verdicts) is 0; "
        f"got {report['verdict_hit_rate']}"
    )
    db.close()


# ---------------------------------------------------------------------------
# Tests — verdict_breakdown gains 'neutrals' column
# ---------------------------------------------------------------------------

def test_verdict_breakdown_includes_neutrals():
    """Monthly verdict breakdown must include a 'neutrals' key."""
    db = _setup_db()
    log_date = date.today().replace(day=1)  # first of current month
    _insert_trade(db, verdict="good_call", log_date=log_date)
    _insert_trade(db, verdict="neutral", log_date=log_date)

    report = compute_verification_report(db)

    assert "verdict_breakdown" in report
    # Find the month containing our inserted rows
    current_month = date.today().replace(day=1).isoformat()
    month_row = next(
        (r for r in report["verdict_breakdown"] if r["period_start"] == current_month),
        None,
    )
    assert month_row is not None, f"Expected a breakdown row for {current_month}"
    assert "neutrals" in month_row, "verdict_breakdown row must include 'neutrals' key"
    assert month_row["neutrals"] == 1, (
        f"Expected neutrals=1; got {month_row['neutrals']}"
    )
    assert month_row["good_calls"] == 1
    assert month_row["total_scored"] == 2, (
        "total_scored in breakdown includes all non-NULL verdicts (including neutral)"
    )
    db.close()


def test_verdict_breakdown_backward_compatible():
    """Existing breakdown keys are unchanged — 'neutrals' is purely additive."""
    db = _setup_db()
    log_date = date.today().replace(day=1)
    _insert_trade(db, verdict="good_call", log_date=log_date)
    _insert_trade(db, verdict="regret", log_date=log_date)
    _insert_trade(db, verdict="bullet_dodged", log_date=log_date)
    _insert_trade(db, verdict="missed_opportunity", log_date=log_date)

    report = compute_verification_report(db)
    month_row = report["verdict_breakdown"][0]

    for key in ("good_calls", "regrets", "missed_opportunity", "bullet_dodged", "total_scored", "neutrals"):
        assert key in month_row, f"Key '{key}' missing from verdict_breakdown row"

    assert month_row["neutrals"] == 0, "neutrals=0 when no neutral verdicts in period"
    db.close()


import pytest  # noqa: E402 (pytest needed for approx; import after all helpers)
