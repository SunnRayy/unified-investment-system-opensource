"""Tests for src.services.forecast_levers.compute_levers (R-2,
docs/plans/2026-07-25-forecast-planning-redesign.md).

In-memory DuckDB via initialize_schema (never a bare, schema-less connector
— see CLAUDE.md Database Safety Rules). Never connects to data/unified.duckdb.

suggested_return_basis and calculate_portfolio_metrics (the TWR/volatility
sources) are patched to fixed values in most tests — the same pattern
tests/services/test_north_star.py already uses for glide_path's
_default_trailing_twr (see test_glide_path_default_twr_uses_suggested_return_basis)
— because building a real multi-month TWR/volatility history is orthogonal
to what this module does (it is a thin, already-derived-elsewhere consumer
of those functions). current_nw and monthly_contribution, by contrast, ARE
computed for real from seeded holdings / income_expense_monthly in every
test, since those are the two inputs the anti-hardcoding test must prove
vary with the DB's actual content.
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.forecast_levers import compute_levers
from src.services.verification_config import NorthStarSection, VerificationConfig


def _make_db() -> DatabaseConnector:
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    return conn


def _seed_net_worth(conn, value: float, asset_id: str = "US_STK_EQ") -> None:
    """One rebalanceable holdings row (no asset_registry entry -> not
    excluded by fetch_non_rebalanceable_asset_ids, matches the
    _insert_monthly_holdings pattern in tests/services/test_north_star.py)."""
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, quantity, market_value, currency, source_system, is_shadow)
        VALUES (?, ?, ?, 1, ?, 'CNY', 'test', FALSE)
        """,
        [date.today().isoformat(), asset_id, asset_id, value],
    )


def _month_start_n_ago(today: date, n: int) -> date:
    year = today.year
    month = today.month - n
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _seed_run_rate(conn, monthly_amount: float, months: int = 12) -> None:
    """months of income_expense_monthly with monthly_amount net external
    investment; income set generously high (10x) so the 60% sanity guard in
    _contribution_run_rate never fires."""
    today = date.today()
    for i in range(months):
        month = _month_start_n_ago(today, i)
        payload = {
            "投资理财_股票基金_天天基金": monthly_amount,
            "收入_主动收入_工资": monthly_amount * 10.0,
        }
        conn.execute(
            "INSERT INTO income_expense_monthly (record_key, transaction_date, payload) VALUES (?, ?, ?)",
            [f"ie_{month.isoformat()}", month.isoformat(), json.dumps(payload)],
        )


def _patch_return_and_volatility(annual_return: float, annual_volatility_pct: float):
    """Patch suggested_return_basis -> annual_return (decimal) and
    calculate_portfolio_metrics -> {"volatility_annual": annual_volatility_pct}
    (a PERCENT, e.g. 17.9 for 17.9% — forecast_levers divides by 100, the
    SAME convention GET /analytics/projection/defaults uses)."""
    return (
        patch(
            "src.financial_analysis.projection_defaults.suggested_return_basis",
            return_value=annual_return,
        ),
        patch(
            "src.financial_analysis.metrics.calculate_portfolio_metrics",
            return_value={"volatility_annual": annual_volatility_pct},
        ),
    )


# ── Base case: fields present, derived (not hardcoded) ──────────────────────

def test_base_case_fields_present_and_derived():
    conn = _make_db()
    _seed_net_worth(conn, 3_269_850.0)
    _seed_run_rate(conn, 30_670.0)

    p_return, p_vol = _patch_return_and_volatility(0.108, 17.9)
    with p_return, p_vol:
        result = compute_levers(conn)

    base = result["base"]
    for key in (
        "current_nw", "expected_return", "volatility", "median_return",
        "monthly_contribution", "target", "years_to_target",
    ):
        assert key in base, f"base missing key {key}"

    # expected_return must equal suggested_return_basis's own return value
    # for this DB — proving it is read live, not hardcoded.
    assert base["expected_return"] == pytest.approx(0.108)
    assert base["volatility"] == pytest.approx(0.179)
    assert base["current_nw"] == pytest.approx(3_269_850.0)
    assert base["monthly_contribution"] == pytest.approx(30_670.0, abs=1.0)
    assert base["years_to_target"] is not None
    assert base["median_return"] is not None
    assert base["median_return"] < base["expected_return"], "median must be drag-adjusted below arithmetic mean"

    assert set(result["levers"].keys()) == {"savings", "return", "volatility"}
    assert len(result["levers"]["savings"]) == 3
    assert len(result["levers"]["return"]) == 2
    assert len(result["levers"]["volatility"]) == 2
    assert "years_to_target" in result["combined"]
    assert "delta_years" in result["combined"]


