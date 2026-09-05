"""Tests for src.services.goal_resolver.resolve_north_star_goal
(docs/plans/2026-07-26-your-path-design-implementation.md §3, W-1).

In-memory DuckDB via initialize_schema (never a bare, schema-less connector
— see CLAUDE.md Database Safety Rules). Never connects to data/unified.duckdb.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.goal_resolver import resolve_north_star_goal
from src.services.verification_config import NorthStarSection, VerificationConfig


def _make_db() -> DatabaseConnector:
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    return conn


def _insert_goal(
    conn,
    name: str,
    target_amount: float,
    target_date: str,
    goal_type: str = "retirement",
    status: str = "active",
) -> int:
    conn.execute(
        """
        INSERT INTO goals (name, target_amount, target_date, goal_type, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        [name, target_amount, target_date, goal_type, status],
    )
    return conn.execute(
        "SELECT id FROM goals WHERE name = ? ORDER BY id DESC LIMIT 1", [name]
    ).fetchone()[0]


# ── goals-hit path ───────────────────────────────────────────────────────

def test_resolves_active_retirement_goal():
    conn = _make_db()
    goal_id = _insert_goal(conn, "FIRE", 25_000_000.0, "2040-12-31")

    result = resolve_north_star_goal(conn)

    assert result["source"] == "goals"
    assert result["target_amount"] == pytest.approx(25_000_000.0)
    assert result["goal_id"] == goal_id
    assert result["name"] == "FIRE"
    assert result["target_date"] == "2040-12-31"
    assert result["fallback_reason"] is None


def test_target_amount_is_float_not_decimal():
    """goals.target_amount is DECIMAL(20,2) -> DuckDB returns decimal.Decimal.
    The resolver must convert to float so downstream arithmetic
    (months_to_target / future_value) never mixes types."""
    conn = _make_db()
    _insert_goal(conn, "FIRE", 12_345_678.90, "2035-06-01")

    result = resolve_north_star_goal(conn)

    assert isinstance(result["target_amount"], float)
    assert result["target_amount"] == pytest.approx(12_345_678.90)


def test_target_date_is_iso_string_not_date_object():
    conn = _make_db()
    _insert_goal(conn, "FIRE", 25_000_000.0, "2038-01-15")

    result = resolve_north_star_goal(conn)

    assert result["target_date"] == "2038-01-15"
    assert isinstance(result["target_date"], str)


# ── case-insensitivity on goal_type ──────────────────────────────────────

@pytest.mark.parametrize("goal_type", ["retirement", "Retirement", "RETIREMENT", "ReTiReMent"])
def test_goal_type_case_insensitive(goal_type: str):
    conn = _make_db()
    _insert_goal(conn, "FIRE", 25_000_000.0, "2040-12-31", goal_type=goal_type)

    result = resolve_north_star_goal(conn)

    assert result["source"] == "goals"
    assert result["target_amount"] == pytest.approx(25_000_000.0)


# ── tie-break: furthest target_date wins, then id DESC ───────────────────

def test_tie_break_furthest_target_date_wins():
    conn = _make_db()
    _insert_goal(conn, "Interim", 15_000_000.0, "2035-01-01")
    far_id = _insert_goal(conn, "Far", 25_000_000.0, "2045-01-01")

    result = resolve_north_star_goal(conn)

    assert result["goal_id"] == far_id
    assert result["target_amount"] == pytest.approx(25_000_000.0)
    assert result["name"] == "Far"


def test_tie_break_same_date_id_desc_wins():
    conn = _make_db()
    _insert_goal(conn, "First", 20_000_000.0, "2040-12-31")
    second_id = _insert_goal(conn, "Second", 22_000_000.0, "2040-12-31")

    result = resolve_north_star_goal(conn)

    assert result["goal_id"] == second_id
    assert result["target_amount"] == pytest.approx(22_000_000.0)


# ── non-matching rows correctly fall out of the query ────────────────────

def test_inactive_retirement_goal_ignored():
    conn = _make_db()
    _insert_goal(conn, "Old FIRE", 30_000_000.0, "2040-12-31", status="completed")

    result = resolve_north_star_goal(conn)

    assert result["source"] == "config_fallback"


def test_non_retirement_goal_type_ignored():
    conn = _make_db()
    _insert_goal(conn, "House", 3_000_000.0, "2030-01-01", goal_type="house")

    result = resolve_north_star_goal(conn)

    assert result["source"] == "config_fallback"


def test_null_goal_type_ignored():
    """LOWER(NULL) is NULL in SQL -> null-goal_type rows correctly fall out
    of the WHERE clause, they must not be 'fixed' to match."""
    conn = _make_db()
    conn.execute(
        "INSERT INTO goals (name, target_amount, target_date, goal_type, status) VALUES (?, ?, ?, NULL, 'active')",
        ["Mystery Goal", 9_000_000.0, "2033-01-01"],
    )

    result = resolve_north_star_goal(conn)

    assert result["source"] == "config_fallback"


# ── config-fallback path: no active retirement goal ──────────────────────

def test_no_retirement_goal_falls_back_to_config():
    conn = _make_db()  # empty goals table

    fake_cfg = VerificationConfig(north_star=NorthStarSection(target_net_worth_cny=18_500_000.0))
    with patch("src.services.goal_resolver.load_verification_config", return_value=fake_cfg):
        result = resolve_north_star_goal(conn)

    assert result["source"] == "config_fallback"
    assert result["fallback_reason"] == "no active retirement goal"
    assert result["target_amount"] == pytest.approx(18_500_000.0)
    assert result["goal_id"] is None
    assert result["name"] is None
    assert result["target_date"] is None


# ── query-failure path: labelled fallback, never raises ──────────────────

def test_goals_query_failure_falls_back_and_logs(caplog):
    conn = _make_db()

    fake_cfg = VerificationConfig(north_star=NorthStarSection(target_net_worth_cny=21_000_000.0))
    with patch.object(conn, "execute", side_effect=RuntimeError("boom")), \
         patch("src.services.goal_resolver.load_verification_config", return_value=fake_cfg):
        result = resolve_north_star_goal(conn)  # must not raise

    assert result["source"] == "config_fallback"
    assert result["fallback_reason"] == "goals query failed"
    assert result["target_amount"] == pytest.approx(21_000_000.0)


def test_never_returns_none_target_amount():
    conn = _make_db()
    result = resolve_north_star_goal(conn)
    assert result["target_amount"] is not None
    assert isinstance(result["target_amount"], float)
