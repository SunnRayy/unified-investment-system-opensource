"""Tests for src/services/rsu_realized_gains.py (plan
2026-08-01-ie-column-mapping-and-ibkr-amounts §WS-C).

Per-month realized gain on RSU share sales: Σ (sale_price − vest_price) × qty,
FIFO across vest lots, USD + CNY. READ-ONLY — no writes.

Uses an in-memory DuckDB initialized from the real schema.sql — never a bare,
schema-less connector, and the connector is always constructed with ':memory:'
(an argument-less construction would open the production DB; see CLAUDE.md
Database Safety Rules and AGENTS.md Rule 6).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.rsu_contributions import (
    _month_str,
    _surviving_lots,
    replay_rsu_lots,
    rsu_retained_ttm,
)
from src.services.rsu_realized_gains import (
    format_report,
    rsu_realized_gains_by_month,
)

_FX_PATCH_TARGET = "src.services.rsu_realized_gains.get_today_usd_cny_rate"


def _make_db() -> DatabaseConnector:
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    return conn


def _insert_rsu_tx(
    conn, tx_date: str, asset_id: str, tx_type: str, quantity: float,
    price_unit: float, *, currency: str = "USD",
) -> None:
    """Insert one RSU_Excel transaction row. Matches the production sign
    convention (verified against data/unified.duckdb): vest quantity positive,
    sell quantity negative (AGENTS.md Rule 26 — RSU sells are negative)."""
    amount_net = quantity * price_unit
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, quantity,
             price_unit, amount_gross, amount_net, currency, source_system, is_provisional)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'RSU_Excel', FALSE)
        """,
        [tx_date, asset_id, asset_id, tx_type, quantity, price_unit,
         amount_net, amount_net, currency],
    )


def _gains(conn, **kwargs) -> dict:
    """Run the report with a pinned FX rate unless the test overrides it."""
    kwargs.setdefault("fx_rate", 7.0)
    return rsu_realized_gains_by_month(conn, **kwargs)


# ── core gain arithmetic ─────────────────────────────────────────────────────

def test_single_lot_single_sale_gain_is_appreciation_above_vest_price():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 100.0, 100.0)
    _insert_rsu_tx(conn, "2025-05-10", "RSU_AMZN", "sell", -40.0, 150.0)

    result = _gains(conn)
    assert [m["month"] for m in result["months"]] == ["2025-05"]
    month = result["months"][0]
    assert month["gain_usd"] == pytest.approx(40 * 50.0)      # 2000.00
    assert month["gain_cny"] == pytest.approx(40 * 50.0 * 7.0)
    assert month["proceeds_usd"] == pytest.approx(6000.0)
    assert month["cost_basis_usd"] == pytest.approx(4000.0)
    assert month["shares_sold"] == pytest.approx(40.0)
    assert result["total_gain_usd"] == pytest.approx(2000.0)
    conn.close()


def test_gain_is_not_gross_proceeds():
    """The whole point of WS-C: the vest-price principal was already booked as
    income (收入_主动收入_RSU*), so reporting proceeds would double-count."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 10.0, 200.0)
    _insert_rsu_tx(conn, "2025-06-01", "RSU_AMZN", "sell", -10.0, 250.0)

    result = _gains(conn)
    assert result["total_gain_usd"] == pytest.approx(500.0)
    assert result["total_proceeds_usd"] == pytest.approx(2500.0)
    conn.close()


def test_sale_spanning_multiple_lots_uses_oldest_lots_first():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 50.0, 10.0)   # cheap, older
    _insert_rsu_tx(conn, "2025-02-01", "RSU_AMZN", "vest", 50.0, 20.0)   # pricier, newer
    _insert_rsu_tx(conn, "2025-03-01", "RSU_AMZN", "sell", -70.0, 30.0)

    result = _gains(conn)
    month = result["months"][0]
    # 50 @ (30-10) + 20 @ (30-20) = 1000 + 200
    assert month["gain_usd"] == pytest.approx(1200.0)
    lots = month["sales"][0]["lots"]
    assert [lot["price_unit"] for lot in lots] == [10.0, 20.0]
    assert [lot["qty"] for lot in lots] == [50.0, 20.0]
    conn.close()


def test_realized_loss_is_reported_as_negative_gain():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 10.0, 300.0)
    _insert_rsu_tx(conn, "2025-04-01", "RSU_AMZN", "sell", -10.0, 250.0)

    result = _gains(conn)
    assert result["total_gain_usd"] == pytest.approx(-500.0)
    assert result["total_gain_cny"] == pytest.approx(-3500.0)
    conn.close()


def test_month_with_sales_but_zero_gain_is_still_reported():
    """Sale price == vest price. The month must appear with gain 0.0 and a
    non-zero share count — it is a real event, not an absence of one."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 10.0, 200.0)
    _insert_rsu_tx(conn, "2025-07-04", "RSU_AMZN", "sell", -10.0, 200.0)

    result = _gains(conn)
    assert [m["month"] for m in result["months"]] == ["2025-07"]
    assert result["months"][0]["gain_usd"] == 0.0
    assert result["months"][0]["gain_cny"] == 0.0
    assert result["months"][0]["shares_sold"] == pytest.approx(10.0)
    conn.close()


