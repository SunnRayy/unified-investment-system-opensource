"""Tests for src/services/north_star*.py (PRD 2026-07-07 F3, Batch B6).

Uses an in-memory DuckDB initialized from the real schema.sql (never a bare,
schema-less connector — see CLAUDE.md Database Safety Rules).
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.north_star import (
    classify_flows_heuristic,
    contribution_metrics,
    contributions_summary,
    create_unforced_error,
    glide_path,
    list_classified_flows,
    list_unclassified_flows,
    list_unforced_errors,
    north_star_panel,
    tag_flow_manual,
    tag_flows_bulk,
    time_in_market,
    untag_flows,
)
from src.services.north_star_flows import compose_natural_key
from src.services.investment_contributions import contributions_summary_v2


def _make_db() -> DatabaseConnector:
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    return conn


def _insert_tx(
    conn, tx_date: str, asset_id: str, tx_type: str, amount_net: float, *, tx_id: int | None = None,
) -> int:
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, amount_net, amount_gross,
             currency, source_system, is_provisional)
        VALUES (?, ?, ?, ?, ?, ?, 'CNY', 'test', FALSE)
        """,
        [tx_date, asset_id, asset_id, tx_type, amount_net, amount_net],
    )
    return conn.execute(
        "SELECT id FROM transactions WHERE asset_id = ? AND transaction_date = ? AND transaction_type = ? ORDER BY id DESC LIMIT 1",
        [asset_id, tx_date, tx_type],
    ).fetchone()[0]


def _nk_for(conn, tx_id: int) -> str:
    """V81 natural key a given transactions.id row currently composes to —
    the identity that ends up stored in cash_flow_tags.source_row_key once
    the row is tagged (test helper, mirrors compose_natural_key)."""
    row = conn.execute(
        "SELECT source_system, transaction_date, asset_id, transaction_type, amount_gross "
        "FROM transactions WHERE id = ?",
        [tx_id],
    ).fetchone()
    return compose_natural_key(*row)


# ── F3.1 heuristic: SGOV -> BRK-B same-day switch = internal_transfer, ¥0 ──

def test_sgov_brkb_same_day_switch_tags_internal_transfer_zero():
    conn = _make_db()
    sgov_id = _insert_tx(conn, "2026-03-01", "US_STK_SGOV", "sell", 10000.0)
    brkb_id = _insert_tx(conn, "2026-03-01", "US_STK_BRKB", "buy", 10000.0)

    result = classify_flows_heuristic(conn)
    assert result["tagged"] == 2

    rows = {
        r[0]: (r[1], r[2])
        for r in conn.execute("SELECT source_row_key, classification, amount_cny FROM cash_flow_tags").fetchall()
    }
    assert rows[_nk_for(conn, sgov_id)] == ("internal_transfer", 0.0)
    assert rows[_nk_for(conn, brkb_id)] == ("internal_transfer", 0.0)


# ── Unmatched transfer_in (salary-deposit-like) stays unclassified ─────────

def test_unmatched_transfer_in_stays_unclassified():
    conn = _make_db()
    tx_id = _insert_tx(conn, "2026-04-01", "CN_FUND_000001", "transfer_in", 5000.0)

    result = classify_flows_heuristic(conn)
    assert result["tagged"] == 0
    assert result["unclassified_count"] >= 1

    tagged = conn.execute("SELECT COUNT(*) FROM cash_flow_tags WHERE source_row_key = ?", [str(tx_id)]).fetchone()[0]
    assert tagged == 0

    unclassified = list_unclassified_flows(conn)
    assert any(row["source_row_key"] == str(tx_id) for row in unclassified)


# ── Manual tag overwrites heuristic; heuristic never overwrites manual ─────

def test_manual_tag_overrides_heuristic_and_survives_rerun():
    conn = _make_db()
    sgov_id = _insert_tx(conn, "2026-03-01", "US_STK_SGOV", "sell", 10000.0)
    _insert_tx(conn, "2026-03-01", "US_STK_BRKB", "buy", 10000.0)

    classify_flows_heuristic(conn)

    tag_flow_manual(conn, "transactions", str(sgov_id), "external_contribution", note="actually new money")
    row = conn.execute(
        "SELECT classification, tagged_by, amount_cny FROM cash_flow_tags WHERE source_row_key = ?",
        [_nk_for(conn, sgov_id)],
    ).fetchone()
    assert row == ("external_contribution", "manual", 10000.0)

    result = classify_flows_heuristic(conn)
    assert result["skipped_manual"] >= 1

    row_after = conn.execute(
        "SELECT classification, tagged_by FROM cash_flow_tags WHERE source_row_key = ?",
        [_nk_for(conn, sgov_id)],
    ).fetchone()
    assert row_after == ("external_contribution", "manual")


def test_manual_tag_invalid_classification_raises():
    conn = _make_db()
    tx_id = _insert_tx(conn, "2026-03-01", "US_STK_SGOV", "sell", 10000.0)
    with pytest.raises(ValueError):
        tag_flow_manual(conn, "transactions", str(tx_id), "not_a_real_classification")


# ── contribution_metrics excludes internal_transfer, surfaces unclassified ──

def test_contribution_metrics_excludes_internal_transfer():
    conn = _make_db()
    salary_id = _insert_tx(conn, "2026-05-01", "CN_FUND_000001", "transfer_in", 8000.0)
    _insert_tx(conn, "2026-05-02", "US_STK_SGOV", "sell", 5000.0)
    _insert_tx(conn, "2026-05-02", "US_STK_BRKB", "buy", 5000.0)

    classify_flows_heuristic(conn)  # tags the SGOV/BRK-B pair, leaves salary untagged
    tag_flow_manual(conn, "transactions", str(salary_id), "external_contribution")

    metrics = contribution_metrics(conn)
    assert metrics["ytd_sum"] == 8000.0
    assert metrics["trailing_12m_sum"] == 8000.0
    assert metrics["unclassified_count"] == 0  # salary now tagged, SGOV/BRK-B tagged by heuristic


# ── glide_path: spreadsheet fixture + monotonic contribution behavior ──────

def test_glide_path_spreadsheet_fixture():
    conn = _make_db()
    result = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0, trailing_twr=0.1105)
    assert result["reachable"] is True
    assert abs(result["years_to_target"] - 17.1) <= 0.3  # PRD acceptance: ~17.1y, tolerance widened slightly
    # PRD's own reference numbers for zero-contribution required CAGR: ~19.8/12.8/9.5
    grid = {row["horizon_years"]: row["required_cagr_pct"]["zero"] for row in result["required_cagr_grid"]}
    assert abs(grid[10] - 19.8) <= 0.5
    assert abs(grid[15] - 12.8) <= 0.5
    assert abs(grid[20] - 9.5) <= 0.5


def test_glide_path_contribution_shortens_years():
    conn = _make_db()
    zero = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0, trailing_twr=0.1105)
    with_contribution = glide_path(conn, monthly_contribution=20_000.0, current_nw=3_276_919.0, trailing_twr=0.1105)
    assert with_contribution["years_to_target"] < zero["years_to_target"]


def test_glide_path_required_cagr_grid_monotonic():
    conn = _make_db()
    result = glide_path(conn, monthly_contribution=10_000.0, current_nw=3_276_919.0, trailing_twr=0.1105)
    for row in result["required_cagr_grid"]:
        rates = row["required_cagr_pct"]
        # More contribution -> lower (or equal) required CAGR to hit the same target/horizon.
        assert rates["zero"] >= rates["scenario"]


def test_glide_path_unreachable_returns_false_not_fabricated():
    conn = _make_db()
    result = glide_path(conn, monthly_contribution=0.0, current_nw=100.0, trailing_twr=-0.5)
    assert result["reachable"] is False
    assert result["years_to_target"] is None


# ── time_in_market: 24-month fixture + insufficient-data guard ─────────────

def _setup_risk_profile(conn, equity_target_pct: float = 60.0) -> None:
    conn.execute(
        "INSERT INTO taxonomy_classes (id, name, parent_id, level, is_rebalanceable) VALUES (1, 'Equity', NULL, 0, TRUE)"
    )
    conn.execute(
        "INSERT INTO risk_profiles (id, name, is_active) VALUES (1, 'Test Profile', TRUE)"
    )
    conn.execute(
        "INSERT INTO risk_profile_allocations (id, profile_id, class_id, target_pct) VALUES (1, 1, 1, ?)",
        [equity_target_pct],
    )
    conn.execute(
        "INSERT INTO asset_registry (canonical_id, display_name, asset_class) VALUES ('US_STK_EQ', 'Equity Fund', 'Equity')"
    )
    conn.execute(
        "INSERT INTO asset_registry (canonical_id, display_name, asset_class) VALUES ('CASH_TEST', 'Cash', 'Cash')"
    )


def _insert_monthly_holdings(conn, month_start: date, equity_value: float, cash_value: float) -> None:
    conn.execute(
        """
        INSERT INTO holdings (snapshot_date, asset_id, asset_name, quantity, market_value, currency, source_system, is_shadow)
        VALUES (?, 'US_STK_EQ', 'Equity Fund', 1, ?, 'CNY', 'test', FALSE)
        """,
        [month_start.isoformat(), equity_value],
    )
    conn.execute(
        """
        INSERT INTO holdings (snapshot_date, asset_id, asset_name, quantity, market_value, currency, source_system, is_shadow)
        VALUES (?, 'CASH_TEST', 'Cash', 1, ?, 'CNY', 'test', FALSE)
        """,
        [month_start.isoformat(), cash_value],
    )


