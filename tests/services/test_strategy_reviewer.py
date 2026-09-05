import pytest

pytestmark = pytest.mark.pipeline

from pathlib import Path

import duckdb
from src.classification.schema import create_classification_tables


def _seed_minimal_registry(conn):
    conn.execute(
        """
        INSERT INTO asset_registry (canonical_id, display_name, asset_class)
        VALUES
          ('US_STK_AAPL', 'Apple', 'US Equity'),
          ('CN_FUND_900002', 'Fund', 'CN Equity')
        """
    )


def _seed_scope_taxonomy(conn):
    conn.execute(
        """
        INSERT INTO taxonomy_classes (id, name, parent_id, level, sort_order)
        VALUES
          (1, 'Equity', NULL, 0, 1),
          (2, 'Fixed Income', NULL, 0, 2),
          (3, 'Cash', NULL, 0, 3),
          (4, 'Commodity', NULL, 0, 4),
          (5, 'Alternative', NULL, 0, 5),
          (6, 'CN Equity', 1, 1, 1),
          (7, 'HK ETF', 1, 1, 2),
          (8, 'US Equity', 1, 1, 3),
          (9, 'US Bonds', 2, 1, 1),
          (10, 'Cash Checking', 3, 1, 1),
          (11, 'Money Market', 3, 1, 2),
          (12, 'Gold', 4, 1, 1),
          (13, 'Crypto', 5, 1, 1)
        """
    )


def _seed_scope_registry(conn):
    conn.execute(
        """
        INSERT INTO asset_registry (canonical_id, display_name, asset_class)
        VALUES
          ('US_STK_AAPL', 'Apple', 'US Equity'),
          ('CN_FUND_900001', 'CN Equity Fund', 'CN Equity'),
          ('CN_FUND_900009', 'HK ETF Fund', 'HK ETF'),
          ('US_STK_IBIT', 'iShares Bitcoin ETF', 'Crypto'),
          ('US_STK_FBTC', 'Fidelity Bitcoin ETF', 'Crypto'),
          ('US_STK_SGOV', 'SGOV', 'US Bonds'),
          ('CASH_USD', 'Cash', 'Cash Checking'),
          ('CN_FUND_900007', 'Money Market', 'Money Market'),
          ('ALTS_Paper_Gold', 'Paper Gold', 'Gold'),
          ('Property_阳光花园', 'Property', 'Property'),
          ('INS_安泰人生', 'Insurance', 'Insurance Products')
        """
    )


def _seed_scope_targets(conn):
    conn.execute("INSERT INTO risk_profiles (id, name, is_active) VALUES (1, '均衡型', TRUE)")
    conn.execute(
        """
        INSERT INTO risk_profile_allocations (id, profile_id, class_id, target_pct)
        VALUES
          (1, 1, 6, 35.0),
          (2, 1, 7, 10.0),
          (3, 1, 8, 20.0),
          (4, 1, 9, 15.0),
          (5, 1, 10, 2.0),
          (6, 1, 11, 3.0),
          (7, 1, 12, 8.0),
          (8, 1, 13, 7.0)
        """
    )
    conn.execute(
        """
        INSERT INTO target_allocations (asset_class, target_pct, tolerance_pct, taxonomy_type, source, effective_date)
        VALUES
          ('CN Equity', 35, 5, 'Asset Class', 'Strategic_Profile', '2026-03-01'),
          ('HK Equity', 5, 5, 'Asset Class', 'Strategic_Profile', '2026-03-01'),
          ('US Equity', 30, 5, 'Asset Class', 'Strategic_Profile', '2026-03-01'),
          ('固定收益', 20, 5, 'Asset Class', 'Strategic_Profile', '2026-03-01'),
          ('现金', 5, 5, 'Asset Class', 'Strategic_Profile', '2026-03-01')
        """
    )


def test_review_allocation_alignment_uses_per_asset_latest_snapshot():
    from src.services.strategy_reviewer import review_allocation_alignment

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    _seed_minimal_registry(conn)

    # Two assets with different latest dates: per-asset latest should include both.
    conn.execute(
        """
        INSERT INTO holdings (snapshot_date, asset_id, market_value, source_system, is_shadow)
        VALUES
          ('2026-03-10', 'US_STK_AAPL', 100, 'Schwab_CSV', FALSE),
          ('2026-03-08', 'CN_FUND_900002', 300, 'CN_Fund_Excel', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO target_allocations (asset_class, target_pct, tolerance_pct, taxonomy_type, source, effective_date)
        VALUES
          ('US Equity', 25, 5, 'Asset Class', 'Strategic_Profile', '2026-03-01'),
          ('CN Equity', 75, 5, 'Asset Class', 'Strategic_Profile', '2026-03-01'),
          ('固定收益', 0, 5, 'Asset Class', 'Strategic_Profile', '2026-03-01'),
          ('现金', 0, 5, 'Asset Class', 'Strategic_Profile', '2026-03-01')
        """
    )

    alignment = review_allocation_alignment(conn)
    assert "US Equity" in alignment["target_scope_alignment"]
    assert "CN Equity" in alignment["target_scope_alignment"]
    assert alignment["target_scope_alignment"]["US Equity"]["actual_pct"] == 25.0
    assert alignment["target_scope_alignment"]["CN Equity"]["actual_pct"] == 75.0


