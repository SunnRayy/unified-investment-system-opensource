from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)

def test_get_performance_summary_structure():
    # Mock return value for net_worth query
    # The endpoint uses db.execute().fetchone()
    # dependency injection might be tricky in unit test without override
    # but let's try to verify the 404 first (Red phase)
    
    response = client.get("/performance/summary")
    # Expect 200, will fail with 404
    assert response.status_code == 200

def test_get_gains_analysis():
    response = client.get("/performance/gains")
    assert response.status_code == 200
    data = response.json()
    assert "assets" in data
    assert "total_unrealized_pl" in data

def test_get_performance_by_class():
    response = client.get("/performance/by-class")
    assert response.status_code == 200
    data = response.json()
    assert "top_classes" in data
    assert "sub_classes" in data
