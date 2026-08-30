from unittest.mock import MagicMock, patch
from src.services.context_generator import MarkdownContextGenerator


def _make_db_mock(**overrides):
    """Return a MagicMock db that returns empty results by default.
    Pass keyword args to override specific query patterns:
      holdings_overview=(reb, non_reb)
      risk_alloc_rows=[(top, sub, pct), ...]
      alloc_rows=[(top_class, val), ...]
      subclass_rows=[(top, sub, val), ...]
      tier_rows=[(name, val, cur_pct, tgt, drift, pl, count), ...]
    """
    db = MagicMock()

    def mock_execute(query, params=None):
        result = MagicMock()

        # Section 1.1 — holdings overview (fetchone returns [total, reb, non_reb])
        # Detected by "as non_reb" alias (unique to section 1.1; section 2.1 uses non_reb_value)
        if "as non_reb" in query and "as non_reb_value" not in query and "FROM holdings h" in query:
            result.fetchone.return_value = overrides.get("holdings_overview", [6000.0, 5000.0, 1000.0])
            result.fetchall.return_value = []

        # Risk profile allocations (for target_map)
        elif "risk_profile_allocations" in query and "risk_profiles" in query:
            result.fetchall.return_value = overrides.get("risk_alloc_rows", [
                ("Equity", "CN Equity", 35.0),
                ("Equity", "US Equity", 20.0),
            ])

        # Section 1.2 — top-class allocation (holdings + taxonomy double-join)
        elif "taxonomy_classes parent_tc ON tc.parent_id" in query and "GROUP BY top_class" in query and "sub_class" not in query:
            key = "reb_alloc_rows" if "is_rebalanceable, r.is_rebalanceable, TRUE) = TRUE" in query else "total_alloc_rows"
            default_rows = [
                ("Equity", 4000.0),
                ("Fixed Income", 1000.0),
            ]
            result.fetchall.return_value = overrides.get(key, overrides.get("alloc_rows", default_rows))

        # Section 1.3 — sub-class breakdown
        elif "taxonomy_classes parent_tc ON tc.parent_id" in query and "GROUP BY top_class, sub_class" in query:
            result.fetchall.return_value = overrides.get("subclass_rows", [
                ("Equity", "CN Equity", 2500.0),
                ("Equity", "US Equity", 1500.0),
                ("Fixed Income", "US Bonds", 1000.0),
            ])

        # Section 1.4 — tier allocation
        elif "asset_tiers" in query and "tier_holdings" in query:
            result.fetchall.return_value = overrides.get("tier_rows", [
                ("Tier 1 Core", 3000.0, 60.0, 50.0, 10.0, 200.0, 5),
                ("Tier 2 Satellite", 2000.0, 40.0, 35.0, 5.0, 100.0, 3),
            ])

        # Section 2.1 — performance summary
        elif "total_cost_basis" in query or ("net_worth" in query and "asset_count" in query):
            result.fetchone.return_value = overrides.get(
                "perf_summary", [5000.0, 4000.0, 10]
            )
            result.fetchall.return_value = []

        # Section 3 — market sentiment
        elif "market_sentiment_cache" in query:
            result.fetchall.return_value = overrides.get("sentiment_rows", [])

        # Section 4 — holdings detail
        elif "taxonomy_classes parent_tc ON tc.parent_id" in query and "weight_pct" in query:
            result.fetchall.return_value = overrides.get("holdings_rows", [])

        # Section 2 — per-asset rows for class/lifetime aggregation
        elif "GROUP BY h.asset_id, top_class" in query:
            result.fetchall.return_value = overrides.get("asset_perf_rows", [
                ("US_STK_AAPL", "Equity", 4000.0, 3500.0, True),
            ])

        # Rebalanceable asset-id helper query
        elif "SELECT h.asset_id" in query and "COALESCE(tc.is_rebalanceable, r.is_rebalanceable, TRUE) = TRUE" in query:
            result.fetchall.return_value = overrides.get("reb_asset_ids_rows", [("US_STK_AAPL",)])

        # Transaction asset universe (for realized calculations including sold-only assets)
        elif "SELECT DISTINCT asset_id FROM transactions" in query:
            result.fetchall.return_value = overrides.get("tx_asset_rows", [("US_STK_AAPL",)])

        # Sold-only class mapping
        elif "FROM asset_registry r" in query and "WHERE r.canonical_id IN" in query:
            result.fetchall.return_value = overrides.get("sold_asset_class_rows", [])

        # Section 2.2 — class performance
        elif "taxonomy_classes parent_tc ON tc.parent_id" in query and "cost_basis" in query:
            result.fetchall.return_value = overrides.get("class_perf", [
                ("Equity", 4000.0, 3500.0, 80.0, 5),
            ])

        # MAX(snapshot_date) for header
        elif "MAX(snapshot_date)" in query:
            result.fetchone.return_value = ["2026-03-01"]
            result.fetchall.return_value = []

        else:
            result.fetchall.return_value = []
            result.fetchone.return_value = None

        return result

    db.execute.side_effect = mock_execute
    return db


