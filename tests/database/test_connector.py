# tests/test_connector.py
import os
from pathlib import Path

def test_connector_creates_database():
    """Test that connector creates DuckDB database file."""
    from src.database.connector import DatabaseConnector
    
    test_db_path = Path("data/test_unified.duckdb")
    if test_db_path.exists():
        test_db_path.unlink()
    
    connector = DatabaseConnector(str(test_db_path))
    
    assert test_db_path.exists()
    assert connector.is_connected()
    
    connector.close()
    test_db_path.unlink()


def test_connector_executes_query():
    """Test that connector can execute queries."""
    from src.database.connector import DatabaseConnector
    
    connector = DatabaseConnector(":memory:")
    result = connector.execute("SELECT 1 as test")
    
    assert result is not None
    assert result.fetchone()[0] == 1
    connector.close()


def test_connector_context_manager():
    """Test that connector works as context manager and auto-closes."""
    from src.database.connector import DatabaseConnector
    
    # Use context manager pattern
    with DatabaseConnector(":memory:") as connector:
        result = connector.execute("SELECT 42 as answer")
        assert result.fetchone()[0] == 42
        assert connector.is_connected()
    
    # After exiting 'with' block, connection should be closed
    assert not connector.is_connected()


def _expected_project_root() -> Path:
    # Mirror src.database.connector.project_root() exactly, including the
    # UIS_PROJECT_ROOT override — otherwise this test fails spuriously under
    # `./dev.sh verify --ci` (which sets UIS_PROJECT_ROOT to an isolated tmp root).
    override = os.getenv("UIS_PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    file_path = Path(__file__).resolve()
    if ".worktrees" in file_path.parts:
        idx = file_path.parts.index(".worktrees")
        return Path(*file_path.parts[:idx])
    return file_path.parents[2]


def test_default_db_path_resolves_to_project_root(monkeypatch):
    """Default DB path should resolve to the canonical project root."""
    from src.database.connector import resolve_db_path

    monkeypatch.delenv("UIS_DB_PATH", raising=False)

    resolved = Path(resolve_db_path("data/unified.duckdb"))
    expected = (_expected_project_root() / "data" / "unified.duckdb").resolve()
    assert resolved == expected


def test_default_db_path_honors_env_override(monkeypatch, tmp_path):
    """UIS_DB_PATH should override default DB path resolution."""
    from src.database.connector import resolve_db_path

    override_path = (tmp_path / "override.duckdb").resolve()
    monkeypatch.setenv("UIS_DB_PATH", str(override_path))

    resolved = Path(resolve_db_path("data/unified.duckdb"))
    assert resolved == override_path


def test_non_default_relative_path_is_not_rewritten(monkeypatch):
    """Custom relative paths should keep existing behavior."""
    from src.database.connector import resolve_db_path

    monkeypatch.delenv("UIS_DB_PATH", raising=False)
    assert resolve_db_path("data/test_unified.duckdb") == "data/test_unified.duckdb"
