"""Tests for src/services/rsu_contributions.py.

Plan: docs/plans/2026-07-25-cash-flow-classification-completion.md §3.3, §5.
This module closes the ADR-025 gap: RSU shares that vest and are KEPT are
real portfolio inflow but appear nowhere in the 月度收支-derived contributions
figures (the ledger books RSU vests as income, not investment). READ-ONLY —
no writes. FIFO lot replay over source_system='RSU_Excel' transactions.

Uses an in-memory DuckDB initialized from the real schema.sql (never a bare,
schema-less connector — see CLAUDE.md Database Safety Rules).
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.rsu_contributions import (
    _surviving_lots,
    rsu_retained_ttm,
    rsu_vest_gross_ttm,
)


def _make_db() -> DatabaseConnector:
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    return conn


def _insert_rsu_tx(
    conn, tx_date: str, asset_id: str, tx_type: str, quantity: float,
    price_unit: float, *, amount_net: float | None = None, currency: str = "USD",
) -> None:
    """Insert one RSU_Excel transaction row. Matches production sign
    convention (verified against data/unified.duckdb): vest quantity is
    positive, sell quantity is negative."""
    if amount_net is None:
        amount_net = quantity * price_unit
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, quantity,
             price_unit, amount_gross, amount_net, currency, source_system, is_provisional)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'RSU_Excel', FALSE)
        """,
        [tx_date, asset_id, asset_id, tx_type, quantity, price_unit, amount_net, amount_net, currency],
    )


_FX_PATCH_TARGET = "src.services.rsu_contributions.get_today_usd_cny_rate"
_FLOWS_FX_PATCH_TARGET = "src.services.north_star_flows.get_today_usd_cny_rate"


# ── FIFO lot replay ──────────────────────────────────────────────────────────

def test_fifo_partial_sell_leaves_correct_remaining_lot():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 100.0, 100.0)
    _insert_rsu_tx(conn, "2025-02-01", "RSU_AMZN", "sell", -40.0, 100.0)

    lots, oversold = _surviving_lots(conn)
    assert len(lots) == 1
    assert lots[0]["asset_id"] == "RSU_AMZN"
    assert lots[0]["qty"] == 60.0
    assert lots[0]["price_unit"] == 100.0
    assert str(lots[0]["vest_date"]) == "2025-01-01"
    assert oversold == {}
    conn.close()


def test_sell_spanning_multiple_lots_consumes_oldest_first():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 50.0, 10.0)   # older, cheaper lot
    _insert_rsu_tx(conn, "2025-02-01", "RSU_AMZN", "vest", 50.0, 20.0)   # newer, pricier lot
    _insert_rsu_tx(conn, "2025-03-01", "RSU_AMZN", "sell", -70.0, 15.0)  # consumes all of lot1 + 20 of lot2

    lots, oversold = _surviving_lots(conn)
    assert len(lots) == 1, "the fully-consumed 2025-01 lot must be gone"
    assert lots[0]["qty"] == 30.0
    assert lots[0]["price_unit"] == 20.0, "surviving lot must be the NEWER (2025-02) one, not the older"
    assert str(lots[0]["vest_date"]) == "2025-02-01"
    assert oversold == {}
    conn.close()


def test_lot_vested_before_window_but_still_held_is_excluded():
    conn = _make_db()
    _insert_rsu_tx(conn, "2024-01-01", "RSU_AMZN", "vest", 10.0, 50.0)  # never sold

    with patch(_FX_PATCH_TARGET, return_value=1.0):
        # 2024 vest is real and survives FIFO, but the window only covers 2025.
        result = rsu_retained_ttm(conn, "2025-01", "2025-12")

    assert result["retained_cny"] == 0.0
    assert result["retained_shares"] == 0.0
    assert result["lots"] == []
    assert result["oversold_shares"] == 0.0

    # Sanity: the lot genuinely does survive FIFO (it's excluded by the
    # window filter, not because it was consumed).
    lots, oversold = _surviving_lots(conn)
    assert len(lots) == 1
    assert lots[0]["qty"] == 10.0
    assert oversold == {}
    conn.close()


