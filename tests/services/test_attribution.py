"""Tests for src/services/attribution.py (Attribution & Flows Program WS-1).

In-memory DuckDB via initialize_schema + run_migrations (never a bare,
schema-less connector; never the real production DB).
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.attribution import (
    compute_month,
    compute_month_raw,
    get_asset_history,
    get_monthly,
    get_summary,
)


def _make_db() -> DatabaseConnector:
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    conn.run_migrations()
    return conn


def _insert_asset(conn, asset_id, name="Test Asset", asset_class="Equity"):
    conn.execute(
        "INSERT INTO asset_registry (canonical_id, display_name, asset_class) VALUES (?, ?, ?)",
        [asset_id, name, asset_class],
    )


def _insert_holding(conn, asset_id, snap_date, qty, price, mv, currency="CNY",
                    is_shadow=False, source_system="test"):
    # Production reality: OLD reader rows are flagged is_shadow=TRUE when a
    # newer snapshot supersedes them (current-state flag, not time-versioning).
    # Historical fixtures below therefore mark prior-month rows shadow to
    # prove the valuation helper does NOT filter on is_shadow.
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, quantity, market_price_unit,
             market_value, currency, source_system, is_shadow)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [snap_date, asset_id, asset_id, qty, price, mv, currency, source_system, is_shadow],
    )


def _insert_tx(conn, tx_date, asset_id, ttype, qty, price_unit, amount_net, currency="CNY"):
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, quantity,
             price_unit, amount_net, currency, source_system, is_provisional)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'test', FALSE)
        """,
        [tx_date, asset_id, asset_id, ttype, qty, price_unit, amount_net, currency],
    )


@pytest.fixture
def db():
    conn = _make_db()
    yield conn
    conn.close()


def test_buy_and_price_reval_no_residual(db):
    """qty change + price change -> price_effect + trade_effect explain delta, residual ~= 0."""
    _insert_asset(db, "TEST_STK")
    _insert_holding(db, "TEST_STK", "2026-05-31", 100, 10, 1000, is_shadow=True)
    _insert_tx(db, "2026-06-10", "TEST_STK", "buy", 20, 11, 220)
    _insert_holding(db, "TEST_STK", "2026-06-30", 120, 12, 1440)

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "TEST_STK")

    assert row["mv_start"] == 1000
    assert row["mv_end"] == 1440
    assert row["price_effect"] == pytest.approx(220.0)  # 100*(12-10) + 20*(12-11)
    assert row["trade_effect"] == pytest.approx(220.0)
    assert row["transfer_effect"] == 0
    assert row["income_effect"] == 0
    assert abs(row["residual"]) < 0.01
    assert row["dq_flag"] is False


def test_transfer_pair_nets_to_zero_at_total_level(db):
    """ACAT transfer_out (amount=0) from one asset + transfer_in to another
    must net to ~0 in total price/transfer effects across the two legs."""
    _insert_asset(db, "TEST_XFER_OUT")
    _insert_asset(db, "TEST_XFER_IN")
    _insert_holding(db, "TEST_XFER_OUT", "2026-05-31", 50, 20, 1000, is_shadow=True)
    _insert_tx(db, "2026-06-05", "TEST_XFER_OUT", "transfer_out", 50, 20, 0)
    _insert_holding(db, "TEST_XFER_OUT", "2026-06-30", 0, 20, 0)

    _insert_tx(db, "2026-06-05", "TEST_XFER_IN", "transfer_in", 50, 20, 0)
    _insert_holding(db, "TEST_XFER_IN", "2026-06-30", 50, 20, 1000)

    rows = compute_month_raw(db, date(2026, 6, 1))
    row_out = next(r for r in rows if r["asset_id"] == "TEST_XFER_OUT")
    row_in = next(r for r in rows if r["asset_id"] == "TEST_XFER_IN")

    assert row_out["transfer_effect"] == pytest.approx(-1000.0)
    assert row_in["transfer_effect"] == pytest.approx(1000.0)
    assert abs(row_out["residual"]) < 0.01
    assert abs(row_in["residual"]) < 0.01
    total_delta = (row_out["mv_end"] - row_out["mv_start"]) + (row_in["mv_end"] - row_in["mv_start"])
    total_transfer = row_out["transfer_effect"] + row_in["transfer_effect"]
    assert total_delta == pytest.approx(0.0)
    assert total_transfer == pytest.approx(0.0)


