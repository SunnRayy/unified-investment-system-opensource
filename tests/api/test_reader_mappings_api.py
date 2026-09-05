"""Tests for src/api/routes/reader_mappings.py (ADR-023 / Reader Mapping
Management, WS-A Step A3).

Uses tmp_path DuckDB files via bootstrap_database (schema + ALL migrations,
including V75 reader_mappings/reader_mapping_audit + idempotent FS seed) —
mirrors tests/services/test_reader_mappings.py's _make_db pattern. Never
touches data/unified.duckdb.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_db
from src.api.main import app
from src.database.connector import DatabaseConnector
from src.database.mapping_seeds import FS_ASSET_MAPPING_SEED
from src.database.schema import bootstrap_database
from src.services.reader_mappings import _get_defaults, load_reader_mappings

pytestmark = pytest.mark.pipeline

_SEED_COUNT = len(FS_ASSET_MAPPING_SEED)


def _expected_fresh_db_fs_column() -> dict:
    """What load_reader_mappings(financial_summary, fs_column) returns on a
    fully-migrated DB (Program OSR WS-3b). Same reasoning as the identically
    named helper in tests/services/test_reader_mappings.py: under the test
    session's UIS_SEED_PROFILE=example, the baseline's persona-renamed
    property key and the DB's real V75-seeded key both survive the merge —
    one extra entry vs. FS_ASSET_MAPPING_SEED alone."""
    expected = dict(_get_defaults()[("financial_summary", "fs_column")])
    expected.update(FS_ASSET_MAPPING_SEED)
    return expected


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "reader_mappings_api_test.duckdb"
    connector = DatabaseConnector(str(db_path))
    bootstrap_database(connector)
    yield connector
    connector.close()


@pytest.fixture
def client(db):
    def override_get_db():
        return db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_fs_fixture(tmp_path: Path) -> Path:
    """Minimal Financial Summary xlsx: header at row index 3 (header=3), 2 data
    rows (kept <=3 so melt_financial_summary_holdings's internal `iloc[3:]`
    trim is a no-op — see src/sources/reader_hooks.py). Columns:
      日期                  — date column, excluded from unmapped scan
      RMB现金现金            — an existing seeded fs_column mapping (CASH_Cash_CNY)
      RMB现金现金_USD        — native-currency sibling -> ignored_native=True
      新增测试列              — genuinely unmapped column
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "资产负债"
    ws.append(["", "", "", ""])
    ws.append(["", "Group Label", "", ""])
    ws.append(["", "", "", ""])
    ws.append(["日期", "RMB现金现金", "RMB现金现金_USD", "新增测试列"])
    ws.append([datetime(2025, 1, 1), 1000.0, 142.0, 999.0])
    ws.append([datetime(2025, 2, 1), 2000.0, 284.0, 888.0])
    path = tmp_path / "Financial Summary_new.xlsx"
    wb.save(path)
    return path


def _mock_settings(tmp_path: Path) -> dict:
    return {
        "source_registry": {
            "financial_summary": {
                "enabled": True,
                "data_dir": str(tmp_path),
                "file_patterns": {"workbook": "Financial Summary_new.xlsx"},
            }
        },
        "finance_dir": str(tmp_path),
    }


def _patched_settings(tmp_path: Path):
    return patch(
        "src.api.routes.reader_mappings.settings_manager.load_settings",
        return_value=_mock_settings(tmp_path),
    )


def _insert_holding(conn, asset_id: str, source_system: str = "Financial_Summary_Excel"):
    conn.execute(
        "INSERT INTO holdings (snapshot_date, asset_id, quantity, market_value, currency, source_system) "
        "VALUES (?, ?, ?, ?, 'CNY', ?)",
        ["2026-01-01", asset_id, 1.0, 100.0, source_system],
    )


def _insert_transaction(conn, asset_id: str, source_system: str = "Financial_Summary_Excel"):
    conn.execute(
        "INSERT INTO transactions (transaction_date, asset_id, transaction_type, amount_net, currency, source_system) "
        "VALUES (?, ?, 'buy', ?, 'CNY', ?)",
        ["2026-01-01", asset_id, 100.0, source_system],
    )