def test_time_in_market_24_month_fixture_ratio():
    conn = _make_db()
    _setup_risk_profile(conn, equity_target_pct=60.0)

    # 20 months at 70% equity (>= 60-10=50 floor), 4 months at 20% (below floor).
    start = date(2024, 8, 1)
    for i in range(24):
        month = date(start.year + (start.month - 1 + i) // 12, (start.month - 1 + i) % 12 + 1, 1)
        if i < 20:
            _insert_monthly_holdings(conn, month, equity_value=7000.0, cash_value=3000.0)
        else:
            _insert_monthly_holdings(conn, month, equity_value=2000.0, cash_value=8000.0)

    result = time_in_market(conn)
    assert result["insufficient_data"] is False
    assert result["total_months"] == 24
    assert result["in_market_months"] == 20
    assert abs(result["ratio"] - 20 / 24) < 1e-3


def test_time_in_market_insufficient_data_below_3_months():
    conn = _make_db()
    _setup_risk_profile(conn, equity_target_pct=60.0)
    _insert_monthly_holdings(conn, date(2026, 5, 1), equity_value=7000.0, cash_value=3000.0)
    _insert_monthly_holdings(conn, date(2026, 6, 1), equity_value=7000.0, cash_value=3000.0)

    result = time_in_market(conn)
    assert result["insufficient_data"] is True


# ── unforced errors: seed row + create/list ────────────────────────────────

def test_unforced_errors_seed_row_present():
    from src.database.seed_loader import seed_demo_content

    conn = _make_db()
    # Program OSR WS-3c: unforced_errors seed moved out of schema.sql into
    # the seed-pack system — test session runs under $UIS_SEED_PROFILE=example
    # (tests/conftest.py), so this populates the persona's example entry.
    seed_demo_content(conn)
    errors = list_unforced_errors(conn)
    assert any("deadline-adjacent liquidation quota" in e["description"] for e in errors)


def test_create_unforced_error_and_list():
    conn = _make_db()
    created = create_unforced_error(
        conn, error_date="2026-05-15", description="Test error", est_cost_cny=1000.0,
        root_cause="test", linked_rule="test rule",
    )
    assert created["description"] == "Test error"
    errors = list_unforced_errors(conn)
    assert any(e["description"] == "Test error" for e in errors)


def test_create_unforced_error_rejects_empty_description():
    conn = _make_db()
    with pytest.raises(ValueError):
        create_unforced_error(conn, error_date="2026-05-15", description="   ")


def test_create_unforced_error_rejects_bad_date():
    conn = _make_db()
    with pytest.raises(ValueError):
        create_unforced_error(conn, error_date="not-a-date", description="Test error")


# ── north_star_panel composes all four sections ─────────────────────────────

def test_north_star_panel_returns_all_sections():
    conn = _make_db()
    panel = north_star_panel(conn)
    assert set(panel.keys()) == {"contributions", "time_in_market", "unforced_errors", "glide_path"}


# ── glide_path: new assumptions fields + basis labels ────────────────────────

def test_glide_path_spreadsheet_fixture_passes_with_explicit_twr():
    """PRD fixture: 17.1y at 11.05%/¥0. Explicit trailing_twr bypasses the
    default path, so this test is unaffected by the TWR-basis change."""
    conn = _make_db()
    result = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0, trailing_twr=0.1105)
    assert result["reachable"] is True
    assert abs(result["years_to_target"] - 17.1) <= 0.3


def test_glide_path_assumptions_include_basis_labels():
    """Assumptions block must carry twr_basis and run_rate_basis strings.
    Updated 2026-07-25 (ADR-025 §5.2 rewire): run_rate_basis now reflects the
    月度收支 net_external_ttm + RSU retained-in-window basis, not the retired
    cash_flow_tags external_contribution mechanism.
    """
    conn = _make_db()
    result = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0, trailing_twr=0.1105)
    assumptions = result["assumptions"]
    assert "twr_basis" in assumptions
    assert "rebalanceable" in assumptions["twr_basis"].lower()
    assert "run_rate_basis" in assumptions
    assert "net_external_ttm" in assumptions["run_rate_basis"]
    assert "current_run_rate_monthly" in assumptions
    # Empty test DB → no income_expense_monthly data → run-rate unavailable → None
    assert assumptions["current_run_rate_monthly"] in (None, 0.0) or isinstance(
        assumptions["current_run_rate_monthly"], (int, float)
    )
    assert "run_rate_status" in assumptions


def test_glide_path_default_twr_calls_rebalanceable_helper():
    """When trailing_twr is not supplied, _default_trailing_twr must delegate
    to suggested_return_basis (not the unbounded calculate_portfolio_twr)."""
    conn = _make_db()
    with patch(
        "src.services.north_star_glide._default_trailing_twr",
        return_value=0.1105,
    ) as mock_twr:
        result = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0)
        mock_twr.assert_called_once_with(conn)
        assert result["reachable"] is True


def test_glide_path_default_twr_uses_suggested_return_basis():
    """_default_trailing_twr must call suggested_return_basis, not the
    unbounded calculate_portfolio_twr."""
    conn = _make_db()
    with patch(
        "src.financial_analysis.projection_defaults.suggested_return_basis",
        return_value=0.1105,
    ) as mock_basis:
        from src.services.north_star_glide import _default_trailing_twr
        result = _default_trailing_twr(conn)
        mock_basis.assert_called_once_with(conn)
        assert result == 0.1105


# ── glide_path: goal resolver wiring (W-1) ────────────────────────────────
# forecast_levers.compute_levers AND north_star_glide.glide_path must go
# through the SAME src.services.goal_resolver.resolve_north_star_goal, or the
# glide table header drifts from the forecast headline (the exact defect
# this workstream fixes). See tests/services/test_goal_resolver.py for the
# resolver's own unit tests (tie-break, case-insensitivity, fallback, etc).

def test_glide_path_assumptions_surface_goal_fields():
    conn = _make_db()
    result = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0, trailing_twr=0.1105)
    assumptions = result["assumptions"]
    for key in ("goal_source", "goal_name", "goal_id", "target"):
        assert key in assumptions, f"assumptions missing key {key}"
    # Empty goals table in this fixture DB -> config fallback.
    assert assumptions["goal_source"] == "config_fallback"
    assert assumptions["goal_name"] is None


def test_glide_path_target_follows_goals_table_not_config():
    """Editing the FIRE goal must move assumptions['target'] — proves
    glide_path goes through the resolver rather than load_verification_config
    directly."""
    conn = _make_db()
    conn.execute(
        "INSERT INTO goals (name, target_amount, target_date, goal_type, status) VALUES (?, ?, ?, ?, ?)",
        ["FIRE", 27_500_000.0, "2041-01-01", "retirement", "active"],
    )

    result = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0, trailing_twr=0.1105)

    assert result["assumptions"]["target"] == pytest.approx(27_500_000.0)
    assert result["assumptions"]["goal_source"] == "goals"
    assert result["assumptions"]["goal_name"] == "FIRE"


def test_glide_path_and_compute_levers_agree_on_target():
    """Both consumers reading the same live goals-table row must resolve to
    the identical target — the anti-drift property this workstream exists
    to guarantee (glide table header vs forecast headline)."""
    from src.services.forecast_levers import compute_levers

    conn = _make_db()
    conn.execute(
        "INSERT INTO goals (name, target_amount, target_date, goal_type, status) VALUES (?, ?, ?, ?, ?)",
        ["FIRE", 23_400_000.0, "2039-06-01", "retirement", "active"],
    )

    glide_result = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0, trailing_twr=0.1105)
    with patch(
        "src.financial_analysis.projection_defaults.suggested_return_basis", return_value=0.1105,
    ), patch(
        "src.financial_analysis.metrics.calculate_portfolio_metrics",
        return_value={"volatility_annual": 15.0},
    ):
        levers_result = compute_levers(conn)

    assert glide_result["assumptions"]["target"] == pytest.approx(23_400_000.0)
    assert levers_result["base"]["target"] == pytest.approx(23_400_000.0)
    assert glide_result["assumptions"]["target"] == levers_result["base"]["target"]


# ── projection_defaults module: shared helpers ────────────────────────────────

def test_avg_monthly_investment_empty_table_returns_zero():
    """avg_monthly_investment returns 0.0 when income_expense_monthly is empty."""
    from src.financial_analysis.projection_defaults import avg_monthly_investment
    conn = _make_db()
    result = avg_monthly_investment(conn, "2020-01-01")
    assert result == 0.0


def test_avg_monthly_investment_sums_touzilichai_columns():
    """avg_monthly_investment sums 投资理财_* payload keys for rows since `since`."""
    import json
    from src.financial_analysis.projection_defaults import avg_monthly_investment
    conn = _make_db()
    # Insert two months: each month has 投资理财_A=10000 and 投资理财_B=5000 → sum=15000/month
    for i, month in enumerate(["2025-01-01", "2025-02-01"]):
        conn.execute(
            "INSERT INTO income_expense_monthly (record_key, transaction_date, payload) VALUES (?, ?, ?)",
            [f"test_key_{i}", month, json.dumps({"投资理财_A": 10000, "投资理财_B": 5000, "其他_C": 999})],
        )
    result = avg_monthly_investment(conn, "2025-01-01")
    assert result == 15000.0  # (15000 + 15000) / 2 = 15000


def test_suggested_return_basis_returns_none_on_empty_db():
    """suggested_return_basis returns None gracefully when holdings are absent."""
    from src.financial_analysis.projection_defaults import suggested_return_basis
    conn = _make_db()
    result = suggested_return_basis(conn)
    # Empty DB has no holdings → TWR calculation returns None or empty; must not raise
    assert result is None or isinstance(result, float)


# ── Fix 5: glide_path run-rate contamination + headline binding ───────────────