def test_vest_income_and_reval_no_residual(db):
    """A vest event contributes income_effect at vest price PLUS a price_effect
    reval term to bring the vested shares up to month-end price."""
    _insert_asset(db, "TEST_RSU")
    _insert_holding(db, "TEST_RSU", "2026-05-31", 10, 100, 1000, is_shadow=True)
    _insert_tx(db, "2026-06-15", "TEST_RSU", "vest", 5, 110, 0)
    _insert_holding(db, "TEST_RSU", "2026-06-30", 15, 120, 1800)

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "TEST_RSU")

    assert row["mv_start"] == 1000
    assert row["mv_end"] == 1800
    assert row["income_effect"] == pytest.approx(550.0)  # 5 * 110
    assert row["price_effect"] == pytest.approx(250.0)  # 10*(120-100) + 5*(120-110)
    assert abs(row["residual"]) < 0.01
    assert row["dq_flag"] is False


def test_dq_flag_set_when_residual_exceeds_threshold(db):
    """A quantity jump with NO matching transaction (phantom qty, e.g. an
    untracked buy/transfer) must set dq_flag.

    Note (Item D, 2026-07-20): price_effect now uses IMPLIED prices
    (mv/qty), so at constant quantity ANY mv change is — by construction —
    fully explained as a price move (mv = qty x price, so a qty-constant mv
    delta literally IS a price delta). A same-source, same-qty mv jump with
    a stale market_price_unit field (the old fixture here) is therefore no
    longer a meaningful dq case; the residual-producing case is a quantity
    change with no transaction to explain it.
    """
    _insert_asset(db, "TEST_DQ")
    _insert_holding(db, "TEST_DQ", "2026-05-31", 100, 10, 1000, is_shadow=True)
    # No transactions at all this month, but the June snapshot shows +50 qty
    # with only a small price move — the extra 50 shares' value (~¥550) is
    # unexplained (no buy/transfer_in transaction), so residual must exceed
    # both the 1% and the ¥500 floor.
    _insert_holding(db, "TEST_DQ", "2026-06-30", 150, 11, 1650)

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "TEST_DQ")

    # price_effect = qty_start * (p_end_implied - p_start_implied) = 100 * (11 - 10) = 100
    assert row["price_effect"] == pytest.approx(100.0)
    assert (row["mv_end"] - row["mv_start"]) == pytest.approx(650.0)
    assert row["residual"] == pytest.approx(550.0)
    assert row["dq_flag"] is True


def test_compute_month_persists_and_is_idempotent(db):
    """compute_month() deletes + rewrites the month partition; running twice
    must not duplicate rows or change results."""
    _insert_asset(db, "TEST_STK")
    _insert_holding(db, "TEST_STK", "2026-05-31", 100, 10, 1000, is_shadow=True)
    _insert_tx(db, "2026-06-10", "TEST_STK", "buy", 20, 11, 220)
    _insert_holding(db, "TEST_STK", "2026-06-30", 120, 12, 1440)

    summary1 = compute_month(db, date(2026, 6, 1))
    summary2 = compute_month(db, date(2026, 6, 1))
    assert summary1["rows"] == summary2["rows"]

    count = db.execute(
        "SELECT COUNT(*) FROM attribution_monthly WHERE month = DATE '2026-06-01' AND asset_id = 'TEST_STK'"
    ).fetchone()[0]
    assert count == 1


def test_get_monthly_rollup_sub_class(db):
    _insert_asset(db, "TEST_STK", asset_class="Equity")
    _insert_holding(db, "TEST_STK", "2026-05-31", 100, 10, 1000, is_shadow=True)
    _insert_tx(db, "2026-06-10", "TEST_STK", "buy", 20, 11, 220)
    _insert_holding(db, "TEST_STK", "2026-06-30", 120, 12, 1440)
    compute_month(db, date(2026, 6, 1))

    result = get_monthly(db, date(2026, 6, 1), level="sub_class")
    assert result["month"] == "2026-06"
    assert result["totals"]["delta"] == pytest.approx(440.0)
    assert len(result["rows"]) >= 1
    assert result["dq_flagged_assets"] == []


def test_get_monthly_asset_level_includes_sub_and_top_class(db):
    """level=asset rows must carry sub_class + top_class (frontend filters
    drill-down client-side on sub_class)."""
    _insert_asset(db, "TEST_STK", asset_class="Equity")
    _insert_holding(db, "TEST_STK", "2026-05-31", 100, 10, 1000, is_shadow=True)
    _insert_holding(db, "TEST_STK", "2026-06-30", 100, 12, 1200)
    compute_month(db, date(2026, 6, 1))

    result = get_monthly(db, date(2026, 6, 1), level="asset")
    row = next(r for r in result["rows"] if r["asset_id"] == "TEST_STK")
    assert "sub_class" in row and row["sub_class"] is not None
    assert "top_class" in row and row["top_class"] is not None


