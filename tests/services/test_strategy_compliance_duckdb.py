"""Runtime DuckDB behavioral tests for BehavioralMetricsComputer._strategy_compliance().

All tests use a real DuckDB file in tmp_path — actual SQL is executed, no mocking.
This catches query-level bugs that source-inspection or mock-based tests cannot.

Schema: minimal DDL covering the 3 tables _strategy_compliance() queries:
  - strategy_memos  (memo_date, title, key_directives, content)
  - transactions    (transaction_date, asset_id, transaction_type)
  - asset_registry  (canonical_id, asset_class)
"""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

from src.services.ai_advisor.behavioral_metrics import BehavioralMetricsComputer


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

AI_SINCE = date(2025, 10, 1)          # earliest memo_date → ai_active_since
POST_AI = date(2025, 11, 1)           # a trade date AFTER ai_since
PRE_AI = date(2025, 9, 1)            # a trade date BEFORE ai_since


@pytest.fixture
def db_path(tmp_path):
    """Write-once DuckDB file with minimal schema for _strategy_compliance() tests.

    Returns the file path as a string.  BehavioralMetricsComputer opens its
    own read-only connections, so the write connection is closed before yielding.
    """
    path = str(tmp_path / "test_compliance.duckdb")
    conn = duckdb.connect(path)

    conn.execute("""
        CREATE TABLE strategy_memos (
            id          INTEGER PRIMARY KEY,
            memo_date   DATE NOT NULL,
            title       VARCHAR,
            key_directives VARCHAR,
            content     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE transactions (
            id               INTEGER PRIMARY KEY,
            transaction_date DATE    NOT NULL,
            asset_id         VARCHAR NOT NULL,
            transaction_type VARCHAR NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE asset_registry (
            id           INTEGER PRIMARY KEY,
            canonical_id VARCHAR NOT NULL,
            asset_class  VARCHAR
        )
    """)

    conn.close()
    yield path


def _insert_memo(conn, id_, memo_date, title="", content=""):
    conn.execute(
        "INSERT INTO strategy_memos (id, memo_date, title, key_directives, content) VALUES (?,?,?,?,?)",
        (id_, memo_date, title, None, content),
    )


def _insert_tx(conn, id_, asset_id, tx_date, tx_type="buy"):
    conn.execute(
        "INSERT INTO transactions (id, transaction_date, asset_id, transaction_type) VALUES (?,?,?,?)",
        (id_, tx_date, asset_id, tx_type),
    )


def _insert_asset(conn, id_, canonical_id, asset_class="US Equity"):
    conn.execute(
        "INSERT INTO asset_registry (id, canonical_id, asset_class) VALUES (?,?,?)",
        (id_, canonical_id, asset_class),
    )


# ---------------------------------------------------------------------------
# Test 1: All AI-era trades matched by ticker — score == 1.0
# ---------------------------------------------------------------------------

def test_full_ticker_match_score_is_one(db_path):
    """When every AI-era traded asset appears in memo text, score must equal 1.0."""
    conn = duckdb.connect(db_path)
    # One memo dated AI_SINCE (sets the ai_active_since floor)
    _insert_memo(conn, 1, AI_SINCE, title="AAPL analysis", content="Buy AAPL on weakness")
    # One post-AI trade for AAPL
    _insert_tx(conn, 1, "US_STK_AAPL", POST_AI)
    # asset_registry entry so ticker 'AAPL' is recognised
    _insert_asset(conn, 1, "US_STK_AAPL", "US Equity")
    conn.close()

    computer = BehavioralMetricsComputer(db_path=db_path)
    result = computer._strategy_compliance(90)

    assert result.dimension == "strategy_compliance"
    assert result.score == pytest.approx(1.0, abs=1e-4), (
        f"Expected score=1.0 when all trades covered by memos, got {result.score}"
    )
    assert result.raw_value == pytest.approx(100.0, abs=1e-4)
    assert str(AI_SINCE) in result.label


# ---------------------------------------------------------------------------
# Test 2: Partial match — only 1 of 2 traded assets mentioned in memos
# ---------------------------------------------------------------------------

def test_partial_ticker_match_score(db_path):
    """When 1 of 2 AI-era trades is covered, score must equal 0.5."""
    conn = duckdb.connect(db_path)
    _insert_memo(conn, 1, AI_SINCE, title="AAPL memo", content="AAPL long thesis")
    _insert_tx(conn, 1, "US_STK_AAPL", POST_AI)   # covered
    _insert_tx(conn, 2, "US_STK_MSFT", POST_AI)   # NOT covered by any memo
    _insert_asset(conn, 1, "US_STK_AAPL", "US Equity")
    _insert_asset(conn, 2, "US_STK_MSFT", "US Equity")
    conn.close()

    computer = BehavioralMetricsComputer(db_path=db_path)
    result = computer._strategy_compliance(90)

    assert result.score == pytest.approx(0.5, abs=1e-4), (
        f"Expected 0.5 for 1/2 covered, got {result.score}"
    )


# ---------------------------------------------------------------------------
# Test 3: Pre-AI trades must NOT appear in the denominator
# ---------------------------------------------------------------------------

