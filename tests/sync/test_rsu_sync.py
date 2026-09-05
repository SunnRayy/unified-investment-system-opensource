"""Tests for RSU sync module (Excel source)."""
import pytest

pytestmark = pytest.mark.pipeline

import openpyxl
from datetime import datetime
from src.sync.rsu_sync import sync_rsu


@pytest.fixture
def rsu_config(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transactions"
    ws.append(["交易日期", "资产名称", "交易类型", "单位", "数量",
               "单位价格_USD", "总金额_USD", "手续费_USD", "备注"])
    ws.append([datetime(2023, 9, 15), "Amazon RSU", "RSU Vest", "Shares",
               48, 172, 8256, None, "Vesting"])
    wb.create_sheet("Notes")
    wb.save(tmp_path / "RSU_transactions.xlsx")
    return {"source_registry": {"rsu": {
        "enabled": True,
        "file_patterns": {"workbook": "RSU_transactions.xlsx"},
        "data_dir": str(tmp_path),
    }}}


class TestRSUSync:
    def test_returns_dataframes(self, rsu_config):
        result = sync_rsu(rsu_config)
        assert "holdings" in result and "transactions" in result
        assert not result["holdings"].empty
        assert len(result["holdings"]) == 1
        assert result["holdings"].iloc[0]["quantity"] == 48
        assert len(result["transactions"]) == 1

    def test_disabled(self):
        result = sync_rsu({"source_registry": {"rsu": {"enabled": False}}})
        assert result["holdings"].empty
        assert result["transactions"].empty

    def test_file_not_found(self):
        result = sync_rsu({"source_registry": {"rsu": {
            "enabled": True,
            "data_dir": "/nonexistent",
        }}})
        assert result["holdings"].empty
        assert result["transactions"].empty

    def test_uses_top_level_finance_dir_when_data_dir_null(self, rsu_config):
        finance_dir = rsu_config["source_registry"]["rsu"]["data_dir"]
        rsu_config["source_registry"]["rsu"]["data_dir"] = None
        rsu_config["finance_dir"] = finance_dir

        result = sync_rsu(rsu_config)

        assert len(result["holdings"]) == 1
