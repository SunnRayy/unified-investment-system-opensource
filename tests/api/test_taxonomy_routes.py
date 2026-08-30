"""Tests for taxonomy management API routes."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from src.api.main import app
from src.api.dependencies import get_db

client = TestClient(app)


@pytest.fixture
def mock_connector():
    with patch("src.api.routes.taxonomy.DatabaseConnector") as mock:
        conn = MagicMock()
        mock.return_value = conn
        yield conn


@pytest.fixture
def mock_db():
    """Override get_db dependency with a MagicMock for GET handler tests."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    conn.execute.return_value = cursor
    app.dependency_overrides[get_db] = lambda: conn
    yield conn
    app.dependency_overrides.pop(get_db, None)


class TestGetClasses:
    def test_returns_hierarchy(self, mock_connector):
        """GET /api/taxonomy/classes returns class hierarchy."""
        with patch("src.api.routes.taxonomy.TaxonomyManager") as MockMgr:
            mgr = MockMgr.return_value
            # Mock a top-level class with one child
            from src.classification.models import TaxonomyClass
            top = TaxonomyClass(id=1, name="股票", name_cn="股票", parent_id=None,
                               level=0, sort_order=1, is_rebalanceable=True, description=None)
            child = TaxonomyClass(id=10, name="CN Equity", name_cn="A股", parent_id=1,
                                  level=1, sort_order=1, is_rebalanceable=True, description=None)
            mgr.get_top_level_classes.return_value = [top]
            mgr.get_children.return_value = [child]

            response = client.get("/taxonomy/classes")
            assert response.status_code == 200
            data = response.json()
            assert len(data["classes"]) == 1
            assert data["classes"][0]["name"] == "股票"
            assert len(data["classes"][0]["children"]) == 1
            assert data["classes"][0]["children"][0]["name"] == "CN Equity"


class TestCreateClass:
    def test_creates_class(self, mock_connector):
        """POST /api/taxonomy/classes creates a new class."""
        with patch("src.api.routes.taxonomy.TaxonomyManager") as MockMgr:
            mgr = MockMgr.return_value
            mgr.create_class.return_value = 99

            response = client.post("/taxonomy/classes", json={
                "name": "Crypto",
                "name_cn": "加密货币",
                "level": 1,
                "parent_id": 6,
            })
            assert response.status_code == 200
            assert response.json()["id"] == 99
            mgr.create_class.assert_called_once()


class TestUpdateClass:
    def test_updates_existing_class(self, mock_connector):
        """PUT /api/taxonomy/classes/{id} updates class fields."""
        with patch("src.api.routes.taxonomy.TaxonomyManager") as MockMgr:
            mgr = MockMgr.return_value
            from src.classification.models import TaxonomyClass
            mgr.get_class_by_id.return_value = TaxonomyClass(
                id=1, name="股票", name_cn="股票", parent_id=None,
                level=0, sort_order=1, is_rebalanceable=True, description=None
            )

            response = client.put("/taxonomy/classes/1", json={"name_cn": "权益"})
            assert response.status_code == 200
            mgr.update_class.assert_called_once_with(1, name_cn="权益")

    def test_404_for_missing_class(self, mock_connector):
        """PUT /api/taxonomy/classes/{id} returns 404 if class not found."""
        with patch("src.api.routes.taxonomy.TaxonomyManager") as MockMgr:
            MockMgr.return_value.get_class_by_id.return_value = None
            response = client.put("/taxonomy/classes/999", json={"name": "test"})
            assert response.status_code == 404


class TestDeleteClass:
    def test_deletes_unused_class(self, mock_connector):
        """DELETE /api/taxonomy/classes/{id} deletes class with no rule references."""
        with patch("src.api.routes.taxonomy.TaxonomyManager") as MockMgr:
            from src.classification.models import TaxonomyClass
            MockMgr.return_value.get_class_by_id.return_value = TaxonomyClass(
                id=99, name="Unused", name_cn=None, parent_id=None,
                level=1, sort_order=99, is_rebalanceable=False, description=None
            )
            mock_connector.execute.return_value = [{"cnt": 0}]

            response = client.delete("/taxonomy/classes/99")
            assert response.status_code == 200

    def test_409_if_rules_reference_class(self, mock_connector):
        """DELETE /api/taxonomy/classes/{id} returns 409 if rules exist."""
        with patch("src.api.routes.taxonomy.TaxonomyManager") as MockMgr:
            from src.classification.models import TaxonomyClass
            MockMgr.return_value.get_class_by_id.return_value = TaxonomyClass(
                id=1, name="股票", name_cn="股票", parent_id=None,
                level=0, sort_order=1, is_rebalanceable=True, description=None
            )
            mock_connector.execute.return_value = [{"cnt": 15}]

            response = client.delete("/taxonomy/classes/1")
            assert response.status_code == 409


