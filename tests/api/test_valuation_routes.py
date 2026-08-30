"""API smoke tests for /valuation endpoints using mocked DB."""
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def mock_db():
    db = MagicMock()
    # Default: empty table
    db.execute.return_value.df.return_value = __import__("pandas").DataFrame()
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.fetchone.return_value = None
    return db


@pytest.fixture
def client(mock_db):
    from src.api.main import app
    from src.api.dependencies import get_db
    app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_get_latest_snapshots_empty(client):
    resp = client.get("/valuation/snapshot/latest")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_latest_snapshots_query_requests_phase1_fields(client, mock_db):
    resp = client.get("/valuation/snapshot/latest")
    assert resp.status_code == 200

    query = mock_db.execute.call_args[0][0]
    assert "display_name" in query
    assert "row_kind" in query
    assert "linked_ticker" in query
    assert "GROUP BY ticker, row_kind" in query


def test_get_reference_empty(client, mock_db):
    mock_db.execute.return_value.fetchall.return_value = []
    resp = client.get("/valuation/reference")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_macro_fallback(client, mock_db):
    mock_db.execute.return_value.fetchone.return_value = None
    resp = client.get("/valuation/macro")
    assert resp.status_code == 200
    data = resp.json()
    assert "us10y" in data
    assert "rate_adjustment_factor" in data
    assert data["fallback_used"] is True


def test_put_reference_invalid_thresholds(client):
    resp = client.put(
        "/valuation/reference/MSFT/pe_forward",
        json={"low_threshold": 30.0, "high_threshold": 10.0}
    )
    assert resp.status_code == 422


def test_get_snapshot_history(client):
    resp = client.get("/valuation/snapshot/history?ticker=MSFT&days=90")
    assert resp.status_code == 200


def test_get_watchlist_empty(client):
    resp = client.get("/valuation/watchlist")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_percentile_endpoint_exists(client):
    resp = client.get("/valuation/percentile/000300/pe_ttm")
    assert resp.status_code == 200


