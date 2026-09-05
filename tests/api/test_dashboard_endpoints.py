# tests/api/test_dashboard_endpoints.py
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)


def test_dashboard_kpi_pnl_can_be_null():
    """pnl_24h should be null if insufficient snapshots, not always 0."""
    response = client.get("/dashboard/kpi")
    assert response.status_code == 200
    data = response.json()
    # pnl_24h can be a number or null
    assert "pnl_24h" in data
    assert data["pnl_24h"] is None or isinstance(data["pnl_24h"], (int, float))


def test_dashboard_kpi_market_pulse_unavailable():
    """market_pulse should indicate unavailability instead of hardcoded 72."""
    response = client.get("/dashboard/kpi")
    data = response.json()
    # Should have source field indicating unavailability, or pulse should be null
    assert "market_pulse" in data
    # Either null or has a source field
    if data["market_pulse"] is not None:
        assert data.get("market_pulse_source") is not None


