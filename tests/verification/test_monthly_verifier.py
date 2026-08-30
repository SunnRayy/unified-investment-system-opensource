"""Test monthly verification metric calculations.

RED phase: These tests MUST fail before implementation exists.
"""
import pytest
from datetime import date
from pathlib import Path

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema


def execute_migration(conn, migration_path: Path):
    """Execute SQL migration, stripping comment lines properly."""
    migration_sql = migration_path.read_text()
    lines = [l for l in migration_sql.split('\n') if not l.strip().startswith('--')]
    clean_sql = '\n'.join(lines)
    for stmt in clean_sql.split(';'):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


@pytest.fixture
def db_with_phase3_data():
    """DB with realistic Phase 3 data for verification testing."""
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)

    # Apply Phase 3 + 4 migrations
    for mig in sorted(Path("src/database/migrations").glob("*.sql")):
        execute_migration(conn, mig)

    # Insert insights with adoption data (January 2026)
    test_insights = [
        ("2026-01-05", "recommendation", "recommendation", "Buy AAPL", "brief", True),
        ("2026-01-10", "recommendation", "recommendation", "Sell Fund A", "committee", True),
        ("2026-01-15", "recommendation", "recommendation", "Hold Gold", "analyze", False),
        ("2026-01-20", "recommendation", "recommendation", "Reduce exposure", "brief", True),
        ("2026-01-25", "recommendation", "recommendation", "Buy bonds", "committee", None),  # pending
    ]
    for d, insight_type, cat, content, model, adopted in test_insights:
        conn.execute("""
            INSERT INTO insights (insight_date, insight_type, category, content, ai_model, adopted)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (d, insight_type, cat, content, model, adopted))

    # Insert trade_logs with outcomes
    conn.execute("""
        INSERT INTO trade_logs (log_date, asset_id, asset_name, action, pnl_pct, pnl_amount)
        VALUES ('2026-01-10', 'CN_FUND_900002', 'Test Fund', 'sell', 5.2, 1500.0)
    """)
    conn.execute("""
        INSERT INTO trade_logs (log_date, asset_id, asset_name, action, pnl_pct, pnl_amount)
        VALUES ('2026-01-20', 'US_STK_AAPL', 'Apple', 'buy', -2.1, -800.0)
    """)

    # Insert deviation_actions with allocation drift data
    conn.execute("""
        INSERT INTO deviation_actions (
            detected_date, deviation_type, deviation_pct, status,
            asset_class, current_pct, target_pct, tolerance_pct, is_within_tolerance
        ) VALUES ('2026-01-15', 'allocation_drift_US_Equity', 7.5, 'observing',
            'US Equity', 27.5, 20.0, 5.0, FALSE)
    """)
    conn.execute("""
        INSERT INTO deviation_actions (
            detected_date, deviation_type, deviation_pct, status,
            asset_class, current_pct, target_pct, tolerance_pct, is_within_tolerance
        ) VALUES ('2026-01-15', 'allocation_drift_CN_Fund', -3.2, 'observing',
            'CN Fund', 31.8, 35.0, 5.0, TRUE)
    """)

    yield conn
    conn.close()


class TestMonthlyVerifierCalculations:
    """Test monthly verification metric calculations."""

    def test_calculate_adoption_rate(self, db_with_phase3_data):
        """Adoption rate = adopted / (adopted + rejected), excluding NULL/pending."""
        from src.verification.monthly_verifier import calculate_adoption_rate

        rate = calculate_adoption_rate(
            db_with_phase3_data,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31)
        )
        # 3 adopted, 1 rejected, 1 pending (excluded) → 3/4 = 75%
        assert rate == pytest.approx(75.0)

    def test_calculate_adoption_rate_by_model(self, db_with_phase3_data):
        """Adoption rate broken down by AI model."""
        from src.verification.monthly_verifier import calculate_adoption_rate_by_model

        rates = calculate_adoption_rate_by_model(
            db_with_phase3_data,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31)
        )
        # brief: 2 adopted out of 2 decided = 100%
        assert rates["brief"] == pytest.approx(100.0)
        # committee: 1 adopted, 1 pending (excluded) → 1/1 = 100%  
        assert rates["committee"] == pytest.approx(100.0)
        # analyze: 0 adopted out of 1 decided = 0%
        assert rates["analyze"] == pytest.approx(0.0)

    def test_calculate_max_drift(self, db_with_phase3_data):
        """Max allocation drift from deviation_actions in the period."""
        from src.verification.monthly_verifier import calculate_max_drift

        drift, details = calculate_max_drift(
            db_with_phase3_data,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31)
        )
        assert drift == pytest.approx(7.5)  # US Equity drift (absolute value)
        assert len(details) == 2
        assert any(d["asset_class"] == "US Equity" for d in details)

    def test_count_insights(self, db_with_phase3_data):
        """Count total insights in the period."""
        from src.verification.monthly_verifier import count_insights

        total = count_insights(
            db_with_phase3_data,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31)
        )
        assert total == 5

    def test_run_monthly_verification(self, db_with_phase3_data):
        """Full monthly verification produces a verification_logs record."""
        from src.verification.monthly_verifier import run_monthly_verification

        result = run_monthly_verification(
            db_with_phase3_data,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            config={}
        )
        assert result["verification_type"] == "monthly"
        assert result["adoption_rate"] is not None
        assert result["max_allocation_drift"] is not None
        assert result["total_insights"] == 5

        # Verify it was saved to verification_logs
        row = db_with_phase3_data.execute("""
            SELECT verification_type, adoption_rate, max_allocation_drift, total_insights
            FROM verification_logs
            WHERE verification_type = 'monthly'
        """).fetchone()
        assert row is not None
        assert row[0] == "monthly"


class TestMonthlyVerifierEdgeCases:
    """Test edge cases and empty data scenarios."""

    def test_adoption_rate_with_no_recommendations(self, db_with_phase3_data):
        """Adoption rate returns 0 when no recommendations exist."""
        from src.verification.monthly_verifier import calculate_adoption_rate

        # Query for a period with no data
        rate = calculate_adoption_rate(
            db_with_phase3_data,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31)
        )
        assert rate == 0.0

    def test_max_drift_with_no_deviations(self, db_with_phase3_data):
        """Max drift returns 0 with empty details when no deviations exist."""
        from src.verification.monthly_verifier import calculate_max_drift

        drift, details = calculate_max_drift(
            db_with_phase3_data,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31)
        )
        assert drift == 0.0
        assert details == []

    def test_calculate_portfolio_return_uses_balance_sheet_history_when_month_start_has_no_authoritative_holdings(
        self, db_with_phase3_data
    ):
        """Portfolio return should use the historical value series, not raw holdings snapshots."""
        from src.verification.monthly_verifier import calculate_portfolio_return

        db_with_phase3_data.execute(
            """
            INSERT INTO balance_sheet_monthly (record_key, snapshot_date, payload)
            VALUES ('bs_2026_02_28', '2026-02-28', '{"合计总资产": 1000.0}')
            """
        )
        db_with_phase3_data.execute(
            """
            INSERT INTO holdings (
                snapshot_date, asset_id, source_system, market_value, is_shadow, authority_source
            ) VALUES
              ('2026-03-01', 'US_STK_SGOV', 'Schwab_CSV', 900, TRUE, 'reader'),
              ('2026-03-19', 'US_STK_SGOV', 'Schwab_CSV', 1100, FALSE, 'reader')
            """
        )

        result = calculate_portfolio_return(
            db_with_phase3_data,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 19),
        )

        assert result == pytest.approx(10.0)

    def test_calculate_benchmark_return_falls_back_to_available_proxy_symbol(self, db_with_phase3_data):
        """Benchmark return should use a supported proxy when configured code is missing."""
        from src.verification.monthly_verifier import calculate_benchmark_return

        db_with_phase3_data.execute(
            """
            INSERT INTO market_daily (code, date, close)
            VALUES
              ('000300', '2026-02-28', 100),
              ('000300', '2026-03-19', 108)
            """
        )

        result = calculate_benchmark_return(
            db_with_phase3_data,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 19),
            benchmark_code="000300",
        )

        assert result == pytest.approx(8.0)
