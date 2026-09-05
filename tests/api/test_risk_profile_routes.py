"""Tests for risk profile management API routes."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)


@pytest.fixture
def mock_connector():
    with patch("src.api.routes.risk_profiles.DatabaseConnector") as mock:
        conn = MagicMock()
        mock.return_value = conn
        yield conn


class TestGetProfiles:
    def test_returns_all_profiles(self, mock_connector):
        """GET /api/risk-profiles returns all profiles."""
        with patch("src.api.routes.risk_profiles.RiskProfileManager") as MockMgr:
            from src.classification.models import RiskProfile
            MockMgr.return_value.get_all_profiles.return_value = [
                RiskProfile(id=1, name="保守型", name_en="Conservative",
                           is_active=True, description="Low risk"),
                RiskProfile(id=2, name="平衡型", name_en="Balanced",
                           is_active=False, description="Medium risk"),
            ]
            response = client.get("/risk-profiles")
            assert response.status_code == 200
            assert len(response.json()["profiles"]) == 2
            assert response.json()["profiles"][0]["is_active"] is True


class TestCreateProfile:
    def test_creates_profile(self, mock_connector):
        """POST /api/risk-profiles creates a new profile."""
        with patch("src.api.routes.risk_profiles.RiskProfileManager") as MockMgr:
            MockMgr.return_value.create_profile.return_value = 5
            response = client.post("/risk-profiles", json={
                "name": "激进型",
                "name_en": "Aggressive",
                "description": "High risk tolerance",
            })
            assert response.status_code == 200
            assert response.json()["id"] == 5


class TestAllocations:
    def test_get_allocations_with_class_names(self, mock_connector):
        """GET /api/risk-profiles/{id}/allocations returns enriched allocations."""
        with patch("src.api.routes.risk_profiles.RiskProfileManager") as MockRPM, \
             patch("src.api.routes.risk_profiles.TaxonomyManager") as MockTM:
            MockRPM.return_value.get_allocations.return_value = {1: 30.0, 2: 70.0}
            from src.classification.models import TaxonomyClass
            MockTM.return_value.get_class_by_id.side_effect = [
                TaxonomyClass(id=1, name="股票", name_cn="股票", parent_id=None,
                             level=0, sort_order=1, is_rebalanceable=True, description=None),
                TaxonomyClass(id=2, name="固定收益", name_cn="固定收益", parent_id=None,
                             level=0, sort_order=2, is_rebalanceable=True, description=None),
            ]
            response = client.get("/risk-profiles/1/allocations")
            assert response.status_code == 200
            allocs = response.json()["allocations"]
            assert len(allocs) == 2
            assert allocs[0]["class_name"] == "股票"

    def test_update_allocations_validates_sum(self, mock_connector):
        """PUT /api/risk-profiles/{id}/allocations rejects allocations not summing to ~100%."""
        with patch("src.api.routes.risk_profiles.RiskProfileManager") as MockMgr:
            from src.classification.models import RiskProfile
            MockMgr.return_value.get_all_profiles.return_value = [
                RiskProfile(id=1, name="test", name_en=None, is_active=True, description=None)
            ]
            response = client.put("/risk-profiles/1/allocations", json={
                "allocations": {"1": 30.0, "2": 30.0}  # Only 60%
            })
            assert response.status_code == 400
            assert "60.0%" in response.json()["detail"]


class TestActivateProfile:
    def test_activates_profile(self, mock_connector):
        """POST /api/risk-profiles/{id}/activate sets profile as active."""
        with patch("src.api.routes.risk_profiles.RiskProfileManager") as MockMgr:
            from src.classification.models import RiskProfile
            MockMgr.return_value.get_all_profiles.return_value = [
                RiskProfile(id=1, name="test", name_en=None, is_active=False, description=None)
            ]
            response = client.post("/risk-profiles/1/activate")
            assert response.status_code == 200
            MockMgr.return_value.activate_profile.assert_called_once_with(1)

    def test_404_for_nonexistent_profile(self, mock_connector):
        """POST /api/risk-profiles/{id}/activate returns 404 for unknown profile."""
        with patch("src.api.routes.risk_profiles.RiskProfileManager") as MockMgr:
            MockMgr.return_value.get_all_profiles.return_value = []
            response = client.post("/risk-profiles/999/activate")
            assert response.status_code == 404