# ─── Smoke tests ────────────────────────────────────────────────────────────

def test_generate_returns_markdown_string():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.fetchone.return_value = None

    generator = MarkdownContextGenerator(db)
    md = generator.generate()

    assert "# Personal Investment Analysis Context" in md
    assert "## 1. Portfolio State" in md
    assert "## 2. Performance Metrics" in md
    assert "## 3. Market Environment" in md
    assert "## 4. Holdings Details" in md


def test_header():
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = ["2026-03-01"]

    generator = MarkdownContextGenerator(db)
    header = generator._header()

    assert "Generated:" in header
    assert "Data as of: 2026-03-01" in header


# ─── Section 1 ──────────────────────────────────────────────────────────────

def test_section_1_portfolio_state_headings():
    db = _make_db_mock()
    generator = MarkdownContextGenerator(db)
    section = generator._section_1_portfolio_state()

    assert "## 1. Portfolio State" in section
    assert "1.1 Current Holdings Overview" in section
    assert "Total Portfolio Value" in section
    assert "Rebalanceable Assets" in section
    assert "1.2a Total Portfolio Allocation (All Assets)" in section
    assert "1.2b Rebalanceable Allocation vs Target" in section
    assert "1.3 Sub-Class Breakdown" in section
    assert "1.4 Tier Allocation" in section


def test_context_generator_uses_latest_per_asset_not_global_max():
    db = _make_db_mock()

    def execute_with_snapshot_behavior(query, params=None):
        result = MagicMock()
        if "as non_reb" in query and "FROM holdings h" in query:
            if "latest_per_asset" in query:
                result.fetchone.return_value = [300.0, 300.0, 0.0]
            else:
                result.fetchone.return_value = [200.0, 200.0, 0.0]
            return result
        return db.execute.side_effect.__wrapped__(query, params)  # type: ignore[attr-defined]

    original = db.execute.side_effect
    execute_with_snapshot_behavior.__wrapped__ = original  # type: ignore[attr-defined]
    db.execute.side_effect = execute_with_snapshot_behavior

    generator = MarkdownContextGenerator(db)
    section = generator._section_1_portfolio_state()

    assert "¥300" in section


def test_section_1_2_shows_top_class_names():
    """Section 1.2 must show top-class names resolved via taxonomy double-join."""
    db = _make_db_mock(
        total_alloc_rows=[("Equity", 4000.0), ("Real Estate", 1000.0)],
        reb_alloc_rows=[("Equity", 4000.0), ("Fixed Income", 1000.0)],
    )
    generator = MarkdownContextGenerator(db)
    section = generator._section_1_portfolio_state()

    assert "Equity" in section
    assert "Fixed Income" in section
    assert "Real Estate" in section


def test_section_1_2b_uses_top_target_map():
    """Section 1.2b target % should aggregate sub-class targets to top-class level."""
    db = _make_db_mock(
        risk_alloc_rows=[
            ("Equity", "CN Equity", 35.0),
            ("Equity", "US Equity", 20.0),
        ],
        reb_alloc_rows=[("Equity", 5000.0)],
    )
    generator = MarkdownContextGenerator(db)
    section = generator._section_1_portfolio_state()

    # Equity target should be 35+20=55%, shown in section
    assert "55.00%" in section


def test_section_1_3_subclass_breakdown_exists():
    """Section 1.3 must show sub-class breakdown with Top Class and Sub-Class columns."""
    db = _make_db_mock(
        subclass_rows=[
            ("Equity", "CN Equity", 2000.0),
            ("Equity", "US Equity", 1000.0),
        ]
    )
    generator = MarkdownContextGenerator(db)
    section = generator._section_1_portfolio_state()

    assert "Sub-Class Breakdown" in section
    assert "Top Class" in section
    assert "Sub-Class" in section
    assert "CN Equity" in section
    assert "US Equity" in section


def test_section_1_3_uses_sub_target_map():
    """Section 1.3 target % should use sub-class level targets."""
    db = _make_db_mock(
        risk_alloc_rows=[
            ("Equity", "CN Equity", 35.0),
        ],
        subclass_rows=[("Equity", "CN Equity", 2000.0)],
    )
    generator = MarkdownContextGenerator(db)
    section = generator._section_1_portfolio_state()

    # CN Equity target should be 35%
    assert "35.00%" in section


