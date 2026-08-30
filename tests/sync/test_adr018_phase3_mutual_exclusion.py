"""Tests for ADR-018 Phase 3: mutual-exclusion between DB-staging and config-driven
reader paths, keyed on import_adapter_approvals.generated_reader_key.

Coverage:
  1. sync_approved_import_adapters skips adapters where generated_reader_key IS NOT NULL.
  2. _load_adapter_authority_rules skips adapters where generated_reader_key IS NOT NULL.
  3. reset_registry() drops the cached singleton.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from src.database.connector import DatabaseConnector


# ---------------------------------------------------------------------------
# Shared DB setup helpers
# ---------------------------------------------------------------------------

def _create_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the minimal tables needed for these tests (inline DDL, no schema.sql)."""
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_holdings_id START 1;
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_holdings_id'),
            snapshot_date DATE NOT NULL,
            asset_id VARCHAR(50) NOT NULL,
            asset_name VARCHAR(200),
            asset_type VARCHAR(100),
            quantity DECIMAL(20,8),
            unit VARCHAR(20),
            cost_price_unit DECIMAL(20,8),
            market_price_unit DECIMAL(20,8),
            market_value DECIMAL(20,2),
            currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
            account VARCHAR(100),
            source_system VARCHAR(50),
            derived_from_transaction_id INTEGER,
            verified BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_shadow BOOLEAN DEFAULT FALSE,
            authority_source VARCHAR(50),
            price_updated_at TIMESTAMP,
            price_source VARCHAR,
            UNIQUE(snapshot_date, asset_id, source_system)
        )
    """)
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_transactions_id START 1;
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_transactions_id'),
            transaction_date DATE NOT NULL,
            asset_id VARCHAR(50) NOT NULL,
            asset_name VARCHAR(200),
            transaction_type VARCHAR(50) NOT NULL,
            quantity DECIMAL(20,8),
            price_unit DECIMAL(20,8),
            amount_gross DECIMAL(20,2),
            amount_net DECIMAL(20,2),
            commission_fee DECIMAL(20,4),
            currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
            account VARCHAR(100),
            memo TEXT,
            source_system VARCHAR(50),
            verified BOOLEAN DEFAULT FALSE,
            is_provisional BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(transaction_date, asset_id, source_system, transaction_type)
        )
    """)
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_import_adapter_runs_id START 1;
        CREATE TABLE IF NOT EXISTS import_adapter_runs (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_import_adapter_runs_id'),
            adapter_key VARCHAR NOT NULL,
            import_type VARCHAR NOT NULL,
            filename VARCHAR,
            file_path VARCHAR,
            column_mapping JSON,
            status VARCHAR NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_import_adapter_staged_rows_id START 1;
        CREATE TABLE IF NOT EXISTS import_adapter_staged_rows (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_import_adapter_staged_rows_id'),
            run_id INTEGER NOT NULL,
            row_index INTEGER NOT NULL,
            row_kind VARCHAR NOT NULL,
            normalized_payload_json JSON NOT NULL,
            validation_status VARCHAR NOT NULL,
            validation_messages_json JSON,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            synced_at TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS import_adapter_approvals (
            adapter_key VARCHAR PRIMARY KEY,
            approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            approved_by VARCHAR,
            source_system VARCHAR NOT NULL,
            asset_prefixes_json JSON NOT NULL,
            authority_priority INTEGER NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            generated_reader_key VARCHAR
        )
    """)


def _init_db(db_path: Path) -> None:
    conn = duckdb.connect(str(db_path))
    _create_tables(conn)

    # --- Adapter 1: one-time-import (generated_reader_key IS NULL) ---
    conn.execute("""
        INSERT INTO import_adapter_runs(id, adapter_key, import_type, filename, status)
        VALUES (1, 'one_time_import', 'holdings', 'one.csv', 'staged')
    """)
    conn.execute("""
        INSERT INTO import_adapter_approvals
            (adapter_key, source_system, asset_prefixes_json, authority_priority,
             enabled, generated_reader_key)
        VALUES ('one_time_import', 'Adapter_OneTime', '["OT_"]', 5, TRUE, NULL)
    """)
    conn.execute("""
        INSERT INTO import_adapter_staged_rows
            (run_id, row_index, row_kind, normalized_payload_json,
             validation_status, validation_messages_json)
        VALUES (1, 0, 'holding',
                '{"asset_id":"OT_STOCK","snapshot_date":"2026-06-01",
                  "quantity":10,"market_value":5000,"currency":"CNY"}',
                'valid', '[]')
    """)

    # --- Adapter 2: reader-backed (generated_reader_key = 'custom_x') ---
    conn.execute("""
        INSERT INTO import_adapter_runs(id, adapter_key, import_type, filename, status)
        VALUES (2, 'reader_backed', 'holdings', 'reader.csv', 'staged')
    """)
    conn.execute("""
        INSERT INTO import_adapter_approvals
            (adapter_key, source_system, asset_prefixes_json, authority_priority,
             enabled, generated_reader_key)
        VALUES ('reader_backed', 'Adapter_ReaderBacked', '["RB_"]', 6, TRUE, 'custom_x')
    """)
    conn.execute("""
        INSERT INTO import_adapter_staged_rows
            (run_id, row_index, row_kind, normalized_payload_json,
             validation_status, validation_messages_json)
        VALUES (2, 0, 'holding',
                '{"asset_id":"RB_STOCK","snapshot_date":"2026-06-01",
                  "quantity":5,"market_value":3000,"currency":"CNY"}',
                'valid', '[]')
    """)

    conn.close()


# ---------------------------------------------------------------------------
# _FakeConnector (mirrors test_adapter_authority_injection.py pattern)
# ---------------------------------------------------------------------------

class _FakeConnector:
    """Minimal wrapper so _load_adapter_authority_rules can call .execute()."""
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn

    def execute(self, sql, params=None):
        if params:
            return self._conn.execute(sql, list(params))
        return self._conn.execute(sql)


# ---------------------------------------------------------------------------
# Tests: sync_approved_import_adapters mutual exclusion
# ---------------------------------------------------------------------------

class TestSyncMutualExclusion:
    """Only adapters with generated_reader_key IS NULL are ingested via DB-staging."""

    def test_only_null_reader_key_rows_are_ingested(self, tmp_path: Path):
        db = tmp_path / "test.duckdb"
        _init_db(db)

        from src.import_adapters.sync import sync_approved_import_adapters

        with DatabaseConnector(str(db)) as connector:
            counts = sync_approved_import_adapters(connector, {})

        # Only OT_STOCK (one_time_import, reader_key=NULL) should be ingested
        assert counts["holdings"] == 1

        with DatabaseConnector(str(db)) as connector:
            row = connector.execute(
                "SELECT asset_id FROM holdings WHERE asset_id = 'OT_STOCK'"
            ).fetchone()
            assert row is not None, "OT_STOCK (null reader_key adapter) should be ingested"

            # RB_STOCK must NOT be in holdings (reader-backed adapter must be skipped)
            rb_row = connector.execute(
                "SELECT asset_id FROM holdings WHERE asset_id = 'RB_STOCK'"
            ).fetchone()
            assert rb_row is None, "RB_STOCK (reader-backed adapter) must NOT be ingested via DB-staging"

    def test_reader_backed_staged_rows_remain_unsynced(self, tmp_path: Path):
        """The reader-backed adapter's staged rows must NOT have synced_at set."""
        db = tmp_path / "test.duckdb"
        _init_db(db)

        from src.import_adapters.sync import sync_approved_import_adapters

        with DatabaseConnector(str(db)) as connector:
            sync_approved_import_adapters(connector, {})

            # one_time_import's staged row should be marked synced
            ot_row = connector.execute(
                "SELECT synced_at FROM import_adapter_staged_rows WHERE run_id = 1"
            ).fetchone()
            assert ot_row[0] is not None, "One-time-import staged row must be marked synced"

            # reader_backed's staged row must NOT be marked synced
            rb_row = connector.execute(
                "SELECT synced_at FROM import_adapter_staged_rows WHERE run_id = 2"
            ).fetchone()
            assert rb_row[0] is None, "Reader-backed staged row must remain unsynced"

    def test_null_key_column_absent_falls_back_gracefully(self, tmp_path: Path):
        """If the column doesn't exist (old DB without V56), the WHERE IS NULL condition
        would fail. Verify the new schema (from schema.sql) always includes the column."""
        db = tmp_path / "fallback.duckdb"
        conn = duckdb.connect(str(db))
        _create_tables(conn)
        # Confirm column exists
        cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'import_adapter_approvals'"
        ).fetchall()]
        assert "generated_reader_key" in cols, "generated_reader_key must be in schema.sql DDL"
        conn.close()


