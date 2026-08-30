"""Tests for allocation validator post-sync validation."""
import pytest

pytestmark = pytest.mark.pipeline

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.classification.schema import create_classification_tables
from src.validation.allocation_validator import validate_allocations


class TestAllocationValidator:
    @pytest.fixture
    def connector(self):
        conn = DatabaseConnector(":memory:")
        initialize_schema(conn)
        create_classification_tables(conn)
        # Seed taxonomy_classes with sub-classes needed by tests
        conn.execute("""
            INSERT INTO taxonomy_classes (id, name, parent_id) VALUES
                (1, 'Equity', NULL),
                (2, 'Fixed Income', NULL),
                (3, 'Real Estate', NULL),
                (8, 'CN Equity', 1),
                (10, 'US Equity', 1),
                (15, 'Property', 3)
        """)
        # Seed a risk profile with target allocations
        conn.execute("""
            INSERT INTO risk_profiles (id, name, is_active) VALUES
                (1, 'Test Profile', TRUE)
        """)
        conn.execute("""
            INSERT INTO risk_profile_allocations (id, profile_id, class_id, target_pct) VALUES
                (1, 1, 8, 40.0),
                (2, 1, 10, 30.0),
                (3, 1, 2, 30.0)
        """)
        yield conn
        conn.close()

    def test_detects_allocation_drift(self, connector):
        """Should detect when current allocation drifts from target."""
        # Add current allocations with drift
        connector.execute("""
            INSERT INTO current_allocations (asset_class, asset_subclass, current_pct,
                                            is_rebalanceable, snapshot_date)
            VALUES
                ('CN Equity', NULL, 50.0, TRUE, CURRENT_DATE),
                ('US Equity', NULL, 30.0, TRUE, CURRENT_DATE),
                ('Fixed Income', NULL, 20.0, TRUE, CURRENT_DATE)
        """)

        drifts = validate_allocations(connector, threshold_pct=5.0)

        assert len(drifts) == 2  # CN Equity (50 vs 40, drift=10) and Fixed Income (20 vs 30, drift=-10)

        cn_drift = next(d for d in drifts if d.asset_class == 'CN Equity')
        assert cn_drift.drift_pct == 10.0
        assert cn_drift.recommendation == 'reduce'

    def test_ignores_non_rebalanceable(self, connector):
        """Should not flag drift for non-rebalanceable assets (no join match)."""
        connector.execute("""
            INSERT INTO current_allocations (asset_class, current_pct,
                                            is_rebalanceable, snapshot_date)
            VALUES
                ('Property', 50.0, FALSE, CURRENT_DATE)
        """)

        drifts = validate_allocations(connector, threshold_pct=5.0)

        # Property has is_rebalanceable=FALSE, should not appear
        assert not any(d.asset_class == 'Property' for d in drifts)

    def test_respects_threshold(self, connector):
        """Should only flag drifts above threshold."""
        connector.execute("""
            INSERT INTO current_allocations (asset_class, asset_subclass, current_pct,
                                            is_rebalanceable, snapshot_date)
            VALUES
                ('CN Equity', NULL, 42.0, TRUE, CURRENT_DATE),
                ('US Equity', NULL, 31.0, TRUE, CURRENT_DATE),
                ('Fixed Income', NULL, 27.0, TRUE, CURRENT_DATE)
        """)

        # With 5% threshold, none should be flagged (drifts: 2, 1, -3)
        drifts = validate_allocations(connector, threshold_pct=5.0)
        assert len(drifts) == 0

        # With 1% threshold, all should be flagged
        drifts = validate_allocations(connector, threshold_pct=1.0)
        assert len(drifts) == 3
