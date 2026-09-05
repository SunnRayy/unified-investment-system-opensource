"""Tests for WS-1 (Financial-Summary cash-flow classifier layer).

Plan: docs/plans/2026-07-20-fs-cash-flows-attribution.md. Financial-Summary
cash/deposit accounts (`source_system='Financial_Summary_Excel'`, asset_id
CASH_* or Wealth_CMB) are stored as monthly BALANCES in holdings — their
month-over-month deltas are real cash flows but invisible to the
transactions-only classifier. These tests cover fs_cash_flow_candidates()
delta computation and its wiring into the classifier surface
(list_unclassified_flows / list_classified_flows / tag_flow_manual /
contribution_metrics) in src/services/north_star_flows.py.

Uses an in-memory DuckDB initialized from the real schema.sql (never a bare,
schema-less connector — see CLAUDE.md Database Safety Rules).
"""
from __future__ import annotations

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.north_star_flows import (
    FS_CASH_FLOW_FLOOR_MONTH,
    FS_CASH_FLOW_MIN_CNY,
    contribution_metrics,
    fs_cash_flow_candidates,
    list_classified_flows,
    list_unclassified_flows,
    tag_flow_manual,
)


def _make_db() -> DatabaseConnector:
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    return conn


def _insert_fs_holding(
    conn, snapshot_date: str, asset_id: str, market_value: float, *, is_shadow: bool = False,
) -> None:
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, market_value, currency, source_system, is_shadow)
        VALUES (?, ?, ?, ?, 'CNY', 'Financial_Summary_Excel', ?)
        """,
        [snapshot_date, asset_id, asset_id, market_value, is_shadow],
    )


# ── Per-asset, per-month LATEST snapshot (never global MAX) ────────────────

def test_latest_snapshot_within_month_wins_not_earliest():
    """Two snapshots for the same asset in the same month: the delta must be
    computed from the LATEST (most recently restated) balance, not the
    earliest-in-month row. A naive 'first row per month' or global-MAX
    implementation would give a different (wrong) answer here."""
    conn = _make_db()
    _insert_fs_holding(conn, "2026-05-01", "CASH_A", 5000.0)
    _insert_fs_holding(conn, "2026-06-01", "CASH_A", 10000.0)   # early-month snapshot
    _insert_fs_holding(conn, "2026-06-20", "CASH_A", 12500.0)   # later restatement, same month

    candidates = fs_cash_flow_candidates(conn)
    key = ("fs_cash_delta", "fscash:CASH_A|2026-06")
    assert key in candidates
    # Correct: 12500 (latest June value) - 5000 (May baseline) = 7500.
    # Wrong-but-plausible answers this must NOT equal: 5000 (10000-5000, earliest-wins)
    # or 2500 (12500-10000, treating both June rows as separate deltas).
    assert candidates[key]["amount_cny"] == 7500.0
    conn.close()


def test_global_max_snapshot_date_would_be_wrong_across_assets():
    """CASH_B has a later global snapshot_date than CASH_A. A buggy
    implementation using a single global MAX(snapshot_date) filter (instead
    of per-asset, per-month) would silently drop CASH_A's June delta because
    CASH_A has no row on the global-latest date. The correct implementation
    computes each asset's own per-month latest independently."""
    conn = _make_db()
    _insert_fs_holding(conn, "2026-05-01", "CASH_A", 5000.0)
    _insert_fs_holding(conn, "2026-06-01", "CASH_A", 7000.0)
    _insert_fs_holding(conn, "2026-05-01", "CASH_B", 2000.0)
    _insert_fs_holding(conn, "2026-07-01", "CASH_B", 9000.0)   # later global date than any CASH_A row

    candidates = fs_cash_flow_candidates(conn)
    a_key = ("fs_cash_delta", "fscash:CASH_A|2026-06")
    b_key = ("fs_cash_delta", "fscash:CASH_B|2026-07")
    assert a_key in candidates, "CASH_A's June delta must not be dropped by CASH_B's later global date"
    assert candidates[a_key]["amount_cny"] == 2000.0  # 7000 - 5000
    assert b_key in candidates
    assert candidates[b_key]["amount_cny"] == 7000.0  # 9000 - 2000
    conn.close()


# ── Materiality threshold ───────────────────────────────────────────────────

