"""Tests for src/services/investment_contributions.py (WS-A).

Plan: docs/plans/2026-07-20-investment-contributions-savings.md. This module
computes the monthly investment-contribution + savings series directly from
income_expense_monthly.payload (FS Excel 月度收支 tab's 投资理财 columns) —
READ-ONLY, no writes. Standalone / not yet wired into any endpoint (WS-B).

Uses an in-memory DuckDB initialized from the real schema.sql (never a bare,
schema-less connector — see CLAUDE.md Database Safety Rules).
"""
from __future__ import annotations

import json

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.investment_contributions import (
    contributions_summary_v2,
    monthly_investment_flows,
)


def _make_db() -> DatabaseConnector:
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    return conn


def _insert_month(conn, record_key: str, month: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO income_expense_monthly (record_key, transaction_date, payload) VALUES (?, ?, ?)",
        [record_key, month, json.dumps(payload)],
    )


# ── Per-month math: Schwab_USD must NEVER be summed ─────────────────────────

def test_schwab_usd_excluded_from_us_schwab_and_gross():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-05-01", {
        "投资理财_股票基金_天天基金": 25000,
        "投资理财_股票基金_Schawab": 115200,
        "投资理财_股票基金_Schawab_USD": 16000,
        "投资理财_黄金_招行纸黄金": 8734,
    })

    series = monthly_investment_flows(conn)
    assert len(series) == 1
    row = series[0]
    assert row["month"] == "2025-05"
    assert row["by_destination"]["us_schwab"] == 115200.0, "must use CNY column alone, not +Schawab_USD"
    assert row["by_destination"]["cn_fund"] == 25000.0
    assert row["by_destination"]["gold"] == 8734.0
    expected_gross = 25000 + 115200 + 8734
    assert row["gross_invested"] == expected_gross
    assert row["gross_invested"] != 25000 + 115200 + 16000 + 8734, "must NOT double-count Schawab_USD"
    conn.close()


def test_gold_sums_both_paper_and_etf():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-06-01", {
        "投资理财_黄金_招行纸黄金": 3000,
        "投资理财_黄金_黄金ETF": 2000,
    })
    series = monthly_investment_flows(conn)
    assert series[0]["by_destination"]["gold"] == 5000.0
    conn.close()


def test_none_and_empty_string_values_treated_as_zero():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-07-01", {
        "投资理财_股票基金_天天基金": None,
        "投资理财_股票基金_Schawab": "",
        "投资理财_黄金_招行纸黄金": 4000,
        "收入_主动收入_工资": 10000,
    })
    series = monthly_investment_flows(conn)
    assert len(series) == 1
    row = series[0]
    assert row["by_destination"]["cn_fund"] == 0.0
    assert row["by_destination"]["us_schwab"] == 0.0
    assert row["gross_invested"] == 4000.0
    assert row["income"] == 10000.0
    conn.close()


# ── redemptions field ────────────────────────────────────────────────────────

def test_redemptions_sums_three_passive_income_keys():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-08-01", {
        "投资理财_股票基金_天天基金": 1000,
        "收入_被动收入_基金赎回": 500,
        "收入_被动收入_黄金卖出": 300,
        "收入_被动收入_银行理财": 200,
    })
    series = monthly_investment_flows(conn)
    assert series[0]["redemptions"] == 1000.0
    conn.close()


# ── all-zero months skipped ──────────────────────────────────────────────────

def test_all_zero_months_are_skipped():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-01-01", {"投资理财_股票基金_天天基金": 10000, "收入_主动收入_工资": 5000})
    _insert_month(conn, "m2", "2025-02-01", {})  # all zero -> skip
    _insert_month(conn, "m3", "2025-03-01", {"投资理财_股票基金_天天基金": 8000, "收入_主动收入_工资": 5000})

    series = monthly_investment_flows(conn)
    months = [r["month"] for r in series]
    assert months == ["2025-01", "2025-03"]
    conn.close()


def test_series_sorted_ascending_by_month():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-03-01", {"投资理财_股票基金_天天基金": 1000, "收入_主动收入_工资": 1})
    _insert_month(conn, "m2", "2025-01-01", {"投资理财_股票基金_天天基金": 2000, "收入_主动收入_工资": 1})
    _insert_month(conn, "m3", "2025-02-01", {"投资理财_股票基金_天天基金": 3000, "收入_主动收入_工资": 1})

    series = monthly_investment_flows(conn)
    assert [r["month"] for r in series] == ["2025-01", "2025-02", "2025-03"]
    conn.close()


# ── trailing window: net_external / internal_realloc offset ─────────────────

def test_pre_2025_05_style_month_net_external_approx_gross():
    """A month with redemptions=0 -> net_external == gross (no offset)."""
    conn = _make_db()
    _insert_month(conn, "m1", "2024-06-01", {
        "投资理财_股票基金_天天基金": 20000,
        "收入_主动收入_工资": 30000,
    })
    summary = contributions_summary_v2(conn, window_months=12)
    assert summary["gross_invested_ttm"] == 20000.0
    assert summary["redemptions_ttm"] == 0.0
    assert summary["net_external_ttm"] == 20000.0
    assert summary["internal_realloc_ttm"] == 0.0
    conn.close()


def test_redemptions_exceed_invested_clamps_net_external_to_zero():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-11-01", {
        "投资理财_股票基金_Schawab": 278430,
        "收入_被动收入_基金赎回": 333258,
        "收入_主动收入_工资": 50000,
    })
    summary = contributions_summary_v2(conn, window_months=12)
    assert summary["gross_invested_ttm"] == 278430.0
    assert summary["redemptions_ttm"] == 333258.0
    assert summary["net_external_ttm"] == 0.0, "clamped at 0, never negative"
    assert summary["internal_realloc_ttm"] == 278430.0, "min(gross, redeem)"
    conn.close()


def test_net_external_and_internal_realloc_over_multi_month_window():
    conn = _make_db()
    # Month 1: pure new savings, no redemption.
    _insert_month(conn, "m1", "2025-01-01", {
        "投资理财_股票基金_天天基金": 30000,
        "收入_主动收入_工资": 40000,
    })
    # Month 2: lump sum reinvestment funded partly by redemption.
    _insert_month(conn, "m2", "2025-02-01", {
        "投资理财_股票基金_Schawab": 50000,
        "收入_被动收入_基金赎回": 20000,
        "收入_主动收入_工资": 40000,
    })
    summary = contributions_summary_v2(conn, window_months=12)
    gross = 30000 + 50000
    redeem = 20000
    assert summary["gross_invested_ttm"] == gross
    assert summary["redemptions_ttm"] == redeem
    assert summary["net_external_ttm"] == max(gross - redeem, 0.0)
    assert summary["internal_realloc_ttm"] == min(gross, redeem)
    conn.close()