# ── Tiered valuation rule (2026-07-19 lead review lock) ────────────────────

def test_valuation_uses_shadow_history_rows(db):
    """A long-held asset whose ONLY May row is is_shadow=TRUE (superseded
    reader row) must still get mv_start from that row — never phantom 0."""
    _insert_asset(db, "TEST_SHADOW_HIST")
    _insert_holding(db, "TEST_SHADOW_HIST", "2026-05-22", 100, 10, 1000,
                    is_shadow=True, source_system="Schwab_CSV")
    _insert_holding(db, "TEST_SHADOW_HIST", "2026-06-26", 100, 11, 1100,
                    source_system="Schwab_CSV")

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "TEST_SHADOW_HIST")
    assert row["mv_start"] == 1000  # NOT 0 — shadow row is history, not absence
    assert row["mv_end"] == 1100
    assert row["price_effect"] == pytest.approx(100.0)
    assert abs(row["residual"]) < 0.01


def test_valuation_consolidated_row_wins_over_broker_rows(db):
    """Tier (a): when a Consolidated row exists at d*, use it ONLY — broker
    rows at the same date are its merged constituents (would double count)."""
    _insert_asset(db, "TEST_COAUTH")
    _insert_holding(db, "TEST_COAUTH", "2026-06-26", 30, 10, 300,
                    is_shadow=True, source_system="Schwab_CSV")
    _insert_holding(db, "TEST_COAUTH", "2026-06-26", 20, 10, 200,
                    is_shadow=True, source_system="Broker_IBKR")
    _insert_holding(db, "TEST_COAUTH", "2026-06-26", 50, 10, 500,
                    source_system="Consolidated")

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "TEST_COAUTH")
    assert row["mv_end"] == 500  # Consolidated only, not 500+300+200


def test_valuation_excludes_pis_baseline_when_reader_present(db):
    """Tier (b): at a d* where both a PIS baseline row and a reader row exist,
    only the reader row counts (ADR-003 — readers are authority)."""
    _insert_asset(db, "TEST_PIS_MIX")
    _insert_holding(db, "TEST_PIS_MIX", "2026-05-31", 100, 10, 1000,
                    is_shadow=True, source_system="PIS")
    _insert_holding(db, "TEST_PIS_MIX", "2026-05-31", 100, 10, 1000,
                    is_shadow=True, source_system="CN_Fund_Excel")
    _insert_holding(db, "TEST_PIS_MIX", "2026-06-30", 100, 11, 1100,
                    source_system="CN_Fund_Excel")

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "TEST_PIS_MIX")
    assert row["mv_start"] == 1000  # reader row only, not 2000


def test_valuation_pis_only_history_is_the_floor(db):
    """Tier (b) fallback: when ONLY baseline sources exist at d* (early
    history), use them — that IS the history floor."""
    _insert_asset(db, "TEST_PIS_ONLY")
    _insert_holding(db, "TEST_PIS_ONLY", "2026-05-31", 100, 10, 1000,
                    is_shadow=True, source_system="PIS_Historical")
    _insert_holding(db, "TEST_PIS_ONLY", "2026-06-30", 100, 11, 1100,
                    source_system="CN_Fund_Excel")

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "TEST_PIS_ONLY")
    assert row["mv_start"] == 1000  # baseline used when it's all there is


# ── LOCKED VALUATION v2 (2026-07-20) ────────────────────────────────────────
# Fixes the v1 single-asset-wide-d* bug: one source's zero-qty tombstone with
# a LATER snapshot_date than another source's real row used to win the
# asset-wide MAX(snapshot_date) and zero the whole asset (production case:
# Schwab US_STK_VOO 2026-06-26 tombstone postdating Broker_IBKR's real
# 2026-06-25 row, mislabeling ~-206.6K of June price_effect). v2 takes each
# source's OWN latest row <= as_of_date independently, so a tombstone on one
# source only zeroes that source's own contribution.