def test_threshold_filters_small_delta_but_preserves_running_balance():
    """A delta below FS_CASH_FLOW_MIN_CNY is excluded from candidates, but
    the actual balance must still be used as the baseline for the NEXT
    month's delta (the filtered-out month is not treated as a gap)."""
    conn = _make_db()
    assert FS_CASH_FLOW_MIN_CNY == 1000.0
    _insert_fs_holding(conn, "2026-01-01", "CASH_C", 5000.0)
    _insert_fs_holding(conn, "2026-02-01", "CASH_C", 5500.0)   # delta=500, below threshold
    _insert_fs_holding(conn, "2026-03-01", "CASH_C", 8000.0)   # delta vs actual 5500 = 2500

    candidates = fs_cash_flow_candidates(conn)
    assert ("fs_cash_delta", "fscash:CASH_C|2026-01") in candidates
    assert candidates[("fs_cash_delta", "fscash:CASH_C|2026-01")]["amount_cny"] == 5000.0
    assert ("fs_cash_delta", "fscash:CASH_C|2026-02") not in candidates, "sub-threshold delta must be excluded"
    assert ("fs_cash_delta", "fscash:CASH_C|2026-03") in candidates
    # Must be computed against the REAL Feb balance (5500), not skip Feb entirely.
    assert candidates[("fs_cash_delta", "fscash:CASH_C|2026-03")]["amount_cny"] == 2500.0
    conn.close()


# ── Attribution-window floor (Fix 1, 2026-07-20 lead review) ──────────────
# fs_cash_flow_candidates() must bound EMITTED candidates to
# FS_CASH_FLOW_FLOOR_MONTH (kept in sync with attribution.HISTORY_FLOOR_MONTH)
# while still walking the FULL history to compute deltas — so the first
# in-window month's delta is against the prior REAL month's balance, not an
# implicit zero.

def test_floor_month_constant_is_2026_01():
    assert FS_CASH_FLOW_FLOOR_MONTH == "2026-01"


def test_pre_floor_months_are_not_emitted_but_still_seed_the_next_delta():
    conn = _make_db()
    _insert_fs_holding(conn, "2025-11-01", "CASH_FLOOR", 3000.0)
    _insert_fs_holding(conn, "2025-12-01", "CASH_FLOOR", 5000.0)
    _insert_fs_holding(conn, "2026-01-01", "CASH_FLOOR", 9000.0)

    candidates = fs_cash_flow_candidates(conn)

    # Pre-floor months must never be emitted, even though their deltas
    # (2000, 2000) both clear the materiality threshold.
    assert ("fs_cash_delta", "fscash:CASH_FLOOR|2025-11") not in candidates
    assert ("fs_cash_delta", "fscash:CASH_FLOOR|2025-12") not in candidates

    # The first in-window month's delta must be computed against Dec-2025's
    # REAL balance (5000), NOT an implicit zero baseline (which would give
    # 9000, the full Jan balance).
    key = ("fs_cash_delta", "fscash:CASH_FLOOR|2026-01")
    assert key in candidates
    assert candidates[key]["amount_cny"] == 4000.0

    # No pre-2026 month leaks into the surfaced set at all.
    assert all(
        row_key.rpartition("|")[2] >= "2026-01"
        for (_, row_key) in candidates.keys()
    )
    conn.close()


def test_floor_month_param_is_overridable():
    conn = _make_db()
    _insert_fs_holding(conn, "2025-11-01", "CASH_FLOOR2", 3000.0)
    _insert_fs_holding(conn, "2025-12-01", "CASH_FLOOR2", 6000.0)

    candidates = fs_cash_flow_candidates(conn, floor_month="2025-01")
    key = ("fs_cash_delta", "fscash:CASH_FLOOR2|2025-12")
    assert key in candidates
    assert candidates[key]["amount_cny"] == 3000.0
    conn.close()


# ── First-seen baseline ─────────────────────────────────────────────────────

def test_first_seen_month_delta_is_opening_balance():
    conn = _make_db()
    _insert_fs_holding(conn, "2026-04-01", "CASH_D", 20000.0)

    candidates = fs_cash_flow_candidates(conn)
    key = ("fs_cash_delta", "fscash:CASH_D|2026-04")
    assert key in candidates
    assert candidates[key]["amount_cny"] == 20000.0
    assert candidates[key]["asset_id"] == "CASH_D"
    assert candidates[key]["transaction_type"] == "cash_delta"
    conn.close()


# ── Scope predicate ──────────────────────────────────────────────────────────