# ── savings_rate guard ───────────────────────────────────────────────────────

def test_savings_rate_none_when_income_ttm_zero():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-01-01", {
        "投资理财_股票基金_天天基金": 5000,
        "收入_主动收入_工资": 0,
    })
    summary = contributions_summary_v2(conn, window_months=12)
    assert summary["income_ttm"] == 0.0
    assert summary["savings_rate_ttm"] is None
    conn.close()


def test_savings_rate_is_ratio_when_income_positive():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-01-01", {
        "投资理财_股票基金_天天基金": 5000,
        "收入_主动收入_工资": 20000,
    })
    summary = contributions_summary_v2(conn, window_months=12)
    assert summary["income_ttm"] == 20000.0
    assert summary["income_basis_ttm"] == 20000.0
    assert summary["net_external_ttm"] == 5000.0
    # investment_rate is the metric this used to call "savings rate"; the
    # savings rate itself is now (income − consumption) / income, and this
    # fixture books no expense at all.
    assert summary["investment_rate_ttm"] == 5000.0 / 20000.0
    assert summary["savings_rate_ttm"] == 1.0
    assert summary["undeployed_cash_ttm"] == 15000.0
    conn.close()


# ── trailing window selects last N DATA months, not calendar-anchored ──────

def test_trailing_window_selects_last_n_data_months():
    conn = _make_db()
    # 15 months of data, Jan-2024 through Mar-2025 (matches +15 with jan start).
    months = []
    for i in range(15):
        year = 2024 + (i // 12)
        month_num = (i % 12) + 1
        month_str = f"{year}-{month_num:02d}-01"
        months.append(month_str[:7])
        _insert_month(conn, f"m{i}", month_str, {
            "投资理财_股票基金_天天基金": 1000.0 * (i + 1),  # distinct per month
            "收入_主动收入_工资": 10000,
        })

    summary = contributions_summary_v2(conn, window_months=12)
    full_series_months = [r["month"] for r in summary["series"]]
    assert full_series_months == months
    assert len(full_series_months) == 15

    # Trailing window = the LAST 12 distinct months present, i.e. months[3:15].
    expected_window = months[-12:]
    assert summary["window_start_month"] == expected_window[0]
    assert summary["window_end_month"] == expected_window[-1]

    # gross_invested_ttm must equal the sum over exactly those 12 months
    # (1000*(i+1) for i in [3..14] -> sum of 4000..15000 step 1000).
    expected_gross = sum(1000.0 * (i + 1) for i in range(3, 15))
    assert summary["gross_invested_ttm"] == expected_gross
    conn.close()


def test_trailing_window_anchored_to_latest_data_month_not_today():
    """The data may lag real time; the window must anchor to the latest
    month present in the series, not to today's calendar date."""
    conn = _make_db()
    # All data is old (far in the past relative to "today"), but the window
    # must still be computed from these months, not silently come up empty
    # because none of them fall within the last `window_months` calendar
    # months of today.
    _insert_month(conn, "m1", "2020-02-01", {"投资理财_股票基金_天天基金": 1000, "收入_主动收入_工资": 5000})
    _insert_month(conn, "m2", "2020-03-01", {"投资理财_股票基金_天天基金": 2000, "收入_主动收入_工资": 5000})

    summary = contributions_summary_v2(conn, window_months=12)
    assert summary["window_start_month"] == "2020-02"
    assert summary["window_end_month"] == "2020-03"
    assert summary["gross_invested_ttm"] == 3000.0
    conn.close()


# ── by_destination_ttm aggregation ───────────────────────────────────────────

def test_by_destination_ttm_sums_across_window():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-01-01", {
        "投资理财_股票基金_天天基金": 1000,
        "投资理财_股票基金_Schawab": 2000,
        "投资理财_黄金_招行纸黄金": 300,
        "投资理财_黄金_黄金ETF": 200,
        "投资理财_银行理财_招行": 500,
    })
    _insert_month(conn, "m2", "2025-02-01", {
        "投资理财_股票基金_天天基金": 1500,
        "投资理财_银行理财_招行": 100,
    })
    summary = contributions_summary_v2(conn, window_months=12)
    dest = summary["by_destination_ttm"]
    assert dest["cn_fund"] == 2500.0
    assert dest["us_schwab"] == 2000.0
    assert dest["gold"] == 500.0
    assert dest["bank_wealth"] == 600.0
    conn.close()


# ── window_months threading (Task B, GET /north-star/contributions toggle) ──
# The '12'/'36'/'ALL' toggle just varies window_months — this generic
# `series[-window_months:]` slice already handles any N without special
# casing, including a window_months larger than the series (Python slicing
# is a no-op past the list length), which is exactly how the API route
# implements "All Time" (see src/api/routes/north_star.py
# _CONTRIBUTIONS_ALL_HISTORY_MONTHS). window_start_month/window_end_month
# must always reflect what was ACTUALLY summed, never a hardcoded value.

