"""Integration test for Phase 4 Verification & Insights.

Tests the full flow: Phase 3 data → run_monthly_verification → API query
"""
import pytest
from datetime import date
from pathlib import Path
from fastapi.testclient import TestClient

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.verification.monthly_verifier import run_monthly_verification


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
def integration_db():
    """Fully initialized DB with Phase 3 data for integration testing."""
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)

    # Apply all migrations
    for mig in sorted(Path("src/database/migrations").glob("*.sql")):
        execute_migration(conn, mig)

    # Seed Phase 3 test data: insights with adoption status
    insights = [
        ("2026-01-05", "recommendation", "recommendation", "Buy AAPL at $150", "brief", True),
        ("2026-01-10", "recommendation", "recommendation", "Sell Fund 900002", "committee", True),
        ("2026-01-15", "recommendation", "recommendation", "Hold Gold position", "analyze", False),
        ("2026-01-20", "recommendation", "recommendation", "Reduce equity", "brief", True),
        ("2026-01-25", "recommendation", "recommendation", "Buy bonds", "committee", None),
    ]
    for d, ins_type, cat, content, model, adopted in insights:
        conn.execute("""
            INSERT INTO insights (insight_date, insight_type, category, content, ai_model, adopted)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (d, ins_type, cat, content, model, adopted))

    # Seed deviation_actions with allocation drift data
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


class TestPhase4Integration:
    """Test full Phase 4 verification flow."""

    def test_end_to_end_verification_flow(self, integration_db):
        """Test: Phase 3 data → run_monthly_verification → DB record created."""
        # 1. Run verification
        result = run_monthly_verification(
            integration_db,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            config={"verification": {"benchmark_code": "000300"}}
        )

        # 2. Verify result structure
        assert result["verification_type"] == "monthly"
        assert result["adoption_rate"] == pytest.approx(75.0)  # 3/4 adopted
        assert result["max_allocation_drift"] == pytest.approx(7.5)
        assert result["total_insights"] == 5

        # 3. Verify record saved to DB
        row = integration_db.execute("""
            SELECT verification_type, adoption_rate, max_allocation_drift, total_insights
            FROM verification_logs
            WHERE verification_type = 'monthly'
            ORDER BY created_at DESC
            LIMIT 1
        """).fetchone()
        assert row is not None
        assert row[0] == "monthly"
        assert float(row[1]) == pytest.approx(75.0)
        assert float(row[2]) == pytest.approx(7.5)
        assert row[3] == 5

    def test_api_returns_verification_after_run(self, integration_db):
        """Test: run_monthly_verification → API /verification/latest returns data."""
        from src.api.main import app
        from src.api.dependencies import get_db

        # 1. Run verification
        run_monthly_verification(
            integration_db,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            config={}
        )

        # 2. Override API dependency to use our test DB
        def override_get_db():
            return integration_db

        app.dependency_overrides[get_db] = override_get_db

        # 3. Query API
        client = TestClient(app)
        response = client.get("/verification/latest")
        assert response.status_code == 200
        data = response.json()

        # 4. Verify API response has computed fields (new contract: always fresh-computed)
        assert "adoption_rate" in data
        assert "total_insights" in data

        # Cleanup
        app.dependency_overrides.clear()

    def test_adoption_rate_by_model_breakdown(self, integration_db):
        """Test: adoption rate by model is computed correctly."""
        from src.verification.monthly_verifier import calculate_adoption_rate_by_model

        rates = calculate_adoption_rate_by_model(
            integration_db,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31)
        )

        # brief: 2 adopted out of 2 decided = 100%
        assert rates["brief"] == pytest.approx(100.0)
        # committee: 1 adopted, 1 pending (excluded) → 100%
        assert rates["committee"] == pytest.approx(100.0)
        # analyze: 0 adopted out of 1 = 0%
        assert rates["analyze"] == pytest.approx(0.0)
