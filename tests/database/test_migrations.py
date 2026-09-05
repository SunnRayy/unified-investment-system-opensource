"""Tests for database migrations.

Covers Migration 10 (V5.8.0), Migration 11 (V5.10.0), Pass D migrations
13 (classification tables), 14 (sentiment columns), and 15 (hot-path indexes),
and Pass F Migration 16 (drop orphaned tables).

All tests use tmp_path (pytest fixture) or ":memory:" — never the production DB.
All inserted dates use fixed past dates (never date.today()) to avoid the
month-start PK collision in the holdings unique constraint.
"""
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema, bootstrap_database, _assert_bootstrap_complete
from src.services.north_star_flows import compose_natural_key


def _make_db(tmp_path):
    """Create a fresh DB with schema + migrations applied."""
    db_path = tmp_path / "test_mig10.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    connector.run_migrations()
    return connector


def test_migration10_trade_logs_updated_at(tmp_path):
    """trade_logs.updated_at column must exist after Migration 10."""
    connector = _make_db(tmp_path)
    result = connector.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'trade_logs' AND column_name = 'updated_at'
    """).fetchone()
    assert result is not None, "trade_logs.updated_at column should exist after Migration 10"
    connector.close()


def test_migration10_trade_logs_verification_block_reason(tmp_path):
    """trade_logs.verification_block_reason column must exist after Migration 10."""
    connector = _make_db(tmp_path)
    result = connector.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'trade_logs' AND column_name = 'verification_block_reason'
    """).fetchone()
    assert result is not None, "trade_logs.verification_block_reason column should exist after Migration 10"
    connector.close()


def test_migration10_verdict_audit_table(tmp_path):
    """verdict_audit table must exist with correct schema after Migration 10."""
    connector = _make_db(tmp_path)
    result = connector.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'verdict_audit'
        ORDER BY column_name
    """).fetchall()
    col_names = {r[0] for r in result}
    required = {"id", "trade_id", "suggested_from_threshold", "keyword_derived", "final_verdict", "mismatch", "created_at"}
    assert required.issubset(col_names), f"verdict_audit missing columns: {required - col_names}"
    connector.close()


def test_migration10_idempotent(tmp_path):
    """Running run_migrations() twice must not raise or corrupt the schema."""
    db_path = tmp_path / "test_mig10_idem.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    connector.run_migrations()
    # Run a second time — must be silent
    connector.run_migrations()
    # Columns still present
    result = connector.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'trade_logs' AND column_name = 'verification_block_reason'
    """).fetchone()
    assert result is not None, "Column should still exist after double migration run"
    connector.close()


# ── Migration 11: V5.10.0 insight_trade_links ──────────────────────────────

def _make_db11(tmp_path):
    """Fresh DB with schema + all migrations including Migration 11."""
    db_path = tmp_path / "test_mig11.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    connector.run_migrations()
    return connector