def test_window_months_36_covers_more_history_than_12():
    conn = _make_db()
    # 40 months of distinct, strictly increasing gross_invested so every
    # window boundary is independently verifiable by its sum.
    months = []
    for i in range(40):
        year = 2022 + (i // 12)
        month_num = (i % 12) + 1
        month_str = f"{year}-{month_num:02d}-01"
        months.append(month_str[:7])
        _insert_month(conn, f"m{i}", month_str, {
            "投资理财_股票基金_天天基金": 1000.0 * (i + 1),
            "收入_主动收入_工资": 10000,
        })

    summary_12 = contributions_summary_v2(conn, window_months=12)
    summary_36 = contributions_summary_v2(conn, window_months=36)

    expected_window_12 = months[-12:]
    expected_window_36 = months[-36:]
    assert summary_12["window_start_month"] == expected_window_12[0]
    assert summary_12["window_end_month"] == expected_window_12[-1]
    assert summary_36["window_start_month"] == expected_window_36[0]
    assert summary_36["window_end_month"] == expected_window_36[-1]

    expected_gross_12 = sum(1000.0 * (i + 1) for i in range(28, 40))
    expected_gross_36 = sum(1000.0 * (i + 1) for i in range(4, 40))
    assert summary_12["gross_invested_ttm"] == expected_gross_12
    assert summary_36["gross_invested_ttm"] == expected_gross_36
    assert summary_36["gross_invested_ttm"] > summary_12["gross_invested_ttm"]
    conn.close()


def test_window_months_larger_than_history_covers_full_series_honestly():
    """'All Time' is implemented as a window_months deliberately larger than
    any realistic ledger — this proves that degrades cleanly to "the whole
    series" rather than erroring or silently truncating, and that the
    returned window bounds are the TRUE first/last data month (never a
    hardcoded '12')."""
    conn = _make_db()
    _insert_month(conn, "m1", "2020-02-01", {"投资理财_股票基金_天天基金": 1000, "收入_主动收入_工资": 5000})
    _insert_month(conn, "m2", "2020-03-01", {"投资理财_股票基金_天天基金": 2000, "收入_主动收入_工资": 5000})
    _insert_month(conn, "m3", "2025-07-01", {"投资理财_股票基金_天天基金": 3000, "收入_主动收入_工资": 5000})

    summary_all = contributions_summary_v2(conn, window_months=100_000)
    assert summary_all["window_start_month"] == "2020-02"
    assert summary_all["window_end_month"] == "2025-07"
    assert summary_all["gross_invested_ttm"] == 6000.0
    assert summary_all["income_ttm"] == 15000.0

    # Sanity: a 12-month window over the SAME data only sees the last month
    # (2020-02/2020-03 are more than 12 data-months before 2025-07's data
    # position in the series — proves the two calls are genuinely different,
    # not coincidentally equal).
    summary_12 = contributions_summary_v2(conn, window_months=12)
    assert summary_12["window_start_month"] == "2020-02"  # only 3 months exist total
    assert summary_12["gross_invested_ttm"] == summary_all["gross_invested_ttm"], (
        "with only 3 data months total, a 12-month window already covers all of them — "
        "this call exists to document that 'window_months=12' and 'ALL' can coincide "
        "when history is short, which is correct, not a bug"
    )
    conn.close()


def test_default_window_months_is_12():
    """Calling without window_months= must preserve pre-existing behaviour
    (every other caller of contributions_summary_v2 relies on this)."""
    conn = _make_db()
    for i in range(15):
        year = 2024 + (i // 12)
        month_num = (i % 12) + 1
        month_str = f"{year}-{month_num:02d}-01"
        _insert_month(conn, f"m{i}", month_str, {
            "投资理财_股票基金_天天基金": 1000.0,
            "收入_主动收入_工资": 10000,
        })
    default_summary = contributions_summary_v2(conn)
    explicit_12_summary = contributions_summary_v2(conn, window_months=12)
    assert default_summary == explicit_12_summary
    conn.close()


# ── empty DB ──────────────────────────────────────────────────────────────

def test_empty_db_returns_empty_series_and_null_window():
    conn = _make_db()
    summary = contributions_summary_v2(conn, window_months=12)
    assert summary["series"] == []
    assert summary["gross_invested_ttm"] == 0.0
    assert summary["redemptions_ttm"] == 0.0
    assert summary["income_ttm"] == 0.0
    assert summary["net_external_ttm"] == 0.0
    assert summary["internal_realloc_ttm"] == 0.0
    assert summary["income_basis_ttm"] == 0.0
    assert summary["expense_basis_ttm"] == 0.0
    assert summary["rsu_retained_ttm"] == 0.0
    assert summary["investment_numerator_ttm"] == 0.0
    assert summary["savings_rate_ttm"] is None
    assert summary["investment_rate_ttm"] is None
    assert summary["pass_through_in_ttm"] == 0.0
    assert summary["pass_through_out_ttm"] == 0.0
    assert summary["window_start_month"] is None
    assert summary["window_end_month"] is None
    assert summary["months_with_contribution"] is None
    assert summary["months_with_contribution_window"] is None
    conn.close()


# ── months_with_contribution (participation signal, W-5 / owner decision
#    2026-07-26, docs/design/2026-07-26-your-path.dc.html.md §3.1) ──────────

def test_months_with_contribution_counts_months_with_any_gross_invested():
    conn = _make_db()
    # 3 months with a nonzero investment, 1 month with none (income only).
    _insert_month(conn, "m1", "2025-01-01", {"投资理财_股票基金_天天基金": 1000, "收入_主动收入_工资": 5000})
    _insert_month(conn, "m2", "2025-02-01", {"投资理财_股票基金_天天基金": 2000, "收入_主动收入_工资": 5000})
    _insert_month(conn, "m3", "2025-03-01", {"收入_主动收入_工资": 5000})  # no investment this month
    _insert_month(conn, "m4", "2025-04-01", {"投资理财_股票基金_Schawab": 500, "收入_主动收入_工资": 5000})

    summary = contributions_summary_v2(conn, window_months=12)
    assert summary["months_with_contribution"] == 3
    assert summary["months_with_contribution_window"] == 4
    conn.close()


def test_months_with_contribution_window_matches_actual_history_when_shorter_than_requested():
    """Only 5 months of real data exist; requesting a 12-month window must
    report a denominator of 5 (the true history), never a padded/fabricated
    12 — see the module docstring's 'never padded to look like a full
    window' rule."""
    conn = _make_db()
    for i in range(5):
        month_str = f"2025-{i + 1:02d}-01"
        _insert_month(conn, f"m{i}", month_str, {
            "投资理财_股票基金_天天基金": 1000.0,
            "收入_主动收入_工资": 5000,
        })
    summary = contributions_summary_v2(conn, window_months=12)
    assert summary["months_with_contribution_window"] == 5
    assert summary["months_with_contribution"] == 5
    conn.close()


def test_months_with_contribution_zero_when_no_month_in_window_invested():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-01-01", {"收入_被动收入_基金赎回": 1000, "收入_主动收入_工资": 5000})
    _insert_month(conn, "m2", "2025-02-01", {"收入_被动收入_基金赎回": 500, "收入_主动收入_工资": 5000})
    summary = contributions_summary_v2(conn, window_months=12)
    assert summary["months_with_contribution"] == 0
    assert summary["months_with_contribution_window"] == 2
    conn.close()


# ===========================================================================
# ie_column mapping-driven semantics (plan 2026-08-01 WS-A, migration V82)
#
# The six 月度收支 column names this module used to hardcode now live in
# reader_mappings (mapping_kind='ie_column'). _make_db() above builds a
# schema-only in-memory DB with no reader_mappings table, so these tests
# exercise the code-default path (src.database.mapping_seeds.IE_COLUMN_SEED);
# _make_migrated_db() adds the V75+V82 tables so DB overrides can be tested.
# ===========================================================================

from src.database.mapping_seeds import IE_COLUMN_SEED, IEColumn  # noqa: E402
from src.services.investment_contributions import load_ie_column_mapping  # noqa: E402


def _make_migrated_db() -> DatabaseConnector:
    """In-memory DB with schema + all migrations (incl. V75 reader_mappings and
    the V82 ie_column seed). Never touches the production DB."""
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    conn.run_migrations()
    return conn


# ── currency='USD' columns must contribute to NOTHING (Rule 2 at the ledger) ─

def test_no_usd_sibling_column_reaches_any_total():
    """Every `_USD` column in the ledger is a native-currency sibling of a CNY
    column the owner already converted in Excel. Summing one would double-count
    the same money at a second exchange rate. This asserts it for ALL of them at
    once, driven off the mapping — including columns added after this test was
    written."""
    conn = _make_db()
    usd_keys = [k for k, s in IE_COLUMN_SEED.items() if s.currency == "USD"]
    assert usd_keys, "fixture is vacuous: the seed declares no USD columns"

    baseline_payload = {
        "投资理财_股票基金_天天基金": 1000.0,
        "投资理财_股票基金_Schawab": 2000.0,
        "投资理财_股票基金_IBKR": 3000.0,
        "收入_被动收入_基金赎回": 400.0,
        "收入_主动收入_工资": 50000.0,
    }
    _insert_month(conn, "cny_only", "2025-01-01", baseline_payload)
    # Same month's CNY figures, plus a large distinct value in EVERY USD column.
    with_usd = dict(baseline_payload)
    for i, key in enumerate(usd_keys):
        with_usd[key] = 100000.0 + i  # distinct, huge, impossible to miss
    _insert_month(conn, "with_usd", "2025-02-01", with_usd)

    series = monthly_investment_flows(conn)
    cny_month, usd_month = series[0], series[1]
    assert cny_month["by_destination"] == usd_month["by_destination"]
    assert cny_month["gross_invested"] == usd_month["gross_invested"] == 6000.0
    assert cny_month["redemptions"] == usd_month["redemptions"] == 400.0
    assert cny_month["income_basis"] == usd_month["income_basis"] == 50000.0
    assert cny_month["income"] == usd_month["income"] == 50400.0  # + the redemption
    conn.close()


def test_schwab_usd_and_ibkr_usd_excluded_by_currency_not_by_name():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-05-01", {
        "投资理财_股票基金_Schawab": 115200,
        "投资理财_股票基金_Schawab_USD": 16000,
        "投资理财_股票基金_IBKR": 72000,
        "投资理财_股票基金_IBKR_USD": 10000,
    })
    row = monthly_investment_flows(conn)[0]
    assert row["by_destination"]["us_schwab"] == 115200.0
    assert row["by_destination"]["us_ibkr"] == 72000.0
    assert row["gross_invested"] == 187200.0
    conn.close()


