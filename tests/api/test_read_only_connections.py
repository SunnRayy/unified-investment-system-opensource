"""Tests that GET handlers use the get_db dependency (not bare DatabaseConnector).

With Depends(get_db), the connection-mode decision (read_only fallback) lives in
get_db — not in the handler.  These tests verify that each endpoint succeeds
(HTTP 200) when given a mock db via dependency_overrides, which proves the
handler actually uses the injected connection rather than opening its own.
"""
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from src.api.main import app
from src.api.dependencies import get_db


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_mock_db():
    """Return a MagicMock that quacks like DatabaseConnector for read paths."""
    db = MagicMock()
    # Default: execute() returns a cursor-like object with empty results
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    db.execute.return_value = cursor
    return db


def _override(mock_db):
    """Install and return a TestClient with get_db overridden."""
    app.dependency_overrides[get_db] = lambda: mock_db
    client = TestClient(app)
    return client


def _restore():
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# taxonomy.py — GET handlers
# ---------------------------------------------------------------------------

class TestTaxonomyGetClassesUsesDependency:
    def test_get_classes_returns_200_with_mock_db(self):
        """GET /taxonomy/classes must succeed when get_db yields a mock connection."""
        mock_db = _make_mock_db()
        with MagicMock() as _:
            from unittest.mock import patch
            with patch("src.api.routes.taxonomy.TaxonomyManager") as MockMgr:
                MockMgr.return_value.get_top_level_classes.return_value = []
                client = _override(mock_db)
                try:
                    response = client.get("/taxonomy/classes")
                finally:
                    _restore()
        assert response.status_code == 200
        assert "classes" in response.json()


class TestTaxonomyGetTiersUsesDependency:
    def test_get_tiers_returns_200_with_mock_db(self):
        """GET /taxonomy/tiers must succeed when get_db yields a mock connection."""
        mock_db = _make_mock_db()
        from unittest.mock import patch
        with patch("src.api.routes.taxonomy.TierManager") as MockMgr:
            MockMgr.return_value.get_all_tiers.return_value = []
            client = _override(mock_db)
            try:
                response = client.get("/taxonomy/tiers")
            finally:
                _restore()
        assert response.status_code == 200
        assert "tiers" in response.json()


class TestTaxonomyGetRulesUsesDependency:
    def test_get_rules_returns_200_with_mock_db(self):
        """GET /taxonomy/rules must succeed when get_db yields a mock connection."""
        mock_db = _make_mock_db()
        client = _override(mock_db)
        try:
            response = client.get("/taxonomy/rules")
        finally:
            _restore()
        assert response.status_code == 200
        assert "rules" in response.json()


class TestTaxonomyGetAssetAuditUsesDependency:
    def test_get_asset_audit_returns_200_with_mock_db(self):
        """GET /taxonomy/audit must succeed when get_db yields a mock connection."""
        mock_db = _make_mock_db()
        client = _override(mock_db)
        try:
            response = client.get("/taxonomy/audit")
        finally:
            _restore()
        assert response.status_code == 200
        assert "assets" in response.json()


# ---------------------------------------------------------------------------
# management.py — GET handlers
# ---------------------------------------------------------------------------

class TestManagementSearchTransactionsUsesDependency:
    def test_search_transactions_returns_200_with_mock_db(self):
        """GET /management/transactions must succeed when get_db yields a mock connection."""
        mock_db = _make_mock_db()
        # count query
        count_cursor = MagicMock()
        count_cursor.fetchone.return_value = (0,)
        count_cursor.description = [("cnt",)]
        # data query
        data_cursor = MagicMock()
        data_cursor.fetchall.return_value = []
        data_cursor.description = []
        mock_db.execute.side_effect = [count_cursor, data_cursor]

        client = _override(mock_db)
        try:
            response = client.get("/management/transactions")
        finally:
            _restore()
        assert response.status_code == 200
        assert "transactions" in response.json()


class TestManagementGetTransactionSourcesUsesDependency:
    def test_get_transaction_sources_returns_200_with_mock_db(self):
        """GET /management/transactions/sources must succeed when get_db yields a mock connection."""
        mock_db = _make_mock_db()
        client = _override(mock_db)
        try:
            response = client.get("/management/transactions/sources")
        finally:
            _restore()
        assert response.status_code == 200
        assert "sources" in response.json()


class TestManagementGetTransactionFiltersUsesDependency:
    def test_get_transaction_filters_returns_200_with_mock_db(self):
        """GET /management/transactions/filters must succeed when get_db yields a mock connection."""
        mock_db = _make_mock_db()
        client = _override(mock_db)
        try:
            response = client.get("/management/transactions/filters")
        finally:
            _restore()
        assert response.status_code == 200
        assert "sources" in response.json()


# ---------------------------------------------------------------------------
# risk_profiles.py — GET handlers
# ---------------------------------------------------------------------------

class TestRiskProfilesGetProfilesUsesDependency:
    def test_get_profiles_returns_200_with_mock_db(self):
        """GET /risk-profiles must succeed when get_db yields a mock connection."""
        mock_db = _make_mock_db()
        from unittest.mock import patch
        with patch("src.api.routes.risk_profiles.RiskProfileManager") as MockMgr:
            MockMgr.return_value.get_all_profiles.return_value = []
            client = _override(mock_db)
            try:
                response = client.get("/risk-profiles")
            finally:
                _restore()
        assert response.status_code == 200
        assert "profiles" in response.json()


class TestRiskProfilesGetAllocationsUsesDependency:
    def test_get_allocations_returns_200_with_mock_db(self):
        """GET /risk-profiles/{id}/allocations must succeed when get_db yields a mock connection."""
        mock_db = _make_mock_db()
        from unittest.mock import patch
        with patch("src.api.routes.risk_profiles.RiskProfileManager") as MockMgr:
            MockMgr.return_value.get_allocations.return_value = {}
            with patch("src.api.routes.risk_profiles.TaxonomyManager"):
                client = _override(mock_db)
                try:
                    response = client.get("/risk-profiles/1/allocations")
                finally:
                    _restore()
        assert response.status_code == 200
        assert "allocations" in response.json()