def test_overselling_does_not_produce_negative_lots(caplog):
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 10.0, 5.0)
    _insert_rsu_tx(conn, "2025-02-01", "RSU_AMZN", "sell", -15.0, 5.0)  # oversell by 5

    with caplog.at_level("WARNING", logger="src.services.rsu_contributions"):
        lots, oversold = _surviving_lots(conn)
    assert lots == [], "over-sold position must leave zero lots, never a negative one"
    assert oversold == {"RSU_AMZN": 5.0}
    assert any("RSU_AMZN" in rec.message and "over-sell" in rec.message.lower() for rec in caplog.records), (
        "over-sell must be logged as a warning naming the asset"
    )

    caplog.clear()
    with patch(_FX_PATCH_TARGET, return_value=1.0), caplog.at_level(
        "WARNING", logger="src.services.rsu_contributions"
    ):
        result = rsu_retained_ttm(conn, "2025-01", "2025-12")
    assert result["retained_cny"] == 0.0
    assert result["retained_shares"] == 0.0
    assert result["oversold_shares"] == 5.0, "over-sell must be surfaced on the public dict, not swallowed"
    assert any(rec.levelname == "WARNING" for rec in caplog.records), "rsu_retained_ttm must not silence the warning"
    conn.close()


def test_zero_position_multiple_assets_no_exception():
    """Full round-trip (vest then sell everything) for one asset while a
    second asset keeps its full position — must not raise and must report
    only the second asset."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-01", "RSU_AMZN", "vest", 20.0, 100.0)
    _insert_rsu_tx(conn, "2025-01-02", "RSU_AMZN", "sell", -20.0, 100.0)
    _insert_rsu_tx(conn, "2025-01-01", "RSU_GOOG", "vest", 5.0, 300.0)

    lots, oversold = _surviving_lots(conn)
    assert len(lots) == 1
    assert lots[0]["asset_id"] == "RSU_GOOG"
    assert oversold == {}
    conn.close()


# ── FX conversion ────────────────────────────────────────────────────────────

def test_usd_to_cny_conversion_applied_to_vest_gross():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-15", "RSU_AMZN", "vest", 10.0, 100.0, amount_net=1000.0)

    with patch(_FX_PATCH_TARGET, return_value=7.5) as mock_fx:
        result = rsu_vest_gross_ttm(conn, "2025-01", "2025-01")

    mock_fx.assert_called_once()
    assert result == 1000.0 * 7.5
    conn.close()


def test_usd_to_cny_conversion_applied_to_retained():
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-15", "RSU_AMZN", "vest", 10.0, 100.0)  # never sold

    with patch(_FX_PATCH_TARGET, return_value=7.5) as mock_fx:
        result = rsu_retained_ttm(conn, "2025-01", "2025-01")

    mock_fx.assert_called_once()
    assert result["retained_cny"] == 10.0 * 100.0 * 7.5
    assert result["retained_shares"] == 10.0
    conn.close()


def test_cny_currency_row_not_converted():
    """A defensive case: if a row were ever CNY-denominated (never happens in
    production — RSU_Excel is always USD — but the module must not assume
    it), the FX rate must not be applied."""
    conn = _make_db()
    _insert_rsu_tx(conn, "2025-01-15", "RSU_AMZN", "vest", 10.0, 100.0,
                    amount_net=1000.0, currency="CNY")

    with patch(_FX_PATCH_TARGET, return_value=7.5):
        result = rsu_vest_gross_ttm(conn, "2025-01", "2025-01")

    assert result == 1000.0, "CNY rows must never be multiplied by the FX rate"
    conn.close()


# ── Empty table ──────────────────────────────────────────────────────────────

def test_empty_rsu_table_returns_zeros_no_exception():
    conn = _make_db()
    with patch(_FX_PATCH_TARGET, return_value=7.0):
        vest = rsu_vest_gross_ttm(conn, "2025-01", "2025-12")
        retained = rsu_retained_ttm(conn, "2025-01", "2025-12")

    assert vest == 0.0
    assert retained == {
        "retained_cny": 0.0, "retained_shares": 0.0, "lots": [], "oversold_shares": 0.0,
    }
    conn.close()


# ── Non-RSU / non-vest/sell rows are ignored ─────────────────────────────────

def test_other_source_systems_ignored():
    conn = _make_db()
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, quantity,
             price_unit, amount_gross, amount_net, currency, source_system, is_provisional)
        VALUES ('2025-01-01', 'US_STK_AMZN', 'AMZN', 'buy', 10.0, 100.0, 1000.0, 1000.0, 'USD', 'Schwab_CSV', FALSE)
        """
    )
    with patch(_FX_PATCH_TARGET, return_value=7.0):
        vest = rsu_vest_gross_ttm(conn, "2025-01", "2025-01")
        retained = rsu_retained_ttm(conn, "2025-01", "2025-01")
    assert vest == 0.0
    assert retained["retained_cny"] == 0.0
    conn.close()