def test_migration11_insight_trade_links_table_exists(tmp_path):
    """insight_trade_links table must exist with required columns after Migration 11."""
    connector = _make_db11(tmp_path)
    result = connector.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'insight_trade_links'
        ORDER BY column_name
    """).fetchall()
    col_names = {r[0] for r in result}
    required = {"id", "insight_id", "trade_id", "link_type", "confidence", "rationale", "created_at"}
    assert required.issubset(col_names), f"insight_trade_links missing columns: {required - col_names}"
    connector.close()


def test_migration11_insight_trade_links_unique_constraint(tmp_path):
    """insight_trade_links must enforce UNIQUE(insight_id, trade_id)."""
    connector = _make_db11(tmp_path)
    # Insert insights and trade_logs rows to satisfy FKs (no FK enforcement in DuckDB, but add anyway)
    connector.execute(
        "INSERT INTO insight_trade_links (insight_id, trade_id, link_type, confidence) VALUES (1, 1, 'auto_source', 1.0)"
    )
    # Second insert with same (insight_id, trade_id) should fail
    try:
        connector.execute(
            "INSERT INTO insight_trade_links (insight_id, trade_id, link_type, confidence) VALUES (1, 1, 'manual', 1.0)"
        )
        assert False, "Duplicate (insight_id, trade_id) should have raised a constraint error"
    except Exception:
        pass  # Expected
    connector.close()


def test_migration11_idempotent(tmp_path):
    """Running run_migrations() twice keeps insight_trade_links intact."""
    db_path = tmp_path / "test_mig11_idem.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    connector.run_migrations()
    # Seed one link
    connector.execute(
        "INSERT INTO insight_trade_links (insight_id, trade_id, link_type, confidence) VALUES (99, 99, 'manual', 1.0)"
    )
    # Second migration run must not wipe the row or raise
    connector.run_migrations()
    count = connector.execute("SELECT COUNT(*) FROM insight_trade_links").fetchone()[0]
    assert count == 1, "Seeded row should survive double migration run"
    connector.close()


def test_migration11_backfill_guard_runs_only_once(tmp_path):
    """Backfill guard: if insight_trade_links already has rows, backfill is skipped on re-run."""
    db_path = tmp_path / "test_mig11_guard.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    connector.run_migrations()
    # Manually insert a sentinel link — simulates post-backfill state
    connector.execute(
        "INSERT INTO insight_trade_links (insight_id, trade_id, link_type, confidence) VALUES (1, 1, 'auto_source', 0.5)"
    )
    # Running migrations again must not duplicate the link
    connector.run_migrations()
    count = connector.execute("SELECT COUNT(*) FROM insight_trade_links").fetchone()[0]
    assert count == 1, "Backfill guard should prevent duplicate rows on second run"
    connector.close()


# ── Pass D: bootstrap_database() + Migrations 13/14/15 ────────────────────────

_CLASSIFICATION_TABLES = [
    "taxonomy_classes",
    "asset_tiers",
    "risk_profiles",
    "risk_profile_allocations",
    "classification_rules",
    "classification_audit_log",
]

_SENTIMENT_COLUMNS = [
    "is_stale",
    "last_refresh_attempt",
    "error_detail",
]

_HOT_PATH_INDEXES = [
    "idx_holdings_source_system",
    "idx_holdings_is_shadow",
    "idx_transactions_asset_id",
    "idx_trade_logs_linked_transaction_id",
]


def _all_tables(conn):
    return {r[0] for r in conn.execute("SHOW TABLES").fetchall()}


def _all_columns(conn, table):
    return {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
        [table],
    ).fetchall()}


def _all_indexes(conn):
    return {r[0] for r in conn.execute("SELECT index_name FROM duckdb_indexes()").fetchall()}


# --- Migration 13: classification tables ---

def _make_minimal_db(tmp_path, name="minimal.duckdb"):
    """Create a minimal DB that has base tables run_migrations() ALTERs need,
    but none of the classification tables — simulating a pre-Pass-D state.

    Avoids destructive DDL (flagged by verify.sh §db-safety) by building the DB
    from scratch with only the required subset of tables.
    """
    db_path = tmp_path / name
    conn = DatabaseConnector(str(db_path))
    # Minimal schema for migrations to not error on ALTER TABLE statements:
    # holdings, transactions, trade_logs, insights, strategy_memos, sync_audit_reports,
    # sync_audit_logs, market_sentiment_cache (base — no new columns yet).
    conn.execute("CREATE TABLE IF NOT EXISTS holdings (id INTEGER, asset_id VARCHAR, snapshot_date DATE, source_system VARCHAR, is_shadow BOOLEAN, market_value DOUBLE, price_updated_at TIMESTAMP, price_source VARCHAR)")
    conn.execute("CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY, asset_id VARCHAR, is_provisional BOOLEAN)")
    conn.execute("CREATE TABLE IF NOT EXISTS trade_logs (id INTEGER, log_date DATE, currency VARCHAR, linked_memo_id INTEGER, verification_status VARCHAR, updated_at TIMESTAMP, verification_block_reason VARCHAR, linked_transaction_id INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS insights (id INTEGER, insight_date DATE, ai_model VARCHAR, content VARCHAR, category VARCHAR, insight_type VARCHAR, title VARCHAR)")
    conn.execute("CREATE TABLE IF NOT EXISTS strategy_memos (id INTEGER, content TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS sync_audit_reports (id VARCHAR PRIMARY KEY, created_at TIMESTAMP, is_no_change BOOLEAN, info_messages JSON)")
    conn.execute("CREATE TABLE IF NOT EXISTS sync_audit_logs (id INTEGER, source_system VARCHAR, conflict_type VARCHAR, is_resolved BOOLEAN, resolution_notes VARCHAR)")
    conn.execute("CREATE TABLE IF NOT EXISTS market_sentiment_cache (indicator_key VARCHAR PRIMARY KEY, section VARCHAR, indicator_name VARCHAR, value DOUBLE, display_value VARCHAR, zone VARCHAR, zone_color VARCHAR, description VARCHAR, raw_json VARCHAR, updated_at TIMESTAMP)")
    # Intentionally omit classification tables — that is the pre-Pass-D state
    return conn


def test_migration13_classification_tables_created(tmp_path):
    """Migration 13 must create all 6 classification tables on a pre-Pass-D DB.

    The pre-Pass-D DB is simulated by building a minimal schema from scratch
    (not by dropping tables — avoid verify.sh §db-safety flag).
    """
    conn = _make_minimal_db(tmp_path, "mig13.duckdb")
    tables_before = _all_tables(conn)
    for t in _CLASSIFICATION_TABLES:
        assert t not in tables_before, f"{t} should be absent before Migration 13"
    # Run migrations — Migration 13 must create them
    conn.run_migrations()
    tables_after = _all_tables(conn)
    for t in _CLASSIFICATION_TABLES:
        assert t in tables_after, f"{t} missing after Migration 13"
    conn.close()


def test_migration13_idempotent(tmp_path):
    """Running run_migrations() twice must not error on classification tables."""
    db_path = tmp_path / "mig13_idem.duckdb"
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    conn.run_migrations()
    conn.run_migrations()  # second run must be silent
    for t in _CLASSIFICATION_TABLES:
        assert t in _all_tables(conn), f"{t} missing after double migration"
    conn.close()


# --- Migration 14: sentiment columns ---

def test_migration14_sentiment_columns_added(tmp_path):
    """Migration 14 must add is_stale, last_refresh_attempt, error_detail."""
    db_path = tmp_path / "mig14.duckdb"
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    # Verify the 3 columns exist (schema.sql now has them via ALTERs at the bottom)
    # On a pre-Pass-D DB, run_migrations would add them; verify they're present
    conn.run_migrations()
    cols = _all_columns(conn, "market_sentiment_cache")
    for c in _SENTIMENT_COLUMNS:
        assert c in cols, f"market_sentiment_cache.{c} missing after Migration 14"
    conn.close()


def test_migration14_idempotent(tmp_path):
    """Running run_migrations() twice must not error on sentiment ALTERs."""
    db_path = tmp_path / "mig14_idem.duckdb"
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    conn.run_migrations()
    conn.run_migrations()
    cols = _all_columns(conn, "market_sentiment_cache")
    for c in _SENTIMENT_COLUMNS:
        assert c in cols, f"Column {c} missing after double migration"
    conn.close()


# --- Migration 15: hot-path indexes ---

def test_migration15_indexes_created(tmp_path):
    """Migration 15 must create all 4 hot-path indexes."""
    db_path = tmp_path / "mig15.duckdb"
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    conn.run_migrations()
    idxs = _all_indexes(conn)
    for idx in _HOT_PATH_INDEXES:
        assert idx in idxs, f"Index {idx} missing after Migration 15"
    conn.close()


def test_migration15_idempotent(tmp_path):
    """Running run_migrations() twice must not error on CREATE INDEX IF NOT EXISTS."""
    db_path = tmp_path / "mig15_idem.duckdb"
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    conn.run_migrations()
    conn.run_migrations()
    idxs = _all_indexes(conn)
    for idx in _HOT_PATH_INDEXES:
        assert idx in idxs, f"Index {idx} missing after double migration"
    conn.close()


# --- bootstrap_database() full bootstrap ---

def test_bootstrap_database_fresh_memory():
    """bootstrap_database() on :memory: must create all required objects."""
    conn = DatabaseConnector(":memory:")
    bootstrap_database(conn)
    tables = _all_tables(conn)
    for t in _CLASSIFICATION_TABLES:
        assert t in tables, f"Missing classification table: {t}"
    cols = _all_columns(conn, "market_sentiment_cache")
    for c in _SENTIMENT_COLUMNS:
        assert c in cols, f"Missing sentiment column: {c}"
    idxs = _all_indexes(conn)
    for idx in _HOT_PATH_INDEXES:
        assert idx in idxs, f"Missing hot-path index: {idx}"
    assert conn._migration_failures == [], f"Unexpected failures: {conn._migration_failures}"
    conn.close()


def test_bootstrap_database_populated_but_incomplete(tmp_path):
    """Migration 13 must create classification tables via the old server path.

    The old server startup called only run_migrations() — never initialize_schema().
    This test simulates that exact scenario: a DB with base tables but no
    classification tables. run_migrations() (Migration 13) must create them.

    Uses a minimal DB built from scratch to avoid destructive DDL (verify.sh §db-safety).
    """
    conn = _make_minimal_db(tmp_path, "populated_incomplete.duckdb")
    tables_before = _all_tables(conn)
    for t in _CLASSIFICATION_TABLES:
        assert t not in tables_before, f"{t} should be absent in pre-Pass-D DB"

    # Old server path — run_migrations() alone. Migration 13 must create the tables.
    conn.run_migrations()
    tables_after = _all_tables(conn)
    for t in _CLASSIFICATION_TABLES:
        assert t in tables_after, f"Missing after run_migrations() (Migration 13): {t}"
    conn.close()


def test_bootstrap_database_idempotent_data_counts(tmp_path):
    """Running bootstrap_database() twice must not change data-fix row counts.

    Guards against Codex finding F2: run_migrations() has 4 pre-existing data-fix
    statements whose results must be stable across repeated bootstrap calls.
    Uses fixed past date (2025-01-01) to avoid month-start PK collision.
    """
    db_path = tmp_path / "idem_counts.duckdb"
    conn = DatabaseConnector(str(db_path))
    bootstrap_database(conn)

    # Capture counts for tables touched by data-fix migrations
    ref_before = conn.execute("SELECT COUNT(*) FROM valuation_reference").fetchone()[0]
    links_before = conn.execute("SELECT COUNT(*) FROM insight_trade_links").fetchone()[0]

    # Second bootstrap
    bootstrap_database(conn)

    ref_after = conn.execute("SELECT COUNT(*) FROM valuation_reference").fetchone()[0]
    links_after = conn.execute("SELECT COUNT(*) FROM insight_trade_links").fetchone()[0]

    assert ref_before == ref_after, f"valuation_reference changed: {ref_before}->{ref_after}"
    assert links_before == links_after, f"insight_trade_links changed: {links_before}->{links_after}"
    conn.close()


def test_bootstrap_database_loud_fail_on_collected_failures(tmp_path):
    """_assert_bootstrap_complete must raise if _migration_failures is non-empty."""
    db_path = tmp_path / "loud_fail.duckdb"
    conn = DatabaseConnector(str(db_path))
    bootstrap_database(conn)

    # Inject a simulated non-idempotent failure
    conn._migration_failures = ["injected: TypeError: simulated failure"]
    try:
        _assert_bootstrap_complete(conn)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "injected" in str(e)
        assert "problem" in str(e).lower()
    conn.close()


def test_bootstrap_database_read_only_sentiment_get(tmp_path):
    """After bootstrap, market_sentiment_cache is queryable on a read-only connection.

    Regression guard: before Pass D, ensure_sentiment_table() was called at GET
    time, which failed on read_only=True connections (GitHub Issue #6 class).
    """
    db_path = tmp_path / "readonly_sentiment.duckdb"
    conn = DatabaseConnector(str(db_path))
    bootstrap_database(conn)
    conn.close()

    # Open read-only — SELECT must succeed (no DDL attempted)
    conn_ro = DatabaseConnector(str(db_path), read_only=True)
    try:
        rows = conn_ro.execute(
            "SELECT indicator_key, is_stale, last_refresh_attempt, error_detail "
            "FROM market_sentiment_cache LIMIT 1"
        ).fetchall()
        # Table empty but query succeeds — that's the pass condition
        assert isinstance(rows, list)
    finally:
        conn_ro.close()


def test_run_migration_helper_idempotency_safe(tmp_path):
    """_run_migration must return True and not collect failures for IF NOT EXISTS."""
    db_path = tmp_path / "run_mig_idem.duckdb"
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)

    # CREATE TABLE IF NOT EXISTS on an existing table is idempotency-safe
    ok = conn._run_migration(
        "test-idem",
        "CREATE TABLE IF NOT EXISTS holdings (id INT)",
    )
    assert ok is True
    assert conn._migration_failures == [], "Should not record as failure"
    conn.close()


def test_run_migration_helper_loud_failure(tmp_path):
    """_run_migration must return False and collect the failure for real errors."""
    db_path = tmp_path / "run_mig_loud.duckdb"
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)

    ok = conn._run_migration(
        "test-bad",
        "SELECT * FROM table_that_does_not_exist_xyz_passd",
    )
    assert ok is False
    assert len(conn._migration_failures) == 1
    assert "test-bad" in conn._migration_failures[0]
    conn.close()


# ── Pass F: Migration 16 — Drop orphaned tables ────────────────────────────────

# Step 0 investigation result: asset_taxonomy has ZERO SQL table references in
# src/ (only YAML file references and test fixtures that CREATE the table
# themselves in isolated in-memory DBs). Decision: include in Migration 16 drop.

_M16_DROPPED_TABLES = [
    "committee_decisions",
    "market_events",
    "economic_indicators",
    "exchange_rates",
    "schema_snapshots",
    "rsu_vesting_schedules",
    "source_authority_rules",
    "asset_taxonomy",
]


def test_pass_f_batch2_orphaned_tables_are_dropped(tmp_path):
    """After running bootstrap_database, the 8 orphaned tables must not exist."""
    from src.database.schema import bootstrap_database
    connector = DatabaseConnector(str(tmp_path / "test.duckdb"))
    bootstrap_database(connector)
    tables = {row[0] for row in connector.execute("SHOW TABLES").fetchall()}
    for table in _M16_DROPPED_TABLES:
        assert table not in tables, f"{table} should have been dropped by Migration 16"
    connector.close()


def test_pass_f_batch2_drop_migration_is_idempotent(tmp_path):
    """Running bootstrap_database twice must not fail (DROP IF EXISTS)."""
    from src.database.schema import bootstrap_database
    connector = DatabaseConnector(str(tmp_path / "test.duckdb"))
    bootstrap_database(connector)  # first run
    bootstrap_database(connector)  # second run — must not raise
    tables = {row[0] for row in connector.execute("SHOW TABLES").fetchall()}
    for table in _M16_DROPPED_TABLES:
        assert table not in tables, f"{table} should still be absent after second bootstrap"
    connector.close()


# ── Pass F Batch 3: schema_version ledger + idempotency + SQL splitter ─────────

def test_schema_version_table_created_on_bootstrap(tmp_path):
    """After bootstrap, schema_version table exists."""
    connector = DatabaseConnector(str(tmp_path / "test.duckdb"))
    bootstrap_database(connector)
    tables = {r[0] for r in connector.execute("SHOW TABLES").fetchall()}
    assert "schema_version" in tables
    connector.close()


def test_each_migration_recorded_exactly_once(tmp_path):
    """Each versioned migration appears exactly once in schema_version."""
    connector = DatabaseConnector(str(tmp_path / "test.duckdb"))
    bootstrap_database(connector)
    rows = connector.execute("SELECT version, label FROM schema_version ORDER BY version").fetchall()
    # Must have at least 16 rows (M1–M16) and each version is unique
    versions = [r[0] for r in rows]
    assert len(versions) == len(set(versions)), "Duplicate version entries"
    assert len(versions) >= 16, f"Expected ≥16 migrations recorded, got {len(versions)}"
    connector.close()


def test_migrations_are_idempotent_with_version_ledger(tmp_path):
    """Running bootstrap_database twice does not double-write schema_version."""
    connector = DatabaseConnector(str(tmp_path / "test.duckdb"))
    bootstrap_database(connector)
    count_1 = connector.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    bootstrap_database(connector)  # second run
    count_2 = connector.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert count_1 == count_2, "Re-running bootstrap added duplicate version entries"
    connector.close()


def test_sql_splitter_handles_semicolons_in_string_literals(tmp_path):
    """The schema.sql splitter correctly handles semicolons inside string literals."""
    # If the splitter is broken, this statement would be split incorrectly
    connector = DatabaseConnector(str(tmp_path / "test.duckdb"))
    sql = """CREATE TABLE _splitter_test (id INTEGER, note VARCHAR DEFAULT 'a;b;c')"""
    # Use the same splitter used by initialize_schema
    from src.database.schema import _split_sql_statements
    stmts = _split_sql_statements(sql)
    assert len(stmts) == 1, f"Splitter over-split: got {stmts}"
    connector.close()


# ── V62: data-fix migration — trade_logs CN_FUND_900016 → CN_FUND_110020 ─────────
# Regression note: V61 keyed on an exact `price = 2.94`, but the real stored NAV
# is 2.944 → DECIMAL equality never matched (0-row no-op) yet still burned the
# version gate.  V62 keys on (asset_id, log_date) + a wide price band.  These
# tests seed the row at the REAL price 2.944 so an exact-2.94 predicate would fail.


def _make_db_with_mislabeled_row(tmp_path, name="v62.duckdb"):
    """Return a DB that has the mislabeled CN_FUND_900016 row from 2026-06-18.

    Uses initialize_schema so all standard tables exist, then inserts the
    mislabeled trade row before running migrations.  Seeds the row at the REAL
    NAV (2.944) and the REAL fund name (示例沪深300价值指数A) so the test
    reproduces the production row exactly.
    """
    db_path = tmp_path / name
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    # Insert the mislabeled row using the natural signature.
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_trade_logs_id START 1
    """)
    conn.execute("""
        INSERT INTO trade_logs (
            log_date, asset_id, asset_name, action, price,
            quantity, verification_status, decision_reason
        ) VALUES (
            DATE '2026-06-18',
            'CN_FUND_900016',
            '示例沪深300价值指数A',
            'buy',
            2.944,
            14981.98,
            'pending',
            'test-v62'
        )
    """)
    return conn


