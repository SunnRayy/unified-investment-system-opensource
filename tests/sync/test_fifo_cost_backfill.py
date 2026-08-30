"""Tests for FIFO cost basis backfill."""

import duckdb
import pytest

pytestmark = pytest.mark.critical


from src.database.connector import DatabaseConnector
from src.sync.orchestrator import _backfill_fifo_cost_basis


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary DuckDB with holdings + transactions schema."""
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


class TestFIFOCostBackfill:
    def test_backfills_cn_fund_cost_from_transactions(self, test_db):
        """CN Fund with NULL cost_price_unit gets FIFO cost from transactions."""
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'CN_FUND_900013', 'Test Fund', 'Fund',
             1000.0, 'share', NULL, 10.0, 10000.0, 'CNY', 'CN Fund', 'CN_Fund_Excel', FALSE)
            """,
        )
        test_db.execute(
            """
            INSERT INTO transactions VALUES
            ('2025-01-01', 'CN_FUND_900013', 'Test Fund', 'buy',
             1000.0, 8.0, 8000.0, 8000.0, 0, 'CNY', 'CN Fund', NULL, 'CN_Fund_Excel', FALSE),
            ('2025-06-01', 'CN_FUND_900013', 'Test Fund', 'buy',
             500.0, 12.0, 6000.0, 6000.0, 0, 'CNY', 'CN Fund', NULL, 'CN_Fund_Excel', FALSE)
            """,
        )
        test_db.execute(
            """
            INSERT INTO transactions VALUES
            ('2025-09-01', 'CN_FUND_900013', 'Test Fund', 'sell',
             500.0, 11.0, 5500.0, 5500.0, 0, 'CNY', 'CN Fund', NULL, 'CN_Fund_Excel', FALSE)
            """,
        )

        count = _backfill_fifo_cost_basis(test_db)

        assert count == 1
        row = test_db.execute(
            """
            SELECT cost_price_unit FROM holdings
            WHERE asset_id = 'CN_FUND_900013' AND source_system = 'CN_Fund_Excel'
            """,
        ).fetchone()

        assert row is not None
        assert abs(float(row[0]) - 10.0) < 0.01

    def test_skips_usd_holdings_with_existing_cost_price_unit(self, test_db):
        """Reader-provided cost_price_unit is respected — backfill only fills NULL gaps.

        Schwab CSV provides cost_price_unit directly (in USD). The backfill must NOT
        override these reader-authority values. P&L calculations use the reader value
        with today's FX rate (constant-FX method) — see performance.py header comment.
        """
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'US_STK_AAPL', 'Apple', 'Equity',
             100.0, 'share', 150.0, 180.0, 18000.0, 'USD', 'Schwab', 'Schwab_CSV', FALSE)
            """,
        )
        test_db.execute(
            """
            INSERT INTO transactions VALUES
            ('2025-01-01', 'US_STK_AAPL', 'Apple', 'buy',
             100.0, 150.0, 15000.0, -15000.0, 0, 'USD', 'Schwab', NULL, 'Schwab_CSV', FALSE)
            """,
        )

        # cost_price_unit is already set (150.0, reader-provided USD) — backfill skips it
        count = _backfill_fifo_cost_basis(test_db)

        assert count == 0
        row = test_db.execute(
            "SELECT cost_price_unit FROM holdings WHERE asset_id = 'US_STK_AAPL'"
        ).fetchone()
        # Reader-provided value preserved unchanged
        assert float(row[0]) == 150.0

    def test_skips_legacy_source_holdings(self, test_db):
        """Legacy PIS holdings are not backfilled."""
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'CN_FUND_900013', 'Test Fund', 'Fund',
             1000.0, 'share', NULL, 10.0, 10000.0, 'CNY', 'CN Fund', 'PIS', FALSE)
            """,
        )

        count = _backfill_fifo_cost_basis(test_db)

        assert count == 0

    def test_no_transactions_sets_cost_zero(self, test_db):
        """If no transactions exist for an asset, cost_price_unit stays NULL."""
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'CN_FUND_999999', 'No Txns Fund', 'Fund',
             100.0, 'share', NULL, 5.0, 500.0, 'CNY', 'CN Fund', 'CN_Fund_Excel', FALSE)
            """,
        )

        count = _backfill_fifo_cost_basis(test_db)

        assert count == 0
        row = test_db.execute(
            """
            SELECT cost_price_unit FROM holdings WHERE asset_id = 'CN_FUND_999999'
            """,
        ).fetchone()
        assert row[0] is None

    def test_logs_warning_when_backfill_fails(self, test_db, monkeypatch, caplog):
        """Backfill failures should log warning with asset context."""
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'CN_FUND_FAIL', 'Fail Fund', 'Fund',
             10.0, 'share', NULL, 5.0, 50.0, 'CNY', 'CN Fund', 'CN_Fund_Excel', FALSE)
            """,
        )

        def _raise(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("src.services.transaction_source_selector.select_transaction_sources", _raise)

        with caplog.at_level("WARNING"):
            count = _backfill_fifo_cost_basis(test_db)

        assert count == 0
        assert any(
            "FIFO backfill failed for CN_FUND_FAIL: boom" in message
            for message in caplog.messages
        )