def test_create_watchlist_item(client):
    resp = client.post(
        "/valuation/watchlist",
        json={
            "ticker": "QQQ",
            "display_name": "Nasdaq 100 ETF",
            "asset_type": "US_INDEX",
            "note": "Monitor for buy-in window",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticker"] == "QQQ"
    assert data["status"] in {"created", "exists"}
    assert data["backfill_status"] in {"seeded", "deferred", "unsupported"}


def test_delete_watchlist_item_is_idempotent(client):
    resp = client.delete("/valuation/watchlist/QQQ")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticker"] == "QQQ"
    assert data["status"] in {"deleted", "not_found"}


# ---------------------------------------------------------------------------
# F4.2 — VOO/SP500 canonical signal dedup (PRD 2026-07-07)
# ---------------------------------------------------------------------------

def _snapshot_row(ticker, row_kind, valuation_signal, percentile_value=None, pct_years=None):
    return {
        "id": 1, "snapshot_date": "2026-07-02", "ticker": ticker,
        "display_name": ticker, "row_kind": row_kind, "linked_ticker": None,
        "asset_id": ticker, "asset_class": "US_ETF" if row_kind == "holding" else "US_INDEX",
        "pe_ttm": 24.1, "pe_forward": 24.1, "pb_ratio": None, "peg_ratio": None,
        "fcf_yield": None, "dividend_yield": None, "ev_ebitda": None, "sec_yield": None,
        "percentile_value": percentile_value, "percentile_metric": "pe_ttm",
        "pct_years": pct_years,
        "valuation_signal": valuation_signal, "signal_basis": "test-basis",
        "rate_adjustment_factor": 1.0, "data_source": "test", "is_estimable": True,
        "notes": None, "created_at": "2026-07-02",
    }


def test_voo_row_displays_canonical_sp500_signal(client, mock_db):
    """PRD F4.2 acceptance: VOO row shows the SP500 canonical signal; no
    conflicting pair exists in the API output."""
    import pandas as pd

    rows = pd.DataFrame([
        _snapshot_row("VOO", "holding", "HIGH"),
        _snapshot_row("S&P500", "tracked_index", "FAIR", percentile_value=69.0, pct_years=10),
    ])
    mock_db.execute.return_value.df.return_value = rows

    resp = client.get("/valuation/snapshot/latest")
    assert resp.status_code == 200
    data = resp.json()

    voo = next(r for r in data if r["ticker"] == "VOO")
    sp500 = next(r for r in data if r["ticker"] == "S&P500")

    assert voo["valuation_signal"] == "FAIR", "VOO must mirror the canonical SP500 signal"
    assert voo["canonical_underlying"] == "SP500"
    assert voo["signal_source_series"] == "S&P500"

    # Invariant: no two instruments mapped to the same canonical underlying
    # may carry different signal values.
    by_canonical: dict[str, set[str]] = {}
    for row in data:
        canonical = row.get("canonical_underlying")
        if canonical:
            by_canonical.setdefault(canonical, set()).add(row["valuation_signal"])
    for canonical_id, signals in by_canonical.items():
        assert len(signals) == 1, f"{canonical_id} has conflicting signals: {signals}"

    assert sp500["valuation_signal"] == "FAIR"


# ---------------------------------------------------------------------------
# F4.5 — bucket-aware signal suppression (PRD 2026-07-07, Batch B5)
# ---------------------------------------------------------------------------

def test_amzn_compliance_row_shows_execution_progress_not_signal(client, mock_db):
    """RSU_AMZN (compliance bucket, sell) never shows a valuation signal —
    execution_progress instead (no LOW/HIGH signal leaks through)."""
    import pandas as pd

    amzn_row = _snapshot_row("AMZN", "holding", "LOW", percentile_value=12.0, pct_years=10)
    amzn_row["asset_id"] = "RSU_AMZN"
    mock_db.execute.return_value.df.return_value = pd.DataFrame([amzn_row])
    mock_db.execute.return_value.fetchall.return_value = []

    resp = client.get("/valuation/snapshot/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]

    assert row["display_mode"] == "execution_progress"
    assert row["valuation_signal"] is None
    assert row["signal_basis"] is None
    assert row["percentile_value"] is None
    assert "execution_progress" in row


def test_ratio_bucket_rows_show_band_position_only(client, mock_db):
    """Gold/IBIT/FBTC (ratio bucket) never show a valuation signal — band
    position (current % vs total holdings) instead."""
    import pandas as pd

    gold_row = _snapshot_row("GOLD", "holding", "HIGH")
    gold_row["asset_id"] = "ALTS_Paper_Gold"
    ibit_row = _snapshot_row("IBIT", "holding", "LOW")
    ibit_row["asset_id"] = "US_STK_IBIT"
    fbtc_row = _snapshot_row("FBTC", "holding", "FAIR")
    fbtc_row["asset_id"] = "US_STK_FBTC"

    mock_db.execute.return_value.df.return_value = pd.DataFrame(
        [gold_row, ibit_row, fbtc_row]
    )
    # Holdings query (Rule 3 per-asset-latest CTE) for current_pct: gold is
    # 200 of a 1000 total portfolio market value -> 20%.
    mock_db.execute.return_value.fetchall.return_value = [
        ("ALTS_Paper_Gold", 200.0, 1000.0),
        ("US_STK_IBIT", 100.0, 1000.0),
        ("US_STK_FBTC", 50.0, 1000.0),
    ]

    resp = client.get("/valuation/snapshot/latest")
    assert resp.status_code == 200
    data = resp.json()
    by_ticker = {r["ticker"]: r for r in data}

    for ticker in ("GOLD", "IBIT", "FBTC"):
        row = by_ticker[ticker]
        assert row["display_mode"] == "band_position"
        assert row["valuation_signal"] is None
        assert row["signal_basis"] is None
        assert row["band_position"]["target_band"] is None

    assert by_ticker["GOLD"]["band_position"]["current_pct"] == pytest.approx(20.0)
    assert by_ticker["IBIT"]["band_position"]["current_pct"] == pytest.approx(10.0)
    assert by_ticker["FBTC"]["band_position"]["current_pct"] == pytest.approx(5.0)


def test_value_bucket_row_keeps_signal_display_mode(client, mock_db):
    """A value-bucket instrument (e.g. VOO, not in bucket_map) keeps
    display_mode='signal' and its signal fields unchanged."""
    import pandas as pd

    voo_row = _snapshot_row("VOO", "holding", "HIGH")
    mock_db.execute.return_value.df.return_value = pd.DataFrame([voo_row])
    mock_db.execute.return_value.fetchall.return_value = []

    resp = client.get("/valuation/snapshot/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["display_mode"] == "signal"
    assert data[0]["valuation_signal"] == "HIGH"