def test_valuation_tombstone_on_one_source_does_not_zero_asset(db):
    """Source A's later-dated zero-qty tombstone must not hide source B's
    real, earlier-dated row — each source gets its own d*, summed together."""
    _insert_asset(db, "TEST_TOMBSTONE")
    _insert_holding(db, "TEST_TOMBSTONE", "2026-06-25", 500, 194.2, 97100,
                    source_system="Broker_IBKR")
    _insert_holding(db, "TEST_TOMBSTONE", "2026-06-26", 0, 0, 0,
                    source_system="Schwab_CSV")

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "TEST_TOMBSTONE")
    assert row["mv_end"] == 97100  # IBKR's real value, NOT zeroed by Schwab's tombstone


def test_valuation_consolidated_drops_broker_real_rows_and_tombstones(db):
    """When a Consolidated row is present at d*, BOTH a co-authority broker's
    real (non-zero) row AND another broker's tombstone (zero) are dropped —
    not summed in, and not allowed to (re-)zero the Consolidated total."""
    _insert_asset(db, "TEST_COAUTH2")
    _insert_holding(db, "TEST_COAUTH2", "2026-06-20", 30, 10, 300,
                    source_system="Schwab_CSV")  # real broker row, own d*
    _insert_holding(db, "TEST_COAUTH2", "2026-06-26", 0, 0, 0,
                    source_system="Broker_IBKR")  # broker tombstone, own d*
    _insert_holding(db, "TEST_COAUTH2", "2026-06-26", 50, 10, 500,
                    source_system="Consolidated")

    rows = compute_month_raw(db, date(2026, 6, 1))
    row = next(r for r in rows if r["asset_id"] == "TEST_COAUTH2")
    assert row["mv_end"] == 500  # Consolidated only — not 500+300, not 500+0


# ── Item A: dq_reason explainability (2026-07-20 owner round-2 review) ─────
# Verified case: 纸黄金 June — mv_end from a Gold_Excel row that predates two
# ¥20,000 buys; the residual is exactly those missing buys. Computed at READ
# time in get_monthly/get_asset_history — no schema change.

def test_dq_reason_snapshot_lag(db):
    """mv_end's own snapshot predates in-month transactions -> 'snapshot_lag'
    with the transaction count/sum surfaced (matches the 纸黄金 case)."""
    _insert_asset(db, "TEST_GOLD", asset_class="Alternatives")
    _insert_holding(db, "TEST_GOLD", "2026-05-31", 100, 100, 10000, is_shadow=True)
    # Excel snapshot lags — dated mid-month, before two buys later in June.
    _insert_holding(db, "TEST_GOLD", "2026-06-15", 100, 100, 10000)
    _insert_tx(db, "2026-06-24", "TEST_GOLD", "buy", 100, 100, 10000)
    _insert_tx(db, "2026-06-30", "TEST_GOLD", "buy", 100, 100, 10000)

    compute_month(db, date(2026, 6, 1))
    result = get_monthly(db, date(2026, 6, 1), level="asset")
    row = next(r for r in result["rows"] if r["asset_id"] == "TEST_GOLD")

    assert row["dq_flag"] is True
    assert row["dq_reason"] is not None
    assert "早于" in row["dq_reason"]
    assert "2 笔交易" in row["dq_reason"]
    assert row["dq_detail"]["kind"] == "snapshot_lag"
    assert row["dq_detail"]["snapshot_end_date"] == "2026-06-15"
    assert row["dq_detail"]["post_snapshot_tx_count"] == 2
    assert row["dq_detail"]["post_snapshot_tx_sum"] == pytest.approx(20000.0)


def test_dq_reason_first_seen(db):
    """No snapshot <= month start -> 'first_seen', mv_start=0 is a true
    absence, not a zero-value snapshot."""
    _insert_asset(db, "TEST_NEW")
    _insert_holding(db, "TEST_NEW", "2026-06-20", 100, 50, 5000)

    compute_month(db, date(2026, 6, 1))
    result = get_monthly(db, date(2026, 6, 1), level="asset")
    row = next(r for r in result["rows"] if r["asset_id"] == "TEST_NEW")

    assert row["mv_start"] == 0
    assert row["dq_flag"] is True
    assert row["dq_detail"]["kind"] == "first_seen"
    assert "首次出现" in row["dq_reason"]


