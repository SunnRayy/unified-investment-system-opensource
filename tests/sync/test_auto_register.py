"""Tests for registering reader-discovered assets during sync."""
from datetime import date
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.pipeline

from src.database.connector import DatabaseConnector


@pytest.fixture
def connector():
    db = DatabaseConnector(":memory:")
    db.execute("""
        CREATE TABLE asset_registry (
            canonical_id VARCHAR PRIMARY KEY,
            display_name VARCHAR,
            asset_class VARCHAR,
            asset_subclass VARCHAR,
            extended_classification JSON,
            tier VARCHAR,
            is_rebalanceable BOOLEAN DEFAULT TRUE,
            risk_level VARCHAR,
            base_currency VARCHAR DEFAULT 'CNY',
            is_active BOOLEAN DEFAULT TRUE,
            last_price_update TIMESTAMP,
            sync_timestamp TIMESTAMP,
            is_pending BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            asset_type VARCHAR,
            market_value DOUBLE,
            currency VARCHAR,
            source_system VARCHAR,
            is_shadow BOOLEAN DEFAULT FALSE
        )
    """)
    db.execute("""
        CREATE TABLE transactions (
            transaction_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            source_system VARCHAR
        )
    """)
    db.execute("""
        CREATE TABLE asset_source_mappings (
            canonical_id VARCHAR,
            source_system VARCHAR,
            source_id VARCHAR,
            mapping_type VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source_system, source_id)
        )
    """)
    yield db
    db.close()


def test_auto_register_new_assets_from_reader_holdings(connector):
    from src.sync.orchestrator import _auto_register_new_assets

    connector.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type, market_value,
            currency, source_system, is_shadow
        ) VALUES
            (?, 'US_STK_BRBK', 'Berkshire Hathaway B', 'Stock', 231000, 'USD', 'Schwab_CSV', FALSE),
            (?, 'UNKNOWN_METADATA', 'Metadata', 'Unknown', 0, 'CNY', 'Schwab_CSV', FALSE)
        """,
        [date(2026, 5, 24), date(2026, 5, 24)],
    )

    with patch("src.sync.orchestrator.AutoTagger") as mock_tagger:
        mock_tagger.return_value.classify_registry.return_value = {"classified": 1, "unclassified": 0}

        inserted = _auto_register_new_assets(connector)

    assert inserted == 1
    row = connector.execute(
        """
        SELECT canonical_id, display_name, asset_class, base_currency, is_active, is_pending
        FROM asset_registry
        WHERE canonical_id = 'US_STK_BRBK'
        """
    ).fetchone()
    assert row == ("US_STK_BRBK", "Berkshire Hathaway B", None, "USD", True, True)
    assert connector.execute(
        "SELECT COUNT(*) FROM asset_registry WHERE canonical_id LIKE 'UNKNOWN_%'"
    ).fetchone()[0] == 0
    mock_tagger.return_value.classify_registry.assert_called_once_with(connector)
