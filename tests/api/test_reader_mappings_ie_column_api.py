"""Tests for the ie_column mapping kind in src/api/routes/reader_mappings.py.

Plan: docs/plans/2026-08-01-ie-column-mapping-and-ibkr-amounts.md WS-A
(migration V82). ie_column makes the Financial Summary 月度收支 sheet's column
SEMANTICS (invested / redemption / income / … and the destination bucket) data
the owner can edit, instead of string literals in
src/services/investment_contributions.py.

Uses tmp_path DuckDB files via bootstrap_database (schema + ALL migrations,
including V82's idempotent ie_column seed) — mirrors
tests/api/test_reader_mappings_api.py. Never touches data/unified.duckdb.
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
from src.database.mapping_seeds import IE_COLUMN_SEED
from src.database.schema import bootstrap_database

pytestmark = pytest.mark.pipeline

_IE_SEED_COUNT = len(IE_COLUMN_SEED)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "reader_mappings_ie_test.duckdb"
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
    """Minimal Financial Summary xlsx with BOTH sheets, header at row index 3.

    月度收支 columns:
      日期                        — date column, excluded from the scan
      投资理财_股票基金_天天基金    — a seeded ie_column mapping (invested/cn_fund)
      投资理财_股票基金_IBKR       — a seeded mapping added 2026-08-01 (invested/us_ibkr)
      收入_被动收入_股票卖出收益    — seeded, and EMPTY in the real Excel today
      投资理财_股票基金_某新券商_USD — unmapped native-currency sibling -> 'native'
      投资理财_股票基金_某个新券商   — genuinely unmapped -> 'candidate'
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "资产负债"
    ws.append(["", "", ""])
    ws.append(["", "Group Label", ""])
    ws.append(["", "", ""])
    ws.append(["日期", "RMB现金现金", "新增测试列"])
    ws.append([datetime(2025, 1, 1), 1000.0, 999.0])

    ie = wb.create_sheet("月度收支")
    ie.append(["", "", "", "", "", ""])
    ie.append(["", "Group Label", "", "", "", ""])
    ie.append(["", "", "", "", "", ""])
    ie.append([
        "日期",
        "投资理财_股票基金_天天基金",
        "投资理财_股票基金_IBKR",
        "收入_被动收入_股票卖出收益",
        "投资理财_股票基金_某新券商_USD",
        "投资理财_股票基金_某个新券商",
    ])
    ie.append([datetime(2025, 1, 1), 1000.0, 0.0, None, 140.0, 500.0])
    ie.append([datetime(2025, 2, 1), 2000.0, 7000.0, None, 280.0, 600.0])

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


def _get_mapping_id(client, map_key: str) -> int:
    resp = client.get("/settings/sources/financial_summary/mappings", params={"kind": "ie_column"})
    assert resp.status_code == 200
    for m in resp.json()["mappings"]:
        if m["map_key"] == map_key:
            return m["id"]
    raise AssertionError(f"map_key {map_key!r} not found in the ie_column list")


# ---------------------------------------------------------------------------
# Kind resolution — financial_summary became MULTI-KIND (fs_column + ie_column)
# ---------------------------------------------------------------------------