def _get_mapping_id(client, map_key: str, reader: str = "financial_summary") -> int:
    resp = client.get(f"/settings/sources/{reader}/mappings", params={"kind": "fs_column"})
    assert resp.status_code == 200
    for m in resp.json()["mappings"]:
        if m["map_key"] == map_key:
            return m["id"]
    raise AssertionError(f"map_key {map_key!r} not found in mappings list")


# ---------------------------------------------------------------------------
# Unknown reader -> 404
# ---------------------------------------------------------------------------


class TestUnknownReader404:
    # Every reader in the plan's scope is now mapping-managed (WS-A fs_column,
    # WS-B id_field_map, WS-C schwab/cn_fund vocabularies) — use ibkr
    # (deliberately excluded: co-authority, shares schwab's vocabularies) and
    # a nonsense reader to exercise the 404 path.
    def test_list_unknown_reader_404(self, client):
        resp = client.get("/settings/sources/ibkr/mappings", params={"kind": "symbol_norm"})
        assert resp.status_code == 404

    def test_create_unknown_reader_404(self, client):
        resp = client.post(
            "/settings/sources/ibkr/mappings",
            json={"kind": "symbol_norm", "map_key": "x", "value": {"to": "X"}},
        )
        assert resp.status_code == 404

    def test_preview_unknown_reader_404(self, client):
        resp = client.post("/settings/sources/not_a_reader/mappings/preview")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET list
# ---------------------------------------------------------------------------


