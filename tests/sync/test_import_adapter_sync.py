from pathlib import Path

import duckdb

from src.database.connector import DatabaseConnector
from src.import_adapters.sync import get_approved_adapter_source_systems, sync_approved_import_adapters


def _init_db(db_path: Path):
    conn = duckdb.connect(str(db_path))
    conn.execute(Path("src/database/schema.sql").read_text(encoding="utf-8"))
    conn.execute("INSERT INTO import_adapter_runs(id, adapter_key, import_type, filename, status) VALUES (1, 'demo', 'holdings', 'a.csv', 'staged')")
    conn.execute("INSERT INTO import_adapter_approvals(adapter_key, source_system, asset_prefixes_json, authority_priority, enabled) VALUES ('demo', 'Adapter_Demo', '[\"US_\"]', 1, TRUE)")
    conn.execute("INSERT INTO import_adapter_staged_rows(run_id, row_index, row_kind, normalized_payload_json, validation_status, validation_messages_json) VALUES (1, 0, 'holding', '{\"asset_id\":\"US_AAPL\",\"snapshot_date\":\"2026-05-09\",\"quantity\":1,\"market_value\":1000,\"currency\":\"CNY\"}', 'valid', '[]')")
    conn.close()


def test_sync_approved_rows(tmp_path: Path):
    db = tmp_path / "test.duckdb"
    _init_db(db)
    with DatabaseConnector(str(db)) as connector:
        systems = get_approved_adapter_source_systems(connector)
        assert "Adapter_Demo" in systems
        counts = sync_approved_import_adapters(connector, {})
        assert counts["holdings"] == 1
        row = connector.execute("SELECT source_system FROM holdings WHERE asset_id='US_AAPL'").fetchone()
        assert row[0] == "Adapter_Demo"


def test_sync_is_idempotent(tmp_path: Path):
    """Running sync twice must NOT double-insert rows (Fix #2)."""
    db = tmp_path / "test.duckdb"
    _init_db(db)
    with DatabaseConnector(str(db)) as connector:
        # First sync
        counts1 = sync_approved_import_adapters(connector, {})
        assert counts1["holdings"] == 1

        # Second sync — should be a no-op because synced_at is now set
        counts2 = sync_approved_import_adapters(connector, {})
        assert counts2["holdings"] == 0

        # Verify only 1 row in holdings, not 2
        total = connector.execute("SELECT COUNT(*) FROM holdings WHERE asset_id='US_AAPL'").fetchone()[0]
        assert total == 1


def test_synced_at_marked(tmp_path: Path):
    """After sync, staged rows should have synced_at set (Fix #2)."""
    db = tmp_path / "test.duckdb"
    _init_db(db)
    with DatabaseConnector(str(db)) as connector:
        sync_approved_import_adapters(connector, {})
        row = connector.execute(
            "SELECT synced_at FROM import_adapter_staged_rows WHERE run_id=1 AND row_index=0"
        ).fetchone()
        assert row[0] is not None
