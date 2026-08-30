"""Tests for src/api/routes/reader_mappings.py — ADR-023 Reader Mapping
Management, Workstream B (Gold/Insurance/RSU id_field_map).

Mirrors tests/api/test_reader_mappings_api.py's fixtures/structure: tmp_path
DuckDB via bootstrap_database (schema + ALL migrations, including V77
id_field_map seed). Never touches data/unified.duckdb.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import openpyxl
import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_db
from src.api.main import app
from src.database.connector import DatabaseConnector
from src.database.mapping_seeds import ID_FIELD_MAP_SEEDS
from src.database.schema import bootstrap_database

pytestmark = pytest.mark.pipeline

_GOLD_SEED_COUNT = len(ID_FIELD_MAP_SEEDS["gold"])
_RSU_SEED_COUNT = len(ID_FIELD_MAP_SEEDS["rsu"])
_INSURANCE_SEED_COUNT = len(ID_FIELD_MAP_SEEDS["insurance"])  # 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "id_field_map_api_test.duckdb"
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


def _make_gold_fixture(tmp_path: Path) -> Path:
    """Minimal Gold Excel matching config/readers/gold.yaml's sheet shape.

    黄金持仓 columns: 标的名称, 持有数量, 单位, 平均成本价, 单价, 当前市值, 未实现盈亏, 交易账户.
    Includes one unmapped account label ("澳门银行") to exercise the
    candidate/unmapped scan.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "黄金持仓"
    ws.append(["标的名称", "持有数量", "单位", "平均成本价", "单价", "当前市值", "未实现盈亏", "交易账户"])
    ws.append(["纸黄金", 100.0, "g", 400.0, 450.0, 45000.0, 5000.0, "招行"])
    ws.append(["纸黄金", 50.0, "g", 400.0, 450.0, 22500.0, 2500.0, "澳门银行"])

    ws2 = wb.create_sheet("黄金交易记录")
    ws2.append(["交易日期", "标的名称", "交易类型", "金额", "数量", "价格", "手续费", "交易账户"])
    ws2.append(["2026-01-01", "纸黄金", "买入", 45000.0, 100.0, 450.0, 0.0, "招行"])

    path = tmp_path / "Gold_test.xlsx"
    wb.save(path)
    return path


def _mock_settings(tmp_path: Path, reader: str, filename: str) -> dict:
    return {
        "source_registry": {
            reader: {
                "enabled": True,
                "data_dir": str(tmp_path),
                "file_patterns": {"workbook": filename},
            }
        },
        "finance_dir": str(tmp_path),
    }


def _patched_settings(tmp_path: Path, reader: str, filename: str):
    return patch(
        "src.api.routes.reader_mappings.settings_manager.load_settings",
        return_value=_mock_settings(tmp_path, reader, filename),
    )


def _insert_holding(conn, asset_id: str, source_system: str = "Gold_Excel"):
    conn.execute(
        "INSERT INTO holdings (snapshot_date, asset_id, quantity, market_value, currency, source_system) "
        "VALUES (?, ?, ?, ?, 'CNY', ?)",
        ["2026-01-01", asset_id, 1.0, 100.0, source_system],
    )


def _insert_transaction(conn, asset_id: str, source_system: str = "Gold_Excel"):
    conn.execute(
        "INSERT INTO transactions (transaction_date, asset_id, transaction_type, amount_net, currency, source_system) "
        "VALUES (?, ?, 'buy', ?, 'CNY', ?)",
        ["2026-01-01", asset_id, 100.0, source_system],
    )


def _get_mapping(client, reader: str, map_key: str) -> dict:
    resp = client.get(f"/settings/sources/{reader}/mappings", params={"kind": "id_field_map"})
    assert resp.status_code == 200
    for m in resp.json()["mappings"]:
        if m["map_key"] == map_key:
            return m
    raise AssertionError(f"map_key {map_key!r} not found in {reader} mappings list")


# ---------------------------------------------------------------------------
# GET list
# ---------------------------------------------------------------------------


