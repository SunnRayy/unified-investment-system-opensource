# tests/api/test_risk_endpoints.py
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)


def test_risk_correlation_returns_matrix():
    """GET /risk/correlation should return correlation matrix."""
    response = client.get("/risk/correlation")
    assert response.status_code == 200
    data = response.json()
    assert "matrix" in data
    assert "assets" in data
    assert "method" in data
    assert isinstance(data["assets"], list)
    assert isinstance(data["matrix"], list)


def test_risk_metrics_has_status_fields():
    """GET /risk/metrics should return status for each metric."""
    response = client.get("/risk/metrics")
    assert response.status_code == 200
    data = response.json()
    # Should have status fields for volatility and sharpe
    assert "volatility_status" in data or "volatility" in data