def test_v62_migration_reassigns_mislabeled_row(tmp_path):
    """V62 must reassign the CN_FUND_900016 row to CN_FUND_110020 on run_migrations()."""
    conn = _make_db_with_mislabeled_row(tmp_path)
    conn.run_migrations()

    row = conn.execute(
        "SELECT asset_id, asset_name FROM trade_logs WHERE log_date = DATE '2026-06-18'"
    ).fetchone()
    assert row is not None, "The trade_logs row must still exist"
    assert row[0] == "CN_FUND_110020", (
        f"asset_id must be reassigned to CN_FUND_110020; got '{row[0]}'"
    )
    assert row[1] == "示例沪深300指数增强A", (
        f"asset_name must be updated; got '{row[1]}'"
    )
    conn.close()


def test_v62_migration_is_idempotent(tmp_path):
    """Running run_migrations() twice must not change the row or raise."""
    conn = _make_db_with_mislabeled_row(tmp_path, "v62_idem.duckdb")
    conn.run_migrations()
    conn.run_migrations()  # second run must be a no-op

    row = conn.execute(
        "SELECT asset_id FROM trade_logs WHERE log_date = DATE '2026-06-18'"
    ).fetchone()
    assert row is not None
    assert row[0] == "CN_FUND_110020", (
        f"asset_id must still be CN_FUND_110020 after double migration; got '{row[0]}'"
    )
    conn.close()


def test_v62_migration_matches_real_stored_precision(tmp_path):
    """Regression for the V61 bug: the row must be reassigned even though its NAV
    is 2.944 (not the 2.94 V61 keyed on).  Proves the predicate is precision-safe.
    """
    conn = _make_db_with_mislabeled_row(tmp_path, "v62_precision.duckdb")
    # Sanity: the seeded price is genuinely 2.944, which != 2.94
    price = conn.execute(
        "SELECT price FROM trade_logs WHERE log_date = DATE '2026-06-18'"
    ).fetchone()[0]
    assert float(price) == 2.944, f"seed price should be 2.944; got {price}"

    conn.run_migrations()
    row = conn.execute(
        "SELECT asset_id FROM trade_logs WHERE log_date = DATE '2026-06-18'"
    ).fetchone()
    assert row[0] == "CN_FUND_110020", (
        "V62 must reassign the 2.944 row; an exact price=2.94 predicate would miss it"
    )
    conn.close()


def test_v62_migration_safe_on_db_without_row(tmp_path):
    """V62 must not raise when no matching row exists (local dev DBs)."""
    from src.database.schema import bootstrap_database
    # Bootstrap a fresh DB — it won't have the mislabeled row
    conn = DatabaseConnector(str(tmp_path / "v62_clean.duckdb"))
    bootstrap_database(conn)  # includes run_migrations()

    # No error on a fresh DB, and the 2026-06-18 row does not exist
    row = conn.execute(
        "SELECT COUNT(*) FROM trade_logs WHERE log_date = DATE '2026-06-18' AND asset_id = 'CN_FUND_900016'"
    ).fetchone()
    assert row[0] == 0, "No mislabeled row should exist on a fresh DB"
    conn.close()

# ---------------------------------------------------------------------------
# V63 — delete phantom GOLD_nan_nan rows (2026-07-05 gold-Excel incident)
# ---------------------------------------------------------------------------

def _make_db_with_phantom_gold_rows(tmp_path, name="v63.duckdb"):
    """DB seeded with the phantom GOLD_nan_nan rows plus LEGITIMATE gold rows
    that V63 must never touch."""
    db_path = tmp_path / name
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    conn.execute("""
        INSERT INTO holdings (asset_id, asset_name, snapshot_date, quantity,
                              market_value, source_system, is_shadow)
        VALUES
            ('GOLD_nan_nan', 'GOLD_nan_nan', DATE '2026-07-05', NULL, NULL, 'Gold_Excel', FALSE),
            ('ALTS_Paper_Gold', '纸黄金', DATE '2026-07-05', 100.0, 90000.0, 'Gold_Excel', FALSE)
    """)
    conn.execute("""
        INSERT INTO asset_registry (canonical_id, display_name, asset_class)
        VALUES ('GOLD_nan_nan', 'GOLD_nan_nan', 'Alts'),
               ('ALTS_Paper_Gold', '纸黄金', 'Alts')
    """)
    return conn