# ── sell-to-cover ────────────────────────────────────────────────────────────

def test_sell_to_cover_is_excluded_and_produces_no_gain():
    """A same-day, same-price sell is mandatory tax withholding: it consumes
    ITS OWN vest lot, so the gain is zero by construction and the month is not
    reported as a sale month."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "vest", 192.0, 192.0)
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "sell", -86.4, 192.0)

    result = _gains(conn)
    assert result["months"] == []
    assert result["total_gain_usd"] == 0.0
    assert result["sell_to_cover_shares"] == pytest.approx(86.4)
    conn.close()


def test_sell_to_cover_does_not_consume_an_older_cheaper_lot():
    """Regression guard for the model divergence documented in the module
    docstring: under strict FIFO the withholding would eat the 2023 lot and
    fabricate a gain on a non-discretionary event."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2023-09-15", "RSU_AMZN", "vest", 100.0, 100.0)   # old, cheap
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "vest", 100.0, 200.0)
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "sell", -50.0, 200.0)   # withholding
    _insert_rsu_tx(conn, "2025-06-01", "RSU_AMZN", "sell", -100.0, 300.0)  # discretionary

    result = _gains(conn)
    assert result["sell_to_cover_shares"] == pytest.approx(50.0)
    month = result["months"][0]
    # The old 100 @ 100 lot must survive the withholding and be consumed first:
    # 100 @ (300-100) = 20000. Under strict FIFO it would be 50@(300-100) +
    # 50@(300-200) = 15000 and the withholding would have booked a phantom gain.
    assert month["gain_usd"] == pytest.approx(20000.0)
    assert [lot["price_unit"] for lot in month["sales"][0]["lots"]] == [100.0]
    conn.close()


def test_same_day_sale_at_a_different_price_is_treated_as_discretionary():
    """Detection is structural (date + price), so a genuine same-day sale at a
    market price different from the vest price is NOT swallowed as withholding."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 100.0, 50.0)   # older lot
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "vest", 100.0, 200.0)
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "sell", -10.0, 210.0)  # not the vest price

    result = _gains(conn)
    assert result["sell_to_cover_shares"] == 0.0
    assert result["months"][0]["gain_usd"] == pytest.approx(10 * (210.0 - 50.0))
    conn.close()


def test_vest_lot_match_excess_falls_through_to_fifo_with_its_real_gain():
    """Regression guard for the per-sale-vs-per-match flag bug: the 20 sh that
    spill past the matched vest lot are a real disposal of the OLD lot and must
    carry their $2,000 gain, not inherit the matched row's zero."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2024-01-01", "RSU_AMZN", "vest", 100.0, 100.0)
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "vest", 40.0, 200.0)
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "sell", -60.0, 200.0)

    result = _gains(conn)
    # The 40 sh fully consume their own vest lot -> a batch disposal at zero
    # gain (not withholding, which is always partial). The 20 sh excess is a
    # normal FIFO consumption of the 100 @ 100 lot.
    assert result["sell_to_cover_shares"] == 0.0
    assert result["total_gain_usd"] == pytest.approx(20 * 100.0)
    conn.close()


