"""Tests for market sentiment API routes."""

import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.api.dependencies import get_db
from src.api.main import app

client = TestClient(app)

_SAMPLE_ROW = (
    "fear_greed",
    "equity_macro",
    "CNN Fear & Greed",
    45.0,
    "45.0",
    "Neutral",
    "yellow",
    "Fear and greed is neutral.",
    '{"fear_and_greed": {"score": 45}}',
    "2026-03-01T10:00:00",
    False,   # is_stale
    None,    # last_refresh_attempt
    None,    # error_detail
    None,    # methodology
    None,    # data_source
)


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.read_only = False

    def override():
        return db

    app.dependency_overrides[get_db] = override
    yield db
    app.dependency_overrides.pop(get_db, None)


def test_get_sentiment_returns_cached_rows(mock_db):
    """GET /market/sentiment should return cached indicators.

    ensure_sentiment_table is no longer called on GET (it runs DDL which requires a
    writable connection). The GET endpoint issues a single SELECT and returns empty
    data gracefully if the table does not yet exist.
    """
    select_mock = MagicMock(fetchall=MagicMock(return_value=[_SAMPLE_ROW]))
    mock_db.execute.side_effect = [select_mock]

    response = client.get("/market/sentiment")

    assert response.status_code == 200
    payload = response.json()
    assert payload["last_updated"] == "2026-03-01T10:00:00"
    assert len(payload["indicators"]) == 1
    assert payload["indicators"][0]["indicator_key"] == "fear_greed"
    assert payload["indicators"][0]["zone"] == "Neutral"


