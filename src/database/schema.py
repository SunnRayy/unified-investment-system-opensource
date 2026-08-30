"""Database schema initialization for Huinsight."""

import logging
import os
from pathlib import Path
from .connector import DatabaseConnector

logger = logging.getLogger(__name__)


def _split_sql_statements(sql: str) -> list:
    """Split SQL on top-level semicolons (not inside string literals or comments).

    The naive ``str.split(';')`` breaks on SQL that embeds semicolons inside
    string literals (e.g. ``DEFAULT 'a;b;c'``).  This function handles:
    - Single-quoted string literals
    - Double-quoted identifiers
    - ``--`` line comments
    - ``/* ... */`` block comments

    Returns:
        List of non-empty SQL statement strings (semicolons stripped).
    """
    statements: list = []
    current: list = []
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        next_ch = sql[i + 1] if i + 1 < len(sql) else ''

        if in_line_comment:
            if ch == '\n':
                in_line_comment = False
            current.append(ch)
        elif in_block_comment:
            if ch == '*' and next_ch == '/':
                in_block_comment = False
                current.append(ch)
                current.append(next_ch)
                i += 1
            else:
                current.append(ch)
        elif in_single_quote:
            if ch == "'":
                in_single_quote = False
            current.append(ch)
        elif in_double_quote:
            if ch == '"':
                in_double_quote = False
            current.append(ch)
        elif ch == '-' and next_ch == '-':
            in_line_comment = True
            current.append(ch)
        elif ch == '/' and next_ch == '*':
            in_block_comment = True
            current.append(ch)
        elif ch == "'":
            in_single_quote = True
            current.append(ch)
        elif ch == '"':
            in_double_quote = True
            current.append(ch)
        elif ch == ';':
            stmt = ''.join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)
        i += 1

    # Handle final statement without trailing semicolon
    remainder = ''.join(current).strip()
    if remainder:
        statements.append(remainder)
    return statements

# Tables that must exist after a successful bootstrap.  Verified by
# _assert_bootstrap_complete() — failure raises before the server serves traffic.
_REQUIRED_TABLES = frozenset([
    # Core pipeline
    "holdings", "transactions", "trade_logs", "insights",
    "sync_audit_reports", "sync_audit_logs", "asset_registry",
    # Classification (Pass D — now created at bootstrap, not sync-time)
    "taxonomy_classes", "asset_tiers", "risk_profiles",
    "risk_profile_allocations", "classification_rules", "classification_audit_log",
    # Sentiment (Pass D)
    "market_sentiment_cache",
    # Auth / profile
    "auth_credentials", "user_profile",
    # Migration version ledger (Pass F Batch 3)
    "schema_version",
    # Reader Mapping Management (ADR-023 / WS-A) — UI-managed reader mappings
    "reader_mappings", "reader_mapping_audit",
])

# Columns that must exist after bootstrap. Tuples of (table, column).
_REQUIRED_COLUMNS = frozenset([
    # Pass D: sentiment columns that previously only existed after a writable request
    ("market_sentiment_cache", "is_stale"),
    ("market_sentiment_cache", "last_refresh_attempt"),
    ("market_sentiment_cache", "error_detail"),
])

# Indexes that must exist after bootstrap.
_REQUIRED_INDEXES = frozenset([
    "idx_holdings_source_system",
    "idx_holdings_is_shadow",
    "idx_transactions_asset_id",
    "idx_trade_logs_linked_transaction_id",
])