# ── specific-lot matching: next-day batch liquidation (owner decision) ───────

def test_next_day_full_liquidation_matches_its_own_vest_lot():
    """2026-03 shape: the broker liquidates the whole batch the day after it
    vests, with tax withheld inside the single row (no separate sell-to-cover).
    It must match its OWN lot (gain 0), not reach past it into an older,
    pricier lot and manufacture a loss."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-09-15", "RSU_AMZN", "vest", 100.0, 232.0)   # older, pricier
    _insert_rsu_tx(conn, "2026-03-15", "RSU_AMZN", "vest", 192.0, 209.304)
    _insert_rsu_tx(conn, "2026-03-16", "RSU_AMZN", "sell", -192.0, 209.304)

    result = _gains(conn)
    assert [m["month"] for m in result["months"]] == ["2026-03"]
    month = result["months"][0]
    assert month["gain_usd"] == 0.0            # not -2,269.60 from the $232 lot
    assert month["shares_sold"] == pytest.approx(192.0)
    assert result["sell_to_cover_shares"] == 0.0   # a full match is not withholding
    assert month["sales"][0]["lots"][0]["vest_date"] == "2026-03-15"
    conn.close()


def test_a_distant_sale_at_the_same_price_still_goes_fifo():
    """The window is what stops a discretionary sale months later that happens
    to trade at an old vest price from claiming that lot."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2024-01-10", "RSU_AMZN", "vest", 50.0, 100.0)    # old, cheap
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "vest", 50.0, 209.304)
    # Same price as the 2025-03 vest, but eight months later — discretionary.
    _insert_rsu_tx(conn, "2025-11-20", "RSU_AMZN", "sell", -50.0, 209.304)

    result = _gains(conn)
    month = result["months"][0]
    assert month["gain_usd"] == pytest.approx(50 * (209.304 - 100.0))
    assert month["sales"][0]["lots"][0]["vest_date"] == "2024-01-10"
    assert result["sell_to_cover_shares"] == 0.0
    conn.close()


@pytest.mark.parametrize(
    "sale_date, expect_matched",
    [
        ("2025-03-15", True),   # same day — withholding / immediate liquidation
        ("2025-03-16", True),   # next day — the observed 2026-03 shape
        ("2025-03-19", True),   # +4 — Friday vest, Monday holiday, Tuesday fill
        ("2025-03-20", False),  # +5 — outside the window, ordinary FIFO
    ],
)
def test_vest_match_window_boundary(sale_date, expect_matched):
    conn = _make_db()
    _insert_rsu_tx(conn, "2024-01-01", "RSU_AMZN", "vest", 50.0, 100.0)   # cheap fallback
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "vest", 50.0, 200.0)
    _insert_rsu_tx(conn, sale_date, "RSU_AMZN", "sell", -50.0, 200.0)

    result = _gains(conn)
    if expect_matched:
        assert result["total_gain_usd"] == 0.0
    else:
        # Falls through to FIFO and consumes the cheap 2024 lot instead.
        assert result["total_gain_usd"] == pytest.approx(50 * 100.0)
    conn.close()