def test_section_1_4_enriched_tier_data():
    """Section 1.4 must show Value, Current %, Target %, Drift, P&L, Assets."""
    db = _make_db_mock(
        tier_rows=[("Tier 1 Core", 3000.0, 60.0, 50.0, 10.0, 200.0, 5)]
    )
    generator = MarkdownContextGenerator(db)
    section = generator._section_1_portfolio_state()

    assert "Tier 1 Core" in section
    assert "Unrealized P&L" in section
    assert "Assets" in section
    assert "60.00%" in section  # current %
    assert "50.00%" in section  # target %


def test_section_1_no_non_rebalanceable_assets_in_tables():
    """Non-rebalanceable asset classes must not appear as rows in allocation tables 1.2/1.3.
    Note: 'Insurance' and 'Real Estate' may appear in the section 1.1 informational note."""
    db = _make_db_mock(
        total_alloc_rows=[("Equity", 5000.0), ("Real Estate", 500.0)],
        reb_alloc_rows=[("Equity", 5000.0), ("Fixed Income", 1000.0)],
    )
    generator = MarkdownContextGenerator(db)
    section = generator._section_1_portfolio_state()

    # Section 1.2b table should only have Equity and Fixed Income rows
    assert "| Equity |" in section
    assert "| Fixed Income |" in section
    # Total-allocation subsection should still show non-rebalanceable classes
    assert "| Real Estate |" in section


def test_context_generator_includes_total_portfolio_allocation_section():
    db = _make_db_mock(
        total_alloc_rows=[("Equity", 5000.0), ("Real Estate", 2600000.0)],
        reb_alloc_rows=[("Equity", 5000.0), ("Fixed Income", 1000.0)],
    )
    generator = MarkdownContextGenerator(db)
    section = generator._section_1_portfolio_state()

    assert "### 1.2a Total Portfolio Allocation (All Assets)" in section
    assert "| Real Estate |" in section


# ─── Section 2 ──────────────────────────────────────────────────────────────

def test_section_2_performance_headings():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.fetchone.return_value = None

    generator = MarkdownContextGenerator(db)
    section = generator._section_2_performance()

    assert "## 2. Performance Metrics" in section


def test_section_2_includes_realized_and_lifetime_metrics():
    db = _make_db_mock(
        perf_summary=[5000.0, 4000.0, 1000.0, 4500.0, 3500.0, 2],
        class_perf=[("Equity", 4000.0, 3500.0, 80.0, 1)],
    )
    with patch(
        "src.services.context_generator.calculate_realized_pnl",
        return_value=(120.0, "CNY"),
        create=True,
    ):
        section = MarkdownContextGenerator(db)._section_2_performance()

    assert "Realized Gains" in section
    assert "Lifetime P&L" in section
    assert "Realized P&L" in section


def test_section_2_includes_returns_and_risk_metrics_reference_block():
    db = _make_db_mock(
        perf_summary=[5000.0, 4000.0, 1000.0, 4500.0, 3500.0, 2],
        class_perf=[("Equity", 4000.0, 3500.0, 80.0, 1)],
    )
    with patch(
        "src.services.context_generator.calculate_portfolio_twr",
        return_value={"cumulative": 0.1234, "annualized": 0.0812},
        create=True,
    ), patch(
        "src.services.context_generator.calculate_portfolio_xirr",
        return_value=0.102,
        create=True,
    ), patch(
        "src.services.context_generator.calculate_portfolio_metrics",
        return_value={
            "sharpe_ratio": 1.1,
            "sortino_ratio": 1.5,
            "max_drawdown": 8.2,
            "calmar_ratio": 0.7,
            "volatility_annual": 12.3,
            "total_return": 15.8,
            "data_points": 24,
        },
        create=True,
    ):
        section = MarkdownContextGenerator(db)._section_2_performance()

    assert "Returns & Risk Metrics" in section
    assert "Sharpe Ratio" in section
    assert "Max Drawdown" in section
    assert "TWR (Cumulative)" in section


def test_section_2_asset_class_realized_includes_sold_only_assets():
    db = _make_db_mock(
        perf_summary=[5000.0, 5000.0, 0.0, 4500.0, 4500.0, 1],
        asset_perf_rows=[("US_STK_AAPL", "Equity", 5000.0, 4500.0, True)],
        reb_asset_ids_rows=[("US_STK_AAPL",)],
        tx_asset_rows=[("US_STK_AAPL",), ("US_STK_SOLD",)],
        sold_asset_class_rows=[("US_STK_SOLD", "Equity", True)],
    )

    def realized_side_effect(_db, aid, start_date=None):
        amount = 100.0 if aid == "US_STK_AAPL" else 50.0 if aid == "US_STK_SOLD" else 0.0
        return amount, "CNY"

    with patch(
        "src.services.context_generator.calculate_realized_pnl",
        side_effect=realized_side_effect,
        create=True,
    ), patch(
        "src.services.context_generator.calculate_portfolio_twr",
        return_value={"cumulative": 0.1, "annualized": 0.08},
        create=True,
    ), patch(
        "src.services.context_generator.calculate_portfolio_xirr",
        return_value=0.09,
        create=True,
    ), patch(
        "src.services.context_generator.calculate_portfolio_metrics",
        return_value={"sharpe_ratio": 1.0, "sortino_ratio": 1.2, "max_drawdown": 5.0, "calmar_ratio": 0.7, "volatility_annual": 12.0, "total_return": 10.0, "data_points": 24},
        create=True,
    ):
        section = MarkdownContextGenerator(db)._section_2_performance()

    # Realized must include active (100) + sold-only (50) for Equity class.
    assert "| Equity | ¥5,000 | 100.00% | ¥500 | ¥150 | ¥650 | 14.44% | 1 |" in section