def _assert_bootstrap_complete(connector: DatabaseConnector) -> None:
    """Raise if the bootstrap left the database in an incomplete state.

    Checks three things:
    1. No non-idempotent migration failures were collected by ``_run_migration``.
    2. Every table in ``_REQUIRED_TABLES`` exists.
    3. Every (table, column) pair in ``_REQUIRED_COLUMNS`` exists.
    4. Every index in ``_REQUIRED_INDEXES`` exists.

    Raises:
        RuntimeError: with a descriptive message listing all failures.
    """
    problems: list[str] = []

    # 1. Migration failures collected by _run_migration()
    for failure in getattr(connector, "_migration_failures", []):
        problems.append(f"migration failure: {failure}")

    # 2. Required tables
    try:
        existing_tables = {r[0] for r in connector.execute("SHOW TABLES").fetchall()}
    except Exception as e:
        raise RuntimeError(f"bootstrap assertion: cannot query tables: {e}") from e

    for table in sorted(_REQUIRED_TABLES - existing_tables):
        problems.append(f"missing table: {table}")

    # 3. Required columns
    try:
        col_rows = connector.execute(
            "SELECT table_name, column_name FROM information_schema.columns"
        ).fetchall()
        existing_cols = {(r[0], r[1]) for r in col_rows}
    except Exception as e:
        problems.append(f"cannot query columns: {e}")
        existing_cols = set()

    for table, col in sorted(_REQUIRED_COLUMNS - existing_cols):
        problems.append(f"missing column: {table}.{col}")

    # 4. Required indexes
    try:
        idx_rows = connector.execute(
            "SELECT index_name FROM duckdb_indexes()"
        ).fetchall()
        existing_indexes = {r[0] for r in idx_rows}
    except Exception as e:
        problems.append(f"cannot query indexes: {e}")
        existing_indexes = set()

    for idx in sorted(_REQUIRED_INDEXES - existing_indexes):
        problems.append(f"missing index: {idx}")

    if problems:
        bullet_list = "\n  ".join(problems)
        raise RuntimeError(
            f"bootstrap_database() completed with {len(problems)} problem(s) —"
            f" server cannot serve a partially-bootstrapped database:\n  {bullet_list}"
        )


def bootstrap_database(connector: DatabaseConnector) -> None:
    """
    Bootstrap (or reconcile) a database to the current schema and migration level.

    This is the single authoritative entry point for making any database current,
    whether it is a brand-new file or a long-running production instance.

    Sequence (order is load-bearing):
      1. ``initialize_schema`` – runs ``schema.sql`` (all CREATE … IF NOT EXISTS,
         idempotent on a populated DB) to ensure every base table and sequence
         exists before the ALTER-TABLE migrations below attempt to touch them.
      2. ``run_migrations`` – applies incremental column additions, index
         creation, and data-fix migrations.
      3. ``_assert_bootstrap_complete`` – raises if any required table, column,
         or index is missing, or if a non-idempotent migration failure was
         collected. This prevents the server from serving a broken database.

    Used by the FastAPI lifespan, the CLI ``--init`` path, and the CLI
    sync/check-integrity path so all entry points follow the same sequence.
    """
    initialize_schema(connector)
    connector.run_migrations()
    _assert_bootstrap_complete(connector)

    # Program OSR WS-3c: populate data_fixes/unforced_errors/memo_registry/
    # memo_asset_map/valuation_reference from a seed pack when $UIS_SEED_PROFILE
    # is set. No-op (and no import cost) otherwise — every deployment today,
    # including production. See src.database.seed_loader.seed_demo_content.
    if os.environ.get("UIS_SEED_PROFILE"):
        from src.database.seed_loader import seed_demo_content  # noqa: PLC0415 — profile-gated, avoid unconditional import cost
        seed_demo_content(connector)


def initialize_schema(connector: DatabaseConnector) -> None:
    """
    Initialize database schema from SQL file.
    
    Args:
        connector: Active database connector
    """
    schema_path = Path(__file__).parent / "schema.sql"
    
    with open(schema_path, 'r') as f:
        schema_sql = f.read()
    
    # Split on top-level semicolons (literal-aware — handles 'a;b;c' defaults).
    statements = _split_sql_statements(schema_sql)

    for statement in statements:
        if statement:
            connector.execute(statement)

