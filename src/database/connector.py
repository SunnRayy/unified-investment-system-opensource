"""DuckDB database connector for Huinsight."""

import duckdb
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/unified.duckdb"


def project_root() -> Path:
    """Resolve canonical project root, normalizing worktree paths."""
    override = os.getenv("UIS_PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    connector_file = Path(__file__).resolve()
    if ".worktrees" in connector_file.parts:
        idx = connector_file.parts.index(".worktrees")
        return Path(*connector_file.parts[:idx])

    return connector_file.parents[2]


def resolve_db_path(db_path: str = DEFAULT_DB_PATH) -> str:
    """Resolve DB path with worktree-safe defaults and env override support."""
    if db_path == ":memory:":
        return db_path

    raw_path = Path(db_path).expanduser()
    if raw_path.is_absolute():
        return str(raw_path.resolve())

    if db_path == DEFAULT_DB_PATH:
        env_override = os.getenv("UIS_DB_PATH")
        if env_override:
            return str(Path(env_override).expanduser().resolve())
        return str((project_root() / DEFAULT_DB_PATH).resolve())

    return db_path


class DatabaseConnector:
    """Manages DuckDB database connection."""
    
    def __init__(self, db_path: str = DEFAULT_DB_PATH, read_only: bool = False):
        """
        Initialize database connection.

        Args:
            db_path: Path to DuckDB database file. Use ":memory:" for in-memory.
            read_only: If True, open database in read-only mode for concurrent access.
        """
        self.db_path = resolve_db_path(db_path)
        self.read_only = read_only
        self._connection: Optional[duckdb.DuckDBPyConnection] = None
        # Populated by run_migrations() for post-bootstrap assertion.
        self._migration_failures: list = []
        self._connect()

    def _connect(self) -> None:
        """Establish database connection."""
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(self.db_path, read_only=self.read_only)
    
    def is_connected(self) -> bool:
        """Check if database connection is active."""
        return self._connection is not None
    
    def execute(self, query: str, params: tuple = ()) -> Any:
        """Execute a SQL query."""
        if not self._connection:
            raise RuntimeError("Database not connected")
        return self._connection.execute(query, params)
    
    def executemany(self, query: str, params_list: list) -> None:
        """Execute a SQL query with multiple parameter sets."""
        if not self._connection:
            raise RuntimeError("Database not connected")
        self._connection.executemany(query, params_list)
    
    def __enter__(self) -> "DatabaseConnector":
        """
        Context manager entry point.
        
        Enables usage with 'with' statement for automatic resource cleanup:
            with DatabaseConnector("data/db.duckdb") as conn:
                conn.execute("SELECT ...")
            # Connection automatically closed when exiting the block
        
        Returns:
            Self for use in the with block
        """
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """
        Context manager exit point.
        
        Automatically closes the database connection when exiting
        the 'with' block, even if an exception occurred.
        
        Args:
            exc_type: Exception type if an error occurred, None otherwise
            exc_val: Exception value if an error occurred, None otherwise
            exc_tb: Exception traceback if an error occurred, None otherwise
            
        Returns:
            False to propagate any exceptions (does not suppress them)
        """
        self.close()
        return False
    
    def close(self) -> None:
        """Close database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def _run_migration(self, label: str, stmt: str) -> bool:
        """Run a single migration statement with loud-failure semantics.

        Catches DuckDB "already exists" / "duplicate column" errors silently —
        those are expected idempotency.  Any *other* failure is logged as a
        warning and appended to ``self._migration_failures`` so the
        post-bootstrap assertion in ``bootstrap_database()`` can raise before
        the server begins serving traffic.

        Args:
            label: Human-readable identifier used in log messages.
            stmt:  SQL statement to execute.

        Returns:
            True if the statement succeeded or was safely idempotent.
            False if a non-idempotent failure occurred (also logged + collected).
        """
        try:
            self.execute(stmt)
            return True
        except Exception as e:
            msg = str(e).lower()
            if any(phrase in msg for phrase in (
                "already exists",
                "duplicate column",
                "column already exists",
            )):
                return True  # safe idempotency — not a real failure
            logger.warning("Migration [%s] failed: %s", label, e, exc_info=True)
            self._migration_failures.append(f"{label}: {type(e).__name__}: {e}")
            return False

    def _record_migration(self, version: int, label: str) -> None:
        """Record a migration in schema_version if not already present (idempotent)."""
        already = self.execute(
            "SELECT 1 FROM schema_version WHERE version = ?", [version]
        ).fetchone()
        if not already:
            self.execute(
                "INSERT INTO schema_version (version, label) VALUES (?, ?)",
                [version, label],
            )

    def _apply_versioned_migration(self, version: int, label: str, stmt: str) -> bool:
        """Apply a migration only if schema_version doesn't already record it.

        Uses ``_run_migration`` for loud-failure semantics (non-idempotent errors
        are collected for the post-bootstrap assertion).  On success the version
        is recorded in ``schema_version`` so the statement is never re-run.

        Args:
            version: Monotonically-increasing integer version number.
            label:   Human-readable label stored in schema_version.
            stmt:    SQL statement to execute.

        Returns:
            True if the migration was already applied, or applied successfully.
            False if a non-idempotent failure occurred.
        """
        already_applied = self.execute(
            "SELECT 1 FROM schema_version WHERE version = ?", [version]
        ).fetchone()
        if already_applied:
            return True  # idempotent skip
        ok = self._run_migration(label, stmt)
        if ok:
            self.execute(
                "INSERT INTO schema_version (version, label) VALUES (?, ?)",
                [version, label],
            )
        return ok

    def run_migrations(self) -> None:
        """Run schema migrations for existing databases."""
        self._migration_failures = []  # reset for this bootstrap run

        # Version ledger — must exist before any versioned migration check.
        # CREATE TABLE IF NOT EXISTS is unconditional and safe on every run.
        self.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version    INTEGER PRIMARY KEY,
                label      VARCHAR NOT NULL,
                applied_at TIMESTAMP DEFAULT now()
            )
        """)

        # ── V1: holdings.price_updated_at ──────────────────────────────────────
        self._apply_versioned_migration(
            1, "V1 holdings.price_updated_at",
            "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS price_updated_at TIMESTAMP",
        )

        # ── V2: transactions.is_provisional ────────────────────────────────────
        self._apply_versioned_migration(
            2, "V2 transactions.is_provisional",
            "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS is_provisional BOOLEAN DEFAULT FALSE",
        )

        # ── V3: insights.title + backfill ──────────────────────────────────────
        # Multi-statement block — gate on version, then execute, then record.
        already_v3 = self.execute("SELECT 1 FROM schema_version WHERE version = 3").fetchone()
        if not already_v3:
            ok_v3 = True
            for _stmt in [
                "ALTER TABLE insights ADD COLUMN IF NOT EXISTS title VARCHAR(200)",
                """UPDATE insights
                   SET title = CASE
                       WHEN length(content) > 50 THEN substr(content, 1, 47) || '...'
                       ELSE content
                   END
                   WHERE title IS NULL AND content IS NOT NULL""",
            ]:
                if not self._run_migration("V3 insights.title", _stmt):
                    ok_v3 = False
            if ok_v3:
                self._record_migration(3, "V3 insights.title+backfill")

        # ── V4: Migration 008 — AI advisor base tables (file) ──────────────────
        already_v4 = self.execute("SELECT 1 FROM schema_version WHERE version = 4").fetchone()
        if not already_v4:
            migration_path = Path(__file__).parent / "migrations" / "008_ai_advisor_tables.sql"
            ok_v4 = self._run_migration("V4 008_ai_advisor_tables", migration_path.read_text(encoding="utf-8"))
            if ok_v4:
                self._record_migration(4, "V4 008_ai_advisor_tables")

        # ── V5: Migration 009 — ai_reports debug columns (file) ────────────────
        already_v5 = self.execute("SELECT 1 FROM schema_version WHERE version = 5").fetchone()
        if not already_v5:
            migration_path = Path(__file__).parent / "migrations" / "009_ai_reports_debug_columns.sql"
            ok_v5 = self._run_migration("V5 009_ai_reports_debug_columns", migration_path.read_text(encoding="utf-8"))
            if ok_v5:
                self._record_migration(5, "V5 009_ai_reports_debug_columns")

        # ── V6: strategy_memos.content (V4.5-1) ────────────────────────────────
        # Has a RuntimeError re-raise guard — keep that, but wrap with version gate.
        already_v6 = self.execute("SELECT 1 FROM schema_version WHERE version = 6").fetchone()
        if not already_v6:
            try:
                self.execute("ALTER TABLE strategy_memos ADD COLUMN IF NOT EXISTS content TEXT")
                cols = [r[0] for r in self.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='strategy_memos'"
                ).fetchall()]
                if 'content' not in cols:
                    raise RuntimeError("V6 migration failed: strategy_memos.content missing")
                self._record_migration(6, "V6 strategy_memos.content")
            except RuntimeError:
                raise
            except Exception as e:
                logger.warning("V6 migration strategy_memos.content: %s", e)
                self._migration_failures.append(f"V6 strategy_memos.content: {type(e).__name__}: {e}")

        # ── V7: trade_logs.currency (V4.5-2) ───────────────────────────────────
        self._apply_versioned_migration(
            7, "V7 trade_logs.currency",
            "ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS currency VARCHAR(10)",
        )

        # ── V8: trade_logs.linked_memo_id (V4.5-3) ─────────────────────────────
        self._apply_versioned_migration(
            8, "V8 trade_logs.linked_memo_id",
            "ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS linked_memo_id INTEGER",
        )

        # ── V9: trade_logs.verification_status (V5.2-1) ────────────────────────
        self._apply_versioned_migration(
            9, "V9 trade_logs.verification_status",
            "ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS verification_status VARCHAR(20) DEFAULT 'pending'",
        )

        # ── V10: asset_analyses table (V5.0-1) ─────────────────────────────────
        already_v10 = self.execute("SELECT 1 FROM schema_version WHERE version = 10").fetchone()
        if not already_v10:
            ok_v10 = True
            for _stmt in [
                "CREATE SEQUENCE IF NOT EXISTS asset_analyses_seq START 1",
                """CREATE TABLE IF NOT EXISTS asset_analyses (
                    id INTEGER PRIMARY KEY DEFAULT nextval('asset_analyses_seq'),
                    asset_code VARCHAR(20) NOT NULL,
                    asset_name VARCHAR(200),
                    analysis_type VARCHAR(20) DEFAULT 'full',
                    technical_signals JSON,
                    llm_analysis JSON,
                    llm_analysis_markdown VARCHAR,
                    portfolio_context JSON,
                    model_used VARCHAR,
                    data_source VARCHAR(50),
                    triggered_by VARCHAR(20),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""",
                "CREATE INDEX IF NOT EXISTS idx_asset_analyses_code ON asset_analyses(asset_code)",
                "CREATE INDEX IF NOT EXISTS idx_asset_analyses_created ON asset_analyses(created_at DESC)",
            ]:
                if not self._run_migration("V10 asset_analyses", _stmt):
                    ok_v10 = False
            if ok_v10:
                self._record_migration(10, "V10 asset_analyses")

        # ── V11: sync_state table (V5.0-2) ─────────────────────────────────────
        self._apply_versioned_migration(
            11, "V11 sync_state",
            """CREATE TABLE IF NOT EXISTS sync_state (
                key VARCHAR PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
        )

        # ── V12: sync_audit_reports columns (V4.8-1) ───────────────────────────
        already_v12 = self.execute("SELECT 1 FROM schema_version WHERE version = 12").fetchone()
        if not already_v12:
            ok_v12 = True
            for _stmt in [
                "ALTER TABLE sync_audit_reports ADD COLUMN IF NOT EXISTS is_no_change BOOLEAN DEFAULT FALSE",
                "ALTER TABLE sync_audit_reports ADD COLUMN IF NOT EXISTS info_messages JSON",
            ]:
                if not self._run_migration("V12 sync_audit_reports", _stmt):
                    ok_v12 = False
            if ok_v12:
                self._record_migration(12, "V12 sync_audit_reports.is_no_change+info_messages")

        # ── V13: holdings.price_source (V5.1-1) ────────────────────────────────
        self._apply_versioned_migration(
            13, "V13 holdings.price_source",
            "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS price_source VARCHAR",
        )

        # ── V14: position_deltas table (V5.1-2) ────────────────────────────────
        already_v14 = self.execute("SELECT 1 FROM schema_version WHERE version = 14").fetchone()
        if not already_v14:
            ok_v14 = True
            for _stmt in [
                "CREATE SEQUENCE IF NOT EXISTS seq_position_deltas_id START 1",
                """CREATE TABLE IF NOT EXISTS position_deltas (
                    id INTEGER PRIMARY KEY DEFAULT nextval('seq_position_deltas_id'),
                    asset_id VARCHAR(50) NOT NULL,
                    old_qty DECIMAL(20,8) DEFAULT 0,
                    new_qty DECIMAL(20,8) DEFAULT 0,
                    delta_qty DECIMAL(20,8) NOT NULL,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    source_system VARCHAR(50),
                    old_snapshot_date DATE,
                    new_snapshot_date DATE,
                    confirmed BOOLEAN DEFAULT FALSE
                )""",
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_position_deltas_dedupe
                ON position_deltas (
                    source_system, asset_id,
                    COALESCE(old_snapshot_date, DATE '1970-01-01'),
                    COALESCE(new_snapshot_date, DATE '1970-01-01')
                )""",
            ]:
                if not self._run_migration("V14 position_deltas", _stmt):
                    ok_v14 = False
            if ok_v14:
                self._record_migration(14, "V14 position_deltas")

        # ── V15: Valuation Module tables + seed (V5.3-1) ───────────────────────
        already_v15 = self.execute("SELECT 1 FROM schema_version WHERE version = 15").fetchone()
        if not already_v15:
            ok_v15 = True
            for _stmt in [
                "CREATE SEQUENCE IF NOT EXISTS seq_valuation_snapshots_id START 1",
                """CREATE TABLE IF NOT EXISTS valuation_snapshots (
                  id INTEGER PRIMARY KEY DEFAULT nextval('seq_valuation_snapshots_id'),
                  snapshot_date DATE NOT NULL,
                  ticker VARCHAR(20) NOT NULL,
                  display_name VARCHAR(200),
                  row_kind VARCHAR(20) DEFAULT 'holding',
                  linked_ticker VARCHAR(20),
                  asset_id VARCHAR(50),
                  asset_class VARCHAR(20) NOT NULL,
                  pe_ttm DOUBLE,
                  pe_forward DOUBLE,
                  pb_ratio DOUBLE,
                  peg_ratio DOUBLE,
                  fcf_yield DOUBLE,
                  dividend_yield DOUBLE,
                  ev_ebitda DOUBLE,
                  sec_yield DOUBLE,
                  pe_ttm_pct DOUBLE,
                  pe_fwd_pct DOUBLE,
                  pb_pct DOUBLE,
                  pct_years INTEGER,
                  valuation_signal VARCHAR(10),
                  signal_basis VARCHAR(200),
                  rate_adjustment_factor DOUBLE,
                  data_source VARCHAR(50),
                  is_estimable BOOLEAN DEFAULT TRUE,
                  notes TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE(snapshot_date, ticker)
                )""",
                "ALTER TABLE valuation_snapshots ADD COLUMN IF NOT EXISTS display_name VARCHAR",
                "ALTER TABLE valuation_snapshots ADD COLUMN IF NOT EXISTS row_kind VARCHAR DEFAULT 'holding'",
                "ALTER TABLE valuation_snapshots ADD COLUMN IF NOT EXISTS linked_ticker VARCHAR",
                """CREATE TABLE IF NOT EXISTS valuation_reference (
                  ticker VARCHAR(20) NOT NULL,
                  metric VARCHAR(20) NOT NULL,
                  low_threshold DOUBLE NOT NULL,
                  high_threshold DOUBLE NOT NULL,
                  historical_mean DOUBLE,
                  rate_sensitive BOOLEAN DEFAULT FALSE,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  notes TEXT,
                  PRIMARY KEY (ticker, metric)
                )""",
                """CREATE TABLE IF NOT EXISTS valuation_history (
                  ticker VARCHAR(20) NOT NULL,
                  metric VARCHAR(20) NOT NULL,
                  observed_date DATE NOT NULL,
                  value DOUBLE NOT NULL,
                  source VARCHAR(50) NOT NULL,
                  PRIMARY KEY (ticker, metric, observed_date)
                )""",
                """CREATE INDEX IF NOT EXISTS idx_valuation_history_lookup
                ON valuation_history (ticker, metric, observed_date)""",
                """CREATE TABLE IF NOT EXISTS valuation_watchlist (
                  ticker VARCHAR(20) PRIMARY KEY,
                  display_name VARCHAR(200) NOT NULL,
                  asset_type VARCHAR(20) NOT NULL,
                  note TEXT,
                  added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""",
            ]:
                if not self._run_migration("V15 valuation_module", _stmt):
                    ok_v15 = False
            if ok_v15:
                # Real reference-data seed moved to the seed-pack system
                # (Program OSR WS-3c, 2026-08-17) — see
                # src.database.seed_loader.seed_demo_content(), gated on
                # $UIS_SEED_PROFILE. Safe: this block is inside the
                # one-time `if not already_v15` gate, so removing the
                # INSERT here has zero effect on an already-migrated DB —
                # it only changes what a FRESH database gets.
                self._record_migration(15, "V15 valuation_module+seed")

        # ── V16: auth_credentials table (V5.4) ─────────────────────────────────
        self._apply_versioned_migration(
            16, "V16 auth_credentials",
            """CREATE TABLE IF NOT EXISTS auth_credentials (
                id INTEGER PRIMARY KEY DEFAULT 1,
                password_hash VARCHAR NOT NULL,
                token_version INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
        )

        # ── V17: user_profile table (V5.6) ─────────────────────────────────────
        self._apply_versioned_migration(
            17, "V17 user_profile",
            """CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY DEFAULT 1,
                display_name VARCHAR,
                avatar_base64 TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
        )

        # ── V18: decision feedback loop — verdict_audit (V5.8.0 / Migration 10) ─
        already_v18 = self.execute("SELECT 1 FROM schema_version WHERE version = 18").fetchone()
        if not already_v18:
            ok_v18 = True
            for _stmt in [
                "ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                "ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS verification_block_reason VARCHAR",
                "CREATE SEQUENCE IF NOT EXISTS seq_verdict_audit_id START 1",
                """CREATE TABLE IF NOT EXISTS verdict_audit (
                       id INTEGER PRIMARY KEY DEFAULT nextval('seq_verdict_audit_id'),
                       trade_id INTEGER NOT NULL,
                       suggested_from_threshold VARCHAR,
                       keyword_derived VARCHAR,
                       final_verdict VARCHAR,
                       mismatch BOOLEAN,
                       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                   )""",
            ]:
                if not self._run_migration("V18 verdict_audit", _stmt):
                    ok_v18 = False
            if ok_v18:
                self._record_migration(18, "V18 verdict_audit+trade_logs_cols")

        # ── V19: insight_trade_links table (V5.10.0 / Migration 11) ────────────
        already_v19 = self.execute("SELECT 1 FROM schema_version WHERE version = 19").fetchone()
        if not already_v19:
            ok_v19 = True
            for _stmt in [
                "CREATE SEQUENCE IF NOT EXISTS seq_insight_trade_links_id START 1",
                """CREATE TABLE IF NOT EXISTS insight_trade_links (
                       id INTEGER PRIMARY KEY DEFAULT nextval('seq_insight_trade_links_id'),
                       insight_id INTEGER NOT NULL,
                       trade_id INTEGER NOT NULL,
                       link_type VARCHAR NOT NULL,
                       confidence DECIMAL(3,2),
                       rationale VARCHAR,
                       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                       UNIQUE(insight_id, trade_id)
                   )""",
            ]:
                if not self._run_migration("V19 insight_trade_links", _stmt):
                    ok_v19 = False
            if ok_v19:
                self._record_migration(19, "V19 insight_trade_links")

        # Migration 11 backfill: populate from runtime join if table is empty.
        # Version-gated separately (V19b) so it only runs once.
        already_v19b = self.execute("SELECT 1 FROM schema_version WHERE version = 191").fetchone()
        if not already_v19b:
            try:
                count_row = self.execute("SELECT COUNT(*) FROM insight_trade_links").fetchone()
                if count_row and count_row[0] == 0:
                    self.execute("""
                        INSERT INTO insight_trade_links (insight_id, trade_id, link_type, confidence)
                        SELECT
                            i.id AS insight_id,
                            tl.id AS trade_id,
                            'auto_source' AS link_type,
                            CAST(
                                GREATEST(0.0, 1.0 - ABS(date_diff('day', i.insight_date, tl.log_date)) / 4.0)
                                AS DECIMAL(3,2)
                            ) AS confidence
                        FROM insights i
                        JOIN trade_logs tl
                            ON tl.suggestion_source IS NOT NULL
                            AND LOWER(tl.suggestion_source) = LOWER(i.ai_model)
                            AND ABS(date_diff('day', i.insight_date, tl.log_date)) <= 3
                        ON CONFLICT (insight_id, trade_id) DO NOTHING
                    """)
                self._record_migration(191, "V19b insight_trade_links_backfill")
            except Exception as e:
                logger.warning("V19b insight_trade_links backfill: %s", e)

        # ── V20: import_adapter tables (V5.6.0 / Migration 12) ─────────────────
        already_v20 = self.execute("SELECT 1 FROM schema_version WHERE version = 20").fetchone()
        if not already_v20:
            ok_v20 = True
            for _stmt in [
                "CREATE SEQUENCE IF NOT EXISTS seq_import_adapter_staged_rows_id START 1",
                """CREATE TABLE IF NOT EXISTS import_adapter_staged_rows (
                       id INTEGER PRIMARY KEY DEFAULT nextval('seq_import_adapter_staged_rows_id'),
                       run_id INTEGER NOT NULL,
                       row_index INTEGER NOT NULL,
                       row_kind VARCHAR NOT NULL,
                       normalized_payload_json JSON NOT NULL,
                       validation_status VARCHAR NOT NULL,
                       validation_messages_json JSON,
                       created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       synced_at TIMESTAMP
                   )""",
                """CREATE TABLE IF NOT EXISTS import_adapter_approvals (
                       adapter_key VARCHAR PRIMARY KEY,
                       approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       approved_by VARCHAR,
                       source_system VARCHAR NOT NULL,
                       asset_prefixes_json JSON NOT NULL,
                       authority_priority INTEGER NOT NULL,
                       enabled BOOLEAN NOT NULL DEFAULT TRUE
                   )""",
            ]:
                if not self._run_migration("V20 import_adapter", _stmt):
                    ok_v20 = False
            if ok_v20:
                self._record_migration(20, "V20 import_adapter_tables")

        # ── V21: PIS cost-basis cleanup data-fix (Migration 12b) ───────────────
        # Pure DML data-fix — version gate ensures it runs once.
        already_v21 = self.execute("SELECT 1 FROM schema_version WHERE version = 21").fetchone()
        if not already_v21:
            try:
                self.execute("""
                    UPDATE sync_audit_logs
                    SET is_resolved = TRUE,
                        resolution_notes = 'Auto-resolved: PIS deprecated V5.7.0'
                    WHERE source_system = 'PIS'
                      AND conflict_type = 'cost_basis_mismatch'
                      AND is_resolved = FALSE
                """)
                self._record_migration(21, "V21 PIS_cost_basis_cleanup")
            except Exception as e:
                logger.warning("V21 PIS cleanup: %s", e)

        # ── V22–V27: Classification tables (Migration 13, Pass D) ──────────────
        # DDL copied verbatim from src/classification/schema.py; seeding stays in seed.py.
        for version, label, stmt in [
            (22, "V22 taxonomy_classes", """
                CREATE TABLE IF NOT EXISTS taxonomy_classes (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    name_cn VARCHAR(100),
                    parent_id INTEGER,
                    level INTEGER NOT NULL DEFAULT 0,
                    sort_order INTEGER DEFAULT 0,
                    is_rebalanceable BOOLEAN DEFAULT TRUE,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(name, parent_id)
                )
            """),
            (23, "V23 asset_tiers", """
                CREATE TABLE IF NOT EXISTS asset_tiers (
                    id VARCHAR(30) PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    name_en VARCHAR(100),
                    target_pct DECIMAL(5,2) NOT NULL,
                    description TEXT,
                    color VARCHAR(20),
                    sort_order INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """),
            (24, "V24 risk_profiles", """
                CREATE TABLE IF NOT EXISTS risk_profiles (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(50) NOT NULL UNIQUE,
                    name_en VARCHAR(50),
                    is_active BOOLEAN DEFAULT FALSE,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """),
            (25, "V25 risk_profile_allocations", """
                CREATE TABLE IF NOT EXISTS risk_profile_allocations (
                    id INTEGER PRIMARY KEY,
                    profile_id INTEGER NOT NULL,
                    class_id INTEGER NOT NULL,
                    target_pct DECIMAL(5,2) NOT NULL,
                    UNIQUE(profile_id, class_id)
                )
            """),
            (26, "V26 classification_rules", """
                CREATE TABLE IF NOT EXISTS classification_rules (
                    id INTEGER PRIMARY KEY,
                    rule_type VARCHAR(20) NOT NULL,
                    pattern VARCHAR(500) NOT NULL,
                    class_id INTEGER,
                    tier_id VARCHAR(30),
                    priority INTEGER DEFAULT 100,
                    source VARCHAR(50) DEFAULT 'seed',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(rule_type, pattern)
                )
            """),
            (27, "V27 classification_audit_log", """
                CREATE TABLE IF NOT EXISTS classification_audit_log (
                    id INTEGER PRIMARY KEY,
                    asset_id VARCHAR(50) NOT NULL,
                    old_class_id INTEGER,
                    new_class_id INTEGER,
                    old_tier_id VARCHAR(30),
                    new_tier_id VARCHAR(30),
                    method VARCHAR(50) NOT NULL,
                    changed_by VARCHAR(100) DEFAULT 'system',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notes TEXT
                )
            """),
        ]:
            self._apply_versioned_migration(version, label, stmt)

        # ── V28–V30: market_sentiment_cache newer columns (Migration 14, Pass D) ─
        for version, label, stmt in [
            (28, "V28 market_sentiment_cache.is_stale",
             "ALTER TABLE market_sentiment_cache ADD COLUMN IF NOT EXISTS is_stale BOOLEAN DEFAULT FALSE"),
            (29, "V29 market_sentiment_cache.last_refresh_attempt",
             "ALTER TABLE market_sentiment_cache ADD COLUMN IF NOT EXISTS last_refresh_attempt TIMESTAMP"),
            (30, "V30 market_sentiment_cache.error_detail",
             "ALTER TABLE market_sentiment_cache ADD COLUMN IF NOT EXISTS error_detail VARCHAR"),
        ]:
            self._apply_versioned_migration(version, label, stmt)

        # ── V31–V34: Hot-path indexes (Migration 15, Pass D) ───────────────────
        for version, label, stmt in [
            (31, "V31 idx_holdings_source_system",
             "CREATE INDEX IF NOT EXISTS idx_holdings_source_system ON holdings(source_system)"),
            (32, "V32 idx_holdings_is_shadow",
             "CREATE INDEX IF NOT EXISTS idx_holdings_is_shadow ON holdings(is_shadow)"),
            (33, "V33 idx_transactions_asset_id",
             "CREATE INDEX IF NOT EXISTS idx_transactions_asset_id ON transactions(asset_id)"),
            (34, "V34 idx_trade_logs_linked_transaction_id",
             "CREATE INDEX IF NOT EXISTS idx_trade_logs_linked_transaction_id ON trade_logs(linked_transaction_id)"),
        ]:
            self._apply_versioned_migration(version, label, stmt)

        # ── V35–V58: Drop orphaned tables (Migration 16, Pass F Batch 2) ────────
        # These 8 tables were scaffolded early and are never queried by the
        # production service layer (confirmed by grep of src/ — zero SQL hits).
        # asset_taxonomy was also confirmed safe: only test fixtures that build
        # their own in-memory schemas reference it; no src/ SQL table reference.
        for version, label, stmt in [
            (35, "V35 drop committee_decisions",         "DROP TABLE IF EXISTS committee_decisions"),
            (36, "V36 drop market_events",               "DROP TABLE IF EXISTS market_events"),
            (37, "V37 drop economic_indicators",         "DROP TABLE IF EXISTS economic_indicators"),
            (38, "V38 drop exchange_rates",              "DROP TABLE IF EXISTS exchange_rates"),
            (39, "V39 drop schema_snapshots",            "DROP TABLE IF EXISTS schema_snapshots"),
            (40, "V40 drop rsu_vesting_schedules",       "DROP TABLE IF EXISTS rsu_vesting_schedules"),
            (41, "V41 drop source_authority_rules",      "DROP TABLE IF EXISTS source_authority_rules"),
            (42, "V42 drop asset_taxonomy",              "DROP TABLE IF EXISTS asset_taxonomy"),
            (43, "V43 drop idx_exchange_rates_date",     "DROP INDEX IF EXISTS idx_exchange_rates_date"),
            (44, "V44 drop idx_rsu_vesting_status",      "DROP INDEX IF EXISTS idx_rsu_vesting_status"),
            (45, "V45 drop idx_asset_taxonomy_class",    "DROP INDEX IF EXISTS idx_asset_taxonomy_class"),
            (46, "V46 drop idx_asset_taxonomy_rebalanceable", "DROP INDEX IF EXISTS idx_asset_taxonomy_rebalanceable"),
            (47, "V47 drop seq_committee_decisions",     "DROP SEQUENCE IF EXISTS seq_committee_decisions_id"),
            (48, "V48 drop seq_market_events",           "DROP SEQUENCE IF EXISTS seq_market_events_id"),
            (49, "V49 drop seq_economic_indicators",     "DROP SEQUENCE IF EXISTS seq_economic_indicators_id"),
            (50, "V50 drop seq_exchange_rates",          "DROP SEQUENCE IF EXISTS seq_exchange_rates_id"),
            (51, "V51 drop seq_schema_snapshots",        "DROP SEQUENCE IF EXISTS seq_schema_snapshots_id"),
            (52, "V52 drop seq_rsu_vesting_schedules",   "DROP SEQUENCE IF EXISTS seq_rsu_vesting_schedules_id"),
            (53, "V53 drop seq_source_authority_rules",  "DROP SEQUENCE IF EXISTS seq_source_authority_rules_id"),
            (54, "V54 drop seq_asset_taxonomy",          "DROP SEQUENCE IF EXISTS seq_asset_taxonomy_id"),
        ]:
            self._apply_versioned_migration(version, label, stmt)

        # ── V55: sync_audit_reports.steps (A3b — per-phase pipeline step results) ─
        self._apply_versioned_migration(
            55, "V55 sync_audit_reports.steps",
            "ALTER TABLE sync_audit_reports ADD COLUMN IF NOT EXISTS steps JSON",
        )

        # ── V56: import_adapter_approvals.generated_reader_key (ADR-018 Phase 3) ──
        # Mutual-exclusion key: NULL = one-time-import path; non-NULL = config-driven
        # reader path.  Prevents double-ingestion when generate_reader=true.
        self._apply_versioned_migration(
            56, "V56 import_adapter_approvals.generated_reader_key",
            "ALTER TABLE import_adapter_approvals ADD COLUMN IF NOT EXISTS generated_reader_key VARCHAR",
        )

        # ── V57: verdict_audit.both_matched (insights-pipeline A3) ───────────────
        # Audit flag: both REGRET and GOOD_CALL keyword sets matched the verification
        # text (mixed narrative) and the tie-break resolution path was taken.
        self._apply_versioned_migration(
            57, "V57 verdict_audit.both_matched",
            "ALTER TABLE verdict_audit ADD COLUMN IF NOT EXISTS both_matched BOOLEAN DEFAULT FALSE",
        )

        # ── V58: verification_logs benchmark columns (insights-pipeline A1) ──────
        # Defensive: schema.sql has these for fresh DBs; older DBs (incl. the
        # GCS-persisted cloud DB) may predate them. GET /verification/history and
        # compute_verification_report now read/write them unconditionally.
        for _ver, _col in (
            (58, "portfolio_return"),
            (59, "benchmark_return"),
            (60, "alpha"),
        ):
            self._apply_versioned_migration(
                _ver, f"V{_ver} verification_logs.{_col}",
                f"ALTER TABLE verification_logs ADD COLUMN IF NOT EXISTS {_col} DECIMAL(8,2)",
            )

        # ── V62: data-fix — reassign a mislabeled trade_logs row ─────────────────
        # Owner-confirmed: one trade recorded under the wrong CN fund code was
        # actually a trade in a different fund. Evidence: the mislabeled fund
        # was fully liquidated months earlier and its NAV history couldn't
        # match the row's recorded price; the correct fund's NAV did.
        #
        # Supersedes V61: V61's WHERE used an exact `price = 2.94`, but the stored
        # value is 2.944 — DuckDB DECIMAL equality never matched, so V61 fired as a
        # 0-row no-op yet still burned its version gate.  V62 keys on the natural
        # (asset_id, log_date) signature — which uniquely identifies the row —
        # plus a wide price band (guards against any legitimate row near the same
        # price) so it can never silently mismatch on decimal scale again.
        #   • DBs without the row execute a 0-row UPDATE safely (no error).
        #   • Post-migration the old asset_id no longer matches → idempotent.
        #   • The version gate prevents the statement from running more than once.
        self._apply_versioned_migration(
            62,
            "V62 data-fix trade_logs mislabeled CN fund reassignment",
            """UPDATE trade_logs
               SET asset_id  = 'CN_FUND_110020',
                   asset_name = '示例沪深300指数增强A'
               WHERE asset_id = 'CN_FUND_900016'
                 AND log_date = DATE '2026-06-18'
                 AND price BETWEEN 2.5 AND 3.5""",
        )

        # ── V63: data-fix — delete phantom GOLD_nan_nan rows ────────────────────
        # 2026-07-05 incident: a stray value in the gold Excel holdings sheet (an
        # otherwise-empty row with a lone 3.0 in the unit-price column) produced a
        # phantom asset via id_template "GOLD_{asset_name}_{account}" with NaN
        # identity fields → literal id 'GOLD_nan_nan', NULL quantity/market_value.
        # This tripped the BLOCKING integrity check active_holdings_have_positive_value
        # and failed the owner's cloud sync. The reader engine now drops NaN-identity
        # rows at ingest (config_driven_reader NaN-identity guard), so this cannot
        # recur; V63 removes the rows the incident already created.
        # Owner explicitly approved this deletion on 2026-07-05 (AskUserQuestion:
        # "Yes, delete via migration"). Exact-match on the literal id ONLY — never
        # a pattern; legitimate GOLD_* / ALTS_Paper_Gold rows are untouched.
        already_v63 = self.execute("SELECT 1 FROM schema_version WHERE version = 63").fetchone()
        if not already_v63:
            ok_v63 = True
            for _stmt in [
                "DELETE FROM holdings WHERE asset_id = 'GOLD_nan_nan'",
                "DELETE FROM transactions WHERE asset_id = 'GOLD_nan_nan'",
                "DELETE FROM asset_registry WHERE canonical_id = 'GOLD_nan_nan'",
            ]:
                if not self._run_migration("V63 delete GOLD_nan_nan", _stmt):
                    ok_v63 = False
            if ok_v63:
                self._record_migration(63, "V63 data-fix delete phantom GOLD_nan_nan rows")

        # ── V64: data-fix — re-open owner trade_logs stuck verified-without-verdict ──
        # Root cause: the trade linker previously promoted ALL matched rows straight to
        # verification_status='verified', regardless of who recorded the trade.  Owner-
        # recorded rows (suggestion_source IS NOT NULL AND != 'imported') that landed in
        # 'verified' with NULL verdict are permanently invisible: the pending-list UI filter
        # shows only pending/pending_window; the verified-history list requires verdict IS
        # NOT NULL; and score_all_trades (decision_scorer.py:803-806) skips status='verified'
        # entirely.  Re-opening them to 'pending_window' lets score_all_trades mature them
        # normally on the next sync.
        #
        # Reader-imported rows (suggestion_source IS NULL or 'imported', ~2,278 rows) are
        # deliberately excluded — they are KPI-measurement rows that must stay 'verified'
        # even without a verdict; touching them would distort the historical KPI baseline.
        #
        # Predicate is keyed on (verification_status, verdict, verification_result,
        # linked_transaction_id, suggestion_source) — never on row ids (which differ
        # between local and cloud DBs).
        #   • DBs without matching rows: 0-row UPDATE, no error.
        #   • After migration rows move to pending_window → predicate no longer matches → idempotent.
        #   • Version gate prevents the statement from running more than once.
        # Measured impact: ~41 rows cloud / ~43 rows local.  Owner approved 2026-07-06.
        self._apply_versioned_migration(
            64,
            "V64 data-fix re-open owner trade_logs stuck verified-without-verdict",
            """UPDATE trade_logs
               SET verification_status = 'pending_window'
               WHERE verification_status = 'verified'
                 AND verdict IS NULL
                 AND COALESCE(verification_result, '') = ''
                 AND linked_transaction_id IS NOT NULL
                 AND suggestion_source IS NOT NULL
                 AND suggestion_source != 'imported'""",
        )

        # ── V65: data-fix — RSU_AMZN Apr-2026 sell verdicts regret→missed_opportunity ─
        # Local DB scored these two rows on 2026-05-27 under pre-V7.1.8 keyword-wins
        # semantics (any "regret"-pattern keyword → verdict='regret').  Cloud re-scored
        # them under current numeric semantics: outcome −24.3% and −17.7% on a Sell means
        # the price rose after selling, so the correct verdict is 'missed_opportunity'.
        # Owner approved a one-time overwrite to converge local to cloud (2026-07-06).
        #
        # Predicate uses natural keys (asset_id, action, log_date, verdict='regret') —
        # never row ids.  Safe on cloud (verdict already 'missed_opportunity' → 0-row no-op
        # on cloud) and idempotent everywhere (after first run verdict != 'regret').
        # Do NOT use exact decimal price matches anywhere (V61 lesson: burned its version
        # gate as a 0-row no-op due to DECIMAL scale mismatch).
        # Deliberate, owner-approved exception to the never-overwrite-verdict rule.
        self._apply_versioned_migration(
            65,
            "V65 data-fix RSU_AMZN Apr-2026 sell verdicts regret→missed_opportunity",
            """UPDATE trade_logs
               SET verdict = 'missed_opportunity'
               WHERE asset_id = 'RSU_AMZN'
                 AND action = 'Sell'
                 AND log_date IN (DATE '2026-04-08', DATE '2026-04-09')
                 AND verdict = 'regret'
                 -- guards restored backups / unknown DB states: only owner-recorded rows are in scope
                 AND COALESCE(suggestion_source, '') != 'imported'""",
        )

        # ── V66: data-fix — re-open unlinked owner trade_logs stuck verified-without-verdict ──
        # Completes V64: V64 required linked_transaction_id IS NOT NULL and missed 7 unlinked
        # owner rows (Feb-2026 memo/unknown trades: verified with no verdict, no narrative,
        # no link — unreachable by UI and scorer).  Re-opened to pending_window they re-enter
        # the linker (fuzzy match may link them) and the scorer (matures or blocks them).
        #
        # Deliberately mirrors the V64 predicate minus the linked_transaction_id IS NOT NULL
        # guard; unlinked rows are equally invisible to the feedback loop.
        #
        # Reader-imported rows (suggestion_source IS NULL or 'imported') are excluded —
        # KPI-measurement rows that must stay 'verified' even without a verdict (same
        # rationale as V64).  Narrative-bearing rows (COALESCE(TRIM(verification_result),'') != '')
        # are excluded — they may be waiting on a manual verdict entry.
        #
        # Predicate uses natural-key / state only (no row ids — differ between local and cloud).
        #   • Measured impact: 7 rows local, 0 rows cloud (cloud's equivalents all linked → V64 scope).
        #   • DBs without matching rows: 0-row UPDATE, no error.
        #   • After migration rows move to pending_window → predicate no longer matches → idempotent.
        #   • Version gate prevents re-execution.
        # Owner approved 2026-07-06.
        self._apply_versioned_migration(
            66,
            "V66 data-fix re-open unlinked owner trade_logs stuck verified-without-verdict",
            """UPDATE trade_logs
               SET verification_status = 'pending_window'
               WHERE verification_status = 'verified'
                 AND verdict IS NULL
                 AND COALESCE(TRIM(verification_result), '') = ''
                 AND linked_transaction_id IS NULL
                 AND suggestion_source IS NOT NULL
                 AND suggestion_source != 'imported'""",
        )

        # ── V67: Migration 010 — process verification schema foundation (file) ─
        # F1.1 (PRD 2026-07-07 process-verification program, Batch B1): additive
        # trade_logs columns for bucket-aware process verification (rule_bucket,
        # memo_id, order_origin, process_* checks, verdict_archived). Loaded from
        # file following the V4/V5 pattern (008/009).
        already_v67 = self.execute("SELECT 1 FROM schema_version WHERE version = 67").fetchone()
        if not already_v67:
            migration_path = Path(__file__).parent / "migrations" / "010_process_verification_schema.sql"
            ok_v67 = self._run_migration(
                "V67 010_process_verification_schema",
                migration_path.read_text(encoding="utf-8"),
            )
            if ok_v67:
                self._record_migration(67, "V67 010_process_verification_schema")

        # ── V68: Migration 011 — value_trap_reviews table (file) ───────────────
        # F2 (PRD 2026-07-07 process-verification program, Batch B3): loss-side
        # mandatory review trigger. New additive table only — no changes to
        # holdings/transactions. Loaded from file following the V67/010 pattern.
        already_v68 = self.execute("SELECT 1 FROM schema_version WHERE version = 68").fetchone()
        if not already_v68:
            migration_path = Path(__file__).parent / "migrations" / "011_value_trap_review.sql"
            ok_v68 = self._run_migration(
                "V68 011_value_trap_review",
                migration_path.read_text(encoding="utf-8"),
            )
            if ok_v68:
                self._record_migration(68, "V68 011_value_trap_review")

        # ── V69: Migration 012 — metric governance (file) ──────────────────
        # F4.3/F4.4/F4.6 (PRD 2026-07-07 process-verification program, Batch B5):
        # metric_catalog + data_fixes + ruling_deferred_events, plus additive
        # methodology/data_source columns on market_sentiment_cache. Loaded
        # from file following the V67/V68 pattern.
        already_v69 = self.execute("SELECT 1 FROM schema_version WHERE version = 69").fetchone()
        if not already_v69:
            migration_path = Path(__file__).parent / "migrations" / "012_metric_governance.sql"
            ok_v69 = self._run_migration(
                "V69 012_metric_governance",
                migration_path.read_text(encoding="utf-8"),
            )
            if ok_v69:
                self._record_migration(69, "V69 012_metric_governance")

        # ── V70: Migration 013 — North Star panel (file) ───────────────────
        # F3 (PRD 2026-07-07 process-verification program, Batch B6):
        # cash_flow_tags (contribution classification) + unforced_errors log.
        # Loaded from file following the V67/V68/V69 pattern.
        already_v70 = self.execute("SELECT 1 FROM schema_version WHERE version = 70").fetchone()
        if not already_v70:
            migration_path = Path(__file__).parent / "migrations" / "013_north_star.sql"
            ok_v70 = self._run_migration(
                "V70 013_north_star",
                migration_path.read_text(encoding="utf-8"),
            )
            if ok_v70:
                self._record_migration(70, "V70 013_north_star")

        # ── V71: Migration 014 — Insight Library governance (file) ────────
        # F6 (PRD 2026-07-07 process-verification program, Batch B7): promote
        # gate (confidence >= 70% OR validated_cases >= 3), rule_layer
        # classification, rule_citations tracking. Additive columns on
        # ai_insights + one new table. Loaded from file following the
        # V67/V68/V69/V70 pattern.
        already_v71 = self.execute("SELECT 1 FROM schema_version WHERE version = 71").fetchone()
        if not already_v71:
            migration_path = Path(__file__).parent / "migrations" / "014_insight_governance.sql"
            ok_v71 = self._run_migration(
                "V71 014_insight_governance",
                migration_path.read_text(encoding="utf-8"),
            )
            if ok_v71:
                self._record_migration(71, "V71 014_insight_governance")

        # ── V72: Migration 015 — memo registry + asset linkage (file) ─────
        # Fix 2 (2026-07-10 fix-request): memo_registry, memo_asset_map,
        # asset_memo_confirmations tables + five seeded memos. Prevents
        # mislabeling missing linkage data as "no memo on record".
        # Loaded from file following the V67/V68/V69/V70/V71 pattern.
        already_v72 = self.execute("SELECT 1 FROM schema_version WHERE version = 72").fetchone()
        if not already_v72:
            migration_path = Path(__file__).parent / "migrations" / "015_memo_registry.sql"
            ok_v72 = self._run_migration(
                "V72 015_memo_registry",
                migration_path.read_text(encoding="utf-8"),
            )
            if ok_v72:
                self._record_migration(72, "V72 015_memo_registry")

        # ── V73: Migration 016 — unforced_errors.cost_edit_history (file) ──────
        # R2-7.5: edit history for est_cost_cny on unforced_errors.
        already_v73 = self.execute("SELECT 1 FROM schema_version WHERE version = 73").fetchone()
        if not already_v73:
            migration_path = Path(__file__).parent / "migrations" / "016_unforced_error_cost_history.sql"
            ok_v73 = self._run_migration(
                "V73 016_unforced_error_cost_history",
                migration_path.read_text(encoding="utf-8"),
            )
            if ok_v73:
                self._record_migration(73, "V73 016_unforced_error_cost_history")

        # ── V74: Migration 017 — cash_flow_tags.rule_id provenance (file) ────
        # WS3: records which rule tagged each row (e.g. same_day_transfer_pair, rsu_vest).
        already_v74 = self.execute("SELECT 1 FROM schema_version WHERE version = 74").fetchone()
        if not already_v74:
            migration_path = Path(__file__).parent / "migrations" / "017_flow_rule_provenance.sql"
            ok_v74 = self._run_migration(
                "V74 017_flow_rule_provenance",
                migration_path.read_text(encoding="utf-8"),
            )
            if ok_v74:
                self._record_migration(74, "V74 017_flow_rule_provenance")

        # ── V75: reader_mappings + reader_mapping_audit (Reader Mapping ─────────
        # Management ADR-023 / WS-A). UI-managed layer for "how raw file data
        # BECOMES assets" (column→asset mappings etc.), starting with the FS
        # Excel column mapping. Seed is idempotent, keyed on the natural
        # UNIQUE key (reader_key, mapping_kind, map_key) — never re-burns this
        # version gate even if re-run (memory: migration-decimal-precision-noop).
        already_v75 = self.execute("SELECT 1 FROM schema_version WHERE version = 75").fetchone()
        if not already_v75:
            ok_v75 = True
            for _stmt in [
                "CREATE SEQUENCE IF NOT EXISTS seq_reader_mappings_id START 1",
                """CREATE TABLE IF NOT EXISTS reader_mappings (
                       id INTEGER PRIMARY KEY DEFAULT nextval('seq_reader_mappings_id'),
                       reader_key VARCHAR NOT NULL,
                       mapping_kind VARCHAR NOT NULL,
                       map_key VARCHAR NOT NULL,
                       map_value VARCHAR NOT NULL,
                       status VARCHAR NOT NULL DEFAULT 'active',
                       sort_order INTEGER,
                       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                       updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                       UNIQUE(reader_key, mapping_kind, map_key)
                   )""",
                "CREATE SEQUENCE IF NOT EXISTS seq_reader_mapping_audit_id START 1",
                """CREATE TABLE IF NOT EXISTS reader_mapping_audit (
                       id INTEGER PRIMARY KEY DEFAULT nextval('seq_reader_mapping_audit_id'),
                       mapping_id INTEGER NOT NULL,
                       action VARCHAR NOT NULL,
                       old_value VARCHAR,
                       new_value VARCHAR,
                       "at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                   )""",
            ]:
                if not self._run_migration("V75 reader_mappings", _stmt):
                    ok_v75 = False

            if ok_v75:
                # Idempotent seed from src.database.mapping_seeds (single source
                # of truth shared with src.sources.reader_hooks' code default).
                # NOTE: connector must NOT import src.sources.reader_hooks here
                # (would create src.database -> src.sources -> ... cycle risk);
                # mapping_seeds.py is the shared, dependency-free seed source.
                from src.database.mapping_seeds import FS_ASSET_MAPPING_SEED
                try:
                    for sort_order, (map_key, (asset_id, asset_name, currency)) in enumerate(
                        FS_ASSET_MAPPING_SEED.items()
                    ):
                        map_value = json.dumps(
                            {"asset_id": asset_id, "asset_name": asset_name, "currency": currency},
                            ensure_ascii=False,
                        )
                        # Idempotent on the natural UNIQUE key: only insert if this
                        # (reader_key, mapping_kind, map_key) row doesn't exist yet —
                        # never re-burns the version gate, safe to re-run.
                        self.execute(
                            """
                            INSERT INTO reader_mappings
                                (reader_key, mapping_kind, map_key, map_value, status, sort_order)
                            SELECT 'financial_summary', 'fs_column', ?, ?, 'active', ?
                            WHERE NOT EXISTS (
                                SELECT 1 FROM reader_mappings
                                WHERE reader_key = 'financial_summary'
                                  AND mapping_kind = 'fs_column'
                                  AND map_key = ?
                            )
                            """,
                            [map_key, map_value, sort_order, map_key],
                        )
                except Exception as e:
                    logger.warning("V75 reader_mappings seed failed: %s", e, exc_info=True)
                    self._migration_failures.append(f"V75 reader_mappings seed: {type(e).__name__}: {e}")
                    ok_v75 = False

            if ok_v75:
                self._record_migration(75, "V75 reader_mappings+reader_mapping_audit+seed")

        # ── V76: reader_mappings 'ignored' status + FS informational-column ────
        # seed (ADR-023 A4.1). The live smoke test on the real FS Excel showed
        # ~29 "unmapped" columns of which most are FS's own informational copy
        # of a value another reader already owns authoritatively (Schwab/IBKR
        # US equities, RSU_Excel vesting, Gold Excel paper gold, Insurance
        # Excel policies) — not a code pattern, an owner decision about
        # specific columns (src.database.mapping_seeds.FS_IGNORED_COLUMNS_SEED).
        # No schema change: 'ignored' is just another `status` value on the
        # existing reader_mappings table. Idempotent on the natural UNIQUE key,
        # same pattern as V75.
        already_v76 = self.execute("SELECT 1 FROM schema_version WHERE version = 76").fetchone()
        if not already_v76:
            from src.database.mapping_seeds import FS_IGNORED_COLUMNS_SEED
            ok_v76 = True
            try:
                for map_key in FS_IGNORED_COLUMNS_SEED:
                    self.execute(
                        """
                        INSERT INTO reader_mappings
                            (reader_key, mapping_kind, map_key, map_value, status, sort_order)
                        SELECT 'financial_summary', 'fs_column', ?, '{}', 'ignored', NULL
                        WHERE NOT EXISTS (
                            SELECT 1 FROM reader_mappings
                            WHERE reader_key = 'financial_summary'
                              AND mapping_kind = 'fs_column'
                              AND map_key = ?
                        )
                        """,
                        [map_key, map_key],
                    )
            except Exception as e:
                logger.warning("V76 reader_mappings ignored-column seed failed: %s", e, exc_info=True)
                self._migration_failures.append(f"V76 reader_mappings ignored seed: {type(e).__name__}: {e}")
                ok_v76 = False

            if ok_v76:
                self._record_migration(76, "V76 reader_mappings ignored-column seed")

        # ── V77: reader_mappings id_field_map seed (Reader Mapping Management ──
        # WS-B). UI-manageable Gold/Insurance/RSU field:label -> code maps,
        # same `reader_mappings` table (no schema change) — mapping_kind=
        # 'id_field_map', map_key='field:label' (e.g. 'account:招行'),
        # map_value=JSON {"code": "CMB"}, for reader_key in
        # ('gold', 'insurance', 'rsu'). Seed mirrors config/readers/*.yaml
        # id_field_maps EXACTLY (src.database.mapping_seeds.ID_FIELD_MAP_SEEDS;
        # a test asserts the two never drift). Idempotent on the natural
        # UNIQUE key, same pattern as V75/V76 — never re-burns this version
        # gate even if re-run (memory: migration-decimal-precision-noop).
        # insurance's seed dict is empty (insurance.yaml declares no
        # id_field_maps today) — the loop below is a no-op for it, but the
        # reader_key is still available for the owner to add a first mapping
        # via the UI without a code change.
        already_v77 = self.execute("SELECT 1 FROM schema_version WHERE version = 77").fetchone()
        if not already_v77:
            from src.database.mapping_seeds import ID_FIELD_MAP_SEEDS
            ok_v77 = True
            try:
                for reader_key, seed in ID_FIELD_MAP_SEEDS.items():
                    for sort_order, (map_key, code) in enumerate(seed.items()):
                        map_value = json.dumps({"code": code}, ensure_ascii=False)
                        self.execute(
                            """
                            INSERT INTO reader_mappings
                                (reader_key, mapping_kind, map_key, map_value, status, sort_order)
                            SELECT ?, 'id_field_map', ?, ?, 'active', ?
                            WHERE NOT EXISTS (
                                SELECT 1 FROM reader_mappings
                                WHERE reader_key = ?
                                  AND mapping_kind = 'id_field_map'
                                  AND map_key = ?
                            )
                            """,
                            [reader_key, map_key, map_value, sort_order, reader_key, map_key],
                        )
            except Exception as e:
                logger.warning("V77 reader_mappings id_field_map seed failed: %s", e, exc_info=True)
                self._migration_failures.append(f"V77 reader_mappings id_field_map seed: {type(e).__name__}: {e}")
                ok_v77 = False

            if ok_v77:
                self._record_migration(77, "V77 reader_mappings id_field_map seed (gold/insurance/rsu)")

        # ── V78: reader_mappings vocab seed (Reader Mapping Management ADR-023 ──
        # WS-C). UI-manageable Schwab/CN-fund vocabularies, same
        # `reader_mappings` table (no schema change) — mapping_kind in
        # ('known_etf', 'symbol_norm', 'action_map') for reader_key='schwab',
        # and 'type_map' for reader_key='cn_fund'. map_value shapes:
        # known_etf={"etf": true}, symbol_norm={"to": ...},
        # action_map/type_map={"type": ...}. Seed mirrors
        # src.database.mapping_seeds.VOCAB_SEEDS exactly (single source of
        # truth also re-exported by src.sources.reader_hooks' private module
        # constants — see that module's ADR-023 WS-C comment). Idempotent on
        # the natural UNIQUE key, same pattern as V75/V76/V77 — never re-burns
        # this version gate even if re-run (memory: migration-decimal-precision-noop).
        already_v78 = self.execute("SELECT 1 FROM schema_version WHERE version = 78").fetchone()
        if not already_v78:
            from src.database.mapping_seeds import VOCAB_SEEDS
            ok_v78 = True
            try:
                for reader_key, kinds in VOCAB_SEEDS.items():
                    for kind, seed in kinds.items():
                        for sort_order, (map_key, value_dict) in enumerate(seed.items()):
                            map_value = json.dumps(value_dict, ensure_ascii=False)
                            self.execute(
                                """
                                INSERT INTO reader_mappings
                                    (reader_key, mapping_kind, map_key, map_value, status, sort_order)
                                SELECT ?, ?, ?, ?, 'active', ?
                                WHERE NOT EXISTS (
                                    SELECT 1 FROM reader_mappings
                                    WHERE reader_key = ?
                                      AND mapping_kind = ?
                                      AND map_key = ?
                                )
                                """,
                                [reader_key, kind, map_key, map_value, sort_order, reader_key, kind, map_key],
                            )
            except Exception as e:
                logger.warning("V78 reader_mappings vocab seed failed: %s", e, exc_info=True)
                self._migration_failures.append(f"V78 reader_mappings vocab seed: {type(e).__name__}: {e}")
                ok_v78 = False

            if ok_v78:
                self._record_migration(78, "V78 reader_mappings vocab seed (schwab/cn_fund)")

        # ── V79: Security Transfer typing (Attribution & Flows WS-3.1) ─────────
        # Schwab's 'Security Transfer' action had no action_map entry, so it fell
        # through to the 'other' default — and is directionally ambiguous (one
        # label covers both ACAT legs in and out), so a flat map_key -> type
        # can't encode it. Seeded here as the pseudo-type 'transfer'
        # (mapping_kind='action_map'), resolved by quantity sign at the reader
        # hook (src.sources.reader_hooks.schwab_transactions_from_csv, right
        # after _schwab_map_action) into 'transfer_out' (qty<0) / 'transfer_in'
        # (qty>=0) — 'transfer' itself is never stored on a transactions row.
        # Two idempotent parts, same version gate (both must ship together —
        # see (b)'s note on the dedup trap):
        #   (a) natural-key insert of the action_map row, same pattern as
        #       V75-V78.
        #   (b) in-place UPDATE heal of the 3 real rows this predicate matches
        #       today (2026-07-19: Jun-9 Schwab ACAT-out VOO -21 / IEF -172 /
        #       SGOV -200, all typed 'other', amount=0) — an UPDATE, not
        #       delete+reinsert, to preserve row ids and trade_log links, and
        #       to avoid the incremental per-row delete-then-insert in
        #       src/sync/phases/_ingest.py (keyed on transaction_type, among
        #       other columns — see the CN Fund self-heal comment at
        #       _ingest.py:436): if the action_map seed shipped without this
        #       heal, the next Schwab sync would produce 'transfer_out' rows
        #       that don't match any existing 'other' row on that key and
        #       duplicate them instead of superseding them.
        #       Range predicates (not exact-decimal equality) per the V61/V62
        #       lesson (memory: migration-decimal-precision-noop) — amount
        #       fields are DECIMAL(20,2) but compared as floats here, so a
        #       tolerance band is used rather than `= 0`.
        already_v79 = self.execute("SELECT 1 FROM schema_version WHERE version = 79").fetchone()
        if not already_v79:
            ok_v79 = True
            try:
                self.execute(
                    """
                    INSERT INTO reader_mappings
                        (reader_key, mapping_kind, map_key, map_value, status, sort_order)
                    SELECT 'schwab', 'action_map', 'Security Transfer', '{"type": "transfer"}', 'active', NULL
                    WHERE NOT EXISTS (
                        SELECT 1 FROM reader_mappings
                        WHERE reader_key = 'schwab'
                          AND mapping_kind = 'action_map'
                          AND map_key = 'Security Transfer'
                    )
                    """
                )
                self.execute(
                    """
                    UPDATE transactions
                    SET transaction_type = CASE WHEN quantity < 0 THEN 'transfer_out' ELSE 'transfer_in' END
                    WHERE source_system = 'Schwab_CSV'
                      AND transaction_type = 'other'
                      AND ABS(quantity) > 0.0001
                      AND ABS(COALESCE(amount_gross, 0)) < 0.005
                      AND ABS(COALESCE(amount_net, 0)) < 0.005
                    """
                )
            except Exception as e:
                logger.warning("V79 Security Transfer typing failed: %s", e, exc_info=True)
                self._migration_failures.append(f"V79 Security Transfer typing: {type(e).__name__}: {e}")
                ok_v79 = False

            if ok_v79:
                self._record_migration(79, "V79 Security Transfer typing (action_map seed + heal)")

        # ── V80: attribution_monthly (Attribution & Flows WS-1) ─────────────
        # Per (month, asset) decomposition of Δmarket_value into price/trade/
        # transfer/income effects + residual. See docs/api-specs/attribution.md
        # for the computation model. Recompute is idempotent per month (the
        # engine deletes the month partition then rewrites) — this table is a
        # pure derived cache, never hand-edited.
        already_v80 = self.execute("SELECT 1 FROM schema_version WHERE version = 80").fetchone()
        if not already_v80:
            ok_v80 = True
            try:
                self.execute("CREATE SEQUENCE IF NOT EXISTS seq_attribution_monthly_id START 1")
                self.execute(
                    """
                    CREATE TABLE IF NOT EXISTS attribution_monthly (
                        id INTEGER PRIMARY KEY DEFAULT nextval('seq_attribution_monthly_id'),
                        month DATE NOT NULL,
                        asset_id VARCHAR(50) NOT NULL,
                        mv_start DECIMAL(20,2) NOT NULL DEFAULT 0,
                        mv_end DECIMAL(20,2) NOT NULL DEFAULT 0,
                        price_effect DECIMAL(20,2) NOT NULL DEFAULT 0,
                        trade_effect DECIMAL(20,2) NOT NULL DEFAULT 0,
                        transfer_effect DECIMAL(20,2) NOT NULL DEFAULT 0,
                        income_effect DECIMAL(20,2) NOT NULL DEFAULT 0,
                        residual DECIMAL(20,2) NOT NULL DEFAULT 0,
                        dq_flag BOOLEAN NOT NULL DEFAULT FALSE,
                        computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(month, asset_id)
                    )
                    """
                )
            except Exception as e:
                logger.warning("V80 attribution_monthly create failed: %s", e, exc_info=True)
                self._migration_failures.append(f"V80 attribution_monthly: {type(e).__name__}: {e}")
                ok_v80 = False

            if ok_v80:
                self._record_migration(80, "V80 attribution_monthly (Attribution & Flows WS-1)")

        # ── V81: cash_flow_tags stable natural key (Attribution & Flows, owner review) ──
        # Root cause: cash_flow_tags.source_row_key stored transactions.id, but
        # _replace_transactions (src/sync/phases/_ingest.py) deletes and
        # reinserts rows on every sync for most sources — ids regenerate —
        # orphaning every tag on the next re-import. Fix: a stable natural key
        # `nk:{source_system}|{date}|{asset_id}|{transaction_type}|{amount_gross:.2f}`
        # (src.services.north_star_flows.compose_natural_key — EXACTLY the
        # identity _ingest.py's incremental delete-match uses, so a tag keyed
        # this way survives re-import by construction). All read/write paths
        # in north_star_flows.py already resolve either key form; this
        # migration re-keys existing data so already-tagged rows benefit too:
        #   (a) live tags (source_row_key still resolves to a transactions.id)
        #       -> re-keyed to that row's current natural key.
        #   (b) orphans (id no longer exists) whose flow_date matches EXACTLY
        #       ONE live transaction -> re-keyed to that transaction's key
        #       (single-candidate match only — ambiguous dates are left alone).
        #   (c) remaining orphans (owner's manual tags for transactions that
        #       no longer exist in this form) are NEVER deleted — owner data.
        #       list_classified_flows surfaces them with orphaned=True instead.
        # Idempotent: re-run is a no-op once every resolvable row is nk:-keyed
        # (STARTS_WITH guard on each half; ON CONFLICT never fires because a
        # live row's current natural key already matching its own stored key
        # is excluded by the STARTS_WITH filter up front).
        already_v81 = self.execute("SELECT 1 FROM schema_version WHERE version = 81").fetchone()
        if not already_v81:
            ok_v81 = True
            try:
                from src.services.north_star_flows import compose_natural_key

                # (a) Re-key live tags: any cash_flow_tags row (source_table=
                # 'transactions') not already nk:-keyed whose source_row_key
                # matches a live transactions.id.
                live_rows = self.execute(
                    """
                    SELECT cft.id, tx.source_system, tx.transaction_date, tx.asset_id,
                           tx.transaction_type, tx.amount_gross
                    FROM cash_flow_tags cft
                    JOIN transactions tx
                        ON cft.source_table = 'transactions'
                       AND cft.source_row_key = CAST(tx.id AS VARCHAR)
                    WHERE cft.source_table = 'transactions'
                      AND NOT STARTS_WITH(cft.source_row_key, 'nk:')
                    """
                ).fetchall()
                for tag_id, source_system, tx_date, asset_id, tx_type, amount_gross in live_rows:
                    nk = compose_natural_key(source_system, tx_date, asset_id, tx_type, amount_gross)
                    # Guard: don't create a duplicate-key collision if some
                    # other tag row already sits under this exact nk (should
                    # not happen given import dedup — defensive only).
                    collision = self.execute(
                        "SELECT 1 FROM cash_flow_tags WHERE source_table = 'transactions' "
                        "AND source_row_key = ? AND id != ?",
                        [nk, tag_id],
                    ).fetchone()
                    if collision:
                        continue
                    self.execute(
                        "UPDATE cash_flow_tags SET source_row_key = ? WHERE id = ?",
                        [nk, tag_id],
                    )

                # (b) Orphan relink: a tag whose source_row_key resolves to no
                # live transaction, but whose flow_date matches exactly one
                # live transaction (any type/source) — that's a
                # single-candidate, unambiguous relink.
                orphan_rows = self.execute(
                    """
                    SELECT cft.id, cft.flow_date
                    FROM cash_flow_tags cft
                    WHERE cft.source_table = 'transactions'
                      AND NOT STARTS_WITH(cft.source_row_key, 'nk:')
                      AND NOT EXISTS (
                          SELECT 1 FROM transactions tx WHERE CAST(tx.id AS VARCHAR) = cft.source_row_key
                      )
                    """
                ).fetchall()
                for tag_id, flow_date in orphan_rows:
                    if flow_date is None:
                        continue
                    matches = self.execute(
                        """
                        SELECT source_system, transaction_date, asset_id, transaction_type, amount_gross
                        FROM transactions WHERE transaction_date = ?
                        """,
                        [flow_date],
                    ).fetchall()
                    if len(matches) != 1:
                        continue
                    source_system, tx_date, asset_id, tx_type, amount_gross = matches[0]
                    nk = compose_natural_key(source_system, tx_date, asset_id, tx_type, amount_gross)
                    collision = self.execute(
                        "SELECT 1 FROM cash_flow_tags WHERE source_table = 'transactions' "
                        "AND source_row_key = ? AND id != ?",
                        [nk, tag_id],
                    ).fetchone()
                    if collision:
                        continue
                    self.execute(
                        "UPDATE cash_flow_tags SET source_row_key = ? WHERE id = ?",
                        [nk, tag_id],
                    )
                # (c) Any remaining orphans are intentionally left as-is —
                # owner data, never deleted. Surfaced via list_classified_flows
                # orphaned=True instead.
            except Exception as e:
                logger.warning("V81 cash_flow_tags natural-key rekey failed: %s", e, exc_info=True)
                self._migration_failures.append(f"V81 cash_flow_tags natural key: {type(e).__name__}: {e}")
                ok_v81 = False

            if ok_v81:
                self._record_migration(81, "V81 cash_flow_tags stable natural key (re-key + orphan relink)")

        # ── V82: reader_mappings ie_column seed (月度收支 column governance) ────
        # plan docs/plans/2026-08-01-ie-column-mapping-and-ibkr-amounts.md WS-A.
        # ADR-023 made the FS 资产负债 sheet's column→asset mapping DATA
        # (mapping_kind='fs_column'); the 月度收支 sheet's column SEMANTICS stayed
        # hardcoded string literals in src/services/investment_contributions.py,
        # so a column the owner added to the Excel was silently dropped from
        # gross_invested with no error. Same `reader_mappings` table, no schema
        # change — mapping_kind='ie_column', reader_key='financial_summary',
        # map_key = the 月度收支 column header, map_value =
        # {"role", "bucket", "currency"} (see
        # src.database.mapping_seeds.IE_COLUMN_SEED for the vocabulary and the
        # per-column reasoning). Idempotent on the natural UNIQUE key
        # (reader_key, mapping_kind, map_key), same pattern as V75-V78 — never
        # re-burns this version gate even if re-run (memory:
        # migration-decimal-precision-noop).
        already_v82 = self.execute("SELECT 1 FROM schema_version WHERE version = 82").fetchone()
        if not already_v82:
            from src.database.mapping_seeds import IE_COLUMN_SEED_JSON
            ok_v82 = True
            try:
                for sort_order, (map_key, value_dict) in enumerate(IE_COLUMN_SEED_JSON.items()):
                    map_value = json.dumps(value_dict, ensure_ascii=False)
                    self.execute(
                        """
                        INSERT INTO reader_mappings
                            (reader_key, mapping_kind, map_key, map_value, status, sort_order)
                        SELECT 'financial_summary', 'ie_column', ?, ?, 'active', ?
                        WHERE NOT EXISTS (
                            SELECT 1 FROM reader_mappings
                            WHERE reader_key = 'financial_summary'
                              AND mapping_kind = 'ie_column'
                              AND map_key = ?
                        )
                        """,
                        [map_key, map_value, sort_order, map_key],
                    )
            except Exception as e:
                logger.warning("V82 reader_mappings ie_column seed failed: %s", e, exc_info=True)
                self._migration_failures.append(f"V82 reader_mappings ie_column seed: {type(e).__name__}: {e}")
                ok_v82 = False

            if ok_v82:
                self._record_migration(82, "V82 reader_mappings ie_column seed (financial_summary 月度收支)")

        # ── V83: 收入_主动收入_报销 -> role='reimbursement' (ADR-025 Amendment ──
        # 2026-08-01, owner classification). 报销 sits in the 收入 block and so
        # feeds 总收入合计, but it is repayment of money the owner already
        # fronted — not income. It must come OUT of the savings-rate
        # denominator, and it needs its own role rather than 'redemption'
        # because 'redemption' also subtracts from the NUMERATOR
        # (net_external = max(invested − redeemed, 0)), which would be wrong.
        #
        # V82's seed is the source of truth for a fresh DB (IE_COLUMN_SEED
        # already carries the corrected role). This migration exists only for a
        # DB that already applied V82 with the pre-amendment value — V82's
        # natural-key insert is a no-op there and would leave the stale role
        # in place forever.
        #
        # Guarded UPDATE: it only rewrites a row whose map_value is EXACTLY the
        # old seed JSON. If the owner has since edited that mapping in the UI,
        # their edit wins and this migration leaves it alone — the ADR-023
        # model is that DB rows override code, never the reverse.
        already_v83 = self.execute("SELECT 1 FROM schema_version WHERE version = 83").fetchone()
        if not already_v83:
            ok_v83 = True
            try:
                old_value = json.dumps(
                    {"role": "income", "bucket": None, "currency": "CNY"}, ensure_ascii=False
                )
                new_value = json.dumps(
                    {"role": "reimbursement", "bucket": None, "currency": "CNY"}, ensure_ascii=False
                )
                self.execute(
                    """
                    UPDATE reader_mappings
                       SET map_value = ?, updated_at = CURRENT_TIMESTAMP
                     WHERE reader_key = 'financial_summary'
                       AND mapping_kind = 'ie_column'
                       AND map_key = '收入_主动收入_报销'
                       AND map_value = ?
                    """,
                    [new_value, old_value],
                )
            except Exception as e:
                logger.warning("V83 ie_column 报销 role heal failed: %s", e, exc_info=True)
                self._migration_failures.append(f"V83 ie_column 报销 role: {type(e).__name__}: {e}")
                ok_v83 = False

            if ok_v83:
                self._record_migration(83, "V83 ie_column 报销 -> role='reimbursement' (ADR-025 amendment)")

        # ── V84: retire the 'total_income' bucket (owner architectural ruling ──
        # 2026-08-01): 所有 excel 里的计算/合计值都不应该被 Huinsight 读取使用，Huinsight 应该
        # 用自己计算逻辑下的分类汇总保持灵活性和准确性.
        #
        # V82 seeded 总收入合计 as role='income', bucket='total_income' — i.e. the
        # savings-rate denominator was READ from an Excel-computed aggregate.
        # That made every downstream figure depend on whether the workbook's SUM
        # range had auto-expanded over newly inserted columns and correctly
        # excluded the _USD siblings — an invisible dependency living in a
        # spreadsheet. The basis is now the sum of the income LEAF columns
        # (src/services/ie_ledger.py); 总收入合计 becomes role='computed', kept
        # only for classification and for the divergence cross-check.
        #
        # Same guarded-UPDATE shape as V83: only rewrites the row if its
        # map_value is EXACTLY the V82 seed value. An owner edit wins (ADR-023 —
        # DB rows override code, never the reverse). Fresh DBs never need this:
        # IE_COLUMN_SEED already carries role='computed'.
        already_v84 = self.execute("SELECT 1 FROM schema_version WHERE version = 84").fetchone()
        if not already_v84:
            ok_v84 = True
            try:
                old_value = json.dumps(
                    {"role": "income", "bucket": "total_income", "currency": "CNY"},
                    ensure_ascii=False,
                )
                new_value = json.dumps(
                    {"role": "computed", "bucket": None, "currency": "CNY"}, ensure_ascii=False
                )
                self.execute(
                    """
                    UPDATE reader_mappings
                       SET map_value = ?, updated_at = CURRENT_TIMESTAMP
                     WHERE reader_key = 'financial_summary'
                       AND mapping_kind = 'ie_column'
                       AND map_key = '总收入合计'
                       AND map_value = ?
                    """,
                    [new_value, old_value],
                )
            except Exception as e:
                logger.warning("V84 ie_column total_income retirement failed: %s", e, exc_info=True)
                self._migration_failures.append(f"V84 ie_column total_income: {type(e).__name__}: {e}")
                ok_v84 = False

            if ok_v84:
                self._record_migration(84, "V84 ie_column 总收入合计 -> role='computed' (no Excel aggregate is an input)")

        # ── V85: pass_through pairing (ADR-025 Amendment 2026-08-01, owner) ────
        # `收入_主动收入_报销` and `工作开支_出差/团建（全额报销）` are the two ends
        # of the SAME money: the owner fronts a work expense and is repaid.
        # Neither is real income nor real consumption, so both are excluded from
        # BOTH the income basis and the expense basis of the savings/investment
        # rates. One shared role (`pass_through`, with bucket 'inflow'/'outflow'
        # naming the end) makes that pairing structural — two unrelated
        # exclusions could be broken later by fixing only one side.
        #
        # Heals DBs seeded by V82 (报销 as 'income'), by V83 (报销 as
        # 'reimbursement' — a short-lived role retired the same day), and V82's
        # 工作开支 as 'expense'. Guarded on the exact prior seed values, so an
        # owner edit always wins (ADR-023). Fresh DBs need none of this:
        # IE_COLUMN_SEED already carries the final values.
        already_v85 = self.execute("SELECT 1 FROM schema_version WHERE version = 85").fetchone()
        if not already_v85:
            ok_v85 = True
            try:
                def _ie_value(role, bucket, group):
                    value = {"role": role, "bucket": bucket, "currency": "CNY"}
                    if group:
                        value["group"] = group
                    return json.dumps(value, ensure_ascii=False)

                heals = [
                    # (map_key, [prior values to match], new value)
                    (
                        "收入_主动收入_报销",
                        [
                            _ie_value("income", None, None),
                            _ie_value("income", None, "active_income"),
                            _ie_value("reimbursement", None, None),
                            _ie_value("reimbursement", None, "active_income"),
                        ],
                        _ie_value("pass_through", "inflow", "active_income"),
                    ),
                    (
                        "工作开支_出差/团建（全额报销）",
                        [
                            _ie_value("expense", None, None),
                            _ie_value("expense", None, "work_expense"),
                        ],
                        _ie_value("pass_through", "outflow", "work_expense"),
                    ),
                ]
                for map_key, old_values, new_value in heals:
                    placeholders = ", ".join("?" for _ in old_values)
                    self.execute(
                        f"""
                        UPDATE reader_mappings
                           SET map_value = ?, updated_at = CURRENT_TIMESTAMP
                         WHERE reader_key = 'financial_summary'
                           AND mapping_kind = 'ie_column'
                           AND map_key = ?
                           AND map_value IN ({placeholders})
                        """,
                        [new_value, map_key, *old_values],
                    )
            except Exception as e:
                logger.warning("V85 ie_column pass_through pairing failed: %s", e, exc_info=True)
                self._migration_failures.append(f"V85 ie_column pass_through: {type(e).__name__}: {e}")
                ok_v85 = False

            if ok_v85:
                self._record_migration(85, "V85 ie_column 报销/工作开支 -> role='pass_through' (in/out pair)")

        # ── V86: manual_asset_pnl + manual_asset_pnl_audit (#7, plan §C.1/C.3) ──
        # Owner-entered P&L for bank-bought assets the readers cannot price:
        # money-market / 理财 / 债券 / 美元债. The owner knows these as outcomes
        # ("I put in X, it earned Y"), not as trades, so this is a direct P&L
        # override keyed per asset — NOT synthetic transactions (rejected, §C.1).
        #
        # Both figures nullable and independently meaningful:
        #   cost_basis_cny    -> unrealized = market - cost  (non-cash assets only)
        #   realized_pnl_cny  -> cumulative realized profit to date
        # An upsert with BOTH null is rejected at the API (400), not here.
        #
        # No `currency` column by design: both figures are CNY by definition, and
        # a currency field would invite a second, contradicting convention.
        #
        # This is OWNER DATA — no reader or sync phase may ever write it, which is
        # what makes it inherently re-sync-safe (guard test pins that).
        #
        # ⚑ The audit timestamp is `changed_at`, NOT `at`: DuckDB reserves `at`,
        # and reader_mapping_audit already has to quote it (connector.py:1100).
        already_v86 = self.execute("SELECT 1 FROM schema_version WHERE version = 86").fetchone()
        if not already_v86:
            ok_v86 = True
            for _stmt in [
                """CREATE TABLE IF NOT EXISTS manual_asset_pnl (
                       asset_id VARCHAR PRIMARY KEY,
                       cost_basis_cny DECIMAL(20,2),
                       realized_pnl_cny DECIMAL(20,2),
                       as_of_date DATE,
                       memo VARCHAR,
                       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                       updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                   )""",
                "CREATE SEQUENCE IF NOT EXISTS seq_manual_asset_pnl_audit_id START 1",
                """CREATE TABLE IF NOT EXISTS manual_asset_pnl_audit (
                       id INTEGER PRIMARY KEY DEFAULT nextval('seq_manual_asset_pnl_audit_id'),
                       asset_id VARCHAR NOT NULL,
                       action VARCHAR NOT NULL,
                       old_value VARCHAR,
                       new_value VARCHAR,
                       changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                   )""",
            ]:
                if not self._run_migration("V86 manual_asset_pnl", _stmt):
                    ok_v86 = False

            if ok_v86:
                self._record_migration(86, "V86 manual_asset_pnl + audit (#7 owner-logged P&L)")

        # ── V87: manual_asset_pnl.market_value_at_log (#7 staleness signal) ────
        # A logged cost is a CUMULATIVE figure for the whole position, so it goes
        # stale the moment the position changes size: buy ¥50K more of a ¥200K bond
        # and the untouched ¥200K cost turns the new principal into ¥50K of phantom
        # profit; sell ¥50K and it becomes a phantom loss. Nothing in the reader
        # data can tell us a buy happened — these assets have no transaction ledger,
        # which is the whole reason they are logged by hand.
        #
        # So record the market value AT THE MOMENT OF LOGGING. Comparing it to the
        # current value gives an honest "this looks out of date" prompt. It cannot
        # distinguish a deposit from interest accrual, which is why it is a warning
        # keyed on a material percentage move, never an automatic adjustment —
        # guessing at the owner's cost is exactly the phantom V7.8.3 removed.
        already_v87 = self.execute("SELECT 1 FROM schema_version WHERE version = 87").fetchone()
        if not already_v87:
            if self._run_migration(
                "V87 manual_asset_pnl.market_value_at_log",
                "ALTER TABLE manual_asset_pnl ADD COLUMN market_value_at_log DECIMAL(20,2)",
            ):
                self._record_migration(
                    87, "V87 manual_asset_pnl.market_value_at_log (#7 staleness signal)"
                )

        # ── V88: clear the manufactured Pension_ cost (owner ruling 2026-08-09) ──
        # `_zero_pl_for_non_tradeable_assets` used to stamp cost_price_unit =
        # market_value for Pension_*, so 个人养老金 reported a manufactured "+¥0.00"
        # every sync — a fake measurement where a dash would honestly say the cost is
        # unknown. `Pension_` has been removed from NON_TRADEABLE_PREFIXES, but that
        # only stops FUTURE stamping; the value already written stays until cleared.
        #
        # Scoped by the fake-zero SIGNATURE (cost == market value within a cent), not
        # by an exact equality on a decimal — an exact `= <literal>` match is how a
        # past data-fix silently matched 0 rows while burning its version gate
        # (memory: migration-decimal-precision-noop). A pension row carrying a
        # genuinely different cost is left alone.
        #
        # Net worth is untouched: market_value is not read or written here.
        already_v88 = self.execute("SELECT 1 FROM schema_version WHERE version = 88").fetchone()
        if not already_v88:
            if self._run_migration(
                "V88 clear manufactured Pension_ cost",
                """
                UPDATE holdings
                SET cost_price_unit = NULL
                WHERE asset_id LIKE 'Pension\\_%' ESCAPE '\\'
                  AND cost_price_unit IS NOT NULL
                  AND market_value IS NOT NULL
                  AND ABS(cost_price_unit - market_value) < 0.01
                """,
            ):
                self._record_migration(
                    88, "V88 clear manufactured Pension_ cost (个人养老金 -> unknown, not fake 0)"
                )

        # ── V89: user_profile.language (Program BIL / WS-5) ────────────────────
        # The AI advisor needs to know what language to write in. Interactive
        # generation can send the browser's locale; a SCHEDULED job cannot. So the
        # preference is persisted, and this is where it gets its first value.
        #
        # Two things this migration deliberately does NOT do:
        #
        # 1. It does not rely on `ADD COLUMN ... DEFAULT 'en'` to backfill. DuckDB's
        #    backfill semantics for that form are unverified in this repo, and the
        #    sibling column (`philosophy`) was added without one. A DEFAULT that
        #    silently fails to reach the existing row would flip the owner's briefs
        #    to English — the single worst outcome of this workstream.
        #
        # 2. It does not blanket-set 'zh-CN'. A fresh/public install must land on
        #    'en'. The discriminator is EVIDENCE, not a guess: an instance that
        #    already stores Chinese-keyed AI reports has demonstrably been producing
        #    Chinese output, so it keeps producing Chinese. A brand-new database has
        #    no such rows and is left NULL, which resolves to 'en'.
        #
        # An explicit owner choice is never overwritten (`language IS NULL` guard),
        # and a NULL outcome is logged as a WARNING rather than left invisible.
        already_v89 = self.execute("SELECT 1 FROM schema_version WHERE version = 89").fetchone()
        if not already_v89:
            if self._run_migration(
                "V89 user_profile.language",
                "ALTER TABLE user_profile ADD COLUMN language VARCHAR",
            ):
                self._seed_profile_language()
                self._record_migration(
                    89, "V89 user_profile.language (Program BIL — persisted advisor language)"
                )

        # ── V192: seed the default asset-class taxonomy ────────────────────────
        # taxonomy_classes had no seed until 2026-08-30, so every install that
        # was not the owner's started with an empty classification table and
        # resolved almost every holding to 'Unclassified' — allocation and
        # attribution both look broken on a first run. See
        # src/database/taxonomy_seeds.py for the full account.
        #
        # Idempotent on the natural key (name), so re-running never duplicates,
        # and an existing database that already has these classes is left
        # exactly as it is — this adds what is missing, it does not reconcile.
        already_v192 = self.execute(
            "SELECT 1 FROM schema_version WHERE version = 192"
        ).fetchone()
        if not already_v192:
            if self._seed_default_taxonomy():
                self._record_migration(192, "V192 default taxonomy_classes seed")

        # ── V193: seed the default risk profiles and their class targets ──────
        # risk_profiles had no seed until 2026-09-02, so every install that was
        # not the owner's had no active profile — and with no active profile the
        # Allocation Report resolves every class target to 0%, flagging 100% of
        # a perfectly sensible portfolio as over target. See
        # src/database/risk_profile_seeds.py for the full account.
        #
        # Runs after V192 by necessity: targets are stored against
        # taxonomy_classes.id, so the classes must exist first.
        already_v193 = self.execute(
            "SELECT 1 FROM schema_version WHERE version = 193"
        ).fetchone()
        if not already_v193:
            if self._seed_default_risk_profiles():
                self._record_migration(193, "V193 default risk_profiles seed")

    def _seed_default_risk_profiles(self) -> bool:
        """Insert any missing default risk profiles. Returns True on success.

        Returns False (leaving the version gate unburned, so the next startup
        retries) if anything goes wrong — a profile seeded without its
        allocations is worse than no profile, because it presents an active
        target set that is silently all-zero, which is the exact bug this seed
        exists to remove.

        Two things it deliberately does not do:

        - It never changes which profile is active if one already is. The
          owner's database has four hand-entered profiles with 均衡型 active;
          this must add what is missing, not campaign for its own default.
        - It never rewrites the allocations of a profile that already exists by
          name, for the same reason.
        """
        from src.database.risk_profile_seeds import ALLOCATIONS, PROFILES

        try:
            class_ids: dict[str, int] = {}
            for row in self.execute("SELECT name, id FROM taxonomy_classes").fetchall():
                if row and row[0] is not None:
                    class_ids[str(row[0])] = int(row[1])
            if not class_ids:
                logger.warning("V193: taxonomy_classes is empty — deferring risk-profile seed")
                return False

            # Only a database with *no* profiles at all has the bug this seed
            # exists to fix. A database that already has profiles has already
            # answered the question, and adding four more would be clutter in
            # someone's live instance rather than a fix — the owner's own
            # database has four hand-entered Chinese-named profiles, and this
            # migration must be invisible to it.
            #
            # This is the difference from V192: a *missing taxonomy class*
            # silently breaks classification, so that seed adds what is absent
            # to any database. A missing profile breaks nothing once one exists.
            existing = self.execute("SELECT COUNT(*) FROM risk_profiles").fetchone()
            if existing and existing[0] > 0:
                logger.info(
                    "V193: %s risk profile(s) already present — leaving them alone",
                    existing[0],
                )
                return True

            has_active = False
            seeded = 0
            for name, description, is_default_active in PROFILES:
                existing = self.execute(
                    "SELECT 1 FROM risk_profiles WHERE name = ?", [name]
                ).fetchone()
                if existing:
                    continue

                # risk_profiles.id has no sequence default — RiskProfileManager
                # assigns MAX(id)+1 by hand, so the seed must do the same.
                profile_id = self.execute(
                    "SELECT COALESCE(MAX(id), 0) + 1 FROM risk_profiles"
                ).fetchone()[0]
                self.execute(
                    """
                    INSERT INTO risk_profiles (id, name, name_en, is_active, description)
                    VALUES (?, ?, NULL, ?, ?)
                    """,
                    [profile_id, name, bool(is_default_active) and not has_active, description],
                )

                for class_name, target_pct in ALLOCATIONS.get(name, {}).items():
                    cls_id = class_ids.get(class_name)
                    if cls_id is None:
                        logger.warning(
                            "V193: no taxonomy class %r for %r target — skipping",
                            class_name, name,
                        )
                        continue
                    alloc_id = self.execute(
                        "SELECT COALESCE(MAX(id), 0) + 1 FROM risk_profile_allocations"
                    ).fetchone()[0]
                    self.execute(
                        """
                        INSERT INTO risk_profile_allocations (id, profile_id, class_id, target_pct)
                        VALUES (?, ?, ?, ?)
                        """,
                        [alloc_id, profile_id, cls_id, float(target_pct)],
                    )
                seeded += 1

            active_now = self.execute(
                "SELECT COUNT(*) FROM risk_profiles WHERE is_active = TRUE"
            ).fetchone()
            logger.info(
                "V193: seeded %s risk profiles (%s active in total)",
                seeded, active_now[0] if active_now else "?",
            )
            return True
        except Exception as e:
            logger.warning("V193 default risk-profile seed failed (will retry next start): %s", e)
            return False

    def _seed_default_taxonomy(self) -> bool:
        """Insert any missing default taxonomy classes. Returns True on success.

        Returns False (leaving the version gate unburned, so the next startup
        retries) if anything goes wrong — a half-seeded taxonomy is worse than
        an unseeded one, because the gaps are silent.
        """
        from src.database.taxonomy_seeds import (
            SUB_CLASSES,
            TOP_LEVEL_CLASSES,
            is_rebalanceable,
        )

        try:
            def _insert(name: str, name_cn: str, parent_id, level: int, sort_order: int) -> None:
                # taxonomy_classes.id has no sequence default — TaxonomyManager
                # assigns MAX(id)+1 by hand, so the seed must do the same or the
                # NOT NULL constraint rejects the row.
                exists = self.execute(
                    "SELECT 1 FROM taxonomy_classes WHERE name = ?", [name]
                ).fetchone()
                if exists:
                    return
                next_id = self.execute(
                    "SELECT COALESCE(MAX(id), 0) + 1 FROM taxonomy_classes"
                ).fetchone()[0]
                self.execute(
                    """
                    INSERT INTO taxonomy_classes
                        (id, name, name_cn, parent_id, level, sort_order, is_rebalanceable)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [next_id, name, name_cn, parent_id, level, sort_order,
                     is_rebalanceable(name)],
                )

            for name, name_cn, sort_order in TOP_LEVEL_CLASSES:
                _insert(name, name_cn, None, 0, sort_order)

            for parent_name, children in SUB_CLASSES.items():
                row = self.execute(
                    "SELECT id FROM taxonomy_classes WHERE name = ?", [parent_name]
                ).fetchone()
                if not row:
                    logger.warning(
                        "V192: parent class %r missing — skipping its sub-classes",
                        parent_name,
                    )
                    continue
                for name, name_cn in children:
                    _insert(name, name_cn, row[0], 1, 0)

            # ── Tiers ────────────────────────────────────────────────────────
            from src.database.taxonomy_seeds import ASSET_TIERS, ID_PREFIX_RULES

            for sort_order, (tier_id, tier_name) in enumerate(ASSET_TIERS):
                self.execute(
                    """
                    INSERT INTO asset_tiers (id, name, target_pct, sort_order)
                    SELECT ?, ?, 0, ?
                    WHERE NOT EXISTS (SELECT 1 FROM asset_tiers WHERE id = ?)
                    """,
                    [tier_id, tier_name, sort_order, tier_id],
                )

            # ── Asset-ID fallback rules ──────────────────────────────────────
            # Without these the classifier has nothing to work with on an
            # uncurated database: its other three strategies all match on
            # values a person entered. See taxonomy_seeds.ID_PREFIX_RULES.
            for priority, pattern, class_name in ID_PREFIX_RULES:
                cls = self.execute(
                    "SELECT id FROM taxonomy_classes WHERE name = ?", [class_name]
                ).fetchone()
                if not cls:
                    logger.warning(
                        "V192: no taxonomy class %r for id_regex %r — skipping",
                        class_name, pattern,
                    )
                    continue
                exists = self.execute(
                    "SELECT 1 FROM classification_rules WHERE rule_type = 'id_regex' AND pattern = ?",
                    [pattern],
                ).fetchone()
                if exists:
                    continue
                next_id = self.execute(
                    "SELECT COALESCE(MAX(id), 0) + 1 FROM classification_rules"
                ).fetchone()[0]
                self.execute(
                    """
                    INSERT INTO classification_rules
                        (id, rule_type, pattern, class_id, priority, source)
                    VALUES (?, 'id_regex', ?, ?, ?, 'default_seed')
                    """,
                    [next_id, pattern, cls[0], priority],
                )

            total = self.execute("SELECT COUNT(*) FROM taxonomy_classes").fetchone()
            rules = self.execute(
                "SELECT COUNT(*) FROM classification_rules WHERE rule_type = 'id_regex'"
            ).fetchone()
            logger.info(
                "V192: %s taxonomy classes, %s id_regex fallback rules",
                total[0] if total else "?", rules[0] if rules else "?",
            )
            return True
        except Exception as e:
            logger.warning("V192 default taxonomy seed failed (will retry next start): %s", e)
            return False

    def _seed_profile_language(self) -> None:
        """Explicit data step for V89. Never raises — a failure here degrades to 'en'."""
        try:
            row = self.execute(
                """
                SELECT COUNT(*) FROM ai_reports
                WHERE report_type IN ('brief', 'review')
                  AND content_json IS NOT NULL
                  AND (content_json LIKE '%宏观形势%'
                       OR content_json LIKE '%持仓分析与风险预警%'
                       OR content_json LIKE '%交易汇总%'
                       OR content_json LIKE '%经验沉淀%'
                       OR content_json LIKE '%宏觀形勢%'
                       OR content_json LIKE '%交易匯總%')
                """
            ).fetchone()
            chinese_report_count = int(row[0]) if row else 0
        except Exception as e:
            logger.warning("V89: could not inspect ai_reports for language evidence: %s", e)
            chinese_report_count = 0

        if chinese_report_count > 0:
            try:
                # UPSERT, not UPDATE: user_profile can legitimately have NO row
                # (it does on a DB where the profile was never saved), and an
                # UPDATE against zero rows is a silent no-op that still burns the
                # version gate. That exact shape has bitten this repo before.
                self.execute(
                    """
                    INSERT INTO user_profile (id, language, updated_at)
                    VALUES (1, 'zh-CN', CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        language = excluded.language,
                        updated_at = excluded.updated_at
                    WHERE user_profile.language IS NULL
                    """
                )
                logger.info(
                    "V89: %d Chinese-keyed AI report(s) found — user_profile.language "
                    "set to 'zh-CN' (existing instance keeps producing Chinese)",
                    chinese_report_count,
                )
            except Exception as e:
                logger.warning("V89: language seed write failed: %s", e)

        try:
            row = self.execute(
                "SELECT language FROM user_profile WHERE id = 1"
            ).fetchone()
        except Exception:
            row = None

        if row is None or not row[0]:
            logger.warning(
                "V89: user_profile.language is NULL (no prior Chinese AI reports "
                "found). AI advisor output falls back to settings.yaml, then 'en'. "
                "Set it explicitly if this instance should generate in another language."
            )


# Transient DuckDB conflict markers — a writer (GCS flush CHECKPOINT, or a write
# request via get_writable_db) briefly holds a read-write connection; DuckDB forbids
# mixing read-only and read-write connections to the same file within one process.
_TRANSIENT_CONFLICT_MARKERS = (
    "different configuration than existing connections",
    "could not set lock",
    "conflicting lock",
)


def is_transient_conflict(exc: Exception) -> bool:
    """True if the exception is a transient DuckDB mixed-mode / lock conflict."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_CONFLICT_MARKERS)


def connect_readonly_with_retry(
    db_path: Optional[str] = None, attempts: int = 6, base_delay: float = 0.1
) -> "DatabaseConnector":
    """Open a READ-ONLY DatabaseConnector, retrying transient mixed-mode/lock
    conflicts with a short backoff (~2.1s budget by default; a writer's hold is brief).
    Raises the last exception if it never clears.

    ALL read paths (auth middleware, login credential read, get_db) must use this so
    they stay read-only and only ever contend — briefly — with genuine writers, never
    with each other. Mixing read-only and read-write opens per-request was the cause of
    the auth 401 storm + dashboard 500s.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            if db_path is None:
                return DatabaseConnector(read_only=True)
            return DatabaseConnector(db_path, read_only=True)
        except Exception as exc:
            if not is_transient_conflict(exc):
                raise
            last_exc = exc
            time.sleep(base_delay * (attempt + 1))
    assert last_exc is not None
    raise last_exc
