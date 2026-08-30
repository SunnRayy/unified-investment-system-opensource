from pathlib import Path

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from src.api.dependencies import (
    DatabaseConfigurationError,
    get_db,
    validate_operational_database,
)
from src.database.connector import DatabaseConnector

def test_get_db_dependency():
    """Verify that get_db yields a connected DatabaseConnector."""
    gen = get_db()
    db = next(gen)
    assert isinstance(db, DatabaseConnector)
    # Don't necessarily check specific connection state here as it relies on file system
    # but we can check if the object is created.
    try:
        next(gen)
    except StopIteration:
        pass

def test_dependency_injection_in_app():
    """Verify DI works within a FastAPI route."""
    app = FastAPI()
    
    @app.get("/test-db")
    def test_route(db: DatabaseConnector = Depends(get_db)):
        return {"connected": db.is_connected()}

    client = TestClient(app)
    response = client.get("/test-db")
    assert response.status_code == 200
    assert response.json() == {"connected": True}


def test_validate_operational_database_detects_missing_tables(tmp_path):
    """Validation should fail loudly when core tables are missing."""
    db_path = tmp_path / "empty.duckdb"
    connector = DatabaseConnector(str(db_path))
    try:
        with pytest.raises(DatabaseConfigurationError) as exc_info:
            validate_operational_database(connector, min_file_size_bytes=0)
    finally:
        connector.close()

    error_text = str(exc_info.value)
    assert "missing required tables" in error_text
    assert "holdings" in error_text
    assert str(db_path) in error_text


def test_validate_operational_database_accepts_core_tables(tmp_path):
    """Validation should pass when file size and core tables are present."""
    db_path = tmp_path / "ready.duckdb"
    connector = DatabaseConnector(str(db_path))
    try:
        connector.execute("CREATE TABLE holdings (id INTEGER)")
        connector.execute("CREATE TABLE transactions (id INTEGER)")
        connector.execute("CREATE TABLE sync_audit_reports (id VARCHAR)")
        validate_operational_database(connector, min_file_size_bytes=0)
    finally:
        connector.close()


def test_get_db_returns_clear_error_for_unready_database(monkeypatch, tmp_path):
    """Dependency should return explicit API error instead of opaque SQL failure."""
    broken_db = tmp_path / "broken.duckdb"
    bootstrap = DatabaseConnector(str(broken_db))
    bootstrap.close()
    monkeypatch.setenv("UIS_DB_PATH", str(broken_db))
    monkeypatch.setenv("UIS_DB_MIN_FILE_BYTES", "0")

    app = FastAPI()

    @app.get("/test-db")
    def test_route(db: DatabaseConnector = Depends(get_db)):
        return {"connected": db.is_connected()}

    response = TestClient(app).get("/test-db")
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "Database not ready" in detail
    assert "missing required tables" in detail
    assert str(Path(broken_db)) in detail


def test_get_db_yields_readonly_connection(monkeypatch):
    """get_db acquires its connection via the shared read-only retry helper and
    yields it, closing it on teardown."""
    class FakeConn:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    fake = FakeConn()
    monkeypatch.setattr("src.api.dependencies.connect_readonly_with_retry", lambda db_path=None: fake)
    monkeypatch.setattr("src.api.dependencies.validate_operational_database", lambda conn: None)

    gen = get_db()
    db = next(gen)
    assert db is fake
    try:
        next(gen)
    except StopIteration:
        pass
    assert fake.closed is True


def test_get_db_returns_503_on_persistent_conflict(monkeypatch):
    """If the read-only helper exhausts its retries on a transient mixed-mode/lock
    conflict, get_db surfaces a retryable 503 (not a hard 500)."""
    from fastapi import HTTPException

    def raise_transient(db_path=None):
        raise RuntimeError(
            "Can't open a connection with a different configuration than existing connections"
        )

    monkeypatch.setattr("src.api.dependencies.connect_readonly_with_retry", raise_transient)

    gen = get_db()
    raised = None
    try:
        next(gen)
    except HTTPException as exc:
        raised = exc
    assert raised is not None and raised.status_code == 503


def test_get_db_returns_500_on_nontransient_error(monkeypatch):
    """A non-transient open failure (genuinely missing/corrupt DB) stays a hard 500."""
    from fastapi import HTTPException

    def raise_other(db_path=None):
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr("src.api.dependencies.connect_readonly_with_retry", raise_other)

    gen = get_db()
    raised = None
    try:
        next(gen)
    except HTTPException as exc:
        raised = exc
    assert raised is not None and raised.status_code == 500


def test_connect_readonly_with_retry_retries_then_succeeds(monkeypatch):
    """The helper retries transient conflicts then returns the connection."""
    import src.database.connector as conn_mod

    calls = {"n": 0}

    class FakeDBC:
        def __init__(self, *args, read_only=False, **kwargs):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("Could not set lock on file")

    monkeypatch.setattr(conn_mod, "DatabaseConnector", FakeDBC)
    monkeypatch.setattr(conn_mod.time, "sleep", lambda _s: None)

    result = conn_mod.connect_readonly_with_retry("x.duckdb")
    assert isinstance(result, FakeDBC)
    assert calls["n"] == 3  # 2 conflicts + 1 success


def test_connect_readonly_with_retry_raises_after_budget(monkeypatch):
    """Persistent transient conflict → raise the last exception after the budget."""
    import src.database.connector as conn_mod

    class FakeDBC:
        def __init__(self, *args, read_only=False, **kwargs):
            raise RuntimeError("conflicting lock is held")

    monkeypatch.setattr(conn_mod, "DatabaseConnector", FakeDBC)
    monkeypatch.setattr(conn_mod.time, "sleep", lambda _s: None)

    raised = None
    try:
        conn_mod.connect_readonly_with_retry("x.duckdb", attempts=3)
    except RuntimeError as exc:
        raised = exc
    assert raised is not None and "conflicting lock" in str(raised)


def test_connect_readonly_with_retry_reraises_nontransient(monkeypatch):
    """Non-transient errors are re-raised immediately, not retried."""
    import src.database.connector as conn_mod

    class FakeDBC:
        def __init__(self, *args, read_only=False, **kwargs):
            raise RuntimeError("disk full")

    monkeypatch.setattr(conn_mod, "DatabaseConnector", FakeDBC)

    raised = None
    try:
        conn_mod.connect_readonly_with_retry("x.duckdb")
    except RuntimeError as exc:
        raised = exc
    assert raised is not None and "disk full" in str(raised)