class TestListIdFieldMapMappings:
    def test_gold_list_seeded(self, client):
        resp = client.get("/settings/sources/gold/mappings", params={"kind": "id_field_map"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reader"] == "gold"
        assert body["mapping_kind"] == "id_field_map"
        assert len(body["mappings"]) == _GOLD_SEED_COUNT
        assert body["defaults_only"] is False
        keys = {m["map_key"] for m in body["mappings"]}
        assert "account:招行" in keys
        assert "asset_name:纸黄金" in keys

    def test_gold_mapping_value_shape(self, client):
        m = _get_mapping(client, "gold", "account:招行")
        assert m["map_value"] == {"code": "CMB"}
        assert m["status"] == "active"

    def test_rsu_list_seeded(self, client):
        resp = client.get("/settings/sources/rsu/mappings", params={"kind": "id_field_map"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["mappings"]) == _RSU_SEED_COUNT
        assert body["mappings"][0]["map_key"] == "asset_name:Amazon RSU"
        assert body["mappings"][0]["map_value"] == {"code": "AMZN"}

    def test_insurance_list_empty_defaults_only(self, client):
        """insurance.yaml declares no id_field_maps — V77 seeds zero rows."""
        resp = client.get("/settings/sources/insurance/mappings", params={"kind": "id_field_map"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["mappings"] == []
        assert body["defaults_only"] is True

    def test_wrong_kind_for_reader_422(self, client):
        resp = client.get("/settings/sources/gold/mappings", params={"kind": "fs_column"})
        assert resp.status_code == 422

    def test_default_kind_used_when_omitted(self, client):
        resp = client.get("/settings/sources/gold/mappings")
        assert resp.status_code == 200
        assert resp.json()["mapping_kind"] == "id_field_map"


# ---------------------------------------------------------------------------
# POST create — validation
# ---------------------------------------------------------------------------


class TestCreateIdFieldMapValidation:
    def test_kind_mismatch_422(self, client):
        resp = client.post(
            "/settings/sources/gold/mappings",
            json={"kind": "fs_column", "map_key": "account:澳门银行", "value": {"code": "MC"}},
        )
        assert resp.status_code == 422

    def test_map_key_missing_colon_422(self, client):
        resp = client.post(
            "/settings/sources/gold/mappings",
            json={"kind": "id_field_map", "map_key": "no_colon_here", "value": {"code": "X"}},
        )
        assert resp.status_code == 422
        assert "field:label" in resp.json()["detail"]

    def test_map_key_unknown_field_422(self, client):
        resp = client.post(
            "/settings/sources/gold/mappings",
            json={"kind": "id_field_map", "map_key": "not_a_real_field:澳门银行", "value": {"code": "MC"}},
        )
        assert resp.status_code == 422
        assert "not a declared id_template field" in resp.json()["detail"]

    def test_map_key_already_active_422(self, client):
        resp = client.post(
            "/settings/sources/gold/mappings",
            json={"kind": "id_field_map", "map_key": "account:招行", "value": {"code": "CMB2"}},
        )
        assert resp.status_code == 422
        assert "already has an active mapping" in resp.json()["detail"]

    def test_code_empty_422(self, client):
        resp = client.post(
            "/settings/sources/gold/mappings",
            json={"kind": "id_field_map", "map_key": "account:澳门银行", "value": {"code": "  "}},
        )
        assert resp.status_code == 422
        assert "must not be empty" in resp.json()["detail"]

    def test_code_non_ascii_422(self, client):
        resp = client.post(
            "/settings/sources/gold/mappings",
            json={"kind": "id_field_map", "map_key": "account:澳门银行", "value": {"code": "澳门"}},
        )
        assert resp.status_code == 422
        assert "ASCII-safe" in resp.json()["detail"]

    def test_code_with_whitespace_422(self, client):
        resp = client.post(
            "/settings/sources/gold/mappings",
            json={"kind": "id_field_map", "map_key": "account:澳门银行", "value": {"code": "MA CAU"}},
        )
        assert resp.status_code == 422
        assert "ASCII-safe" in resp.json()["detail"]


class TestCreateIdFieldMap:
    def test_create_gold_new_account_label(self, client):
        resp = client.post(
            "/settings/sources/gold/mappings",
            json={"kind": "id_field_map", "map_key": "account:澳门银行", "value": {"code": "MACAU"}},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["map_key"] == "account:澳门银行"
        assert body["map_value"] == {"code": "MACAU"}
        assert body["status"] == "active"

        resp2 = client.get("/settings/sources/gold/mappings", params={"kind": "id_field_map"})
        assert len(resp2.json()["mappings"]) == _GOLD_SEED_COUNT + 1

    def test_create_insurance_first_ever_mapping(self, client):
        """insurance has zero seeded rows — this is a genuinely new mapping,
        not a reactivation, and must still take effect (product_name is a
        valid id_template field for insurance even with no defaults today)."""
        resp = client.post(
            "/settings/sources/insurance/mappings",
            json={"kind": "id_field_map", "map_key": "product_name:示例定期寿险1", "value": {"code": "QSAD1"}},
        )
        assert resp.status_code == 201
        assert resp.json()["map_value"] == {"code": "QSAD1"}

    def test_create_insurance_policy_name_field_valid(self, client):
        """policy_name (the premiums-sheet melt field) is also a valid
        id_template placeholder for insurance, distinct from product_name."""
        resp = client.post(
            "/settings/sources/insurance/mappings",
            json={"kind": "id_field_map", "map_key": "policy_name:示例定期寿险1", "value": {"code": "QSAD1P"}},
        )
        assert resp.status_code == 201

    def test_reactivate_archived_row(self, client, db):
        archived = _get_mapping(client, "gold", "account:招行")
        resp = client.post(f"/settings/sources/gold/mappings/{archived['id']}/archive")
        assert resp.status_code == 200

        resp2 = client.post(
            "/settings/sources/gold/mappings",
            json={"kind": "id_field_map", "map_key": "account:招行", "value": {"code": "CMB3"}},
        )
        assert resp2.status_code == 201
        assert resp2.json()["id"] == archived["id"]
        assert resp2.json()["status"] == "active"
        assert resp2.json()["map_value"] == {"code": "CMB3"}


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


class TestPatchIdFieldMap:
    def test_patch_code_no_holdings(self, client):
        m = _get_mapping(client, "gold", "account:工行")
        resp = client.patch(
            f"/settings/sources/gold/mappings/{m['id']}", json={"value": {"code": "ICBC2"}}
        )
        assert resp.status_code == 200
        assert resp.json()["map_value"] == {"code": "ICBC2"}

    def test_patch_code_blocked_by_holdings(self, client, db):
        _insert_holding(db, "GOLD_PAPER_CMB")
        m = _get_mapping(client, "gold", "account:招行")
        resp = client.patch(
            f"/settings/sources/gold/mappings/{m['id']}", json={"value": {"code": "CMB9"}}
        )
        assert resp.status_code == 409
        assert "Cannot change code" in resp.json()["detail"]

    def test_patch_code_empty_422(self, client):
        m = _get_mapping(client, "gold", "account:工行")
        resp = client.patch(f"/settings/sources/gold/mappings/{m['id']}", json={"value": {"code": ""}})
        assert resp.status_code == 422

    def test_patch_sort_order(self, client):
        m = _get_mapping(client, "gold", "account:工行")
        resp = client.patch(f"/settings/sources/gold/mappings/{m['id']}", json={"sort_order": 99})
        assert resp.status_code == 200
        assert resp.json()["sort_order"] == 99


# ---------------------------------------------------------------------------
# Archive / Restore
# ---------------------------------------------------------------------------


class TestArchiveRestoreIdFieldMap:
    def test_archive_no_holdings(self, client):
        m = _get_mapping(client, "gold", "account:建行")
        resp = client.post(f"/settings/sources/gold/mappings/{m['id']}/archive")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mapping"]["status"] == "archived"
        assert body["asset_has_holdings"] is False
        assert body["deactivate_hint"] is None

    def test_archive_with_holdings_no_deactivate_hint(self, client, db):
        """id_field_map has no single asset_id to chain a deactivation into —
        deactivate_hint is always None, even when holdings are affected."""
        _insert_holding(db, "GOLD_PAPER_BOC")
        m = _get_mapping(client, "gold", "account:中行")
        resp = client.post(f"/settings/sources/gold/mappings/{m['id']}/archive")
        assert resp.status_code == 200
        body = resp.json()
        assert body["asset_has_holdings"] is True
        assert body["deactivate_hint"] is None

    def test_restore(self, client):
        m = _get_mapping(client, "gold", "account:建行")
        client.post(f"/settings/sources/gold/mappings/{m['id']}/archive")
        resp = client.post(f"/settings/sources/gold/mappings/{m['id']}/restore")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


class TestDeleteIdFieldMap:
    def test_delete_no_related_rows(self, client):
        m = _get_mapping(client, "gold", "account:建行")
        resp = client.delete(f"/settings/sources/gold/mappings/{m['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "CCB"
        assert body["asset_id"] is None

        resp2 = client.get("/settings/sources/gold/mappings", params={"kind": "id_field_map"})
        assert m["id"] not in {row["id"] for row in resp2.json()["mappings"]}

    def test_delete_blocked_by_holdings(self, client, db):
        _insert_holding(db, "GOLD_PAPER_ICBC")
        m = _get_mapping(client, "gold", "account:工行")
        resp = client.delete(f"/settings/sources/gold/mappings/{m['id']}")
        assert resp.status_code == 409
        assert "Cannot delete" in resp.json()["detail"]

    def test_delete_blocked_by_transactions(self, client, db):
        _insert_transaction(db, "GOLD_PAPER_BOC")
        m = _get_mapping(client, "gold", "account:中行")
        resp = client.delete(f"/settings/sources/gold/mappings/{m['id']}")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Ignore-column / unignore — fs_column only, must 404 for id_field_map readers
# ---------------------------------------------------------------------------


class TestIgnoreColumnNotApplicable:
    def test_ignore_column_404_for_gold(self, client):
        resp = client.post("/settings/sources/gold/mappings/ignore-column", json={"map_key": "account:x"})
        assert resp.status_code == 404

    def test_unignore_404_for_gold(self, client):
        resp = client.post("/settings/sources/gold/mappings/1/unignore")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


class TestPreviewIdFieldMap:
    def test_preview_no_file(self, client, tmp_path):
        with _patched_settings(tmp_path, "gold", "Gold_test.xlsx"):
            resp = client.post("/settings/sources/gold/mappings/preview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_path"] is None
        assert body["items"] == []

    def test_preview_gold_file(self, client, tmp_path):
        _make_gold_fixture(tmp_path)
        with _patched_settings(tmp_path, "gold", "Gold_test.xlsx"):
            resp = client.post("/settings/sources/gold/mappings/preview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_path"] is not None
        items_by_label = {(item["field"], item["label"]): item for item in body["items"]}

        assert items_by_label[("asset_name", "纸黄金")]["mapped"] is True
        assert items_by_label[("asset_name", "纸黄金")]["code"] == "PAPER"
        assert items_by_label[("account", "招行")]["mapped"] is True
        assert items_by_label[("account", "招行")]["code"] == "CMB"
        assert items_by_label[("account", "澳门银行")]["mapped"] is False
        assert items_by_label[("account", "澳门银行")]["code"] is None

        unmapped_keys = {c["column"] for c in body["unmapped_columns"]}
        assert "account:澳门银行" in unmapped_keys
        assert all(c["category"] == "candidate" for c in body["unmapped_columns"])

    def test_list_unmapped_columns_matches_preview(self, client, tmp_path):
        """The GET list endpoint's unmapped_columns must agree with preview's
        scan (both drive off the same scan_unmapped_id_field_map_labels)."""
        _make_gold_fixture(tmp_path)
        with _patched_settings(tmp_path, "gold", "Gold_test.xlsx"):
            resp = client.get("/settings/sources/gold/mappings", params={"kind": "id_field_map"})
        assert resp.status_code == 200
        unmapped_keys = {c["column"] for c in resp.json()["unmapped_columns"]}
        assert "account:澳门银行" in unmapped_keys


# ---------------------------------------------------------------------------
# GET /settings/sources amber-chip unmapped_count (ADR-023 WS-B — generalized
# from _compute_fs_unmapped_count to _compute_unmapped_count in settings.py)
# ---------------------------------------------------------------------------


def _mock_source_registry_settings(tmp_path: Path, reader: str, filename: str) -> dict:
    return {
        "source_registry": {
            reader: {
                "enabled": True,
                "data_dir": str(tmp_path),
                "file_patterns": {"workbook": filename},
            }
        },
        "finance_dir": str(tmp_path),
    }


class TestSourcesUnmappedCountGeneralized:
    def test_gold_unmapped_count_reflects_candidate_labels(self, client, tmp_path):
        _make_gold_fixture(tmp_path)
        with patch(
            "src.api.routes.settings.settings_manager.load_settings",
            return_value=_mock_source_registry_settings(tmp_path, "gold", "Gold_test.xlsx"),
        ):
            resp = client.get("/settings/sources")
        assert resp.status_code == 200
        sources = {s["key"]: s for s in resp.json()["sources"]}
        # Only "account:澳门银行" is unmapped in the fixture.
        assert sources["gold"]["unmapped_count"] == 1

    def test_gold_unmapped_count_none_when_file_missing(self, client, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with patch(
            "src.api.routes.settings.settings_manager.load_settings",
            return_value=_mock_source_registry_settings(empty_dir, "gold", "Gold_test.xlsx"),
        ):
            resp = client.get("/settings/sources")
        assert resp.status_code == 200
        sources = {s["key"]: s for s in resp.json()["sources"]}
        assert sources["gold"]["unmapped_count"] is None