def test_generate_strategy_report_inserts_report_row():
    from src.services.strategy_reviewer import generate_strategy_report

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    create_classification_tables(conn)
    conn.execute(
        """
        INSERT INTO taxonomy_classes (id, name, parent_id, level, sort_order)
        VALUES
          (1, 'Equity', NULL, 0, 1),
          (2, 'US Equity', 1, 1, 1)
        """
    )
    conn.execute("INSERT INTO risk_profiles (id, name, is_active) VALUES (1, '均衡型', TRUE)")
    conn.execute("INSERT INTO risk_profile_allocations (id, profile_id, class_id, target_pct) VALUES (1, 1, 2, 50)")
    _seed_minimal_registry(conn)

    conn.execute(
        """
        INSERT INTO holdings (snapshot_date, asset_id, market_value, source_system, is_shadow)
        VALUES ('2026-03-10', 'US_STK_AAPL', 100, 'Schwab_CSV', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO target_allocations (asset_class, target_pct, tolerance_pct, taxonomy_type, source, effective_date)
        VALUES ('US Equity', 40, 5, 'Asset Class', 'Strategic_Profile', '2026-03-01')
        """
    )
    conn.execute(
        """
        INSERT INTO target_allocations (asset_class, target_pct, tolerance_pct, taxonomy_type, source, effective_date)
        VALUES ('US Equity', 50, 5, 'Asset Class', NULL, '2026-03-02')
        """
    )

    report = generate_strategy_report(conn)
    stored = conn.execute("SELECT COUNT(*) FROM strategy_review_reports").fetchone()[0]

    assert report["target_scope_alignment_status"] in {"aligned", "drifting", "misaligned"}
    assert report["uis_scope_alignment_status"] in {"aligned", "drifting", "misaligned"}
    assert stored == 1


def test_review_allocation_alignment_splits_aia_and_uis_scopes_and_includes_btc_proxy():
    from src.services.strategy_reviewer import review_allocation_alignment

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    create_classification_tables(conn)
    _seed_scope_taxonomy(conn)
    _seed_scope_registry(conn)
    _seed_scope_targets(conn)
    conn.execute(
        """
        INSERT INTO holdings (snapshot_date, asset_id, market_value, source_system, is_shadow)
        VALUES
          ('2026-03-19', 'US_STK_AAPL', 300, 'Schwab_CSV', FALSE),
          ('2026-03-19', 'CN_FUND_900001', 200, 'CN_Fund_Excel', FALSE),
          ('2026-03-19', 'CN_FUND_900009', 100, 'CN_Fund_Excel', FALSE),
          ('2026-03-19', 'US_STK_IBIT', 50, 'Schwab_CSV', FALSE),
          ('2026-03-19', 'US_STK_FBTC', 50, 'Schwab_CSV', FALSE),
          ('2026-03-19', 'US_STK_SGOV', 150, 'Schwab_CSV', FALSE),
          ('2026-03-19', 'CASH_USD', 100, 'Schwab_CSV', FALSE),
          ('2026-03-19', 'CN_FUND_900007', 50, 'CN_Fund_Excel', FALSE),
          ('2026-03-19', 'ALTS_Paper_Gold', 80, 'Gold_Excel', FALSE),
          ('2026-03-19', 'Property_阳光花园', 600, 'Financial_Summary_Excel', FALSE),
          ('2026-03-19', 'INS_安泰人生', 20, 'Insurance_Excel', FALSE)
        """
    )

    alignment = review_allocation_alignment(conn)

    assert "US Equity" in alignment["target_scope_alignment"]
    assert alignment["target_scope_alignment"]["US Equity"]["actual_pct"] == pytest.approx(40.0)
    assert "Commodity" not in alignment["target_scope_alignment"]
    assert "Alternative" not in alignment["target_scope_alignment"]
    assert "Real Estate" not in alignment["target_scope_alignment"]
    assert "Insurance" not in alignment["target_scope_alignment"]
    assert alignment["uis_scope_alignment"]["Equity"]["target_pct"] == pytest.approx(65.0)
    assert alignment["uis_scope_alignment"]["Alternative"]["target_pct"] == pytest.approx(7.0)
    assert alignment["target_scope_summary"]["excluded_classes"] == ['Commodity', 'Insurance', 'Real Estate']
    assert alignment["target_scope_alignment_status"] in {"aligned", "drifting", "misaligned"}
    assert alignment["uis_scope_alignment_status"] in {"aligned", "drifting", "misaligned"}


