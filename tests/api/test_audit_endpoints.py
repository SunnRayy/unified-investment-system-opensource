# tests/api/test_audit_endpoints.py
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)


def test_audit_summary_returns_expected_fields():
    """GET /audit/summary should return summary statistics."""
    response = client.get("/audit/summary")
    assert response.status_code == 200
    data = response.json()
    assert "total_logs" in data
    assert "last_sync_timestamp" in data
    assert "unresolved_conflicts" in data
    assert isinstance(data["total_logs"], int)


def test_audit_summary_unresolved_conflicts_is_non_negative():
    """unresolved_conflicts should be >= 0."""
    response = client.get("/audit/summary")
    data = response.json()
    assert data["unresolved_conflicts"] >= 0

