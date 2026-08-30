import asyncio

import duckdb
import pytest

pytestmark = pytest.mark.critical


from src.api.routes.data import get_wealthos_assets
from src.api.routes.data import get_wealthos_summary
from src.api.routes.performance import (
    calculate_realized_pnl,
    get_gains_analysis,
    get_performance_summary,
)
from src.services.transaction_source_selector import select_transaction_sources as _select_transaction_sources


class DuckDBAdapter:
    def __init__(self, connection: duckdb.DuckDBPyConnection):
        self.connection = connection

    def execute(self, query, params=None):
        if params is None:
            return self.connection.execute(query)
        return self.connection.execute(query, params)


@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "known_issue_phase47.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            asset_type VARCHAR,
            quantity DOUBLE,
            unit VARCHAR,
            cost_price_unit DOUBLE,
            market_price_unit DOUBLE,
            market_value DOUBLE,
            currency VARCHAR,
            account VARCHAR,
            source_system VARCHAR,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            transaction_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            transaction_type VARCHAR,
            quantity DOUBLE,
            price_unit DOUBLE,
            amount_gross DOUBLE,
            amount_net DOUBLE,
            commission_fee DOUBLE,
            currency VARCHAR,
            account VARCHAR,
            memo VARCHAR,
            source_system VARCHAR,
            is_provisional BOOLEAN
        )
        """
    )
    try:
        yield DuckDBAdapter(conn)
    finally:
        conn.close()


def test_wealthos_assets_uses_latest_snapshot_only(tmp_path):
    db_path = tmp_path / "known_issue_assets.duckdb"
    conn = duckdb.connect(str(db_path))
    db = DuckDBAdapter(conn)

    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            cost_price_unit DOUBLE,
            quantity DOUBLE,
            market_price_unit DOUBLE,
            market_value DOUBLE,
            currency VARCHAR,
            source_system VARCHAR,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            transaction_date DATE,
            asset_id VARCHAR,
            transaction_type VARCHAR,
            quantity DOUBLE,
            price_unit DOUBLE,
            currency VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE asset_registry (
            canonical_id VARCHAR,
            asset_class VARCHAR
        )
        """
    )

    conn.execute(
        """
        INSERT INTO holdings VALUES
        ('2026-02-11', 'US_STK_TEST', 'Test Asset', 50, 1, 100, 100, 'CNY', 'Schwab_CSV', FALSE),
        ('2026-02-12', 'US_STK_TEST', 'Test Asset', 100, 1, 120, 120, 'CNY', 'Schwab_CSV', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO transactions VALUES
        ('2026-01-01', 'US_STK_TEST', 'buy', 1, 100, 'CNY')
        """
    )
    conn.execute(
        """
        INSERT INTO asset_registry VALUES
        ('US_STK_TEST', 'US Equity')
        """
    )

    assets_raw = asyncio.run(get_wealthos_assets(db=db))
    assets = assets_raw.get("assets", []) + assets_raw.get("non_rebalanceable_assets", [])
    conn.close()

    row = next(asset for asset in assets if asset["code"] == "US_STK_TEST")
    assert row["cur"] == 120.0
    assert row["invested"] == 100.0
    assert row["pl"] == 20.0