def test_sale_before_a_vest_never_matches_that_vest():
    """The window is one-sided — a sale cannot dispose of a batch that has not
    vested yet, even at an identical price."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2024-01-01", "RSU_AMZN", "vest", 50.0, 100.0)
    _insert_rsu_tx(conn, "2025-03-13", "RSU_AMZN", "sell", -50.0, 200.0)
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "vest", 50.0, 200.0)

    result = _gains(conn)
    assert result["total_gain_usd"] == pytest.approx(50 * 100.0)
    conn.close()


# ── edge cases: empty, oversold, multi-asset, window ─────────────────────────

def test_empty_database_returns_empty_result_never_raises():
    conn = _make_db()
    result = _gains(conn)
    assert result["months"] == []
    assert result["total_gain_usd"] == 0.0
    assert result["total_gain_cny"] == 0.0
    assert result["total_shares_sold"] == 0.0
    assert result["oversold_shares"] == 0.0
    assert "No RSU share sales" in format_report(result)
    conn.close()


def test_no_sales_only_vests_returns_empty_months():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 100.0, 100.0)
    result = _gains(conn)
    assert result["months"] == []
    assert result["total_gain_usd"] == 0.0
    conn.close()


def test_oversold_shares_are_surfaced_and_never_booked_as_zero_basis_gain():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 10.0, 100.0)
    _insert_rsu_tx(conn, "2025-02-01", "RSU_AMZN", "sell", -25.0, 150.0)

    result = _gains(conn)
    month = result["months"][0]
    # Only the 10 matched shares produce a gain; the 15 unmatched ones would be
    # a $2,250 phantom profit if they were treated as zero-basis.
    assert month["gain_usd"] == pytest.approx(10 * 50.0)
    assert month["unmatched_shares"] == pytest.approx(15.0)
    assert month["shares_sold"] == pytest.approx(10.0)
    assert result["oversold_shares"] == pytest.approx(15.0)
    assert "UNDERSTATED" in format_report(result)
    conn.close()


def test_both_rsu_assets_are_kept_separate():
    """RSU_GOOG is Google Class C and deliberately not GOOGL — lots must never
    cross assets."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 10.0, 100.0)
    _insert_rsu_tx(conn, "2025-01-01", "RSU_GOOG", "vest", 10.0, 300.0)
    _insert_rsu_tx(conn, "2025-05-01", "RSU_AMZN", "sell", -10.0, 150.0)
    _insert_rsu_tx(conn, "2025-05-01", "RSU_GOOG", "sell", -10.0, 350.0)

    result = _gains(conn)
    by_asset = result["months"][0]["by_asset"]
    assert by_asset["RSU_AMZN"]["gain_usd"] == pytest.approx(500.0)
    assert by_asset["RSU_GOOG"]["gain_usd"] == pytest.approx(500.0)
    assert result["total_gain_usd"] == pytest.approx(1000.0)
    conn.close()


def test_window_filters_sale_months_but_not_the_lot_replay():
    """A lot vested outside the window is still the lot a windowed sale
    consumes — the replay is always full-history."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2020-01-01", "RSU_AMZN", "vest", 100.0, 10.0)
    _insert_rsu_tx(conn, "2024-06-01", "RSU_AMZN", "sell", -10.0, 20.0)
    _insert_rsu_tx(conn, "2025-06-01", "RSU_AMZN", "sell", -10.0, 30.0)

    result = _gains(conn, start_month="2025-01", end_month="2025-12")
    assert [m["month"] for m in result["months"]] == ["2025-06"]
    assert result["total_gain_usd"] == pytest.approx(10 * 20.0)  # basis is the 2020 lot
    assert result["window"] == {"start_month": "2025-01", "end_month": "2025-12"}
    conn.close()


# ── FX transparency ──────────────────────────────────────────────────────────

def test_explicit_fx_rate_is_used_and_labelled_as_caller_supplied():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 10.0, 100.0)
    _insert_rsu_tx(conn, "2025-05-01", "RSU_AMZN", "sell", -10.0, 200.0)

    result = rsu_realized_gains_by_month(conn, fx_rate=6.9)
    assert result["fx_rate"] == 6.9
    assert "caller-supplied" in result["fx_rate_source"]
    assert result["fx_rate_is_fallback"] is False
    assert result["total_gain_cny"] == pytest.approx(1000.0 * 6.9)
    conn.close()


def test_hardcoded_fallback_rate_is_flagged_not_presented_as_authoritative():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 10.0, 100.0)
    _insert_rsu_tx(conn, "2025-05-01", "RSU_AMZN", "sell", -10.0, 200.0)

    with patch(_FX_PATCH_TARGET, return_value=7.0):
        result = rsu_realized_gains_by_month(conn)
    assert result["fx_rate_is_fallback"] is True
    assert "fx-constant" in result["fx_rate_source"]
    assert "NOT authoritative" in format_report(result)
    conn.close()


def test_live_spot_rate_is_labelled_as_a_single_rate_for_all_months():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 10.0, 100.0)
    _insert_rsu_tx(conn, "2025-05-01", "RSU_AMZN", "sell", -10.0, 200.0)

    with patch(_FX_PATCH_TARGET, return_value=6.7504):
        result = rsu_realized_gains_by_month(conn)
    assert result["fx_rate"] == pytest.approx(6.7504)
    assert result["fx_rate_is_fallback"] is False
    assert "not the rate on the sale date" in result["fx_rate_source"]
    conn.close()


def test_non_usd_row_contributes_to_cny_but_not_to_the_usd_columns():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 10.0, 100.0, currency="CNY")
    _insert_rsu_tx(conn, "2025-05-01", "RSU_AMZN", "sell", -10.0, 200.0, currency="CNY")

    result = _gains(conn)
    month = result["months"][0]
    assert month["gain_usd"] == 0.0
    assert month["gain_cny"] == pytest.approx(1000.0)   # already CNY, not × 7
    assert month["proceeds_usd"] == 0.0
    assert month["shares_sold"] == pytest.approx(10.0)
    conn.close()


# ── shared-core contract: the refactor must not move rsu_contributions ───────

def test_surviving_lots_uses_specific_lot_matching_by_default():
    """One rule everywhere (owner decision 2026-08-01): `_surviving_lots()` —
    and therefore the shipped `rsu_retained_ttm()` figure that
    `investment_contributions.py` and `north_star_glide.py` consume — matches
    specific vest lots, exactly like the realized-gain report. Pinned in BOTH
    directions so neither an accidental flip nor a silent revert goes unseen."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2023-09-15", "RSU_AMZN", "vest", 100.0, 100.0)
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "vest", 100.0, 200.0)
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "sell", -50.0, 200.0)

    # Shipped: the withholding consumes its OWN lot, so the old cheap lot lives.
    specific, _ = _surviving_lots(conn)
    assert [(lot["price_unit"], lot["qty"]) for lot in specific] == [(100.0, 100.0), (200.0, 50.0)]

    # The retired rule, still reachable so this test can prove they differ.
    strict, _ = replay_rsu_lots(conn, legacy_strict_fifo=True)
    assert [(lot["price_unit"], lot["qty"]) for lot in strict] == [(100.0, 50.0), (200.0, 100.0)]
    conn.close()