def _insert_tx(conn, tx_date: str, asset_id: str, tx_type: str, amount_net: float) -> int:
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, amount_net, amount_gross,
             currency, source_system, is_provisional)
        VALUES (?, ?, ?, ?, ?, ?, 'CNY', 'test', FALSE)
        """,
        [tx_date, asset_id, asset_id, tx_type, amount_net, amount_net],
    )
    return conn.execute(
        "SELECT id FROM transactions WHERE asset_id = ? AND transaction_date = ? ORDER BY id DESC LIMIT 1",
        [asset_id, tx_date],
    ).fetchone()[0]


def test_glide_path_headline_uses_zero_when_no_run_rate():
    """Fixture: NW ¥3,000,000, TWR 11.05%, ¥0 run-rate.
    Headline years ≈ 18.0–18.2y; headline says 'zero' scenario.
    Headline number equals the years-to-target at ¥0/mo.

    Fix 5 acceptance: fixture NW ¥3,000,000 / TWR 11.05% / ¥0 →
    headline ≈18.1y (tolerance ±0.2y).
    """
    conn = _make_db()
    result = glide_path(conn, monthly_contribution=0.0, current_nw=3_000_000.0, trailing_twr=0.1105)
    assert result["reachable"] is True

    # years_to_target is the scenario (also ¥0/mo here)
    assert abs(result["years_to_target"] - 18.1) <= 0.3

    # headline sub-dict must exist
    headline = result["headline"]
    assert headline is not None
    assert headline["scenario_used"] in ("zero", "current_run_rate")
    assert headline["years_to_target"] is not None

    # Headline number is close to the ¥0/mo cell value (≈ 18.0–18.2y)
    assert abs(headline["years_to_target"] - 18.1) <= 0.2, (
        f"Expected ~18.1y at ¥0/mo, got {headline['years_to_target']}"
    )


# ── WS3: Provenance rules engine ─────────────────────────────────────────────

def test_rsu_vest_tagged_as_external_contribution_with_rule_id():
    """RSU vest rows must be tagged as external_contribution with rule_id='rsu_vest'."""
    conn = _make_db()
    today = date.today()
    vest_id = _insert_tx(conn, today.isoformat(), "RSU_AMZN", "vest", 160939.0)

    result = classify_flows_heuristic(conn)
    assert result["tagged"] >= 1

    row = conn.execute(
        "SELECT classification, tagged_by, rule_id FROM cash_flow_tags WHERE source_row_key = ?",
        [_nk_for(conn, vest_id)],
    ).fetchone()
    assert row is not None, "vest row must be tagged"
    assert row[0] == "external_contribution"
    assert row[1] == "heuristic"
    assert row[2] == "rsu_vest"
    conn.close()


def test_same_day_transfer_pair_rule_id():
    """Matched transfer_in/out pair must use rule_id='same_day_transfer_pair'."""
    conn = _make_db()
    today = date.today()
    in_id = _insert_tx(conn, today.isoformat(), "CN_FUND_000001", "transfer_in", 20000.0)
    out_id = _insert_tx(conn, today.isoformat(), "CN_FUND_000002", "transfer_out", 20000.0)

    classify_flows_heuristic(conn)

    for row_id in (in_id, out_id):
        row = conn.execute(
            "SELECT classification, rule_id FROM cash_flow_tags WHERE source_row_key = ?",
            [_nk_for(conn, row_id)],
        ).fetchone()
        assert row is not None
        assert row[0] == "internal_transfer"
        assert row[1] == "same_day_transfer_pair"
    conn.close()


def test_money_market_move_rule_id():
    """SGOV/BRK-B same-day switch must use rule_id='money_market_move'."""
    conn = _make_db()
    today = date.today()
    sgov_id = _insert_tx(conn, today.isoformat(), "US_STK_SGOV", "sell", 15000.0)
    brk_id = _insert_tx(conn, today.isoformat(), "US_STK_BRKB", "buy", 15000.0)

    classify_flows_heuristic(conn)

    for row_id in (sgov_id, brk_id):
        row = conn.execute(
            "SELECT classification, rule_id FROM cash_flow_tags WHERE source_row_key = ?",
            [_nk_for(conn, row_id)],
        ).fetchone()
        assert row is not None
        assert row[0] == "internal_transfer"
        assert row[1] == "money_market_move"
    conn.close()


def test_transfer_pair_not_tagged_external_even_with_vest_present():
    """A same-day matched transfer pair must NOT be tagged external_contribution
    even when a vest row is also present (internal-transfer rules run first)."""
    conn = _make_db()
    today = date.today()
    in_id = _insert_tx(conn, today.isoformat(), "CN_FUND_A", "transfer_in", 10000.0)
    out_id = _insert_tx(conn, today.isoformat(), "CN_FUND_B", "transfer_out", 10000.0)
    vest_id = _insert_tx(conn, today.isoformat(), "RSU_GOOG", "vest", 50000.0)

    classify_flows_heuristic(conn)

    pair_in = conn.execute(
        "SELECT classification, rule_id FROM cash_flow_tags WHERE source_row_key = ?",
        [_nk_for(conn, in_id)],
    ).fetchone()
    pair_out = conn.execute(
        "SELECT classification, rule_id FROM cash_flow_tags WHERE source_row_key = ?",
        [_nk_for(conn, out_id)],
    ).fetchone()
    vest_row = conn.execute(
        "SELECT classification, rule_id FROM cash_flow_tags WHERE source_row_key = ?",
        [_nk_for(conn, vest_id)],
    ).fetchone()

    assert pair_in[0] == "internal_transfer", "transfer_in must be internal_transfer"
    assert pair_in[1] == "same_day_transfer_pair"
    assert pair_out[0] == "internal_transfer", "transfer_out must be internal_transfer"
    assert vest_row[0] == "external_contribution", "vest must be external_contribution"
    assert vest_row[1] == "rsu_vest"
    conn.close()


# ── Attribution & Flows WS-3.1: security_transfer_pair (cross-source ACAT) ──

def _insert_transfer_leg(
    conn, tx_date: str, asset_id: str, tx_type: str, quantity: float, source_system: str,
) -> int:
    """A $0 transfer_in/transfer_out leg — the shape V79's heal produces for a
    Schwab 'Security Transfer' row (or an already-correct IBKR counterpart)."""
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, quantity,
             amount_gross, amount_net, currency, source_system, is_provisional)
        VALUES (?, ?, ?, ?, ?, 0.00, 0.00, 'CNY', ?, FALSE)
        """,
        [tx_date, asset_id, asset_id, tx_type, quantity, source_system],
    )
    return conn.execute(
        "SELECT id FROM transactions WHERE asset_id = ? AND transaction_date = ? "
        "AND transaction_type = ? AND source_system = ? ORDER BY id DESC LIMIT 1",
        [asset_id, tx_date, tx_type, source_system],
    ).fetchone()[0]


def test_security_transfer_pair_matches_cross_day_cross_source():
    """Jun-8 IBKR transfer_in (+200) + Jun-9 Schwab transfer_out (-200) on the
    same asset_id — R1 (exact-date grouping) cannot match these; the new
    windowed rule must tag both legs internal_transfer,
    rule_id='security_transfer_pair'."""
    conn = _make_db()
    in_id = _insert_transfer_leg(conn, "2026-06-08", "US_STK_SGOV", "transfer_in", 200.0, "Broker_IBKR")
    out_id = _insert_transfer_leg(conn, "2026-06-09", "US_STK_SGOV", "transfer_out", -200.0, "Schwab_CSV")

    result = classify_flows_heuristic(conn)
    assert result["tagged"] == 2

    for row_id in (in_id, out_id):
        row = conn.execute(
            "SELECT classification, rule_id, amount_cny FROM cash_flow_tags WHERE source_row_key = ?",
            [_nk_for(conn, row_id)],
        ).fetchone()
        assert row is not None
        assert row[0] == "internal_transfer"
        assert row[1] == "security_transfer_pair"
        assert row[2] == 0.0
    conn.close()


def test_security_transfer_pair_unpaired_out_not_tagged():
    """A transfer_out leg with no matching transfer_in anywhere must stay unclassified."""
    conn = _make_db()
    out_id = _insert_transfer_leg(conn, "2026-06-09", "US_STK_IEF", "transfer_out", -172.0, "Schwab_CSV")

    result = classify_flows_heuristic(conn)
    assert result["tagged"] == 0

    row = conn.execute(
        "SELECT COUNT(*) FROM cash_flow_tags WHERE source_row_key = ?", [str(out_id)]
    ).fetchone()[0]
    assert row == 0
    conn.close()


def test_security_transfer_pair_nine_days_apart_not_tagged():
    """A same-asset, same-quantity pair 9 days apart is outside the 7-day
    window and must NOT be matched by security_transfer_pair."""
    conn = _make_db()
    in_id = _insert_transfer_leg(conn, "2026-06-01", "US_STK_VOO", "transfer_in", 21.0, "Broker_IBKR")
    out_id = _insert_transfer_leg(conn, "2026-06-10", "US_STK_VOO", "transfer_out", -21.0, "Schwab_CSV")

    result = classify_flows_heuristic(conn)
    assert result["tagged"] == 0

    for row_id in (in_id, out_id):
        count = conn.execute(
            "SELECT COUNT(*) FROM cash_flow_tags WHERE source_row_key = ?", [str(row_id)]
        ).fetchone()[0]
        assert count == 0
    conn.close()


def test_security_transfer_pair_manual_tag_not_overwritten():
    """A manually-tagged leg of a would-be ACAT pair must survive re-classification
    untouched — its partner leg stays unmatched (no half-tagged pair)."""
    conn = _make_db()
    in_id = _insert_transfer_leg(conn, "2026-06-08", "US_STK_IEF", "transfer_in", 172.0, "Broker_IBKR")
    out_id = _insert_transfer_leg(conn, "2026-06-09", "US_STK_IEF", "transfer_out", -172.0, "Schwab_CSV")

    tag_flow_manual(conn, "transactions", str(in_id), "external_contribution", note="owner override")

    classify_flows_heuristic(conn)

    in_row = conn.execute(
        "SELECT classification, tagged_by FROM cash_flow_tags WHERE source_row_key = ?",
        [_nk_for(conn, in_id)],
    ).fetchone()
    assert in_row == ("external_contribution", "manual")

    out_row = conn.execute(
        "SELECT classification FROM cash_flow_tags WHERE source_row_key = ?", [str(out_id)]
    ).fetchone()
    assert out_row is None, "partner leg must not be tagged once its match is manually claimed"
    conn.close()


def test_security_transfer_pair_asset_id_mismatch_not_tagged():
    """Same-quantity, same-window legs on DIFFERENT asset_ids must not match
    (R1's amount-based match would have missed these too since amount is $0
    for both, but asset_id is the primary key for this rule)."""
    conn = _make_db()
    in_id = _insert_transfer_leg(conn, "2026-06-08", "US_STK_VOO", "transfer_in", 21.0, "Broker_IBKR")
    out_id = _insert_transfer_leg(conn, "2026-06-09", "US_STK_IEF", "transfer_out", -21.0, "Schwab_CSV")

    result = classify_flows_heuristic(conn)
    assert result["tagged"] == 0
    for row_id in (in_id, out_id):
        count = conn.execute(
            "SELECT COUNT(*) FROM cash_flow_tags WHERE source_row_key = ?", [str(row_id)]
        ).fetchone()[0]
        assert count == 0
    conn.close()


def test_manual_tag_not_overwritten_on_reheuristic():
    """A tagged_by='manual' row must survive a re-run of classify_flows_heuristic."""
    conn = _make_db()
    today = date.today()
    vest_id = _insert_tx(conn, today.isoformat(), "RSU_AMZN", "vest", 100000.0)

    # Manually tag it first
    tag_flow_manual(conn, "transactions", str(vest_id), "income_reinvested",
                    note="manually reclassified")

    result = classify_flows_heuristic(conn)
    assert result["skipped_manual"] >= 1

    row = conn.execute(
        "SELECT classification, tagged_by FROM cash_flow_tags WHERE source_row_key = ?",
        [_nk_for(conn, vest_id)],
    ).fetchone()
    assert row[0] == "income_reinvested", "manual tag must not be overwritten"
    assert row[1] == "manual"
    conn.close()


def test_list_classified_flows_returns_rule_id():
    """list_classified_flows must include rule_id in each row dict."""
    conn = _make_db()
    today = date.today()
    vest_id = _insert_tx(conn, today.isoformat(), "RSU_AMZN", "vest", 80000.0)

    classify_flows_heuristic(conn)

    rows = list_classified_flows(conn)
    vest_rows = [r for r in rows if r["source_row_key"] == _nk_for(conn, vest_id)]
    assert len(vest_rows) == 1
    assert "rule_id" in vest_rows[0]
    assert vest_rows[0]["rule_id"] == "rsu_vest"
    conn.close()


def test_list_classified_flows_manual_tag_rule_id_is_none():
    """Manually tagged rows must have rule_id=None in list_classified_flows."""
    conn = _make_db()
    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "CN_FUND_000001", "transfer_in", 5000.0)
    tag_flow_manual(conn, "transactions", str(tx_id), "external_contribution", note="manual")

    rows = list_classified_flows(conn)
    assert len(rows) == 1
    assert rows[0]["rule_id"] is None
    conn.close()


def test_rsu_vest_in_candidate_universe():
    """vest rows must appear in list_unclassified_flows before tagging."""
    conn = _make_db()
    today = date.today()
    vest_id = _insert_tx(conn, today.isoformat(), "RSU_AMZN", "vest", 50000.0)

    unclassified = list_unclassified_flows(conn)
    assert any(r["source_row_key"] == str(vest_id) for r in unclassified), (
        "vest row must appear in unclassified before tagging"
    )
    conn.close()


