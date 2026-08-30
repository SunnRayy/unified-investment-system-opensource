"""Tests for src/api/routes/reader_mappings.py — ADR-023 Reader Mapping
Management, Workstream C (Schwab known_etf/symbol_norm/action_map + CN-fund
type_map vocabularies).

Mirrors tests/api/test_reader_mappings_id_field_map_api.py's fixtures/
structure: tmp_path DuckDB via bootstrap_database (schema + ALL migrations,
including the V78 vocab seed). Never touches data/unified.duckdb.
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
from src.database.mapping_seeds import VOCAB_SEEDS
from src.database.schema import bootstrap_database

pytestmark = pytest.mark.pipeline

_KNOWN_ETF_SEED_COUNT = len(VOCAB_SEEDS["schwab"]["known_etf"])
_SYMBOL_NORM_SEED_COUNT = len(VOCAB_SEEDS["schwab"]["symbol_norm"])
_ACTION_MAP_SEED_COUNT = len(VOCAB_SEEDS["schwab"]["action_map"])
_TYPE_MAP_SEED_COUNT = len(VOCAB_SEEDS["cn_fund"]["type_map"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "vocab_api_test.duckdb"
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


def _make_schwab_txn_fixture(tmp_path: Path) -> Path:
    """Minimal Schwab transactions CSV matching the real header shape.
    Includes one unmapped action ("Journal Fee") and the compound ticker
    BRK/B to exercise the vocab scans."""
    path = tmp_path / "Individual_XXX999_Transactions_20260101-000000.csv"
    path.write_text(
        "Date,Action,Symbol,Description,Quantity,Price,Fees & Comm,Amount\n"
        '01/02/2026,Buy,SGOV,"ISHARES TR",10,$100.00,$0.00,-$1000.00\n'
        '01/03/2026,Journal Fee,MSFT,"MICROSOFT CORP",0,$0.00,$0.00,-$1.00\n'
        '01/04/2026,Sell,BRK/B,"BERKSHIRE HATHAWAY",1,$400.00,$0.00,$400.00\n'
    )
    return path


def _make_cn_fund_fixture(tmp_path: Path) -> Path:
    """Minimal CN Fund workbook with a 基金交易记录 sheet. Includes one
    unmapped 操作类型 ("神秘操作") to exercise the candidate scan."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "基金交易记录"
    ws.append(["交易日期", "基金代码", "基金名称", "操作类型", "交易金额", "交易份额", "交易时基金单位净值", "手续费", "交易原因"])
    ws.append(["2026-01-01", "900001", "测试基金", "申购", 1000.0, 500.0, 2.0, 0.0, ""])
    ws.append(["2026-01-02", "900001", "测试基金", "神秘操作", 100.0, 50.0, 2.0, 0.0, ""])

    path = tmp_path / "funding_transactions.xlsx"
    wb.save(path)
    return path


def _mock_settings(tmp_path: Path, reader: str, file_patterns: dict) -> dict:
    return {
        "source_registry": {
            reader: {
                "enabled": True,
                "data_dir": str(tmp_path),
                "file_patterns": file_patterns,
            }
        },
        "finance_dir": str(tmp_path),
    }


def _patched_settings(tmp_path: Path, reader: str, file_patterns: dict):
    return patch(
        "src.api.routes.reader_mappings.settings_manager.load_settings",
        return_value=_mock_settings(tmp_path, reader, file_patterns),
    )


_SCHWAB_PATTERNS = {
    "positions": "Individual-Positions-*.csv",
    "transactions": "Individual_*_Transactions_*.csv",
}
_CN_FUND_PATTERNS = {"workbook": "funding_transactions.xlsx"}


def _insert_holding(conn, asset_id: str, source_system: str = "Schwab_CSV"):
    conn.execute(
        "INSERT INTO holdings (snapshot_date, asset_id, quantity, market_value, currency, source_system) "
        "VALUES (?, ?, ?, ?, 'CNY', ?)",
        ["2026-01-01", asset_id, 1.0, 100.0, source_system],
    )