def test_wealthos_assets_sold_then_reentered_remains_active(tmp_path):
    """Asset with latest non-shadow holding must remain ACTIVE even if a sell happened after snapshot.

    Regression target (Fix A):
    - Last holdings snapshot can lag transaction stream.
    - A post-snapshot sell followed by post-snapshot buy means position is re-entered.
    - WealthOS must not force status=CLOSED purely due to "any sell after snapshot".
    """
    db_path = tmp_path / "known_issue_sold_then_reentered.duckdb"
    conn = duckdb.connect(str(db_path))
    db = DuckDBAdapter(conn)

    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            cost_price_unit DOUBLE,
            quantity DOUBLE,
            market_price_unit DOUBLE,
            market_value DOUBLE,
            currency VARCHAR,
            source_system VARCHAR,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            transaction_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            transaction_type VARCHAR,
            quantity DOUBLE,
            price_unit DOUBLE,
            amount_gross DOUBLE,
            amount_net DOUBLE,
            commission_fee DOUBLE,
            currency VARCHAR,
            account VARCHAR,
            memo VARCHAR,
            source_system VARCHAR,
            is_provisional BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE asset_registry (
            canonical_id VARCHAR,
            asset_class VARCHAR
        )
        """
    )

    conn.execute(
        """
        INSERT INTO holdings VALUES
        ('2026-02-27', 'US_STK_SGOV', 'US_STK_SGOV', 703.0075, 336, 100.25, 236681.97, 'USD', 'Schwab_CSV', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO transactions VALUES
        ('2026-02-01', 'US_STK_SGOV', 'US_STK_SGOV', 'buy', 336, 100.0, -33600, -33600, 0, 'USD', 'Schwab', NULL, 'Schwab_CSV', FALSE),
        ('2026-03-04', 'US_STK_SGOV', 'US_STK_SGOV', 'sell', 30, 100.41, 3012.3, 3012.3, 0, 'USD', 'Schwab', NULL, 'Schwab_CSV', FALSE),
        ('2026-03-05', 'US_STK_SGOV', 'US_STK_SGOV', 'buy', 50, 100.42, -5021.0, -5021.0, 0, 'USD', 'Schwab', NULL, 'Schwab_CSV', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO asset_registry VALUES
        ('US_STK_SGOV', 'US Bonds')
        """
    )

    assets_raw = asyncio.run(get_wealthos_assets(include_non_rebalanceable=True, db=db))
    assets = assets_raw.get("assets", []) + assets_raw.get("non_rebalanceable_assets", [])
    conn.close()

    row = next(asset for asset in assets if asset["code"] == "US_STK_SGOV")
    assert row["status"] == "ACTIVE"
    assert row["cur"] == 236681.97


def test_wealthos_assets_financial_summary_active_not_closed_by_adjustment_pair(tmp_path):
    """Financial Summary active holding should not be closed by legacy adjustment pair."""
    db_path = tmp_path / "known_issue_pension_adjustment_pair.duckdb"
    conn = duckdb.connect(str(db_path))
    db = DuckDBAdapter(conn)

    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            cost_price_unit DOUBLE,
            quantity DOUBLE,
            market_price_unit DOUBLE,
            market_value DOUBLE,
            currency VARCHAR,
            source_system VARCHAR,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            transaction_date DATE,
            asset_id VARCHAR,
            asset_name VARCHAR,
            transaction_type VARCHAR,
            quantity DOUBLE,
            price_unit DOUBLE,
            amount_gross DOUBLE,
            amount_net DOUBLE,
            commission_fee DOUBLE,
            currency VARCHAR,
            account VARCHAR,
            memo VARCHAR,
            source_system VARCHAR,
            is_provisional BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE asset_registry (
            canonical_id VARCHAR,
            asset_class VARCHAR
        )
        """
    )

    conn.execute(
        """
        INSERT INTO holdings VALUES
        ('2026-02-01', 'Pension_Personal', '个人养老金', 37608.61, 1, 37608.61, 37608.61, 'CNY', 'Financial_Summary_Excel', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO transactions VALUES
        ('2026-02-06', 'Pension_Personal', '个人养老金', 'Adjustment_Buy', 1, 36036.39, NULL, -36036.39, 0, 'CNY', 'Pension', NULL, 'PIS_SQLite', FALSE),
        ('2026-02-07', 'Pension_Personal', '个人养老金', 'Adjustment_Sell', -1, 0, NULL, 0, 0, 'CNY', 'Pension', NULL, 'PIS_SQLite', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO asset_registry VALUES
        ('Pension_Personal', 'CN Equity')
        """
    )

    assets_raw = asyncio.run(get_wealthos_assets(include_non_rebalanceable=True, db=db))
    assets = assets_raw.get("assets", []) + assets_raw.get("non_rebalanceable_assets", [])
    conn.close()

    row = next(asset for asset in assets if asset["code"] == "Pension_Personal")
    assert row["status"] == "ACTIVE"
    assert row["cur"] == 37608.61


def test_realized_pnl_excludes_legacy_when_reader_source_exists(tmp_path):
    db_path = tmp_path / "known_issue_realized.dudb"
    conn = duckdb.connect(str(db_path))
    db = DuckDBAdapter(conn)

    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            source_system VARCHAR,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            transaction_type VARCHAR,
            quantity DOUBLE,
            price_unit DOUBLE,
            amount_net DOUBLE,
            currency VARCHAR,
            transaction_date DATE,
            asset_id VARCHAR,
            source_system VARCHAR
        )
        """
    )

    conn.execute(
        """
        INSERT INTO holdings VALUES
        ('2026-02-12', 'CN_FUND_900013', 'CN_Fund_Excel', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO transactions VALUES
        ('Buy', 100, 10, -1000, 'CNY', '2026-01-01', 'CN_FUND_900013', 'PIS_SQLite'),
        ('Sell', 50, 12, 600, 'CNY', '2026-01-02', 'CN_FUND_900013', 'PIS_SQLite'),
        ('Buy', 100, 10, -1000, 'CNY', '2026-01-01', 'CN_FUND_900013', 'CN_Fund_Excel'),
        ('Sell', 50, 12, 600, 'CNY', '2026-01-02', 'CN_FUND_900013', 'CN_Fund_Excel')
        """
    )

    realized, currency = calculate_realized_pnl(db, "CN_FUND_900013")
    conn.close()

    assert realized == 100.0
    assert currency == "CNY"


def test_realized_pnl_keeps_legacy_when_no_reader_source_exists(tmp_path):
    db_path = tmp_path / "known_issue_realized_legacy_only.dudb"
    conn = duckdb.connect(str(db_path))
    db = DuckDBAdapter(conn)

    conn.execute(
        """
        CREATE TABLE holdings (
            snapshot_date DATE,
            asset_id VARCHAR,
            source_system VARCHAR,
            is_shadow BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
            transaction_type VARCHAR,
            quantity DOUBLE,
            price_unit DOUBLE,
            amount_net DOUBLE,
            currency VARCHAR,
            transaction_date DATE,
            asset_id VARCHAR,
            source_system VARCHAR
        )
        """
    )

    conn.execute(
        """
        INSERT INTO holdings VALUES
        ('2026-02-12', 'CN_FUND_LEGACY', 'PIS_SQLite', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO transactions VALUES
        ('Buy', 100, 10, -1000, 'CNY', '2026-01-01', 'CN_FUND_LEGACY', 'PIS_SQLite'),
        ('Sell', 50, 12, 600, 'CNY', '2026-01-02', 'CN_FUND_LEGACY', 'PIS_SQLite')
        """
    )

    realized, currency = calculate_realized_pnl(db, "CN_FUND_LEGACY")
    conn.close()

    assert realized == 100.0
    assert currency == "CNY"


class TestPhase47LegacyShadowRegression:
    """Regression: Legacy PIS holdings must be shadow when reader source exists."""

    def test_legacy_holdings_shadowed_after_reader_insert(self, test_db):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'CN_FUND_900013', 'Fund A', 'Fund',
             1000, 'share', 8.0, 10.0, 10000.0, 'CNY', 'CN Fund', 'PIS', FALSE)
            """
        )
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'CN_FUND_900013', 'Fund A', 'Fund',
             1000, 'share', NULL, 10.0, 10000.0, 'CNY', 'CN Fund', 'CN_Fund_Excel', FALSE)
            """
        )

        from src.sync.orchestrator import _shadow_legacy_holdings

        count = _shadow_legacy_holdings(test_db)
        assert count >= 1

        pis_row = test_db.execute(
            """
            SELECT is_shadow FROM holdings
            WHERE asset_id = 'CN_FUND_900013' AND source_system = 'PIS'
            """
        ).fetchone()
        assert pis_row[0] is True

        reader_row = test_db.execute(
            """
            SELECT is_shadow FROM holdings
            WHERE asset_id = 'CN_FUND_900013' AND source_system = 'CN_Fund_Excel'
            """
        ).fetchone()
        assert reader_row[0] is False


class TestPhase47NoDualSourceAggregation:
    """Regression: API queries must not double-count from dual-source rows."""

    def test_market_value_not_doubled(self, test_db):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'CN_FUND_900013', 'Fund A', 'Fund',
             1000, 'share', 8.0, 10.0, 10000.0, 'CNY', 'CN Fund', 'PIS', FALSE),
            ('2026-02-12', 'CN_FUND_900013', 'Fund A', 'Fund',
             1000, 'share', NULL, 10.0, 10000.0, 'CNY', 'CN Fund', 'CN_Fund_Excel', FALSE)
            """
        )

        before = test_db.execute(
            """
            SELECT SUM(market_value) FROM holdings
            WHERE asset_id = 'CN_FUND_900013' AND is_shadow = FALSE
            """
        ).fetchone()[0]
        assert float(before) == 20000.0

        from src.sync.orchestrator import _shadow_legacy_holdings

        _shadow_legacy_holdings(test_db)

        after = test_db.execute(
            """
            SELECT SUM(market_value) FROM holdings
            WHERE asset_id = 'CN_FUND_900013' AND is_shadow = FALSE
            """
        ).fetchone()[0]
        assert float(after) == 10000.0


