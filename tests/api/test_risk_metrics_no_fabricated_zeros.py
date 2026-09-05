"""A failed risk calculation must never be served as a measurement of zero.

Round 2 §2 reported the Risk Matrix showing Portfolio Volatility 0%,
Diversification Score 0/10, Sharpe 0, VaR 0% and Beta 0 on a fully synced
portfolio. Those numbers were not computed — they were fabricated, twice over:

  * `GET /risk/metrics` caught every exception and returned
    ``{"volatility": 0, ..., "beta": 0, "div_score": 0}`` with HTTP 200
  * the frontend client returned the *same* zeros object whenever the response
    was not ok

The tell is that `beta: 0` and `div_score: 0` are not reachable from the real
calculation at all. `calculate_portfolio_risk({})` — the genuine empty-portfolio
answer — yields ``beta: 0.8, div_score: 2``, because beta is
``0.8 + equity_weight * 0.4`` and div_score is ``min(10, n * 2 + 2)``. Neither
expression can produce zero. Any zero in those two fields is therefore proof of
a fabricated payload, which is what makes it a usable assertion here.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_db
from src.api.main import app
from src.database.connector import DatabaseConnector
from src.database.schema import bootstrap_database
from src.financial_analysis.risk_calculator import calculate_portfolio_risk


@pytest.fixture
def client(tmp_path):
    """A TestClient bound to a throwaway database, never the configured one."""
    db = DatabaseConnector(str(tmp_path / "risk.duckdb"))
    bootstrap_database(db)

    def _override_get_db():
        return db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def test_empty_portfolio_beta_and_div_score_are_never_zero():
    """Anti-vacuity guard for the assertions below.

    If the real calculation ever starts returning beta 0 / div_score 0 for an
    empty portfolio, the tests in this file stop distinguishing a fabricated
    payload from a genuine one and must be rewritten rather than left passing.
    """
    genuine = calculate_portfolio_risk({})
    assert genuine["beta"] != 0, genuine
    assert genuine["div_score"] != 0, genuine


def test_failed_risk_metrics_is_an_error_not_a_zero(client, monkeypatch):
    """The whole point: a computation that blew up must not return HTTP 200."""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated failure inside the risk calculation")

    monkeypatch.setattr("src.api.routes.data.calculate_portfolio_risk", _boom)

    response = client.get("/risk/metrics")

    assert response.status_code >= 400, (
        f"a failed risk calculation returned {response.status_code} with body "
        f"{response.json()} — a client cannot tell that apart from a real "
        "measurement, which is how five hard zeros reached the Risk Matrix"
    )
    assert "error" in response.json()


def test_failed_risk_metrics_does_not_serve_the_fabricated_payload(client, monkeypatch):
    """Belt and braces: even if the status code regresses, the zeros must not
    come back."""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated failure inside the risk calculation")

    monkeypatch.setattr("src.api.routes.data.calculate_portfolio_risk", _boom)

    body = client.get("/risk/metrics").json()

    if "beta" in body and "div_score" in body:
        assert not (body["beta"] == 0 and body["div_score"] == 0), (
            "beta 0 with div_score 0 is unreachable from calculate_portfolio_risk "
            f"— this payload was fabricated by an exception handler: {body}"
        )


def test_empty_portfolio_still_returns_a_real_two_hundred(client):
    """An empty database is a legitimate answer, not an error. The endpoint
    must keep distinguishing 'nothing to measure' from 'measuring failed'."""
    response = client.get("/risk/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["beta"] == pytest.approx(0.8), body
    assert body["div_score"] == 2, body