class TestKindResolution:
    def test_omitted_kind_still_defaults_to_fs_column(self, client):
        """Backward-compat guarantee: every caller written before ie_column
        existed omits `kind` and must keep getting fs_column, not a 422."""
        resp = client.get("/settings/sources/financial_summary/mappings")
        assert resp.status_code == 200
        assert resp.json()["mapping_kind"] == "fs_column"

    def test_ie_column_is_selectable(self, client):
        resp = client.get(
            "/settings/sources/financial_summary/mappings", params={"kind": "ie_column"}
        )
        assert resp.status_code == 200
        assert resp.json()["mapping_kind"] == "ie_column"

    def test_unknown_kind_422(self, client):
        resp = client.get(
            "/settings/sources/financial_summary/mappings", params={"kind": "not_a_kind"}
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET list
# ---------------------------------------------------------------------------


class TestListIeColumnMappings:
    def test_lists_the_v82_seed(self, client):
        resp = client.get(
            "/settings/sources/financial_summary/mappings", params={"kind": "ie_column"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["mappings"]) == _IE_SEED_COUNT
        by_key = {m["map_key"]: m["map_value"] for m in body["mappings"]}
        assert by_key["投资理财_股票基金_IBKR"] == {
            "role": "invested", "bucket": "us_ibkr", "currency": "CNY",
        }
        assert by_key["收入_被动收入_股票卖出收益"]["role"] == "income"
        assert by_key["投资理财_股票基金_Schawab_USD"]["currency"] == "USD"

    def test_unmapped_scan_surfaces_a_new_month_column_as_candidate(self, client, tmp_path):
        """The Rule-12 half of WS-A: a 月度收支 column nobody mapped used to
        vanish out of gross_invested silently. It must now be actionable."""
        _make_fs_fixture(tmp_path)
        with _patched_settings(tmp_path):
            resp = client.get(
                "/settings/sources/financial_summary/mappings", params={"kind": "ie_column"}
            )
        assert resp.status_code == 200
        unmapped = {c["column"]: c["category"] for c in resp.json()["unmapped_columns"]}
        assert unmapped.get("投资理财_股票基金_某个新券商") == "candidate"
        # A native-currency sibling is structurally not actionable (it must
        # contribute to nothing whether it is mapped or not).
        assert unmapped.get("投资理财_股票基金_某新券商_USD") == "native"
        # Seeded columns and the date column are never reported.
        assert "投资理财_股票基金_天天基金" not in unmapped
        assert "日期" not in unmapped

    def test_ie_scan_does_not_leak_balance_sheet_columns(self, client, tmp_path):
        _make_fs_fixture(tmp_path)
        with _patched_settings(tmp_path):
            resp = client.get(
                "/settings/sources/financial_summary/mappings", params={"kind": "ie_column"}
            )
        columns = {c["column"] for c in resp.json()["unmapped_columns"]}
        assert "新增测试列" not in columns, "that column is on the 资产负债 sheet, not 月度收支"

    def test_fs_column_scan_unaffected(self, client, tmp_path):
        _make_fs_fixture(tmp_path)
        with _patched_settings(tmp_path):
            resp = client.get(
                "/settings/sources/financial_summary/mappings", params={"kind": "fs_column"}
            )
        columns = {c["column"] for c in resp.json()["unmapped_columns"]}
        assert "新增测试列" in columns
        assert "投资理财_股票基金_某个新券商" not in columns


# ---------------------------------------------------------------------------
# POST create + validation
# ---------------------------------------------------------------------------


def _create(client, map_key: str, value: dict):
    return client.post(
        "/settings/sources/financial_summary/mappings",
        json={"kind": "ie_column", "map_key": map_key, "value": value},
    )


class TestCreateIeColumnMapping:
    def test_create_success(self, client):
        resp = _create(
            client, "投资理财_股票基金_某个新券商",
            {"role": "invested", "bucket": "us_schwab", "currency": "CNY"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["mapping_kind"] == "ie_column"
        assert body["map_value"] == {"role": "invested", "bucket": "us_schwab", "currency": "CNY"}
        assert body["status"] == "active"

    def test_create_duplicate_map_key_422(self, client):
        resp = _create(
            client, "投资理财_股票基金_天天基金",
            {"role": "invested", "bucket": "cn_fund", "currency": "CNY"},
        )
        assert resp.status_code == 422

    def test_bad_role_422(self, client):
        resp = _create(client, "新列", {"role": "contribution", "bucket": None, "currency": "CNY"})
        assert resp.status_code == 422
        assert "role" in resp.json()["detail"]

    def test_invested_without_bucket_422(self, client):
        """An invested column with no destination bucket would contribute to
        nothing — the exact silent failure this kind removes."""
        resp = _create(client, "新列", {"role": "invested", "bucket": None, "currency": "CNY"})
        assert resp.status_code == 422
        assert "bucket" in resp.json()["detail"]

    def test_bad_bucket_for_role_422(self, client):
        resp = _create(client, "新列", {"role": "invested", "bucket": "inflow", "currency": "CNY"})
        assert resp.status_code == 422

    def test_expense_with_a_bucket_422(self, client):
        resp = _create(client, "新列", {"role": "expense", "bucket": "gold", "currency": "CNY"})
        assert resp.status_code == 422

    def test_bad_currency_422(self, client):
        resp = _create(client, "新列", {"role": "invested", "bucket": "gold", "currency": "HKD"})
        assert resp.status_code == 422

    def test_no_column_can_claim_to_be_the_income_total_422(self, client):
        """Supersedes the old "only one column may be the income basis" guard.

        The basis used to be READ from 总收入合计 (bucket='total_income'), so a
        second such column would double-count income. The owner retired that
        design on 2026-08-01 — the basis is the sum of the income LEAF columns
        — and with it the bucket: role='income' now carries NO bucket at all,
        so the retired value cannot be resurrected from the UI either.
        """
        resp = _create(client, "另一个收入合计", {"role": "income", "bucket": "total_income", "currency": "CNY"})
        assert resp.status_code == 422
        assert "total_income" in resp.json()["detail"]
        assert "role 'income'" in resp.json()["detail"]

    def test_income_component_without_bucket_is_fine(self, client):
        resp = _create(client, "收入_被动收入_利息", {"role": "income", "bucket": None, "currency": "CNY"})
        assert resp.status_code == 201
        assert resp.json()["map_value"]["bucket"] is None

    def test_pass_through_role_is_accepted(self, client):
        """ADR-025 Amendment 2026-08-01 (WS-G) — a second 报销-style round trip
        must be classifiable from the UI without a code change, on either end.
        (Supersedes the short-lived `reimbursement` role, which had only one.)
        """
        resp = _create(
            client, "收入_主动收入_差旅报销",
            {"role": "pass_through", "bucket": "inflow", "currency": "CNY"},
        )
        assert resp.status_code == 201
        assert resp.json()["map_value"] == {
            "role": "pass_through", "bucket": "inflow", "currency": "CNY",
        }
        resp = _create(
            client, "工作开支_差旅垫付",
            {"role": "pass_through", "bucket": "outflow", "currency": "CNY"},
        )
        assert resp.status_code == 201
        assert resp.json()["map_value"]["bucket"] == "outflow"

    def test_retired_reimbursement_role_422(self, client):
        """The role that `pass_through` replaced. A DB row still carrying it
        would be counted in NOTHING by ie_ledger (with a warning), so the API
        must not let a new one in."""
        resp = _create(
            client, "收入_主动收入_差旅报销", {"role": "reimbursement", "bucket": None, "currency": "CNY"}
        )
        assert resp.status_code == 422
        assert "role" in resp.json()["detail"]

    def test_pass_through_with_a_destination_bucket_422(self, client):
        """A pass_through bucket names WHICH END of the round trip it is — an
        investment destination there would put a repayment into
        gross_invested."""
        resp = _create(
            client, "收入_主动收入_差旅报销",
            {"role": "pass_through", "bucket": "cn_fund", "currency": "CNY"},
        )
        assert resp.status_code == 422

    def test_seeded_pass_through_rows_are_listed(self, client):
        """Both ends of the round trip, each tagged with its Excel subtotal
        group — the pairing has to be visible in the UI, not implicit."""
        resp = client.get(
            "/settings/sources/financial_summary/mappings", params={"kind": "ie_column"}
        )
        by_key = {m["map_key"]: m["map_value"] for m in resp.json()["mappings"]}
        assert by_key["收入_主动收入_报销"] == {
            "role": "pass_through", "bucket": "inflow", "currency": "CNY",
            "group": "active_income",
        }
        assert by_key["工作开支_出差/团建（全额报销）"] == {
            "role": "pass_through", "bucket": "outflow", "currency": "CNY",
            "group": "work_expense",
        }


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


class TestPatchIeColumnMapping:
    def test_patch_bucket(self, client):
        mid = _get_mapping_id(client, "投资理财_股票基金_IBKR")
        resp = client.patch(
            f"/settings/sources/financial_summary/mappings/{mid}",
            json={"value": {"bucket": "us_schwab"}},
        )
        assert resp.status_code == 200
        assert resp.json()["map_value"] == {
            "role": "invested", "bucket": "us_schwab", "currency": "CNY",
        }

    def test_patch_role_revalidates_the_whole_value(self, client):
        """Flipping a bucket-less column to role='invested' must 422 rather than
        persist an invested row with nowhere to land."""
        mid = _get_mapping_id(client, "收入_被动收入_股票卖出收益")
        resp = client.patch(
            f"/settings/sources/financial_summary/mappings/{mid}",
            json={"value": {"role": "invested"}},
        )
        assert resp.status_code == 422

    def test_patch_role_and_bucket_together_succeeds(self, client):
        mid = _get_mapping_id(client, "收入_被动收入_股票卖出收益")
        resp = client.patch(
            f"/settings/sources/financial_summary/mappings/{mid}",
            json={"value": {"role": "invested", "bucket": "us_ibkr"}},
        )
        assert resp.status_code == 200
        assert resp.json()["map_value"]["bucket"] == "us_ibkr"

    def test_patch_cannot_resurrect_the_retired_income_total_bucket(self, client):
        mid = _get_mapping_id(client, "收入_主动收入_工资")
        resp = client.patch(
            f"/settings/sources/financial_summary/mappings/{mid}",
            json={"value": {"bucket": "total_income"}},
        )
        assert resp.status_code == 422

    def test_patch_cannot_turn_an_excel_aggregate_into_a_calculation_input(self, client):
        """WS-E, as an API guardrail: 总收入合计 is role='computed' and may only
        stay classified/cross-checked. Giving it a summable role from the UI
        would put an Excel-computed aggregate back into the arithmetic
        alongside its own leaves — the double count V84 removed.
        """
        mid = _get_mapping_id(client, "总收入合计")
        resp = client.patch(
            f"/settings/sources/financial_summary/mappings/{mid}",
            json={"value": {"bucket": "total_income", "currency": "CNY"}},
        )
        assert resp.status_code == 422
        # Its own row is not blocked from a legitimate edit, though — the
        # cross-check target is data the owner may re-declare.
        resp = client.patch(
            f"/settings/sources/financial_summary/mappings/{mid}",
            json={"value": {"validates": {"groups": ["active_income"]}}},
        )
        assert resp.status_code == 200
        assert resp.json()["map_value"]["role"] == "computed"
        assert resp.json()["map_value"]["validates"]["groups"] == ["active_income"]

    def test_patch_currency_to_usd_takes_the_column_out_of_every_total(self, client):
        mid = _get_mapping_id(client, "投资理财_股票基金_IBKR")
        resp = client.patch(
            f"/settings/sources/financial_summary/mappings/{mid}",
            json={"value": {"currency": "USD"}},
        )
        assert resp.status_code == 200
        assert resp.json()["map_value"]["currency"] == "USD"


# ---------------------------------------------------------------------------
# Archive / restore / delete
# ---------------------------------------------------------------------------


class TestArchiveRestoreDeleteIeColumn:
    def test_archive_removes_it_from_the_merged_mapping(self, client, db):
        from src.services.reader_mappings import load_reader_mappings

        mid = _get_mapping_id(client, "投资理财_黄金_黄金ETF")
        resp = client.post(f"/settings/sources/financial_summary/mappings/{mid}/archive")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mapping"]["status"] == "archived"
        # An ie_column row derives no asset_id, so there is nothing to chain into.
        assert body["asset_has_holdings"] is False
        assert body["deactivate_hint"] is None
        merged = load_reader_mappings(db, "financial_summary", "ie_column")
        assert "投资理财_黄金_黄金ETF" not in merged

    def test_restore(self, client):
        mid = _get_mapping_id(client, "投资理财_黄金_黄金ETF")
        client.post(f"/settings/sources/financial_summary/mappings/{mid}/archive")
        resp = client.post(f"/settings/sources/financial_summary/mappings/{mid}/restore")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    def test_delete(self, client):
        mid = _get_mapping_id(client, "非必要开支_电子产品")
        resp = client.delete(f"/settings/sources/financial_summary/mappings/{mid}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == mid


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


class TestPreviewIeColumn:
    def test_preview_reports_per_column_stats(self, client, tmp_path):
        _make_fs_fixture(tmp_path)
        with _patched_settings(tmp_path):
            resp = client.post(
                "/settings/sources/financial_summary/mappings/preview", params={"kind": "ie_column"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["mapping_kind"] == "ie_column"
        results = {r["map_key"]: r for r in body["results"]}

        cn = results["投资理财_股票基金_天天基金"]
        assert cn["column_found"] is True
        assert cn["nonzero_rows"] == 2
        assert cn["latest_value"] == 2000.0
        assert cn["latest_date"].startswith("2025-02")

        ibkr = results["投资理财_股票基金_IBKR"]
        assert ibkr["column_found"] is True
        assert ibkr["nonzero_rows"] == 1, "the 0.0 month must not count as a value"

        empty = results["收入_被动收入_股票卖出收益"]
        assert empty["column_found"] is True
        assert empty["nonzero_rows"] == 0, "empty in the real Excel today — no path may need a value"
        assert empty["latest_value"] is None

        absent = results["投资理财_银行理财_招行"]
        assert absent["column_found"] is False
        assert absent["nonzero_rows"] == 0

    def test_preview_without_a_file_is_empty_not_an_error(self, client, tmp_path):
        with _patched_settings(tmp_path):  # no workbook written
            resp = client.post(
                "/settings/sources/financial_summary/mappings/preview", params={"kind": "ie_column"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_path"] is None
        assert body["results"] == []

    def test_preview_defaults_to_fs_column_when_kind_omitted(self, client, tmp_path):
        _make_fs_fixture(tmp_path)
        with _patched_settings(tmp_path):
            resp = client.post("/settings/sources/financial_summary/mappings/preview")
        assert resp.status_code == 200
        assert resp.json()["mapping_kind"] == "fs_column"


# ---------------------------------------------------------------------------
# End-to-end: an owner edit in the UI changes the ledger math
# ---------------------------------------------------------------------------


def test_owner_mapping_edit_changes_the_contribution_math(client, db):
    """The workstream's acceptance criterion: a new Excel column becomes part of
    gross_invested by adding a MAPPING ROW, with no code change."""
    import json as _json

    from src.services.investment_contributions import contributions_summary_v2

    db.execute(
        "INSERT INTO income_expense_monthly (record_key, transaction_date, payload) VALUES (?, ?, ?)",
        ["m1", "2025-08-01", _json.dumps({"投资理财_股票基金_某个新券商": 12345, "总收入合计": 50000})],
    )
    assert contributions_summary_v2(db)["gross_invested_ttm"] == 0.0

    resp = _create(
        client, "投资理财_股票基金_某个新券商",
        {"role": "invested", "bucket": "us_ibkr", "currency": "CNY"},
    )
    assert resp.status_code == 201

    after = contributions_summary_v2(db)
    assert after["gross_invested_ttm"] == 12345.0
    assert after["by_destination_ttm"]["us_ibkr"] == 12345.0
