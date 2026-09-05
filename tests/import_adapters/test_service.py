from pathlib import Path

import duckdb
import pandas as pd

from src.database.connector import DatabaseConnector
from src.import_adapters.service import ImportAdapterService


def _init_db(db_path: Path):
    conn = duckdb.connect(str(db_path))
    schema = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema)
    conn.close()


def test_import_service_lifecycle(tmp_path: Path):
    db = tmp_path / "test.duckdb"
    _init_db(db)
    csv = tmp_path / "h.csv"
    pd.DataFrame([
        {"asset_id": "US_AAPL", "snapshot_date": "2026-05-09", "quantity": 1, "market_value": 1000, "currency": "CNY"}
    ]).to_csv(csv, index=False)

    with DatabaseConnector(str(db)) as connector:
        service = ImportAdapterService(connector)
        run = service.create_import_run("demo", "holdings", "h.csv", str(csv))
        service.configure_import_run(run["run_id"], run["inferred_mapping"])
        validation = service.validate_import_run(run["run_id"])
        assert validation["valid"]
        staged = service.stage_import_run(run["run_id"])
        assert staged == 1
        before_holdings = connector.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
        before_tx = connector.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert before_holdings == 0
        assert before_tx == 0
        service.approve_adapter("demo", "Adapter_Demo", ["US_"], 5)
        adapters = service.list_adapters()
        assert adapters[0]["source_system"] == "Adapter_Demo"


def test_staging_all_rows_not_truncated(tmp_path: Path):
    """Verify that staging inserts ALL rows from a file, not just the 5-row preview (Fix #1)."""
    db = tmp_path / "test.duckdb"
    _init_db(db)
    csv = tmp_path / "multi.csv"
    rows = [
        {"asset_id": f"US_STOCK_{i}", "snapshot_date": "2026-05-09",
         "quantity": i * 10, "market_value": i * 1000, "currency": "CNY"}
        for i in range(1, 9)  # 8 rows, well above the 5-row preview default
    ]
    pd.DataFrame(rows).to_csv(csv, index=False)

    with DatabaseConnector(str(db)) as connector:
        service = ImportAdapterService(connector)
        run = service.create_import_run("multi", "holdings", "multi.csv", str(csv))
        assert run["total_rows"] == 8
        # Preview should only have 5 rows
        assert len(run["preview_rows"]) == 5
        service.configure_import_run(run["run_id"], run["inferred_mapping"])
        validation = service.validate_import_run(run["run_id"])
        assert validation["valid"]
        staged = service.stage_import_run(run["run_id"])
        # Must stage all 8 rows, not just 5
        assert staged == 8


def test_staging_normalizes_amounts_and_dates(tmp_path: Path):
    """Verify that clean_amount and parse_date are applied during staging (Fix #4)."""
    db = tmp_path / "test.duckdb"
    _init_db(db)
    csv = tmp_path / "formatted.csv"
    pd.DataFrame([
        {"asset_id": "US_AAPL", "snapshot_date": "05/09/2026",
         "quantity": "100", "market_value": "$7,000.50", "currency": "CNY"}
    ]).to_csv(csv, index=False)

    with DatabaseConnector(str(db)) as connector:
        service = ImportAdapterService(connector)
        run = service.create_import_run("fmt", "holdings", "fmt.csv", str(csv))
        service.configure_import_run(run["run_id"], run["inferred_mapping"])
        service.validate_import_run(run["run_id"])
        service.stage_import_run(run["run_id"])

        import json
        row = connector.execute(
            "SELECT normalized_payload_json FROM import_adapter_staged_rows WHERE run_id=?",
            (run["run_id"],)
        ).fetchone()
        payload = json.loads(row[0])
        assert payload["market_value"] == 7000.5
        assert payload["quantity"] == 100.0
        assert payload["snapshot_date"] == "2026-05-09"


def test_staging_warns_on_missing_market_value(tmp_path: Path):
    """Holdings with no market_value should get a validation warning (Fix #4)."""
    db = tmp_path / "test.duckdb"
    _init_db(db)
    csv = tmp_path / "no_mv.csv"
    pd.DataFrame([
        {"asset_id": "US_AAPL", "snapshot_date": "2026-05-09", "quantity": 10, "currency": "CNY"}
    ]).to_csv(csv, index=False)

    with DatabaseConnector(str(db)) as connector:
        service = ImportAdapterService(connector)
        run = service.create_import_run("no_mv", "holdings", "no_mv.csv", str(csv))
        # Manually set mapping to exclude market_value
        service.configure_import_run(run["run_id"], {
            "asset_id": "asset_id",
            "snapshot_date": "snapshot_date",
            "quantity": "quantity",
        })
        service.validate_import_run(run["run_id"])
        service.stage_import_run(run["run_id"])

        import json
        row = connector.execute(
            "SELECT validation_status, validation_messages_json FROM import_adapter_staged_rows WHERE run_id=?",
            (run["run_id"],)
        ).fetchone()
        assert row[0] == "warning"
        messages = json.loads(row[1])
        assert any("market_value" in m for m in messages)