def test_dq_reason_stale_end_snapshot(db):
    """d_end exists, no post-snapshot transactions, but is >7 days before
    month-end -> 'stale_end_snapshot' (distinct from snapshot_lag, which
    requires an actual missed transaction)."""
    _insert_asset(db, "TEST_STALE")
    _insert_holding(db, "TEST_STALE", "2026-05-31", 100, 10, 1000, is_shadow=True)
    # Phantom qty jump with no transaction, dated early in June (>7 days
    # before month-end) — forces a real residual without any transactions
    # that would otherwise be caught by the snapshot_lag check.
    _insert_holding(db, "TEST_STALE", "2026-06-05", 200, 10, 2000)

    compute_month(db, date(2026, 6, 1))
    result = get_monthly(db, date(2026, 6, 1), level="asset")
    row = next(r for r in result["rows"] if r["asset_id"] == "TEST_STALE")

    assert row["dq_flag"] is True
    assert row["dq_detail"]["kind"] == "stale_end_snapshot"
    assert row["dq_detail"]["snapshot_end_date"] == "2026-06-05"
    assert "距月末 >7 天" in row["dq_reason"]


def test_dq_reason_none_when_not_flagged(db):
    """Non-flagged rows carry dq_reason=None, dq_detail=None (present keys,
    null values — not omitted)."""
    _insert_asset(db, "TEST_CLEAN")
    _insert_holding(db, "TEST_CLEAN", "2026-05-31", 100, 10, 1000, is_shadow=True)
    _insert_holding(db, "TEST_CLEAN", "2026-06-30", 100, 10, 1000)

    compute_month(db, date(2026, 6, 1))
    result = get_monthly(db, date(2026, 6, 1), level="asset")
    row = next(r for r in result["rows"] if r["asset_id"] == "TEST_CLEAN")

    assert row["dq_flag"] is False
    assert row["dq_reason"] is None
    assert row["dq_detail"] is None


def test_dq_reason_in_asset_history(db):
    """get_asset_history() surfaces the same dq_reason for a flagged month."""
    _insert_asset(db, "TEST_GOLD_HIST", asset_class="Alternatives")
    _insert_holding(db, "TEST_GOLD_HIST", "2026-05-31", 100, 100, 10000, is_shadow=True)
    _insert_holding(db, "TEST_GOLD_HIST", "2026-06-15", 100, 100, 10000)
    _insert_tx(db, "2026-06-24", "TEST_GOLD_HIST", "buy", 100, 100, 10000)

    compute_month(db, date(2026, 6, 1))
    hist = get_asset_history(db, "TEST_GOLD_HIST", months=3)
    month0 = hist["months"][0]

    assert month0["dq_flag"] is True
    assert month0["dq_detail"]["kind"] == "snapshot_lag"
    assert month0["dq_detail"]["post_snapshot_tx_count"] == 1


# ── Item B: multi-month range aggregation on get_monthly (month_to) ────────

def test_get_monthly_range_asset_level_aggregates_mv_and_effects(db):
    """level=asset over [Jan, Feb]: mv_start = Jan's mv_start, mv_end = Feb's
    mv_end, effects summed, dq_flag = OR across months."""
    _insert_asset(db, "TEST_RANGE", asset_class="Equity")
    _insert_holding(db, "TEST_RANGE", "2025-12-31", 100, 10, 1000, is_shadow=True)
    _insert_tx(db, "2026-01-10", "TEST_RANGE", "buy", 20, 11, 220)
    _insert_holding(db, "TEST_RANGE", "2026-01-31", 120, 12, 1440)
    # Feb: phantom +50 qty with no transaction -> dq_flag True this month only.
    _insert_holding(db, "TEST_RANGE", "2026-02-28", 170, 12, 2040)

    compute_month(db, date(2026, 1, 1))
    compute_month(db, date(2026, 2, 1))

    result = get_monthly(db, date(2026, 1, 1), month_to=date(2026, 2, 1), level="asset")

    assert result["month"] == "2026-01..2026-02"
    row = next(r for r in result["rows"] if r["asset_id"] == "TEST_RANGE")
    assert row["mv_start"] == pytest.approx(1000.0)  # Jan's mv_start
    assert row["mv_end"] == pytest.approx(2040.0)  # Feb's mv_end
    assert row["delta"] == pytest.approx(1040.0)
    assert row["price_effect"] == pytest.approx(220.0)  # Jan's only (Feb's is 0, phantom qty)
    assert row["trade_effect"] == pytest.approx(220.0)  # Jan's buy only
    assert row["dq_flag"] is True  # OR: Feb flagged even though Jan wasn't
    assert row["dq_reason"] is not None  # from the worst (Feb) month

    assert result["totals"]["delta"] == pytest.approx(1040.0)


