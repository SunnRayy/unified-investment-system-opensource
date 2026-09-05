"""Hermetic tests for GET /settings/llm/usage endpoint.

Tests call aggregate_llm_usage() directly from the lightweight
settings_llm_usage module (no python-multipart or yfinance required).

A temp DuckDB is pointed to via UIS_DB_PATH — no real DB is touched.
"""

from datetime import datetime

import duckdb

from src.api.routes.settings_llm_usage import aggregate_llm_usage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
    CREATE TABLE llm_usage (
        id          INTEGER,
        report_type VARCHAR,
        model_used  VARCHAR,
        prompt_tokens      INTEGER,
        completion_tokens  INTEGER,
        total_tokens       INTEGER,
        cost_estimate_usd  DOUBLE,
        success            BOOLEAN,
        error_message      VARCHAR,
        created_at         TIMESTAMP
    )
"""

_TS1 = datetime(2026, 6, 10, 9, 0, 0)
_TS2 = datetime(2026, 6, 15, 12, 0, 0)
_TS3 = datetime(2026, 6, 18, 8, 0, 0)


def _make_db_with_rows(db_path):
    """Create llm_usage table with 3 rows: 2 gemini successes, 1 haiku failure."""
    conn = duckdb.connect(str(db_path))
    conn.execute(_CREATE_TABLE)
    # gemini — two successful calls
    conn.execute(
        "INSERT INTO llm_usage VALUES (1,'brief','gemini/gemini-2.5-flash',500,200,700,0.0014,true,NULL,?)",
        [_TS1],
    )
    conn.execute(
        "INSERT INTO llm_usage VALUES (2,'review','gemini/gemini-2.5-flash',800,300,1100,0.0022,true,NULL,?)",
        [_TS2],
    )
    # haiku — one failed call (cost 0, error message present)
    conn.execute(
        "INSERT INTO llm_usage VALUES (3,'brief','anthropic/claude-3-haiku',0,0,0,0.0,false,'timeout',?)",
        [_TS3],
    )
    conn.close()
    return db_path


def _make_empty_db(db_path):
    """Create llm_usage table with zero rows."""
    conn = duckdb.connect(str(db_path))
    conn.execute(_CREATE_TABLE)
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Tests — per-model aggregation + totals
# ---------------------------------------------------------------------------


def test_usage_with_data_returns_two_models(tmp_path, monkeypatch):
    db_path = tmp_path / "llm_usage.duckdb"
    _make_db_with_rows(db_path)
    monkeypatch.setenv("UIS_DB_PATH", str(db_path))

    result = aggregate_llm_usage()

    assert len(result.models) == 2


def test_usage_gemini_aggregation(tmp_path, monkeypatch):
    db_path = tmp_path / "llm_usage.duckdb"
    _make_db_with_rows(db_path)
    monkeypatch.setenv("UIS_DB_PATH", str(db_path))

    result = aggregate_llm_usage()

    gemini = next(m for m in result.models if "gemini" in m.model_used)
    assert gemini.calls == 2
    assert gemini.prompt_tokens == 1300       # 500 + 800
    assert gemini.completion_tokens == 500    # 200 + 300
    assert gemini.total_tokens == 1800        # 700 + 1100
    assert abs(gemini.cost_usd - 0.0036) < 1e-9
    assert gemini.success_calls == 2
    assert gemini.failure_calls == 0
    assert gemini.last_used is not None


def test_usage_anthropic_aggregation(tmp_path, monkeypatch):
    db_path = tmp_path / "llm_usage.duckdb"
    _make_db_with_rows(db_path)
    monkeypatch.setenv("UIS_DB_PATH", str(db_path))

    result = aggregate_llm_usage()

    anthropic = next(m for m in result.models if "anthropic" in m.model_used)
    assert anthropic.calls == 1
    assert anthropic.total_tokens == 0
    assert anthropic.cost_usd == 0.0
    assert anthropic.success_calls == 0
    assert anthropic.failure_calls == 1


def test_usage_totals(tmp_path, monkeypatch):
    db_path = tmp_path / "llm_usage.duckdb"
    _make_db_with_rows(db_path)
    monkeypatch.setenv("UIS_DB_PATH", str(db_path))

    result = aggregate_llm_usage()

    assert result.total_calls == 3
    assert result.total_tokens == 1800
    assert abs(result.total_cost_usd - 0.0036) < 1e-9


def test_usage_order_by_call_count(tmp_path, monkeypatch):
    """Models must be ordered by call count descending."""
    db_path = tmp_path / "llm_usage.duckdb"
    _make_db_with_rows(db_path)
    monkeypatch.setenv("UIS_DB_PATH", str(db_path))

    result = aggregate_llm_usage()

    call_counts = [m.calls for m in result.models]
    assert call_counts == sorted(call_counts, reverse=True)


def test_usage_last_used_is_isoformat_string(tmp_path, monkeypatch):
    db_path = tmp_path / "llm_usage.duckdb"
    _make_db_with_rows(db_path)
    monkeypatch.setenv("UIS_DB_PATH", str(db_path))

    result = aggregate_llm_usage()

    for m in result.models:
        if m.last_used is not None:
            datetime.fromisoformat(m.last_used)  # raises ValueError if not valid ISO


# ---------------------------------------------------------------------------
# Tests — empty table
# ---------------------------------------------------------------------------


def test_empty_table_returns_empty_response(tmp_path, monkeypatch):
    db_path = tmp_path / "llm_empty.duckdb"
    _make_empty_db(db_path)
    monkeypatch.setenv("UIS_DB_PATH", str(db_path))

    result = aggregate_llm_usage()

    assert result.models == []
    assert result.total_calls == 0
    assert result.total_tokens == 0
    assert result.total_cost_usd == 0.0


# ---------------------------------------------------------------------------
# Tests — table missing (pre-migration DB)
# ---------------------------------------------------------------------------


def test_no_table_returns_empty_response(tmp_path, monkeypatch):
    """llm_usage table absent → returns empty data (not an error)."""
    db_path = tmp_path / "llm_notable.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE other_table (id INTEGER)")
    conn.close()
    monkeypatch.setenv("UIS_DB_PATH", str(db_path))

    result = aggregate_llm_usage()

    assert result.models == []
    assert result.total_calls == 0
