"""Tests for Phase 4 analyze API endpoints."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import ai_advisor as ai_advisor_routes
from src.market_data.fetchers.base import (
    DataFetchError,
    NoDataError,
    UnsupportedCodeError,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> Path:
    """Create a minimal in-memory DuckDB with asset_analyses and asset_registry tables."""
    db_path = tmp_path / "test_analyze.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE asset_analyses (
            id INTEGER,
            asset_code VARCHAR,
            asset_name VARCHAR,
            technical_signals VARCHAR,
            llm_analysis VARCHAR,
            llm_analysis_markdown VARCHAR,
            portfolio_context VARCHAR,
            model_used VARCHAR,
            data_source VARCHAR,
            triggered_by VARCHAR,
            created_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE asset_registry (
            canonical_id VARCHAR PRIMARY KEY,
            name VARCHAR,
            display_name VARCHAR,
            asset_class VARCHAR,
            base_currency VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE holdings (
            asset_id VARCHAR,
            market_value DOUBLE,
            snapshot_date DATE,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        INSERT INTO asset_analyses VALUES
        (
            1, 'AAPL', 'Apple Inc.',
            '{"signal_score": 72}',
            '{"timing_signal": "buy", "confidence": 0.75}',
            '## AAPL Analysis\nBullish.',
            '{"portfolio_weight": 0.05}',
            'gemini/gemini-2.5-flash',
            'yfinance',
            'user',
            '2026-03-27 10:00:00'
        ),
        (
            2, 'AAPL', 'Apple Inc.',
            '{"signal_score": 55}',
            '{"timing_signal": "hold", "confidence": 0.60}',
            '## AAPL Analysis\nNeutral.',
            '{"portfolio_weight": 0.05}',
            'gemini/gemini-2.5-flash',
            'yfinance',
            'user',
            '2026-03-26 10:00:00'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO asset_registry VALUES
        ('AAPL', 'Apple Inc.', 'Apple Inc.', 'US Equity', 'USD')
        """
    )
    conn.close()
    return db_path


@pytest.fixture
def analyze_client(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    monkeypatch.setattr(ai_advisor_routes, "_DB_PATH", db_path)
    app = FastAPI()
    app.include_router(ai_advisor_routes.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /ai-advisor/analyze
# ---------------------------------------------------------------------------

def _mock_analysis_result(id_=1):
    """Return a minimal AnalysisResult dataclass instance."""
    from src.analysis.pipeline import AnalysisResult
    return AnalysisResult(
        id=id_,
        asset_code="AAPL",
        asset_name="Apple Inc.",
        technical_signals={"signal_score": 72, "trend_status": "BULL"},
        llm_analysis={"timing_signal": "buy", "confidence": 0.75},
        llm_analysis_markdown="## AAPL\nBullish.",
        portfolio_context={"portfolio_weight": 0.05},
        model_used="gemini/gemini-2.5-flash",
        data_source="yfinance",
        triggered_by="user",
        created_at="2026-03-27T10:00:00",
        usage={"total_tokens": 500},
    )


def test_post_analyze_success(analyze_client, monkeypatch):
    """Pipeline returns valid AnalysisResult → 200 with full result."""
    mock_result = _mock_analysis_result(id_=1)

    with patch("src.analysis.pipeline.AssetAnalysisPipeline") as MockPipeline:
        MockPipeline.return_value.analyze.return_value = mock_result
        resp = analyze_client.post(
            "/ai-advisor/analyze",
            json={"asset_code": "AAPL", "analysis_type": "full"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 1
    assert data["asset_code"] == "AAPL"
    assert data["technical_signals"]["signal_score"] == 72


def test_post_analyze_unsupported_code(analyze_client):
    """Pipeline raises UnsupportedCodeError → 422."""
    with patch("src.analysis.pipeline.AssetAnalysisPipeline") as MockPipeline:
        MockPipeline.return_value.analyze.side_effect = UnsupportedCodeError("Unsupported code: XYZ")
        resp = analyze_client.post(
            "/ai-advisor/analyze",
            json={"asset_code": "XYZ"},
        )

    assert resp.status_code == 422
    assert "Unsupported" in resp.json()["detail"]


def test_post_analyze_no_data(analyze_client):
    """Pipeline raises NoDataError → 422."""
    with patch("src.analysis.pipeline.AssetAnalysisPipeline") as MockPipeline:
        MockPipeline.return_value.analyze.side_effect = NoDataError("No data for XYZ")
        resp = analyze_client.post(
            "/ai-advisor/analyze",
            json={"asset_code": "XYZ"},
        )

    assert resp.status_code == 422


def test_post_analyze_data_fetch_error(analyze_client):
    """Pipeline raises DataFetchError → 503."""
    with patch("src.analysis.pipeline.AssetAnalysisPipeline") as MockPipeline:
        MockPipeline.return_value.analyze.side_effect = DataFetchError("Provider down")
        resp = analyze_client.post(
            "/ai-advisor/analyze",
            json={"asset_code": "AAPL"},
        )

    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"].lower()


def test_post_analyze_persist_failure(analyze_client):
    """Pipeline returns result with id=None → 500."""
    mock_result = _mock_analysis_result(id_=None)
    mock_result.id = None

    with patch("src.analysis.pipeline.AssetAnalysisPipeline") as MockPipeline:
        MockPipeline.return_value.analyze.return_value = mock_result
        resp = analyze_client.post(
            "/ai-advisor/analyze",
            json={"asset_code": "AAPL"},
        )

    assert resp.status_code == 500
    assert "persist" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /ai-advisor/analyze/history
# ---------------------------------------------------------------------------

def test_get_history_returns_list(analyze_client):
    """Returns list of history items from the DB."""
    resp = analyze_client.get("/ai-advisor/analyze/history")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["asset_code"] == "AAPL"
    assert data[0]["id"] == 1


def test_get_history_filters_by_asset_code(analyze_client):
    """Filters by asset_code query param."""
    resp = analyze_client.get("/ai-advisor/analyze/history?asset_code=AAPL&limit=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["asset_code"] == "AAPL"


# ---------------------------------------------------------------------------
# GET /ai-advisor/analyze/{id}
# ---------------------------------------------------------------------------

def test_get_by_id_success(analyze_client):
    """Returns 200 with full analysis details."""
    resp = analyze_client.get("/ai-advisor/analyze/1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 1
    assert data["asset_code"] == "AAPL"
    assert data["technical_signals"]["signal_score"] == 72
    assert data["llm_analysis"]["timing_signal"] == "buy"


def test_get_by_id_not_found(analyze_client):
    """Returns 404 when id does not exist."""
    resp = analyze_client.get("/ai-advisor/analyze/9999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Route ordering regression test
# ---------------------------------------------------------------------------

def test_search_route_not_matched_as_id(analyze_client):
    """GET /ai-advisor/analyze/search?q=AM must NOT return 422 (route ordering regression)."""
    resp = analyze_client.get("/ai-advisor/analyze/search?q=AM")
    # Should be 200 (empty list or results) — NOT 422 (which would mean it tried
    # to parse 'search' as an integer analysis_id)
    assert resp.status_code != 422
    assert resp.status_code == 200