def test_v63_migration_deletes_phantom_gold_rows(tmp_path):
    """V63 must delete GOLD_nan_nan from holdings and asset_registry."""
    conn = _make_db_with_phantom_gold_rows(tmp_path)
    conn.run_migrations()

    assert conn.execute(
        "SELECT COUNT(*) FROM holdings WHERE asset_id = 'GOLD_nan_nan'"
    ).fetchone()[0] == 0, "phantom holdings row must be deleted"
    assert conn.execute(
        "SELECT COUNT(*) FROM asset_registry WHERE canonical_id = 'GOLD_nan_nan'"
    ).fetchone()[0] == 0, "phantom registry row must be deleted"
    assert conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE asset_id = 'GOLD_nan_nan'"
    ).fetchone()[0] == 0
    conn.close()


def test_v63_migration_preserves_legitimate_gold_rows(tmp_path):
    """Exact-match only: legitimate gold assets must be untouched."""
    conn = _make_db_with_phantom_gold_rows(tmp_path, "v63_legit.duckdb")
    conn.run_migrations()

    assert conn.execute(
        "SELECT COUNT(*) FROM holdings WHERE asset_id = 'ALTS_Paper_Gold'"
    ).fetchone()[0] == 1, "legitimate gold holding must survive V63"
    assert conn.execute(
        "SELECT COUNT(*) FROM asset_registry WHERE canonical_id = 'ALTS_Paper_Gold'"
    ).fetchone()[0] == 1, "legitimate gold registry row must survive V63"
    conn.close()


def test_v63_migration_idempotent_and_safe_on_clean_db(tmp_path):
    """Double-run is a no-op; a DB without phantom rows migrates cleanly."""
    conn = _make_db_with_phantom_gold_rows(tmp_path, "v63_idem.duckdb")
    conn.run_migrations()
    conn.run_migrations()  # no error, no change
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 63"
    ).fetchone()[0] == 1
    conn.close()

    clean = DatabaseConnector(str(tmp_path / "v63_clean.duckdb"))
    initialize_schema(clean)
    clean.run_migrations()  # must not raise
    assert clean.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 63"
    ).fetchone()[0] == 1
    clean.close()


# ---------------------------------------------------------------------------
# V64 — re-open owner trade_logs stuck verified-without-verdict (2026-07-06)
# ---------------------------------------------------------------------------
# V64 targets rows where:
#   verification_status = 'verified' AND verdict IS NULL AND
#   COALESCE(verification_result, '') = '' AND linked_transaction_id IS NOT NULL AND
#   suggestion_source IS NOT NULL AND suggestion_source != 'imported'
# Reader/backfill rows (source NULL or 'imported') are deliberately excluded.
# ---------------------------------------------------------------------------

def _make_db_v64(tmp_path, name="v64.duckdb"):
    """DB with a set of trade_logs rows covering all V64 predicate branches."""
    db_path = tmp_path / name
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    # We need a real transaction to satisfy the linked_transaction_id FK.
    conn.execute("""
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, source_system
        ) VALUES
            (DATE '2026-04-01', 'US_STK_AAPL', 'AAPL', 'BUY', 10, 100, 1000, 'Schwab_CSV'),
            (DATE '2026-04-02', 'US_STK_MSFT', 'MSFT', 'BUY', 5, 200, 1000, 'Schwab_CSV'),
            (DATE '2026-04-03', 'US_STK_GOOG', 'GOOG', 'BUY', 2, 500, 1000, 'Schwab_CSV'),
            (DATE '2026-04-04', 'US_STK_AMZN', 'AMZN', 'BUY', 1, 800, 800, 'Schwab_CSV'),
            (DATE '2026-04-05', 'US_STK_TSLA', 'TSLA', 'BUY', 3, 250, 750, 'Schwab_CSV')
    """)
    tx_ids = conn.execute(
        "SELECT id FROM transactions ORDER BY transaction_date"
    ).fetchall()
    tx_id_a, tx_id_b, tx_id_c, tx_id_d, tx_id_e = [r[0] for r in tx_ids]

    conn.execute("""
        INSERT INTO trade_logs (
            log_date, asset_id, action, verification_status,
            verdict, verification_result, linked_transaction_id, suggestion_source
        ) VALUES
            -- (a) manual-source, verified, NULL verdict, empty result, linked → SHOULD flip to pending_window
            (DATE '2026-04-01', 'US_STK_AAPL', 'Buy', 'verified',
             NULL, NULL, ?, 'manual'),
            -- (b) imported-source, same shape → must stay verified (KPI protection)
            (DATE '2026-04-02', 'US_STK_MSFT', 'Buy', 'verified',
             NULL, NULL, ?, 'imported'),
            -- (c) NULL-source, same shape → must stay verified (KPI protection)
            (DATE '2026-04-03', 'US_STK_GOOG', 'Buy', 'verified',
             NULL, NULL, ?, NULL),
            -- (d) manual-source, verified WITH verdict → must stay verified (verdict already present)
            (DATE '2026-04-04', 'US_STK_AMZN', 'Buy', 'verified',
             'good_call', NULL, ?, 'manual'),
            -- (e) manual-source, verified, narrative non-empty, NULL verdict → stays verified
            --     (verification_result != '' so COALESCE(...) != '' predicate fails)
            (DATE '2026-04-05', 'US_STK_TSLA', 'Buy', 'verified',
             NULL, 'some narrative text', ?, 'manual')
    """, [tx_id_a, tx_id_b, tx_id_c, tx_id_d, tx_id_e])
    return conn


def test_v64_migration_reopens_owner_verified_without_verdict(tmp_path):
    """V64 must re-open manual-source verified+NULL verdict rows to pending_window."""
    conn = _make_db_v64(tmp_path)
    conn.run_migrations()

    rows = conn.execute(
        "SELECT asset_id, verification_status FROM trade_logs ORDER BY log_date"
    ).fetchall()
    by_asset = {r[0]: r[1] for r in rows}

    assert by_asset["US_STK_AAPL"] == "pending_window", (
        f"manual+verified+NULL verdict must become pending_window; got {by_asset['US_STK_AAPL']}"
    )
    assert by_asset["US_STK_MSFT"] == "verified", (
        "imported-source must stay verified (KPI protection)"
    )
    assert by_asset["US_STK_GOOG"] == "verified", (
        "NULL-source must stay verified (KPI protection)"
    )
    assert by_asset["US_STK_AMZN"] == "verified", (
        "row with verdict already set must stay verified"
    )
    assert by_asset["US_STK_TSLA"] == "verified", (
        "row with non-empty narrative must stay verified"
    )
    conn.close()


def test_v64_migration_idempotent(tmp_path):
    """Running run_migrations() twice must not change rows or raise."""
    conn = _make_db_v64(tmp_path, "v64_idem.duckdb")
    conn.run_migrations()
    conn.run_migrations()  # second run: version gate blocks re-execution

    row = conn.execute(
        "SELECT verification_status FROM trade_logs WHERE asset_id = 'US_STK_AAPL'"
    ).fetchone()
    assert row[0] == "pending_window", (
        f"after double run AAPL should still be pending_window; got {row[0]}"
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 64"
    ).fetchone()[0] == 1, "version gate must be recorded exactly once"
    conn.close()


def test_v64_migration_safe_on_clean_db(tmp_path):
    """V64 must not raise and applies cleanly on a DB with no matching rows."""
    conn = DatabaseConnector(str(tmp_path / "v64_clean.duckdb"))
    initialize_schema(conn)
    conn.run_migrations()  # no trade_logs rows → 0-row UPDATE, no error

    assert conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 64"
    ).fetchone()[0] == 1
    conn.close()


# ---------------------------------------------------------------------------
# V65 — RSU_AMZN Apr-2026 sell verdicts regret→missed_opportunity (2026-07-06)
# ---------------------------------------------------------------------------
# Local DB scored the two 2026-04-08 and 2026-04-09 RSU_AMZN Sell rows under
# pre-V7.1.8 keyword-wins semantics (verdict='regret').  Cloud re-scored under
# current numeric semantics (verdict='missed_opportunity').  Owner approved a
# one-time overwrite.  Predicate uses natural keys — no exact decimal price matches.
# ---------------------------------------------------------------------------