# ---------------------------------------------------------------------------
# Tests: _load_adapter_authority_rules mutual exclusion
# ---------------------------------------------------------------------------

class TestAuthorityRulesMutualExclusion:
    """_load_adapter_authority_rules must skip reader-backed adapters."""

    @pytest.fixture
    def db_conn(self, tmp_path: Path):
        conn = duckdb.connect(str(tmp_path / "auth.duckdb"))
        conn.execute("""
            CREATE TABLE import_adapter_approvals (
                adapter_key VARCHAR PRIMARY KEY,
                approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                approved_by VARCHAR,
                source_system VARCHAR NOT NULL,
                asset_prefixes_json JSON NOT NULL,
                authority_priority INTEGER NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                generated_reader_key VARCHAR
            )
        """)
        return conn

    def _insert(self, conn, adapter_key, source_system, prefixes, priority,
                enabled=True, generated_reader_key=None):
        conn.execute(
            "INSERT INTO import_adapter_approvals "
            "(adapter_key, source_system, asset_prefixes_json, authority_priority, "
            " enabled, generated_reader_key) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [adapter_key, source_system, json.dumps(prefixes), priority,
             enabled, generated_reader_key],
        )

    def test_only_null_reader_key_yields_rule(self, db_conn):
        from src.sync.phases._post_reader import _load_adapter_authority_rules

        self._insert(db_conn, "one_time", "Adapter_OneTime", ["OT_"], 5,
                     generated_reader_key=None)
        self._insert(db_conn, "reader_backed", "Adapter_ReaderBacked", ["RB_"], 6,
                     generated_reader_key="custom_x")

        rules = _load_adapter_authority_rules(_FakeConnector(db_conn))

        # Only the NULL-key adapter produces a rule
        assert len(rules) == 1, f"Expected 1 rule, got {len(rules)}: {rules}"
        assert rules[0]["authority"] == "Adapter_OneTime"
        assert rules[0]["pattern"] == "OT_*"

    def test_reader_backed_adapter_not_injected(self, db_conn):
        from src.sync.phases._post_reader import _load_adapter_authority_rules

        self._insert(db_conn, "reader_backed", "Adapter_ReaderBacked", ["RB_"], 6,
                     generated_reader_key="custom_x")

        rules = _load_adapter_authority_rules(_FakeConnector(db_conn))
        assert rules == [], "Reader-backed adapter must yield no dynamic authority rules"

    def test_multiple_reader_backed_all_excluded(self, db_conn):
        from src.sync.phases._post_reader import _load_adapter_authority_rules

        self._insert(db_conn, "rb_a", "Adapter_A", ["A_"], 5,
                     generated_reader_key="reader_a")
        self._insert(db_conn, "rb_b", "Adapter_B", ["B_"], 6,
                     generated_reader_key="reader_b")
        self._insert(db_conn, "one_time", "Adapter_C", ["C_"], 7,
                     generated_reader_key=None)

        rules = _load_adapter_authority_rules(_FakeConnector(db_conn))
        authorities = {r["authority"] for r in rules}
        assert "Adapter_C" in authorities
        assert "Adapter_A" not in authorities
        assert "Adapter_B" not in authorities