class TestPhase47BondSourceSelection:
    """Regression: avoid mixing AIA + Schwab transactions for the same bond ETF."""

    def test_prefers_schwab_over_aia_when_both_exist(self, test_db):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'US_STK_IEF', 'IEF', 'Fund',
             172, 'share', 0, 0, 1000, 'CNY', 'Broker', 'PIS', FALSE)
            """
        )
        test_db.execute(
            """
            INSERT INTO transactions VALUES
            ('2026-02-04', 'US_STK_IEF', 'IEF', 'sell',
             174, 668.5, 116319, 116319, 0, 'CNY', 'Broker', NULL, 'AIA', FALSE),
            ('2025-11-17', 'US_STK_IEF', 'IEF', 'buy',
             51, 96.5, -4922.5, -4922.5, 0, 'USD', 'Broker', NULL, 'Schwab_CSV', FALSE),
            ('2026-02-04', 'US_STK_IEF', 'IEF', 'sell',
             51, 95.5, 4870.5, 4870.5, 0, 'USD', 'Broker', NULL, 'Schwab_CSV', FALSE)
            """
        )

        selected = _select_transaction_sources(test_db, "US_STK_IEF")
        assert selected == ["Schwab_CSV"]

    def test_uses_aia_when_schwab_absent(self, test_db):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-12', 'US_STK_IEF', 'IEF', 'Fund',
             172, 'share', 0, 0, 1000, 'CNY', 'Broker', 'PIS', FALSE)
            """
        )
        test_db.execute(
            """
            INSERT INTO transactions VALUES
            ('2026-02-04', 'US_STK_IEF', 'IEF', 'sell',
             174, 668.5, 116319, 116319, 0, 'CNY', 'Broker', NULL, 'AIA', FALSE)
            """
        )

        selected = _select_transaction_sources(test_db, "US_STK_IEF")
        assert selected == ["AIA"]