def _make_db_v65(tmp_path, name="v65.duckdb"):
    """DB with RSU_AMZN Sell rows at various states for V65 predicate testing."""
    db_path = tmp_path / name
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    conn.execute("""
        INSERT INTO trade_logs (
            log_date, asset_id, action, verdict, suggestion_source, verification_status
        ) VALUES
            -- Target row 1: RSU_AMZN Sell 2026-04-08 verdict='regret' → must become 'missed_opportunity'
            (DATE '2026-04-08', 'RSU_AMZN', 'Sell', 'regret', 'manual', 'verified'),
            -- Target row 2: RSU_AMZN Sell 2026-04-09 verdict='regret' → must become 'missed_opportunity'
            (DATE '2026-04-09', 'RSU_AMZN', 'Sell', 'regret', 'manual', 'verified'),
            -- Already correct: RSU_AMZN Sell 2026-04-08 with 'missed_opportunity' → unchanged (cloud shape)
            (DATE '2026-04-08', 'RSU_AMZN', 'Sell', 'missed_opportunity', 'imported', 'verified'),
            -- Non-RSU row with verdict='regret' → must be untouched
            (DATE '2026-04-08', 'US_STK_AAPL', 'Sell', 'regret', 'manual', 'verified'),
            -- Buy (not Sell) RSU_AMZN → must be untouched
            (DATE '2026-04-08', 'RSU_AMZN', 'Buy', 'regret', 'manual', 'verified')
    """)
    return conn


def test_v65_migration_updates_regret_to_missed_opportunity(tmp_path):
    """V65 must flip RSU_AMZN Sell Apr-08/09 regret→missed_opportunity (manual rows only)."""
    conn = _make_db_v65(tmp_path)
    conn.run_migrations()

    rows = conn.execute(
        """
        SELECT log_date, asset_id, action, suggestion_source, verdict
        FROM trade_logs
        ORDER BY log_date, asset_id, action, suggestion_source
        """
    ).fetchall()
    # Include suggestion_source in the key to avoid collision: two rows share
    # (2026-04-08, RSU_AMZN, Sell) — one manual (target) and one imported (control).
    lookup = {(str(r[0]), r[1], r[2], r[3]): r[4] for r in rows}

    # Manual rows must be flipped.
    assert lookup[("2026-04-08", "RSU_AMZN", "Sell", "manual")] == "missed_opportunity", (
        f"2026-04-08 RSU_AMZN Sell (manual) must be missed_opportunity; "
        f"got {lookup.get(('2026-04-08','RSU_AMZN','Sell','manual'))}"
    )
    assert lookup[("2026-04-09", "RSU_AMZN", "Sell", "manual")] == "missed_opportunity", (
        "2026-04-09 RSU_AMZN Sell (manual) must be missed_opportunity"
    )
    # Imported control row (already missed_opportunity) must stay as-is.
    assert lookup[("2026-04-08", "RSU_AMZN", "Sell", "imported")] == "missed_opportunity", (
        "imported control row must remain missed_opportunity"
    )
    # Non-RSU must be untouched.
    assert lookup[("2026-04-08", "US_STK_AAPL", "Sell", "manual")] == "regret", (
        "AAPL Sell must remain regret"
    )
    # Buy must be untouched.
    assert lookup[("2026-04-08", "RSU_AMZN", "Buy", "manual")] == "regret", (
        "RSU_AMZN Buy must remain regret"
    )
    conn.close()


def test_v65_migration_already_missed_opportunity_is_no_op(tmp_path):
    """Row already at missed_opportunity (imported source) → unchanged (0-row no-op is safe)."""
    conn = _make_db_v65(tmp_path, "v65_noop.duckdb")
    conn.run_migrations()

    # The 'imported' source row was already 'missed_opportunity' → still 'missed_opportunity'
    rows = conn.execute(
        """
        SELECT suggestion_source, verdict FROM trade_logs
        WHERE asset_id = 'RSU_AMZN' AND action = 'Sell'
          AND suggestion_source = 'imported'
        """
    ).fetchall()
    assert len(rows) >= 1, "expected at least one imported RSU_AMZN Sell row"
    assert all(r[1] == "missed_opportunity" for r in rows), (
        "already-correct imported rows must stay missed_opportunity"
    )
    conn.close()