def _legacy_flag_call_sites(source: str, label: str) -> list[str]:
    """Call sites passing `legacy_strict_fifo=` as a keyword argument.

    AST, not grep: the parameter's own definition and every docstring that
    names the flag are text matches but not calls, and a guard that trips on
    its own documentation gets deleted by the next person to touch it.
    """
    return [
        f"{label}:{node.lineno}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "legacy_strict_fifo"
    ]


def test_no_production_code_opts_into_legacy_strict_fifo():
    """`legacy_strict_fifo` is a test-only escape hatch. If a src/ call site
    ever passes it, the two-rule split is back and `rsu_retained_ttm` has
    silently diverged from the realized-gain report again — the exact defect
    this change closed."""
    offenders = [
        hit
        for path in sorted(Path("src").rglob("*.py"))
        for hit in _legacy_flag_call_sites(path.read_text(encoding="utf-8"), str(path))
    ]
    assert offenders == [], f"production code must not opt into strict FIFO: {offenders}"


def test_legacy_flag_guard_actually_detects_a_violation():
    """Anti-vacuity: prove the guard above can fail. A structural check that
    silently matches nothing is worse than no check at all — integrity check #4
    was vacuous from inception for exactly this reason."""
    assert _legacy_flag_call_sites(
        "replay_rsu_lots(db, legacy_strict_fifo=True)", "synthetic"
    ) == ["synthetic:1"]
    assert _legacy_flag_call_sites("replay_rsu_lots(db)", "synthetic") == []


def test_on_match_callback_is_invoked_with_unconsumed_lot_state():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 100.0, 10.0)
    _insert_rsu_tx(conn, "2025-02-01", "RSU_AMZN", "sell", -30.0, 15.0)

    seen = []
    replay_rsu_lots(conn, on_match=lambda sale, lot, qty: seen.append((sale["price_unit"], lot["price_unit"], qty)))
    assert seen == [(15.0, 10.0, 30.0)]
    conn.close()