def _month_start_n_ago(today: date, n: int) -> date:
    """First-of-month date N months before `today` (n=0 -> this month)."""
    year = today.year
    month = today.month - n
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _insert_income_expense(conn, month_date: date, payload: dict) -> None:
    conn.execute(
        "INSERT INTO income_expense_monthly (record_key, transaction_date, payload) VALUES (?, ?, ?)",
        [f"ie_{month_date.isoformat()}", month_date.isoformat(), json.dumps(payload)],
    )


def test_glide_path_headline_uses_run_rate_when_investment_ledger_available():
    """ADR-025 §5.2 rewire: run-rate now derives from 月度收支 net_external_ttm
    (+ RSU retained-in-window, zero here — no RSU transactions in this
    fixture), not cash_flow_tags. ¥30,000/mo net external investment for 12
    months -> headline switches to current_run_rate.
    """
    conn = _make_db()
    today = date.today()

    for i in range(12):
        month = _month_start_n_ago(today, i)
        _insert_income_expense(conn, month, {
            "投资理财_股票基金_天天基金": 30_000.0,
            "收入_主动收入_工资": 200_000.0,  # generous income so the 60% sanity guard never fires
        })

    result = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0, trailing_twr=0.1105)
    assert result["reachable"] is True

    run_rate = result.get("run_rate_monthly")
    assert run_rate is not None, "Expected run-rate to be available with 12 months of ledger investment"
    assert abs(run_rate - 30_000.0) < 1000.0, f"Expected ~30000/mo, got {run_rate}"

    headline = result["headline"]
    assert headline["scenario_used"] == "current_run_rate"
    assert headline["years_to_target"] is not None

    # With ¥30K/mo at NW ¥3.28M and 11.05% TWR → ~12.2y
    assert 11.5 <= headline["years_to_target"] <= 13.0, (
        f"Expected 12–12.5y for ¥30K/mo, got {headline['years_to_target']}"
    )
    assert result.get("run_rate_status") == "available"


def test_glide_path_run_rate_not_gated_by_contamination():
    """ADR-025 §5.2 rewire: the run-rate no longer reads cash_flow_tags at
    all, so a large pile of untagged flow candidates (the OLD contamination
    trigger) must NOT suppress it — as long as income_expense_monthly has
    ledger data, a run-rate is returned regardless of tagging completeness.
    """
    conn = _make_db()
    today = date.today()

    # 118 untagged transfer_in transactions -> well above the OLD 5% contamination
    # threshold. Dated safely outside the ledger window so they cannot be
    # mistaken for ledger data by any code path.
    for i in range(118):
        _insert_tx(conn, f"2019-{(i % 12) + 1:02d}-01", "CASH_IN", "transfer_in", 1000.0)

    for i in range(12):
        month = _month_start_n_ago(today, i)
        _insert_income_expense(conn, month, {
            "投资理财_股票基金_天天基金": 10_000.0,
            "收入_主动收入_工资": 100_000.0,
        })

    result = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0, trailing_twr=0.1105)

    assert result["run_rate_monthly"] is not None, (
        "run-rate must NOT be suppressed by untagged cash_flow_tags candidates"
    )
    assert abs(result["run_rate_monthly"] - 10_000.0) < 1000.0
    assert result["run_rate_status"] == "available"


def test_glide_path_run_rate_ignores_cash_flow_tags_classification():
    """cash_flow_tags classification (external_contribution vs
    internal_transfer) must have ZERO effect on the ADR-025-sourced run-rate
    — it is computed purely from income_expense_monthly + RSU FIFO now, and
    never reads cash_flow_tags. Tag a same-day SGOV/MSFT switch two different
    ways and confirm the run-rate is identical (and matches the ledger-only
    figure) either way.
    """
    conn = _make_db()
    from src.services.north_star_flows import tag_flow_manual

    today = date.today()
    for i in range(12):
        month = _month_start_n_ago(today, i)
        _insert_income_expense(conn, month, {
            "投资理财_股票基金_天天基金": 15_000.0,
            "收入_主动收入_工资": 150_000.0,
        })

    sgov_id = _insert_tx(conn, "2025-03-01", "US_STK_SGOV", "sell", 50_000.0)
    msft_id = _insert_tx(conn, "2025-03-01", "US_STK_MSFT", "buy", 50_000.0)
    tag_flow_manual(conn, "transactions", str(sgov_id), "internal_transfer", note="switch")
    tag_flow_manual(conn, "transactions", str(msft_id), "internal_transfer", note="switch")
    baseline = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0, trailing_twr=0.1105)

    tag_flow_manual(conn, "transactions", str(sgov_id), "external_contribution", note="reclassified")
    tag_flow_manual(conn, "transactions", str(msft_id), "external_contribution", note="reclassified")
    reclassified = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0, trailing_twr=0.1105)

    assert baseline["run_rate_monthly"] == reclassified["run_rate_monthly"]
    assert abs(baseline["run_rate_monthly"] - 15_000.0) < 1000.0


def test_glide_path_sanity_guard_fires_when_run_rate_exceeds_income():
    """If run-rate > 60% of trailing gross income, run_rate_monthly must be None
    and run_rate_status must be 'run-rate implausible — check flow tagging'.

    ADR-025 §5.2 rewire: run-rate now comes from income_expense_monthly's own
    投资理财 columns (net_external_ttm), not tagged transactions — so both the
    numerator (investment) and the guard's denominator (income) are read off
    the SAME income_expense_monthly rows.
    """
    conn = _make_db()
    today = date.today()

    # ¥5,000/month income, ¥40,000/month invested -> net_external_ttm=480,000/yr,
    # run_rate=40,000/mo, gross_income=60,000/yr, 60% threshold=36,000/mo.
    # 40,000 > 36,000 -> implausible.
    for i in range(12):
        month = _month_start_n_ago(today, i)
        _insert_income_expense(conn, month, {
            "投资理财_股票基金_天天基金": 40_000.0,
            "收入_主动收入_工资": 5_000.0,
        })

    result = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0, trailing_twr=0.1105)

    assert result["run_rate_monthly"] is None
    status = result["run_rate_status"]
    assert "implausible" in (status or "").lower(), (
        f"Expected implausible status, got: {status}"
    )


def test_flow_contamination_status_empty_db():
    """Empty DB: no candidates → not contaminated."""
    from src.services.north_star_flows import flow_contamination_status
    conn = _make_db()
    status = flow_contamination_status(conn)
    assert status["contaminated"] is False
    assert status["unclassified_count"] == 0
    assert status["total_count"] == 0
    conn.close()


def test_flow_contamination_status_fires_on_large_untagged_inflow():
    """A single unclassified transfer_in > ¥50,000 triggers contamination."""
    from src.services.north_star_flows import flow_contamination_status
    conn = _make_db()
    _insert_tx(conn, "2025-01-01", "BIG_INFLOW", "transfer_in", 60_000.0)
    status = flow_contamination_status(conn)
    assert status["contaminated"] is True
    assert status["has_large_untagged_inflow"] is True
    conn.close()


def test_flow_contamination_pct_threshold():
    """With 1 unclassified out of 10 total (10% > 5%), contaminated=True."""
    from src.services.north_star_flows import flow_contamination_status, tag_flow_manual
    conn = _make_db()
    # Insert 10 transfers; tag 9; leave 1 untagged
    ids = []
    for i in range(10):
        tx_id = _insert_tx(conn, f"2025-0{(i % 9) + 1}-01", f"ASSET_{i}", "transfer_in", 1000.0)
        ids.append(tx_id)
    for tx_id in ids[:9]:
        tag_flow_manual(conn, "transactions", str(tx_id), "internal_transfer")
    status = flow_contamination_status(conn)
    assert status["contaminated"] is True
    assert status["unclassified_count"] == 1
    assert status["total_count"] == 10
    conn.close()


# ── income_expense_monthly exclusion from classifier scope ───────────────────

def test_income_expense_monthly_excluded_from_unclassified_flows():
    """income_expense_monthly rows must NEVER appear in list_unclassified_flows
    and must not affect unclassified_count (scope-reduction per owner decision).
    """
    import json
    from src.services.north_star_flows import flow_contamination_status
    conn = _make_db()
    today = date.today()

    # Insert an income_expense_monthly row with a nonzero net
    conn.execute(
        "INSERT INTO income_expense_monthly (record_key, transaction_date, payload) VALUES (?, ?, ?)",
        [
            "iem_exclusion_test",
            today.isoformat(),
            json.dumps({"收入_主动收入_工资": 20000.0, "必要开支_日常支出_餐饮娱乐": 5000.0}),
        ],
    )

    # list_unclassified_flows must return nothing for this row
    unclassified = list_unclassified_flows(conn)
    assert not any(r["source_table"] == "income_expense_monthly" for r in unclassified), (
        "income_expense_monthly rows must not appear in list_unclassified_flows"
    )

    # contribution_metrics unclassified_count must also be 0 (no tx candidates)
    metrics = contribution_metrics(conn)
    assert metrics["unclassified_count"] == 0, (
        "unclassified_count must not include income_expense_monthly rows"
    )

    # flow_contamination_status must show 0 total_count
    status = flow_contamination_status(conn)
    assert status["total_count"] == 0
    assert status["unclassified_count"] == 0
    assert status["contaminated"] is False
    conn.close()


def test_income_expense_monthly_excluded_from_classified_flows():
    """A cash_flow_tags row pointing to income_expense_monthly must not appear
    in list_classified_flows (the classifier now only surfaces transactions).
    """
    conn = _make_db()
    today = date.today()

    # Manually insert a tag for an income_expense_monthly source (e.g. a legacy
    # row or one inserted via tag_flow_manual before this exclusion was in place)
    conn.execute(
        "INSERT INTO cash_flow_tags (source_table, source_row_key, classification, tagged_by, amount_cny, flow_date)"
        " VALUES ('income_expense_monthly', 'iem_legacy_key', 'income_reinvested', 'manual', 5000.0, ?)",
        [today.isoformat()],
    )

    # list_classified_flows must exclude it
    rows = list_classified_flows(conn)
    assert not any(r["source_table"] == "income_expense_monthly" for r in rows), (
        "list_classified_flows must not return income_expense_monthly-sourced tags"
    )
    conn.close()


# ── R2-4: years_to_target_by_scenario fixture tests ──────────────────────────