def test_v65_migration_does_not_flip_imported_regret(tmp_path):
    """V65 Fix-3: an 'imported' row with verdict='regret' must NOT be flipped."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema

    db_path = tmp_path / "v65_imported_regret.duckdb"
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    # Seed an imported row that has verdict='regret' on the exact target dates.
    # With Fix-3's COALESCE(suggestion_source,'') != 'imported' guard this must be skipped.
    conn.execute("""
        INSERT INTO trade_logs (
            log_date, asset_id, action, verdict, suggestion_source, verification_status
        ) VALUES
            (DATE '2026-04-08', 'RSU_AMZN', 'Sell', 'regret', 'imported', 'verified'),
            (DATE '2026-04-09', 'RSU_AMZN', 'Sell', 'regret', 'imported', 'verified')
    """)
    conn.run_migrations()

    rows = conn.execute(
        """
        SELECT log_date, verdict FROM trade_logs
        WHERE asset_id = 'RSU_AMZN' AND action = 'Sell'
          AND suggestion_source = 'imported'
        ORDER BY log_date
        """
    ).fetchall()
    assert len(rows) == 2
    for log_date, verdict in rows:
        assert verdict == "regret", (
            f"imported row {log_date} must NOT be flipped by V65; got {verdict}"
        )
    conn.close()


def test_v65_migration_idempotent(tmp_path):
    """Double run_migrations() must not error and version gate fires once."""
    conn = _make_db_v65(tmp_path, "v65_idem.duckdb")
    conn.run_migrations()
    conn.run_migrations()

    assert conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 65"
    ).fetchone()[0] == 1, "version gate must be recorded exactly once"
    conn.close()


def test_v65_migration_safe_on_clean_db(tmp_path):
    """V65 must not raise on a DB with no RSU_AMZN rows (0-row no-op)."""
    conn = DatabaseConnector(str(tmp_path / "v65_clean.duckdb"))
    initialize_schema(conn)
    conn.run_migrations()  # no matching rows → safe 0-row UPDATE

    assert conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 65"
    ).fetchone()[0] == 1
    conn.close()


# ---------------------------------------------------------------------------
# V66 — re-open unlinked owner trade_logs stuck verified-without-verdict (2026-07-06)
# ---------------------------------------------------------------------------
# V66 completes V64: same shape but targets linked_transaction_id IS NULL (unlinked).
# V64 required linked_transaction_id IS NOT NULL and missed 7 unlinked owner rows
# (Feb-2026 memo/unknown trades: verified with no verdict, no narrative, no link —
# unreachable by UI and scorer).  Re-opened to pending_window.
#
# V66 targets rows where:
#   verification_status = 'verified' AND verdict IS NULL AND
#   COALESCE(TRIM(verification_result), '') = '' AND linked_transaction_id IS NULL AND
#   suggestion_source IS NOT NULL AND suggestion_source != 'imported'
# Reader/backfill rows (source NULL or 'imported') are deliberately excluded.
# Narrative-bearing rows (verification_result non-empty) are excluded.
# ---------------------------------------------------------------------------

def _make_db_v66(tmp_path, name="v66.duckdb"):
    """DB with trade_logs rows covering all V66 predicate branches (no FK required — link IS NULL)."""
    db_path = tmp_path / name
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    conn.execute("""
        INSERT INTO trade_logs (
            log_date, asset_id, action, verification_status,
            verdict, verification_result, linked_transaction_id, suggestion_source
        ) VALUES
            -- (a) unlinked owner row, verified, NULL verdict, empty result → SHOULD flip to pending_window
            (DATE '2026-02-10', 'US_STK_AAPL', 'Buy', 'verified',
             NULL, NULL, NULL, 'memo'),
            -- (b) imported-source, same shape → must stay verified (KPI protection)
            (DATE '2026-02-11', 'US_STK_MSFT', 'Buy', 'verified',
             NULL, NULL, NULL, 'imported'),
            -- (c) NULL-source, same shape → must stay verified (KPI protection)
            (DATE '2026-02-12', 'US_STK_GOOG', 'Buy', 'verified',
             NULL, NULL, NULL, NULL),
            -- (d) owner row with non-empty narrative → must stay verified (waiting for manual entry)
            (DATE '2026-02-13', 'US_STK_AMZN', 'Buy', 'verified',
             NULL, 'some narrative text', NULL, 'memo'),
            -- (e) owner row with verdict already set → must stay verified
            (DATE '2026-02-14', 'US_STK_TSLA', 'Buy', 'verified',
             'good_call', NULL, NULL, 'memo')
    """)
    return conn


def test_v66_migration_reopens_unlinked_owner_verified_without_verdict(tmp_path):
    """V66 must re-open unlinked owner-source verified+NULL-verdict rows to pending_window."""
    conn = _make_db_v66(tmp_path)
    conn.run_migrations()

    rows = conn.execute(
        "SELECT asset_id, verification_status FROM trade_logs ORDER BY log_date"
    ).fetchall()
    by_asset = {r[0]: r[1] for r in rows}

    assert by_asset["US_STK_AAPL"] == "pending_window", (
        f"unlinked memo+verified+NULL verdict must become pending_window; got {by_asset['US_STK_AAPL']}"
    )
    assert by_asset["US_STK_MSFT"] == "verified", (
        "imported-source must stay verified (KPI protection)"
    )
    assert by_asset["US_STK_GOOG"] == "verified", (
        "NULL-source must stay verified (KPI protection)"
    )
    assert by_asset["US_STK_AMZN"] == "verified", (
        "row with non-empty narrative must stay verified"
    )
    assert by_asset["US_STK_TSLA"] == "verified", (
        "row with verdict already set must stay verified"
    )
    conn.close()


def test_v66_linked_owner_row_is_untouched(tmp_path):
    """Linked owner rows (V64 scope) are untouched by V66 — V64 already moved them."""
    db_path = tmp_path / "v66_linked.duckdb"
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    # Insert a real transaction to satisfy the FK for the linked row.
    conn.execute("""
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, source_system
        ) VALUES (DATE '2026-03-01', 'US_STK_AAPL', 'AAPL', 'BUY', 10, 100, 1000, 'Schwab_CSV')
    """)
    tx_id = conn.execute("SELECT id FROM transactions LIMIT 1").fetchone()[0]
    conn.execute("""
        INSERT INTO trade_logs (
            log_date, asset_id, action, verification_status,
            verdict, verification_result, linked_transaction_id, suggestion_source
        ) VALUES (DATE '2026-03-01', 'US_STK_AAPL', 'Buy', 'verified',
                  NULL, NULL, ?, 'manual')
    """, [tx_id])
    conn.run_migrations()

    # V64 fires first and moves the linked row to pending_window; V66 predicate
    # (linked_transaction_id IS NULL) does not match it → V66 is a no-op for this row.
    row = conn.execute(
        "SELECT verification_status FROM trade_logs"
    ).fetchone()
    assert row[0] == "pending_window", (
        f"linked owner row is V64 scope and must be pending_window after full migration run; got {row[0]}"
    )
    conn.close()


def test_v66_migration_idempotent(tmp_path):
    """Running run_migrations() twice must not change rows or raise."""
    conn = _make_db_v66(tmp_path, "v66_idem.duckdb")
    conn.run_migrations()
    conn.run_migrations()  # second run: version gate blocks re-execution

    row = conn.execute(
        "SELECT verification_status FROM trade_logs WHERE asset_id = 'US_STK_AAPL'"
    ).fetchone()
    assert row[0] == "pending_window", (
        f"after double run AAPL should still be pending_window; got {row[0]}"
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 66"
    ).fetchone()[0] == 1, "version gate must be recorded exactly once"
    conn.close()


def test_v66_migration_safe_on_clean_db(tmp_path):
    """V66 must not raise on a DB with no matching rows (0-row no-op)."""
    conn = DatabaseConnector(str(tmp_path / "v66_clean.duckdb"))
    initialize_schema(conn)
    conn.run_migrations()  # no trade_logs rows → 0-row UPDATE, no error

    assert conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 66"
    ).fetchone()[0] == 1
    conn.close()


# ---------------------------------------------------------------------------
# V67 — Migration 010: process-based verification schema foundation (2026-07-07)
# F1.1 (PRD process-verification program, Batch B1): additive trade_logs columns
# for bucket-aware process verification. See
# src/database/migrations/010_process_verification_schema.sql.
# ---------------------------------------------------------------------------

_V67_NEW_COLUMNS = [
    "rule_bucket", "memo_id", "order_origin",
    "process_authorized", "process_params_ok", "process_data_verified",
    "process_checked_at", "process_notes", "verdict_archived",
]


def _make_db_v67(tmp_path, name="v67.duckdb"):
    db_path = tmp_path / name
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    return conn


def test_v67_migration_adds_all_columns(tmp_path):
    """All 9 new trade_logs columns must exist after Migration 010."""
    conn = _make_db_v67(tmp_path)
    conn.run_migrations()
    cols = _all_columns(conn, "trade_logs")
    for c in _V67_NEW_COLUMNS:
        assert c in cols, f"trade_logs.{c} missing after Migration 010 (V67)"
    conn.close()


def test_v67_migration_copies_verdict_to_verdict_archived(tmp_path):
    """Existing verdict values must be copied into verdict_archived, never destroyed."""
    conn = _make_db_v67(tmp_path, "v67_archive.duckdb")
    conn.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verdict)
        VALUES (DATE '2026-04-08', 'RSU_AMZN', 'Sell', 'missed_opportunity')
    """)
    conn.run_migrations()

    row = conn.execute(
        "SELECT verdict, verdict_archived FROM trade_logs WHERE asset_id = 'RSU_AMZN'"
    ).fetchone()
    assert row[0] == "missed_opportunity", "original verdict must be untouched"
    assert row[1] == "missed_opportunity", "verdict_archived must be copied from verdict"
    conn.close()


def test_v67_migration_leaves_null_verdict_rows_alone(tmp_path):
    """Rows with no verdict must not get a spurious verdict_archived value."""
    conn = _make_db_v67(tmp_path, "v67_null.duckdb")
    conn.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action)
        VALUES (DATE '2026-04-08', 'US_STK_MSFT', 'Buy')
    """)
    conn.run_migrations()

    row = conn.execute(
        "SELECT verdict, verdict_archived FROM trade_logs WHERE asset_id = 'US_STK_MSFT'"
    ).fetchone()
    assert row[0] is None
    assert row[1] is None
    conn.close()


def test_v67_migration_idempotent(tmp_path):
    """Running run_migrations() twice must not raise, corrupt columns, or
    re-archive an already-archived verdict differently.

    Uses an asset/date/verdict combination that is deliberately outside every
    other data-fix migration's predicate (V62/V64/V65/V66 all key on specific
    natural keys) so this test is not accidentally coupled to unrelated
    migrations mutating the seeded verdict.
    """
    conn = _make_db_v67(tmp_path, "v67_idem.duckdb")
    conn.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verdict)
        VALUES (DATE '2025-01-15', 'US_STK_GOOGL', 'Sell', 'regret')
    """)
    conn.run_migrations()
    conn.run_migrations()  # second run must be silent and not re-run the backfill

    cols = _all_columns(conn, "trade_logs")
    for c in _V67_NEW_COLUMNS:
        assert c in cols, f"trade_logs.{c} missing after double migration"

    row = conn.execute(
        "SELECT verdict, verdict_archived FROM trade_logs WHERE asset_id = 'US_STK_GOOGL'"
    ).fetchone()
    assert row[0] == "regret"
    assert row[1] == "regret"

    assert conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 67"
    ).fetchone()[0] == 1, "version gate must be recorded exactly once"
    conn.close()


def test_v67_migration_never_overwrites_manually_archived_verdict(tmp_path):
    """If verdict_archived is already set (e.g. from a prior manual archive or
    a differing prior verdict), the backfill UPDATE must not clobber it — the
    guard is `verdict_archived IS NULL`."""
    conn = _make_db_v67(tmp_path, "v67_guard.duckdb")
    conn.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verdict)
        VALUES (DATE '2026-04-08', 'RSU_AMZN', 'Sell', 'missed_opportunity')
    """)
    conn.run_migrations()
    # Simulate a later verdict change that should NOT re-propagate to the archive.
    conn.execute(
        "UPDATE trade_logs SET verdict = 'good_call' WHERE asset_id = 'RSU_AMZN'"
    )
    conn.run_migrations()  # version-gated — must not re-run the archive UPDATE

    row = conn.execute(
        "SELECT verdict, verdict_archived FROM trade_logs WHERE asset_id = 'RSU_AMZN'"
    ).fetchone()
    assert row[0] == "good_call"
    assert row[1] == "missed_opportunity", (
        "verdict_archived must retain the originally-archived value"
    )
    conn.close()


def test_v67_migration_safe_on_clean_db_with_no_trade_logs_rows(tmp_path):
    """Migration 010 must not raise on a DB with zero trade_logs rows."""
    conn = _make_db_v67(tmp_path, "v67_clean.duckdb")
    conn.run_migrations()

    assert conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 67"
    ).fetchone()[0] == 1
    conn.close()


# ---------------------------------------------------------------------------
# V69 — Migration 012: metric governance (2026-07-07)
# F4.3/F4.4/F4.6 (PRD process-verification program, Batch B5): metric_catalog,
# data_fixes, ruling_deferred_events + additive market_sentiment_cache
# columns. See src/database/migrations/012_metric_governance.sql.
# ---------------------------------------------------------------------------

def _make_db_v69(tmp_path, name="v69.duckdb"):
    db_path = tmp_path / name
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    return conn


def test_v69_migration_creates_governance_tables(tmp_path):
    conn = _make_db_v69(tmp_path)
    conn.run_migrations()
    tables = {
        row[0] for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    for expected in ("metric_catalog", "data_fixes", "ruling_deferred_events"):
        assert expected in tables, f"{expected} missing after Migration 012 (V69)"

    cols = _all_columns(conn, "market_sentiment_cache")
    assert "methodology" in cols
    assert "data_source" in cols
    conn.close()


def test_v69_migration_seeds_metric_catalog(tmp_path):
    """6 metric_catalog rows per PRD F4.6. data_fixes backlog itself is no
    longer seeded by run_migrations() (Program OSR WS-3c moved it to
    src.database.seed_loader.seed_demo_content(), called from
    bootstrap_database() and gated on $UIS_SEED_PROFILE) — metric_catalog
    stays here because it's generic product schema, not owner content."""
    conn = _make_db_v69(tmp_path, "v69_seeds.duckdb")
    conn.run_migrations()

    catalog_keys = {
        row[0] for row in conn.execute("SELECT metric_key FROM metric_catalog").fetchall()
    }
    assert catalog_keys == {
        "buffett_indicator", "csi500_pe", "vix", "fx_usd_cny",
        "sp500_pe_percentile", "rebalance_discipline",
    }

    # run_migrations() alone (no seed_demo_content()) must not populate data_fixes.
    statuses = [row[0] for row in conn.execute("SELECT status FROM data_fixes").fetchall()]
    assert len(statuses) == 0
    conn.close()


