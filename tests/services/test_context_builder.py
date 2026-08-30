"""Tests for ContextBuilder."""

from __future__ import annotations

from asyncio import run
from unittest.mock import MagicMock, patch

import duckdb
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_mock(fetchall_return=None, fetchone_return=None):
    """Return a MagicMock DatabaseConnector."""
    db = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = fetchall_return or []
    result.fetchone.return_value = fetchone_return
    db.execute.return_value = result
    return db


def _make_builder(db_mock=None, aia_overrides=None):
    """Instantiate ContextBuilder with mocked DB and AIA paths.

    Bypasses __init__ entirely to avoid real DB and filesystem access.
    Patches the module-level DatabaseConnector so the import-time reference
    is also safe.
    """
    from src.services.ai_advisor.context_builder import ContextBuilder
    cb = ContextBuilder.__new__(ContextBuilder)
    cb._db = db_mock if db_mock is not None else _make_db_mock()
    cb._aia = aia_overrides if aia_overrides is not None else {}
    return cb


# ---------------------------------------------------------------------------
# Test 1: build_portfolio_context returns a string containing "%"
# ---------------------------------------------------------------------------

class TestBuildPortfolioContext:
    def test_summary_contains_percent(self):
        """build_portfolio_context('summary') must include a '%' sign."""
        alloc_rows = [
            ("Equity", 500000.0, 60.0),
            ("Fixed Income", 200000.0, 25.0),
            ("Cash", 125000.0, 15.0),
        ]
        db = _make_db_mock(fetchall_return=alloc_rows)

        cb = _make_builder(db_mock=db)
        result = cb.build_portfolio_context("summary")

        assert isinstance(result, str)
        assert "%" in result

    def test_detailed_includes_asset_table(self):
        """build_portfolio_context('detailed') should include a markdown table."""
        db = _make_db_mock()
        cb = _make_builder(db_mock=db)
        with patch(
            "src.services.ai_advisor.context_builder.build_compass_allocation",
            return_value=[
                {
                    "asset_class": "Equity (股票)",
                    "current_value": 500000.0,
                    "current_pct": 100.0,
                    "target_pct": 100.0,
                    "drift_pct": 0.0,
                    "is_top_level": True,
                }
            ],
        ), patch(
            "src.services.ai_advisor.context_builder.fetch_wealthos_active_holdings",
            return_value=[
                {
                    "asset_id": "US_STK_AAPL",
                    "name": "Apple Inc.",
                    "market_value": 500000.0,
                    "cost_basis": 400000.0,
                    "lifetime_pl": 100000.0,
                    "return_pct": 25.0,
                }
            ],
        ):
            result = cb.build_portfolio_context("detailed")

        assert "%" in result
        assert "Apple Inc." in result

    def test_detailed_uses_friendly_asset_labels_not_internal_ids(self):
        """Detailed holdings table should prefer asset labels over raw canonical IDs."""
        db = _make_db_mock()
        cb = _make_builder(db_mock=db)
        with patch(
            "src.services.ai_advisor.context_builder.build_compass_allocation",
            return_value=[
                {
                    "asset_class": "Fixed Income (固定收益)",
                    "current_value": 313792.0,
                    "current_pct": 100.0,
                    "target_pct": 100.0,
                    "drift_pct": 0.0,
                    "is_top_level": True,
                }
            ],
        ), patch(
            "src.services.ai_advisor.context_builder.fetch_wealthos_active_holdings",
            return_value=[
                {
                    "asset_id": "US_STK_SGOV",
                    "name": "ISHARES 0-3 MONTH TREASURY BOND ETF",
                    "market_value": 313792.0,
                    "cost_basis": 313792.0,
                    "lifetime_pl": 0.0,
                    "return_pct": 0.0,
                }
            ],
        ):
            result = cb.build_portfolio_context("detailed", include_non_rebalanceable=False)

        assert "SGOV" in result
        assert "ISHARES 0-3 MONTH TREASURY BOND ETF" in result
        assert "| US_STK_SGOV |" not in result

    def test_summary_formats_compass_top_level_rows(self):
        """Summary portfolio context should format top-level Compass rows directly."""
        db = _make_db_mock()
        cb = _make_builder(db_mock=db)
        with patch(
            "src.services.ai_advisor.context_builder.build_compass_allocation",
            return_value=[
                {
                    "asset_class": "Equity (股票)",
                    "current_value": 500000.0,
                    "current_pct": 60.0,
                    "target_pct": 65.0,
                    "drift_pct": -5.0,
                    "is_top_level": True,
                },
                {
                    "asset_class": "Fixed Income (固定收益)",
                    "current_value": 200000.0,
                    "current_pct": 25.0,
                    "target_pct": 18.0,
                    "drift_pct": 7.0,
                    "is_top_level": True,
                },
            ],
        ) as mock_allocation:
            result = cb.build_portfolio_context("summary")

        mock_allocation.assert_called_once_with(db, include_non_rebalanceable=True)
        assert "Equity" in result
        assert "Fixed Income" in result
        assert "数据暂不可用" not in result

    def test_summary_excludes_non_rebalanceable_and_recomputes_percentages(self):
        """Filtered portfolio context should exclude illiquid classes and renormalize weights."""
        db = duckdb.connect(":memory:")
        db.execute(
            """
            CREATE TABLE holdings (
                asset_id VARCHAR,
                asset_name VARCHAR,
                market_value DOUBLE,
                snapshot_date DATE,
                is_shadow BOOLEAN,
                cost_price_unit DOUBLE,
                quantity DOUBLE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE asset_registry (
                canonical_id VARCHAR,
                display_name VARCHAR,
                asset_class VARCHAR,
                is_rebalanceable BOOLEAN
            )
            """
        )
        db.execute(
            """
            CREATE TABLE taxonomy_classes (
                id INTEGER,
                name VARCHAR,
                name_cn VARCHAR,
                parent_id INTEGER,
                is_rebalanceable BOOLEAN
            )
            """
        )
        db.execute(
            """
            CREATE TABLE risk_profiles (
                id INTEGER,
                name VARCHAR,
                is_active BOOLEAN
            )
            """
        )
        db.execute(
            """
            CREATE TABLE risk_profile_allocations (
                id INTEGER,
                profile_id INTEGER,
                class_id INTEGER,
                target_pct DOUBLE
            )
            """
        )
        db.execute(
            """
            INSERT INTO taxonomy_classes VALUES
            (1, 'Equity', '股票', NULL, TRUE),
            (2, 'Real Estate', '房地产', NULL, FALSE),
            (3, 'Insurance', '保险', NULL, FALSE),
            (4, 'US Equity', '美股', 1, TRUE),
            (5, 'Property', '房产', 2, FALSE),
            (6, 'Insurance Products', '保险产品', 3, FALSE)
            """
        )
        db.execute(
            """
            INSERT INTO asset_registry VALUES
            ('US_STK_AAPL', 'Apple Inc.', 'US Equity', TRUE),
            ('Property_A', 'Property A', 'Property', FALSE),
            ('INS_AIA', 'AIA Policy', 'Insurance Products', FALSE)
            """
        )
        db.execute(
            """
            INSERT INTO holdings VALUES
            ('US_STK_AAPL', 'Apple Inc.', 800000, '2026-03-20', FALSE, 5000, 100),
            ('Property_A', 'Property A', 100000, '2026-03-20', FALSE, 100000, 1),
            ('INS_AIA', 'AIA Policy', 100000, '2026-03-20', FALSE, 100000, 1)
            """
        )
        db.execute("INSERT INTO risk_profiles VALUES (1, 'Balanced', TRUE)")
        db.execute(
            """
            INSERT INTO risk_profile_allocations VALUES
            (1, 1, 4, 80.0),
            (2, 1, 5, 10.0),
            (3, 1, 6, 10.0)
            """
        )

        cb = _make_builder()
        cb._db = db
        result = cb.build_portfolio_context("summary", include_non_rebalanceable=False)
        db.close()

        assert "Equity: 当前100.0% vs 目标100.0%" in result
        assert "Real Estate" not in result
        assert "Insurance" not in result

    def test_drift_uses_compass_child_targets_without_double_counting(self, tmp_path):
        """Top-level drift should sum child risk-profile targets exactly once."""
        db_path = tmp_path / "ai_advisor_compass_targets.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE holdings (
                asset_id VARCHAR,
                asset_name VARCHAR,
                market_value DOUBLE,
                snapshot_date DATE,
                is_shadow BOOLEAN
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE asset_registry (
                canonical_id VARCHAR,
                display_name VARCHAR,
                asset_class VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE taxonomy_classes (
                id INTEGER,
                name VARCHAR,
                name_cn VARCHAR,
                parent_id INTEGER,
                is_rebalanceable BOOLEAN
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE risk_profiles (
                id INTEGER,
                name VARCHAR,
                is_active BOOLEAN
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE risk_profile_allocations (
                id INTEGER,
                profile_id INTEGER,
                class_id INTEGER,
                target_pct DOUBLE,
                created_at DATE
            )
            """
        )

        conn.execute(
            """
            INSERT INTO taxonomy_classes VALUES
            (1, 'Equity', '股票', NULL, TRUE),
            (2, 'Fixed Income', '固定收益', NULL, TRUE),
            (3, 'Cash', '现金', NULL, TRUE),
            (4, 'US Equity', '美股', 1, TRUE),
            (5, 'CN Equity', 'A股', 1, TRUE)
            """
        )
        conn.execute(
            """
            INSERT INTO asset_registry VALUES
            ('US_STK_AAPL', 'Apple Inc.', 'US Equity'),
            ('US_BOND_SGOV', 'SGOV', 'Fixed Income')
            """
        )
        conn.execute(
            """
            INSERT INTO holdings VALUES
            ('US_STK_AAPL', 'Apple Inc.', 700000, '2026-03-20', FALSE),
            ('US_BOND_SGOV', 'SGOV', 300000, '2026-03-20', FALSE)
            """
        )
        conn.execute("INSERT INTO risk_profiles VALUES (1, 'Active', TRUE)")
        conn.execute(
            """
            INSERT INTO risk_profile_allocations VALUES
            (1, 1, 4, 30.0, '2026-03-20'),
            (2, 1, 5, 35.0, '2026-03-20'),
            (3, 1, 2, 20.0, '2026-03-20'),
            (4, 1, 3, 5.0, '2026-03-20')
            """
        )

        cb = _make_builder()
        cb._db = conn
        result = cb.build_portfolio_context("summary", include_non_rebalanceable=False)
        conn.close()

        assert "Equity: 当前70.0% vs 目标65.0%" in result
        assert "Fixed Income: 当前30.0% vs 目标20.0%" in result
        assert "目标130.0%" not in result

    def test_drift_uses_compass_targets_for_commodity_and_alternative(self, tmp_path):
        """Commodity and alternative drift should come from active Compass targets, not zero fallback."""
        db_path = tmp_path / "ai_advisor_compass_targets_partial.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE holdings (
                asset_id VARCHAR,
                asset_name VARCHAR,
                market_value DOUBLE,
                snapshot_date DATE,
                is_shadow BOOLEAN
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE asset_registry (
                canonical_id VARCHAR,
                display_name VARCHAR,
                asset_class VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE taxonomy_classes (
                id INTEGER,
                name VARCHAR,
                name_cn VARCHAR,
                parent_id INTEGER,
                is_rebalanceable BOOLEAN
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE risk_profiles (
                id INTEGER,
                name VARCHAR,
                is_active BOOLEAN
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE risk_profile_allocations (
                id INTEGER,
                profile_id INTEGER,
                class_id INTEGER,
                target_pct DOUBLE,
                created_at DATE
            )
            """
        )

        conn.execute(
            """
            INSERT INTO taxonomy_classes VALUES
            (1, 'Equity', '股票', NULL, TRUE),
            (2, 'Fixed Income', '固定收益', NULL, TRUE),
            (3, 'Real Estate', '房地产', NULL, FALSE),
            (4, 'Commodity', '商品', NULL, TRUE),
            (5, 'Cash', '现金', NULL, TRUE),
            (6, 'Alternative', '另类投资', NULL, TRUE),
            (7, 'Insurance', '保险', NULL, FALSE),
            (8, 'US Equity', '美股', 1, TRUE),
            (9, 'Gold', '黄金', 4, TRUE),
            (10, 'Crypto', '加密货币', 6, TRUE)
            """
        )
        conn.execute(
            """
            INSERT INTO asset_registry VALUES
            ('US_STK_MSFT', 'Microsoft', 'US Equity'),
            ('ALTS_Paper_Gold', 'Paper Gold', 'Gold'),
            ('US_STK_IBIT', 'IBIT', 'Crypto')
            """
        )
        conn.execute(
            """
            INSERT INTO holdings VALUES
            ('US_STK_MSFT', 'Microsoft', 700000, '2026-03-20', FALSE),
            ('ALTS_Paper_Gold', 'Paper Gold', 200000, '2026-03-20', FALSE),
            ('US_STK_IBIT', 'IBIT', 100000, '2026-03-20', FALSE)
            """
        )
        conn.execute("INSERT INTO risk_profiles VALUES (1, 'Active', TRUE)")
        conn.execute(
            """
            INSERT INTO risk_profile_allocations VALUES
            (1, 1, 8, 60.0, '2026-03-20'),
            (2, 1, 9, 8.0, '2026-03-20'),
            (3, 1, 10, 7.0, '2026-03-20')
            """
        )

        cb = _make_builder()
        cb._db = conn
        result = cb.build_portfolio_context("summary", include_non_rebalanceable=False)
        conn.close()

        assert "Commodities: 当前20.0% vs 目标8.0%" in result
        assert "Alternatives: 当前10.0% vs 目标7.0%" in result
        assert "目标0.0%" not in result

    def test_portfolio_context_matches_compass_top_level_allocation(self, tmp_path):
        """Brief portfolio context should use the same top-level current/target/drift as Compass."""
        from src.api.routes.compass import get_compass_allocation

        db_path = tmp_path / "ai_advisor_compass_consistency.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE holdings (
                asset_id VARCHAR,
                asset_name VARCHAR,
                market_value DOUBLE,
                currency VARCHAR,
                snapshot_date DATE,
                is_shadow BOOLEAN
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE asset_registry (
                canonical_id VARCHAR,
                display_name VARCHAR,
                asset_class VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE taxonomy_classes (
                id INTEGER,
                name VARCHAR,
                name_cn VARCHAR,
                parent_id INTEGER,
                level INTEGER,
                is_rebalanceable BOOLEAN
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE risk_profiles (
                id INTEGER,
                name VARCHAR,
                is_active BOOLEAN
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE risk_profile_allocations (
                id INTEGER,
                profile_id INTEGER,
                class_id INTEGER,
                target_pct DOUBLE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE target_allocations (
                id INTEGER,
                asset_class VARCHAR,
                target_pct DOUBLE,
                taxonomy_type VARCHAR,
                effective_date DATE
            )
            """
        )

        conn.execute(
            """
            INSERT INTO taxonomy_classes VALUES
            (1, 'Equity', '股票', NULL, 0, TRUE),
            (2, 'Fixed Income', '固定收益', NULL, 0, TRUE),
            (3, 'Cash', '现金', NULL, 0, TRUE),
            (4, 'US Equity', '美股', 1, 1, TRUE),
            (5, 'US Bonds', '美债', 2, 1, TRUE),
            (6, 'Cash Checking', '活期', 3, 1, TRUE)
            """
        )
        conn.execute(
            """
            INSERT INTO asset_registry VALUES
            ('US_STK_AAPL', 'Apple Inc.', 'US Equity'),
            ('US_BOND_SGOV', 'SGOV', 'US Bonds'),
            ('CASH_CNY', 'Cash', 'Cash Checking')
            """
        )
        conn.execute(
            """
            INSERT INTO holdings VALUES
            ('US_STK_AAPL', 'Apple Inc.', 700000, 'CNY', '2026-03-20', FALSE),
            ('US_BOND_SGOV', 'SGOV', 200000, 'CNY', '2026-03-20', FALSE),
            ('CASH_CNY', 'Cash', 100000, 'CNY', '2026-03-20', FALSE)
            """
        )
        conn.execute("INSERT INTO risk_profiles VALUES (1, 'Active', TRUE)")
        conn.execute(
            """
            INSERT INTO risk_profile_allocations VALUES
            (1, 1, 4, 65.0),
            (2, 1, 5, 18.0),
            (3, 1, 6, 2.0)
            """
        )
        conn.execute(
            """
            INSERT INTO target_allocations VALUES
            (1, '股票', 40.0, 'Asset Class', '2026-03-09'),
            (2, '固定收益', 30.0, 'Asset Class', '2026-03-09'),
            (3, '现金', 30.0, 'Asset Class', '2026-03-09')
            """
        )

        compass_rows = run(get_compass_allocation(include_non_rebalanceable=False, db=conn))
        top_level = {row["asset_class"].split(" (")[0]: row for row in compass_rows if row["is_top_level"]}

        cb = _make_builder()
        cb._db = conn
        result = cb.build_portfolio_context("summary", include_non_rebalanceable=False)
        conn.close()

        equity = top_level["Equity"]
        fixed_income = top_level["Fixed Income"]
        cash = top_level["Cash"]

        assert f"Equity: 当前{equity['current_pct']:.1f}% vs 目标{equity['target_pct']:.1f}%" in result
        assert f"Fixed Income: 当前{fixed_income['current_pct']:.1f}% vs 目标{fixed_income['target_pct']:.1f}%" in result
        assert f"Cash: 当前{cash['current_pct']:.1f}% vs 目标{cash['target_pct']:.1f}%" in result

    def test_summary_uses_shared_performance_semantics_helper(self):
        """AI Advisor performance context should be built from the shared semantics helper."""
        db = _make_db_mock()
        cb = _make_builder(db_mock=db)

        perf_summary = {
            "net_worth": 800000.0,
            "total_cost_basis": 600000.0,
            "total_unrealized_pl": 200000.0,
            "unrealized_pl_pct": 33.33,
            "total_realized_pl": 120.0,
            "total_lifetime_pl": 200120.0,
            "asset_count": 2,
            "snapshot_date": "2026-03-20",
        }
        with patch(
            "src.services.ai_advisor.context_builder.build_portfolio_summary_semantics",
            return_value=perf_summary,
        ) as mock_summary, patch(
            "src.services.ai_advisor.context_builder.calculate_portfolio_twr",
            return_value={"cumulative": 0.1234, "annualized": 0.0812},
        ), patch(
            "src.services.ai_advisor.context_builder.calculate_portfolio_xirr",
            return_value=0.102,
        ), patch(
            "src.services.ai_advisor.context_builder.calculate_portfolio_metrics",
            return_value={
                "sharpe_ratio": 1.1,
                "sortino_ratio": 1.5,
                "max_drawdown": 8.2,
                "calmar_ratio": 0.7,
                "volatility_annual": 12.3,
                "total_return": 15.8,
                "data_points": 24,
            },
        ):
            result = cb.build_portfolio_context("summary", include_non_rebalanceable=False)

        mock_summary.assert_called_once_with(db, include_non_rebalanceable=False)
        assert "Net Worth: ¥800,000" in result
        assert "Unrealized P&L" in result
        assert "TWR (Cumulative)" in result
        assert "MWR (XIRR)" in result
        assert "Max Drawdown" in result

    def test_detailed_uses_wealthos_active_holdings_helper(self):
        """AI Advisor holdings detail should follow WealthOS active-holding semantics."""
        db = _make_db_mock()
        cb = _make_builder(db_mock=db)

        helper_rows = [
            {
                "asset_id": "US_STK_AAPL",
                "name": "Apple Inc.",
                "asset_class": "US Equity",
                "source_system": "Schwab_CSV",
                "market_value": 700000.0,
                "cost_basis": 500000.0,
                "total_quantity": 100.0,
                "top_class": "Equity",
                "sub_class": "US Equity",
                "is_rebalanceable": True,
            },
            {
                "asset_id": "CASH_CNY",
                "name": "Cash Checking",
                "asset_class": "Cash Checking",
                "source_system": "PIS",
                "market_value": 100000.0,
                "cost_basis": 100000.0,
                "total_quantity": 1.0,
                "top_class": "Cash",
                "sub_class": "Cash Checking",
                "is_rebalanceable": True,
            },
        ]

        with patch(
            "src.services.ai_advisor.context_builder.fetch_wealthos_active_holdings",
            return_value=helper_rows,
        ) as mock_holdings, patch(
            "src.services.ai_advisor.context_builder.build_portfolio_summary_semantics",
            return_value={
                "net_worth": 800000.0,
                "total_cost_basis": 600000.0,
                "total_unrealized_pl": 200000.0,
                "unrealized_pl_pct": 33.33,
                "total_realized_pl": 120.0,
                "total_lifetime_pl": 200120.0,
                "asset_count": 2,
                "snapshot_date": "2026-03-20",
            },
        ), patch(
            "src.services.ai_advisor.context_builder.calculate_portfolio_twr",
            return_value={"cumulative": 0.1234, "annualized": 0.0812},
        ), patch(
            "src.services.ai_advisor.context_builder.calculate_portfolio_xirr",
            return_value=0.102,
        ), patch(
            "src.services.ai_advisor.context_builder.calculate_portfolio_metrics",
            return_value={
                "sharpe_ratio": 1.1,
                "sortino_ratio": 1.5,
                "max_drawdown": 8.2,
                "calmar_ratio": 0.7,
                "volatility_annual": 12.3,
                "total_return": 15.8,
                "data_points": 24,
            },
        ):
            result = cb.build_portfolio_context("detailed", include_non_rebalanceable=False)

        mock_holdings.assert_called_once_with(db, include_non_rebalanceable=False)
        assert "Apple Inc." in result
        assert "Cash Checking" in result
        assert "US_STK_AAPL" not in result


# ---------------------------------------------------------------------------
# Test 2: build_identity_context truncates to 500 chars for summary
# ---------------------------------------------------------------------------

class TestBuildIdentityContext:
    def test_db_profile_included_when_user_profile_exists(self):
        """Investor profile from user_profile table is included when a display_name exists."""
        import json as _json
        db = MagicMock()

        phil = _json.dumps({"goal": "目标测试", "risk_tolerance": "30%回撤"})

        def side_effect(query, params=()):
            result = MagicMock()
            if "user_profile" in query:
                # Two-column return: (display_name, philosophy)
                result.fetchone.return_value = ("Ray Guo", phil)
            elif "risk_profiles" in query:
                result.fetchone.return_value = (1, "积极型", "Aggressive", "高风险高回报")
            elif "risk_profile_allocations" in query:
                result.fetchall.return_value = [("US Equity", 50.0), ("CN Equity", 30.0)]
            elif "ai_insights" in query:
                result.fetchall.return_value = []
            else:
                result.fetchall.return_value = []
                result.fetchone.return_value = None
            return result

        db.execute.side_effect = side_effect
        cb = _make_builder(db_mock=db)
        result = cb.build_identity_context("summary")

        assert isinstance(result, str)
        assert "Ray Guo" in result
        assert "积极型" in result or "Aggressive" in result

    def test_detail_levels_vary_profile_content(self):
        """Summary/detailed/full must produce different identity content depth."""
        import json as _json
        db = MagicMock()

        phil = _json.dumps({
            "goal": "财务独立 2000万",
            "horizon": "10-20年",
            "risk_tolerance": "最大30%回撤",
            "core_weakness": "受市场噪音干扰",
            "portfolio_structure": "权益64% 固收20% 另类15% 现金1%",
        })

        def side_effect(query, params=()):
            result = MagicMock()
            if "user_profile" in query:
                result.fetchone.return_value = (None, phil)
            elif "risk_profiles" in query:
                result.fetchone.return_value = (1, "均衡型", None, None)
            elif "risk_profile_allocations" in query:
                result.fetchall.return_value = [
                    ("A股", 32.0), ("美股", 27.0), ("美债", 20.0),
                    ("黄金", 10.0), ("加密货币", 5.0), ("港股", 5.0), ("活期存款", 1.0),
                ]
            elif "ai_insights" in query:
                result.fetchall.return_value = []
            else:
                result.fetchall.return_value = []
                result.fetchone.return_value = None
            return result

        db.execute.side_effect = side_effect
        cb = _make_builder(db_mock=db)

        summary_out = cb.build_identity_context("summary")
        detailed_out = cb.build_identity_context("detailed")
        full_out = cb.build_identity_context("full")

        # Summary: shows goal + risk_tolerance, NOT horizon or core_weakness
        assert "财务独立" in summary_out
        assert "最大30%回撤" in summary_out
        assert "10-20年" not in summary_out
        assert "受市场噪音" not in summary_out
        # Summary truncates to top 4 allocations
        assert "活期存款" not in summary_out  # 7th item, excluded in summary

        # Detailed: shows all 4 philosophy bullets
        assert "10-20年" in detailed_out
        assert "受市场噪音" in detailed_out
        # Detailed shows all allocations
        assert "活期存款" in detailed_out
        # No portfolio structure narrative at detailed level
        assert "权益64%" not in detailed_out

        # Full: adds portfolio structure narrative
        assert "权益64%" in full_out
        assert "活期存款" in full_out

    def test_summary_includes_recent_ai_insights(self, tmp_path):
        """Validated/principle AI insights must be appended in newest-first order."""
        profile_file = tmp_path / "Profile.md"
        profile_file.write_text("Profile text", encoding="utf-8")

        insights_rows = [
            ("Newest principle", "Body 1", "principle", "2026-03-20 12:00:00"),
            ("Middle validated", "Body 2", "validated", "2026-03-19 12:00:00"),
            ("Older principle", "Body 3", "principle", "2026-03-18 12:00:00"),
            ("Oldest validated", "Body 4", "validated", "2026-03-17 12:00:00"),
        ]

        db = MagicMock()

        def side_effect(query, params=()):
            assert "FROM ai_insights" in query
            assert "status IN ('validated', 'principle')" in query
            assert "ORDER BY updated_at DESC" in query
            result = MagicMock()
            result.fetchall.return_value = insights_rows
            return result

        db.execute.side_effect = side_effect

        aia = {"profile_path": str(profile_file)}
        cb = _make_builder(db_mock=db, aia_overrides=aia)
        result = cb.build_identity_context("summary")

        assert "## AI洞见沉淀" in result
        assert "Newest principle" in result
        assert "Middle validated" in result
        assert "Older principle" in result
        assert "Oldest validated" not in result
        assert result.index("Newest principle") < result.index("Middle validated")
        assert result.index("Middle validated") < result.index("Older principle")
        assert db.execute.called

    def test_detailed_includes_more_ai_insights_than_summary(self, tmp_path):
        """Detailed identity context should include more insight rows than summary."""
        profile_file = tmp_path / "Profile.md"
        profile_file.write_text("Profile text", encoding="utf-8")

        insights_rows = [
            ("Insight 1", "Body 1", "principle", "2026-03-20 12:00:00"),
            ("Insight 2", "Body 2", "validated", "2026-03-19 12:00:00"),
            ("Insight 3", "Body 3", "principle", "2026-03-18 12:00:00"),
            ("Insight 4", "Body 4", "validated", "2026-03-17 12:00:00"),
            ("Insight 5", "Body 5", "principle", "2026-03-16 12:00:00"),
        ]

        db = MagicMock()

        def side_effect(query, params=()):
            result = MagicMock()
            result.fetchall.return_value = insights_rows
            return result

        db.execute.side_effect = side_effect

        aia = {"profile_path": str(profile_file)}
        cb = _make_builder(db_mock=db, aia_overrides=aia)
        summary = cb.build_identity_context("summary")
        detailed = cb.build_identity_context("detailed")

        summary_count = sum(title in summary for title, *_ in insights_rows)
        detailed_count = sum(title in detailed for title, *_ in insights_rows)

        assert "## AI洞见沉淀" in detailed
        assert summary_count < detailed_count
        assert summary_count == 3
        assert detailed_count == 5

    def test_missing_profile_does_not_crash(self):
        """Missing profile file should return a string (may be empty or insights only)."""
        aia = {
            "profile_path": "/nonexistent/path/Profile.md",
            "insights_path": "/nonexistent/path/Insight.md",
        }
        cb = _make_builder(aia_overrides=aia)
        result = cb.build_identity_context("summary")
        assert isinstance(result, str)

    def test_all_db_queries_failing_is_non_fatal(self):
        """All DB queries failing should return an empty string (no crash)."""
        db = MagicMock()
        db.execute.side_effect = Exception("table does not exist")

        cb = _make_builder(db_mock=db)
        result = cb.build_identity_context("summary")

        assert isinstance(result, str)
        assert db.execute.called

    def test_risk_profile_allocations_included(self):
        """Target allocations from active risk profile appear in identity context."""
        db = MagicMock()

        def side_effect(query, params=()):
            result = MagicMock()
            if "user_profile" in query:
                result.fetchone.return_value = ("Test User",)
            elif "risk_profiles" in query:
                result.fetchone.return_value = (2, "稳健型", "Balanced", "平衡收益与风险")
            elif "risk_profile_allocations" in query:
                result.fetchall.return_value = [
                    ("A股", 35.0),
                    ("美股", 30.0),
                    ("固收", 20.0),
                    ("另类", 15.0),
                ]
            elif "ai_insights" in query:
                result.fetchall.return_value = []
            else:
                result.fetchall.return_value = []
                result.fetchone.return_value = None
            return result

        db.execute.side_effect = side_effect
        cb = _make_builder(db_mock=db)
        result = cb.build_identity_context("summary")

        assert "## 投资者画像" in result
        assert "稳健型" in result or "Balanced" in result
        assert "35%" in result  # A股 allocation
        assert "30%" in result  # 美股 allocation


class TestBuildMarketContext:
    def test_summary_includes_multiple_market_sections(self):
        """Summary market context should not collapse to one latest crypto row."""
        rows = [
            (
                "equity_macro",
                "CNN Fear & Greed Index",
                "17.2",
                "Extreme Fear",
            ),
            (
                "equity_macro",
                "Brent Crude Oil",
                "$102.76",
                "Danger",
            ),
            (
                "crypto",
                "BTC Dominance",
                "56.4%",
                "BTC Strong",
            ),
        ]

        db = MagicMock()

        def side_effect(query, params=()):
            normalized = " ".join(query.split())
            assert "FROM market_sentiment_cache" in normalized
            result = MagicMock()
            result.fetchall.return_value = rows
            return result

        db.execute.side_effect = side_effect
        cb = _make_builder(db_mock=db)
        result = cb.build_market_context("summary")

        assert "CNN Fear & Greed Index" in result
        assert "Brent Crude Oil" in result
        assert "BTC Dominance" in result
        assert result.count("('") == 0
        assert "Market data unavailable" not in result
        assert "Market data not yet synced." not in result

    def test_detailed_and_full_are_not_identical(self):
        """Detailed and full market context should not render the same text."""
        detailed_rows = [
            (
                "equity_macro",
                "btc_dominance",
                "BTC Dominance",
                "56.4%",
                "BTC Strong",
                "2026-03-20 12:00:00",
            ),
            (
                "equity_macro",
                "vix",
                "VIX",
                "25.1",
                "Elevated",
                "2026-03-20 12:00:00",
            ),
        ]
        full_rows = [
            (
                "btc_dominance",
                "crypto",
                "BTC Dominance",
                56.4,
                "56.4%",
                "BTC Strong",
                "orange",
                "BTC dominance zone: BTC Strong.",
                "{}",
            ),
            (
                "vix",
                "equity_macro",
                "VIX",
                25.1,
                "25.1",
                "Elevated",
                "red",
                "VIX zone: Elevated.",
                "{}",
            ),
        ]

        db = MagicMock()

        def side_effect(query, params=()):
            normalized = " ".join(query.split())
            result = MagicMock()
            if "FROM market_sentiment_cache" in normalized:
                if "SELECT *" in normalized:
                    result.fetchall.return_value = full_rows
                else:
                    result.fetchall.return_value = detailed_rows
                return result
            result.fetchall.return_value = []
            result.fetchone.return_value = None
            return result

        db.execute.side_effect = side_effect
        cb = _make_builder(db_mock=db)

        detailed = cb.build_market_context("detailed")
        full = cb.build_market_context("full")

        assert detailed != full
        assert "BTC Dominance" in detailed or "btc_dominance" in detailed
        assert "BTC Dominance" in full or "btc_dominance" in full


# ---------------------------------------------------------------------------
# Test 3: build_strategy_context returns "No strategy memos found." when empty
# ---------------------------------------------------------------------------

class TestBuildStrategyContext:
    def test_missing_directory_returns_fallback(self):
        """Non-existent strategy directory returns the standard fallback message."""
        aia = {"strategy_path": "/nonexistent/strategy/dir"}
        cb = _make_builder(aia_overrides=aia)
        result = cb.build_strategy_context("summary")
        assert result == "No strategy memos found."

    def test_empty_directory_returns_fallback(self, tmp_path):
        """Empty strategy directory (no .md files) returns fallback message."""
        aia = {"strategy_path": str(tmp_path)}
        cb = _make_builder(aia_overrides=aia)
        result = cb.build_strategy_context("summary")
        assert result == "No strategy memos found."

    def test_with_files_returns_content(self, tmp_path):
        """Strategy directory with .md files returns content."""
        md = tmp_path / "2026-01-strategy.md"
        md.write_text("# 策略\n\n买入A股。", encoding="utf-8")
        aia = {"strategy_path": str(tmp_path)}
        cb = _make_builder(aia_overrides=aia)
        result = cb.build_strategy_context("summary")
        assert "策略" in result or "买入" in result

    def test_no_strategy_path_returns_fallback(self):
        """Missing strategy_path key in config returns fallback."""
        cb = _make_builder(aia_overrides={})
        result = cb.build_strategy_context("summary")
        assert result == "No strategy memos found."


# ---------------------------------------------------------------------------
# Test 4: build_transactions_context returns "No recent trades." when DB empty
# ---------------------------------------------------------------------------

class TestBuildTransactionsContext:
    def test_empty_db_returns_fallback(self):
        """Empty trade_logs query must return the fallback message."""
        db = _make_db_mock(fetchall_return=[])
        cb = _make_builder(db_mock=db)
        result = cb.build_transactions_context("14d")
        assert result == "No recent trades."

    def test_db_exception_returns_fallback(self):
        """DB exception must return the fallback message (never crash)."""
        db = MagicMock()
        db.execute.side_effect = Exception("table not found")
        cb = _make_builder(db_mock=db)
        result = cb.build_transactions_context("14d")
        assert result == "No recent trades."

    def test_summary_uses_display_names_symbols_and_local_currency(self):
        """Transaction summary should show friendly labels and quote-currency prices."""
        rows = [
            ("2026-03-16", "US_STK_MSFT", "Microsoft Corp", "CNY", "Sell", 192, 209.304, "A"),
            ("2026-03-06", "US_STK_AMZN", None, "CNY", "Buy", 31, 100.45, None),
        ]

        db = MagicMock()

        def side_effect(query, params=()):
            normalized = " ".join(query.split())
            result = MagicMock()
            if "FROM trade_logs" in normalized:
                assert "INTERVAL '6' MONTH" in normalized
                result.fetchall.return_value = rows
                return result
            result.fetchall.return_value = []
            result.fetchone.return_value = None
            return result

        db.execute.side_effect = side_effect
        cb = _make_builder(db_mock=db)
        result = cb.build_transactions_context("6m")

        assert "Microsoft Corp (MSFT)" in result
        assert "AMZN" in result
        assert "US_STK_MSFT" not in result
        assert "US_STK_AMZN" not in result
        assert "USD" in result
        assert "209.304" in result or "209.30" in result

    def test_transactions_support_one_year_timeframe(self):
        """Transaction context must support a 1y timeframe query."""
        rows = [("2025-03-16", "US_STK_MSFT", "Microsoft Corp", "USD", "Sell", 192, 209.304, "A")]

        db = MagicMock()

        def side_effect(query, params=()):
            normalized = " ".join(query.split())
            result = MagicMock()
            if "FROM trade_logs" in normalized:
                assert "INTERVAL '1' YEAR" in normalized
                result.fetchall.return_value = rows
                return result
            result.fetchall.return_value = []
            result.fetchone.return_value = None
            return result

        db.execute.side_effect = side_effect
        cb = _make_builder(db_mock=db)
        result = cb.build_transactions_context("1y")

        assert "Microsoft Corp" in result
        assert "No recent trades." not in result

    @pytest.mark.parametrize("timeframe, interval_sql", [
        ("6m", "INTERVAL '6' MONTH"),
        ("1y", "INTERVAL '1' YEAR"),
        ("all", None),
    ])
    def test_long_timeframes_skip_bad_rows_instead_of_falling_back(self, timeframe, interval_sql):
        """A single bad long-window row should not collapse the entire transactions block."""
        db = MagicMock()

        def side_effect(query, params=()):
            normalized = " ".join(query.split())
            result = MagicMock()
            if "FROM trade_logs" in normalized:
                if interval_sql is None:
                    assert "WHERE tl.log_date >=" not in normalized
                else:
                    assert interval_sql in normalized
                result.fetchall.return_value = [
                    ("2026-03-16", "US_STK_AMZN", None, "CNY", "Sell", 192, 209.304, "A"),
                    ("2026-01-21", "CN_FUND_900011", None, "CNY", "Buy", 22781.49, None, "A"),
                    ("2026-01-20", "US_STK_MSFT", "Microsoft Corp", "CNY", "Buy", 4, 384.09, "A"),
                ]
                return result
            result.fetchall.return_value = []
            result.fetchone.return_value = None
            return result

        db.execute.side_effect = side_effect
        cb = _make_builder(db_mock=db)
        result = cb.build_transactions_context(timeframe, "summary")

        assert "No recent trades." not in result
        assert "AMZN" in result
        assert "Microsoft Corp" in result

    def test_detail_levels_are_materially_different(self):
        """Transactions summary/detailed/full should not collapse to the same text."""
        db = MagicMock()

        def side_effect(query, params=()):
            normalized = " ".join(query.split())
            result = MagicMock()
            if "FROM trade_logs" in normalized:
                if "decision_reason" in normalized:
                    result.fetchall.return_value = [
                        (
                            "2026-03-16",
                            "US_STK_MSFT",
                            "Microsoft Corp",
                            "USD",
                            "Sell",
                            192,
                            209.304,
                            40282.99,
                            "A",
                            "Reason",
                            "AI suggestion",
                            "LLM",
                            "verified",
                        )
                    ]
                elif "amount" in normalized:
                    result.fetchall.return_value = [
                        (
                            "2026-03-16",
                            "US_STK_MSFT",
                            "Microsoft Corp",
                            "USD",
                            "Sell",
                            192,
                            209.304,
                            40282.99,
                            "A",
                        )
                    ]
                else:
                    result.fetchall.return_value = [
                        ("2026-03-16", "US_STK_MSFT", "Microsoft Corp", "USD", "Sell", 192, 209.304, "A"),
                    ]
                return result
            result.fetchall.return_value = []
            result.fetchone.return_value = None
            return result

        db.execute.side_effect = side_effect
        cb = _make_builder(db_mock=db)
        from src.services.ai_advisor.context_builder import render_context

        summary = render_context(cb, {
            "tiers": {
                "transactions": {"enabled": True, "detail": "summary", "timeframe": "30d"},
            },
            "include_realtime": False,
            "include_non_rebalanceable": False,
        })
        detailed = render_context(cb, {
            "tiers": {
                "transactions": {"enabled": True, "detail": "detailed", "timeframe": "30d"},
            },
            "include_realtime": False,
            "include_non_rebalanceable": False,
        })
        full = render_context(cb, {
            "tiers": {
                "transactions": {"enabled": True, "detail": "full", "timeframe": "30d"},
            },
            "include_realtime": False,
            "include_non_rebalanceable": False,
        })

        assert summary != detailed
        assert detailed != full
        assert summary != full

    def test_uses_real_trade_log_columns(self):
        """Recent trades context must read log_date and price from trade_logs."""
        rows = [
            ("2026-03-16", "US_STK_AMZN", "Amazon.com", "CNY", "Sell", 192, 209.304, "A"),
            ("2026-03-06", "US_STK_SGOV", "SGOV", "CNY", "Buy", 31, 100.45, None),
        ]

        db = MagicMock()

        def side_effect(query, params=()):
            normalized = " ".join(query.split())
            if "FROM trade_logs" in normalized:
                assert "log_date" in normalized
                assert "price_cny" not in normalized
                result = MagicMock()
                result.fetchall.return_value = rows
                return result

            result = MagicMock()
            result.fetchall.return_value = []
            result.fetchone.return_value = None
            return result

        db.execute.side_effect = side_effect
        cb = _make_builder(db_mock=db)
        result = cb.build_transactions_context("30d")

        assert "Amazon.com (AMZN)" in result
        assert "AMZN" in result
        assert "SGOV" in result
        assert "US_STK_AMZN" not in result
        assert "USD" in result
        assert "No recent trades." not in result


# ---------------------------------------------------------------------------
# Test 5: estimate_tokens returns dict with "total" key > 0 when tiers enabled
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_total_key_present_and_positive(self):
        """estimate_tokens must return a dict with 'total' > 0 when tiers are enabled."""
        cb = _make_builder()
        config = {
            "identity":     {"enabled": True,  "detail": "summary"},
            "portfolio":    {"enabled": True,  "detail": "detailed"},
            "market":       {"enabled": False, "detail": "summary"},
            "strategy":     {"enabled": True,  "detail": "summary"},
            "transactions": {"enabled": True,  "detail": "14d"},
        }
        result = cb.estimate_tokens(config)

        assert isinstance(result, dict)
        assert "total" in result
        assert result["total"] > 0

    def test_disabled_tiers_contribute_zero(self):
        """Disabled tiers must contribute 0 to the total."""
        cb = _make_builder()
        config = {
            "identity":     {"enabled": False, "detail": "summary"},
            "portfolio":    {"enabled": False, "detail": "summary"},
            "market":       {"enabled": False, "detail": "summary"},
            "strategy":     {"enabled": False, "detail": "summary"},
            "transactions": {"enabled": False, "detail": "14d"},
        }
        result = cb.estimate_tokens(config)
        assert result["total"] == 0
        for tier in ["identity", "portfolio", "market", "strategy", "transactions"]:
            assert result[tier]["estimated_tokens"] == 0

    def test_all_tiers_present_in_result(self):
        """Result must include all five tier keys."""
        cb = _make_builder()
        result = cb.estimate_tokens({})
        for tier in ["identity", "portfolio", "market", "strategy", "transactions"]:
            assert tier in result


# ---------------------------------------------------------------------------
# Test 6: _build_investor_profile_section — tier variation with rich philosophy
# ---------------------------------------------------------------------------

class TestBuildInvestorProfileSection:
    """_build_investor_profile_section must produce increasing depth across tiers."""

    def _make_builder_with_philosophy(self, philosophy: dict, alloc_rows=None):
        """Return a builder whose DB mock returns a populated philosophy."""
        import json as _json

        # Mock call sequence:
        #   1st execute → fetchone: (display_name, philosophy_json)  [user_profile query]
        #   2nd execute → fetchone: (id, name, name_en, description)  [risk_profiles query]
        #   3rd execute → fetchall: alloc_rows  [risk_profile_allocations query]
        philosophy_json = _json.dumps(philosophy, ensure_ascii=False)

        db = MagicMock()

        profile_result = MagicMock()
        profile_result.fetchone.return_value = ("Ray", philosophy_json)

        risk_result = MagicMock()
        risk_result.fetchone.return_value = (1, "成长型", "Growth", "High equity")

        alloc_result = MagicMock()
        alloc_result.fetchall.return_value = alloc_rows or [
            ("美股", 30.0),
            ("A股", 30.0),
            ("固收", 20.0),
            ("黄金", 10.0),
            ("港股", 5.0),
            ("现金", 5.0),
        ]

        db.execute.side_effect = [profile_result, risk_result, alloc_result]
        return _make_builder(db_mock=db)

    def test_summary_contains_goal_and_risk_tolerance(self):
        """Summary must include goal and risk_tolerance."""
        phil = {
            "goal": "财务独立2000万",
            "horizon": "10-20年",
            "risk_tolerance": "最大回撤30%",
            "core_weakness": "追涨杀跌",
            "portfolio_structure": "权益60-70%核心配置详情...",
        }
        cb = self._make_builder_with_philosophy(phil)
        result = cb._build_investor_profile_section(detail="summary")
        assert "财务独立2000万" in result
        assert "最大回撤30%" in result

    def test_summary_excludes_horizon_and_core_weakness(self):
        """Summary must NOT include horizon or core_weakness."""
        phil = {
            "goal": "财务独立2000万",
            "horizon": "10-20年",
            "risk_tolerance": "最大回撤30%",
            "core_weakness": "追涨杀跌",
            "portfolio_structure": "权益60-70%...",
        }
        cb = self._make_builder_with_philosophy(phil)
        result = cb._build_investor_profile_section(detail="summary")
        assert "10-20年" not in result
        assert "追涨杀跌" not in result

    def test_summary_limits_allocations_to_four(self):
        """Summary must show at most 4 allocations."""
        phil = {"goal": "独立", "risk_tolerance": "中低"}
        cb = self._make_builder_with_philosophy(phil)  # 6 alloc rows configured
        result = cb._build_investor_profile_section(detail="summary")
        # Only top 4 should appear; 港股 and 现金 are in positions 5-6
        assert "港股" not in result
        assert "现金" not in result

    def test_detailed_includes_all_philosophy_bullets(self):
        """Detailed must include goal, horizon, risk_tolerance, and core_weakness."""
        phil = {
            "goal": "财务独立2000万",
            "horizon": "10-20年",
            "risk_tolerance": "最大回撤30%",
            "core_weakness": "追涨杀跌",
            "portfolio_structure": "权益60-70%...",
        }
        db = MagicMock()
        profile_result = MagicMock()
        import json as _json
        profile_result.fetchone.return_value = ("Ray", _json.dumps(phil))
        risk_result = MagicMock()
        risk_result.fetchone.return_value = (1, "成长型", "Growth", "")
        alloc_result = MagicMock()
        alloc_result.fetchall.return_value = [("美股", 30.0), ("A股", 30.0)]
        db.execute.side_effect = [profile_result, risk_result, alloc_result]
        cb = _make_builder(db_mock=db)
        result = cb._build_investor_profile_section(detail="detailed")
        assert "财务独立2000万" in result
        assert "10-20年" in result
        assert "最大回撤30%" in result
        assert "追涨杀跌" in result

    def test_detailed_shows_all_allocations(self):
        """Detailed must show ALL allocations (not capped at 4)."""
        phil = {"goal": "独立", "risk_tolerance": "中低"}
        cb = self._make_builder_with_philosophy(phil)  # 6 alloc rows
        result = cb._build_investor_profile_section(detail="detailed")
        assert "港股" in result
        assert "现金" in result

    def test_full_includes_portfolio_structure_verbatim(self):
        """Full must append portfolio_structure narrative verbatim."""
        long_narrative = "权益60-70%[A股30-35% Tier3, 美股25-30% Tier1, 港股5%动态阀门], 固收20%, 另类10-15%, 现金5%"
        phil = {
            "goal": "财务独立2000万",
            "risk_tolerance": "最大回撤30%",
            "portfolio_structure": long_narrative,
        }
        db = MagicMock()
        profile_result = MagicMock()
        import json as _json
        profile_result.fetchone.return_value = ("Ray", _json.dumps(phil))
        risk_result = MagicMock()
        risk_result.fetchone.return_value = (1, "成长型", "Growth", "")
        alloc_result = MagicMock()
        alloc_result.fetchall.return_value = [("美股", 30.0)]
        db.execute.side_effect = [profile_result, risk_result, alloc_result]
        cb = _make_builder(db_mock=db)
        result = cb._build_investor_profile_section(detail="full")
        assert long_narrative in result

    def test_full_is_strictly_longer_than_detailed(self):
        """Full output must be strictly longer than detailed when portfolio_structure is set."""
        phil = {
            "goal": "财务独立2000万",
            "horizon": "15年",
            "risk_tolerance": "最大回撤30%",
            "core_weakness": "追涨杀跌",
            "portfolio_structure": "权益60-70%核心配置详情",
        }
        import json as _json

        def _make():
            db = MagicMock()
            profile_result = MagicMock()
            profile_result.fetchone.return_value = ("Ray", _json.dumps(phil))
            risk_result = MagicMock()
            risk_result.fetchone.return_value = (1, "成长型", "Growth", "")
            alloc_result = MagicMock()
            alloc_result.fetchall.return_value = [("美股", 30.0), ("A股", 30.0)]
            db.execute.side_effect = [profile_result, risk_result, alloc_result]
            return _make_builder(db_mock=db)

        result_detailed = _make()._build_investor_profile_section(detail="detailed")
        result_full = _make()._build_investor_profile_section(detail="full")
        assert len(result_full) > len(result_detailed)

    def test_summary_shorter_than_detailed(self):
        """Summary output must be strictly shorter than detailed when all fields set."""
        phil = {
            "goal": "财务独立2000万",
            "horizon": "15年",
            "risk_tolerance": "最大回撤30%",
            "core_weakness": "追涨杀跌",
            "portfolio_structure": "权益60-70%",
        }
        import json as _json

        def _make():
            db = MagicMock()
            profile_result = MagicMock()
            profile_result.fetchone.return_value = ("Ray", _json.dumps(phil))
            risk_result = MagicMock()
            risk_result.fetchone.return_value = (1, "成长型", "Growth", "")
            alloc_result = MagicMock()
            alloc_result.fetchall.return_value = [("美股", 30.0), ("A股", 30.0), ("固收", 20.0), ("黄金", 10.0), ("港股", 5.0), ("现金", 5.0)]
            db.execute.side_effect = [profile_result, risk_result, alloc_result]
            return _make_builder(db_mock=db)

        result_summary = _make()._build_investor_profile_section(detail="summary")
        result_detailed = _make()._build_investor_profile_section(detail="detailed")
        assert len(result_summary) < len(result_detailed)

    def test_missing_philosophy_does_not_crash(self):
        """A user_profile row with null philosophy must not crash."""
        db = MagicMock()
        profile_result = MagicMock()
        profile_result.fetchone.return_value = ("Ray", None)
        risk_result = MagicMock()
        risk_result.fetchone.return_value = None
        db.execute.side_effect = [profile_result, risk_result]
        cb = _make_builder(db_mock=db)
        # Must not raise; may return empty string or just the header
        result = cb._build_investor_profile_section(detail="full")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Test 7: settings_manager philosophy persistence (direct, no FastAPI import)
# ---------------------------------------------------------------------------

class TestSettingsManagerPhilosophy:
    """Direct unit tests for get_profile/save_profile philosophy round-trip."""

    def test_save_and_get_philosophy_round_trip(self, tmp_path):
        """save_profile persists philosophy; get_profile reads it back."""
        import duckdb as _duckdb
        from unittest.mock import patch

        db_path = str(tmp_path / "test_profile.duckdb")
        with _duckdb.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE user_profile (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    display_name VARCHAR,
                    avatar_base64 TEXT,
                    philosophy TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

        # Patch DatabaseConnector to use our test DB
        class _FakeDB:
            def __init__(self):
                self._conn = _duckdb.connect(db_path)
            def execute(self, sql, params=None):
                if params:
                    return self._conn.execute(sql, params)
                return self._conn.execute(sql)
            def close(self):
                self._conn.close()

        from src.services import settings_manager as sm

        philosophy = {
            "goal": "财务独立2000万",
            "horizon": "10-20年",
            "risk_tolerance": "最大回撤30%",
            "core_weakness": "追涨杀跌",
            "portfolio_structure": "权益60-70%，固收20%，另类10-15%，现金5%",
        }

        with patch("src.database.connector.DatabaseConnector", _FakeDB):
            sm.save_profile("Ray", None, philosophy=philosophy)
            result = sm.get_profile()

        assert result["philosophy"]["goal"] == "财务独立2000万"
        assert result["philosophy"]["horizon"] == "10-20年"
        assert result["philosophy"]["portfolio_structure"] == "权益60-70%，固收20%，另类10-15%，现金5%"
        assert result["display_name"] == "Ray"

    def test_save_philosophy_none_preserves_existing(self, tmp_path):
        """save_profile(philosophy=None) must not overwrite existing philosophy."""
        import duckdb as _duckdb
        from unittest.mock import patch

        db_path = str(tmp_path / "test_profile2.duckdb")
        with _duckdb.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE user_profile (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    display_name VARCHAR,
                    avatar_base64 TEXT,
                    philosophy TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

        class _FakeDB:
            def __init__(self):
                self._conn = _duckdb.connect(db_path)
            def execute(self, sql, params=None):
                if params:
                    return self._conn.execute(sql, params)
                return self._conn.execute(sql)
            def close(self):
                self._conn.close()

        from src.services import settings_manager as sm

        with patch("src.database.connector.DatabaseConnector", _FakeDB):
            sm.save_profile("Ray", None, philosophy={"goal": "独立"})
            sm.save_profile("Ray Updated", None, philosophy=None)
            result = sm.get_profile()

        # display_name updated; philosophy must NOT be overwritten
        assert result["display_name"] == "Ray Updated"
        assert result["philosophy"]["goal"] == "独立"

    def test_partial_philosophy_merge(self, tmp_path):
        """Partial philosophy update must merge, not replace."""
        import duckdb as _duckdb
        from unittest.mock import patch

        db_path = str(tmp_path / "test_profile3.duckdb")
        with _duckdb.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE user_profile (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    display_name VARCHAR,
                    avatar_base64 TEXT,
                    philosophy TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

        class _FakeDB:
            def __init__(self):
                self._conn = _duckdb.connect(db_path)
            def execute(self, sql, params=None):
                if params:
                    return self._conn.execute(sql, params)
                return self._conn.execute(sql)
            def close(self):
                self._conn.close()

        from src.services import settings_manager as sm

        with patch("src.database.connector.DatabaseConnector", _FakeDB):
            sm.save_profile("Ray", None, philosophy={"goal": "独立", "horizon": "15年"})
            # Note: the route layer does the merging; here we test save with a full merged dict
            sm.save_profile("Ray", None, philosophy={"goal": "独立", "horizon": "15年", "risk_tolerance": "中低"})
            result = sm.get_profile()

        assert result["philosophy"]["goal"] == "独立"
        assert result["philosophy"]["horizon"] == "15年"
        assert result["philosophy"]["risk_tolerance"] == "中低"
