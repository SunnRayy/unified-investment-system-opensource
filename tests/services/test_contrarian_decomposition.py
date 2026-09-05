"""Tests for F5 contrarian metric decomposition (PRD 2026-07-07 §F5).

Uses a minimal in-memory DuckDB (trade_logs + market_daily) rather than the
full schema, matching the existing lightweight-fixture pattern used by
tests/services/test_decision_scorer_maturity.py. All fixture dates are
relative to date.today() so the trade_logs.log_date >= CURRENT_DATE - INTERVAL
window_days DAY filter always includes them, regardless of when the suite runs.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.database.connector import DatabaseConnector
from src.services.ai_advisor.behavioral_metrics import BehavioralMetricsComputer


def _setup_db() -> DatabaseConnector:
    """Minimal in-memory DB: trade_logs (with order_origin) + market_daily."""
    db = DatabaseConnector(":memory:")
    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_trade_logs_id START 1")
    db.execute("""
        CREATE TABLE trade_logs (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_trade_logs_id'),
            log_date DATE NOT NULL,
            asset_id VARCHAR(50) NOT NULL,
            action VARCHAR(20) NOT NULL,
            order_origin VARCHAR(20)
        )
    """)
    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_market_daily_id START 1")
    db.execute("""
        CREATE TABLE market_daily (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_market_daily_id'),
            code VARCHAR(20) NOT NULL,
            date DATE NOT NULL,
            close DECIMAL(20,4)
        )
    """)
    # _resolve_market_codes falls back to asset_source_mappings — create it
    # empty so the primary-code regex path is exercised without errors.
    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_asm_id START 1")
    db.execute("""
        CREATE TABLE asset_source_mappings (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_asm_id'),
            canonical_id VARCHAR(50) NOT NULL,
            source_system VARCHAR(50) NOT NULL,
            source_id VARCHAR(100) NOT NULL
        )
    """)
    # Empty transactions table — only needed so the legacy _contrarian_tendency
    # method (still emitted for backward compatibility) runs its real
    # "insufficient data" path instead of raising/falling back generically.
    db.execute("""
        CREATE TABLE transactions (
            transaction_date DATE,
            asset_id VARCHAR(50),
            transaction_type VARCHAR(20)
        )
    """)
    return db


def _insert_trade(db, asset_id: str, log_date: date, order_origin: str | None) -> None:
    db.execute(
        "INSERT INTO trade_logs (log_date, asset_id, action, order_origin) VALUES (?, ?, 'Buy', ?)",
        [log_date, asset_id, order_origin],
    )


def _insert_price_series(db, code: str, base_date: date, closes: list[float]) -> None:
    """Insert one row per day starting at base_date, ascending, 1 calendar day apart."""
    for i, close in enumerate(closes):
        db.execute(
            "INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
            [code, base_date + timedelta(days=i), close],
        )


# A 10-point descending-after-peak series: peak (100) at index 4, trough (85) at
# index 9. Rolling max over the trailing 10 trading days ending at index 9 = 100.
# drawdown at index 9 = (100-85)/100*100 = 15% >= default threshold (5%).
_DRAWDOWN_SERIES = [80, 85, 90, 95, 100, 98, 95, 92, 88, 85]


def _dim(results, dimension: str):
    matches = [r for r in results if r.dimension == dimension]
    assert matches, f"no MetricResult for dimension={dimension!r}"
    return matches[0]


# ---------------------------------------------------------------------------
# Acceptance 1: IBKR-recurring-style (auto_dca) buy during drawdown counts
# systematic, not manual.
# ---------------------------------------------------------------------------

def test_auto_dca_buy_in_drawdown_counts_systematic_not_manual():
    db = _setup_db()
    base = date.today() - timedelta(days=30)
    _insert_price_series(db, "BRKB", base, _DRAWDOWN_SERIES)
    buy_date = base + timedelta(days=9)  # trough day, index 9
    _insert_trade(db, "US_STK_BRKB", buy_date, "auto_dca")

    computer = BehavioralMetricsComputer(":memory:")
    results = computer.compute_all(window_days=90, conn=db)

    systematic = _dim(results, "systematic_contrarian")
    manual = _dim(results, "manual_contrarian")

    assert systematic.metadata["systematic_contrarian_buys"] == 1
    assert systematic.metadata["systematic_total_buys"] == 1
    assert systematic.score == pytest.approx(1.0)

    # Manual dimension sees zero manual-tagged buys at all — "no data", not a
    # false zero.
    assert manual.metadata["untagged_count"] == 0
    assert manual.label == "No data"


def test_conditional_order_buy_in_drawdown_counts_systematic():
    """conditional_order (GTC-fill style) also buckets into systematic, not manual."""
    db = _setup_db()
    base = date.today() - timedelta(days=30)
    _insert_price_series(db, "GLD", base, _DRAWDOWN_SERIES)
    buy_date = base + timedelta(days=9)
    _insert_trade(db, "US_ETF_GLD", buy_date, "conditional_order")

    computer = BehavioralMetricsComputer(":memory:")
    results = computer.compute_all(window_days=90, conn=db)
    systematic = _dim(results, "systematic_contrarian")

    assert systematic.metadata["systematic_contrarian_buys"] == 1
    assert systematic.metadata["systematic_total_buys"] == 1


# ---------------------------------------------------------------------------
# Acceptance 2: three manual buys within a drawdown window in one month raise
# the alert (via the rate path — 3/3 manual buys = 100% > 30% threshold).
# ---------------------------------------------------------------------------

def test_three_manual_dip_buys_in_one_month_raises_alert():
    db = _setup_db()
    base = date.today() - timedelta(days=30)
    _insert_price_series(db, "QQQ", base, _DRAWDOWN_SERIES)
    # Indices 6, 7, 8 all sit at/after the peak with a >=5% drawdown from the
    # index-4 peak (100): (100-95)/100=5%, (100-92)/100=8%, (100-88)/100=12%.
    for idx in (6, 7, 8):
        _insert_trade(db, "US_ETF_QQQ", base + timedelta(days=idx), "manual")

    computer = BehavioralMetricsComputer(":memory:")
    results = computer.compute_all(window_days=90, conn=db)
    manual = _dim(results, "manual_contrarian")

    assert manual.metadata["manual_contrarian_buys"] == 3
    assert manual.metadata["manual_total_buys"] == 3
    assert manual.raw_value == pytest.approx(100.0)
    assert manual.metadata["alert"] is True
    # Neutral score regardless of the (high) rate — this dimension must never
    # be rewarded via radar geometry.
    assert manual.score == pytest.approx(0.5)


def test_manual_contrarian_alert_false_when_below_both_thresholds():
    """A single manual dip-buy among several non-dip manual buys should not alert."""
    db = _setup_db()
    base = date.today() - timedelta(days=30)
    _insert_price_series(db, "VOO", base, _DRAWDOWN_SERIES)
    # One contrarian buy (index 9, trough).
    _insert_trade(db, "US_ETF_VOO", base + timedelta(days=9), "manual")
    # 9 non-contrarian buys on a DIFFERENT, perfectly flat instrument (distinct
    # market code — a flat series appended to the same code would still "see"
    # the first series' peak inside its trailing rolling-max window).
    _insert_price_series(db, "STBL", base, [50] * 15)
    for i in range(9):
        _insert_trade(db, "US_ETF_STBL", base + timedelta(days=i + 1), "manual")

    computer = BehavioralMetricsComputer(":memory:")
    results = computer.compute_all(window_days=90, conn=db)
    manual = _dim(results, "manual_contrarian")

    assert manual.metadata["manual_total_buys"] == 10
    assert manual.metadata["manual_contrarian_buys"] == 1
    assert manual.raw_value == pytest.approx(10.0)
    assert manual.metadata["alert"] is False


# ---------------------------------------------------------------------------
# Acceptance 3: untagged historical orders are excluded with a visible
# "n untagged" count, never silently defaulted to either side.
# ---------------------------------------------------------------------------

def test_untagged_buys_excluded_with_visible_count():
    db = _setup_db()
    base = date.today() - timedelta(days=30)
    _insert_price_series(db, "AAPL", base, _DRAWDOWN_SERIES)
    _insert_trade(db, "US_STK_AAPL", base + timedelta(days=9), "auto_dca")
    # Two untagged (NULL order_origin) buys — must not count toward either side.
    _insert_trade(db, "US_STK_AAPL", base + timedelta(days=1), None)
    _insert_trade(db, "US_STK_AAPL", base + timedelta(days=2), None)

    computer = BehavioralMetricsComputer(":memory:")
    results = computer.compute_all(window_days=90, conn=db)

    systematic = _dim(results, "systematic_contrarian")
    manual = _dim(results, "manual_contrarian")

    assert systematic.metadata["untagged_count"] == 2
    assert manual.metadata["untagged_count"] == 2
    # Untagged rows never counted in the systematic denominator either.
    assert systematic.metadata["systematic_total_buys"] == 1


def test_all_buys_untagged_returns_no_data_shape_for_both_dimensions():
    db = _setup_db()
    base = date.today() - timedelta(days=30)
    _insert_price_series(db, "MSFT", base, _DRAWDOWN_SERIES)
    _insert_trade(db, "US_STK_MSFT", base + timedelta(days=9), None)
    _insert_trade(db, "US_STK_MSFT", base + timedelta(days=1), "")

    computer = BehavioralMetricsComputer(":memory:")
    results = computer.compute_all(window_days=90, conn=db)

    systematic = _dim(results, "systematic_contrarian")
    manual = _dim(results, "manual_contrarian")

    for dim in (systematic, manual):
        assert dim.score == pytest.approx(0.5)
        assert dim.label == "No data"
        assert dim.metadata["untagged_count"] == 2


# ---------------------------------------------------------------------------
# Acceptance 4: buys with no market_daily price data are excluded honestly,
# not silently folded into either the numerator or denominator.
# ---------------------------------------------------------------------------

def test_no_price_data_buy_excluded_honestly():
    db = _setup_db()
    base = date.today() - timedelta(days=30)
    # NOTE: no market_daily rows inserted for this asset/code at all.
    _insert_trade(db, "US_STK_NODATA", base + timedelta(days=9), "auto_dca")

    computer = BehavioralMetricsComputer(":memory:")
    results = computer.compute_all(window_days=90, conn=db)
    systematic = _dim(results, "systematic_contrarian")

    assert systematic.label == "No price data"
    assert systematic.metadata["excluded_no_price_count"] == 1
    assert systematic.score == pytest.approx(0.5)


def test_no_price_data_excluded_from_denominator_when_other_priced_buys_exist():
    db = _setup_db()
    base = date.today() - timedelta(days=30)
    _insert_price_series(db, "IBM", base, _DRAWDOWN_SERIES)
    _insert_trade(db, "US_STK_IBM", base + timedelta(days=9), "auto_dca")  # priced, contrarian
    _insert_trade(db, "US_STK_NOPRICE", base + timedelta(days=9), "auto_dca")  # no price data

    computer = BehavioralMetricsComputer(":memory:")
    results = computer.compute_all(window_days=90, conn=db)
    systematic = _dim(results, "systematic_contrarian")

    # Denominator counts only the priced buy — the no-price buy is excluded,
    # not folded in as a non-contrarian buy.
    assert systematic.metadata["systematic_total_buys"] == 1
    assert systematic.metadata["systematic_contrarian_buys"] == 1
    assert systematic.metadata["excluded_no_price_count"] == 1


# ---------------------------------------------------------------------------
# Legacy dimension: kept unchanged, tagged deprecated.
# ---------------------------------------------------------------------------

def test_legacy_contrarian_tendency_tagged_deprecated():
    db = _setup_db()
    computer = BehavioralMetricsComputer(":memory:")
    results = computer.compute_all(window_days=90, conn=db)
    legacy = _dim(results, "contrarian_tendency")

    assert legacy.metadata is not None
    assert legacy.metadata["deprecated"] is True
    assert legacy.metadata["replaced_by"] == ["systematic_contrarian", "manual_contrarian"]