# ── us_ibkr destination bucket (added 2026-08-01) ───────────────────────────

def test_ibkr_is_its_own_destination_bucket():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-06-01", {"投资理财_股票基金_IBKR": 50000, "收入_主动收入_工资": 100000})
    summary = contributions_summary_v2(conn)
    assert summary["by_destination_ttm"]["us_ibkr"] == 50000.0
    assert summary["by_destination_ttm"]["us_schwab"] == 0.0, "IBKR must not land in the Schwab bucket"
    assert summary["gross_invested_ttm"] == 50000.0
    conn.close()


def test_destination_keys_are_stable_even_when_a_bucket_is_empty():
    """The UI reads by_destination by key — a bucket with no money in the window
    must still be present at 0.0, never absent."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-06-01", {"投资理财_股票基金_天天基金": 1000, "收入_主动收入_工资": 2000})
    summary = contributions_summary_v2(conn)
    expected = {s.bucket for s in IE_COLUMN_SEED.values() if s.role == "invested" and s.currency == "CNY"}
    assert set(summary["by_destination_ttm"]) == expected
    assert set(summary["series"][0]["by_destination"]) == expected
    conn.close()


# ── 收入_被动收入_股票卖出收益: income, NOT redemption (owner 2026-08-01) ────

def test_stock_sale_gain_is_not_a_redemption():
    """The principal entered the ledger as RSU *income*, never as a 投资理财
    column, so subtracting it would remove money that was never added — the
    double-subtract ADR-025 §4b warns about."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-07-01", {
        "投资理财_股票基金_天天基金": 10000,
        "收入_被动收入_股票卖出收益": 80000,
        "收入_主动收入_工资": 200000,
    })
    row = monthly_investment_flows(conn)[0]
    assert row["redemptions"] == 0.0, "股票卖出收益 must never be netted out of contributions"
    summary = contributions_summary_v2(conn)
    assert summary["net_external_ttm"] == 10000.0
    assert summary["redemptions_ttm"] == 0.0
    conn.close()


def test_excel_aggregate_columns_are_never_read_as_inputs():
    """Owner ruling 2026-08-01: 所有 excel 里的计算/合计值都不应该被 Huinsight 读取使用.
    Every total is derived from the LEAF columns; the Excel's own 总收入合计 /
    总支出 / *合计 columns are present in the payload and must contribute
    nothing, even when they are (as here) deliberately wrong."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-07-01", {
        "收入_主动收入_工资": 40000,
        "收入_主动收入_RSU": 100000,
        "收入_被动收入_股票卖出收益": 60000,
        "收入_被动收入_基金赎回": 5000,
        "投资理财_股票基金_天天基金": 20000,
        "必要开支_日常支出_餐饮娱乐": 3000,
        # Deliberately absurd aggregates — if any of these is read, it shows.
        "主动收入合计": 999_999.0,
        "被动收入合计": 999_999.0,
        "总收入合计": 999_999.0,
        "必要支出": 999_999.0,
        "总支出": 999_999.0,
        "理财": 999_999.0,
    })
    row = monthly_investment_flows(conn)[0]
    assert row["income_basis"] == 200000.0, "40000 + 100000 + 60000 income leaves"
    assert row["income"] == 205000.0, "+ the 5000 redemption = the Excel-equivalent gross income"
    assert row["expense_basis"] == 3000.0
    assert row["gross_invested"] == 20000.0
    conn.close()


def test_stock_sale_gain_columns_handle_all_zero():
    """Both 股票卖出收益 columns are empty in the Excel today — no path may
    require a non-zero value."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-07-01", {
        "投资理财_股票基金_天天基金": 1000,
        "收入_被动收入_股票卖出收益": None,
        "收入_被动收入_股票卖出收益_USD": "",
        "收入_主动收入_工资": 5000,
    })
    summary = contributions_summary_v2(conn)
    assert summary["gross_invested_ttm"] == 1000.0
    assert summary["income_ttm"] == 5000.0
    assert summary["redemptions_ttm"] == 0.0
    conn.close()