def test_review_contrarian_consistency_marks_insufficient_market_context():
    from src.services.strategy_reviewer import review_contrarian_consistency

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, suggestion_source)
        VALUES ('2026-03-10', 'CN_FUND_900001', 'Sell', 'analyze')
        """
    )
    conn.execute(
        """
        INSERT INTO market_daily (code, date, open, close)
        VALUES ('900001', '2026-03-10', 2.75, 2.75)
        """
    )

    result = review_contrarian_consistency(conn)

    assert result["status"] == "insufficient_market_context"
    assert result["contrarian_score"] is None


def test_review_trading_frequency_counts_strategy_linked_trades_only():
    from src.services.strategy_reviewer import review_trading_frequency

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, suggestion_source)
        VALUES
          (CURRENT_DATE - INTERVAL '10 days', 'US_STK_AAPL', 'Buy', 'aia_trades_md'),
          (CURRENT_DATE - INTERVAL '20 days', 'US_STK_AAPL', 'Sell', 'analyze'),
          (CURRENT_DATE - INTERVAL '40 days', 'US_STK_AAPL', 'Buy', NULL),
          (CURRENT_DATE - INTERVAL '50 days', 'US_STK_AAPL', 'Sell', 'aia_trades_md'),
          (CURRENT_DATE - INTERVAL '80 days', 'US_STK_AAPL', 'Buy', NULL),
          (CURRENT_DATE - INTERVAL '85 days', 'US_STK_AAPL', 'Buy', 'aia_trades_md')
        """
    )

    result = review_trading_frequency(conn)

    assert result["period_30d"] == 2
    assert result["period_60d"] == 3
    assert result["period_90d"] == 4
    assert result["assessment"] == "aligned"


def test_review_contrarian_consistency_counts_strategy_linked_sells_only():
    from src.services.strategy_reviewer import review_contrarian_consistency

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, suggestion_source)
        VALUES
          ('2026-03-10', 'CN_FUND_900001', 'Sell', 'analyze'),
          ('2026-03-09', 'CN_FUND_900001', 'Sell', NULL),
          ('2026-03-08', 'US_STK_AAPL', 'Sell', 'aia_trades_md')
        """
    )
    conn.execute(
        """
        INSERT INTO market_daily (code, date, open, close)
        VALUES
          ('900001', '2026-03-10', 2.75, 2.75),
          ('SPY', '2026-03-08', 100, 100)
        """
    )

    result = review_contrarian_consistency(conn)

    assert result["status"] == "insufficient_market_context"
    assert result["sell_count"] == 2


def test_generate_strategy_report_normalizes_profile_discrepancies_to_top_level():
    from src.services.strategy_reviewer import generate_strategy_report

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    create_classification_tables(conn)
    _seed_scope_taxonomy(conn)
    _seed_scope_registry(conn)
    _seed_scope_targets(conn)
    conn.execute(
        """
        INSERT INTO holdings (snapshot_date, asset_id, market_value, source_system, is_shadow)
        VALUES
          ('2026-03-19', 'US_STK_AAPL', 300, 'Schwab_CSV', FALSE),
          ('2026-03-19', 'CN_FUND_900001', 200, 'CN_Fund_Excel', FALSE),
          ('2026-03-19', 'CN_FUND_900009', 100, 'CN_Fund_Excel', FALSE),
          ('2026-03-19', 'US_STK_SGOV', 150, 'Schwab_CSV', FALSE),
          ('2026-03-19', 'CASH_USD', 100, 'Schwab_CSV', FALSE),
          ('2026-03-19', 'ALTS_Paper_Gold', 80, 'Gold_Excel', FALSE),
          ('2026-03-19', 'US_STK_IBIT', 50, 'Schwab_CSV', FALSE)
        """
    )

    report = generate_strategy_report(conn)

    assert report["profile_discrepancies"]["target_only"] == []
    assert report["profile_discrepancies"]["uis_only"] == ["Alternative", "Commodity"]
    assert report["profile_discrepancies"]["both"] == ["Cash", "Equity", "Fixed Income"]
