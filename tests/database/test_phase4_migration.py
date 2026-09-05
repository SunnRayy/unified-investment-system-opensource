"""Test Phase 4 schema migration for verification_logs.

RED phase: These tests MUST fail before migration exists.
"""
import duckdb
import pytest
from pathlib import Path


def execute_migration(conn, migration_path: Path):
    """Execute SQL migration, stripping comment lines properly."""
    migration_sql = migration_path.read_text()
    # Remove comment lines, then split by semicolon
    lines = [l for l in migration_sql.split('\n') if not l.strip().startswith('--')]
    clean_sql = '\n'.join(lines)
    for stmt in clean_sql.split(';'):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


@pytest.fixture
def pre_migration_db():
    """DB with current verification_logs schema (Phase 2 reconciliation-focused)."""
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE SEQUENCE seq_verification_logs_id START 1;
        CREATE TABLE verification_logs (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_verification_logs_id'),
            verification_date DATE NOT NULL,
            source_a VARCHAR(50),
            source_b VARCHAR(50),
            data_type VARCHAR(50),
            discrepancy_count INTEGER,
            discrepancy_details JSON,
            user_confirmed BOOLEAN DEFAULT FALSE,
            resolution_action TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return conn


class TestPhase4VerificationLogsMigration:
    """Test Phase 4 schema migration adds monthly KPI columns."""

    def test_migration_adds_monthly_kpi_columns(self, pre_migration_db):
        """Migration should add verification_type, period, KPI fields."""
        conn = pre_migration_db
        migration_path = Path("src/database/migrations/006_phase4_verification_logs.sql")
        assert migration_path.exists(), "Migration file must exist"
        execute_migration(conn, migration_path)

        # Test insert with new columns
        conn.execute("""
            INSERT INTO verification_logs (
                verification_date, verification_type, period_start, period_end,
                ai_hit_rate, adoption_rate, portfolio_return, benchmark_return, alpha,
                max_allocation_drift, total_insights, generated_by
            ) VALUES (
                '2026-02-01', 'monthly', '2026-01-01', '2026-01-31',
                72.5, 85.0, 3.2, 2.1, 1.1,
                4.5, 11, 'system'
            )
        """)
        row = conn.execute("""
            SELECT verification_type, ai_hit_rate, adoption_rate, alpha, generated_by
            FROM verification_logs
        """).fetchone()
        assert row[0] == "monthly"
        assert float(row[1]) == pytest.approx(72.5)
        assert float(row[2]) == pytest.approx(85.0)
        assert float(row[3]) == pytest.approx(1.1)
        assert row[4] == "system"

    def test_migration_preserves_existing_columns(self, pre_migration_db):
        """Existing reconciliation columns should still work after migration."""
        conn = pre_migration_db
        # Insert pre-migration style row
        conn.execute("""
            INSERT INTO verification_logs (
                verification_date, source_a, source_b, data_type, discrepancy_count
            ) VALUES ('2026-01-15', 'PIS', 'AIA', 'holdings', 3)
        """)

        migration_path = Path("src/database/migrations/006_phase4_verification_logs.sql")
        if migration_path.exists():
            execute_migration(conn, migration_path)

        row = conn.execute("""
            SELECT source_a, source_b, data_type, discrepancy_count
            FROM verification_logs
        """).fetchone()
        assert row[0] == "PIS"
        assert row[1] == "AIA"
        assert row[2] == "holdings"
        assert row[3] == 3

    def test_migration_is_idempotent(self, pre_migration_db):
        """Running migration twice should not error (IF NOT EXISTS)."""
        conn = pre_migration_db
        migration_path = Path("src/database/migrations/006_phase4_verification_logs.sql")
        assert migration_path.exists(), "Migration file must exist"
        # Run twice to verify idempotency
        for _ in range(2):
            execute_migration(conn, migration_path)
        
        # Should still work
        conn.execute("""
            INSERT INTO verification_logs (
                verification_date, verification_type, period_start, period_end
            ) VALUES ('2026-02-01', 'monthly', '2026-01-01', '2026-01-31')
        """)
        assert conn.execute("SELECT COUNT(*) FROM verification_logs").fetchone()[0] == 1

    def test_json_columns_accept_complex_data(self, pre_migration_db):
        """New JSON columns should accept complex nested data."""
        conn = pre_migration_db
        migration_path = Path("src/database/migrations/006_phase4_verification_logs.sql")
        assert migration_path.exists(), "Migration file must exist"
        execute_migration(conn, migration_path)

        # Insert with JSON columns
        import json
        hit_rate_by_model = json.dumps({"brief": 100.0, "committee": 50.0, "analyze": 0.0})
        drift_details = json.dumps([
            {"asset_class": "US Equity", "current_pct": 27.5, "target_pct": 20.0, "deviation_pct": 7.5}
        ])
        key_lessons = json.dumps(["Avoid FOMO trades", "Stick to allocation targets"])

        conn.execute("""
            INSERT INTO verification_logs (
                verification_date, verification_type,
                ai_hit_rate_by_model, drift_details, key_lessons
            ) VALUES (?, ?, ?, ?, ?)
        """, ('2026-02-01', 'monthly', hit_rate_by_model, drift_details, key_lessons))

        row = conn.execute("""
            SELECT ai_hit_rate_by_model, drift_details, key_lessons
            FROM verification_logs
        """).fetchone()
        assert json.loads(row[0])["brief"] == 100.0
        assert json.loads(row[1])[0]["asset_class"] == "US Equity"
        assert "Avoid FOMO" in json.loads(row[2])[0]