# ── non-money columns ───────────────────────────────────────────────────────

def test_reference_expense_and_computed_columns_contribute_to_nothing():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-08-01", {
        "投资理财_股票基金_天天基金": 1000,
        "收入_主动收入_工资": 5000,
        "收入_被动收入_黄金卖出(克)": 250.5,     # grams, not yuan
        "参考_美元汇率 ": 7.19,                  # note: trailing space, as in the live payload
        "参考_黄金价格_克价": 907.77,
        "必要开支_日常支出_餐饮娱乐": 3000,
        "理财": 1000,                            # Excel-side subtotal
        "总支出": 4000,
        "被动收入合计": 0,
    })
    row = monthly_investment_flows(conn)[0]
    assert row["gross_invested"] == 1000.0
    assert row["redemptions"] == 0.0
    assert row["income"] == 5000.0
    conn.close()


def test_unmapped_column_contributes_to_nothing_and_does_not_raise():
    """An unknown column is inert here (it surfaces as an actionable
    `candidate` in the ie_column unmapped scan instead — see
    src/api/routes/reader_mappings.py)."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-08-01", {
        "投资理财_股票基金_天天基金": 1000,
        "投资理财_股票基金_某个新券商": 999999,
        "收入_主动收入_工资": 5000,
    })
    row = monthly_investment_flows(conn)[0]
    assert row["gross_invested"] == 1000.0
    conn.close()


def test_payload_bookkeeping_keys_are_ignored():
    """_transform_income_expense injects asset_id/source_system into the
    payload — neither is a ledger column."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-08-01", {
        "asset_id": "IE_20250801",
        "source_system": "Financial_Summary",
        "投资理财_股票基金_天天基金": 1000,
        "收入_主动收入_工资": 5000,
    })
    row = monthly_investment_flows(conn)[0]
    assert row["gross_invested"] == 1000.0
    conn.close()


def test_trailing_whitespace_in_a_header_still_matches_its_mapping():
    """The live 参考_美元汇率 payload key carries a trailing space; a hand-edited
    header can gain or lose one at any time. Matching is strip-normalized on
    both sides so that can never silently drop a column out of a total."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-08-01", {
        "投资理财_股票基金_天天基金 ": 1000,   # trailing space on a MONEY column
        " 收入_主动收入_工资": 5000,           # leading space
    })
    row = monthly_investment_flows(conn)[0]
    assert row["gross_invested"] == 1000.0
    assert row["income"] == 5000.0
    conn.close()


# ── the mapping is DATA: a DB edit changes the math, no code change ─────────

def test_db_override_reroutes_a_column_to_a_different_bucket():
    conn = _make_migrated_db()
    _insert_month(conn, "m1", "2025-08-01", {"投资理财_股票基金_天天基金": 7000, "收入_主动收入_工资": 10000})
    before = contributions_summary_v2(conn)
    assert before["by_destination_ttm"]["cn_fund"] == 7000.0

    conn.execute(
        "UPDATE reader_mappings SET map_value = ? WHERE reader_key = 'financial_summary' "
        "AND mapping_kind = 'ie_column' AND map_key = '投资理财_股票基金_天天基金'",
        ['{"role": "invested", "bucket": "us_ibkr", "currency": "CNY"}'],
    )
    after = contributions_summary_v2(conn)
    # cn_fund had exactly one invested column; re-routing it leaves the bucket
    # with no declaring row at all, so the key is gone rather than 0.0 — the key
    # set is derived from the mapping, which is the intended behaviour.
    assert after["by_destination_ttm"].get("cn_fund", 0.0) == 0.0
    assert after["by_destination_ttm"]["us_ibkr"] == 7000.0
    assert after["gross_invested_ttm"] == before["gross_invested_ttm"] == 7000.0
    conn.close()


def test_db_row_can_add_a_brand_new_column_without_a_code_change():
    """The whole point of WS-A: a column the owner adds to the Excel becomes a
    contribution by adding a mapping row, not by editing this module."""
    conn = _make_migrated_db()
    _insert_month(conn, "m1", "2025-08-01", {"投资理财_股票基金_某个新券商": 12345, "收入_主动收入_工资": 10000})
    assert contributions_summary_v2(conn)["gross_invested_ttm"] == 0.0

    conn.execute(
        "INSERT INTO reader_mappings (reader_key, mapping_kind, map_key, map_value, status) "
        "VALUES ('financial_summary', 'ie_column', '投资理财_股票基金_某个新券商', ?, 'active')",
        ['{"role": "invested", "bucket": "us_schwab", "currency": "CNY"}'],
    )
    assert contributions_summary_v2(conn)["gross_invested_ttm"] == 12345.0
    conn.close()


def test_seeded_db_and_code_defaults_agree():
    """A migrated DB and a schema-only DB must produce the SAME numbers — the
    V82 seed and the code default are one source of truth."""
    payload = {
        "投资理财_股票基金_天天基金": 1000,
        "投资理财_股票基金_Schawab": 2000,
        "投资理财_股票基金_Schawab_USD": 280,
        "收入_被动收入_基金赎回": 300,
        "收入_主动收入_工资": 9000,
    }
    plain = _make_db()
    _insert_month(plain, "m1", "2025-08-01", payload)
    migrated = _make_migrated_db()
    _insert_month(migrated, "m1", "2025-08-01", payload)
    assert contributions_summary_v2(plain) == contributions_summary_v2(migrated)
    plain.close()
    migrated.close()


def test_load_ie_column_mapping_is_strip_normalized():
    conn = _make_db()
    mapping = load_ie_column_mapping(conn)
    assert all(k == k.strip() for k in mapping)
    assert isinstance(mapping["收入_主动收入_工资"], IEColumn)
    conn.close()


# ===========================================================================
# savings_rate_ttm / investment_rate_ttm — ADR-025 Amendment 2026-08-01
# (owner-approved; plan 2026-08-01-ie-column-mapping-and-ibkr-amounts WS-D/E/G)
#
#   income_basis_ttm    = Σ(role='income', CNY)   — LEAF columns, never an
#                         Excel aggregate (WS-E: 所有 excel 里的计算/合计值都
#                         不应该被 Huinsight 读取使用)
#   expense_basis_ttm   = Σ(role='expense', CNY)  — investment is not spending
#   savings_rate_ttm    = (income_basis − expense_basis) / income_basis
#   investment_rate_ttm = (net_external + rsu_retained) / income_basis
#
# TWO metrics, because the shipped one was mislabelled (WS-G): "money that
# reached an investment account / income" is an INVESTMENT rate. Money earned
# and left in a bank account is saved, just not deployed — which is why every
# correction still felt too low to the owner.
#
# Both exclusions in the basis are derived from the ie_column `role`, never
# from a column list: redemptions (converting an asset to cash is not earning)
# and BOTH ends of the 报销/工作开支 pass-through round trip.
# ===========================================================================


def _insert_rsu_vest(conn, date_str: str, qty: float, price_cny: float, asset_id: str = "RSU_AMZN"):
    """A CNY-denominated RSU vest — currency='CNY' so no FX rate is involved
    and the fixture value is exactly the retained figure (mirrors the
    defensive CNY-row fixtures in tests/services/test_rsu_contributions.py)."""
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, quantity,
             price_unit, amount_gross, amount_net, currency, source_system, is_provisional)
        VALUES (?, ?, ?, 'vest', ?, ?, ?, ?, 'CNY', 'RSU_Excel', FALSE)
        """,
        [date_str, asset_id, asset_id, qty, price_cny, qty * price_cny, qty * price_cny],
    )


