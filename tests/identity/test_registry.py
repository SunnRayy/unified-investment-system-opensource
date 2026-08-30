"""Tests for asset registry manager."""
import pytest

pytestmark = pytest.mark.pipeline

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.identity.registry import AssetRegistry


class TestAssetRegistry:
    @pytest.fixture
    def connector(self):
        conn = DatabaseConnector(":memory:")
        initialize_schema(conn)
        yield conn
        conn.close()

    @pytest.fixture
    def registry(self, connector):
        return AssetRegistry(connector)

    def test_register_new_asset(self, registry, connector):
        """Should register a new asset and create mapping."""
        canonical_id = registry.register_asset(
            source_id="900002",
            source_system="PIS",
            display_name="示例新兴产业股票",
            asset_class="Equity",
            asset_subclass="CN Equity"
        )

        assert canonical_id == "CN_FUND_900002"

        # Verify in database
        result = connector.execute(
            "SELECT display_name FROM asset_registry WHERE canonical_id = ?",
            (canonical_id,)
        ).fetchone()
        assert result[0] == "示例新兴产业股票"

    def test_lookup_existing_asset(self, registry):
        """Should return existing canonical_id on second registration."""
        # First registration
        canonical1 = registry.register_asset(
            source_id="900002",
            source_system="PIS",
            display_name="示例新兴产业股票"
        )

        # Second registration with same source
        canonical2 = registry.register_asset(
            source_id="900002",
            source_system="PIS",
            display_name="示例新兴产业股票"
        )

        assert canonical1 == canonical2

    def test_resolve_by_source(self, registry):
        """Should resolve canonical_id from source mapping."""
        registry.register_asset(
            source_id="600519",
            source_system="DSA",
            display_name="贵州茅台"
        )

        canonical = registry.resolve("600519", "DSA")
        assert canonical == "CN_STK_600519.SH"

    def test_resolve_unknown_returns_none(self, registry):
        """Should return None for unknown source ID."""
        canonical = registry.resolve("UNKNOWN123", "PIS")
        assert canonical is None

    def test_get_asset_info(self, registry):
        """Should retrieve full asset info by canonical_id."""
        registry.register_asset(
            source_id="900002",
            source_system="PIS",
            display_name="示例新兴产业股票",
            asset_class="Equity",
            asset_subclass="CN Equity",
            is_rebalanceable=True
        )

        info = registry.get_asset("CN_FUND_900002")
        assert info['display_name'] == "示例新兴产业股票"
        assert info['asset_class'] == "Equity"
        assert info['is_rebalanceable'] == True