def test_get_monthly_range_rollup_sums_across_months(db):
    """sub_class rollup over a range sums mv_start/mv_end/effects the same
    way as the asset-level range aggregate (via the per-asset intermediate,
    not a flat SQL SUM which would double count intermediate mv rows)."""
    _insert_asset(db, "TEST_RANGE2", asset_class="Equity")
    _insert_holding(db, "TEST_RANGE2", "2025-12-31", 100, 10, 1000, is_shadow=True)
    _insert_tx(db, "2026-01-10", "TEST_RANGE2", "buy", 20, 11, 220)
    _insert_holding(db, "TEST_RANGE2", "2026-01-31", 120, 12, 1440)
    _insert_holding(db, "TEST_RANGE2", "2026-02-28", 170, 12, 2040)

    compute_month(db, date(2026, 1, 1))
    compute_month(db, date(2026, 2, 1))

    result = get_monthly(db, date(2026, 1, 1), month_to=date(2026, 2, 1), level="sub_class")

    assert result["month"] == "2026-01..2026-02"
    row = next(r for r in result["rows"] if r["key"] == "Equity")
    assert row["mv_start"] == pytest.approx(1000.0)
    assert row["mv_end"] == pytest.approx(2040.0)
    assert row["dq_flag"] is True
    assert "TEST_RANGE2" in result["dq_flagged_assets"]


def test_get_monthly_range_same_month_uses_dotted_format(db):
    """month_to equal to month is still 'present' -> dotted format, per spec
    ('When present' — not 'when different from month')."""
    _insert_asset(db, "TEST_SAMEMONTH")
    _insert_holding(db, "TEST_SAMEMONTH", "2026-05-31", 100, 10, 1000, is_shadow=True)
    _insert_holding(db, "TEST_SAMEMONTH", "2026-06-30", 100, 10, 1000)
    compute_month(db, date(2026, 6, 1))

    result = get_monthly(db, date(2026, 6, 1), month_to=date(2026, 6, 1), level="asset")
    assert result["month"] == "2026-06..2026-06"


# ── Item D: source-transition guard (2026-07-20 owner round-2 review) ──────
# Verified on real DB: Feb-2026 US_STK_AGG, mv_start 68,615.89 -> price_effect
# -398,403.95, residual +601,603.46 — a PIS(legacy)->reader tier-mismatch
# artifact, not a real price move.

def test_source_transition_suppresses_price_effect(db):
    """A legacy-only start boundary + non-legacy-only end boundary must
    suppress price_effect entirely (fold into residual) and force dq_flag,
    regardless of how large or small the naive qty x delta-price would be."""
    _insert_asset(db, "TEST_TRANSITION")
    _insert_holding(db, "TEST_TRANSITION", "2026-01-31", 100, 10, 1000,
                    is_shadow=True, source_system="PIS_Historical")
    _insert_holding(db, "TEST_TRANSITION", "2026-02-28", 100, 686, 68600,
                    source_system="CN_Fund_Excel")

    rows = compute_month_raw(db, date(2026, 2, 1))
    row = next(r for r in rows if r["asset_id"] == "TEST_TRANSITION")

    assert row["source_transition"] is True
    assert row["price_effect"] == 0.0
    assert row["dq_flag"] is True
    # The whole (unexplained-by-price) delta lands in residual, not a bogus
    # price number.
    assert row["residual"] == pytest.approx(67600.0)


def test_source_transition_dq_reason(db):
    """_derive_dq_reason (via get_monthly) labels the transition-suppressed
    row 'source_transition', taking priority over other reason kinds."""
    _insert_asset(db, "TEST_TRANSITION2")
    _insert_holding(db, "TEST_TRANSITION2", "2026-01-31", 100, 10, 1000,
                    is_shadow=True, source_system="PIS_Historical")
    _insert_holding(db, "TEST_TRANSITION2", "2026-02-28", 100, 686, 68600,
                    source_system="CN_Fund_Excel")

    compute_month(db, date(2026, 2, 1))
    result = get_monthly(db, date(2026, 2, 1), level="asset")
    row = next(r for r in result["rows"] if r["asset_id"] == "TEST_TRANSITION2")

    assert row["dq_flag"] is True
    assert row["dq_detail"]["kind"] == "source_transition"
    assert "估值来源变更" in row["dq_reason"]


def test_source_transition_implied_price_ratio_guard(db):
    """Even within the SAME tier, an implied-price ratio jump of >1 order of
    magnitude (convention mismatch, e.g. per-100-shares vs per-share) trips
    the guard too."""
    _insert_asset(db, "TEST_CONVENTION")
    _insert_holding(db, "TEST_CONVENTION", "2026-01-31", 100, 10, 1000,
                    source_system="CN_Fund_Excel")
    # Same non-legacy source both sides, but implied price jumps 50x.
    _insert_holding(db, "TEST_CONVENTION", "2026-02-28", 100, 500, 50000,
                    source_system="CN_Fund_Excel")

    rows = compute_month_raw(db, date(2026, 2, 1))
    row = next(r for r in rows if r["asset_id"] == "TEST_CONVENTION")

    assert row["source_transition"] is True
    assert row["price_effect"] == 0.0