# ── Anti-hardcoding: two different fixture DBs -> different years_to_target ──

def test_years_to_target_differs_across_two_different_fixture_dbs():
    """Regression-proof for §4b: if someone freezes years_to_target to a
    constant, this test fails. Two DBs with different seeded net worth AND
    contribution run-rate (the two genuinely DB-derived inputs) must produce
    different years_to_target under the SAME (patched) return/volatility."""
    conn_a = _make_db()
    _seed_net_worth(conn_a, 1_000_000.0)
    _seed_run_rate(conn_a, 5_000.0)

    conn_b = _make_db()
    _seed_net_worth(conn_b, 5_000_000.0)
    _seed_run_rate(conn_b, 40_000.0)

    p_return, p_vol = _patch_return_and_volatility(0.10, 15.0)
    with p_return, p_vol:
        result_a = compute_levers(conn_a)
    p_return, p_vol = _patch_return_and_volatility(0.10, 15.0)
    with p_return, p_vol:
        result_b = compute_levers(conn_b)

    assert result_a["base"]["current_nw"] != result_b["base"]["current_nw"]
    assert result_a["base"]["monthly_contribution"] != result_b["base"]["monthly_contribution"]
    assert result_a["base"]["years_to_target"] is not None
    assert result_b["base"]["years_to_target"] is not None
    assert result_a["base"]["years_to_target"] != result_b["base"]["years_to_target"], (
        "years_to_target must differ between two DBs with different NW/contribution — "
        "identical output here means the value is frozen/hardcoded, not derived"
    )
    # The richer DB (higher NW, higher contribution) must reach goal sooner.
    assert result_b["base"]["years_to_target"] < result_a["base"]["years_to_target"]


# ── Savings lever: higher contribution -> strictly smaller years_to_target ──

def test_savings_lever_monotonically_decreases_years_to_target():
    conn = _make_db()
    _seed_net_worth(conn, 3_000_000.0)
    _seed_run_rate(conn, 40_000.0)

    p_return, p_vol = _patch_return_and_volatility(0.10, 16.0)
    with p_return, p_vol:
        result = compute_levers(conn)

    base_years = result["base"]["years_to_target"]
    savings = result["levers"]["savings"]
    assert base_years is not None

    prev_years = base_years
    prev_pm = result["base"]["monthly_contribution"]
    for row in savings:
        assert row["monthly_contribution"] > prev_pm, "each savings step must be a larger contribution"
        assert row["years_to_target"] is not None
        assert row["years_to_target"] < prev_years, (
            f"a higher monthly_contribution ({row['monthly_contribution']}) must yield a "
            f"strictly smaller years_to_target than the previous step ({prev_years}), "
            f"got {row['years_to_target']}"
        )
        assert row["delta_years"] < 0, "delta_years must be negative when the lever gets you there sooner"
        prev_years = row["years_to_target"]
        prev_pm = row["monthly_contribution"]


# ── Volatility lever: lower volatility -> strictly smaller years_to_target ──

def test_volatility_lever_lower_volatility_strictly_decreases_years_to_target():
    conn = _make_db()
    _seed_net_worth(conn, 3_000_000.0)
    _seed_run_rate(conn, 40_000.0)

    p_return, p_vol = _patch_return_and_volatility(0.10, 25.0)  # high vol so -5pp/-8pp stay well above the floor
    with p_return, p_vol:
        result = compute_levers(conn)

    base_years = result["base"]["years_to_target"]
    vol_rows = result["levers"]["volatility"]
    assert base_years is not None

    prev_years = base_years
    prev_vol = result["base"]["volatility"]
    for row in vol_rows:
        assert row["volatility"] < prev_vol, "each volatility step must be strictly lower"
        assert row["years_to_target"] is not None
        assert row["years_to_target"] < prev_years, (
            "lower volatility (less drag) must yield a strictly smaller years_to_target "
            f"than the previous step ({prev_years}), got {row['years_to_target']}"
        )
        assert row["delta_years"] < 0
        prev_years = row["years_to_target"]
        prev_vol = row["volatility"]


