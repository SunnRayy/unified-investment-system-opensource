"""Test verification API endpoints.

RED phase: These tests MUST fail before implementation exists.
"""
import pytest
from fastapi.testclient import TestClient
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from src.api.main import app
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from pathlib import Path


def execute_migration(conn, migration_path: Path):
    """Execute SQL migration, stripping comment lines properly."""
    migration_sql = migration_path.read_text()
    lines = [line for line in migration_sql.split('\n') if not line.strip().startswith('--')]
    clean_sql = '\n'.join(lines)
    for stmt in clean_sql.split(';'):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


@pytest.fixture
def client():
    """Test client with in-memory database."""
    # Override database dependency
    from src.api.dependencies import get_db
    
    test_conn = DatabaseConnector(":memory:")
    initialize_schema(test_conn)
    
    # Apply migrations
    for mig in sorted(Path("src/database/migrations").glob("*.sql")):
        execute_migration(test_conn, mig)
    
    def override_get_db():
        return test_conn
    
    app.dependency_overrides[get_db] = override_get_db
    
    yield TestClient(app)
    
    app.dependency_overrides.clear()
    test_conn.close()


@pytest.fixture
def client_with_verification_data(client):
    """Test client with verification data populated."""
    from src.api.dependencies import get_db
    conn = app.dependency_overrides[get_db]()
    
    # Insert a verification record
    conn.execute("""
        INSERT INTO verification_logs (
            verification_date, verification_type, period_start, period_end,
            adoption_rate, portfolio_return, benchmark_return, alpha,
            max_allocation_drift, total_insights, generated_by,
            ai_hit_rate_by_model, drift_details
        ) VALUES (
            '2026-02-01', 'monthly', '2026-01-01', '2026-01-31',
            75.0, 3.2, 2.1, 1.1,
            7.5, 11, 'system',
            '{"brief": 100.0, "committee": 50.0}',
            '[{"asset_class": "US Equity", "deviation_pct": 7.5}]'
        )
    """)
    
    return client