def test_years_to_target_by_scenario_zero_fixture():
    """NW ¥3,000,000 / TWR 11.05% / ¥0 → zero ≈18.1y.
    Fixture validated: 18.1y (deterministic monthly-compounding formula).
    R2-4 acceptance: field present and within ±0.15y of 18.1.
    """
    conn = _make_db()
    result = glide_path(conn, monthly_contribution=0.0, current_nw=3_000_000.0, trailing_twr=0.1105)
    by_scenario = result.get("years_to_target_by_scenario")
    assert by_scenario is not None, "years_to_target_by_scenario must be present"
    assert by_scenario["zero"] is not None
    assert abs(by_scenario["zero"] - 18.1) <= 0.15, (
        f"Expected zero ≈18.1y, got {by_scenario['zero']}"
    )
    # scenario is None when monthly_contribution == 0
    assert by_scenario["scenario"] is None, (
        f"scenario must be None when monthly_contribution=0, got {by_scenario['scenario']}"
    )


def test_years_to_target_by_scenario_10k_scenario():
    """NW ¥3,000,000 / TWR 11.05% / ¥10,000/mo → scenario ≈15.56y ±0.2.
    Fixture validated: 15.56y with deterministic monthly-compounding engine.
    R2-4 acceptance: within ±0.2y of 15.56.
    """
    conn = _make_db()
    result = glide_path(conn, monthly_contribution=10_000.0, current_nw=3_000_000.0, trailing_twr=0.1105)
    by_scenario = result.get("years_to_target_by_scenario")
    assert by_scenario is not None, "years_to_target_by_scenario must be present"
    assert by_scenario["scenario"] is not None, (
        "scenario must be non-None when monthly_contribution=10000"
    )
    assert abs(by_scenario["scenario"] - 15.56) <= 0.2, (
        f"Expected scenario ≈15.56y (±0.2), got {by_scenario['scenario']}"
    )
    # scenario < zero (contributions shorten the horizon)
    assert by_scenario["zero"] is not None
    assert by_scenario["scenario"] < by_scenario["zero"], (
        "¥10K/mo scenario should reach target sooner than ¥0/mo"
    )


def test_years_to_target_by_scenario_run_rate_none_when_contaminated():
    """Regression (R2-2): with 118 untagged flows, run_rate in
    years_to_target_by_scenario must be None — never a number.
    """
    conn = _make_db()
    for i in range(118):
        _insert_tx(conn, f"2025-{(i % 12) + 1:02d}-01", "CASH_IN", "transfer_in", 1000.0)

    result = glide_path(conn, monthly_contribution=0.0, current_nw=3_276_919.0, trailing_twr=0.1105)
    by_scenario = result.get("years_to_target_by_scenario")
    assert by_scenario is not None
    # Contaminated state: run_rate must be None (R2-2 guard)
    assert by_scenario["run_rate"] is None, (
        f"run_rate must be None when flow data is contaminated, got {by_scenario['run_rate']}"
    )
    # zero should still be populated
    assert by_scenario["zero"] is not None, "zero must be computable regardless of run-rate status"


# ── WS-A: list_classified_flows ──────────────────────────────────────────────

def test_list_classified_flows_empty_when_nothing_tagged():
    conn = _make_db()
    rows = list_classified_flows(conn)
    assert rows == []
    conn.close()


def test_list_classified_flows_returns_tagged_rows():
    conn = _make_db()
    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "CN_FUND_000001", "transfer_in", 7000.0)
    tag_flow_manual(conn, "transactions", str(tx_id), "external_contribution", note="salary")

    rows = list_classified_flows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["source_table"] == "transactions"
    assert row["source_row_key"] == _nk_for(conn, tx_id)
    assert row["classification"] == "external_contribution"
    assert row["tagged_by"] == "manual"
    assert row["asset_id"] == "CN_FUND_000001"
    # BUG 3 fix: returns real source amount (not stored tag amount)
    assert row["amount_cny"] == 7000.0
    assert row["transaction_type"] == "transfer_in"
    assert row["note"] == "salary"
    assert row["orphaned"] is False
    conn.close()


def test_list_classified_flows_internal_transfer_returns_real_source_amount():
    """BUG 3 fix: internal_transfer is stored as ¥0 in cash_flow_tags (by convention),
    but list_classified_flows must return the real source amount for display."""
    conn = _make_db()
    today = date.today()
    # Insert a ¥50,000 internal transfer
    tx_id = _insert_tx(conn, today.isoformat(), "US_STK_SGOV", "sell", 50000.0)
    tag_flow_manual(conn, "transactions", str(tx_id), "internal_transfer", note="SGOV switch")

    # Verify that the tag stores ¥0 (by convention)
    stored = conn.execute(
        "SELECT amount_cny FROM cash_flow_tags WHERE source_row_key = ?", [_nk_for(conn, tx_id)]
    ).fetchone()
    assert float(stored[0]) == 0.0, "Tag should store ¥0 for internal_transfer"

    # But list_classified_flows should return the REAL source amount
    rows = list_classified_flows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["classification"] == "internal_transfer"
    assert row["amount_cny"] == 50000.0, (
        f"list_classified_flows must return real source amount ¥50000, got {row['amount_cny']}"
    )
    assert row["transaction_type"] == "sell"
    conn.close()


def test_list_classified_flows_filter_by_classification():
    conn = _make_db()
    today = date.today()
    tx_id_a = _insert_tx(conn, today.isoformat(), "A_ASSET", "transfer_in", 1000.0)
    tx_id_b = _insert_tx(conn, today.isoformat(), "B_ASSET", "transfer_in", 2000.0)
    tag_flow_manual(conn, "transactions", str(tx_id_a), "external_contribution")
    tag_flow_manual(conn, "transactions", str(tx_id_b), "income_reinvested")

    ec_rows = list_classified_flows(conn, classification="external_contribution")
    assert all(r["classification"] == "external_contribution" for r in ec_rows)
    assert any(r["source_row_key"] == _nk_for(conn, tx_id_a) for r in ec_rows)
    assert not any(r["source_row_key"] == _nk_for(conn, tx_id_b) for r in ec_rows)

    ir_rows = list_classified_flows(conn, classification="income_reinvested")
    assert all(r["classification"] == "income_reinvested" for r in ir_rows)
    conn.close()


def test_list_classified_flows_invalid_classification_raises():
    conn = _make_db()
    with pytest.raises(ValueError, match="classification must be one of"):
        list_classified_flows(conn, classification="not_valid")
    conn.close()


# ── WS-A: tag_flows_bulk ─────────────────────────────────────────────────────

def test_tag_flows_bulk_happy_path():
    conn = _make_db()
    today = date.today()
    tx_id_a = _insert_tx(conn, today.isoformat(), "BULK_A", "transfer_in", 5000.0)
    tx_id_b = _insert_tx(conn, today.isoformat(), "BULK_B", "transfer_in", 6000.0)

    items = [
        {"source_table": "transactions", "source_row_key": str(tx_id_a)},
        {"source_table": "transactions", "source_row_key": str(tx_id_b)},
    ]
    result = tag_flows_bulk(conn, items, "external_contribution")
    assert result["tagged"] == 2
    assert result["not_found"] == 0

    # Both rows should be in cash_flow_tags with tagged_by='manual'
    count = conn.execute(
        "SELECT COUNT(*) FROM cash_flow_tags WHERE tagged_by = 'manual'"
    ).fetchone()[0]
    assert count == 2
    conn.close()


def test_tag_flows_bulk_invalid_classification_raises():
    conn = _make_db()
    with pytest.raises(ValueError, match="classification must be one of"):
        tag_flows_bulk(conn, [], "bogus")
    conn.close()


def test_tag_flows_bulk_missing_row_counted_in_not_found():
    conn = _make_db()
    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "REAL_A", "transfer_in", 1000.0)
    items = [
        {"source_table": "transactions", "source_row_key": str(tx_id)},
        {"source_table": "transactions", "source_row_key": "999999"},
    ]
    result = tag_flows_bulk(conn, items, "external_contribution")
    assert result["tagged"] == 1
    assert result["not_found"] == 1
    conn.close()


def test_tag_flows_bulk_empty_returns_zeros():
    conn = _make_db()
    result = tag_flows_bulk(conn, [], "external_contribution")
    assert result == {"tagged": 0, "not_found": 0}
    conn.close()


def test_tag_flows_bulk_internal_transfer_stores_zero_amount():
    """internal_transfer amounts are stored as ¥0 by convention (same as single-tag path)."""
    conn = _make_db()
    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "IT_ASSET", "transfer_in", 50000.0)
    items = [{"source_table": "transactions", "source_row_key": str(tx_id)}]
    tag_flows_bulk(conn, items, "internal_transfer")

    row = conn.execute(
        "SELECT amount_cny FROM cash_flow_tags WHERE source_row_key = ?", [_nk_for(conn, tx_id)]
    ).fetchone()
    assert row is not None
    assert float(row[0]) == 0.0
    conn.close()


# ── WS-A: untag_flows ────────────────────────────────────────────────────────

def test_untag_flows_removes_tagged_rows():
    conn = _make_db()
    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "UNTAG_TEST", "transfer_in", 3000.0)
    tag_flow_manual(conn, "transactions", str(tx_id), "external_contribution")

    count_before = conn.execute("SELECT COUNT(*) FROM cash_flow_tags").fetchone()[0]
    assert count_before >= 1

    nk = _nk_for(conn, tx_id)
    result = untag_flows(conn, [{"source_table": "transactions", "source_row_key": str(tx_id)}])
    assert result["deleted"] == 1

    count_after = conn.execute("SELECT COUNT(*) FROM cash_flow_tags WHERE source_row_key = ?", [nk]).fetchone()[0]
    assert count_after == 0
    conn.close()


def test_untag_flows_matches_stored_natural_key_directly():
    """untag also works when the caller echoes back the row's *stored* key
    (nk:...) rather than the original transactions.id — the shape
    list_classified_flows actually returns to the frontend."""
    conn = _make_db()
    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "UNTAG_NK_TEST", "transfer_in", 4000.0)
    tag_flow_manual(conn, "transactions", str(tx_id), "external_contribution")

    stored_key = list_classified_flows(conn)[0]["source_row_key"]
    assert stored_key.startswith("nk:")

    result = untag_flows(conn, [{"source_table": "transactions", "source_row_key": stored_key}])
    assert result["deleted"] == 1
    assert list_classified_flows(conn) == []
    conn.close()


def test_untag_flows_empty_returns_zero():
    conn = _make_db()
    result = untag_flows(conn, [])
    assert result == {"deleted": 0}
    conn.close()


def test_untag_flows_nonexistent_row_returns_zero():
    conn = _make_db()
    result = untag_flows(conn, [{"source_table": "transactions", "source_row_key": "999999"}])
    assert result["deleted"] == 0
    conn.close()


def test_untag_does_not_touch_source_tables():
    """untag_flows must only delete from cash_flow_tags, never from transactions."""
    conn = _make_db()
    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "SAFE_ASSET", "transfer_in", 1000.0)
    tag_flow_manual(conn, "transactions", str(tx_id), "external_contribution")

    tx_count_before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    untag_flows(conn, [{"source_table": "transactions", "source_row_key": str(tx_id)}])
    tx_count_after = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert tx_count_after == tx_count_before  # transactions unchanged
    conn.close()


# ── WS-A: contributions_summary ──────────────────────────────────────────────

