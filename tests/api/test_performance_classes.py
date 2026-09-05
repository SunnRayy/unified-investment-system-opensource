from fastapi.testclient import TestClient
from src.api.main import app
import pytest

client = TestClient(app)

def test_performance_by_class_structure():
    response = client.get("/performance/by-class")
    assert response.status_code == 200
    data = response.json()
    
    assert "top_classes" in data
    assert "sub_classes" in data
    
    if not data["top_classes"]:
        pytest.skip("No data to test structure")
        
    cls = data["top_classes"][0]
    assert "realized_pl" in cls
    assert "lifetime_pl" in cls
    assert "unrealized_pl" in cls

def test_cash_pnl_is_zero():
    """Verify that Cash asset class has 0 unrealized P&L."""
    response = client.get("/performance/by-class")
    assert response.status_code == 200
    data = response.json()
    
    for cls in data["top_classes"]:
        if cls["class_name"] in ["Cash (现金)", "Cash"]:
            # Cost Basis should equal Market Value (approx) due to fix
            assert abs(cls["unrealized_pl"]) < 0.01, f"Cash Unrealized P&L should be 0, got {cls['unrealized_pl']}"
            assert abs(cls["cost_basis"] - cls["market_value"]) < 0.01
            
    # Also check sub-classes
    for cls in data["sub_classes"]:
         if cls["sub_class"] in ["Cash (现金)", "Cash"]:
            assert abs(cls["unrealized_pl"]) < 0.01, f"Cash Subclass Unrealized P&L should be 0, got {cls['unrealized_pl']}"

def test_realized_pl_aggregation():
    """Verify Realized P&L is being aggregated."""
    response = client.get("/performance/by-class")
    assert response.status_code == 200
    data = response.json()
    
    total_realized = sum(cls["realized_pl"] for cls in data["top_classes"])
    
    # Compare with summary endpoint
    summary_resp = client.get("/performance/summary")
    summary_data = summary_resp.json()
    
    # They should be roughly equal (float precision)
    # Note: Summary uses a simpler query, verify logic consistency
    assert abs(total_realized - summary_data["total_realized_pl"]) < 1.0, \
        f"Class Realized {total_realized} != Summary Realized {summary_data['total_realized_pl']}"