def test_volatility_lever_never_floors_at_or_below_zero():
    """-8pp on a low base volatility (e.g. 6%) must clamp above zero, never
    hit or cross it (which would make median_return ill-defined-adjacent)."""
    conn = _make_db()
    _seed_net_worth(conn, 3_000_000.0)
    _seed_run_rate(conn, 40_000.0)

    p_return, p_vol = _patch_return_and_volatility(0.10, 6.0)  # 6% vol; -8pp would go negative unfloored
    with p_return, p_vol:
        result = compute_levers(conn)

    for row in result["levers"]["volatility"]:
        assert row["volatility"] > 0.0


# ── Unreachable goal: null, not an exception, callable stays HTTP-200-safe ──

def test_unreachable_goal_returns_null_years_not_exception():
    """Tiny NW, zero contribution, ~flat/negative median return -> the
    60-year solver horizon in months_to_target must report None (never
    fabricate a number, never raise)."""
    conn = _make_db()
    _seed_net_worth(conn, 100.0)
    # No income_expense_monthly rows seeded -> run-rate status is
    # "no contribution data available" -> monthly_contribution falls back to 0.0.

    p_return, p_vol = _patch_return_and_volatility(0.0, 15.0)  # median_return(0, 0.15) is slightly negative
    with p_return, p_vol:
        result = compute_levers(conn)  # must not raise

    assert result["base"]["monthly_contribution"] == 0.0
    assert result["base"]["years_to_target"] is None
    for lever_group in result["levers"].values():
        for row in lever_group:
            # Every lever step, when even the base can't reach the target with
            # ~zero contribution and ~flat growth, must also report null —
            # never a fabricated number.
            assert row["years_to_target"] is None, (
                f"expected an unreachable target to yield None, got {row}"
            )
            assert row["delta_years"] is None
    assert result["combined"]["years_to_target"] is None
    assert result["combined"]["delta_years"] is None


# ── goal resolver wiring (W-1): compute_levers must go through the single
#    resolver, not read target_net_worth_cny directly ──────────────────────

def test_compute_levers_exposes_goal_key():
    """The 'goal' key must be the full resolver dict, so the UI can render
    the goal name / prompt when source == 'config_fallback'."""
    conn = _make_db()
    _seed_net_worth(conn, 3_000_000.0)
    _seed_run_rate(conn, 20_000.0)

    p_return, p_vol = _patch_return_and_volatility(0.10, 15.0)
    with p_return, p_vol:
        result = compute_levers(conn)  # empty goals table -> config fallback

    assert "goal" in result
    goal = result["goal"]
    for key in ("target_amount", "source", "goal_id", "name", "target_date", "fallback_reason"):
        assert key in goal, f"goal missing key {key}"
    assert goal["source"] == "config_fallback"
    assert goal["fallback_reason"] == "no active retirement goal"
    assert result["base"]["target"] == pytest.approx(goal["target_amount"])


def test_compute_levers_target_follows_goals_table_not_config():
    """Editing the FIRE goal must move base['target'] — proves compute_levers
    goes through the resolver rather than load_verification_config directly."""
    conn = _make_db()
    _seed_net_worth(conn, 3_000_000.0)
    _seed_run_rate(conn, 20_000.0)
    conn.execute(
        "INSERT INTO goals (name, target_amount, target_date, goal_type, status) VALUES (?, ?, ?, ?, ?)",
        ["FIRE", 27_500_000.0, "2041-01-01", "retirement", "active"],
    )

    p_return, p_vol = _patch_return_and_volatility(0.10, 15.0)
    with p_return, p_vol:
        result = compute_levers(conn)

    assert result["base"]["target"] == pytest.approx(27_500_000.0)
    assert result["goal"]["source"] == "goals"
    assert result["goal"]["name"] == "FIRE"