class TestVerificationLatestEndpoint:
    """Test GET /verification/latest endpoint."""

    def test_latest_returns_message_when_no_data(self, client):
        """Should return computed report (not a message) even when no prior logs exist."""
        response = client.get("/verification/latest")
        assert response.status_code == 200
        data = response.json()
        # New behavior: always computes on-demand — never returns just a {"message": ...}
        assert "adoption_rate" in data
        assert "total_insights" in data

    def test_latest_returns_verification_data(self, client_with_verification_data):
        """Should return latest verification report with computed fields."""
        response = client_with_verification_data.get("/verification/latest")
        assert response.status_code == 200
        data = response.json()
        # New contract: always returns computed fields
        assert "adoption_rate" in data
        assert "max_drift" in data
        assert "total_insights" in data
        assert "verdict_hit_rate" in data
        assert "adoption_history" in data

    def test_latest_excludes_lessons_from_adoption_rate(self, client):
        from src.api.dependencies import get_db

        conn = app.dependency_overrides[get_db]()
        conn.execute(
            """
            INSERT INTO insights (
                insight_date, insight_type, category, content, adopted, created_at, title
            ) VALUES
              ('2026-03-01', 'recommendation', 'recommendation', 'Keep SGOV core', 1, '2026-03-01 10:00:00', 'Keep SGOV core'),
              ('2026-03-02', 'lesson', 'lesson', 'Learned discipline', NULL, '2026-03-02 10:00:00', 'Learned discipline')
            """
        )

        response = client.get("/verification/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["total_insights"] == 1
        assert data["adoption_rate"] == 100.0

    def test_latest_returns_portfolio_and_benchmark_when_market_data_exists(self, client):
        from src.api.dependencies import get_db

        conn = app.dependency_overrides[get_db]()
        today = date(2025, 6, 15)    # fixed mid-month — never equals month_start
        month_start = date(2025, 6, 1)  # always different from today

        conn.execute(
            """
            INSERT INTO holdings (
                snapshot_date, asset_id, source_system, market_value, is_shadow, authority_source
            ) VALUES
              (?, 'US_STK_SGOV', 'Schwab_CSV', 1000, FALSE, 'reader'),
              (?, 'US_STK_SGOV', 'Schwab_CSV', 1100, FALSE, 'reader')
            """,
            (str(month_start), str(today)),
        )
        conn.execute(
            """
            INSERT INTO market_daily (code, date, close)
            VALUES
              ('000300', ?, 100),
              ('000300', ?, 105)
            """,
            (str(month_start), str(today)),
        )

        # Patch date.today() in verification_service so it uses our fixed test date
        mock_date = MagicMock(spec=date, wraps=date)
        mock_date.today.return_value = today
        with patch("src.services.verification_service.date", mock_date):
            response = client.get("/verification/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["portfolio_return"] == 10.0
        assert data["benchmark_return"] == 5.0
        assert data["alpha"] == 5.0

    def test_latest_uses_balance_sheet_history_and_benchmark_proxy_when_primary_code_missing(self, client):
        from src.api.dependencies import get_db

        conn = app.dependency_overrides[get_db]()
        today = date(2025, 6, 15)    # fixed mid-month — never equals month_start
        month_start = date(2025, 6, 1)  # always different from today
        prior_month_end = month_start - timedelta(days=1)

        conn.execute(
            """
            INSERT INTO balance_sheet_monthly (record_key, snapshot_date, payload)
            VALUES (?, ?, '{"合计总资产": 1000.0}')
            """,
            (f"bs_{prior_month_end.isoformat()}", str(prior_month_end)),
        )
        conn.execute(
            """
            INSERT INTO holdings (
                snapshot_date, asset_id, source_system, market_value, is_shadow, authority_source
            ) VALUES
              (?, 'US_STK_SGOV', 'Schwab_CSV', 900, TRUE, 'reader'),
              (?, 'US_STK_SGOV', 'Schwab_CSV', 1100, FALSE, 'reader')
            """,
            (str(month_start), str(today)),
        )
        conn.execute(
            """
            INSERT INTO market_daily (code, date, close)
            VALUES
              ('000300', ?, 100),
              ('000300', ?, 108)
            """,
            (str(prior_month_end), str(today)),
        )

        # Patch date.today() in verification_service so it uses our fixed test date
        mock_date = MagicMock(spec=date, wraps=date)
        mock_date.today.return_value = today
        # Pin the proxy list rather than inheriting it from whatever config is on
        # disk. The subject here is the fallback *behaviour* — the configured
        # code has no data, so the proxies are tried — not which codes happen to
        # be configured. Reading ambient config made this pass only on a machine
        # whose gitignored settings.yaml resolved to the 6-code default; CI has
        # no settings.yaml, falls back to settings.example.yaml's four generic
        # codes, and '000300' was not among them.
        with patch(
            "src.verification.monthly_verifier.get_benchmark_proxy_codes",
            return_value=("000300", "CSI300", "000300", "CSI300", "SPY", "^GSPC"),
        ), patch("src.services.verification_service.date", mock_date):
            response = client.get("/verification/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["portfolio_return"] == 10.0
        assert data["benchmark_return"] == 8.0
        assert data["alpha"] == 2.0


class TestVerificationHistoryEndpoint:
    """Test GET /verification/history endpoint."""

    def test_history_returns_empty_list_when_no_data(self, client):
        """Should return empty list when no verification reports exist."""
        response = client.get("/verification/history")
        assert response.status_code == 200
        assert response.json() == []

    def test_history_returns_list_of_reports(self, client_with_verification_data):
        """Should return list of verification reports."""
        response = client_with_verification_data.get("/verification/history")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["verification_type"] == "monthly"

    def test_history_respects_limit_parameter(self, client_with_verification_data):
        """Should respect limit query parameter."""
        response = client_with_verification_data.get("/verification/history?limit=5")
        assert response.status_code == 200

    def test_history_returns_portfolio_benchmark_alpha(self, client_with_verification_data):
        """History endpoint must surface portfolio_return, benchmark_return, and alpha."""
        response = client_with_verification_data.get("/verification/history")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        row = data[0]
        assert "portfolio_return" in row, "history row must include portfolio_return"
        assert "benchmark_return" in row, "history row must include benchmark_return"
        assert "alpha" in row, "history row must include alpha"
        assert row["portfolio_return"] == pytest.approx(3.2)
        assert row["benchmark_return"] == pytest.approx(2.1)
        assert row["alpha"] == pytest.approx(1.1)


class TestVerificationPersistsReturnColumns:
    """Tests that compute_verification_report() persists return columns to verification_logs."""

    def test_compute_persists_portfolio_benchmark_alpha_to_db(self):
        """After compute with seeded market data, verification_logs row has non-NULL return cols.

        Deliberately self-contained (own in-memory DB, no app/fixture coupling):
        reading app.dependency_overrides here was order-dependent — an earlier
        test module reloading src.api.main hands this test a fresh app with
        empty overrides (KeyError).
        """
        from src.services.verification_service import compute_verification_report

        conn = DatabaseConnector(":memory:")
        initialize_schema(conn)
        for mig in sorted(Path("src/database/migrations").glob("*.sql")):
            execute_migration(conn, mig)
        today = date(2025, 6, 15)
        month_start = date(2025, 6, 1)

        # Seed holdings so calculate_portfolio_return has a start + end value
        conn.execute(
            """
            INSERT INTO holdings (
                snapshot_date, asset_id, source_system, market_value, is_shadow, authority_source
            ) VALUES
              (?, 'US_STK_SGOV', 'Schwab_CSV', 1000, FALSE, 'reader'),
              (?, 'US_STK_SGOV', 'Schwab_CSV', 1100, FALSE, 'reader')
            """,
            (str(month_start), str(today)),
        )
        # Seed benchmark prices so calculate_benchmark_return can compute a return
        conn.execute(
            """
            INSERT INTO market_daily (code, date, close)
            VALUES ('000300', ?, 100), ('000300', ?, 105)
            """,
            (str(month_start), str(today)),
        )

        mock_date = MagicMock(spec=date, wraps=date)
        mock_date.today.return_value = today
        with patch("src.services.verification_service.date", mock_date):
            compute_verification_report(conn)

        row = conn.execute(
            """
            SELECT portfolio_return, benchmark_return, alpha
            FROM verification_logs
            WHERE verification_type = 'monthly'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()

        assert row is not None, "verification_logs row was not inserted"
        assert row[0] is not None, "portfolio_return must be persisted (was NULL)"
        assert row[1] is not None, "benchmark_return must be persisted (was NULL)"
        assert row[2] is not None, "alpha must be persisted (was NULL)"
        # Sanity-check values match what the service returns
        assert float(row[0]) == pytest.approx(10.0)
        assert float(row[1]) == pytest.approx(5.0)
        assert float(row[2]) == pytest.approx(5.0)
        conn.close()