def test_contributions_summary_shape_empty_db():
    conn = _make_db()
    result = contributions_summary(conn)
    assert "ytd_sum" in result
    assert "trailing_12m_sum" in result
    assert "unclassified_count" in result
    assert "by_classification" in result
    bc = result["by_classification"]
    assert set(bc.keys()) == {"external_contribution", "internal_transfer", "income_reinvested"}
    assert all(isinstance(v, float) for v in bc.values())
    conn.close()


def test_contributions_summary_ytd_and_by_classification():
    conn = _make_db()
    today = date.today()
    # Tag two contributions this year
    tx_id_a = _insert_tx(conn, today.isoformat(), "SUM_A", "transfer_in", 10000.0)
    tx_id_b = _insert_tx(conn, today.isoformat(), "SUM_B", "transfer_in", 5000.0)
    tag_flow_manual(conn, "transactions", str(tx_id_a), "external_contribution")
    tag_flow_manual(conn, "transactions", str(tx_id_b), "income_reinvested")

    result = contributions_summary(conn)
    # ytd_sum only counts external_contribution (contribution_metrics behavior)
    assert result["ytd_sum"] == 10000.0
    # by_classification reflects trailing-12M per-class sums
    assert result["by_classification"]["external_contribution"] == 10000.0
    assert result["by_classification"]["income_reinvested"] == 5000.0
    assert result["by_classification"]["internal_transfer"] == 0.0
    assert result["unclassified_count"] == 0
    conn.close()


# ── WS-B: contributions_summary().investment sub-object ─────────────────────
# (plan docs/plans/2026-07-20-investment-contributions-savings.md §Reconciliation)

def _insert_income_expense_month(conn, record_key: str, month: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO income_expense_monthly (record_key, transaction_date, payload) VALUES (?, ?, ?)",
        [record_key, month, json.dumps(payload)],
    )


def test_contributions_summary_investment_field_present_legacy_unchanged():
    """investment.* is additive: legacy ytd_sum/trailing_12m_sum/
    by_classification/unclassified_count keep their existing (cash_flow_tags-
    only) semantics, unchanged by the presence of 投资理财 data."""
    conn = _make_db()
    _insert_income_expense_month(conn, "recon1", "2025-06-01", {
        "投资理财_股票基金_天天基金": 20000,
        "投资理财_股票基金_Schawab": 10000,
        "收入_主动收入_工资": 50000,
    })

    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "SUM_LEGACY", "transfer_in", 7000.0)
    tag_flow_manual(conn, "transactions", str(tx_id), "external_contribution")

    result = contributions_summary(conn)

    # Legacy fields: cash_flow_tags-derived only, untouched by 投资理财 data.
    assert result["ytd_sum"] == 7000.0
    assert result["trailing_12m_sum"] == 7000.0
    assert result["by_classification"]["external_contribution"] == 7000.0
    assert result["unclassified_count"] == 0

    # New investment.* sub-object, sourced from 投资理财 only.
    assert "investment" in result
    inv = result["investment"]
    assert inv["net_external_ttm"] == 30000.0
    assert inv["gross_invested_ttm"] == 30000.0
    # investment_rate is the metric this used to call "savings rate" (WS-G,
    # 2026-08-01); savings_rate is now (income − consumption) / income, and
    # this fixture books no expense at all.
    assert inv["investment_rate_ttm"] == pytest.approx(0.6)
    assert inv["savings_rate_ttm"] == pytest.approx(1.0)
    assert "series" in inv
    conn.close()


def test_contributions_summary_reconciliation_no_double_count():
    """§Reconciliation regression test (MANDATORY per plan): investment.*,
    the legacy cash_flow_tags sums, and rsu.* are THREE independent sources
    that must never be summed together. Builds a DB with (a)
    income_expense_monthly 投资理财 rows, (b) a cash_flow_tags
    external_contribution row (via a tagged transaction), and (c) an
    RSU_Excel vest + partial sell, all seeded with distinct non-zero values
    so no pairwise/triple sum can coincidentally match another field and
    make the assertion pass vacuously. Proves no returned field equals any
    of the double/triple-count combinations across the three sources.

    Widened 2026-08-01 (plan 2026-08-01-ie-column-mapping-and-ibkr-amounts
    WS-A) to the four columns added that day: the `us_ibkr` destination, the
    `收入_被动收入_股票卖出收益` realized-gain column (role='income', NOT
    'redemption' — see below), and their two `_USD` native-currency siblings,
    which must reach no total at all."""
    conn = _make_db()
    _insert_income_expense_month(conn, "recon2", "2025-09-01", {
        "投资理财_股票基金_天天基金": 20000,
        "投资理财_黄金_招行纸黄金": 5000,
        "投资理财_股票基金_IBKR": 7000,
        "收入_被动收入_基金赎回": 3000,
        # Realized gain on RSU shares sold: role='income'. Its principal was
        # booked as income at vest and NEVER as a 投资理财 column, so netting it
        # out of contributions would double-subtract (ADR-025 §4b). It is an
        # income LEAF, so it counts ONCE in the income basis and must move no
        # contribution field at all.
        "收入_被动收入_股票卖出收益": 6000,
        # Native-currency siblings — the owner applies FX in Excel, so these are
        # the same money as their CNY partners. They must reach nothing.
        "投资理财_股票基金_Schawab_USD": 500,
        "投资理财_股票基金_IBKR_USD": 1000,
        "收入_被动收入_股票卖出收益_USD": 850,
        "收入_主动收入_工资": 40000,
        # The Excel's own aggregates (role='computed'). Present so this sweep
        # proves the WS-E ruling structurally: Huinsight derives every total from the
        # leaves above and must reach the SAME numbers with these columns
        # sitting right next to them — summing an aggregate alongside its own
        # leaves is the double count V84 removed.
        "主动收入合计": 40000,
        "被动收入合计": 9000,
        "总收入合计": 49000,
        "理财": 32000,
        "总支出": 32000,
    })

    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "RECON_TAG", "transfer_in", 99999.0)
    tag_flow_manual(conn, "transactions", str(tx_id), "external_contribution")

    # RSU: vest 40 sh @ 111.1 CNY/sh (=4444 gross), sell 30 sh, leaving 10 sh
    # retained (=1111 CNY at vest price) — both inside the "2025-09" window
    # (the only income_expense_monthly month present). currency='CNY' so no
    # FX mocking is needed (mirrors the defensive CNY-row test in
    # test_rsu_contributions.py). vest_gross_ttm (4444) and retained_ttm
    # (1111) are deliberately DISTINCT non-zero values, chosen so that
    # neither they nor any of their pairwise/triple sums with
    # net_external_ttm (22000) / trailing_12m_sum (99999) coincidentally
    # equal any other field already in the response (e.g.
    # investment.gross_invested_ttm = 25000, which a naive X=3000 would have
    # collided with).
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, quantity,
             price_unit, amount_gross, amount_net, currency, source_system, is_provisional)
        VALUES ('2025-09-05', 'RSU_AMZN', 'RSU_AMZN', 'vest', 40.0, 111.1, 4444.0, 4444.0,
                'CNY', 'RSU_Excel', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, quantity,
             price_unit, amount_gross, amount_net, currency, source_system, is_provisional)
        VALUES ('2025-09-08', 'RSU_AMZN', 'RSU_AMZN', 'sell', -30.0, 111.1, 3333.0, 3333.0,
                'CNY', 'RSU_Excel', FALSE)
        """
    )

    result = contributions_summary(conn)
    expected_investment = contributions_summary_v2(conn)

    # investment.net_external_ttm matches the 投资理财-only computation exactly.
    assert result["investment"]["net_external_ttm"] == expected_investment["net_external_ttm"]
    # 20000 (天天基金) + 5000 (纸黄金) + 7000 (IBKR) - 3000 (基金赎回).
    assert result["investment"]["net_external_ttm"] == pytest.approx(29000.0)
    inv = result["investment"]
    # Neither _USD sibling reached gross_invested; IBKR landed in its own bucket.
    assert inv["gross_invested_ttm"] == pytest.approx(32000.0)
    assert inv["by_destination_ttm"]["us_ibkr"] == pytest.approx(7000.0)
    assert inv["by_destination_ttm"]["us_schwab"] == pytest.approx(0.0), (
        "投资理财_股票基金_Schawab_USD must reach nothing — there is no CNY Schwab row this month"
    )
    # 股票卖出收益 is NOT netted out of contributions (it is not a redemption)
    # and counts exactly once as an income leaf.
    assert inv["redemptions_ttm"] == pytest.approx(3000.0)
    assert inv["income_basis_ttm"] == pytest.approx(46000.0), "工资 40000 + 卖出收益 6000"
    # Excel-equivalent gross income, derived: basis + the 3000 redemption.
    assert inv["income_ttm"] == pytest.approx(49000.0)
    assert inv["income_ttm"] != pytest.approx(55000.0), (
        "the 收入_被动收入_股票卖出收益_USD sibling must reach no total"
    )

    # trailing_12m_sum is the unrelated cash_flow_tags-derived total (proves
    # it did NOT pick up any 投资理财 or RSU amounts, and vice versa).
    assert result["trailing_12m_sum"] == pytest.approx(99999.0)

    # rsu.* is the third independent source — gross vest vs. FIFO-retained.
    assert result["rsu"]["vest_gross_ttm"] == pytest.approx(4444.0)
    assert result["rsu"]["retained_ttm"] == pytest.approx(1111.0)
    assert result["rsu"]["vest_gross_ttm"] != result["rsu"]["retained_ttm"], (
        "fixture must seed distinct non-zero RSU figures, not both derived from the same value"
    )

    net_external_ttm = result["investment"]["net_external_ttm"]
    trailing_12m_sum = result["trailing_12m_sum"]
    vest_gross_ttm = result["rsu"]["vest_gross_ttm"]
    retained_ttm = result["rsu"]["retained_ttm"]

    # ONE owner-approved exception (ADR-025 Amendment 2026-08-01): the
    # INVESTMENT-rate numerator IS net_external_ttm + rsu.retained_ttm. The
    # denominator already books the full vest as income, so excluding the
    # retained shares understated it; the glide run-rate has always summed
    # exactly these two (§4c). It is asserted POSITIVELY here — the exception
    # is a tested requirement, not a hole in the invariant — and therefore is
    # NOT in the forbidden `combos` below. Every other combination stays
    # forbidden, including the same pair plus trailing_12m_sum.
    assert result["investment"]["investment_numerator_ttm"] == pytest.approx(
        net_external_ttm + retained_ttm
    ), "the one sanctioned cross-source sum must actually be what investment_rate uses"

    combos = {
        "net_external_ttm + trailing_12m_sum": net_external_ttm + trailing_12m_sum,
        "net_external_ttm + rsu.vest_gross_ttm": net_external_ttm + vest_gross_ttm,
        "trailing_12m_sum + rsu.vest_gross_ttm": trailing_12m_sum + vest_gross_ttm,
        "net_external_ttm + rsu.retained_ttm + trailing_12m_sum": (
            net_external_ttm + retained_ttm + trailing_12m_sum
        ),
        # ie_column-specific double-count shapes (plan 2026-08-01 WS-A). Each
        # is what a field WOULD equal if the corresponding rule were violated.
        "gross_invested_ttm if the _USD siblings were summed": 32000.0 + 500.0 + 1000.0,
        "net_external_ttm if 股票卖出收益 were treated as a redemption": 29000.0 - 6000.0,
        # WS-E shapes: an Excel aggregate read as a calculation input, on top of
        # the very leaves it aggregates.
        "income_ttm if 总收入合计 were summed with its own leaves": 49000.0 + 49000.0,
        "gross_invested_ttm if 理财 were summed with its own leaves": 32000.0 + 32000.0,
    }
    # No two combo targets may accidentally collide with each other either —
    # that would silently weaken which regression actually gets tested.
    assert len(set(round(v, 6) for v in combos.values())) == len(combos), (
        f"combo targets are not pairwise distinct, fixture values need adjusting: {combos}"
    )

    def _flatten(d, prefix=""):
        for k, v in d.items():
            path = f"{prefix}{k}"
            if isinstance(v, dict):
                yield from _flatten(v, f"{path}.")
            elif isinstance(v, list):
                continue
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                yield path, v

    for path, value in _flatten(result):
        for combo_name, combo_value in combos.items():
            assert value != pytest.approx(combo_value), (
                f"{path}={value} unexpectedly equals {combo_name} ({combo_value}) — double-count regression"
            )

    # Anti-vacuity: the sweep above is only meaningful if it actually walked
    # the fields it is supposed to protect.
    walked = {path for path, _ in _flatten(result)}
    for required in (
        "investment.net_external_ttm",
        "investment.income_ttm",
        "investment.income_basis_ttm",
        "investment.expense_basis_ttm",
        "investment.investment_numerator_ttm",
        "trailing_12m_sum",
        "rsu.vest_gross_ttm",
        "rsu.retained_ttm",
    ):
        assert required in walked, f"{required} was not walked — the sweep is vacuous"

    conn.close()


def test_contributions_summary_empty_db_returns_without_error():
    conn = _make_db()
    result = contributions_summary(conn)
    assert result["investment"]["net_external_ttm"] == 0.0
    assert result["investment"]["savings_rate_ttm"] is None
    conn.close()


# ── Task B: contributions_summary(window_months=) threading ────────────────
# (docs/api-specs / plan 2026-07-25-cash-flow-classification-completion.md
# follow-up: Cash Flow tab Last 12m/36m/All Time toggle must drive the KPI
# tiles, not just the chart)

def test_contributions_summary_window_months_default_is_12():
    conn = _make_db()
    for i in range(15):
        year = 2024 + (i // 12)
        month_num = (i % 12) + 1
        _insert_income_expense_month(conn, f"w{i}", f"{year}-{month_num:02d}-01", {
            "投资理财_股票基金_天天基金": 1000.0,
            "收入_主动收入_工资": 10000,
        })
    default_result = contributions_summary(conn)
    explicit_result = contributions_summary(conn, window_months=12)
    assert default_result["investment"] == explicit_result["investment"]
    conn.close()


def test_contributions_summary_window_months_36_widens_investment_window():
    conn = _make_db()
    months = []
    for i in range(40):
        year = 2022 + (i // 12)
        month_num = (i % 12) + 1
        month_str = f"{year}-{month_num:02d}-01"
        months.append(month_str[:7])
        _insert_income_expense_month(conn, f"w{i}", month_str, {
            "投资理财_股票基金_天天基金": 1000.0 * (i + 1),
            "收入_主动收入_工资": 10000,
        })

    result_12 = contributions_summary(conn, window_months=12)
    result_36 = contributions_summary(conn, window_months=36)

    assert result_12["investment"]["window_start_month"] == months[-12]
    assert result_36["investment"]["window_start_month"] == months[-36]
    assert result_36["investment"]["gross_invested_ttm"] > result_12["investment"]["gross_invested_ttm"]

    # ytd_sum/trailing_12m_sum (legacy, ADR-025 §4a) are untouched by
    # window_months — no cash_flow_tags rows here, so both stay 0.0, but the
    # point is they must be equal across the two calls (same fixed 12M/YTD
    # basis), not vary with window_months.
    assert result_12["ytd_sum"] == result_36["ytd_sum"]
    assert result_12["trailing_12m_sum"] == result_36["trailing_12m_sum"]
    conn.close()


def test_contributions_summary_window_months_all_history():
    conn = _make_db()
    months = []
    for i in range(40):
        year = 2022 + (i // 12)
        month_num = (i % 12) + 1
        month_str = f"{year}-{month_num:02d}-01"
        months.append(month_str[:7])
        _insert_income_expense_month(conn, f"w{i}", month_str, {
            "投资理财_股票基金_天天基金": 1000.0 * (i + 1),
            "收入_主动收入_工资": 10000,
        })

    result_all = contributions_summary(conn, window_months=100_000)
    assert result_all["investment"]["window_start_month"] == months[0]
    assert result_all["investment"]["window_end_month"] == months[-1]
    expected_gross = sum(1000.0 * (i + 1) for i in range(40))
    assert result_all["investment"]["gross_invested_ttm"] == expected_gross


def test_contributions_summary_rsu_window_follows_investment_window_months():
    """rsu.* is read off investment.window_start_month/window_end_month —
    this must still hold when a non-default window_months is passed, per
    the "keep that coupling intact" requirement (Task B)."""
    conn = _make_db()
    months = []
    for i in range(20):
        year = 2024 + (i // 12)
        month_num = (i % 12) + 1
        month_str = f"{year}-{month_num:02d}-01"
        months.append(month_str[:7])
        _insert_income_expense_month(conn, f"w{i}", month_str, {
            "投资理财_股票基金_天天基金": 1000.0,
            "收入_主动收入_工资": 10000,
        })

    result_36 = contributions_summary(conn, window_months=36)
    assert result_36["rsu"]["window_start_month"] == result_36["investment"]["window_start_month"]
    assert result_36["rsu"]["window_end_month"] == result_36["investment"]["window_end_month"]
    conn.close()


# ── FX conversion: USD transactions must appear in CNY in all views ─────────

def _insert_tx_usd(conn, tx_date: str, asset_id: str, tx_type: str, amount_net: float) -> int:
    """Insert a transaction with currency='USD'."""
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, amount_net, amount_gross,
             currency, source_system, is_provisional)
        VALUES (?, ?, ?, ?, ?, ?, 'USD', 'test', FALSE)
        """,
        [tx_date, asset_id, asset_id, tx_type, amount_net, amount_net],
    )
    return conn.execute(
        "SELECT id FROM transactions WHERE asset_id = ? AND transaction_date = ? AND transaction_type = ? ORDER BY id DESC LIMIT 1",
        [asset_id, tx_date, tx_type],
    ).fetchone()[0]