@patch("src.api.routes.sentiment._load_fred_key", return_value="fred-test-key")
@patch("src.api.routes.sentiment.MacroAnalyzer")
def test_refresh_sentiment_fetches_and_upserts(mock_analyzer_cls, mock_load_fred, mock_db):
    """POST /market/sentiment/refresh should fetch and store indicators."""
    mock_analyzer = MagicMock()
    mock_analyzer.fetch_all.return_value = [
        {
            "indicator_key": "fear_greed",
            "section": "equity_macro",
            "indicator_name": "CNN Fear & Greed",
            "value": 46.0,
            "display_value": "46.0",
            "zone": "Neutral",
            "zone_color": "yellow",
            "description": "Fear and greed is neutral.",
            "raw_json": '{"fear_and_greed": {"score": 46}}',
            "updated_at": "2026-03-01T12:00:00",
        }
    ]
    mock_analyzer_cls.return_value = mock_analyzer

    response = client.post("/market/sentiment/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["indicators"]) == 1
    assert payload["indicators"][0]["indicator_key"] == "fear_greed"
    assert payload["last_updated"] is not None

    mock_load_fred.assert_called_once()
    mock_analyzer_cls.assert_called_once_with(fred_api_key="fred-test-key")

    insert_calls = [
        call for call in mock_db.execute.call_args_list
        if "INSERT INTO market_sentiment_cache" in str(call)
    ]
    assert len(insert_calls) == 1


# ---------------------------------------------------------------------------
# F4.3 — Buffett indicator methodology-tag ingestion gate (PRD 2026-07-07,
# Batch B5). Uses a real in-memory DuckDB (not MagicMock) because
# require_methodology() reads metric_catalog and the gate's pass/skip
# behavior must reflect real row state, not a mock's default truthiness.
# ---------------------------------------------------------------------------

import pytest
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema


@pytest.fixture
def real_db_client():
    test_conn = DatabaseConnector(":memory:")
    initialize_schema(test_conn)

    def override():
        return test_conn

    app.dependency_overrides[get_db] = override
    yield TestClient(app), test_conn
    app.dependency_overrides.pop(get_db, None)
    test_conn.close()


@patch("src.api.routes.sentiment._load_fred_key", return_value="fred-test-key")
@patch("src.api.routes.sentiment.MacroAnalyzer")
def test_refresh_sentiment_skips_untagged_buffett_indicator(
    mock_analyzer_cls, mock_load_fred, real_db_client
):
    """PRD F4.3: ingestion of a buffett_* series without a methodology tag is
    rejected (skipped, never silently persisted)."""
    test_client, conn = real_db_client
    mock_analyzer = MagicMock()
    mock_analyzer.fetch_all.return_value = [
        {
            "indicator_key": "buffett_us",
            "section": "equity_macro",
            "indicator_name": "Buffett Indicator (US)",
            "value": 218.1,
            "display_value": "218.1%",
            "zone": "Significantly Overvalued",
            "zone_color": "red",
            "description": "test",
            "raw_json": "{}",
            "updated_at": "2026-07-07T00:00:00",
            "methodology": None,  # untagged -> must be skipped
            "data_source": None,
        }
    ]
    mock_analyzer_cls.return_value = mock_analyzer

    resp = test_client.post("/market/sentiment/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["skipped_ungoverned"] == 1
    assert body["indicators"] == []

    row = conn.execute(
        "SELECT COUNT(*) FROM market_sentiment_cache WHERE indicator_key = 'buffett_us'"
    ).fetchone()
    assert row[0] == 0, "untagged buffett series must never be persisted"


@patch("src.api.routes.sentiment._load_fred_key", return_value="fred-test-key")
@patch("src.api.routes.sentiment.MacroAnalyzer")
def test_refresh_sentiment_persists_tagged_buffett_indicator(
    mock_analyzer_cls, mock_load_fred, real_db_client
):
    """A properly-tagged buffett_* series is persisted with its methodology/
    data_source columns intact."""
    test_client, conn = real_db_client
    mock_analyzer = MagicMock()
    mock_analyzer.fetch_all.return_value = [
        {
            "indicator_key": "buffett_us",
            "section": "equity_macro",
            "indicator_name": "Buffett Indicator (US)",
            "value": 218.1,
            "display_value": "218.1%",
            "zone": "Significantly Overvalued",
            "zone_color": "red",
            "description": "test",
            "raw_json": "{}",
            "updated_at": "2026-07-07T00:00:00",
            "methodology": "buffett_fed_z1_corp_equities_gdp",
            "data_source": "FRED (test)",
        }
    ]
    mock_analyzer_cls.return_value = mock_analyzer

    resp = test_client.post("/market/sentiment/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["skipped_ungoverned"] == 0

    row = conn.execute(
        "SELECT methodology, data_source FROM market_sentiment_cache WHERE indicator_key = 'buffett_us'"
    ).fetchone()
    assert row == ("buffett_fed_z1_corp_equities_gdp", "FRED (test)")


def test_buffett_variants_coexist_with_distinct_methodology_tags(real_db_client):
    """Storage-level test: two Buffett indicator rows (different indicator_keys,
    distinct methodology tags) coexist side-by-side in market_sentiment_cache
    (PRD F4.3 acceptance: 'both variants can coexist with distinct tags')."""
    _test_client, conn = real_db_client
    conn.execute(
        """
        INSERT INTO market_sentiment_cache
            (indicator_key, section, indicator_name, value, methodology, data_source, updated_at)
        VALUES ('buffett_us', 'equity_macro', 'Buffett Indicator (US)', 194.9,
                'buffett_fed_z1_corp_equities_gdp', 'FRED (Fed Z.1)', CURRENT_TIMESTAMP)
        """
    )
    conn.execute(
        """
        INSERT INTO market_sentiment_cache
            (indicator_key, section, indicator_name, value, methodology, data_source, updated_at)
        VALUES ('buffett_cn', 'equity_macro', 'Buffett Indicator (China)', 78.3,
                'buffett_classic_tmc_gdp', 'World Bank (DDDM01CNA156NWDB)', CURRENT_TIMESTAMP)
        """
    )

    rows = conn.execute(
        "SELECT indicator_key, methodology FROM market_sentiment_cache ORDER BY indicator_key"
    ).fetchall()
    assert rows == [
        ("buffett_cn", "buffett_classic_tmc_gdp"),
        ("buffett_us", "buffett_fed_z1_corp_equities_gdp"),
    ]
