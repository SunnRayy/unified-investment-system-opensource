"""Tests for WS-2 (attribution consumes FS-cash cash_flow_tags).

Plan: docs/plans/2026-07-20-fs-cash-flows-attribution.md. WS-1
(src/services/north_star_flows.py, already merged) turns Financial-Summary
cash/deposit month-over-month balance deltas into taggable flow candidates
(cash_flow_tags.source_table='fs_cash_delta', key
`fscash:{asset_id}|{YYYY-MM}`). WS-2 makes compute_month_raw() CONSUME those
tags: a tagged FS-cash month's residual moves into transfer_effect
(external_contribution/internal_transfer) or income_effect
(income_reinvested), and dq_flag clears. An untagged FS-cash month is left
exactly as any other residual (still flagged), with `_derive_dq_reason`
steering the owner to the classification page.

Uses an in-memory DuckDB via initialize_schema + run_migrations (never a
bare, schema-less connector — CLAUDE.md Database Safety Rules).
"""
from __future__ import annotations

from datetime import date

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.attribution import (
    _derive_dq_reason,
    compute_month,
    compute_month_raw,
    get_summary,
)
from src.services.north_star_flows import _compose_fs_cash_key


def _make_db() -> DatabaseConnector:
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    conn.run_migrations()
    return conn


def _insert_asset(conn, asset_id, name="Test Asset", asset_class="Cash"):
    conn.execute(
        "INSERT INTO asset_registry (canonical_id, display_name, asset_class) VALUES (?, ?, ?)",
        [asset_id, name, asset_class],
    )