def test_scope_excludes_property_and_pension_includes_cash_and_wealth_cmb():
    conn = _make_db()
    _insert_fs_holding(conn, "2026-01-01", "Property_House1", 2_000_000.0)
    _insert_fs_holding(conn, "2026-02-01", "Property_House1", 2_100_000.0)   # material delta, out of scope
    _insert_fs_holding(conn, "2026-01-01", "Pension_Personal", 100_000.0)
    _insert_fs_holding(conn, "2026-02-01", "Pension_Personal", 105_000.0)    # material delta, out of scope
    _insert_fs_holding(conn, "2026-01-01", "CASH_SAVINGS", 30_000.0)
    _insert_fs_holding(conn, "2026-01-01", "Wealth_CMB", 15_000.0)

    candidates = fs_cash_flow_candidates(conn)
    asset_ids_in_scope = {info["asset_id"] for info in candidates.values()}
    assert "Property_House1" not in asset_ids_in_scope
    assert "Pension_Personal" not in asset_ids_in_scope
    assert "CASH_SAVINGS" in asset_ids_in_scope
    assert "Wealth_CMB" in asset_ids_in_scope
    conn.close()


# ── is_shadow=TRUE rows must still be counted ───────────────────────────────

def test_is_shadow_true_rows_are_not_filtered():
    """FS monthly history is is_shadow=TRUE by design (superseded snapshot,
    not invalid) — fs_cash_flow_candidates must NOT filter on is_shadow."""
    conn = _make_db()
    _insert_fs_holding(conn, "2026-01-01", "CASH_SHADOW", 10000.0, is_shadow=True)
    _insert_fs_holding(conn, "2026-02-01", "CASH_SHADOW", 13000.0, is_shadow=True)

    candidates = fs_cash_flow_candidates(conn)
    key = ("fs_cash_delta", "fscash:CASH_SHADOW|2026-02")
    assert key in candidates
    assert candidates[key]["amount_cny"] == 3000.0
    conn.close()


# ── tag_flow_manual: fs_cash_delta branch ───────────────────────────────────

def test_tag_flow_manual_fs_cash_external_contribution_stores_delta():
    conn = _make_db()
    _insert_fs_holding(conn, "2026-05-01", "CASH_SAVINGS", 10000.0)
    _insert_fs_holding(conn, "2026-06-01", "CASH_SAVINGS", 25000.0)

    key = "fscash:CASH_SAVINGS|2026-06"
    result = tag_flow_manual(conn, "fs_cash_delta", key, "external_contribution", note="salary top-up")
    assert result["classification"] == "external_contribution"
    assert result["amount_cny"] == 15000.0

    row = conn.execute(
        "SELECT amount_cny, classification, tagged_by, source_table FROM cash_flow_tags WHERE source_row_key = ?",
        [key],
    ).fetchone()
    assert row == (15000.0, "external_contribution", "manual", "fs_cash_delta")
    conn.close()


def test_tag_flow_manual_fs_cash_internal_transfer_stores_zero():
    conn = _make_db()
    _insert_fs_holding(conn, "2026-05-01", "CASH_SAVINGS", 10000.0)
    _insert_fs_holding(conn, "2026-06-01", "CASH_SAVINGS", 25000.0)

    key = "fscash:CASH_SAVINGS|2026-06"
    result = tag_flow_manual(conn, "fs_cash_delta", key, "internal_transfer", note="moved from Wealth_CMB")
    assert result["amount_cny"] == 0.0

    row = conn.execute(
        "SELECT amount_cny FROM cash_flow_tags WHERE source_row_key = ?", [key]
    ).fetchone()
    assert float(row[0]) == 0.0
    conn.close()


def test_tag_flow_manual_fs_cash_unknown_key_raises_lookup_error():
    """A key with no matching candidate (never existed, or below threshold) raises LookupError."""
    conn = _make_db()
    with pytest.raises(LookupError):
        tag_flow_manual(conn, "fs_cash_delta", "fscash:CASH_NOPE|2026-06", "external_contribution")
    conn.close()


def test_tag_flow_manual_fs_cash_malformed_key_raises_lookup_error():
    conn = _make_db()
    with pytest.raises(LookupError):
        tag_flow_manual(conn, "fs_cash_delta", "not-a-well-formed-key", "external_contribution")
    conn.close()


def test_tag_flow_manual_invalid_source_table_error_mentions_fs_cash_delta():
    conn = _make_db()
    _insert_fs_holding(conn, "2026-06-01", "CASH_SAVINGS", 1000.0)
    with pytest.raises(ValueError, match="fs_cash_delta"):
        tag_flow_manual(conn, "not_a_real_table", "whatever", "external_contribution")
    conn.close()


# ── unclassified / classified round-trip ────────────────────────────────────

