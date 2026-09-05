"""Test Phase 3 schema migration applies cleanly."""
import duckdb
import pytest
from pathlib import Path


@pytest.fixture
def pre_migration_db():
    """Create a DB with the pre-migration schema (no new columns)."""
    conn = duckdb.connect(":memory:")
    # Minimal pre-migration insights table
    conn.execute("""
        CREATE SEQUENCE seq_insights_id START 1;
        CREATE TABLE insights (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_insights_id'),
            insight_date DATE NOT NULL,
            insight_type VARCHAR(50) NOT NULL,
            category VARCHAR(100),
            content TEXT NOT NULL,
            user_notes TEXT,
            observation_source VARCHAR(100),
            verified BOOLEAN DEFAULT FALSE,
            verification_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Minimal pre-migration deviation_actions table
    conn.execute("""
        CREATE SEQUENCE seq_deviation_actions_id START 1;
        CREATE TABLE deviation_actions (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_deviation_actions_id'),
            detected_date DATE NOT NULL,
            deviation_type VARCHAR(100),
            deviation_pct DECIMAL(10,4),
            planned_action TEXT,
            status VARCHAR(50) DEFAULT 'observing',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return conn


def test_migration_adds_insights_columns(pre_migration_db):
    """Migration should add 7 new columns to insights table."""
    conn = pre_migration_db
    migration_sql = Path("src/database/migrations/003_phase3_decision_recording.sql").read_text()

    for statement in migration_sql.split(";"):
        # Strip comment lines from the statement
        lines = [line for line in statement.strip().split('\n') if not line.strip().startswith('--')]
        clean_statement = '\n'.join(lines).strip()
        if clean_statement:
            conn.execute(clean_statement)

    # Verify new columns exist by inserting a row that uses them
    conn.execute("""
        INSERT INTO insights (insight_date, insight_type, content,
            recommendation_id, adopted, adoption_date, outcome_accuracy,
            ai_model, confidence_score, tags)
        VALUES ('2026-01-01', 'recommendation', 'test',
            1, TRUE, '2026-01-15', 85.5,
            'gemini', 0.92, '["tag1", "tag2"]')
    """)
    row = conn.execute("SELECT recommendation_id, adopted, ai_model, confidence_score FROM insights").fetchone()
    assert row[0] == 1
    assert row[1] is True
    assert row[2] == "gemini"
    assert float(row[3]) == pytest.approx(0.92)


def test_migration_adds_deviation_columns(pre_migration_db):
    """Migration should add 5 new columns to deviation_actions table."""
    conn = pre_migration_db
    migration_sql = Path("src/database/migrations/003_phase3_decision_recording.sql").read_text()

    for statement in migration_sql.split(";"):
        # Strip comment lines from the statement
        lines = [line for line in statement.strip().split('\n') if not line.strip().startswith('--')]
        clean_statement = '\n'.join(lines).strip()
        if clean_statement:
            conn.execute(clean_statement)

    conn.execute("""
        INSERT INTO deviation_actions (detected_date, deviation_type, deviation_pct,
            asset_class, current_pct, target_pct, tolerance_pct, is_within_tolerance)
        VALUES ('2026-01-01', 'allocation_drift_US Equity', 3.5,
            'US Equity', 23.5, 20.0, 5.0, TRUE)
    """)
    row = conn.execute("SELECT asset_class, current_pct, target_pct, is_within_tolerance FROM deviation_actions").fetchone()
    assert row[0] == "US Equity"
    assert float(row[1]) == pytest.approx(23.5)
    assert row[3] is True


def test_migration_is_idempotent(pre_migration_db):
    """Running migration twice should not error."""
    conn = pre_migration_db
    migration_sql = Path("src/database/migrations/003_phase3_decision_recording.sql").read_text()

    for _ in range(2):
        for statement in migration_sql.split(";"):
            # Strip comment lines from the statement
            lines = [line for line in statement.strip().split('\n') if not line.strip().startswith('--')]
            clean_statement = '\n'.join(lines).strip()
            if clean_statement:
                conn.execute(clean_statement)

    # Should still work
    conn.execute("INSERT INTO insights (insight_date, insight_type, content, adopted) VALUES ('2026-01-01', 'test', 'test', TRUE)")
    assert conn.execute("SELECT COUNT(*) FROM insights").fetchone()[0] == 1
