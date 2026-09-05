"""Tests for per-sync legacy prefix normalization."""

import duckdb
import pytest

pytestmark = pytest.mark.pipeline


from src.database.connector import DatabaseConnector


@pytest.fixture
def test_db(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    conn = duckdb.connect(db_path)
    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            asset_type VARCHAR,
            quantity DECIMAL(20,8),
            unit VARCHAR,
            cost_price_unit DECIMAL(20,8),
            market_price_unit DECIMAL(20,8),
            market_value DECIMAL(20,2),
            currency VARCHAR,
            account VARCHAR,
            source_system VARCHAR,
            is_shadow BOOLEAN DEFAULT FALSE,
            UNIQUE (snapshot_date, asset_id, source_system)
        )
        """,
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            transaction_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            transaction_type VARCHAR,
            quantity DECIMAL(20,8),
            price_unit DECIMAL(20,8),
            amount_gross DECIMAL(20,2),
            amount_net DECIMAL(20,2),
            commission_fee DECIMAL(20,4),
            currency VARCHAR,
            account VARCHAR,
            memo VARCHAR,
            source_system VARCHAR,
            is_provisional BOOLEAN DEFAULT FALSE
        )
        """,
    )
    conn.execute(
        """
        CREATE TABLE asset_registry (
            canonical_id VARCHAR PRIMARY KEY,
            asset_class VARCHAR,
            asset_subclass VARCHAR
        )
        """,
    )
    conn.execute(
        """
        CREATE TABLE asset_source_mappings (
            canonical_id VARCHAR,
            source_system VARCHAR,
            source_id VARCHAR
        )
        """,
    )
    conn.close()

    connector = DatabaseConnector(db_path)
    try:
        yield connector
    finally:
        connector.close()


class TestNormalizeLegacyPrefixes:
    def test_normalizes_ins_to_INS(self, test_db):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'Ins_安泰人生', '安泰人生', 'Insurance',
             0, 'policy', 0, NULL, 8624.5, 'CNY', 'Insurance', 'PIS', FALSE)
            """,
        )

        from src.sync.orchestrator import _normalize_legacy_prefixes

        count = _normalize_legacy_prefixes(test_db)

        assert count > 0
        row = test_db.execute(
            "SELECT asset_id FROM holdings WHERE asset_id = 'INS_安泰人生'",
        ).fetchone()
        assert row is not None
        old = test_db.execute(
            "SELECT asset_id FROM holdings WHERE asset_id = 'Ins_安泰人生'",
        ).fetchone()
        assert old is None

    def test_normalizes_rsu_rsu_to_rsu(self, test_db):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'RSU_RSU_AMZN', 'Amazon RSU', 'RSU',
             100, 'share', 150, 180, 18000, 'USD', 'RSU', 'PIS', FALSE)
            """,
        )

        from src.sync.orchestrator import _normalize_legacy_prefixes

        count = _normalize_legacy_prefixes(test_db)

        assert count > 0
        row = test_db.execute(
            "SELECT asset_id FROM holdings WHERE asset_id = 'RSU_AMZN'",
        ).fetchone()
        assert row is not None

    def test_normalizes_transactions_too(self, test_db):
        test_db.execute(
            """
            INSERT INTO transactions VALUES
            ('2025-01-01', 'Ins_安泰人生', '安泰人生', 'premium_payment',
             0, 0, 3000, 3000, 0, 'CNY', 'Insurance', NULL, 'PIS', FALSE)
            """,
        )

        from src.sync.orchestrator import _normalize_legacy_prefixes

        _normalize_legacy_prefixes(test_db)
        row = test_db.execute(
            "SELECT asset_id FROM transactions WHERE asset_id = 'INS_安泰人生'",
        ).fetchone()
        assert row is not None

    def test_idempotent(self, test_db):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'INS_安泰人生', '安泰人生', 'Insurance',
             1, 'policy', 6000, NULL, 8624.5, 'CNY', 'Insurance', 'Insurance_Excel', FALSE)
            """,
        )

        from src.sync.orchestrator import _normalize_legacy_prefixes

        count = _normalize_legacy_prefixes(test_db)
        assert count == 0

    def test_handles_conflict_on_rename(self, test_db):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'Ins_安泰人生', '安泰人生', 'Insurance',
             0, 'policy', 0, NULL, 8624.5, 'CNY', 'Insurance', 'PIS', FALSE),
            ('2026-02-12', 'INS_安泰人生', '安泰人生', 'Insurance',
             1, 'policy', 6000, NULL, 8624.5, 'CNY', 'Insurance', 'PIS', FALSE)
            """,
        )

        from src.sync.orchestrator import _normalize_legacy_prefixes

        count = _normalize_legacy_prefixes(test_db)
        assert count > 0

        active_old = test_db.execute(
            """
            SELECT asset_id, source_system, is_shadow
            FROM holdings
            WHERE asset_id LIKE 'Ins_%' AND is_shadow = FALSE
            """,
        ).fetchall()
        assert len(active_old) == 0

    def test_normalizes_registry_and_source_mapping(self, test_db):
        test_db.execute(
            """
            INSERT INTO asset_registry VALUES
            ('Ins_安泰人生', '保险', '人寿险')
            """,
        )
        test_db.execute(
            """
            INSERT INTO asset_source_mappings VALUES
            ('Ins_安泰人生', 'PIS', 'Ins_安泰人生')
            """,
        )

        from src.sync.orchestrator import _normalize_legacy_prefixes

        _normalize_legacy_prefixes(test_db)

        registry = test_db.execute(
            "SELECT canonical_id FROM asset_registry WHERE canonical_id = 'INS_安泰人生'",
        ).fetchone()
        mapping = test_db.execute(
            "SELECT canonical_id FROM asset_source_mappings WHERE canonical_id = 'INS_安泰人生'",
        ).fetchone()

        assert registry is not None
        assert mapping is not None