class TestListMappings:
    def test_list_seeded_mappings(self, client):
        resp = client.get("/settings/sources/financial_summary/mappings", params={"kind": "fs_column"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reader"] == "financial_summary"
        assert body["mapping_kind"] == "fs_column"
        assert len(body["mappings"]) == _SEED_COUNT
        assert body["defaults_only"] is False
        keys = {m["map_key"] for m in body["mappings"]}
        assert "RMB现金现金" in keys

    def test_list_kind_mismatch_422(self, client):
        resp = client.get("/settings/sources/financial_summary/mappings", params={"kind": "id_field_map"})
        assert resp.status_code == 422

    def test_list_kind_defaults_when_omitted(self, client):
        resp = client.get("/settings/sources/financial_summary/mappings")
        assert resp.status_code == 200
        assert resp.json()["mapping_kind"] == "fs_column"


# ---------------------------------------------------------------------------
# POST create
# ---------------------------------------------------------------------------


class TestCreateMapping:
    def test_create_success(self, client):
        resp = client.post(
            "/settings/sources/financial_summary/mappings",
            json={
                "kind": "fs_column",
                "map_key": "新增测试列",
                "value": {"asset_id": "CASH_Test_New", "asset_name": "测试新资产", "currency": "CNY"},
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["map_key"] == "新增测试列"
        assert body["map_value"]["asset_id"] == "CASH_Test_New"
        assert body["status"] == "active"

    def test_create_duplicate_map_key_422(self, client):
        resp = client.post(
            "/settings/sources/financial_summary/mappings",
            json={
                "kind": "fs_column",
                "map_key": "RMB现金现金",  # already an active seeded mapping
                "value": {"asset_id": "CASH_Dup", "asset_name": "dup", "currency": "CNY"},
            },
        )
        assert resp.status_code == 422

    def test_create_non_cny_currency_422(self, client):
        resp = client.post(
            "/settings/sources/financial_summary/mappings",
            json={
                "kind": "fs_column",
                "map_key": "新美元列",
                "value": {"asset_id": "CASH_USD_New", "asset_name": "usd asset", "currency": "USD"},
            },
        )
        assert resp.status_code == 422
        assert "CNY" in resp.json()["detail"]

    def test_create_empty_map_key_422(self, client):
        resp = client.post(
            "/settings/sources/financial_summary/mappings",
            json={"kind": "fs_column", "map_key": "  ", "value": {"asset_id": "X", "asset_name": "x", "currency": "CNY"}},
        )
        assert resp.status_code == 422

    def test_create_duplicate_asset_id_422(self, client):
        resp = client.post(
            "/settings/sources/financial_summary/mappings",
            json={
                "kind": "fs_column",
                "map_key": "新列B",
                # CASH_Cash_CNY already belongs to the seeded "RMB现金现金" mapping
                "value": {"asset_id": "CASH_Cash_CNY", "asset_name": "dup asset", "currency": "CNY"},
            },
        )
        assert resp.status_code == 422

    def test_create_asset_id_collision_other_source_409(self, client, db):
        _insert_holding(db, "US_STK_AAPL", source_system="IBKR_Flex_CSV")
        resp = client.post(
            "/settings/sources/financial_summary/mappings",
            json={
                "kind": "fs_column",
                "map_key": "新列C",
                "value": {"asset_id": "US_STK_AAPL", "asset_name": "collide", "currency": "CNY"},
            },
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


class TestPatchMapping:
    def test_patch_asset_name_and_sort_order(self, client):
        mapping_id = _get_mapping_id(client, "RMB存款_招行")
        resp = client.patch(
            f"/settings/sources/financial_summary/mappings/{mapping_id}",
            json={"value": {"asset_name": "招行存款 (renamed)"}, "sort_order": 99},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["map_value"]["asset_name"] == "招行存款 (renamed)"
        assert body["sort_order"] == 99
        # asset_id unchanged
        assert body["map_value"]["asset_id"] == "CASH_Deposit_CMB_CNY"

    def test_patch_asset_id_blocked_when_holdings_exist_409(self, client, db):
        mapping_id = _get_mapping_id(client, "RMB现金现金")
        _insert_holding(db, "CASH_Cash_CNY")
        resp = client.patch(
            f"/settings/sources/financial_summary/mappings/{mapping_id}",
            json={"value": {"asset_id": "CASH_Cash_CNY_Renamed"}},
        )
        assert resp.status_code == 409

    def test_patch_asset_id_allowed_when_no_holdings(self, client):
        mapping_id = _get_mapping_id(client, "投资资产_存款基金_个人养老金")
        resp = client.patch(
            f"/settings/sources/financial_summary/mappings/{mapping_id}",
            json={"value": {"asset_id": "Pension_Personal_Renamed"}},
        )
        assert resp.status_code == 200
        assert resp.json()["map_value"]["asset_id"] == "Pension_Personal_Renamed"

    def test_patch_unknown_id_404(self, client):
        resp = client.patch(
            "/settings/sources/financial_summary/mappings/999999",
            json={"sort_order": 1},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Archive / Restore
# ---------------------------------------------------------------------------


class TestArchiveRestore:
    def test_archive_without_holdings_no_deactivate_hint(self, client):
        mapping_id = _get_mapping_id(client, "固定资产_房产_阳光花园")
        resp = client.post(f"/settings/sources/financial_summary/mappings/{mapping_id}/archive")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mapping"]["status"] == "archived"
        assert body["asset_has_holdings"] is False
        assert body["deactivate_hint"] is None

    def test_archive_with_holdings_returns_deactivate_hint(self, client, db):
        mapping_id = _get_mapping_id(client, "投资资产_银行理财_招行")
        _insert_holding(db, "Wealth_CMB")
        resp = client.post(f"/settings/sources/financial_summary/mappings/{mapping_id}/archive")
        assert resp.status_code == 200
        body = resp.json()
        assert body["asset_has_holdings"] is True
        assert body["deactivate_hint"]["asset_id"] == "Wealth_CMB"
        assert "taxonomy/assets/Wealth_CMB" in body["deactivate_hint"]["endpoint"]

    def test_archive_removes_from_active_list_scan(self, client):
        mapping_id = _get_mapping_id(client, "RMB存款_北京银行")
        client.post(f"/settings/sources/financial_summary/mappings/{mapping_id}/archive")
        resp = client.get("/settings/sources/financial_summary/mappings", params={"kind": "fs_column"})
        row = next(m for m in resp.json()["mappings"] if m["id"] == mapping_id)
        assert row["status"] == "archived"

    def test_archive_then_restore_round_trip(self, client, db):
        mapping_id = _get_mapping_id(client, "RMB存款_工行")
        client.post(f"/settings/sources/financial_summary/mappings/{mapping_id}/archive")
        resp = client.post(f"/settings/sources/financial_summary/mappings/{mapping_id}/restore")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

        audit_rows = db.execute(
            "SELECT action FROM reader_mapping_audit WHERE mapping_id = ? ORDER BY id", [mapping_id]
        ).fetchall()
        actions = [r[0] for r in audit_rows]
        assert "archive" in actions
        assert "restore" in actions

    def test_recreate_after_archive_reactivates_same_row(self, client, db):
        """The table's UNIQUE(reader_key, mapping_kind, map_key) applies
        regardless of status, so POST create for an archived map_key's key
        cannot be a second INSERT (see docs/api-specs/reader-mappings.md
        'Reactivation note') — it reactivates the existing row in place."""
        mapping_id = _get_mapping_id(client, "美元存款_Chase")
        client.post(f"/settings/sources/financial_summary/mappings/{mapping_id}/archive")

        resp = client.post(
            "/settings/sources/financial_summary/mappings",
            json={
                "kind": "fs_column",
                "map_key": "美元存款_Chase",
                "value": {"asset_id": "CASH_Deposit_Chase_USD_v2", "asset_name": "Chase v2", "currency": "CNY"},
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] == mapping_id  # same row, reactivated — not a new id
        assert body["status"] == "active"
        assert body["map_value"]["asset_id"] == "CASH_Deposit_Chase_USD_v2"

        rows = db.execute(
            "SELECT COUNT(*) FROM reader_mappings WHERE reader_key='financial_summary' "
            "AND mapping_kind='fs_column' AND map_key='美元存款_Chase'"
        ).fetchone()
        assert rows[0] == 1  # UNIQUE constraint: still exactly one physical row

    def test_archive_unknown_id_404(self, client):
        resp = client.post("/settings/sources/financial_summary/mappings/999999/archive")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


class TestDeleteMapping:
    def test_delete_without_history_succeeds(self, client, db):
        create_resp = client.post(
            "/settings/sources/financial_summary/mappings",
            json={
                "kind": "fs_column",
                "map_key": "待删除列",
                "value": {"asset_id": "CASH_ToDelete", "asset_name": "to delete", "currency": "CNY"},
            },
        )
        mapping_id = create_resp.json()["id"]
        resp = client.delete(f"/settings/sources/financial_summary/mappings/{mapping_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == mapping_id

        audit_rows = db.execute(
            "SELECT action FROM reader_mapping_audit WHERE mapping_id = ?", [mapping_id]
        ).fetchall()
        assert any(r[0] == "delete" for r in audit_rows)

    def test_delete_with_holdings_history_409(self, client, db):
        mapping_id = _get_mapping_id(client, "投资资产_存款基金_个人养老金")
        _insert_holding(db, "Pension_Personal")
        resp = client.delete(f"/settings/sources/financial_summary/mappings/{mapping_id}")
        assert resp.status_code == 409

    def test_delete_with_transaction_history_409(self, client, db):
        create_resp = client.post(
            "/settings/sources/financial_summary/mappings",
            json={
                "kind": "fs_column",
                "map_key": "有交易记录列",
                "value": {"asset_id": "CASH_HasTx", "asset_name": "has tx", "currency": "CNY"},
            },
        )
        mapping_id = create_resp.json()["id"]
        _insert_transaction(db, "CASH_HasTx")
        resp = client.delete(f"/settings/sources/financial_summary/mappings/{mapping_id}")
        assert resp.status_code == 409

    def test_delete_unknown_id_404(self, client):
        resp = client.delete("/settings/sources/financial_summary/mappings/999999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


class TestPreviewMappings:
    def test_preview_no_file_returns_empty(self, client, tmp_path):
        with _patched_settings(tmp_path):  # no fixture file written -> file not found
            resp = client.post("/settings/sources/financial_summary/mappings/preview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_path"] is None
        assert body["results"] == []
        assert body["unmapped_columns"] == []

    def test_preview_with_fixture(self, client, tmp_path):
        _make_fs_fixture(tmp_path)
        with _patched_settings(tmp_path):
            resp = client.post("/settings/sources/financial_summary/mappings/preview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_path"] is not None

        result = next(r for r in body["results"] if r["map_key"] == "RMB现金现金")
        assert result["column_found"] is True
        assert result["nonzero_rows"] == 2
        assert result["latest_value"] == 2000.0
        assert result["latest_date"] == "2025-02-01"

        # A seeded map_key that has no matching column in this minimal fixture.
        other = next(r for r in body["results"] if r["map_key"] == "RMB存款_中行")
        assert other["column_found"] is False
        assert other["nonzero_rows"] == 0

        unmapped_by_col = {c["column"]: c["ignored_native"] for c in body["unmapped_columns"]}
        assert unmapped_by_col.get("新增测试列") is False
        assert unmapped_by_col.get("RMB现金现金_USD") is True
        assert "日期" not in unmapped_by_col
        assert "RMB现金现金" not in unmapped_by_col  # already mapped

    def test_preview_with_proposed_overlay(self, client, tmp_path):
        _make_fs_fixture(tmp_path)
        with _patched_settings(tmp_path):
            resp = client.post(
                "/settings/sources/financial_summary/mappings/preview",
                json={
                    "proposed": [
                        {
                            "map_key": "新增测试列",
                            "value": {"asset_id": "CASH_Proposed", "asset_name": "proposed", "currency": "CNY"},
                        }
                    ]
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        # Once proposed, "新增测试列" should resolve as a found/matched column...
        result = next(r for r in body["results"] if r["map_key"] == "新增测试列")
        assert result["column_found"] is True
        # ...and drop out of unmapped_columns.
        unmapped_cols = {c["column"] for c in body["unmapped_columns"]}
        assert "新增测试列" not in unmapped_cols


# ---------------------------------------------------------------------------
# /settings/sources unmapped_count extension
# ---------------------------------------------------------------------------


class TestSourcesUnmappedCount:
    def test_unmapped_count_present_for_financial_summary(self, client, tmp_path):
        _make_fs_fixture(tmp_path)
        with patch(
            "src.api.routes.settings.settings_manager.load_settings",
            return_value=_mock_settings(tmp_path),
        ):
            resp = client.get("/settings/sources")
        assert resp.status_code == 200
        sources = {s["key"]: s for s in resp.json()["sources"]}
        assert sources["financial_summary"]["unmapped_count"] == 1  # only "新增测试列"

    def test_unmapped_count_null_for_other_readers(self, client, tmp_path):
        with patch(
            "src.api.routes.settings.settings_manager.load_settings",
            return_value=_mock_settings(tmp_path),
        ):
            resp = client.get("/settings/sources")
        assert resp.status_code == 200
        sources = {s["key"]: s for s in resp.json()["sources"]}
        for key, cfg in sources.items():
            if key != "financial_summary":
                assert cfg["unmapped_count"] is None

    def test_unmapped_count_null_when_file_missing(self, client, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with patch(
            "src.api.routes.settings.settings_manager.load_settings",
            return_value=_mock_settings(empty_dir),
        ):
            resp = client.get("/settings/sources")
        assert resp.status_code == 200
        sources = {s["key"]: s for s in resp.json()["sources"]}
        assert sources["financial_summary"]["unmapped_count"] is None


# ---------------------------------------------------------------------------
# Ignore-column / Unignore (ADR-023 A4.1)
# ---------------------------------------------------------------------------


class TestIgnoreUnignoreColumn:
    def test_ignore_column_flow(self, client, db, tmp_path):
        """Full round trip: candidate -> ignore -> excluded from actionable
        count + regular mappings list -> unignore -> candidate again. Also
        asserts the sync-path loader (load_reader_mappings) is unaffected —
        '新增测试列' was never a real asset mapping, so ignoring it must not
        change the merged dict the melt hook sees."""
        _make_fs_fixture(tmp_path)
        with _patched_settings(tmp_path):
            resp = client.get("/settings/sources/financial_summary/mappings", params={"kind": "fs_column"})
            unmapped = {c["column"]: c for c in resp.json()["unmapped_columns"]}
            assert unmapped["新增测试列"]["category"] == "candidate"
            assert unmapped["新增测试列"]["mapping_id"] is None

            ignore_resp = client.post(
                "/settings/sources/financial_summary/mappings/ignore-column",
                json={"map_key": "新增测试列"},
            )
            assert ignore_resp.status_code == 201
            ignore_body = ignore_resp.json()
            assert ignore_body["category"] == "ignored"
            mapping_id = ignore_body["mapping_id"]
            assert mapping_id is not None

            resp2 = client.get("/settings/sources/financial_summary/mappings", params={"kind": "fs_column"})
            body2 = resp2.json()
            unmapped2 = {c["column"]: c for c in body2["unmapped_columns"]}
            assert unmapped2["新增测试列"]["category"] == "ignored"
            assert unmapped2["新增测试列"]["mapping_id"] == mapping_id
            # Ignored rows are markers, not asset mappings — excluded from the list.
            assert "新增测试列" not in {m["map_key"] for m in body2["mappings"]}

        # Loader unaffected: this key was never in FS_ASSET_MAPPING_SEED, so the
        # merged dict the sync path/melt hook sees is unchanged by the ignore.
        merged = load_reader_mappings(db, "financial_summary", "fs_column")
        assert "新增测试列" not in merged
        assert len(merged) == len(_expected_fresh_db_fs_column())

        with _patched_settings(tmp_path):
            unignore_resp = client.post(f"/settings/sources/financial_summary/mappings/{mapping_id}/unignore")
            assert unignore_resp.status_code == 200
            assert unignore_resp.json() == {"unignored": mapping_id, "map_key": "新增测试列"}

            resp3 = client.get("/settings/sources/financial_summary/mappings", params={"kind": "fs_column"})
            unmapped3 = {c["column"]: c for c in resp3.json()["unmapped_columns"]}
            assert unmapped3["新增测试列"]["category"] == "candidate"

    def test_ignore_column_empty_map_key_422(self, client):
        resp = client.post(
            "/settings/sources/financial_summary/mappings/ignore-column", json={"map_key": "  "}
        )
        assert resp.status_code == 422

    def test_ignore_column_already_mapped_422(self, client):
        resp = client.post(
            "/settings/sources/financial_summary/mappings/ignore-column",
            json={"map_key": "RMB现金现金"},  # already an active seeded mapping
        )
        assert resp.status_code == 422

    def test_ignore_column_unknown_reader_404(self, client):
        resp = client.post(
            "/settings/sources/schwab/mappings/ignore-column", json={"map_key": "x"}
        )
        assert resp.status_code == 404

    def test_unignore_unknown_id_404(self, client):
        resp = client.post("/settings/sources/financial_summary/mappings/999999/unignore")
        assert resp.status_code == 404

    def test_unignore_non_ignored_row_422(self, client):
        mapping_id = _get_mapping_id(client, "RMB现金现金")
        resp = client.post(f"/settings/sources/financial_summary/mappings/{mapping_id}/unignore")
        assert resp.status_code == 422

    def test_generic_endpoints_reject_ignored_row_409(self, client):
        """An ignored row's id must not flow through the generic patch/archive/
        restore/delete endpoints — map_value='{}' has no asset_id/asset_name/
        currency, so _reject_if_ignored guards with a clear 409 instead."""
        ignore_resp = client.post(
            "/settings/sources/financial_summary/mappings/ignore-column",
            json={"map_key": "另一个待忽略列"},
        )
        mapping_id = ignore_resp.json()["mapping_id"]

        assert client.patch(
            f"/settings/sources/financial_summary/mappings/{mapping_id}", json={"sort_order": 1}
        ).status_code == 409
        assert client.post(
            f"/settings/sources/financial_summary/mappings/{mapping_id}/archive"
        ).status_code == 409
        assert client.post(
            f"/settings/sources/financial_summary/mappings/{mapping_id}/restore"
        ).status_code == 409
        assert client.delete(
            f"/settings/sources/financial_summary/mappings/{mapping_id}"
        ).status_code == 409
