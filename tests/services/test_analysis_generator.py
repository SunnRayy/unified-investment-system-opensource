"""Tests for single-asset analysis pipeline and generator."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import duckdb
import pandas as pd

from src.analysis.models import (
    MACDStatus,
    RSIStatus,
    TechnicalSignals,
    TrendStatus,
    VolumeStatus,
)
from src.database.connector import DatabaseConnector


class _NoCloseConn:
    """DuckDB wrapper used in tests to keep shared in-memory DB alive."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def close(self):
        return None


def _make_signals() -> TechnicalSignals:
    return TechnicalSignals(
        trend_status=TrendStatus.BULL,
        ma5=101.2,
        ma10=100.1,
        ma20=98.7,
        ma_alignment_score=2,
        rsi_value=56.1,
        rsi_status=RSIStatus.NEUTRAL,
        macd_line=0.21,
        macd_signal=0.11,
        macd_hist=0.10,
        macd_status=MACDStatus.BULLISH,
        volume_ratio=1.2,
        volume_status=VolumeStatus.NORMAL,
        support_levels=[95.0, 92.5],
        resistance_levels=[105.0, 110.0],
        signal_score=73,
        trend_direction_score=52,
    )


def _setup_context_builder_with_db(conn: duckdb.DuckDBPyConnection):
    from src.services.ai_advisor.context_builder import ContextBuilder

    cb = ContextBuilder.__new__(ContextBuilder)
    cb._db = conn
    return cb


def test_migration_idempotent():
    connector = DatabaseConnector(":memory:")
    connector.run_migrations()
    connector.run_migrations()

    row = connector.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_name = 'asset_analyses'
        """
    ).fetchone()

    assert row is not None
    connector.close()


def test_build_asset_context_held_asset():
    from src.services.ai_advisor.context_builder import ContextBuilder

    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE holdings (
            asset_id VARCHAR,
            asset_name VARCHAR,
            quantity DOUBLE,
            cost_price_unit DOUBLE,
            market_value DOUBLE,
            market_price_unit DOUBLE,
            currency VARCHAR,
            snapshot_date DATE,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        INSERT INTO holdings VALUES
            ('US_STK_AAPL', 'Apple Inc.', 20, 100, 14000, 700, 'USD', '2026-03-20', FALSE),
            ('US_STK_AAPL', 'Apple Inc.', 20, 95, 12000, 600, 'USD', '2026-03-18', FALSE),
            ('US_STK_MSFT', 'Microsoft', 10, 200, 10000, 1000, 'USD', '2026-03-20', FALSE)
        """
    )

    conn.execute(
        """
        CREATE TABLE trade_logs (
            id INTEGER,
            log_date DATE,
            asset_id VARCHAR,
            action VARCHAR,
            price DOUBLE,
            quantity DOUBLE,
            amount DOUBLE,
            currency VARCHAR
        )
        """
    )
    conn.execute(
        """
        INSERT INTO trade_logs VALUES
            (1, '2026-03-21', 'US_STK_AAPL', 'buy', 700, 2, 1400, 'USD'),
            (2, '2026-03-19', 'US_STK_AAPL', 'sell', 680, 1, 680, 'USD')
        """
    )

    conn.execute(
        """
        CREATE TABLE strategy_memos (
            id INTEGER,
            memo_date DATE,
            title VARCHAR,
            content VARCHAR
        )
        """
    )
    conn.execute(
        """
        INSERT INTO strategy_memos VALUES
            (1, '2026-03-22', 'AAPL strategy update', 'Prefer staggered adds near support')
        """
    )

    cb = ContextBuilder.__new__(ContextBuilder)
    cb._db = conn

    ctx = cb.build_asset_context("US_STK_AAPL")

    assert ctx["asset_code"] == "US_STK_AAPL"
    assert ctx["asset_name"] == "Apple Inc."
    assert ctx["position"] is not None
    assert ctx["position"]["quantity"] == 20
    assert ctx["position"]["market_value_cny"] == 14000
    assert ctx["allocation"]["current_pct"] == 58.33
    assert len(ctx["recent_trades"]) == 2
    assert len(ctx["related_memos"]) == 1
    assert ctx["philosophy_excerpt"] == ""

    conn.close()


