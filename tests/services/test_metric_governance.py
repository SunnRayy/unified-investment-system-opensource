"""Tests for src/services/metric_governance.py (PRD 2026-07-07 F4.3/F4.4/F4.6,
Batch B5). In-memory DuckDB via initialize_schema (never a bare, schema-less
connector — see CLAUDE.md Database Safety Rules). schema.sql mirrors migration
012, so a fresh in-memory DB already has metric_catalog seeded (buffett_indicator,
csi500_pe, vix, fx_usd_cny, sp500_pe_percentile, rebalance_discipline) and the
data_fixes backlog (2 done + 3 open)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.database.seed_loader import seed_demo_content
from src.services.metric_governance import (
    evaluate_reliability,
    get_metrics_overview,
    log_ruling_deferred,
    require_methodology,
)


@pytest.fixture
def conn():
    c = DatabaseConnector(":memory:")
    initialize_schema(c)
    yield c
    c.close()


# ── evaluate_reliability: freshness-class staleness ─────────────────────────

def test_fast_metric_25h_old_is_unreliable(conn):
    now = datetime(2026, 7, 7, 12, 0, 0)
    as_of = now - timedelta(hours=25)
    result = evaluate_reliability(conn, "vix", as_of, now=now)
    assert result["reliable"] is False
    assert result["freshness_class"] == "fast"
    assert "stale" in result["reason"]


def test_slow_metric_6d_old_is_reliable(conn):
    now = datetime(2026, 7, 7, 12, 0, 0)
    as_of = now - timedelta(days=6)
    result = evaluate_reliability(conn, "rebalance_discipline", as_of, now=now)
    assert result["reliable"] is True
    assert result["freshness_class"] == "slow"


def test_slow_metric_8d_old_is_unreliable(conn):
    now = datetime(2026, 7, 7, 12, 0, 0)
    as_of = now - timedelta(days=8)
    result = evaluate_reliability(conn, "rebalance_discipline", as_of, now=now)
    assert result["reliable"] is False
    assert "stale" in result["reason"]


def test_missing_as_of_is_unreliable(conn):
    result = evaluate_reliability(conn, "vix", None)
    assert result["reliable"] is False
    assert result["reason"] == "no as_of timestamp"


def test_unknown_metric_treated_as_slow(conn):
    now = datetime(2026, 7, 7, 12, 0, 0)
    fresh = now - timedelta(hours=1)
    result = evaluate_reliability(conn, "totally_unknown_metric", fresh, now=now)
    assert result["freshness_class"] == "slow"
    assert result["reliable"] is True
    assert "unknown metric_key" in result["reason"]

    stale = now - timedelta(days=8)
    result_stale = evaluate_reliability(conn, "totally_unknown_metric", stale, now=now)
    assert result_stale["reliable"] is False
    assert "unknown metric_key" in result_stale["reason"]


# ── evaluate_reliability: F4.6 overdue data_fix auto-flip ───────────────────

def test_overdue_open_data_fix_flips_metric_unreliable(conn):
    now = datetime(2026, 7, 7, 12, 0, 0)
    fresh_as_of = now - timedelta(hours=1)  # would otherwise be reliable

    conn.execute(
        """
        INSERT INTO data_fixes (title, metric_key, opened_at, due_at, status)
        VALUES ('test overdue fix', 'vix', ?, ?, 'open')
        """,
        [now - timedelta(days=10), now - timedelta(days=1)],
    )

    result = evaluate_reliability(conn, "vix", fresh_as_of, now=now)
    assert result["reliable"] is False
    assert "overdue data_fix" in result["reason"]


def test_done_data_fix_does_not_flip_metric(conn):
    now = datetime(2026, 7, 7, 12, 0, 0)
    fresh_as_of = now - timedelta(hours=1)

    conn.execute(
        """
        INSERT INTO data_fixes (title, metric_key, opened_at, due_at, status, closed_at)
        VALUES ('test done fix', 'vix', ?, ?, 'done', ?)
        """,
        [now - timedelta(days=10), now - timedelta(days=1), now],
    )

    result = evaluate_reliability(conn, "vix", fresh_as_of, now=now)
    assert result["reliable"] is True


# ── log_ruling_deferred ──────────────────────────────────────────────────────

def test_log_ruling_deferred_inserts_event(conn):
    log_ruling_deferred(conn, "vix", "value_trap:TEST_ASSET")
    rows = conn.execute(
        "SELECT metric_key, context FROM ruling_deferred_events"
    ).fetchall()
    assert rows == [("vix", "value_trap:TEST_ASSET")]


# ── require_methodology (F4.3) ───────────────────────────────────────────────

def test_require_methodology_raises_for_buffett_indicator_without_tag(conn):
    with pytest.raises(ValueError, match="methodology_sensitive"):
        require_methodology(conn, "buffett_indicator", None)
    with pytest.raises(ValueError):
        require_methodology(conn, "buffett_indicator", "")


def test_require_methodology_passes_with_tag(conn):
    require_methodology(conn, "buffett_indicator", "buffett_classic_tmc_gdp")  # no raise


def test_require_methodology_ignores_non_sensitive_metric(conn):
    require_methodology(conn, "vix", None)  # vix is not methodology_sensitive -> no raise


def test_require_methodology_ignores_unknown_metric(conn):
    require_methodology(conn, "some_metric_not_in_catalog", None)  # no raise


# ── get_metrics_overview ─────────────────────────────────────────────────────

def test_get_metrics_overview_includes_seeded_metrics_and_fix_counts(conn):
    # Program OSR WS-3c: data_fixes seed moved out of schema.sql into the
    # seed-pack system — test session runs under $UIS_SEED_PROFILE=example
    # (tests/conftest.py), so this populates the persona's 3 example entries
    # (2 open on rebalance_discipline/fx_usd_cny, 1 done on rebalance_discipline).
    seed_demo_content(conn)
    overview = get_metrics_overview(conn)
    by_key = {row["metric_key"]: row for row in overview}

    for expected_key in (
        "buffett_indicator", "csi500_pe", "vix", "fx_usd_cny",
        "sp500_pe_percentile", "rebalance_discipline",
    ):
        assert expected_key in by_key

    assert by_key["buffett_indicator"]["methodology_sensitive"] is True
    assert by_key["buffett_indicator"]["open_fix_count"] == 0  # no persona fix tied to it
    assert by_key["rebalance_discipline"]["open_fix_count"] == 1  # one open, one done
    assert by_key["fx_usd_cny"]["open_fix_count"] == 1