def _insert_fs_holding(conn, asset_id, snap_date, mv, is_shadow=False):
    """FS-cash monthly balance row — mirrors tests/services/test_fs_cash_flows.py
    (no quantity/price: FS stores only the CNY balance)."""
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, market_value, currency, source_system, is_shadow)
        VALUES (?, ?, ?, ?, 'CNY', 'Financial_Summary_Excel', ?)
        """,
        [snap_date, asset_id, asset_id, mv, is_shadow],
    )


def _insert_holding(conn, asset_id, snap_date, qty, price, mv, currency="CNY",
                    is_shadow=False, source_system="test"):
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, quantity, market_price_unit,
             market_value, currency, source_system, is_shadow)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [snap_date, asset_id, asset_id, qty, price, mv, currency, source_system, is_shadow],
    )


def _tag_fs_cash(conn, asset_id, year_month, classification, amount_cny=0.0, flow_date=None):
    key = _compose_fs_cash_key(asset_id, year_month)
    conn.execute(
        """
        INSERT INTO cash_flow_tags
            (source_table, source_row_key, classification, tagged_by, amount_cny, flow_date)
        VALUES ('fs_cash_delta', ?, ?, 'manual', ?, ?)
        """,
        [key, classification, amount_cny, flow_date],
    )


@pytest.fixture
def db():
    conn = _make_db()
    yield conn
    conn.close()


def _assert_waterfall_identity(row):
    """delta == price + trade + transfer + income + residual must hold
    exactly, even after the FS-cash residual-absorption adjustment — moving
    residual INTO a bucket must not change the total explained sum."""
    delta = row["mv_end"] - row["mv_start"]
    explained = (
        row["price_effect"] + row["trade_effect"] + row["transfer_effect"]
        + row["income_effect"] + row["residual"]
    )
    assert explained == pytest.approx(delta)


def test_tagged_fs_cash_external_contribution_absorbs_residual_into_transfer(db):
    _insert_fs_holding(db, "CASH_EXT", "2026-05-31", 10000.0, is_shadow=True)
    _insert_fs_holding(db, "CASH_EXT", "2026-06-30", 25000.0)
    _tag_fs_cash(db, "CASH_EXT", "2026-06", "external_contribution",
                 amount_cny=15000.0, flow_date="2026-06-30")

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "CASH_EXT")

    assert row["mv_start"] == 10000.0
    assert row["mv_end"] == 25000.0
    assert row["transfer_effect"] == pytest.approx(15000.0)
    assert row["income_effect"] == 0.0
    assert row["residual"] == 0.0
    assert row["dq_flag"] is False
    assert row["source_transition"] is False
    _assert_waterfall_identity(row)


def test_tagged_fs_cash_internal_transfer_absorbs_residual_into_transfer(db):
    """internal_transfer has the same per-asset effect as external_contribution
    — the money still physically moved in/out of this specific account."""
    _insert_fs_holding(db, "CASH_XFER", "2026-05-31", 10000.0, is_shadow=True)
    _insert_fs_holding(db, "CASH_XFER", "2026-06-30", 25000.0)
    _tag_fs_cash(db, "CASH_XFER", "2026-06", "internal_transfer",
                 amount_cny=0.0, flow_date="2026-06-30")

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "CASH_XFER")

    assert row["transfer_effect"] == pytest.approx(15000.0)
    assert row["income_effect"] == 0.0
    assert row["residual"] == 0.0
    assert row["dq_flag"] is False
    _assert_waterfall_identity(row)


def test_tagged_fs_cash_income_reinvested_absorbs_residual_into_income(db):
    _insert_fs_holding(db, "CASH_INC", "2026-05-31", 8000.0, is_shadow=True)
    _insert_fs_holding(db, "CASH_INC", "2026-06-30", 10000.0)
    _tag_fs_cash(db, "CASH_INC", "2026-06", "income_reinvested",
                 amount_cny=2000.0, flow_date="2026-06-30")

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "CASH_INC")

    assert row["income_effect"] == pytest.approx(2000.0)
    assert row["transfer_effect"] == 0.0
    assert row["residual"] == 0.0
    assert row["dq_flag"] is False
    _assert_waterfall_identity(row)


def test_untagged_fs_cash_leaves_residual_and_flag_with_new_dq_reason(db):
    """No tag -> unchanged: residual stays, dq_flag stays True, and
    _derive_dq_reason returns the fs_cash_untagged reason (not the generic
    'unexplained residual' fallback)."""
    _insert_fs_holding(db, "CASH_UNTAGGED", "2026-05-31", 10000.0, is_shadow=True)
    _insert_fs_holding(db, "CASH_UNTAGGED", "2026-06-30", 25000.0)

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "CASH_UNTAGGED")

    assert row["residual"] == pytest.approx(15000.0)
    assert row["transfer_effect"] == 0.0
    assert row["income_effect"] == 0.0
    assert row["dq_flag"] is True

    reason = _derive_dq_reason(db, "CASH_UNTAGGED", date(2026, 6, 1))
    assert reason["dq_detail"]["kind"] == "fs_cash_untagged"
    assert reason["dq_detail"]["asset_id"] == "CASH_UNTAGGED"
    assert "现金余额变动未分类" in reason["dq_reason"]


def test_untagged_fs_cash_with_source_transition_prefers_fs_cash_untagged_reason(db):
    """Fix 2 (2026-07-20 lead review): an FS-cash asset whose start/end
    boundary rows ALSO trip the tier-mismatch source_transition guard (e.g. a
    legacy PIS -> Financial_Summary_Excel handoff) must still get the
    actionable fs_cash_untagged message, not '估值来源变更 (PIS→reader)'. Any
    in-scope FS-cash asset reaching _derive_dq_reason is by construction
    untagged (a tagged month never gets this far — compute_month_raw clears
    dq_flag), so fs_cash_untagged is always the right message for it and must
    win over source_transition."""
    _insert_holding(db, "CASH_TIER", "2026-05-31", 0, None, 10000.0,
                     is_shadow=True, source_system="PIS_Excel")
    _insert_holding(db, "CASH_TIER", "2026-06-30", 0, None, 25000.0,
                     is_shadow=False, source_system="Financial_Summary_Excel")

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "CASH_TIER")
    # Confirm the guard actually fires here — otherwise this test would prove
    # nothing about ordering.
    assert row["source_transition"] is True
    assert row["dq_flag"] is True

    reason = _derive_dq_reason(db, "CASH_TIER", date(2026, 6, 1))
    assert reason["dq_detail"]["kind"] == "fs_cash_untagged"
    assert reason["dq_detail"]["kind"] != "source_transition"
    assert "现金余额变动未分类" in reason["dq_reason"]


def test_non_fs_asset_with_same_shaped_residual_is_untouched(db):
    """A regular (non-FS-cash) asset with an unexplained qty jump must NOT be
    touched by the FS-cash absorption logic — even when a cash_flow_tags row
    exists under the matching fscash: key, proving the guard (_is_fs_cash_asset)
    actually gates the lookup rather than the tag simply being absent."""
    _insert_asset(db, "TEST_NONFS", asset_class="Equity")
    _insert_holding(db, "TEST_NONFS", "2026-05-31", 100, 10, 1000, is_shadow=True)
    # Phantom qty jump, no transaction -> forces a residual (same DQ shape as
    # test_dq_flag_set_when_residual_exceeds_threshold in test_attribution.py).
    _insert_holding(db, "TEST_NONFS", "2026-06-30", 150, 11, 1650)
    _tag_fs_cash(db, "TEST_NONFS", "2026-06", "external_contribution",
                 amount_cny=550.0, flow_date="2026-06-30")

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "TEST_NONFS")

    assert row["residual"] == pytest.approx(550.0)
    assert row["transfer_effect"] == 0.0
    assert row["dq_flag"] is True


def test_get_summary_net_flows_includes_tagged_fs_cash_external_contribution(db):
    """Net Flows (get_summary) sums cash_flow_tags across ALL source_tables
    by flow_date — no change needed for WS-2, this just verifies the plan's
    'VERIFY (don't change unless broken)' claim with a real tagged FS-cash row."""
    _insert_fs_holding(db, "CASH_SUMMARY", "2026-05-31", 10000.0, is_shadow=True)
    _insert_fs_holding(db, "CASH_SUMMARY", "2026-06-30", 25000.0)
    _tag_fs_cash(db, "CASH_SUMMARY", "2026-06", "external_contribution",
                 amount_cny=15000.0, flow_date="2026-06-15")

    compute_month(db, date(2026, 6, 1))

    result = get_summary(db, months=3)
    june = next(m for m in result["months"] if m["month"] == "2026-06")
    assert june["flows"] is not None
    assert june["flows"]["external_in"] == pytest.approx(15000.0)
    assert june["flows"]["net_external"] == pytest.approx(15000.0)
