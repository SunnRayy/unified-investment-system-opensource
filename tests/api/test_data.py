from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)

# We might need to mock get_db to return a mock connector/cursor suitable for these queries
# but for now let's see if the endpoints are reachable and return 200/empty lists if DB not populated.

def test_kpi_endpoint():
    response = client.get("/dashboard/kpi")
    # Should work even if DB empty (returns 0s)
    assert response.status_code == 200
    data = response.json()
    assert "net_worth" in data
    assert "market_pulse" in data

def test_audit_logs_endpoint():
    response = client.get("/audit/logs")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_insights_endpoint():
    response = client.get("/insights")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