def test_v69_migration_idempotent(tmp_path):
    """Running run_migrations() twice must not raise or duplicate seeds."""
    conn = _make_db_v69(tmp_path, "v69_idem.duckdb")
    conn.run_migrations()
    conn.run_migrations()  # second run must be silent, no duplicate seeds

    assert conn.execute(
        "SELECT COUNT(*) FROM metric_catalog"
    ).fetchone()[0] == 6
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 69"
    ).fetchone()[0] == 1, "version gate must be recorded exactly once"
    conn.close()


# ── V79: Security Transfer typing (Attribution & Flows WS-3.1) ──────────────
# Schwab's 'Security Transfer' action had no action_map entry (fell through to
# 'other'), and is directionally ambiguous — the pseudo-type 'transfer' is
# seeded (action_map) and resolved to transfer_out/transfer_in by quantity
# sign at the reader hook. V79 also heals the 3 REAL rows already stored in
# production before this mapping existed. See src/database/connector.py V79
# and src/services/north_star_flows.py's security_transfer_pair rule.


def _make_db_with_untyped_acat_rows(tmp_path, name="v79.duckdb"):
    """Return a DB (schema only, no migrations run yet) seeded with the 3
    REAL stored Schwab ACAT-out rows V79's heal predicate targets — exact
    values verified in production 2026-07-19 (Jun-9 2026, amount 0.00)."""
    db_path = tmp_path / name
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    rows = [
        ("2026-06-09", "US_STK_VOO", -21.0),
        ("2026-06-09", "US_STK_IEF", -172.0),
        ("2026-06-09", "US_STK_SGOV", -200.0),
    ]
    for tx_date, asset_id, qty in rows:
        conn.execute(
            """
            INSERT INTO transactions
                (transaction_date, asset_id, asset_name, transaction_type,
                 quantity, amount_gross, amount_net, source_system, is_provisional)
            VALUES (?, ?, ?, 'other', ?, 0.00, 0.00, 'Schwab_CSV', FALSE)
            """,
            [tx_date, asset_id, asset_id, qty],
        )
    return conn


def test_v79_migration_retypes_all_three_acat_rows(tmp_path):
    """The 3 real rows must be retyped by quantity sign: all three are
    negative (ACAT-out of Schwab) -> transfer_out."""
    conn = _make_db_with_untyped_acat_rows(tmp_path)
    conn.run_migrations()

    rows = conn.execute(
        "SELECT asset_id, transaction_type, quantity FROM transactions "
        "WHERE source_system = 'Schwab_CSV' AND transaction_date = DATE '2026-06-09' "
        "ORDER BY asset_id"
    ).fetchall()
    by_asset = {r[0]: (r[1], float(r[2])) for r in rows}
    assert by_asset["US_STK_IEF"] == ("transfer_out", -172.0)
    assert by_asset["US_STK_SGOV"] == ("transfer_out", -200.0)
    assert by_asset["US_STK_VOO"] == ("transfer_out", -21.0)
    conn.close()


def test_v79_migration_positive_quantity_resolves_transfer_in(tmp_path):
    """Sanity check on the sign branch itself: a positive-quantity 'other'/$0
    Schwab row (ACAT-in) must resolve to transfer_in, not transfer_out."""
    db_path = tmp_path / "v79_in.duckdb"
    conn = DatabaseConnector(str(db_path))
    initialize_schema(conn)
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type,
             quantity, amount_gross, amount_net, source_system, is_provisional)
        VALUES (DATE '2026-06-09', 'US_STK_AAPL', 'US_STK_AAPL', 'other', 10.0, 0.00, 0.00, 'Schwab_CSV', FALSE)
        """
    )
    conn.run_migrations()
    row = conn.execute(
        "SELECT transaction_type FROM transactions WHERE asset_id = 'US_STK_AAPL'"
    ).fetchone()
    assert row[0] == "transfer_in"
    conn.close()


def test_v79_migration_idempotent_second_run_no_op(tmp_path):
    """Running run_migrations() twice must not raise or re-flip already-healed rows."""
    conn = _make_db_with_untyped_acat_rows(tmp_path, "v79_idem.duckdb")
    conn.run_migrations()
    conn.run_migrations()  # second run must be a no-op

    rows = conn.execute(
        "SELECT transaction_type FROM transactions WHERE source_system = 'Schwab_CSV'"
    ).fetchall()
    assert {r[0] for r in rows} == {"transfer_out"}
    conn.close()


def test_v79_mapping_row_present_exactly_once_after_two_runs(tmp_path):
    conn = _make_db_with_untyped_acat_rows(tmp_path, "v79_mapping.duckdb")
    conn.run_migrations()
    conn.run_migrations()

    count = conn.execute(
        "SELECT COUNT(*) FROM reader_mappings WHERE reader_key = 'schwab' "
        "AND mapping_kind = 'action_map' AND map_key = 'Security Transfer'"
    ).fetchone()[0]
    assert count == 1

    value = conn.execute(
        "SELECT map_value FROM reader_mappings WHERE reader_key = 'schwab' "
        "AND mapping_kind = 'action_map' AND map_key = 'Security Transfer'"
    ).fetchone()[0]
    assert value == '{"type": "transfer"}'
    conn.close()


def test_v79_migration_recorded(tmp_path):
    conn = _make_db_with_untyped_acat_rows(tmp_path, "v79_recorded.duckdb")
    conn.run_migrations()
    row = conn.execute("SELECT label FROM schema_version WHERE version = 79").fetchone()
    assert row is not None
    conn.close()


def test_v79_migration_safe_on_clean_db(tmp_path):
    """No matching 'other'/$0 Schwab rows on a fresh DB — bootstrap must not raise."""
    conn = DatabaseConnector(str(tmp_path / "v79_clean.duckdb"))
    bootstrap_database(conn)  # includes run_migrations()
    row = conn.execute(
        "SELECT COUNT(*) FROM transactions "
        "WHERE source_system = 'Schwab_CSV' AND transaction_type = 'other'"
    ).fetchone()
    assert row[0] == 0
    conn.close()


def test_v79_migration_does_not_touch_unrelated_other_rows(tmp_path):
    """A genuine 'other' row with a real nonzero amount (e.g. Wire Transfer)
    must NOT be retyped — the heal predicate requires amount ~= 0 AND qty != 0."""
    conn = _make_db_with_untyped_acat_rows(tmp_path, "v79_unrelated.duckdb")
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type,
             quantity, amount_gross, amount_net, source_system, is_provisional)
        VALUES (DATE '2026-06-01', 'CASH_USD', 'CASH_USD', 'other', 0.0, 500.00, 500.00, 'Schwab_CSV', FALSE)
        """
    )
    conn.run_migrations()
    row = conn.execute(
        "SELECT transaction_type FROM transactions "
        "WHERE asset_id = 'CASH_USD' AND transaction_date = DATE '2026-06-01'"
    ).fetchone()
    assert row[0] == "other"
    conn.close()