def test_on_match_receives_none_lot_for_the_oversold_excess():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 10.0, 10.0)
    _insert_rsu_tx(conn, "2025-02-01", "RSU_AMZN", "sell", -25.0, 15.0)

    seen = []
    replay_rsu_lots(conn, on_match=lambda sale, lot, qty: seen.append((lot, qty)))
    assert seen[-1][0] is None
    assert seen[-1][1] == pytest.approx(15.0)
    conn.close()


# ── read-only guarantee (structural) ─────────────────────────────────────────

def test_module_contains_no_write_statements():
    """Hard constraint: this service is strictly read-only. A structural guard
    beats a docstring promise (AGENTS.md Rule 24)."""
    source = Path("src/services/rsu_realized_gains.py").read_text(encoding="utf-8")
    # Two-word SQL forms only — bare English words like "dropped" or "update"
    # appear legitimately in prose and must not trip the guard.
    for pattern in (
        r"INSERT\s+INTO", r"UPDATE\s+\w+\s+SET", r"DELETE\s+FROM", r"DROP\s+TABLE",
        r"CREATE\s+(TABLE|VIEW|INDEX)", r"ALTER\s+TABLE", r"TRUNCATE\s+TABLE",
        r"\bexecutemany\b", r"\bCOPY\s+\w+\s+TO\b",
    ):
        assert not re.search(pattern, source, flags=re.IGNORECASE), (
            f"rsu_realized_gains.py must stay read-only — found /{pattern}/"
        )


def test_report_does_not_mutate_the_transactions_table():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 10.0, 100.0)
    _insert_rsu_tx(conn, "2025-05-01", "RSU_AMZN", "sell", -10.0, 200.0)
    before = conn.execute("SELECT COUNT(*), SUM(quantity) FROM transactions").fetchone()

    _gains(conn)

    assert conn.execute("SELECT COUNT(*), SUM(quantity) FROM transactions").fetchone() == before
    conn.close()


# ── C3 acceptance gate: production-shaped RSU history ────────────────────────

# The real RSU_Excel history as of 2026-08-01 (read-only dump of
# data/unified.duckdb). Amazon withholds exactly 45% of each vest as
# sell-to-cover, so every vest is followed by a same-day, same-price sell.
_PRODUCTION_ROWS = [
    ("2023-09-15", "RSU_AMZN", "vest",   48.0,  172.0),
    ("2023-09-15", "RSU_AMZN", "sell",  -21.6,  172.0),
    ("2024-09-15", "RSU_AMZN", "vest",  144.0,  185.0),
    ("2024-09-15", "RSU_AMZN", "sell",  -64.8,  185.0),
    ("2025-03-15", "RSU_AMZN", "vest",  192.0,  192.0),
    ("2025-03-15", "RSU_AMZN", "sell",  -86.4,  192.0),
    ("2025-09-15", "RSU_AMZN", "vest",  192.0,  232.0),
    ("2025-09-15", "RSU_AMZN", "sell",  -86.4,  232.0),
    ("2025-10-31", "RSU_AMZN", "sell",  -26.0,  248.9214),
    ("2025-11-03", "RSU_AMZN", "sell",  -40.0,  255.0472),
    ("2025-11-03", "RSU_AMZN", "sell",  -39.0,  257.0),
    ("2025-11-12", "RSU_AMZN", "sell",  -55.0,  250.0),
    ("2026-03-15", "RSU_AMZN", "vest",  192.0,  209.304),
    ("2026-03-16", "RSU_AMZN", "sell", -192.0,  209.304),
    ("2026-04-08", "RSU_AMZN", "sell",  -25.0,  224.42),
    ("2026-04-10", "RSU_AMZN", "sell",  -25.0,  232.0),
    ("2026-06-25", "RSU_GOOG", "vest",   13.0,  342.19),
    ("2026-06-25", "RSU_GOOG", "sell",   -5.85, 342.19),
]


def _production_db() -> DatabaseConnector:
    conn = _make_db()
    for tx_date, asset_id, tx_type, qty, price in _PRODUCTION_ROWS:
        _insert_rsu_tx(conn, tx_date, asset_id, tx_type, qty, price)
    return conn