def test_no_source_transition_when_same_tier_and_reasonable_price_move(db):
    """Sanity: normal same-tier price moves (e.g. a stock up 20%) must NOT
    trip the guard — only genuine tier/convention mismatches should."""
    _insert_asset(db, "TEST_NORMAL")
    _insert_holding(db, "TEST_NORMAL", "2026-01-31", 100, 10, 1000,
                    source_system="Schwab_CSV")
    _insert_holding(db, "TEST_NORMAL", "2026-02-28", 100, 12, 1200,
                    source_system="Schwab_CSV")

    rows = compute_month_raw(db, date(2026, 2, 1))
    row = next(r for r in rows if r["asset_id"] == "TEST_NORMAL")

    assert row["source_transition"] is False
    assert row["price_effect"] == pytest.approx(200.0)
    assert row["dq_flag"] is False


# ── Item E: get_summary flows null-vs-zero (2026-07-20 owner round-2 review) ─

def test_summary_flows_null_when_no_classified_flows(db):
    """A month with ZERO classified cash_flow_tags rows -> flows=None (not a
    misleading {external_in: 0, ...} that reads as 'no flows moved')."""
    _insert_asset(db, "TEST_SUMMARY_NOFLOW")
    _insert_holding(db, "TEST_SUMMARY_NOFLOW", "2026-05-31", 100, 10, 1000, is_shadow=True)
    _insert_holding(db, "TEST_SUMMARY_NOFLOW", "2026-06-30", 100, 10, 1000)
    compute_month(db, date(2026, 6, 1))

    result = get_summary(db, months=3)
    june = next(m for m in result["months"] if m["month"] == "2026-06")
    assert june["flows"] is None
    assert june["invest_ratio"] is None


def test_summary_flows_numeric_when_classified_flows_exist(db):
    """A month WITH classified cash_flow_tags rows -> real numeric flows."""
    _insert_asset(db, "TEST_SUMMARY_FLOW")
    _insert_holding(db, "TEST_SUMMARY_FLOW", "2026-05-31", 100, 10, 1000, is_shadow=True)
    _insert_holding(db, "TEST_SUMMARY_FLOW", "2026-06-30", 100, 10, 1000)
    compute_month(db, date(2026, 6, 1))

    db.execute(
        """
        INSERT INTO cash_flow_tags
            (source_table, source_row_key, classification, tagged_by, amount_cny, flow_date)
        VALUES ('transactions', 'nk:test|2026-06-15|TEST_SUMMARY_FLOW|deposit|5000.00',
                'external_contribution', 'heuristic', 5000.0, '2026-06-15')
        """
    )

    result = get_summary(db, months=3)
    june = next(m for m in result["months"] if m["month"] == "2026-06")
    assert june["flows"] is not None
    assert june["flows"]["external_in"] == pytest.approx(5000.0)
    assert june["flows"]["net_external"] == pytest.approx(5000.0)


def test_summary_flows_zero_when_only_internal_tags(db):
    """A month whose ONLY classified tags are internal transfers HAS been
    classified — external flows are a genuine ¥0, not 'unknown' (owner review
    2026-07-20: Apr/May showed 'no classified flows yet' despite classified
    internal transfers)."""
    _insert_asset(db, "TEST_SUMMARY_INT")
    _insert_holding(db, "TEST_SUMMARY_INT", "2026-05-31", 100, 10, 1000, is_shadow=True)
    _insert_holding(db, "TEST_SUMMARY_INT", "2026-06-30", 100, 10, 1000)
    compute_month(db, date(2026, 6, 1))

    db.execute(
        """
        INSERT INTO cash_flow_tags
            (source_table, source_row_key, classification, tagged_by, amount_cny, flow_date)
        VALUES ('transactions', 'nk:test|2026-06-16|TEST_SUMMARY_INT|transfer_in|0.00',
                'internal_transfer', 'manual', 0.0, '2026-06-16')
        """
    )

    result = get_summary(db, months=3)
    june = next(m for m in result["months"] if m["month"] == "2026-06")
    assert june["flows"] == {"external_in": 0.0, "external_out": 0.0, "net_external": 0.0}