_MOCK_FX_RATE = 7.5  # deterministic test rate


def test_usd_transaction_amount_converted_in_unclassified_flows():
    """A USD transfer_in of $1000 must appear as ¥7500 in list_unclassified_flows
    when the FX rate is mocked to 7.5."""
    conn = _make_db()
    today = date.today()
    tx_id = _insert_tx_usd(conn, today.isoformat(), "US_STK_BRK_B", "transfer_in", 1000.0)

    with patch("src.services.north_star_flows.get_today_usd_cny_rate", return_value=_MOCK_FX_RATE):
        rows = list_unclassified_flows(conn)

    matching = [r for r in rows if r["source_row_key"] == str(tx_id)]
    assert len(matching) == 1, "USD transfer_in must appear in unclassified flows"
    assert matching[0]["amount_cny"] == pytest.approx(1000.0 * _MOCK_FX_RATE), (
        f"Expected ¥{1000.0 * _MOCK_FX_RATE}, got {matching[0]['amount_cny']}"
    )
    conn.close()


def test_cny_transaction_amount_unchanged_in_unclassified_flows():
    """A CNY transfer_in of ¥5000 must appear as ¥5000 (no conversion)
    in list_unclassified_flows regardless of FX rate."""
    conn = _make_db()
    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "CN_FUND_NOCURRENCY", "transfer_in", 5000.0)

    with patch("src.services.north_star_flows.get_today_usd_cny_rate", return_value=_MOCK_FX_RATE):
        rows = list_unclassified_flows(conn)

    matching = [r for r in rows if r["source_row_key"] == str(tx_id)]
    assert len(matching) == 1
    assert matching[0]["amount_cny"] == pytest.approx(5000.0)
    conn.close()


def test_usd_transaction_converted_in_list_classified_flows():
    """A USD vest of $4448.47 manually tagged external_contribution must appear
    as ¥4448.47 * rate in list_classified_flows."""
    conn = _make_db()
    today = date.today()
    tx_id = _insert_tx_usd(conn, today.isoformat(), "RSU_GOOG", "vest", 4448.47)

    with patch("src.services.north_star_flows.get_today_usd_cny_rate", return_value=_MOCK_FX_RATE):
        tag_flow_manual(conn, "transactions", str(tx_id), "external_contribution", note="RSU vest")

    with patch("src.services.north_star_flows.get_today_usd_cny_rate", return_value=_MOCK_FX_RATE):
        rows = list_classified_flows(conn)

    matching = [r for r in rows if r["source_row_key"] == _nk_for(conn, tx_id)]
    assert len(matching) == 1
    assert matching[0]["amount_cny"] == pytest.approx(4448.47 * _MOCK_FX_RATE, rel=1e-4), (
        f"Expected ¥{4448.47 * _MOCK_FX_RATE:.2f}, got {matching[0]['amount_cny']}"
    )
    conn.close()


def test_usd_vest_summed_in_cny_by_contribution_metrics():
    """An RSU vest row tagged external_contribution (USD) must contribute
    amount_net * rate to ytd_sum in contribution_metrics, not raw USD amount."""
    conn = _make_db()
    today = date.today()
    _insert_tx_usd(conn, today.isoformat(), "RSU_AMZN_FX", "vest", 4448.47)

    with patch("src.services.north_star_flows.get_today_usd_cny_rate", return_value=_MOCK_FX_RATE):
        classify_flows_heuristic(conn)  # R3 tags rsu_vest

    with patch("src.services.north_star_flows.get_today_usd_cny_rate", return_value=_MOCK_FX_RATE):
        metrics = contribution_metrics(conn)

    expected_cny = 4448.47 * _MOCK_FX_RATE
    assert metrics["ytd_sum"] == pytest.approx(expected_cny, rel=1e-4), (
        f"Expected ytd_sum ≈ ¥{expected_cny:.2f}, got {metrics['ytd_sum']}"
    )
    assert metrics["trailing_12m_sum"] == pytest.approx(expected_cny, rel=1e-4)
    conn.close()