class TestRules:
    def test_get_rules(self, mock_db):
        """GET /api/taxonomy/rules returns all rules with class names."""
        # Production code calls execute(...).fetchall() → list of tuples indexed by position
        # Columns: id, rule_type, pattern, class_id, tier_id, priority, source, created_at, class_name, class_name_cn, tier_name
        mock_db.execute.return_value.fetchall.return_value = [
            (1, "exact_id", "US_STK_AAPL", 1, None, 10, "manual", None, "US Equity", None, None)
        ]
        response = client.get("/taxonomy/rules")
        assert response.status_code == 200
        assert len(response.json()["rules"]) == 1

    def test_create_rule(self, mock_connector):
        """POST /api/taxonomy/rules creates a new classification rule."""
        with patch("src.api.routes.taxonomy.TaxonomyManager") as MockMgr:
            from src.classification.models import TaxonomyClass
            MockMgr.return_value.get_class_by_id.return_value = TaxonomyClass(
                id=1, name="股票", name_cn="股票", parent_id=None,
                level=0, sort_order=1, is_rebalanceable=True, description=None
            )
            response = client.post("/taxonomy/rules", json={
                "rule_type": "exact_id",
                "pattern": "US_STK_TSLA",
                "class_id": 1,
            })
            assert response.status_code == 200

    def test_create_rule_runs_auto_tagger(self, mock_connector):
        """POST /api/taxonomy/rules immediately reapplies classification rules."""
        mock_connector.execute.return_value.fetchone.return_value = (42,)
        with patch("src.api.routes.taxonomy.TaxonomyManager") as MockMgr, \
             patch("src.api.routes.taxonomy.AutoTagger") as MockTagger:
            from src.classification.models import TaxonomyClass
            MockMgr.return_value.get_class_by_id.return_value = TaxonomyClass(
                id=1, name="US Equity", name_cn=None, parent_id=None,
                level=1, sort_order=1, is_rebalanceable=True, description=None
            )

            response = client.post("/taxonomy/rules", json={
                "rule_type": "exact_id",
                "pattern": "US_STK_BRBK",
                "class_id": 1,
            })

            assert response.status_code == 200
            MockTagger.return_value.classify_registry.assert_called_once_with(mock_connector)

    def test_upsert_rule_runs_auto_tagger(self, mock_connector):
        """PUT /api/taxonomy/rules immediately reapplies classification rules."""
        mock_connector.execute.return_value.fetchall.return_value = []
        mock_connector.execute.return_value.fetchone.return_value = (42,)
        with patch("src.api.routes.taxonomy.TaxonomyManager") as MockMgr, \
             patch("src.api.routes.taxonomy.AutoTagger") as MockTagger:
            from src.classification.models import TaxonomyClass
            MockMgr.return_value.get_class_by_id.return_value = TaxonomyClass(
                id=1, name="US Equity", name_cn=None, parent_id=None,
                level=1, sort_order=1, is_rebalanceable=True, description=None
            )

            response = client.put("/taxonomy/rules", json={
                "rule_type": "exact_id",
                "pattern": "US_STK_BRBK",
                "class_id": 1,
            })

            assert response.status_code == 200
            MockTagger.return_value.classify_registry.assert_called_once_with(mock_connector)


class TestAutoTag:
    def test_runs_auto_tagger(self, mock_connector):
        """POST /api/taxonomy/auto-tag runs auto-tagger and returns stats."""
        with patch("src.api.routes.taxonomy.AutoTagger") as MockTagger:
            MockTagger.return_value.classify_registry.return_value = {
                "classified": 40, "unclassified": 2
            }
            response = client.post("/taxonomy/auto-tag")
            assert response.status_code == 200
            assert response.json()["classified"] == 40