# ── W-3: crossing_years (analytic crossing-time percentiles) ────────────────

def test_base_exposes_crossing_years_ascending():
    conn = _make_db()
    _seed_net_worth(conn, 3_269_850.0)
    _seed_run_rate(conn, 30_670.0)

    p_return, p_vol = _patch_return_and_volatility(0.108, 17.9)
    with p_return, p_vol:
        result = compute_levers(conn)

    crossing = result["base"]["crossing_years"]
    for key in ("p25", "p50", "p75"):
        assert key in crossing
    assert crossing["p25"] < crossing["p50"] < crossing["p75"], (
        "p25 < p50 < p75 must hold WITHOUT any frontend inversion — this is "
        "the whole point of W-3 replacing the ADR-026 ordering-trap approximation"
    )
    # p50 must track years_to_target (same median-drift crossing, see
    # projection_defaults.crossing_time_percentiles docstring).
    assert crossing["p50"] == pytest.approx(result["base"]["years_to_target"], abs=0.05)


def test_crossing_years_all_none_when_volatility_unavailable():
    conn = _make_db()
    _seed_net_worth(conn, 3_000_000.0)
    _seed_run_rate(conn, 20_000.0)

    with patch(
        "src.financial_analysis.projection_defaults.suggested_return_basis", return_value=0.10
    ), patch(
        "src.financial_analysis.metrics.calculate_portfolio_metrics", return_value={}
    ):
        result = compute_levers(conn)

    assert result["base"]["volatility"] is None
    assert result["base"]["crossing_years"] == {"p25": None, "p50": None, "p75": None}


# ── W-2: optional slider params ──────────────────────────────────────────────

def test_no_slider_params_matches_pre_w2_response():
    """The hard backward-compat requirement: calling compute_levers with no
    slider kwargs must be identical to calling it with all three explicitly
    None — i.e. supplying the params machinery at all must not perturb the
    response when nothing is actually requested. No 'applied' key at all."""
    conn = _make_db()
    _seed_net_worth(conn, 3_269_850.0)
    _seed_run_rate(conn, 30_670.0)

    p_return, p_vol = _patch_return_and_volatility(0.108, 17.9)
    with p_return, p_vol:
        result_default = compute_levers(conn)
    p_return, p_vol = _patch_return_and_volatility(0.108, 17.9)
    with p_return, p_vol:
        result_explicit_none = compute_levers(
            conn, savings_pct=None, return_pp=None, volatility_pp=None
        )

    assert result_default == result_explicit_none
    assert "applied" not in result_default
    assert len(result_default["levers"]["savings"]) == 3
    assert len(result_default["levers"]["return"]) == 2
    assert len(result_default["levers"]["volatility"]) == 2


def test_savings_pct_param_adds_one_row_and_echoes_applied():
    conn = _make_db()
    _seed_net_worth(conn, 3_000_000.0)
    _seed_run_rate(conn, 40_000.0)

    p_return, p_vol = _patch_return_and_volatility(0.10, 16.0)
    with p_return, p_vol:
        result = compute_levers(conn, savings_pct=15.0)

    assert len(result["levers"]["savings"]) == 4  # 3 presets + 1 slider row
    assert result["applied"] == {"savings_pct": 15.0, "return_pp": None, "volatility_pp": None}
    new_row = result["levers"]["savings"][-1]
    expected_pm = round(result["base"]["monthly_contribution"] * 1.15, 2)
    assert new_row["monthly_contribution"] == pytest.approx(expected_pm)
    assert new_row["years_to_target"] is not None
    # savings_pct doesn't touch return/volatility levers.
    assert len(result["levers"]["return"]) == 2
    assert len(result["levers"]["volatility"]) == 2