def _get_mapping(client, reader: str, kind: str, map_key: str) -> dict:
    resp = client.get(f"/settings/sources/{reader}/mappings", params={"kind": kind})
    assert resp.status_code == 200
    for m in resp.json()["mappings"]:
        if m["map_key"] == map_key:
            return m
    raise AssertionError(f"map_key {map_key!r} not found in {reader}/{kind} mappings list")


# ---------------------------------------------------------------------------
# GET list — multi-kind reader semantics
# ---------------------------------------------------------------------------


class TestListVocabMappings:
    def test_schwab_kind_required_422(self, client):
        """schwab manages three kinds — omitting kind= is ambiguous."""
        resp = client.get("/settings/sources/schwab/mappings")
        assert resp.status_code == 422
        assert "multiple mapping kinds" in resp.json()["detail"]

    def test_schwab_invalid_kind_422(self, client):
        resp = client.get("/settings/sources/schwab/mappings", params={"kind": "fs_column"})
        assert resp.status_code == 422

    def test_schwab_known_etf_list_seeded(self, client):
        resp = client.get("/settings/sources/schwab/mappings", params={"kind": "known_etf"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reader"] == "schwab"
        assert body["mapping_kind"] == "known_etf"
        assert len(body["mappings"]) == _KNOWN_ETF_SEED_COUNT
        assert body["defaults_only"] is False
        keys = {m["map_key"] for m in body["mappings"]}
        assert "QQQ" in keys and "SGOV" in keys
        qqq = next(m for m in body["mappings"] if m["map_key"] == "QQQ")
        assert qqq["map_value"] == {"etf": True}

    def test_schwab_symbol_norm_list_seeded(self, client):
        resp = client.get("/settings/sources/schwab/mappings", params={"kind": "symbol_norm"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["mappings"]) == _SYMBOL_NORM_SEED_COUNT
        brkb = next(m for m in body["mappings"] if m["map_key"] == "BRK/B")
        assert brkb["map_value"] == {"to": "BRK-B"}

    def test_schwab_action_map_list_seeded(self, client):
        resp = client.get("/settings/sources/schwab/mappings", params={"kind": "action_map"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["mappings"]) == _ACTION_MAP_SEED_COUNT
        buy = next(m for m in body["mappings"] if m["map_key"] == "Buy")
        assert buy["map_value"] == {"type": "buy"}

    def test_cn_fund_type_map_list_seeded_default_kind(self, client):
        """cn_fund is single-kind — kind= may be omitted."""
        resp = client.get("/settings/sources/cn_fund/mappings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mapping_kind"] == "type_map"
        assert len(body["mappings"]) == _TYPE_MAP_SEED_COUNT
        shengou = next(m for m in body["mappings"] if m["map_key"] == "申购")
        assert shengou["map_value"] == {"type": "buy"}

    def test_ibkr_still_404(self, client):
        """ibkr shares schwab's vocabularies (co-authority) — no own panel."""
        resp = client.get("/settings/sources/ibkr/mappings")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST create — validation
# ---------------------------------------------------------------------------


class TestCreateVocabValidation:
    def test_action_map_type_not_in_enum_422(self, client):
        resp = client.post(
            "/settings/sources/schwab/mappings",
            json={"kind": "action_map", "map_key": "Journal Fee", "value": {"type": "not_a_type"}},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "not an allowed transaction_type" in detail
        # The 422 must list the allowed values so the owner can self-correct.
        for t in ("buy", "sell", "other"):
            assert t in detail

    def test_type_map_type_not_in_enum_422(self, client):
        resp = client.post(
            "/settings/sources/cn_fund/mappings",
            json={"kind": "type_map", "map_key": "神秘操作", "value": {"type": "mystery"}},
        )
        assert resp.status_code == 422
        assert "not an allowed transaction_type" in resp.json()["detail"]

    def test_type_map_transfer_pseudo_type_422(self, client):
        """WS-3.1 (V79): 'transfer' is a Schwab-only pseudo-type — only the
        Schwab transactions hook resolves it by quantity sign. A type_map row
        targeting it would persist a literal 'transfer' on CN-fund rows that
        no consumer understands, so it must hard-422 (kind-scoped exclusion,
        not an enum removal — action_map keeps accepting it)."""
        resp = client.post(
            "/settings/sources/cn_fund/mappings",
            json={"kind": "type_map", "map_key": "证券转托管", "value": {"type": "transfer"}},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "Schwab-only pseudo-type" in detail
        assert "action_map" in detail

    def test_patch_type_map_to_transfer_pseudo_type_422(self, client):
        """The same kind-scoped exclusion must hold on PATCH (edit path)."""
        m = _get_mapping(client, "cn_fund", "type_map", "快速取现")
        resp = client.patch(
            f"/settings/sources/cn_fund/mappings/{m['id']}",
            json={"value": {"type": "transfer"}},
        )
        assert resp.status_code == 422
        assert "Schwab-only pseudo-type" in resp.json()["detail"]

    def test_known_etf_value_fixed_422(self, client):
        resp = client.post(
            "/settings/sources/schwab/mappings",
            json={"kind": "known_etf", "map_key": "VGT", "value": {"etf": False}},
        )
        assert resp.status_code == 422
        assert '{"etf": true}' in resp.json()["detail"]

    def test_symbol_norm_to_empty_422(self, client):
        resp = client.post(
            "/settings/sources/schwab/mappings",
            json={"kind": "symbol_norm", "map_key": "FOO/B", "value": {"to": "  "}},
        )
        assert resp.status_code == 422
        assert "must not be empty" in resp.json()["detail"]

    def test_known_etf_ticker_with_whitespace_422(self, client):
        resp = client.post(
            "/settings/sources/schwab/mappings",
            json={"kind": "known_etf", "map_key": "V GT", "value": {"etf": True}},
        )
        assert resp.status_code == 422
        assert "ASCII-safe" in resp.json()["detail"]

    def test_duplicate_active_map_key_422(self, client):
        resp = client.post(
            "/settings/sources/schwab/mappings",
            json={"kind": "known_etf", "map_key": "QQQ", "value": {"etf": True}},
        )
        assert resp.status_code == 422
        assert "already has an active mapping" in resp.json()["detail"]

    def test_kind_required_on_multi_kind_reader_422(self, client):
        resp = client.post(
            "/settings/sources/schwab/mappings",
            json={"kind": "", "map_key": "VGT", "value": {"etf": True}},
        )
        assert resp.status_code == 422


class TestCreateVocab:
    def test_create_known_etf(self, client):
        resp = client.post(
            "/settings/sources/schwab/mappings",
            json={"kind": "known_etf", "map_key": "VGT", "value": {"etf": True}},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["map_key"] == "VGT"
        assert body["map_value"] == {"etf": True}
        assert body["status"] == "active"

    def test_create_known_etf_lowercase_uppercased(self, client):
        """The schwab symbol normalizer uppercases before lookup — a
        lowercase key would silently never match, so the API normalizes."""
        resp = client.post(
            "/settings/sources/schwab/mappings",
            json={"kind": "known_etf", "map_key": "vgt", "value": {"etf": True}},
        )
        assert resp.status_code == 201
        assert resp.json()["map_key"] == "VGT"

    def test_create_symbol_norm(self, client):
        resp = client.post(
            "/settings/sources/schwab/mappings",
            json={"kind": "symbol_norm", "map_key": "FOO/B", "value": {"to": "FOO-B"}},
        )
        assert resp.status_code == 201
        assert resp.json()["map_value"] == {"to": "FOO-B"}

    def test_create_action_map(self, client):
        resp = client.post(
            "/settings/sources/schwab/mappings",
            json={"kind": "action_map", "map_key": "Journal Fee", "value": {"type": "other"}},
        )
        assert resp.status_code == 201
        assert resp.json()["map_value"] == {"type": "other"}

    def test_create_action_map_transfer_pseudo_type_accepted(self, client):
        """WS-3.1 (V79): action_map (Schwab) MAY target the 'transfer'
        pseudo-type — the Schwab transactions hook resolves it by quantity
        sign. ('Security Transfer' itself is already seeded by V79, so a
        fresh map_key is used here.)"""
        resp = client.post(
            "/settings/sources/schwab/mappings",
            json={"kind": "action_map", "map_key": "Internal Transfer", "value": {"type": "transfer"}},
        )
        assert resp.status_code == 201
        assert resp.json()["map_value"] == {"type": "transfer"}

    def test_create_type_map_chinese_label(self, client):
        resp = client.post(
            "/settings/sources/cn_fund/mappings",
            json={"kind": "type_map", "map_key": "神秘操作", "value": {"type": "other"}},
        )
        assert resp.status_code == 201
        assert resp.json()["map_key"] == "神秘操作"

    def test_reactivate_archived_row(self, client):
        archived = _get_mapping(client, "schwab", "action_map", "Journal")
        resp = client.post(f"/settings/sources/schwab/mappings/{archived['id']}/archive")
        assert resp.status_code == 200

        resp2 = client.post(
            "/settings/sources/schwab/mappings",
            json={"kind": "action_map", "map_key": "Journal", "value": {"type": "transfer_in"}},
        )
        assert resp2.status_code == 201
        assert resp2.json()["id"] == archived["id"]
        assert resp2.json()["status"] == "active"
        assert resp2.json()["map_value"] == {"type": "transfer_in"}


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


class TestPatchVocab:
    def test_patch_action_type(self, client):
        m = _get_mapping(client, "schwab", "action_map", "Credit Interest")
        resp = client.patch(
            f"/settings/sources/schwab/mappings/{m['id']}", json={"value": {"type": "interest"}}
        )
        assert resp.status_code == 200
        assert resp.json()["map_value"] == {"type": "interest"}

    def test_patch_action_type_enum_422(self, client):
        m = _get_mapping(client, "schwab", "action_map", "Buy")
        resp = client.patch(
            f"/settings/sources/schwab/mappings/{m['id']}", json={"value": {"type": "nonsense"}}
        )
        assert resp.status_code == 422
        assert "not an allowed transaction_type" in resp.json()["detail"]

    def test_patch_type_map_type(self, client):
        m = _get_mapping(client, "cn_fund", "type_map", "快速取现")
        resp = client.patch(
            f"/settings/sources/cn_fund/mappings/{m['id']}", json={"value": {"type": "transfer_out"}}
        )
        assert resp.status_code == 200
        assert resp.json()["map_value"] == {"type": "transfer_out"}

    def test_patch_symbol_norm_to_no_references(self, client):
        """No holdings/transactions reference the old target — the edit is
        allowed, and the new target is uppercased."""
        m = _get_mapping(client, "schwab", "symbol_norm", "BRKA")
        resp = client.patch(
            f"/settings/sources/schwab/mappings/{m['id']}", json={"value": {"to": "brka2"}}
        )
        assert resp.status_code == 200
        assert resp.json()["map_value"] == {"to": "BRKA2"}

    def test_patch_symbol_norm_blocked_by_references(self, client, db):
        """Changing a normalization target whose OLD target already has
        holdings rows -> 409 (archive + create instead)."""
        _insert_holding(db, "US_STK_BRK-B")
        m = _get_mapping(client, "schwab", "symbol_norm", "BRK/B")
        resp = client.patch(
            f"/settings/sources/schwab/mappings/{m['id']}", json={"value": {"to": "BRKB2"}}
        )
        assert resp.status_code == 409
        assert "archive this mapping" in resp.json()["detail"]

    def test_patch_known_etf_value_rejected(self, client):
        m = _get_mapping(client, "schwab", "known_etf", "QQQ")
        resp = client.patch(
            f"/settings/sources/schwab/mappings/{m['id']}", json={"value": {"etf": False}}
        )
        assert resp.status_code == 422

    def test_patch_sort_order(self, client):
        m = _get_mapping(client, "schwab", "known_etf", "QQQ")
        resp = client.patch(f"/settings/sources/schwab/mappings/{m['id']}", json={"sort_order": 99})
        assert resp.status_code == 200
        assert resp.json()["sort_order"] == 99


# ---------------------------------------------------------------------------
# Archive / Restore / Delete guards
# ---------------------------------------------------------------------------


class TestArchiveRestoreDeleteVocab:
    def test_archive_known_etf_no_holdings(self, client):
        m = _get_mapping(client, "schwab", "known_etf", "TZA")
        resp = client.post(f"/settings/sources/schwab/mappings/{m['id']}/archive")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mapping"]["status"] == "archived"
        assert body["asset_has_holdings"] is False
        assert body["deactivate_hint"] is None

    def test_archive_known_etf_with_holdings_reports_no_hint(self, client, db):
        """A vocab mapping has no single deactivation target — deactivate_hint
        is always null even when the exact-asset_id check finds holdings."""
        _insert_holding(db, "US_ETF_SGOV")
        m = _get_mapping(client, "schwab", "known_etf", "SGOV")
        resp = client.post(f"/settings/sources/schwab/mappings/{m['id']}/archive")
        assert resp.status_code == 200
        body = resp.json()
        assert body["asset_has_holdings"] is True
        assert body["deactivate_hint"] is None

    def test_restore(self, client):
        m = _get_mapping(client, "schwab", "known_etf", "TZA")
        client.post(f"/settings/sources/schwab/mappings/{m['id']}/archive")
        resp = client.post(f"/settings/sources/schwab/mappings/{m['id']}/restore")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    def test_delete_known_etf_no_references(self, client):
        m = _get_mapping(client, "schwab", "known_etf", "TNA")
        resp = client.delete(f"/settings/sources/schwab/mappings/{m['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == m["id"]
        assert body["code"] == "TNA"
        assert body["asset_id"] is None

    def test_delete_known_etf_blocked_by_holdings(self, client, db):
        _insert_holding(db, "US_ETF_QQQ")
        m = _get_mapping(client, "schwab", "known_etf", "QQQ")
        resp = client.delete(f"/settings/sources/schwab/mappings/{m['id']}")
        assert resp.status_code == 409
        assert "Cannot delete" in resp.json()["detail"]

    def test_delete_action_map_always_allowed(self, client):
        """Raw action labels aren't persisted on transaction rows — no
        reference check is possible (documented in spec C3), so delete of an
        action_map row succeeds."""
        m = _get_mapping(client, "schwab", "action_map", "ACH")
        resp = client.delete(f"/settings/sources/schwab/mappings/{m['id']}")
        assert resp.status_code == 200

    def test_ignore_column_404_for_schwab(self, client):
        resp = client.post(
            "/settings/sources/schwab/mappings/ignore-column", json={"map_key": "X"}
        )
        assert resp.status_code == 404

    def test_ignore_column_404_for_cn_fund(self, client):
        resp = client.post(
            "/settings/sources/cn_fund/mappings/ignore-column", json={"map_key": "X"}
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


class TestPreviewVocab:
    def test_preview_kind_required_for_schwab_422(self, client):
        resp = client.post("/settings/sources/schwab/mappings/preview")
        assert resp.status_code == 422

    def test_preview_no_file(self, client, tmp_path):
        with _patched_settings(tmp_path, "schwab", _SCHWAB_PATTERNS):
            resp = client.post("/settings/sources/schwab/mappings/preview", params={"kind": "action_map"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_path"] is None
        assert body["items"] == []

    def test_preview_schwab_action_map(self, client, tmp_path):
        _make_schwab_txn_fixture(tmp_path)
        with _patched_settings(tmp_path, "schwab", _SCHWAB_PATTERNS):
            resp = client.post("/settings/sources/schwab/mappings/preview", params={"kind": "action_map"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_path"] is not None
        by_value = {i["value"]: i for i in body["items"]}
        assert by_value["Buy"]["mapped"] is True
        assert by_value["Buy"]["mapped_value"] == {"type": "buy"}
        assert by_value["Journal Fee"]["mapped"] is False
        # Unmapped actions ARE candidates (they'd melt to 'other').
        unmapped = {c["column"] for c in body["unmapped_columns"]}
        assert "Journal Fee" in unmapped

    def test_preview_schwab_known_etf_no_candidates(self, client, tmp_path):
        """known_etf preview shows the full scan but surfaces NO candidates —
        an unmapped symbol is the normal case (most tickers are stocks)."""
        _make_schwab_txn_fixture(tmp_path)
        with _patched_settings(tmp_path, "schwab", _SCHWAB_PATTERNS):
            resp = client.post("/settings/sources/schwab/mappings/preview", params={"kind": "known_etf"})
        assert resp.status_code == 200
        body = resp.json()
        by_value = {i["value"]: i for i in body["items"]}
        assert by_value["SGOV"]["mapped"] is True
        assert by_value["MSFT"]["mapped"] is False
        assert body["unmapped_columns"] == []

    def test_preview_cn_fund_type_map(self, client, tmp_path):
        _make_cn_fund_fixture(tmp_path)
        with _patched_settings(tmp_path, "cn_fund", _CN_FUND_PATTERNS):
            resp = client.post("/settings/sources/cn_fund/mappings/preview")
        assert resp.status_code == 200
        body = resp.json()
        by_value = {i["value"]: i for i in body["items"]}
        assert by_value["申购"]["mapped"] is True
        assert by_value["申购"]["mapped_value"] == {"type": "buy"}
        assert by_value["神秘操作"]["mapped"] is False
        unmapped = {c["column"] for c in body["unmapped_columns"]}
        assert "神秘操作" in unmapped

    def test_list_unmapped_matches_preview_for_action_map(self, client, tmp_path):
        _make_schwab_txn_fixture(tmp_path)
        with _patched_settings(tmp_path, "schwab", _SCHWAB_PATTERNS):
            resp = client.get("/settings/sources/schwab/mappings", params={"kind": "action_map"})
        assert resp.status_code == 200
        unmapped = {c["column"] for c in resp.json()["unmapped_columns"]}
        assert "Journal Fee" in unmapped


# ---------------------------------------------------------------------------
# GET /settings/sources amber chip (WS-C: schwab action_map, cn_fund type_map)
# ---------------------------------------------------------------------------


class TestSourcesUnmappedCountVocab:
    def test_schwab_unmapped_count_counts_action_candidates(self, client, tmp_path):
        _make_schwab_txn_fixture(tmp_path)
        with patch(
            "src.api.routes.settings.settings_manager.load_settings",
            return_value=_mock_settings(tmp_path, "schwab", _SCHWAB_PATTERNS),
        ):
            resp = client.get("/settings/sources")
        assert resp.status_code == 200
        sources = {s["key"]: s for s in resp.json()["sources"]}
        # Only "Journal Fee" is unmapped in the fixture.
        assert sources["schwab"]["unmapped_count"] == 1

    def test_schwab_unmapped_count_none_when_file_missing(self, client, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with patch(
            "src.api.routes.settings.settings_manager.load_settings",
            return_value=_mock_settings(empty_dir, "schwab", _SCHWAB_PATTERNS),
        ):
            resp = client.get("/settings/sources")
        assert resp.status_code == 200
        sources = {s["key"]: s for s in resp.json()["sources"]}
        assert sources["schwab"]["unmapped_count"] is None

    def test_cn_fund_unmapped_count(self, client, tmp_path):
        _make_cn_fund_fixture(tmp_path)
        with patch(
            "src.api.routes.settings.settings_manager.load_settings",
            return_value=_mock_settings(tmp_path, "cn_fund", _CN_FUND_PATTERNS),
        ):
            resp = client.get("/settings/sources")
        assert resp.status_code == 200
        sources = {s["key"]: s for s in resp.json()["sources"]}
        assert sources["cn_fund"]["unmapped_count"] == 1
