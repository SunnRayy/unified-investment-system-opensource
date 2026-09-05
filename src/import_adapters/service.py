from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from src.database.connector import DatabaseConnector

from .file_reader import clean_amount, parse_date, read_tabular_file
from .mapping import infer_mapping, missing_required_fields


class ImportAdapterService:
    def __init__(self, connector: DatabaseConnector):
        self.connector = connector

    def create_import_run(self, adapter_key: str, import_type: str, filename: str, file_path: str, header_row: int = 0) -> dict[str, Any]:
        result = read_tabular_file(Path(file_path), nrows=5, header_row=header_row)
        mapping = infer_mapping(result.headers, import_type)
        run_id = self.connector.execute(
            """
            INSERT INTO import_adapter_runs(adapter_key, import_type, filename, file_path, status, detected_headers, column_mapping, row_counts_json)
            VALUES (?, ?, ?, ?, 'uploaded', ?, ?, ?)
            RETURNING id
            """,
            (
                adapter_key,
                import_type,
                filename,
                file_path,
                json.dumps(result.headers),
                json.dumps(mapping),
                json.dumps({"total_rows": result.total_rows, "header_row": header_row}),
            ),
        ).fetchone()[0]
        return {
            "run_id": run_id,
            "headers": result.headers,
            "inferred_mapping": mapping,
            "preview_rows": result.preview_rows,
            "total_rows": result.total_rows,
        }

    def configure_import_run(self, run_id: int, column_mapping: dict[str, str], fx_rate: float | None = None) -> None:
        self.connector.execute(
            "UPDATE import_adapter_runs SET column_mapping=?, status='configured' WHERE id=?",
            (json.dumps({"column_mapping": column_mapping, "fx_rate": fx_rate}), run_id),
        )

    def _get_header_row(self, run_id: int) -> int:
        row = self.connector.execute(
            "SELECT row_counts_json FROM import_adapter_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row or not row[0]:
            return 0
        counts = json.loads(row[0])
        return int(counts.get("header_row", 0))

    def validate_import_run(self, run_id: int) -> dict[str, Any]:
        row = self.connector.execute(
            "SELECT import_type, file_path, column_mapping FROM import_adapter_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if not row:
            raise ValueError("Run not found")
        import_type, file_path, raw_mapping = row
        parsed = json.loads(raw_mapping) if raw_mapping else {}
        column_mapping = parsed.get("column_mapping", parsed)
        fx_rate = parsed.get("fx_rate")
        missing = missing_required_fields(column_mapping, import_type)
        if import_type == "holdings" and "currency" in column_mapping and fx_rate is None:
            warnings = ["fx_rate_missing_for_non_cny"]
        else:
            warnings = []
        errors = [f"missing:{f}" for f in missing]
        header_row = self._get_header_row(run_id)
        file_data = read_tabular_file(Path(file_path), nrows=None, header_row=header_row)
        validation = {"valid": len(errors) == 0, "warnings": warnings, "errors": errors, "row_counts": {"total": file_data.total_rows}}
        existing_counts = json.loads(self.connector.execute(
            "SELECT row_counts_json FROM import_adapter_runs WHERE id=?", (run_id,)
        ).fetchone()[0] or "{}")
        existing_counts.update({"total": file_data.total_rows})
        self.connector.execute(
            "UPDATE import_adapter_runs SET status='validated', warnings_json=?, errors_json=?, row_counts_json=? WHERE id=?",
            (json.dumps(warnings), json.dumps(errors), json.dumps(existing_counts), run_id),
        )
        return validation

    def stage_import_run(self, run_id: int) -> int:
        row = self.connector.execute(
            "SELECT import_type, file_path, column_mapping FROM import_adapter_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if not row:
            raise ValueError("Run not found")
        import_type, file_path, raw_mapping = row
        parsed = json.loads(raw_mapping) if raw_mapping else {}
        column_mapping = parsed.get("column_mapping", parsed)
        header_row = self._get_header_row(run_id)
        file_data = read_tabular_file(Path(file_path), nrows=None, header_row=header_row)
        all_rows = file_data.full_rows or file_data.preview_rows
        staged = 0
        for idx, raw in enumerate(all_rows):
            payload: dict = {}
            messages: list[str] = []
            for dst, src in column_mapping.items():
                val = raw.get(src)
                # Apply value normalization
                if dst in ("market_value", "quantity", "market_price_unit", "price_unit",
                           "amount_gross", "commission_fee", "cost_price_unit"):
                    cleaned = clean_amount(val)
                    if val not in (None, "") and cleaned is None:
                        messages.append(f"row {idx}: could not parse amount for '{dst}': {val!r}")
                    payload[dst] = cleaned
                elif dst in ("snapshot_date", "transaction_date"):
                    parsed_date = parse_date(val)
                    if val not in (None, "") and parsed_date is None:
                        messages.append(f"row {idx}: could not parse date for '{dst}': {val!r}")
                    payload[dst] = str(parsed_date) if parsed_date else val
                else:
                    payload[dst] = val

            # Inject today's date for holdings when snapshot_date is not mapped
            if import_type == "holdings" and "snapshot_date" not in column_mapping:
                payload["snapshot_date"] = str(date.today())

            # Validate required fields for holdings
            validation_status = "valid"
            if import_type == "holdings":
                if payload.get("market_value") is None and payload.get("quantity") is not None:
                    messages.append(f"row {idx}: market_value is missing or null")
                    validation_status = "warning"

            self.connector.execute(
                """
                INSERT INTO import_adapter_staged_rows(run_id,row_index,row_kind,normalized_payload_json,validation_status,validation_messages_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, idx, "holding" if import_type == "holdings" else "transaction",
                 json.dumps(payload), validation_status, json.dumps(messages)),
            )
            staged += 1
        self.connector.execute("UPDATE import_adapter_runs SET status='staged' WHERE id=?", (run_id,))
        return staged

    def approve_adapter(self, adapter_key: str, source_system: str, asset_prefixes: list[str], authority_priority: int, approved_by: str | None = None) -> None:
        if not source_system:
            raise ValueError("source_system is required")
        if not asset_prefixes:
            raise ValueError("asset_prefixes is required")
        self.connector.execute(
            """
            INSERT INTO import_adapter_approvals(adapter_key, approved_by, source_system, asset_prefixes_json, authority_priority, enabled)
            VALUES (?, ?, ?, ?, ?, TRUE)
            ON CONFLICT(adapter_key) DO UPDATE SET
              approved_by=excluded.approved_by,
              source_system=excluded.source_system,
              asset_prefixes_json=excluded.asset_prefixes_json,
              authority_priority=excluded.authority_priority,
              enabled=TRUE,
              approved_at=now()
            """,
            (adapter_key, approved_by, source_system, json.dumps(asset_prefixes), int(authority_priority)),
        )

    def get_staged_rows(self, run_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connector.execute(
            """
            SELECT row_index, row_kind, normalized_payload_json, validation_status, validation_messages_json
            FROM import_adapter_staged_rows
            WHERE run_id=?
            ORDER BY row_index
            LIMIT ?
            """,
            (run_id, limit),
        ).fetchall()
        return [
            {
                "row_index": r[0],
                "row_kind": r[1],
                "payload": json.loads(r[2]) if r[2] else {},
                "validation_status": r[3],
                "messages": json.loads(r[4]) if r[4] else [],
            }
            for r in rows
        ]

    def list_adapters(self) -> list[dict[str, Any]]:
        rows = self.connector.execute(
            "SELECT adapter_key, source_system, authority_priority, enabled FROM import_adapter_approvals ORDER BY adapter_key"
        ).fetchall()
        return [
            {
                "adapter_key": r[0],
                "source_system": r[1],
                "authority_priority": r[2],
                "enabled": bool(r[3]),
            }
            for r in rows
        ]