def test_pre_ai_trades_excluded_from_denominator(db_path):
    """Transactions before ai_active_since must be excluded — denominator = post-AI only."""
    conn = duckdb.connect(db_path)
    _insert_memo(conn, 1, AI_SINCE, title="AAPL memo", content="AAPL thesis")
    # Pre-AI trade for MSFT (should not appear in denominator)
    _insert_tx(conn, 1, "US_STK_MSFT", PRE_AI)
    # Post-AI trade for AAPL (covered by memo)
    _insert_tx(conn, 2, "US_STK_AAPL", POST_AI)
    _insert_asset(conn, 1, "US_STK_AAPL", "US Equity")
    _insert_asset(conn, 2, "US_STK_MSFT", "US Equity")
    conn.close()

    computer = BehavioralMetricsComputer(db_path=db_path)
    result = computer._strategy_compliance(90)

    # Only AAPL is in denominator (post-AI), and it matches → score = 1.0
    assert result.score == pytest.approx(1.0, abs=1e-4), (
        f"Pre-AI MSFT trade should not count in denominator; expected score=1.0, got {result.score}"
    )


# ---------------------------------------------------------------------------
# Test 4: No trades after ai_since → fallback with score=0.5
# ---------------------------------------------------------------------------

def test_no_trades_in_ai_window_returns_fallback(db_path):
    """With a memo but no post-AI trades, total_trades=0 → score=0.5 fallback."""
    conn = duckdb.connect(db_path)
    _insert_memo(conn, 1, AI_SINCE, title="market overview", content="stay in cash")
    # Only a pre-AI trade exists
    _insert_tx(conn, 1, "US_STK_MSFT", PRE_AI)
    _insert_asset(conn, 1, "US_STK_MSFT", "US Equity")
    conn.close()

    computer = BehavioralMetricsComputer(db_path=db_path)
    result = computer._strategy_compliance(90)

    assert result.score == 0.5
    assert "No trades" in result.label or "no trades" in result.label.lower()


# ---------------------------------------------------------------------------
# Test 5: CN fund matched via class-keyword CTE (not ticker)
# ---------------------------------------------------------------------------

def test_cn_fund_matched_via_class_keyword(db_path):
    """CN funds with numeric codes must match via class_keywords CTE (A股/QDII etc.)."""
    conn = duckdb.connect(db_path)
    # Memo mentions A股 (CN Equity keyword)
    _insert_memo(conn, 1, AI_SINCE, title="A股配置策略", content="继续持有A股基金")
    # CN fund with numeric code — symbol part is '000001' (numeric, won't match ticker CTE)
    _insert_tx(conn, 1, "CN_FUND_000001", POST_AI)
    _insert_asset(conn, 1, "CN_FUND_000001", "CN Equity")
    conn.close()

    computer = BehavioralMetricsComputer(db_path=db_path)
    result = computer._strategy_compliance(90)

    assert result.score == pytest.approx(1.0, abs=1e-4), (
        f"CN fund with 'A股' keyword in memo should match via class_keywords CTE, got score={result.score}"
    )


# ---------------------------------------------------------------------------
# Test 6: No strategy memos at all → ai_active_since = None → score=0.5
# ---------------------------------------------------------------------------

def test_no_memos_returns_no_ai_data_fallback(db_path):
    """When strategy_memos is empty, _ai_active_since returns None → score=0.5 fallback."""
    # No rows inserted — strategy_memos is empty
    conn = duckdb.connect(db_path)
    _insert_tx(conn, 1, "US_STK_AAPL", POST_AI)
    _insert_asset(conn, 1, "US_STK_AAPL", "US Equity")
    conn.close()

    computer = BehavioralMetricsComputer(db_path=db_path)
    result = computer._strategy_compliance(90)

    assert result.score == 0.5
    assert result.label == "No AI advisory data yet"


# ---------------------------------------------------------------------------
# Test 7: Sell transactions in AI window count in denominator
# ---------------------------------------------------------------------------

def test_sell_transactions_count_in_denominator(db_path):
    """Sell transactions in the AI window should appear in the denominator."""
    conn = duckdb.connect(db_path)
    _insert_memo(conn, 1, AI_SINCE, title="AAPL sell signal", content="AAPL risk elevated")
    # AAPL sell (covered), MSFT sell (not covered)
    _insert_tx(conn, 1, "US_STK_AAPL", POST_AI, tx_type="sell")
    _insert_tx(conn, 2, "US_STK_MSFT", POST_AI, tx_type="sell")
    _insert_asset(conn, 1, "US_STK_AAPL", "US Equity")
    _insert_asset(conn, 2, "US_STK_MSFT", "US Equity")
    conn.close()

    computer = BehavioralMetricsComputer(db_path=db_path)
    result = computer._strategy_compliance(90)

    # 1 of 2 sell assets covered → 0.5
    assert result.score == pytest.approx(0.5, abs=1e-4)


# ---------------------------------------------------------------------------
# Test 8: effective_window_days reflects ai_since, not window_days argument
# ---------------------------------------------------------------------------

def test_effective_window_days_based_on_ai_since(db_path):
    """computation_window_days in result must reflect days since ai_since, not window_days arg."""
    conn = duckdb.connect(db_path)
    _insert_memo(conn, 1, AI_SINCE, title="test", content="AAPL")
    _insert_tx(conn, 1, "US_STK_AAPL", POST_AI)
    _insert_asset(conn, 1, "US_STK_AAPL", "US Equity")
    conn.close()

    computer = BehavioralMetricsComputer(db_path=db_path)
    result = computer._strategy_compliance(window_days=90)

    expected_days = (date.today() - AI_SINCE).days
    # Must NOT be 90 (the argument) — should reflect the actual AI-active window
    assert result.computation_window_days == expected_days, (
        f"Expected computation_window_days={expected_days} (days since ai_since), "
        f"got {result.computation_window_days}"
    )
