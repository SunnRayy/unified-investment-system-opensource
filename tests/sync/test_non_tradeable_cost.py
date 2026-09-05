"""Tests for non-tradeable asset P&L treatment."""

import duckdb
import logging
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
    conn.close()
    connector = DatabaseConnector(db_path)
    try:
        yield connector
    finally:
        connector.close()


class TestNonTradeableCost:
    def test_pension_cost_is_left_unknown_not_stamped_to_market_value(self, test_db):
        """Owner ruling 2026-08-09 (V88): 个人养老金 no longer gets a manufactured
        cost.

        Stamping cost = market_value made the pension report "+¥0.00" forever — a
        fake measurement, where a dash honestly says the cost is unknown. It is
        now balance-only until the owner logs a cost (#7). `Pension_` was removed
        from NON_TRADEABLE_PREFIXES; `Property_` keeps the old behaviour.
        """
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'Pension_Personal', 'Pension', 'Pension',
             0, 'account', 0, NULL, 36036.39, 'CNY', 'Pension', 'PIS', FALSE)
            """,
        )

        from src.sync.orchestrator import _zero_pl_for_non_tradeable_assets

        _zero_pl_for_non_tradeable_assets(test_db)

        row = test_db.execute(
            """
            SELECT cost_price_unit, market_value
            FROM holdings
            WHERE asset_id = 'Pension_Personal'
            """,
        ).fetchone()
        # Untouched: still the 0 it was inserted with, NOT re-stamped to 36036.39.
        assert float(row[0] or 0.0) == 0.0
        assert float(row[1]) == pytest.approx(36036.39), "market value must never move"

    def test_property_prefers_shadowed_legacy_cost_from_pis_sources(self, test_db):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'Property_House', 'House', 'Property',
             1, 'unit', 0, NULL, 2600000.0, 'CNY', 'Property', 'Financial_Summary_Excel', FALSE),
            ('2026-02-01', 'Property_House', 'House', 'Property',
             1, 'unit', 2800000.0, NULL, 2600000.0, 'CNY', 'Property', 'PIS', TRUE),
            ('2026-02-10', 'Property_House', 'House', 'Property',
             1, 'unit', 2820000.0, NULL, 2600000.0, 'CNY', 'Property', 'PIS_Historical', TRUE),
            ('2026-02-11', 'Property_House', 'House', 'Property',
             1, 'unit', 2900000.0, NULL, 2600000.0, 'CNY', 'Property', 'AIA', TRUE)
            """,
        )

        from src.sync.orchestrator import _zero_pl_for_non_tradeable_assets

        count = _zero_pl_for_non_tradeable_assets(test_db)

        assert count >= 1
        row = test_db.execute(
            """
            SELECT cost_price_unit, quantity
            FROM holdings
            WHERE asset_id = 'Property_House'
              AND source_system = 'Financial_Summary_Excel'
            """,
        ).fetchone()
        assert abs(float(row[0]) - 2820000.0) < 0.01
        assert float(row[1]) == 1.0

    def test_property_falls_back_to_market_and_warns_when_no_legacy_shadow_cost(
        self, test_db, caplog
    ):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'Property_House', 'House', 'Property',
             1, 'unit', 0, NULL, 2600000.0, 'CNY', 'Property', 'Financial_Summary_Excel', FALSE),
            ('2026-02-10', 'Property_House', 'House', 'Property',
             1, 'unit', 2820000.0, NULL, 2600000.0, 'CNY', 'Property', 'PIS', FALSE)
            """,
        )

        from src.sync.orchestrator import _zero_pl_for_non_tradeable_assets

        with caplog.at_level(logging.WARNING):
            count = _zero_pl_for_non_tradeable_assets(test_db)

        assert count >= 1
        row = test_db.execute(
            """
            SELECT cost_price_unit, quantity
            FROM holdings
            WHERE asset_id = 'Property_House'
              AND source_system = 'Financial_Summary_Excel'
            """,
        ).fetchone()
        assert abs(float(row[0]) - 2600000.0) < 0.01
        assert float(row[1]) == 1.0
        assert "No legacy shadow cost found for Property_House" in caplog.text

    def test_pension_legacy_cost_is_also_left_alone(self, test_db):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'Pension_Personal', 'Pension', 'Pension',
             1, 'unit', 0, NULL, 36036.39, 'CNY', 'Pension', 'Financial_Summary_Excel', FALSE),
            ('2026-02-01', 'Pension_Personal', 'Pension', 'Pension',
             1, 'unit', 50000.00, NULL, 36036.39, 'CNY', 'Pension', 'PIS', TRUE)
            """,
        )

        from src.sync.orchestrator import _zero_pl_for_non_tradeable_assets

        _zero_pl_for_non_tradeable_assets(test_db)

        row = test_db.execute(
            """
            SELECT cost_price_unit, quantity
            FROM holdings
            WHERE asset_id = 'Pension_Personal'
              AND source_system = 'Financial_Summary_Excel'
            """,
        ).fetchone()
        # Pension is out of scope entirely now — the 0 stands, unmodified.
        assert float(row[0] or 0.0) == 0.0
        assert float(row[1]) == 1.0

    def test_insurance_resets_to_zero_unrealized(self, test_db):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'INS_安泰人生', '安泰人生', 'Insurance',
             2, 'policy', 56181.9, NULL, 8624.5, 'CNY', 'Insurance', 'PIS', FALSE)
            """,
        )

        from src.sync.orchestrator import _zero_pl_for_non_tradeable_assets

        count = _zero_pl_for_non_tradeable_assets(test_db)

        assert count >= 1
        row = test_db.execute(
            """
            SELECT cost_price_unit, quantity, market_value
            FROM holdings
            WHERE asset_id = 'INS_安泰人生'
            """,
        ).fetchone()
        assert abs(float(row[0]) - float(row[2])) < 0.01
        assert float(row[1]) == 1.0
        assert abs(float(row[2]) - float(row[0]) * float(row[1])) < 0.01

    def test_does_not_touch_tradeable_assets(self, test_db):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'US_STK_AAPL', 'Apple', 'Equity',
             100, 'share', 150, 180, 18000, 'USD', 'Schwab', 'Schwab_CSV', FALSE)
            """,
        )

        from src.sync.orchestrator import _zero_pl_for_non_tradeable_assets

        count = _zero_pl_for_non_tradeable_assets(test_db)

        assert count == 0
        row = test_db.execute(
            "SELECT cost_price_unit FROM holdings WHERE asset_id = 'US_STK_AAPL'",
        ).fetchone()
        assert abs(float(row[0]) - 150.0) < 0.01