# ── WS-B: get_summary top-level trailing-12m contribution/savings fields ────
# (plan docs/plans/2026-07-20-investment-contributions-savings.md §Reconciliation)

def _insert_income_expense_month(conn, record_key: str, month: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO income_expense_monthly (record_key, transaction_date, payload) VALUES (?, ?, ?)",
        [record_key, month, json.dumps(payload)],
    )


def test_get_summary_includes_investment_ttm_fields(db):
    """Top-level (not per-month) savings_rate_ttm/net_external_ttm/etc. are
    sourced from investment_contributions.contributions_summary_v2 — a
    DIFFERENT source than the per-month cash_flow_tags flows/invest_ratio
    tested above. Must match contributions_summary_v2 called directly on the
    same DB (single source of truth, not re-derived differently here)."""
    _insert_income_expense_month(db, "r1", "2025-09-01", {
        "投资理财_股票基金_天天基金": 20000,
        "收入_主动收入_工资": 40000,
        "必要开支_贷款_房贷": 10000,
    })

    result = get_summary(db, months=3)
    assert "months" in result
    assert result["net_external_ttm"] == pytest.approx(20000.0)
    assert result["gross_invested_ttm"] == pytest.approx(20000.0)
    assert result["internal_realloc_ttm"] == pytest.approx(0.0)
    # WS-G (2026-08-01): savings_rate_ttm carries the SAVINGS rate —
    # (income_basis − expense_basis)/income_basis = (40000 − 10000)/40000 — and
    # the deployment ratio it used to carry (20000/40000 = 0.5) is now its own
    # field. BOTH are echoed by this endpoint; a consumer that renders one under
    # the other's label is wrong, so they must never be equal here.
    assert result["savings_rate_ttm"] == pytest.approx(0.75)
    assert result["investment_rate_ttm"] == pytest.approx(0.5)
    assert result["savings_rate_ttm"] != pytest.approx(result["investment_rate_ttm"]), (
        "fixture must keep the two rates distinct or this test cannot catch a swap"
    )
    assert result["income_basis_ttm"] == pytest.approx(40000.0)
    assert result["expense_basis_ttm"] == pytest.approx(10000.0)
    assert result["undeployed_cash_ttm"] == pytest.approx(10000.0)  # 30000 saved − 20000 deployed
    assert result["window_start_month"] == "2025-09"
    assert result["window_end_month"] == "2025-09"

    from src.services.investment_contributions import contributions_summary_v2
    expected = contributions_summary_v2(db)
    for field in (
        "savings_rate_ttm", "investment_rate_ttm", "income_basis_ttm",
        "expense_basis_ttm", "undeployed_cash_ttm", "net_external_ttm",
        "rsu_retained_ttm", "internal_realloc_ttm", "gross_invested_ttm", "income_ttm",
    ):
        assert result[field] == expected[field], f"{field} diverged from the single source"


def test_get_summary_ttm_shape_is_identical_on_empty_data(db):
    """The empty-data default set must carry EXACTLY the populated key set.

    A default block that drifts behind the populated one changes the response
    shape depending on whether the owner has ledger data — the consumer then
    sees `undefined` only on a fresh DB, which is precisely the state nobody
    tests interactively. Guard the two key sets against each other rather than
    restating either list (a restated list drifts in silence).
    """
    empty = get_summary(db, months=3)  # no income_expense_monthly rows inserted

    _insert_income_expense_month(db, "r1", "2025-09-01", {
        "收入_主动收入_工资": 40000,
        "必要开支_贷款_房贷": 10000,
    })
    populated = get_summary(db, months=3)

    assert set(empty) == set(populated)
    # anti-vacuity: the guard is worthless if the fields are absent from both
    assert {"savings_rate_ttm", "investment_rate_ttm", "undeployed_cash_ttm"} <= set(empty)

    # Per-month savings_rate stays None — trailing-only, per plan.
    for m in populated["months"]:
        assert m["savings_rate"] is None


def test_get_summary_empty_db_returns_defaults_for_ttm_fields():
    """An empty income_expense_monthly table must not break get_summary —
    ttm fields default to None/0, not an exception."""
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    conn.run_migrations()

    result = get_summary(conn, months=3)
    assert result["months"] == []
    assert result["net_external_ttm"] == 0.0
    assert result["gross_invested_ttm"] == 0.0
    assert result["internal_realloc_ttm"] == 0.0
    assert result["savings_rate_ttm"] is None
    assert result["window_start_month"] is None
    assert result["window_end_month"] is None
    conn.close()