# ── Wiring: contributions_summary carries the rsu key with a matching window ─

def test_contributions_summary_includes_rsu_key_with_matching_window():
    from src.services.north_star import contributions_summary

    conn = _make_db()
    today = date.today()

    # 12 months of ledger data so investment.window_start_month/end_month are non-None.
    for i in range(12):
        month = _month_start_n_ago(today, i)
        conn.execute(
            "INSERT INTO income_expense_monthly (record_key, transaction_date, payload) VALUES (?, ?, ?)",
            [f"ie_{i}", month.isoformat(), json.dumps({
                "投资理财_股票基金_天天基金": 10_000.0, "收入_主动收入_工资": 50_000.0,
            })],
        )

    # An RSU vest inside that same window, never sold -> retained figure > 0.
    window_month = _month_start_n_ago(today, 1)
    _insert_rsu_tx(conn, window_month.isoformat(), "RSU_AMZN", "vest", 10.0, 200.0)

    with patch(_FX_PATCH_TARGET, return_value=7.0), patch(_FLOWS_FX_PATCH_TARGET, return_value=7.0):
        result = contributions_summary(conn)

    assert "rsu" in result
    assert result["rsu"]["window_start_month"] == result["investment"]["window_start_month"]
    assert result["rsu"]["window_end_month"] == result["investment"]["window_end_month"]
    assert result["rsu"]["retained_shares"] == 10.0
    assert result["rsu"]["retained_ttm"] == 10.0 * 200.0 * 7.0
    assert result["rsu"]["oversold_shares"] == 0.0
    # ytd_sum/trailing_12m_sum/by_classification must be untouched by this wiring.
    assert "ytd_sum" in result
    assert "trailing_12m_sum" in result
    assert "by_classification" in result
    conn.close()


def test_contributions_summary_rsu_key_zero_when_no_ledger_data():
    """No income_expense_monthly rows -> investment.window_* is None ->
    rsu.* must degrade to zeros/None, never guess an independent window."""
    from src.services.north_star import contributions_summary

    conn = _make_db()
    with patch(_FX_PATCH_TARGET, return_value=7.0), patch(_FLOWS_FX_PATCH_TARGET, return_value=7.0):
        result = contributions_summary(conn)

    assert result["investment"]["window_start_month"] is None
    assert result["rsu"]["window_start_month"] is None
    assert result["rsu"]["window_end_month"] is None
    assert result["rsu"]["vest_gross_ttm"] == 0.0
    assert result["rsu"]["retained_ttm"] == 0.0
    assert result["rsu"]["retained_shares"] == 0.0
    assert result["rsu"]["oversold_shares"] == 0.0
    conn.close()


# ── Wiring: glide-path run-rate is available and NOT gated by contamination ──

def _month_start_n_ago(today: date, n: int) -> date:
    year = today.year
    month = today.month - n
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def test_contribution_run_rate_available_despite_many_untagged_flows():
    """_contribution_run_rate (north_star_glide.py) must return
    ("available", value) even when the cash_flow_tags candidate universe is
    heavily contaminated (the OLD gate this function used to respect) — the
    run-rate no longer reads cash_flow_tags at all post-ADR-025-§5.2 rewire.
    """
    from src.services.north_star_glide import _contribution_run_rate

    conn = _make_db()
    today = date.today()

    # A large pile of untagged flow candidates — would have tripped the old
    # >5%-unclassified / >¥50K contamination gate many times over.
    for i in range(60):
        conn.execute(
            """
            INSERT INTO transactions
                (transaction_date, asset_id, asset_name, transaction_type,
                 amount_net, amount_gross, currency, source_system, is_provisional)
            VALUES (?, 'CASH_IN', 'CASH_IN', 'transfer_in', 5000.0, 5000.0, 'CNY', 'test', FALSE)
            """,
            [f"2019-{(i % 12) + 1:02d}-01"],
        )

    for i in range(12):
        month = _month_start_n_ago(today, i)
        conn.execute(
            "INSERT INTO income_expense_monthly (record_key, transaction_date, payload) VALUES (?, ?, ?)",
            [f"ie_{i}", month.isoformat(), json.dumps({
                "投资理财_股票基金_天天基金": 20_000.0, "收入_主动收入_工资": 120_000.0,
            })],
        )

    with patch(_FX_PATCH_TARGET, return_value=7.0):
        run_rate, status = _contribution_run_rate(conn)

    assert status == "available"
    assert run_rate is not None
    assert abs(run_rate - 20_000.0) < 1000.0
    conn.close()