class TestPhase47bRealizedPnLGuards:
    """Regression: money market and pension assets should not report realized P&L."""

    def test_money_market_asset_realized_pnl_forced_zero(self, test_db):
        test_db.execute(
            """
            CREATE TABLE asset_registry (
                canonical_id VARCHAR,
                asset_class VARCHAR
            )
            """
        )
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-13', 'CN_FUND_900007', '示例现金管理货币C', 'Fund',
             1000, 'share', 1, 1, 1000, 'CNY', 'CN Fund', 'CN_Fund_Excel', FALSE)
            """
        )
        test_db.execute(
            """
            INSERT INTO asset_registry VALUES
            ('CN_FUND_900007', '货币市场')
            """
        )
        test_db.execute(
            """
            INSERT INTO transactions VALUES
            ('2026-01-01', 'CN_FUND_900007', '示例现金管理货币C', 'buy',
             100, 1.0, -100, -100, 0, 'CNY', 'CN Fund', NULL, 'CN_Fund_Excel', FALSE),
            ('2026-01-02', 'CN_FUND_900007', '示例现金管理货币C', 'sell',
             100, 1.1, 110, 110, 0, 'CNY', 'CN Fund', NULL, 'CN_Fund_Excel', FALSE)
            """
        )

        realized, currency = calculate_realized_pnl(test_db, "CN_FUND_900007")
        assert realized == 0.0
        assert currency == "CNY"

    def test_pension_asset_realized_pnl_forced_zero(self, test_db):
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-13', 'Pension_Personal', 'Pension_Personal', 'Pension',
             1, 'account', 36036.39, NULL, 36036.39, 'CNY', 'Pension', 'PIS', FALSE)
            """
        )
        test_db.execute(
            """
            INSERT INTO transactions VALUES
            ('2026-02-06', 'Pension_Personal', 'Pension_Personal', 'Adjustment_Buy',
             1, 36036.39, NULL, -36036.39, 0, 'CNY', 'Pension', NULL, 'PIS_SQLite', FALSE),
            ('2026-02-07', 'Pension_Personal', 'Pension_Personal', 'Adjustment_Sell',
             -1, 0, NULL, 0, 0, 'CNY', 'Pension', NULL, 'PIS_SQLite', FALSE)
            """
        )

        realized, currency = calculate_realized_pnl(test_db, "Pension_Personal")
        assert realized == 0.0
        assert currency == "CNY"


