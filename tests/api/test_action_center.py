"""Test Action Center endpoint.

RED phase: These tests MUST fail before implementation exists.
"""
import pytest
from fastapi.testclient import TestClient
from pathlib import Path

from src.api.main import app
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema

# --- Setup Helpers ---

def execute_migration(conn, migration_path: Path):
    migration_sql = migration_path.read_text()
    lines = [l for l in migration_sql.split('\n') if not l.strip().startswith('--')]
    clean_sql = '\n'.join(lines)
    for stmt in clean_sql.split(';'):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception:
                pass

@pytest.fixture
def client_with_actions_data():
    """Test client with data triggering actions."""
    from src.api.dependencies import get_db
    
    test_conn = DatabaseConnector(":memory:")
    initialize_schema(test_conn)
    
    # Apply migrations
    for mig in sorted(Path("src/database/migrations").glob("*.sql")):
        execute_migration(test_conn, mig)
    
    # 1. Insert Drift Alert (Observing, out of tolerance)
    # Check if table has action_type or not. Plan says no, Schema says no.
    # Columns: asset_class, deviation_pct, tolerance_pct, status, created_at, is_within_tolerance, detected_date
    test_conn.execute("""
        INSERT INTO deviation_actions (
            asset_class, deviation_pct, tolerance_pct, status, created_at, is_within_tolerance, detected_date
        ) VALUES (
            'Equity', 8.5, 5.0, 'observing', '2026-02-01 12:00:00', 0, '2026-02-01'
        )
    """)
    
    # 2. Insert Pending Insight
    # Columns: content, category, created_at, adopted, ai_model, title, insight_date, insight_type
    test_conn.execute("""
        INSERT INTO insights (content, category, created_at, adopted, ai_model, title, insight_date, insight_type)
        VALUES (
            'Rec 1', 'recommendation', '2026-02-01', NULL, 'model', 'Pending Rec', '2026-02-01', 'strategic'
        )
    """)
    
    def override_get_db():
        return test_conn
    
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    test_conn.close()

def test_get_dashboard_actions(client_with_actions_data):
    """Should return list of actionable items."""
    response = client_with_actions_data.get("/dashboard/actions")
    assert response.status_code == 200
    data = response.json()
    assert "actions" in data
    actions = data["actions"]
    
    # Check Drift Alert
    drift = next((a for a in actions if a["type"] == "drift_alert"), None)
    assert drift is not None
    assert "Equity" in drift["title"]
    assert drift["priority"] == "high"
    
    # Check Pending Decision
    pending = next((a for a in actions if a["type"] == "pending_decision"), None)
    assert pending is not None
    assert pending["priority"] == "medium"
