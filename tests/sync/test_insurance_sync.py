"""Tests for Insurance sync module."""
import pytest

pytestmark = pytest.mark.pipeline

import openpyxl
from datetime import datetime
from src.sync.insurance_sync import sync_insurance

@pytest.fixture
def insurance_config(tmp_path):
    wb = openpyxl.Workbook()
    ws_s = wb.active
    ws_s.title = "保险汇总"
    ws_s.append(["产品名称", "保险公司", "产品类型", "开始日期", "保障期限",
                  "缴费期限", "年保费", "保额", "保障范围", "保单状态"])
    ws_s.append(["TestIns", "Co", "综合", datetime(2020, 1, 1), "30年",
                  datetime(2050, 1, 1), 5000, 500000, "重疾500000", "有效"])
    ws_p = wb.create_sheet("保费记录")
    ws_p.append(["日期", "TestIns"])
    ws_p.append([datetime(2020, 1, 1), 5000])
    wb.save(tmp_path / "Insurance_Portfolio.xlsx")
    return {"source_registry": {"insurance": {
        "enabled": True, "file_patterns": {"workbook": "Insurance_Portfolio.xlsx"},
        "data_dir": str(tmp_path)}}}

class TestInsuranceSync:
    def test_returns_dataframes(self, insurance_config):
        result = sync_insurance(insurance_config)
        assert len(result["holdings"]) == 1
        assert len(result["transactions"]) == 1

    def test_disabled(self):
        result = sync_insurance({"source_registry": {"insurance": {"enabled": False}}})
        assert result["holdings"].empty

    def test_file_not_found(self):
        result = sync_insurance({"source_registry": {"insurance": {
            "enabled": True, "data_dir": "/nonexistent"}}})
        assert result["holdings"].empty

    def test_uses_top_level_finance_dir_when_data_dir_null(self, insurance_config):
        finance_dir = insurance_config["source_registry"]["insurance"]["data_dir"]
        insurance_config["source_registry"]["insurance"]["data_dir"] = None
        insurance_config["finance_dir"] = finance_dir

        result = sync_insurance(insurance_config)

        assert len(result["holdings"]) == 1
