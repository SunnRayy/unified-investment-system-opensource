"""Tests for technical context enrichment in ContextBuilder."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import duckdb


def _make_builder(db=None):
    from src.services.ai_advisor.context_builder import ContextBuilder

    cb = ContextBuilder.__new__(ContextBuilder)
    cb._db = db if db is not None else MagicMock()
    return cb


def _make_result(rows):
    result = MagicMock()
    result.fetchall.return_value = rows
    return result


def _signals(
    *,
    trend_status="BULL",
    rsi_value=56.1,
    rsi_status="NEUTRAL",
    ma_alignment_score=2,
    macd_status="BULLISH",
    volume_status="NORMAL",
    signal_score=73,
    support_levels=None,
    resistance_levels=None,
):
    return {
        "trend_status": trend_status,
        "rsi_value": rsi_value,
        "rsi_status": rsi_status,
        "ma_alignment_score": ma_alignment_score,
        "macd_status": macd_status,
        "volume_status": volume_status,
        "signal_score": signal_score,
        "support_levels": support_levels or [],
        "resistance_levels": resistance_levels or [],
    }


def test_no_analyses_returns_empty():
    db = MagicMock()

    def execute_side_effect(sql, params=None):
        if "FROM holdings" in sql:
            return _make_result([("US_STK_AAPL",)])
        if "FROM asset_analyses" in sql:
            return _make_result([])
        raise AssertionError(f"Unexpected SQL: {sql}")

    db.execute.side_effect = execute_side_effect
    cb = _make_builder(db)

    result = cb.build_technical_context()

    assert result == ""


def test_old_analyses_excluded():
    db = MagicMock()
    old_created_at = datetime.now() - timedelta(hours=25)

    def execute_side_effect(sql, params=None):
        if "FROM holdings" in sql:
            return _make_result([("US_STK_AAPL",)])
        if "FROM asset_analyses" in sql:
            assert "INTERVAL '24 hours'" in sql
            assert old_created_at < datetime.now() - timedelta(hours=24)
            return _make_result([])
        raise AssertionError(f"Unexpected SQL: {sql}")

    db.execute.side_effect = execute_side_effect
    cb = _make_builder(db)

    result = cb.build_technical_context()

    assert result == ""


def test_only_held_assets():
    db = MagicMock()

    def execute_side_effect(sql, params=None):
        if "FROM holdings" in sql:
            return _make_result([("US_STK_AAPL",)])
        if "FROM asset_analyses" in sql:
            held_codes = set(params[0])
            assert held_codes == {"US_STK_AAPL", "AAPL"}
            return _make_result([])
        raise AssertionError(f"Unexpected SQL: {sql}")

    db.execute.side_effect = execute_side_effect
    cb = _make_builder(db)

    result = cb.build_technical_context()

    assert result == ""


def test_deduplicates_to_most_recent():
    db = MagicMock()
    newest = datetime.now() - timedelta(hours=2)
    signals = json.dumps(_signals())

    def execute_side_effect(sql, params=None):
        if "FROM holdings" in sql:
            return _make_result([("US_STK_AAPL",)])
        if "FROM asset_analyses" in sql:
            assert "ROW_NUMBER() OVER" in sql
            assert "WHERE rn = 1" in sql
            return _make_result([("US_STK_AAPL", "Apple", signals, newest)])
        raise AssertionError(f"Unexpected SQL: {sql}")

    db.execute.side_effect = execute_side_effect
    cb = _make_builder(db)

    result = cb.build_technical_context()

    assert result.startswith("## 近期技术分析")
    assert result.count("US_STK_AAPL(Apple)") == 1


def test_detail_levels_summary():
    db = MagicMock()
    created_at = datetime.now() - timedelta(minutes=45)
    signals = json.dumps(_signals())

    def execute_side_effect(sql, params=None):
        if "FROM holdings" in sql:
            return _make_result([("US_STK_AAPL",)])
        if "FROM asset_analyses" in sql:
            return _make_result([("US_STK_AAPL", "Apple", signals, created_at)])
        raise AssertionError(f"Unexpected SQL: {sql}")

    db.execute.side_effect = execute_side_effect
    cb = _make_builder(db)

    result = cb.build_technical_context(detail="summary")

    assert "BULL" in result
    assert "RSI=56.1(NEUTRAL)" in result
    assert "评分73/100" in result
    assert "MACD=" not in result
    assert "量能=" not in result


def test_detail_levels_full():
    db = MagicMock()
    created_at = datetime.now() - timedelta(hours=3)
    signals = _signals(
        support_levels=[95.0, 92.5],
        resistance_levels=[105.0, 110.0],
    )

    def execute_side_effect(sql, params=None):
        if "FROM holdings" in sql:
            return _make_result([("US_STK_AAPL",)])
        if "FROM asset_analyses" in sql:
            return _make_result([("US_STK_AAPL", "Apple", signals, created_at)])
        raise AssertionError(f"Unexpected SQL: {sql}")

    db.execute.side_effect = execute_side_effect
    cb = _make_builder(db)

    result = cb.build_technical_context(detail="full")

    assert "MACD=BULLISH" in result
    assert "量能=NORMAL" in result
    assert "支撑=" in result
    assert "95.0" in result
    assert "阻力=" in result
    assert "110.0" in result


def test_integration_duckdb():
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE SEQUENCE seq_holdings_id START 1")
    conn.execute(
        """
        CREATE TABLE holdings (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_holdings_id'),
            snapshot_date DATE NOT NULL,
            asset_id VARCHAR(50) NOT NULL,
            asset_name VARCHAR(200),
            asset_type VARCHAR(100),
            quantity DECIMAL(20,8),
            unit VARCHAR(20),
            cost_price_unit DECIMAL(20,8),
            market_price_unit DECIMAL(20,8),
            market_value DECIMAL(20,2),
            currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
            account VARCHAR(100),
            source_system VARCHAR(50),
            derived_from_transaction_id INTEGER,
            verified BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_shadow BOOLEAN DEFAULT FALSE,
            authority_source VARCHAR(50),
            price_updated_at TIMESTAMP
        )
        """
    )
    conn.execute("CREATE SEQUENCE asset_analyses_seq START 1")
    conn.execute(
        """
        CREATE TABLE asset_analyses (
            id INTEGER PRIMARY KEY DEFAULT nextval('asset_analyses_seq'),
            asset_code VARCHAR(20) NOT NULL,
            asset_name VARCHAR(200),
            analysis_type VARCHAR(20) DEFAULT 'full',
            technical_signals JSON,
            llm_analysis JSON,
            llm_analysis_markdown VARCHAR,
            portfolio_context JSON,
            model_used VARCHAR,
            data_source VARCHAR(50),
            triggered_by VARCHAR(20),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, quantity, market_value, source_system, is_shadow
        )
        VALUES ('2026-03-27', 'US_STK_AAPL', 'Apple Inc.', 10, 100000, 'Schwab_CSV', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO asset_analyses (
            asset_code, asset_name, analysis_type, technical_signals, created_at
        )
        VALUES (?, ?, 'full', ?, ?)
        """,
        [
            "US_STK_AAPL",
            "Apple Inc.",
            json.dumps(_signals()),
            datetime.now(),
        ],
    )

    cb = _make_builder(conn)

    result = cb.build_technical_context()

    assert result
    assert "US_STK_AAPL" in result


def test_integration_stale_row_excluded():
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE SEQUENCE seq_holdings_id_stale START 1")
    conn.execute(
        """
        CREATE TABLE holdings (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_holdings_id_stale'),
            snapshot_date DATE NOT NULL,
            asset_id VARCHAR(50) NOT NULL,
            asset_name VARCHAR(200),
            asset_type VARCHAR(100),
            quantity DECIMAL(20,8),
            unit VARCHAR(20),
            cost_price_unit DECIMAL(20,8),
            market_price_unit DECIMAL(20,8),
            market_value DECIMAL(20,2),
            currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
            account VARCHAR(100),
            source_system VARCHAR(50),
            derived_from_transaction_id INTEGER,
            verified BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_shadow BOOLEAN DEFAULT FALSE,
            authority_source VARCHAR(50),
            price_updated_at TIMESTAMP
        )
        """
    )
    conn.execute("CREATE SEQUENCE asset_analyses_seq_stale START 1")
    conn.execute(
        """
        CREATE TABLE asset_analyses (
            id INTEGER PRIMARY KEY DEFAULT nextval('asset_analyses_seq_stale'),
            asset_code VARCHAR(20) NOT NULL,
            asset_name VARCHAR(200),
            analysis_type VARCHAR(20) DEFAULT 'full',
            technical_signals JSON,
            llm_analysis JSON,
            llm_analysis_markdown VARCHAR,
            portfolio_context JSON,
            model_used VARCHAR,
            data_source VARCHAR(50),
            triggered_by VARCHAR(20),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, quantity, market_value, source_system, is_shadow
        )
        VALUES ('2026-03-27', 'US_STK_MSFT', 'Microsoft Corp.', 5, 80000, 'Schwab_CSV', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO asset_analyses (
            asset_code, asset_name, analysis_type, technical_signals, created_at
        )
        VALUES (?, ?, 'full', ?, ?)
        """,
        [
            "US_STK_MSFT",
            "Microsoft Corp.",
            json.dumps(_signals()),
            datetime.now() - timedelta(hours=25),
        ],
    )

    cb = _make_builder(conn)

    result = cb.build_technical_context()

    assert result == ""