def test_return_pp_param_adds_one_row_and_echoes_applied():
    conn = _make_db()
    _seed_net_worth(conn, 3_000_000.0)
    _seed_run_rate(conn, 40_000.0)

    p_return, p_vol = _patch_return_and_volatility(0.10, 16.0)
    with p_return, p_vol:
        result = compute_levers(conn, return_pp=3.5)

    assert len(result["levers"]["return"]) == 3
    assert result["applied"] == {"savings_pct": None, "return_pp": 3.5, "volatility_pp": None}
    new_row = result["levers"]["return"][-1]
    assert new_row["expected_return"] == pytest.approx(0.10 + 0.035)


def test_volatility_pp_param_adds_one_row_floored_above_zero():
    conn = _make_db()
    _seed_net_worth(conn, 3_000_000.0)
    _seed_run_rate(conn, 40_000.0)

    p_return, p_vol = _patch_return_and_volatility(0.10, 6.0)  # low vol; -10pp would go negative unfloored
    with p_return, p_vol:
        result = compute_levers(conn, volatility_pp=10.0)

    new_row = result["levers"]["volatility"][-1]
    assert new_row["volatility"] > 0.0
    assert result["applied"]["volatility_pp"] == 10.0


def test_slider_params_are_clamped_to_range_not_rejected():
    """compute_levers itself defensively clamps (belt-and-suspenders on top
    of the route's Query(ge=,le=) 422 validation) — out-of-range values here
    must clamp, not raise, and the echoed applied value must show the
    CLAMPED value actually used."""
    conn = _make_db()
    _seed_net_worth(conn, 3_000_000.0)
    _seed_run_rate(conn, 40_000.0)

    p_return, p_vol = _patch_return_and_volatility(0.10, 16.0)
    with p_return, p_vol:
        result = compute_levers(conn, savings_pct=999.0, return_pp=-5.0, volatility_pp=999.0)

    assert result["applied"]["savings_pct"] == 60.0  # clamped to _SAVINGS_PCT_RANGE max
    assert result["applied"]["return_pp"] == 0.0  # clamped to _RETURN_PP_RANGE min
    assert result["applied"]["volatility_pp"] == 10.0  # clamped to _VOLATILITY_PP_RANGE max


def test_combined_uses_joint_slider_position_when_supplied():
    """combined must move off the first-preset default for whichever lever
    got a slider param, while the untouched lever(s) keep using the
    existing first-preset step."""
    conn = _make_db()
    _seed_net_worth(conn, 3_000_000.0)
    _seed_run_rate(conn, 40_000.0)

    p_return, p_vol = _patch_return_and_volatility(0.10, 16.0)
    with p_return, p_vol:
        default_result = compute_levers(conn)
    p_return, p_vol = _patch_return_and_volatility(0.10, 16.0)
    with p_return, p_vol:
        slider_result = compute_levers(conn, savings_pct=50.0)  # matches the 2nd preset step's magnitude

    # savings_pct=50 pushes combined to use a bigger contribution than the
    # default (first preset = +25%) -> combined must reach the goal sooner
    # or equal, never later, than the default combined.
    assert slider_result["combined"]["years_to_target"] is not None
    assert default_result["combined"]["years_to_target"] is not None
    assert slider_result["combined"]["years_to_target"] <= default_result["combined"]["years_to_target"]
    assert "+50%" in slider_result["combined"]["label"]


def test_compute_levers_never_reads_config_target_directly():
    """Patching load_verification_config's target must NOT move base['target']
    once a real goals-table row exists — that would mean compute_levers is
    still reading the config value, not the resolver's goals-sourced result."""
    conn = _make_db()
    _seed_net_worth(conn, 3_000_000.0)
    _seed_run_rate(conn, 20_000.0)
    conn.execute(
        "INSERT INTO goals (name, target_amount, target_date, goal_type, status) VALUES (?, ?, ?, ?, ?)",
        ["FIRE", 27_500_000.0, "2041-01-01", "retirement", "active"],
    )

    fake_cfg = VerificationConfig(north_star=NorthStarSection(target_net_worth_cny=99_000_000.0))
    p_return, p_vol = _patch_return_and_volatility(0.10, 15.0)
    with p_return, p_vol, patch(
        "src.services.goal_resolver.load_verification_config", return_value=fake_cfg
    ):
        result = compute_levers(conn)

    assert result["base"]["target"] == pytest.approx(27_500_000.0)