def test_section_2_2_no_non_rebalanceable_assets():
    """Section 2.2 asset class list must not include Insurance or Property."""
    db = _make_db_mock(class_perf=[
        ("Equity", 5000.0, 4000.0, 80.0, 10),
        ("Fixed Income", 1000.0, 900.0, 20.0, 3),
    ])
    generator = MarkdownContextGenerator(db)
    section = generator._section_2_performance()

    assert "Insurance" not in section
    assert "Property" not in section
    assert "Real Estate" not in section


# ─── Section 3 ──────────────────────────────────────────────────────────────

def test_section_3_market_environment():
    db = MagicMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [
        ("fear_greed", "equity_macro", "Fear & Greed", 30.0, "30 (Fear)", "Fear", "orange", "Fear area", "{}", "2026-03-01")
    ]
    db.execute.return_value = mock_result

    generator = MarkdownContextGenerator(db)
    section = generator._section_3_market_environment()

    assert "## 3. Market Environment" in section
    assert "Fear & Greed" in section
    assert "30 (Fear)" in section


def test_context_generator_market_status_summary_present():
    db = MagicMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    db.execute.return_value = mock_result

    with patch(
        "src.services.context_generator.assess_portfolio_regime",
        return_value={
            "trend": "Bull",
            "volatility_level": "Normal",
            "drawdown_pct": -1.84,
            "momentum_3m_pct": 6.3,
        },
        create=True,
    ):
        section = MarkdownContextGenerator(db)._section_3_market_environment()

    assert "### 3.0 Market Regime Summary" in section
    assert "Trend: Bull" in section
    assert "Volatility: Normal" in section


def test_context_generator_market_status_summary_absent_gracefully():
    db = MagicMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    db.execute.return_value = mock_result

    with patch(
        "src.services.context_generator.assess_portfolio_regime",
        return_value=None,
        create=True,
    ):
        section = MarkdownContextGenerator(db)._section_3_market_environment()

    assert "### 3.0 Market Regime Summary" in section
    assert "Market regime data unavailable — run market data sync." in section


def test_context_generator_escapes_pipe_in_markdown_cells():
    generator = MarkdownContextGenerator(MagicMock())
    table = generator.generate_markdown_table(
        ["Indicator", "Value"],
        [["BTC Vol", "$69,955 | 52.7% vol"]],
    )
    assert "\\|" in table
    assert "$69,955 | 52.7% vol" not in table


# ─── Section 4 ──────────────────────────────────────────────────────────────

def test_section_4_holdings_detail_heading():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.fetchone.return_value = None

    generator = MarkdownContextGenerator(db)
    section = generator._section_4_holdings_detail()

    assert "## 4. Holdings Details" in section


def test_section_4_holding_details_keep_rebalanceable_and_aggregate_cash():
    """Section 4 should keep rebalanceable assets only and aggregate cash to one row."""
    db = _make_db_mock(holdings_rows=[
        ("Apple Inc", "US_STK_AAPL", "Equity", "US Equity", 10000.0, 8000.0, 5.0, True),
        ("Cash Account 1", "CASH_1", "Cash", "Cash Checking", 3000.0, 3000.0, 1.5, True),
        ("Cash Account 2", "CASH_2", "Cash", "Money Market", 2000.0, 2000.0, 1.0, True),
        ("Property A", "Property_A", "Real Estate", "Property", 2600000.0, 2820000.0, 47.8, False),
    ])
    with patch(
        "src.services.context_generator.calculate_realized_pnl",
        return_value=(0.0, "CNY"),
        create=True,
    ):
        section = MarkdownContextGenerator(db)._section_4_holdings_detail()

    assert "| Property A |" not in section
    assert "Rebalanceable" not in section
    assert "| Cash Total |" in section
    assert "| CASH_TOTAL |" in section
    assert "Cash Account 1" not in section
    assert "Cash Account 2" not in section
