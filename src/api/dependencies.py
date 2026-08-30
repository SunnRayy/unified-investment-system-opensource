import os
from pathlib import Path
from typing import Generator, Optional, Tuple
from fastapi import HTTPException

from src.database.connector import (
    DatabaseConnector,
    connect_readonly_with_retry,
    is_transient_conflict,
    resolve_db_path,
)

REQUIRED_CORE_TABLES = ("holdings", "transactions", "sync_audit_reports")
DEFAULT_MIN_FILE_BYTES = 1_000_000


class DatabaseConfigurationError(RuntimeError):
    """Raised when API database is missing expected runtime prerequisites."""


def _min_file_size_bytes() -> int:
    raw = os.getenv("UIS_DB_MIN_FILE_BYTES")
    if raw is None:
        return DEFAULT_MIN_FILE_BYTES
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_MIN_FILE_BYTES


def validate_operational_database(
    conn: DatabaseConnector,
    min_file_size_bytes: Optional[int] = None,
    required_tables: Tuple[str, ...] = REQUIRED_CORE_TABLES,
) -> None:
    """Ensure API runtime DB points to a healthy database."""
    min_size = _min_file_size_bytes() if min_file_size_bytes is None else max(0, min_file_size_bytes)

    if conn.db_path != ":memory:" and min_size > 0:
        db_file = Path(conn.db_path)
        if not db_file.exists():
            raise DatabaseConfigurationError(
                f"database file does not exist: {db_file}"
            )
        file_size = db_file.stat().st_size
        if file_size < min_size:
            raise DatabaseConfigurationError(
                f"database file too small ({file_size} bytes, expected >= {min_size}): {db_file}"
            )

    try:
        table_rows = conn.execute("SHOW TABLES").fetchall()
    except Exception as exc:
        raise DatabaseConfigurationError(
            f"unable to inspect database tables for {conn.db_path}: {exc}"
        ) from exc

    existing_tables = {row[0] for row in table_rows}
    missing_tables = [name for name in required_tables if name not in existing_tables]
    if missing_tables:
        missing = ", ".join(missing_tables)
        raise DatabaseConfigurationError(
            f"database missing required tables ({missing}): {conn.db_path}"
        )

def get_writable_db() -> Generator[DatabaseConnector, None, None]:
    """
    Dependency to yield a writable DatabaseConnector instance.
    Use this for mutation endpoints (POST, DELETE, PUT) that need to write to the DB.
    Opens the database in read-write mode.
    """
    db_path = resolve_db_path()
    conn: Optional[DatabaseConnector] = None
    try:
        conn = DatabaseConnector(db_path, read_only=False)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database not ready: unable to open writable database at {db_path}: {exc}",
        ) from exc

    try:
        validate_operational_database(conn)
        yield conn
    except DatabaseConfigurationError as exc:
        raise HTTPException(status_code=500, detail=f"Database not ready: {exc}") from exc
    finally:
        conn.close()


def get_db() -> Generator[DatabaseConnector, None, None]:
    """
    Dependency to yield a DatabaseConnector instance.
    Ensures connection is closed after request.
    Opens database in read-only mode to allow concurrent API access.
    """
    db_path = resolve_db_path()
    conn: Optional[DatabaseConnector] = None
    # Open READ-ONLY with retry. A writer (GCS flush CHECKPOINT or a write request)
    # briefly holds a read-write connection; DuckDB forbids mixing read-only and
    # read-write opens to one file in a single process. All read paths share this
    # helper so they stay read-only and only ever contend briefly with real writers.
    try:
        conn = connect_readonly_with_retry(db_path)
    except Exception as exc:
        if is_transient_conflict(exc):
            # A writer held the lock for the whole retry budget — temporarily
            # unavailable, not a hard error. 503 + Retry-After lets the client retry.
            raise HTTPException(
                status_code=503,
                detail="Database temporarily busy (write in progress); please retry.",
                headers={"Retry-After": "1"},
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"Database not ready: unable to open database at {db_path}: {exc}",
        ) from exc

    try:
        validate_operational_database(conn)
        yield conn
    except DatabaseConfigurationError as exc:
        raise HTTPException(status_code=500, detail=f"Database not ready: {exc}") from exc
    finally:
        conn.close()
