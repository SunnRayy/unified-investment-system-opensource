"""Tests for insurance cost basis from premium payments."""

import duckdb
import pytest

pytestmark = pytest.mark.critical


from src.database.connector import DatabaseConnector
from src.sync.orchestrator import _set_insurance_cost_from_premiums


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
            quantity DECIMAL(20, 8),
            unit VARCHAR,
            cost_price_unit DECIMAL(20, 8),
            market_price_unit DECIMAL(20, 8),
            market_value DECIMAL(20, 2),
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
            quantity DECIMAL(20, 8),
            price_unit DECIMAL(20, 8),
            amount_gross DECIMAL(20, 2),
            amount_net DECIMAL(20, 2),
            commission_fee DECIMAL(20, 4),
            currency VARCHAR,
            account VARCHAR,
            memo VARCHAR,
            source_system VARCHAR,
            is_provisional BOOLEAN DEFAULT FALSE
        )
        """,
    )
    conn.close()
    connector = DatabaseConnector(db_path)
    try:
        yield connector
    finally:
        connector.close()


class TestInsuranceCostFromPremiums:
    def test_sets_cost_from_premium_sum(self, test_db):
        """Insurance cost = sum of all premium payments."""
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'INS_安泰人生', '安泰人生', 'Insurance',
             0.0, 'policy', NULL, NULL, 0.0, 'CNY', 'Insurance', 'Insurance_Excel', FALSE)
            """,
        )
        test_db.execute(
            """
            INSERT INTO transactions VALUES
            ('2024-01-01', 'INS_安泰人生', '安泰人生', 'premium_payment',
             0, 0, 3000.0, 3000.0, 0, 'CNY', 'Insurance', NULL, 'Insurance_Excel', FALSE),
            ('2025-01-01', 'INS_安泰人生', '安泰人生', 'premium_payment',
             0, 0, 3000.0, 3000.0, 0, 'CNY', 'Insurance', NULL, 'Insurance_Excel', FALSE)
            """,
        )

        count = _set_insurance_cost_from_premiums(test_db)

        assert count == 1
        row = test_db.execute(
            """
            SELECT cost_price_unit, quantity, market_value
            FROM holdings
            WHERE asset_id = 'INS_安泰人生' AND source_system = 'Insurance_Excel'
            """,
        ).fetchone()
        assert row is not None
        assert abs(float(row[0]) - 6000.0) < 0.01
        assert float(row[1]) == 1.0
        assert abs(float(row[2]) - 6000.0) < 0.01  # Market value backfilled from sum of premiums

    def test_no_premiums_sets_cost_equal_market(self, test_db):
        """If no premium transactions, cost = market_value (P&L = 0)."""
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'INS_Test', 'Test Policy', 'Insurance',
             0.0, 'policy', NULL, NULL, 2000.0, 'CNY', 'Insurance', 'Insurance_Excel', FALSE)
            """,
        )

        count = _set_insurance_cost_from_premiums(test_db)

        assert count == 1
        row = test_db.execute(
            """
            SELECT cost_price_unit, quantity
            FROM holdings
            WHERE asset_id = 'INS_Test'
            """,
        ).fetchone()
        assert row is not None
        assert abs(float(row[0]) - 2000.0) < 0.01
        assert float(row[1]) == 1.0

    def test_updates_latest_snapshot_only(self, test_db):
        """Insurance adjustment should target only the latest active snapshot."""
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-01-01', 'INS_History', 'History Policy', 'Insurance',
             0.0, 'policy', NULL, NULL, 1000.0, 'CNY', 'Insurance', 'Insurance_Excel', FALSE),
            ('2026-02-12', 'INS_History', 'History Policy', 'Insurance',
             0.0, 'policy', NULL, NULL, 2000.0, 'CNY', 'Insurance', 'Insurance_Excel', FALSE)
            """,
        )

        count = _set_insurance_cost_from_premiums(test_db)
        assert count == 1

        old_row = test_db.execute(
            """
            SELECT cost_price_unit, quantity
            FROM holdings
            WHERE asset_id = 'INS_History' AND snapshot_date = '2026-01-01'
            """,
        ).fetchone()
        latest_row = test_db.execute(
            """
            SELECT cost_price_unit, quantity
            FROM holdings
            WHERE asset_id = 'INS_History' AND snapshot_date = '2026-02-12'
            """,
        ).fetchone()

        assert old_row[0] is None
        assert float(old_row[1]) == 0.0
        assert abs(float(latest_row[0]) - 2000.0) < 0.01
        assert float(latest_row[1]) == 1.0

    def test_applies_to_active_pis_insurance_rows(self, test_db):
        """If PIS insurance row is active, it still needs premium-based cost."""
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'INS_安泰人生', '安泰人生', 'Insurance',
             2.0, 'policy', 0, NULL, 8624.5, 'CNY', 'Insurance', 'PIS', FALSE)
            """,
        )
        test_db.execute(
            """
            INSERT INTO transactions VALUES
            ('2024-01-01', 'INS_安泰人生', '安泰人生', 'premium_payment',
             0, 0, 3000.0, 3000.0, 0, 'CNY', 'Insurance', NULL, 'PIS', FALSE),
            ('2025-01-01', 'INS_安泰人生', '安泰人生', 'premium_payment',
             0, 0, 3000.0, 3000.0, 0, 'CNY', 'Insurance', NULL, 'PIS', FALSE)
            """,
        )

        count = _set_insurance_cost_from_premiums(test_db)

        assert count == 1
        row = test_db.execute(
            """
            SELECT cost_price_unit, quantity
            FROM holdings
            WHERE asset_id = 'INS_安泰人生' AND source_system = 'PIS'
            """,
        ).fetchone()
        assert row is not None
        assert abs(float(row[0]) - 3000.0) < 0.01
        assert abs(float(row[1]) - 2.0) < 0.01
