"""Test verification trends API endpoint.

RED phase: This test MUST fail before implementation exists.
"""
import pytest
from fastapi.testclient import TestClient
from pathlib import Path

from src.api.main import app
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema

# --- Duplicate setup from test_verification.py since conftest is missing ---

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
def client():
    """Test client with in-memory database."""
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
def client_with_trend_data(client):
    """Test client with insights data for monthly trend analysis."""
    from src.api.dependencies import get_db
    conn = app.dependency_overrides[get_db]()

    # Insert insights across two months so trends endpoint returns 2 periods
    # Jan 2026: 4 total, 3 adopted → 75%
    for i in range(3):
        conn.execute(
            "INSERT INTO insights (insight_date, insight_type, content, adopted, created_at) VALUES (?, 'recommendation', ?, 1, ?)",
            (f"2026-01-{i+10}", f"insight {i}", "2026-01-15"),
        )
    conn.execute(
        "INSERT INTO insights (insight_date, insight_type, content, adopted, created_at) VALUES (?, 'recommendation', ?, 0, ?)",
        ("2026-01-20", "rejected insight", "2026-01-20"),
    )
    # Feb 2026: 5 total, 4 adopted → 80%
    for i in range(4):
        conn.execute(
            "INSERT INTO insights (insight_date, insight_type, content, adopted, created_at) VALUES (?, 'recommendation', ?, 1, ?)",
            (f"2026-02-{i+5}", f"feb insight {i}", "2026-02-10"),
        )
    conn.execute(
        "INSERT INTO insights (insight_date, insight_type, content, adopted, created_at) VALUES (?, 'recommendation', ?, 0, ?)",
        ("2026-02-20", "rejected feb", "2026-02-20"),
    )

    return client

# --- Tests ---

def test_get_verification_trends(client_with_trend_data):
    """Should return trend data for dashboard charts."""
    # When
    response = client_with_trend_data.get("/verification/trends")
    
    # Then
    assert response.status_code == 200
    data = response.json()
    assert "periods" in data
    assert len(data["periods"]) == 2
    
    # Verify sort order (ASC by period_start)
    first = data["periods"][0]
    second = data["periods"][1]
    
    assert first["period_start"] == "2026-01-01"
    assert first["adoption_rate"] == 75.0
    
    assert second["period_start"] == "2026-02-01"
    assert second["adoption_rate"] == 80.0


def test_get_verification_trends_excludes_lessons(client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO insights (insight_date, insight_type, category, content, adopted, created_at)
        VALUES
          ('2026-03-01', 'recommendation', 'recommendation', 'Insight A', 1, '2026-03-01 09:00:00'),
          ('2026-03-02', 'lesson', 'lesson', 'Lesson A', NULL, '2026-03-02 09:00:00')
        """
    )

    response = client.get("/verification/trends")
    assert response.status_code == 200
    data = response.json()
    assert len(data["periods"]) == 1
    assert data["periods"][0]["period_start"] == "2026-03-01"
    assert data["periods"][0]["total_insights"] == 1
    assert data["periods"][0]["adoption_rate"] == 100.0