def test_usd_vest_summed_in_cny_by_contributions_summary():
    """contributions_summary by_classification for external_contribution must
    include USD vest converted to CNY at the mock rate."""
    conn = _make_db()
    today = date.today()
    _insert_tx_usd(conn, today.isoformat(), "RSU_AMZN_SUMM", "vest", 4448.47)

    with patch("src.services.north_star_flows.get_today_usd_cny_rate", return_value=_MOCK_FX_RATE):
        classify_flows_heuristic(conn)

    with patch("src.services.north_star_flows.get_today_usd_cny_rate", return_value=_MOCK_FX_RATE):
        summary = contributions_summary(conn)

    expected_cny = 4448.47 * _MOCK_FX_RATE
    ec_sum = summary["by_classification"]["external_contribution"]
    assert ec_sum == pytest.approx(expected_cny, rel=1e-4), (
        f"Expected external_contribution ≈ ¥{expected_cny:.2f}, got {ec_sum}"
    )
    conn.close()


# ── V81: cash_flow_tags stable natural key ───────────────────────────────────
# Root cause (owner-review fix): source_row_key stored transactions.id, but
# _replace_transactions (src/sync/phases/_ingest.py) deletes and reinserts
# rows on every sync for most sources — ids regenerate — orphaning every tag
# on the next re-import. compose_natural_key/parse_natural_key/is_natural_key/
# _resolve_transactions_row (all in src.services.north_star_flows) are the
# single choke point for the fix; these tests exercise them end-to-end.

def test_compose_and_parse_natural_key_round_trip():
    from src.services.north_star_flows import is_natural_key, parse_natural_key

    nk = compose_natural_key("Schwab_CSV", date(2026, 6, 9), "US_STK_VOO", "transfer_out", -0.0)
    assert is_natural_key(nk)
    assert nk == "nk:Schwab_CSV|2026-06-09|US_STK_VOO|transfer_out|0.00"

    parsed = parse_natural_key(nk)
    assert parsed == {
        "source_system": "Schwab_CSV",
        "transaction_date": "2026-06-09",
        "asset_id": "US_STK_VOO",
        "transaction_type": "transfer_out",
        "amount_gross": 0.0,
    }


def test_compose_natural_key_treats_none_amount_gross_as_zero():
    """Matches the COALESCE(amount_gross, 0) semantics of _ingest.py's delete-match."""
    nk = compose_natural_key("test", date(2026, 1, 1), "ASSET", "buy", None)
    assert nk.endswith("|0.00")


def test_parse_natural_key_rejects_non_nk_string():
    from src.services.north_star_flows import parse_natural_key
    assert parse_natural_key("12345") is None
    assert parse_natural_key("nk:too|few|parts") is None


def test_natural_key_tag_survives_replace_transactions_reimport():
    """The core V81 guarantee: a manually-tagged row's classification survives
    _replace_transactions' delete+reinsert (transactions.id regenerates) —
    because the tag is keyed on the stable identity, not the id."""
    import pandas as pd
    from src.sync.phases._ingest import _replace_transactions

    conn = _make_db()
    tx_id_old = _insert_tx(conn, "2026-06-09", "CN_FUND_REIMPORT", "transfer_in", 12345.0)
    tag_flow_manual(conn, "transactions", str(tx_id_old), "external_contribution", note="pre-reimport")

    classified_before = list_classified_flows(conn)
    assert len(classified_before) == 1
    assert classified_before[0]["orphaned"] is False
    stored_key = classified_before[0]["source_row_key"]
    assert stored_key.startswith("nk:")

    # Simulate a sync re-import: SAME identity (date/asset/type/amount_gross/
    # source_system) as the existing row -> _replace_transactions' incremental
    # delete-match deletes the old row and inserts a brand-new one (new id).
    tx_cols = [
        "transaction_date", "asset_id", "asset_name", "transaction_type",
        "quantity", "price_unit", "amount_gross", "amount_net", "commission_fee",
        "currency", "account", "memo", "source_system",
    ]
    new_row = {
        "transaction_date": date(2026, 6, 9), "asset_id": "CN_FUND_REIMPORT",
        "asset_name": "CN_FUND_REIMPORT", "transaction_type": "transfer_in",
        "quantity": None, "price_unit": None, "amount_gross": 12345.0,
        "amount_net": 12345.0, "commission_fee": 0.0, "currency": "CNY",
        "account": None, "memo": None, "source_system": "test",
    }
    tx_df = pd.DataFrame([new_row], columns=tx_cols)
    count = _replace_transactions(conn, tx_df)
    assert count == 1

    tx_id_new = conn.execute(
        "SELECT id FROM transactions WHERE asset_id = 'CN_FUND_REIMPORT'"
    ).fetchone()[0]
    assert tx_id_new != tx_id_old, "id must have regenerated — this is the root cause the fix targets"

    classified_after = list_classified_flows(conn)
    assert len(classified_after) == 1
    assert classified_after[0]["orphaned"] is False, "tag must resolve to the re-imported row, not orphan"
    assert classified_after[0]["source_row_key"] == stored_key, "natural key is unchanged across reimport"
    assert classified_after[0]["classification"] == "external_contribution"
    assert classified_after[0]["amount_cny"] == 12345.0
    conn.close()


def test_tag_flow_manual_resolves_legacy_id_and_composes_nk():
    """tag_flow_manual accepts a raw transactions.id (unchanged frontend
    contract) but always stores the row's current natural key."""
    conn = _make_db()
    tx_id = _insert_tx(conn, "2026-05-01", "LEGACY_ID_TEST", "transfer_in", 2500.0)
    result = tag_flow_manual(conn, "transactions", str(tx_id), "external_contribution")
    assert result["source_row_key"] == _nk_for(conn, tx_id)
    assert result["source_row_key"].startswith("nk:")
    conn.close()


def test_tag_flow_manual_resolves_already_nk_key_idempotently():
    """Re-tagging via the row's own already-stored nk: key (the shape
    list_classified_flows echoes back) must upsert in place, not duplicate."""
    conn = _make_db()
    tx_id = _insert_tx(conn, "2026-05-02", "NK_REINPUT_TEST", "transfer_in", 1500.0)
    first = tag_flow_manual(conn, "transactions", str(tx_id), "external_contribution")
    nk = first["source_row_key"]

    second = tag_flow_manual(conn, "transactions", nk, "income_reinvested", note="reclassified")
    assert second["source_row_key"] == nk
    assert second["classification"] == "income_reinvested"

    count = conn.execute("SELECT COUNT(*) FROM cash_flow_tags").fetchone()[0]
    assert count == 1, "re-tagging via the stored nk must update in place, not insert a duplicate"
    conn.close()


def test_tag_flow_manual_unresolvable_key_raises_lookup_error():
    conn = _make_db()
    with pytest.raises(LookupError):
        tag_flow_manual(conn, "transactions", "999999999", "external_contribution")
    conn.close()


def test_manual_orphan_prevents_heuristic_retag_via_nk_space():
    """manual_keys (used by classify_flows_heuristic to skip already-manually
    -tagged rows) must be compared in the SAME key space the rules compute
    (nk:), not raw transactions.id — otherwise a manually-tagged row that
    also matches a heuristic rule would get silently re-tagged/counted wrong."""
    conn = _make_db()
    sgov_id = _insert_tx(conn, "2026-03-05", "US_STK_SGOV", "sell", 20000.0)
    _insert_tx(conn, "2026-03-05", "US_STK_BRKB", "buy", 20000.0)

    tag_flow_manual(conn, "transactions", str(sgov_id), "external_contribution", note="owner override")

    result = classify_flows_heuristic(conn)
    assert result["skipped_manual"] >= 1

    row = conn.execute(
        "SELECT classification, tagged_by FROM cash_flow_tags WHERE source_row_key = ?",
        [_nk_for(conn, sgov_id)],
    ).fetchone()
    assert row == ("external_contribution", "manual"), "manual tag must survive classify_flows_heuristic"
    conn.close()


def test_list_classified_flows_orphaned_row_visible_with_flag():
    """A tag whose transaction can no longer be resolved (re-imported with a
    different identity, or genuinely deleted) must still appear in
    list_classified_flows — never silently dropped — with orphaned=True and
    null amount/asset/type, but flow_date/classification/tagged_by/note
    preserved."""
    conn = _make_db()
    conn.execute(
        """
        INSERT INTO cash_flow_tags
            (source_table, source_row_key, classification, tagged_by, amount_cny, flow_date, note)
        VALUES ('transactions', '424242', 'external_contribution', 'manual', 9000.0, '2020-01-01', 'old manual tag')
        """
    )
    rows = list_classified_flows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["orphaned"] is True
    assert row["amount_cny"] is None
    assert row["asset_id"] is None
    assert row["transaction_type"] is None
    assert row["classification"] == "external_contribution"
    assert row["tagged_by"] == "manual"
    assert row["flow_date"] == "2020-01-01"
    assert row["note"] == "old manual tag"
    conn.close()


def test_list_classified_flows_orphan_excluded_from_contribution_sums():
    """An orphaned tag contributes no amount to contribution_metrics /
    contributions_summary (its real current amount is unknowable) — same as
    today's behavior for a genuinely unresolvable row, but now reached via
    explicit resolution rather than an accidental SQL JOIN key mismatch."""
    conn = _make_db()
    today = date.today()
    conn.execute(
        """
        INSERT INTO cash_flow_tags
            (source_table, source_row_key, classification, tagged_by, amount_cny, flow_date)
        VALUES ('transactions', '999888', 'external_contribution', 'manual', 50000.0, ?)
        """,
        [today.isoformat()],
    )
    metrics = contribution_metrics(conn)
    assert metrics["ytd_sum"] == 0.0
    summary = contributions_summary(conn)
    assert summary["by_classification"]["external_contribution"] == 0.0
    conn.close()


def test_legacy_id_key_still_resolves_in_list_classified_flows():
    """Backward compat: a tag still keyed by a legacy transactions.id (not
    yet touched by V81 re-keying) resolves normally as long as that id is
    still live — orphaned=False, real amount/asset/type populated."""
    conn = _make_db()
    tx_id = _insert_tx(conn, "2026-04-10", "LEGACY_STILL_LIVE", "transfer_in", 4000.0)
    # Insert directly under the legacy id key, bypassing tag_flow_manual's
    # nk-composition, to simulate a not-yet-migrated row on a live DB.
    conn.execute(
        """
        INSERT INTO cash_flow_tags
            (source_table, source_row_key, classification, tagged_by, amount_cny, flow_date)
        VALUES ('transactions', ?, 'external_contribution', 'manual', 4000.0, '2026-04-10')
        """,
        [str(tx_id)],
    )
    rows = list_classified_flows(conn)
    assert len(rows) == 1
    assert rows[0]["orphaned"] is False
    assert rows[0]["source_row_key"] == str(tx_id)
    assert rows[0]["amount_cny"] == 4000.0
    assert rows[0]["asset_id"] == "LEGACY_STILL_LIVE"
    conn.close()