class TestAssetAudit:
    def test_returns_all_assets_with_classifications(self, mock_db):
        """GET /api/taxonomy/audit returns assets with their class info."""
        # Production code calls execute(...).fetchall() → list of tuples indexed by position
        # Columns: canonical_id, display_name, asset_class, tier, class_name, class_name_cn,
        #          parent_class_name, parent_class_name_cn, market_value_cny, quantity, snapshot_date,
        #          source_system, market_price, price_currency, price_source, is_rebalanceable
        mock_db.execute.return_value.fetchall.return_value = [
            ("CN_FUND_900002", "嘉实沪深300", "CN Equity", "第一梯队 (底仓/价值型)", "CN Equity", None, None, None, 100000, None, None, "CN_Fund_Excel", 1.2345, "CNY", "akshare_fund", True)
        ]
        response = client.get("/taxonomy/audit")
        assert response.status_code == 200
        assert response.json()["total"] == 1
        asset = response.json()["assets"][0]
        assert asset["market_price"] == 1.2345
        assert asset["price_currency"] == "CNY"
        assert asset["price_source"] == "akshare_fund"

    def test_includes_tier_field_per_asset(self, mock_db):
        """GET /api/taxonomy/audit includes tier field from asset_registry.tier."""
        mock_db.execute.return_value.fetchall.return_value = [
            ("US_STK_AAPL", "Apple Inc", "US Equity", "第三梯队 (交易/择时)", "US Equity", None, "Equity", None, 500000, 10, "2026-02-01", "Schwab", 201.25, "USD", "yfinance", True)
        ]
        response = client.get("/taxonomy/audit")
        assert response.status_code == 200
        asset = response.json()["assets"][0]
        assert "tier" in asset
        assert asset["tier"] == "第三梯队 (交易/择时)"

    def test_tier_field_is_none_for_untiered_assets(self, mock_db):
        """GET /api/taxonomy/audit returns tier=None for assets without tier assignment."""
        mock_db.execute.return_value.fetchall.return_value = [
            ("CASH_CNY", "Cash CNY", "Cash", None, "Cash Checking", None, "Cash", None, 10000, None, "2026-02-01", "Schwab", None, "CNY", None, True)
        ]
        response = client.get("/taxonomy/audit")
        assert response.status_code == 200
        asset = response.json()["assets"][0]
        assert asset["tier"] is None


class TestSetAssetTier:
    def test_sets_tier_on_existing_asset(self, mock_connector):
        """PUT /taxonomy/assets/{asset_id}/tier updates asset_registry.tier."""
        # fetchone: tier lookup returns tier name
        mock_connector.execute.return_value.fetchone.return_value = ("第一梯队 (底仓/价值型)",)
        response = client.put("/taxonomy/assets/US_STK_AAPL/tier", json={"tier_id": "tier_1_core"})
        assert response.status_code == 200
        assert response.json()["tier"] == "第一梯队 (底仓/价值型)"

    def test_clears_tier_when_tier_id_is_null(self, mock_connector):
        """PUT /taxonomy/assets/{asset_id}/tier with tier_id=None clears the tier."""
        response = client.put("/taxonomy/assets/US_STK_AAPL/tier", json={"tier_id": None})
        assert response.status_code == 200
        assert response.json()["tier"] is None

    def test_404_when_tier_not_found(self, mock_connector):
        """PUT /taxonomy/assets/{asset_id}/tier returns 404 when tier_id doesn't exist."""
        mock_connector.execute.return_value.fetchone.return_value = None
        response = client.put("/taxonomy/assets/US_STK_AAPL/tier", json={"tier_id": "nonexistent_tier"})
        assert response.status_code == 404


class TestDeactivateAsset:
    def test_deactivates_asset_and_shadows_holdings(self, mock_connector):
        """DELETE /taxonomy/assets/{asset_id} deactivates registry row and shadows holdings."""
        mock_connector.execute.return_value.fetchone.return_value = ("US_STK_BRBK",)

        response = client.delete("/taxonomy/assets/US_STK_BRBK")

        assert response.status_code == 200
        statements = [call.args[0] for call in mock_connector.execute.call_args_list]
        assert any("UPDATE asset_registry" in statement and "is_active = FALSE" in statement for statement in statements)
        assert any("UPDATE holdings" in statement and "is_shadow = TRUE" in statement for statement in statements)

    def test_deactivate_missing_asset_returns_404(self, mock_connector):
        """DELETE /taxonomy/assets/{asset_id} returns 404 when the asset is unknown."""
        mock_connector.execute.return_value.fetchone.return_value = None

        response = client.delete("/taxonomy/assets/DOES_NOT_EXIST")

        assert response.status_code == 404