def test_c3_oct_nov_2025_batch_matches_the_full_lot_inventory():
    """C3 gate. The owner's session hand-calc gave $7,550.84 for this batch by
    enumerating only the 2025-03 ($192) and 2025-09 ($232) lots. Two older
    RETAINED lots — 26.4 sh @ $172 (2023-09) and 79.2 sh @ $185 (2024-09) —
    were also still open, and FIFO consumes those cheaper lots FIRST, so the
    true gain is HIGHER. Total open shares reconcile either way (316.8), which
    is why the omission is invisible in a share count.
    """
    conn = _production_db()
    result = _gains(conn, start_month="2025-10", end_month="2025-11")

    assert [m["month"] for m in result["months"]] == ["2025-10", "2025-11"]
    assert result["months"][0]["gain_usd"] == pytest.approx(1999.96, abs=0.01)
    assert result["months"][1]["gain_usd"] == pytest.approx(8809.29, abs=0.01)
    assert result["total_gain_usd"] == pytest.approx(10809.25, abs=0.02)
    assert result["total_proceeds_usd"] == pytest.approx(40446.85, abs=0.01)
    assert result["total_shares_sold"] == pytest.approx(160.0)
    assert result["oversold_shares"] == 0.0

    # The first sale must be matched against the 2023 lot, not the 2025-03 one.
    first_lot = result["months"][0]["sales"][0]["lots"][0]
    assert first_lot["vest_date"] == "2023-09-15"
    assert first_lot["price_unit"] == pytest.approx(172.0)
    conn.close()