class TestPhase47cCrossReportConsistency:
    """Regression: Performance and WealthOS should use consistent P&L semantics."""

    def test_performance_gains_treats_zh_cash_as_zero_unrealized(self, test_db):
        # Post-migration: Chinese '现金' is normalized to 'Cash Checking' during sync.
        # The API layer only sees English asset_class values.
        test_db.execute(
            """
            CREATE TABLE asset_registry (
                canonical_id VARCHAR,
                asset_class VARCHAR
            )
            """
        )
        test_db.execute(
            """
            CREATE TABLE taxonomy_classes (
                id INTEGER PRIMARY KEY,
                name VARCHAR,
                parent_id INTEGER
            )
            """
        )
        test_db.execute(
            """
            INSERT INTO asset_registry VALUES
            ('CASH_TEST', 'Cash Checking')
            """
        )
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-13', 'CASH_TEST', 'Cash Test', 'Cash',
             1, 'unit', 1, 1, 1000, 'CNY', 'Cash', 'PIS', FALSE)
            """
        )

        gains = asyncio.run(get_gains_analysis(period="all_time", db=test_db))
        row = next(item for item in gains["assets"] if item["asset_id"] == "CASH_TEST")
        assert row["unrealized_pl"] == 0.0

    def test_wealthos_summary_lifetime_matches_performance_summary(self, test_db):
        test_db.execute(
            """
            CREATE TABLE asset_registry (
                canonical_id VARCHAR,
                asset_class VARCHAR
            )
            """
        )
        test_db.execute(
            """
            CREATE TABLE asset_taxonomy (
                asset_class VARCHAR,
                asset_subclass VARCHAR,
                expired_date DATE
            )
            """
        )
        test_db.execute(
            """
            INSERT INTO asset_registry VALUES
            ('US_STK_TEST', 'US Equity')
            """
        )
        test_db.execute(
            """
            INSERT INTO asset_taxonomy VALUES
            ('股票', 'US Equity', NULL)
            """
        )
        test_db.execute(
            """
            INSERT INTO holdings VALUES
            ('2026-02-13', 'US_STK_TEST', 'US Test', 'Equity',
             1, 'share', 100, 120, 120, 'CNY', 'Broker', 'PIS', FALSE)
            """
        )
        test_db.execute(
            """
            INSERT INTO transactions VALUES
            ('2026-01-01', 'US_STK_TEST', 'US Test', 'buy',
             1, 100, -100, -100, 0, 'CNY', 'Broker', NULL, 'PIS', FALSE)
            """
        )

        wealth_summary = asyncio.run(get_wealthos_summary(db=test_db))
        perf_summary = asyncio.run(get_performance_summary(period="all_time", db=test_db))

        assert wealth_summary["total_lifetime_gain"] == perf_summary["total_lifetime_pl"]