def test_build_asset_context_not_held():
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE holdings (
            asset_id VARCHAR,
            asset_name VARCHAR,
            quantity DOUBLE,
            cost_price_unit DOUBLE,
            market_value DOUBLE,
            market_price_unit DOUBLE,
            currency VARCHAR,
            snapshot_date DATE,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE trade_logs (
            id INTEGER,
            log_date DATE,
            asset_id VARCHAR,
            action VARCHAR,
            price DOUBLE,
            quantity DOUBLE,
            amount DOUBLE,
            currency VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE strategy_memos (
            id INTEGER,
            memo_date DATE,
            title VARCHAR,
            content VARCHAR
        )
        """
    )

    cb = _setup_context_builder_with_db(conn)
    ctx = cb.build_asset_context("US_STK_AAPL")

    assert ctx["position"] is None
    assert ctx["allocation"] is None
    assert ctx["recent_trades"] == []

    conn.close()


def test_build_asset_context_no_memos():
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE holdings (
            asset_id VARCHAR,
            asset_name VARCHAR,
            quantity DOUBLE,
            cost_price_unit DOUBLE,
            market_value DOUBLE,
            market_price_unit DOUBLE,
            currency VARCHAR,
            snapshot_date DATE,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        INSERT INTO holdings VALUES
            ('US_STK_AAPL', 'Apple Inc.', 5, 100, 5000, 1000, 'USD', '2026-03-20', FALSE)
        """
    )
    conn.execute(
        """
        CREATE TABLE trade_logs (
            id INTEGER,
            log_date DATE,
            asset_id VARCHAR,
            action VARCHAR,
            price DOUBLE,
            quantity DOUBLE,
            amount DOUBLE,
            currency VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE strategy_memos (
            id INTEGER,
            memo_date DATE,
            title VARCHAR,
            content VARCHAR
        )
        """
    )
    conn.execute(
        """
        INSERT INTO strategy_memos VALUES
            (1, '2026-03-20', 'Macro update', 'No single-stock notes today')
        """
    )

    cb = _setup_context_builder_with_db(conn)
    ctx = cb.build_asset_context("US_STK_AAPL")

    assert ctx["asset_name"] == "Apple Inc."
    assert ctx["related_memos"] == []

    conn.close()


def test_normalize_llm_valid():
    from src.analysis.pipeline import _missing_analysis_keys, _normalize_llm_analysis

    raw = {
        "summary": "ok",
        "valuation_judgment": "PE at 55th pct, fair value",
        "rule_bucket": "价值估算",
        "operation_signal": "buy",
        "falsification_conditions": ["If PE > 80th pct, reassess"],
        "validity_period": "3-6 months",
        "confidence": 0.85,
        "sizing_suggestion": "small add",
        "risk_factors": ["volatility"],
        "portfolio_alignment": "aligned",
    }

    normalized = _normalize_llm_analysis(raw)

    assert _missing_analysis_keys(normalized) == []
    assert normalized["operation_signal"] == "buy"
    assert normalized["confidence"] == 0.85
    assert normalized["falsification_conditions"] == ["If PE > 80th pct, reassess"]


def test_normalize_llm_backward_compat_timing_signal():
    """Old timing_signal is promoted to operation_signal for backward compatibility."""
    from src.analysis.pipeline import _normalize_llm_analysis

    normalized = _normalize_llm_analysis({"timing_signal": "hold"})
    assert normalized["operation_signal"] == "hold"


def test_normalize_llm_missing_keys():
    from src.analysis.pipeline import _missing_analysis_keys, _normalize_llm_analysis

    normalized = _normalize_llm_analysis({})

    assert _missing_analysis_keys(normalized) == []
    assert normalized["operation_signal"] == "wait"
    assert normalized["summary"]


def test_normalize_operation_signal_invalid():
    from src.analysis.pipeline import _normalize_llm_analysis

    normalized = _normalize_llm_analysis({"operation_signal": "aggressive"})

    assert normalized["operation_signal"] == "wait"


def test_normalize_timing_signal_invalid():
    """Old timing_signal promoted then normalized to valid set."""
    from src.analysis.pipeline import _normalize_llm_analysis

    normalized = _normalize_llm_analysis({"timing_signal": "aggressive"})

    assert normalized["operation_signal"] == "wait"


def test_normalize_confidence_clamped():
    from src.analysis.pipeline import _normalize_llm_analysis

    high = _normalize_llm_analysis({"confidence": 1.5})
    low = _normalize_llm_analysis({"confidence": -0.1})

    assert high["confidence"] == 1.0
    assert low["confidence"] == 0.0


def test_normalize_falsification_conditions_coercion():
    """Non-list falsification_conditions are wrapped in a list."""
    from src.analysis.pipeline import _normalize_llm_analysis

    normalized = _normalize_llm_analysis({"falsification_conditions": "If rate spikes"})
    assert normalized["falsification_conditions"] == ["If rate spikes"]

    normalized_empty = _normalize_llm_analysis({"falsification_conditions": []})
    assert normalized_empty["falsification_conditions"] == []


@patch("src.analysis.pipeline._save_analysis_to_db", return_value=88)
@patch("src.analysis.pipeline.LLMClient")
@patch("src.analysis.pipeline.ContextBuilder")
@patch("src.analysis.pipeline.StockTrendAnalyzer")
@patch("src.analysis.pipeline.MarketDataService")
def test_pipeline_full_mock(
    MockMarketDataService,
    MockStockTrendAnalyzer,
    MockContextBuilder,
    MockLLMClient,
    mock_save,
):
    from src.analysis.pipeline import AssetAnalysisPipeline

    mock_df = pd.DataFrame(
        [
            {
                "date": "2026-03-20",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 123,
                "pct_chg": 0.5,
                "source": "mock_feed",
            }
        ]
    )
    MockMarketDataService.return_value.get_ohlcv.return_value = mock_df

    signals = _make_signals()
    MockStockTrendAnalyzer.return_value.analyze.return_value = signals

    mock_ctx = {
        "asset_code": "US_STK_AAPL",
        "asset_name": "Apple Inc.",
        "position": None,
        "allocation": None,
        "recent_trades": [],
        "related_memos": [],
        "philosophy_excerpt": "",
    }
    MockContextBuilder.return_value.build_asset_context.return_value = mock_ctx

    response = SimpleNamespace(
        content_json={
            "summary": "summary",
            "valuation_judgment": "PE at 55th pct, fair",
            "rule_bucket": "价值估算",
            "operation_signal": "hold",
            "falsification_conditions": ["If PE exceeds 80th pct"],
            "validity_period": "3 months",
            "confidence": 0.66,
            "sizing_suggestion": "maintain",
            "risk_factors": ["risk"],
            "portfolio_alignment": "ok",
        },
        content="{}",
        model_used="gemini/gemini-2.5-flash",
        usage={"total_tokens": 10},
    )
    MockLLMClient.return_value.complete.return_value = response

    result = AssetAnalysisPipeline().analyze("US_STK_AAPL", triggered_by="user", db_path=":memory:", days=60)

    assert result.id == 88
    assert result.asset_code == "US_STK_AAPL"
    assert result.asset_name == "Apple Inc."
    assert result.model_used == "gemini/gemini-2.5-flash"
    assert result.data_source == "mock_feed"
    assert result.llm_analysis["operation_signal"] == "hold"
    mock_save.assert_called_once()


@patch("src.analysis.pipeline.LLMClient")
@patch("src.analysis.pipeline.ContextBuilder")
@patch("src.analysis.pipeline.StockTrendAnalyzer")
@patch("src.analysis.pipeline.MarketDataService")
def test_pipeline_saves_to_db(
    MockMarketDataService,
    MockStockTrendAnalyzer,
    MockContextBuilder,
    MockLLMClient,
):
    from src.analysis.pipeline import AssetAnalysisPipeline

    mock_df = pd.DataFrame(
        [
            {
                "date": "2026-03-20",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 123,
                "pct_chg": 0.5,
                "source": "mock_feed",
            }
        ]
    )
    MockMarketDataService.return_value.get_ohlcv.return_value = mock_df
    MockStockTrendAnalyzer.return_value.analyze.return_value = _make_signals()
    MockContextBuilder.return_value.build_asset_context.return_value = {
        "asset_code": "US_STK_AAPL",
        "asset_name": "Apple Inc.",
        "position": None,
        "allocation": None,
        "recent_trades": [],
        "related_memos": [],
        "philosophy_excerpt": "",
    }

    MockLLMClient.return_value.complete.return_value = SimpleNamespace(
        content_json={
            "summary": "summary",
            "technical_assessment": "assessment",
            "timing_signal": "wait",
            "confidence": 0.4,
            "key_levels": {"entry": [], "stop_loss": None, "targets": []},
            "sizing_suggestion": "wait",
            "risk_factors": [],
            "portfolio_alignment": "n/a",
        },
        content="{}",
        model_used="gemini/gemini-2.5-flash",
        usage={"total_tokens": 10},
    )

    shared_conn = duckdb.connect(":memory:")
    shared_conn.execute("CREATE SEQUENCE asset_analyses_seq START 1")
    shared_conn.execute(
        """
        CREATE TABLE asset_analyses (
            id INTEGER PRIMARY KEY DEFAULT nextval('asset_analyses_seq'),
            asset_code VARCHAR,
            asset_name VARCHAR,
            analysis_type VARCHAR,
            technical_signals JSON,
            llm_analysis JSON,
            llm_analysis_markdown VARCHAR,
            portfolio_context JSON,
            model_used VARCHAR,
            data_source VARCHAR,
            triggered_by VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    with patch("src.analysis.pipeline.duckdb.connect", return_value=_NoCloseConn(shared_conn)):
        result = AssetAnalysisPipeline().analyze("US_STK_AAPL", db_path=":memory:")

    row = shared_conn.execute(
        "SELECT asset_code, model_used, triggered_by FROM asset_analyses"
    ).fetchone()

    assert result.id == 1
    assert row == ("US_STK_AAPL", "gemini/gemini-2.5-flash", "user")
    shared_conn.close()


def test_analysis_generator_passthrough():
    from src.services.ai_advisor.analysis_generator import AnalysisGenerator

    fake_result = SimpleNamespace(asset_code="US_STK_AAPL", model_used="mock")

    with patch("src.services.ai_advisor.analysis_generator.AssetAnalysisPipeline") as MockPipeline:
        MockPipeline.return_value.analyze.return_value = fake_result

        result = AnalysisGenerator().analyze(
            asset_code="US_STK_AAPL",
            triggered_by="memo",
            db_path=":memory:",
            days=90,
        )

    assert result.asset_code == "US_STK_AAPL"
    MockPipeline.return_value.analyze.assert_called_once_with(
        asset_code="US_STK_AAPL",
        triggered_by="memo",
        db_path=":memory:",
        days=90,
    )