def test_unclassified_flows_includes_fs_cash_candidate():
    conn = _make_db()
    _insert_fs_holding(conn, "2026-05-01", "CASH_SAVINGS", 10000.0)
    _insert_fs_holding(conn, "2026-06-01", "CASH_SAVINGS", 25000.0)

    unclassified = list_unclassified_flows(conn)
    fs_rows = {r["source_row_key"]: r for r in unclassified if r["source_table"] == "fs_cash_delta"}
    # Both the May baseline (first-seen delta=10000) and the June delta
    # (15000) are material candidates — the classifier surfaces every
    # untagged candidate, not just the one the test cares about.
    assert set(fs_rows.keys()) == {"fscash:CASH_SAVINGS|2026-05", "fscash:CASH_SAVINGS|2026-06"}
    june_row = fs_rows["fscash:CASH_SAVINGS|2026-06"]
    assert june_row["amount_cny"] == 15000.0
    assert june_row["asset_id"] == "CASH_SAVINGS"
    conn.close()


def test_tagging_fs_cash_moves_it_from_unclassified_to_classified():
    conn = _make_db()
    _insert_fs_holding(conn, "2026-05-01", "CASH_SAVINGS", 10000.0)
    _insert_fs_holding(conn, "2026-06-01", "CASH_SAVINGS", 25000.0)
    key = "fscash:CASH_SAVINGS|2026-06"

    # Before tagging: appears in unclassified, not in classified.
    assert any(r["source_row_key"] == key for r in list_unclassified_flows(conn))
    assert not any(r["source_row_key"] == key for r in list_classified_flows(conn))

    tag_flow_manual(conn, "fs_cash_delta", key, "external_contribution", note="savings")

    # After tagging: gone from unclassified, present in classified with correct fields.
    assert not any(r["source_row_key"] == key for r in list_unclassified_flows(conn))
    classified = [r for r in list_classified_flows(conn) if r["source_row_key"] == key]
    assert len(classified) == 1
    row = classified[0]
    assert row["source_table"] == "fs_cash_delta"
    assert row["classification"] == "external_contribution"
    assert row["amount_cny"] == 15000.0
    assert row["asset_id"] == "CASH_SAVINGS"
    assert row["transaction_type"] == "cash_delta"
    assert row["orphaned"] is False
    conn.close()


def test_list_classified_flows_filter_by_classification_includes_fs_cash():
    conn = _make_db()
    _insert_fs_holding(conn, "2026-05-01", "CASH_SAVINGS", 10000.0)
    _insert_fs_holding(conn, "2026-06-01", "CASH_SAVINGS", 25000.0)
    key = "fscash:CASH_SAVINGS|2026-06"
    tag_flow_manual(conn, "fs_cash_delta", key, "external_contribution")

    ec_rows = list_classified_flows(conn, classification="external_contribution")
    assert any(r["source_row_key"] == key for r in ec_rows)

    it_rows = list_classified_flows(conn, classification="internal_transfer")
    assert not any(r["source_row_key"] == key for r in it_rows)
    conn.close()


# ── contribution_metrics ─────────────────────────────────────────────────────

def test_contribution_metrics_includes_tagged_fs_cash_external_contribution():
    from datetime import date

    conn = _make_db()
    today = date.today()
    this_month = today.replace(day=1)
    prev_month_val = this_month.month - 1 or 12
    prev_year = this_month.year if this_month.month != 1 else this_month.year - 1
    prev_month = this_month.replace(year=prev_year, month=prev_month_val)

    _insert_fs_holding(conn, prev_month.isoformat(), "CASH_SAVINGS", 10000.0)
    _insert_fs_holding(conn, this_month.isoformat(), "CASH_SAVINGS", 40000.0)  # delta = 30000

    # prev_month is itself a first-seen candidate (baseline delta=10000);
    # tag both so the only remaining variable in unclassified_count is what
    # this test controls.
    prev_key = f"fscash:CASH_SAVINGS|{prev_month.strftime('%Y-%m')}"
    tag_flow_manual(conn, "fs_cash_delta", prev_key, "external_contribution", note="baseline")

    this_key = f"fscash:CASH_SAVINGS|{this_month.strftime('%Y-%m')}"
    tag_flow_manual(conn, "fs_cash_delta", this_key, "external_contribution", note="savings")

    metrics = contribution_metrics(conn)
    assert metrics["ytd_sum"] >= 30000.0
    assert metrics["trailing_12m_sum"] >= 30000.0
    assert metrics["unclassified_count"] == 0
    conn.close()


def test_contribution_metrics_excludes_untagged_fs_cash_from_sum_but_counts_unclassified():
    conn = _make_db()
    _insert_fs_holding(conn, "2026-05-01", "CASH_SAVINGS", 10000.0)
    _insert_fs_holding(conn, "2026-06-01", "CASH_SAVINGS", 25000.0)  # untagged

    metrics = contribution_metrics(conn)
    # Untagged FS-cash deltas are candidates (both months clear the ¥1000
    # threshold) but must not be summed into ytd/trailing until tagged.
    assert metrics["unclassified_count"] >= 1
    conn.close()