# ---------------------------------------------------------------------------
# Tests: reset_registry()
# ---------------------------------------------------------------------------

class TestResetRegistry:
    """reset_registry() drops the singleton so the next get_registry() reloads."""

    def _seed_fake_singleton(self, reg_module) -> object:
        """Plant a non-None object into _registry_instance without going through
        _load_registry (which validates canonical source systems).  We test only
        the cache-drop mechanics here, not registry loading."""
        sentinel = object()
        with reg_module._registry_lock:
            reg_module._registry_instance = sentinel  # type: ignore[assignment]
        return sentinel

    def test_reset_clears_singleton(self):
        """After reset_registry(), _registry_instance must be None."""
        import src.sources.registry as reg_module
        from src.sources.registry import reset_registry

        sentinel = self._seed_fake_singleton(reg_module)
        assert reg_module._registry_instance is sentinel, "Pre-condition: singleton must be set"

        reset_registry()

        assert reg_module._registry_instance is None, \
            "After reset_registry(), _registry_instance must be None"

    def test_reset_is_idempotent_when_already_none(self):
        """Calling reset_registry() when instance is already None must not raise."""
        import src.sources.registry as reg_module
        from src.sources.registry import reset_registry

        with reg_module._registry_lock:
            reg_module._registry_instance = None

        # Must not raise
        reset_registry()
        assert reg_module._registry_instance is None

    def test_generate_reader_artifacts_calls_reset(self, tmp_path: Path):
        """generate_reader_artifacts() must call reset_registry() so a new reader
        YAML is visible to the next get_registry() call."""
        import src.sources.registry as reg_module
        from src.import_adapters.reader_generator import generate_reader_artifacts

        # Seed a fake non-None singleton (bypasses SourceRegistry validation)
        self._seed_fake_singleton(reg_module)
        assert reg_module._registry_instance is not None

        config_dir = tmp_path / "readers"
        config_dir.mkdir(parents=True, exist_ok=True)
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("symbol,qty\nAAPL,10\n", encoding="utf-8")

        generate_reader_artifacts(
            reader_key="new_reader",
            source_system="New_Reader",
            display_name="New Reader",
            asset_prefixes=["NR_"],
            authority_priority=7,
            column_mapping={"asset_id": "symbol", "quantity": "qty"},
            fx_rate=None,
            import_type="holdings",
            upload_file_path=str(csv_file),
            file_format="csv",
            config_readers_dir=config_dir,
            settings_path=tmp_path / "settings.yaml",
            authority_path=tmp_path / "authority.yaml",
            data_dir_root=tmp_path / "data",
        )

        assert reg_module._registry_instance is None, \
            "generate_reader_artifacts() must reset the singleton after writing artifacts"