def test_income_basis_excludes_redemptions_but_income_ttm_does_not():
    """The Excel-equivalent gross income stays visible and unredefined; the
    rate's denominator is the separate, narrower `income_basis_ttm`.

    Both are now DERIVED from the leaves (owner ruling 2026-08-01, WS-E) —
    `income_ttm` is income leaves + redemptions + the pass-through inflow, i.e.
    what 总收入合计 says, computed by Huinsight rather than read out of the workbook.
    """
    conn = _make_db()
    _insert_month(conn, "m1", "2025-01-01", {
        "投资理财_股票基金_天天基金": 50000,
        "收入_被动收入_基金赎回": 30000,
        "收入_主动收入_工资": 70000,
    })
    summary = contributions_summary_v2(conn)
    assert summary["income_ttm"] == 100000.0, "the Excel-equivalent 总收入合计, derived"
    assert summary["redemptions_ttm"] == 30000.0
    assert summary["income_basis_ttm"] == 70000.0, "a redemption is not earnings"
    # numerator 50000-30000 = 20000; denominator 70000.
    assert summary["net_external_ttm"] == 20000.0
    assert summary["investment_rate_ttm"] == pytest.approx(20000.0 / 70000.0)
    # The old (double-penalised) figure would have been 20000/100000 = 0.20.
    assert summary["investment_rate_ttm"] != pytest.approx(0.20)
    conn.close()


def test_savings_numerator_includes_retained_rsu():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-03-01", {
        "投资理财_股票基金_天天基金": 10000,
        "收入_主动收入_工资": 100000,
    })
    _insert_rsu_vest(conn, "2025-03-05", qty=10.0, price_cny=2000.0)  # 20000 CNY, never sold
    summary = contributions_summary_v2(conn)
    assert summary["rsu_retained_ttm"] == 20000.0
    assert summary["investment_numerator_ttm"] == 30000.0  # 10000 net external + 20000 retained
    assert summary["investment_rate_ttm"] == pytest.approx(30000.0 / 100000.0)
    conn.close()


def test_retained_rsu_outside_the_window_is_not_counted():
    """The retained figure is scoped to THIS function's own window bounds — it
    can never drift onto a different window (same discipline as the glide
    run-rate)."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-06-01", {"投资理财_股票基金_天天基金": 10000, "收入_主动收入_工资": 100000})
    _insert_rsu_vest(conn, "2020-01-15", qty=10.0, price_cny=2000.0)  # long before the window
    summary = contributions_summary_v2(conn)
    assert summary["rsu_retained_ttm"] == 0.0
    assert summary["investment_numerator_ttm"] == 10000.0
    conn.close()


def test_sold_rsu_is_not_retained():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-03-01", {"投资理财_股票基金_天天基金": 10000, "收入_主动收入_工资": 100000})
    _insert_rsu_vest(conn, "2025-03-05", qty=10.0, price_cny=2000.0)
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, quantity,
             price_unit, amount_gross, amount_net, currency, source_system, is_provisional)
        VALUES ('2025-03-20', 'RSU_AMZN', 'RSU_AMZN', 'sell', -10.0, 2500.0, 25000.0, 25000.0,
                'CNY', 'RSU_Excel', FALSE)
        """
    )
    summary = contributions_summary_v2(conn)
    assert summary["rsu_retained_ttm"] == 0.0, "sold shares are not retained inflow"
    conn.close()


def test_stock_sale_gain_stays_in_the_denominator():
    """收入_被动收入_股票卖出收益 is role='income', NOT 'redemption' — so unlike
    a fund redemption it is NOT subtracted from the income basis. This is the
    behaviour that must survive the owner filling that column in."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-04-01", {
        "投资理财_股票基金_天天基金": 20000,
        "收入_被动收入_股票卖出收益": 60000,
        "收入_被动收入_基金赎回": 5000,
        "收入_主动收入_工资": 135000,
    })
    summary = contributions_summary_v2(conn)
    assert summary["redemptions_ttm"] == 5000.0, "the realized gain is not a redemption"
    assert summary["income_basis_ttm"] == 195000.0, (
        "the gain is an income leaf and counts once; the redemption never enters the basis"
    )
    assert summary["income_ttm"] == 200000.0, "the Excel-equivalent total does include it"
    conn.close()


def test_income_basis_is_independent_of_redemptions():
    """A month that redeems far more than it earns: the basis is the income
    LEAF sum, so a redemption can neither inflate it (as the old
    总收入合计-based denominator did) nor drive it negative."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-11-01", {
        "投资理财_股票基金_Schawab": 278430,
        "收入_被动收入_基金赎回": 333258,
        "收入_主动收入_工资": 50000,
    })
    summary = contributions_summary_v2(conn)
    assert summary["income_basis_ttm"] == 50000.0
    assert summary["income_ttm"] == 383258.0, "Excel-equivalent gross income DOES include it"
    assert summary["net_external_ttm"] == 0.0, "redemptions still net out of contributions"
    conn.close()