def test_v79_migration_does_not_touch_non_schwab_source(tmp_path):
    """A matching-shaped 'other'/$0 row from a different source_system (e.g.
    IBKR, already typed correctly upstream) must not be retyped — the heal
    is scoped to source_system = 'Schwab_CSV' only."""
    conn = _make_db_with_untyped_acat_rows(tmp_path, "v79_non_schwab.duckdb")
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type,
             quantity, amount_gross, amount_net, source_system, is_provisional)
        VALUES (DATE '2026-06-09', 'US_STK_VOO', 'US_STK_VOO', 'other', -5.0, 0.00, 0.00, 'Broker_IBKR', FALSE)
        """
    )
    conn.run_migrations()
    row = conn.execute(
        "SELECT transaction_type FROM transactions "
        "WHERE source_system = 'Broker_IBKR' AND asset_id = 'US_STK_VOO'"
    ).fetchone()
    assert row[0] == "other"
    conn.close()


# ── Migration V80: attribution_monthly (Attribution & Flows WS-1) ──────────

def test_v80_attribution_monthly_table_exists(tmp_path):
    connector = _make_db(tmp_path)
    cols = connector.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'attribution_monthly'
        ORDER BY column_name
        """
    ).fetchall()
    col_names = {r[0] for r in cols}
    required = {
        "id", "month", "asset_id", "mv_start", "mv_end", "price_effect",
        "trade_effect", "transfer_effect", "income_effect", "residual",
        "dq_flag", "computed_at",
    }
    assert required.issubset(col_names), f"attribution_monthly missing columns: {required - col_names}"
    connector.close()


def test_v80_attribution_monthly_unique_month_asset(tmp_path):
    connector = _make_db(tmp_path)
    connector.execute(
        "INSERT INTO asset_registry (canonical_id, display_name, asset_class) VALUES ('TEST_A', 'Test A', 'Equity')"
    )
    connector.execute(
        """
        INSERT INTO attribution_monthly (month, asset_id, mv_start, mv_end)
        VALUES (DATE '2026-06-01', 'TEST_A', 100, 110)
        """
    )
    with __import__("pytest").raises(Exception):
        connector.execute(
            """
            INSERT INTO attribution_monthly (month, asset_id, mv_start, mv_end)
            VALUES (DATE '2026-06-01', 'TEST_A', 100, 120)
            """
        )
    connector.close()


def test_v80_migration_idempotent(tmp_path):
    db_path = tmp_path / "test_v80_idem.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    connector.run_migrations()
    connector.run_migrations()  # must not raise
    row = connector.execute("SELECT 1 FROM schema_version WHERE version = 80").fetchone()
    assert row is not None
    connector.close()


# ── Migration V81: cash_flow_tags stable natural key ────────────────────────
# Root cause: source_row_key stored transactions.id, but _replace_transactions
# deletes+reinserts rows on every sync (ids regenerate) — orphaning every tag.
# V81 re-keys existing tags to a stable natural key
# (source_system|date|asset_id|type|amount_gross) and relinks unambiguous
# orphans, without ever deleting owner data. See connector.py V81 block.

def _insert_v81_transaction(
    connector, *, asset_id: str, tx_date_sql: str, tx_type: str = "transfer_in",
    amount: float = 5000.00, source_system: str = "test",
) -> int:
    connector.execute(
        f"""
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type,
             amount_gross, amount_net, currency, source_system, is_provisional)
        VALUES (DATE '{tx_date_sql}', ?, ?, ?, ?, ?, 'CNY', ?, FALSE)
        """,
        [asset_id, asset_id, tx_type, amount, amount, source_system],
    )
    return connector.execute(
        "SELECT id FROM transactions WHERE asset_id = ? AND transaction_date = CAST(? AS DATE) "
        "ORDER BY id DESC LIMIT 1",
        [asset_id, tx_date_sql],
    ).fetchone()[0]


def _insert_v81_tag(
    connector, *, source_row_key: str, flow_date_sql: str,
    classification: str = "external_contribution", tagged_by: str = "manual",
    amount_cny: float = 5000.00,
) -> None:
    connector.execute(
        f"""
        INSERT INTO cash_flow_tags
            (source_table, source_row_key, classification, tagged_by, amount_cny, flow_date)
        VALUES ('transactions', ?, ?, ?, ?, DATE '{flow_date_sql}')
        """,
        [source_row_key, classification, tagged_by, amount_cny],
    )


def test_v81_migration_recorded(tmp_path):
    """V81 runs (and is recorded) even on a clean DB with no cash_flow_tags rows."""
    connector = _make_db(tmp_path)
    row = connector.execute("SELECT label FROM schema_version WHERE version = 81").fetchone()
    assert row is not None
    connector.close()


def test_v81_rekeys_live_tag_to_natural_key(tmp_path):
    """A tag whose source_row_key still resolves to a live transactions.id is
    re-keyed to that row's stable natural key."""
    db_path = tmp_path / "v81_rekey.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    tx_id = _insert_v81_transaction(
        connector, asset_id="CN_FUND_000001", tx_date_sql="2026-06-01", amount=5000.00,
    )
    _insert_v81_tag(connector, source_row_key=str(tx_id), flow_date_sql="2026-06-01")

    connector.run_migrations()

    row = connector.execute(
        "SELECT source_row_key FROM cash_flow_tags WHERE classification = 'external_contribution'"
    ).fetchone()
    assert row is not None
    expected = compose_natural_key("test", "2026-06-01", "CN_FUND_000001", "transfer_in", 5000.00)
    assert row[0] == expected
    assert row[0].startswith("nk:")
    connector.close()


def test_v81_migration_idempotent(tmp_path):
    """Running run_migrations() twice must not re-key twice, duplicate rows,
    or raise (ON CONFLICT / STARTS_WITH guards)."""
    db_path = tmp_path / "v81_idem.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    tx_id = _insert_v81_transaction(
        connector, asset_id="CN_FUND_IDEM", tx_date_sql="2026-06-02", amount=3000.00,
    )
    _insert_v81_tag(connector, source_row_key=str(tx_id), flow_date_sql="2026-06-02", amount_cny=3000.00)

    connector.run_migrations()
    row1 = connector.execute("SELECT source_row_key FROM cash_flow_tags").fetchone()[0]
    connector.run_migrations()  # second run — must be a no-op
    row2 = connector.execute("SELECT source_row_key FROM cash_flow_tags").fetchone()[0]

    assert row1 == row2
    count = connector.execute("SELECT COUNT(*) FROM cash_flow_tags").fetchone()[0]
    assert count == 1, "idempotent re-run must not create a duplicate tag row"
    connector.close()


def test_v81_relinks_orphan_with_unique_date_match(tmp_path):
    """An orphan tag (source_row_key matches no live transactions.id) whose
    flow_date matches EXACTLY ONE live transaction is relinked to that row's
    natural key."""
    db_path = tmp_path / "v81_relink.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    _insert_v81_transaction(
        connector, asset_id="CN_FUND_000002", tx_date_sql="2026-06-05", amount=8000.00,
    )
    # Orphan: '99999' does not match any transactions.id, but flow_date is
    # the single live transaction's date.
    _insert_v81_tag(connector, source_row_key="99999", flow_date_sql="2026-06-05", amount_cny=8000.00)

    connector.run_migrations()

    row = connector.execute(
        "SELECT source_row_key FROM cash_flow_tags WHERE classification = 'external_contribution'"
    ).fetchone()
    expected = compose_natural_key("test", "2026-06-05", "CN_FUND_000002", "transfer_in", 8000.00)
    assert row[0] == expected
    connector.close()


def test_v81_leaves_ambiguous_date_orphan_untouched(tmp_path):
    """An orphan tag whose flow_date matches MORE THAN ONE live transaction
    must be left alone — never guessed which one it was."""
    db_path = tmp_path / "v81_ambiguous.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    for asset in ("CN_FUND_A", "CN_FUND_B"):
        _insert_v81_transaction(
            connector, asset_id=asset, tx_date_sql="2026-06-07", amount=1000.00,
        )
    _insert_v81_tag(connector, source_row_key="88888", flow_date_sql="2026-06-07", amount_cny=1000.00)

    connector.run_migrations()

    row = connector.execute(
        "SELECT source_row_key FROM cash_flow_tags WHERE classification = 'external_contribution'"
    ).fetchone()
    assert row[0] == "88888", "ambiguous-date orphan must be left under its original key"
    connector.close()


def test_v81_leaves_unresolvable_orphan_untouched_and_not_deleted(tmp_path):
    """An orphan with no flow_date match anywhere is NEVER deleted — it's
    owner data. Confirms the migration is additive-only on cash_flow_tags."""
    db_path = tmp_path / "v81_unresolvable.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)
    _insert_v81_tag(
        connector, source_row_key="77777", flow_date_sql="2020-01-01",
        classification="income_reinvested", amount_cny=3000.00,
    )

    connector.run_migrations()

    row = connector.execute(
        "SELECT source_row_key, classification FROM cash_flow_tags"
    ).fetchone()
    assert row == ("77777", "income_reinvested")
    count = connector.execute("SELECT COUNT(*) FROM cash_flow_tags").fetchone()[0]
    assert count == 1
    connector.close()