def test_c3_reconciles_to_the_hand_calc_when_only_the_two_2025_lots_exist():
    """Isolates the divergence: replay the identical Oct–Nov sales against the
    owner's assumed lot inventory and the hand-calc figure comes back exactly.
    So the two calculations agree on method (FIFO, vest-price basis) and
    differ only on which lots were still open."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-03-15", "RSU_AMZN", "vest", 105.6, 192.0)
    _insert_rsu_tx(conn, "2025-09-15", "RSU_AMZN", "vest", 105.6, 232.0)
    for tx_date, _asset, tx_type, qty, price in _PRODUCTION_ROWS[8:12]:
        _insert_rsu_tx(conn, tx_date, "RSU_AMZN", tx_type, qty, price)

    # $7,550.844 exactly; totals sum per-month rounded figures, hence abs=0.02.
    result = _gains(conn)
    assert result["total_gain_usd"] == pytest.approx(7550.84, abs=0.02)
    conn.close()


def test_c3_production_shape_full_history_totals():
    conn = _production_db()
    result = _gains(conn)

    months = {m["month"]: m["gain_usd"] for m in result["months"]}
    assert months == {
        "2025-10": pytest.approx(1999.96, abs=0.01),
        "2025-11": pytest.approx(8809.29, abs=0.01),
        # 2026-03-16 liquidates the whole 2026-03-15 vest at its own vest
        # price: gain 0.00, NOT the -1,510.73 strict FIFO produced by reaching
        # past it into the 2025-09 $232 lot (owner decision, 2026-08-01).
        "2026-03": 0.0,
        # ...which in turn leaves the cheaper $192 lot open for April, so April
        # rises from 945.30 to 1,810.50.
        "2026-04": pytest.approx(1810.50, abs=0.01),
    }
    assert result["total_gain_usd"] == pytest.approx(12619.75, abs=0.02)
    # Withholding months (2023-09, 2024-09, 2025-03, 2025-09, 2026-06) are
    # PARTIAL vest-lot matches: no owner disposal, so no row of zeroes.
    assert result["sell_to_cover_shares"] == pytest.approx(265.05)
    assert result["oversold_shares"] == 0.0
    conn.close()


def test_c3_specific_lot_gain_reconciles_with_the_surviving_cost_basis():
    """Conservation check across the two rules: proceeds are fixed, so every
    dollar of extra realized gain must be a dollar of cost basis left behind in
    the surviving lots. Guards against the rule change quietly creating or
    destroying basis."""
    conn = _production_db()

    strict_lots, _ = replay_rsu_lots(conn, legacy_strict_fifo=True)
    specific_lots, _ = replay_rsu_lots(conn)
    strict_basis = sum(lot["qty"] * lot["price_unit"] for lot in strict_lots)
    specific_basis = sum(lot["qty"] * lot["price_unit"] for lot in specific_lots)

    # Same shares held either way — only the basis composition differs.
    assert sum(lot["qty"] for lot in strict_lots) == pytest.approx(113.95)
    assert sum(lot["qty"] for lot in specific_lots) == pytest.approx(113.95)
    assert strict_basis == pytest.approx(24800.33, abs=0.01)
    assert specific_basis == pytest.approx(27176.26, abs=0.01)

    # 12,619.75 (specific-lot) - 10,243.82 (strict FIFO) == 27,176.26 - 24,800.33
    assert _gains(conn)["total_gain_usd"] - 10243.82 == pytest.approx(
        specific_basis - strict_basis, abs=0.02
    )
    conn.close()


def test_shipped_retained_lots_are_the_2025_09_batch_not_the_liquidated_one():
    """The behaviour change the owner asked for, pinned on production shape.

    Strict FIFO reported the surviving 106.8 AMZN shares as the 2026-03-15
    vest — the batch he ruled was liquidated on 2026-03-16. What he actually
    still holds is the 2025-09-15 batch at $232, plus a 1.2 sh tail of the
    2025-03 vest. Same 113.95 shares, honest provenance."""
    conn = _production_db()
    lots, oversold = replay_rsu_lots(conn)
    assert oversold == {}
    assert [(lot["asset_id"], str(lot["vest_date"]), round(lot["qty"], 2), lot["price_unit"])
            for lot in lots] == [
        ("RSU_AMZN", "2025-03-15", 1.2, 192.0),
        ("RSU_AMZN", "2025-09-15", 105.6, 232.0),
        ("RSU_GOOG", "2026-06-25", 7.15, 342.19),
    ]
    assert sum(lot["qty"] for lot in lots) == pytest.approx(113.95)

    # The retired rule, kept reachable so an accidental revert is caught.
    legacy, _ = replay_rsu_lots(conn, legacy_strict_fifo=True)
    assert [(str(lot["vest_date"]), round(lot["qty"], 2)) for lot in legacy] == [
        ("2026-03-15", 106.8), ("2026-06-25", 7.15),
    ]
    conn.close()


def test_retained_ttm_window_drops_the_out_of_window_tail():
    """FX-independent invariant for downstream consumers: over the live
    2025-08..2026-07 window the 1.2 sh @ $192 tail (2025-03 vest) falls OUTSIDE
    the window and must not be counted, leaving 112.75 sh / 26,945.8585 USD.
    Asserted on USD basis, not CNY: the CNY figure moves with live FX between
    runs (6.7504 vs 6.7505 is ~¥2.70) and is not a stable assertion target."""
    conn = _production_db()
    lots, _ = replay_rsu_lots(conn)
    in_window = [lot for lot in lots if "2025-08" <= _month_str(lot["vest_date"]) <= "2026-07"]

    assert [(str(lot["vest_date"]), round(lot["qty"], 2)) for lot in in_window] == [
        ("2025-09-15", 105.6), ("2026-06-25", 7.15),
    ]
    assert sum(lot["qty"] for lot in in_window) == pytest.approx(112.75)
    assert sum(lot["qty"] * lot["price_unit"] for lot in in_window) == pytest.approx(
        26945.8585, abs=0.0001
    )

    with patch("src.services.rsu_contributions.get_today_usd_cny_rate", return_value=6.7504):
        result = rsu_retained_ttm(conn, "2025-08", "2026-07")
    assert result["retained_shares"] == pytest.approx(112.75)
    assert result["retained_cny"] == pytest.approx(181895.32, abs=0.01)
    assert result["oversold_shares"] == 0.0
    conn.close()


def test_format_report_renders_the_production_table():
    conn = _production_db()
    text = format_report(_gains(conn))
    assert "2025-10" in text and "2025-11" in text
    assert "1,999.96" in text and "8,809.29" in text
    assert "收入_被动收入_股票卖出收益" in text
    assert "Sell-to-cover" in text
    conn.close()