def test_savings_rate_is_none_without_an_income_basis():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-11-01", {"收入_被动收入_基金赎回": 1000})
    summary = contributions_summary_v2(conn)
    assert summary["income_basis_ttm"] == 0.0
    assert summary["savings_rate_ttm"] is None
    assert summary["investment_rate_ttm"] is None
    conn.close()


def test_rates_match_the_live_window_target():
    """Synthetic acceptance scenario exercising the WS-D/WS-G formula fixes
    end-to-end (plan 2026-08-01 §WS-G / ADR-025 Amendment 2026-08-01).

    This used to reproduce the owner's real live-ledger window byte-for-byte;
    Program OSR replaced every figure with synthetic ones (persona-consistent,
    not derived from any real data) because the file must not ship real
    financial figures. The assertions below are generated from this fixture
    by actually running `contributions_summary_v2` — not hand-computed — so
    they test that the real function's output is internally consistent (the
    two defects WS-D/WS-G fixed stay fixed), not that it matches a specific
    historical number.

    Two defects the original incident fixed, still exercised here structurally:
      * the DENOMINATOR (WS-D): 总收入合计 must not double-count a redemption
        (added back as income while also subtracted from the numerator) or
        count the repaid 报销 as earnings;
      * the METRIC (WS-G): an *investment* rate must not wear the
        savings-rate label — money earned and left in the bank is saved,
        just not deployed. Both ship separately, and savings_rate_ttm must
        stay >= investment_rate_ttm (the gap is undeployed cash).

    RSU is seeded as a CNY-denominated vest at a fixed price — deliberately
    not a live-FX figure, per the same "never assert on a live-FX CNY number"
    rule as the real incident (`fx-constant` tech debt).

    Insurance columns use the WS-0 persona rename (安泰人生/公司团险/互联网保险)
    rather than the pre-rename real names — those renames are in the code's
    own ie_column baseline (Program OSR WS-3b), so this DB being schema-only
    (reader_mappings has zero rows, forcing the code-baseline classification
    path) is exercised the same way regardless of $UIS_SEED_PROFILE.
    """
    conn = _make_db()
    _insert_month(conn, "live", "2026-07-01", {
        # ── income leaves ──
        "收入_主动收入_工资": 150000.00,
        "收入_主动收入_RSU": 120000.00,
        "收入_主动收入_公积金": 40000.00,       # housing-fund withdrawals — owner says income
        "收入_主动收入_其他偶然": 30000.00,     # bonus — owner-confirmed income
        # ── not income: the two ends of the pass-through round trip ──
        "收入_主动收入_报销": 10000.00,
        "工作开支_出差/团建（全额报销）": 9500.00,
        # ── redemption ──
        "收入_被动收入_基金赎回": 200000.00,
        # ── invested (the Excel's 理财) ──
        "投资理财_股票基金_Schawab": 180000.00,
        "投资理财_股票基金_天天基金": 20000.00,
        "投资理财_银行理财_招行": 30000.00,
        "投资理财_黄金_招行纸黄金": 50000.00,
        # ── expense leaves ──
        "必要开支_日常支出_房租水电": 36000.00,
        "必要开支_贷款_房贷": 24000.00,
        "必要开支_日常支出_餐饮娱乐": 18000.00,
        "必要开支_保险_安泰人生": 3000.00,
        "必要开支_保险_公司团险": 2500.00,
        "必要开支_日常支出_交通": 2000.00,
        "必要开支_保险_互联网保险": 1800.00,
        "必要开支_家庭及临时支出": 1500.00,
        "非必要开支_旅行出游": 12000.00,
        "非必要开支_护肤衣物": 5000.00,
        "非必要开支_运动健身健康": 4000.00,
        "非必要开支_其他/娱乐": 3500.00,
        "非必要开支_电子产品": 1000.00,
        # ── native-currency siblings: the SAME money, already counted above in
        #    CNY. These must reach no total at all (Rule 2 at the ledger layer).
        "投资理财_股票基金_Schawab_USD": 25000.00,
        "收入_主动收入_RSU_USD": 16500.00,
    })
    _insert_rsu_vest(conn, "2026-07-15", qty=100.0, price_cny=800.00)

    summary = contributions_summary_v2(conn)

    # ── the two bases ──
    assert summary["income_basis_ttm"] == pytest.approx(340000.00)
    assert summary["expense_basis_ttm"] == pytest.approx(114300.00)
    assert summary["income_ttm"] == pytest.approx(550000.00)
    assert summary["redemptions_ttm"] == pytest.approx(200000.00)
    assert summary["pass_through_in_ttm"] == pytest.approx(10000.00)
    assert summary["pass_through_out_ttm"] == pytest.approx(9500.00)

    # ── the numerator ──
    assert summary["gross_invested_ttm"] == pytest.approx(280000.00)
    assert summary["net_external_ttm"] == pytest.approx(80000.00)
    assert summary["rsu_retained_ttm"] == pytest.approx(80000.00)
    assert summary["investment_numerator_ttm"] == pytest.approx(160000.00)

    # ── the two rates ──
    assert summary["savings_rate_ttm"] == pytest.approx(0.663824, abs=5e-6)
    assert summary["investment_rate_ttm"] == pytest.approx(0.470588, abs=5e-6)
    assert summary["undeployed_cash_ttm"] == pytest.approx(65700.00)

    # 报销 must not have touched the numerator — the reason it is not tagged
    # 'redemption'.
    assert summary["net_external_ttm"] == pytest.approx(280000.00 - 200000.00)
    # Neither _USD sibling reached a total.
    assert summary["by_destination_ttm"]["us_schwab"] == pytest.approx(180000.00)

    # Regression guard, data-independent: the pre-WS-D bug used raw income_ttm
    # (which still includes the redemption and the repaid 报销) as the
    # denominator instead of income_basis_ttm. Recomputed from this fixture's
    # own actual figures, not a pinned historical constant, so it stays valid
    # under any future data change to this test.
    old_buggy_denominator_rate = summary["net_external_ttm"] / summary["income_ttm"]
    assert summary["investment_rate_ttm"] != pytest.approx(old_buggy_denominator_rate, abs=5e-5), (
        "investment_rate_ttm must not collapse to net_external_ttm / income_ttm "
        "(the WS-D denominator bug) — got a value equal to the buggy formula's output"
    )
    assert summary["savings_rate_ttm"] >= summary["investment_rate_ttm"], (
        "saving is broader than deploying — the gap is undeployed cash"
    )
    conn.close()


def test_the_two_rates_reconcile_through_undeployed_cash():
    """The identity that keeps the pair honest: what you kept and did not
    deploy has to show up somewhere. Live window: 797,231.65 kept −
    549,937.94 deployed = 247,293.71 undeployed."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-05-01", {
        "收入_主动收入_工资": 100000,
        "必要开支_贷款_房贷": 30000,
        "投资理财_股票基金_天天基金": 25000,
    })
    summary = contributions_summary_v2(conn)
    kept = summary["income_basis_ttm"] - summary["expense_basis_ttm"]
    assert kept == pytest.approx(70000.0)
    assert summary["investment_numerator_ttm"] == pytest.approx(25000.0)
    assert summary["undeployed_cash_ttm"] == pytest.approx(kept - 25000.0)
    assert summary["savings_rate_ttm"] == pytest.approx(0.70)
    assert summary["investment_rate_ttm"] == pytest.approx(0.25)
    conn.close()


# ── pass_through (报销 / 工作开支) — one round trip, both ends excluded ──────

def test_pass_through_leaves_the_denominator_but_not_the_numerator():
    """The distinction that earns the round trip its own role: 'redemption'
    also subtracts from the investment numerator (net_external), which would
    punish a repayment as if it were money pulled back out of an investment."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-01-01", {
        "投资理财_股票基金_天天基金": 50000,
        "收入_主动收入_报销": 8000,
        "收入_主动收入_工资": 92000,
    })
    summary = contributions_summary_v2(conn)
    assert summary["pass_through_in_ttm"] == 8000.0
    assert summary["redemptions_ttm"] == 0.0, "报销 is NOT a redemption"
    assert summary["net_external_ttm"] == 50000.0, "numerator untouched by 报销"
    assert summary["income_basis_ttm"] == 92000.0, "the repayment is not earnings"
    assert summary["income_ttm"] == 100000.0, "but it IS money that arrived"
    assert summary["investment_rate_ttm"] == pytest.approx(50000.0 / 92000.0)
    conn.close()


def test_both_ends_of_the_round_trip_are_excluded_from_both_bases():
    """WS-G: the fronted spend and its repayment cancel. Excluding only the
    income half (the short-lived `reimbursement` role) would have deflated the
    savings rate by counting the fronted money as consumption."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-02-01", {
        "收入_主动收入_工资": 100000,
        "收入_主动收入_报销": 5000,
        "工作开支_出差/团建（全额报销）": 5000,
        "必要开支_贷款_房贷": 20000,
    })
    summary = contributions_summary_v2(conn)
    assert summary["income_basis_ttm"] == 100000.0
    assert summary["expense_basis_ttm"] == 20000.0, "the fronted spend is not consumption"
    assert summary["pass_through_in_ttm"] == 5000.0
    assert summary["pass_through_out_ttm"] == 5000.0
    assert summary["savings_rate_ttm"] == pytest.approx(0.80)
    # Half-fixing it (dropping only the inflow) would have given 75/100.
    assert summary["savings_rate_ttm"] != pytest.approx(0.75)
    conn.close()


def test_pass_through_and_redemption_both_leave_the_denominator():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-01-01", {
        "投资理财_股票基金_天天基金": 50000,
        "收入_被动收入_基金赎回": 20000,
        "收入_主动收入_报销": 5000,
        "收入_主动收入_工资": 75000,
    })
    summary = contributions_summary_v2(conn)
    assert summary["income_basis_ttm"] == 75000.0
    assert summary["income_ttm"] == 100000.0
    assert summary["net_external_ttm"] == 30000.0, "only the redemption nets out of contributions"
    assert summary["investment_rate_ttm"] == pytest.approx(30000.0 / 75000.0)
    conn.close()


def test_housing_fund_and_bonus_stay_in_the_denominator():
    """Owner decisions 2026-08-01: 公积金 withdrawals count as income (the
    housing-fund balance is not a tracked asset, so the money enters the system
    from outside) and 其他偶然 is bonus money. Neither is excluded."""
    conn = _make_db()
    _insert_month(conn, "m1", "2025-10-01", {
        "投资理财_股票基金_天天基金": 10000,
        "收入_主动收入_公积金": 96720,
        "收入_主动收入_其他偶然": 14400,
        "收入_主动收入_工资": 200000,
    })
    summary = contributions_summary_v2(conn)
    assert summary["pass_through_in_ttm"] == 0.0
    assert summary["income_basis_ttm"] == 311120.0, "both columns are income leaves"
    conn.close()


def test_pass_through_is_per_month_in_the_series():
    conn = _make_db()
    _insert_month(conn, "m1", "2025-09-01", {
        "收入_主动收入_报销": 7116.0, "收入_主动收入_工资": 218254.24,
        "工作开支_出差/团建（全额报销）": 6216.0,
    })
    _insert_month(conn, "m2", "2025-10-01", {
        "收入_主动收入_报销": 0, "收入_主动收入_工资": 192138.22,
    })
    _insert_month(conn, "m3", "2025-12-01", {
        "收入_主动收入_报销": 30348.38, "收入_主动收入_工资": 213099.47,
        "工作开支_出差/团建（全额报销）": 31248.38,
    })
    series = monthly_investment_flows(conn)
    assert [m["pass_through_in"] for m in series] == [7116.0, 0.0, 30348.38]
    assert [m["pass_through_out"] for m in series] == [6216.0, 0.0, 31248.38]
    summary = contributions_summary_v2(conn)
    assert summary["pass_through_in_ttm"] == pytest.approx(37464.38)
    assert summary["pass_through_out_ttm"] == pytest.approx(37464.38)
    conn.close()


def test_savings_rate_is_none_when_there_is_no_window():
    conn = _make_db()
    summary = contributions_summary_v2(conn, window_months=0)
    assert summary["savings_rate_ttm"] is None
    assert summary["rsu_retained_ttm"] == 0.0
    conn.close()
