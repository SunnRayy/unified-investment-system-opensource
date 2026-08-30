"""Tests for Gold sync module."""
import pytest

pytestmark = pytest.mark.pipeline

import openpyxl
from datetime import datetime
from src.sync.gold_sync import sync_gold

@pytest.fixture
def gold_config(tmp_path):
    wb = openpyxl.Workbook()
    ws_h = wb.active
    ws_h.title = "黄金持仓"
    ws_h.append(["资产类别", "标的名称", "持有数量", "单位", "平均成本价",
                  "单价", "当前市值", "未实现盈亏", "交易账户"])
    ws_h.append(["黄金", "纸黄金", 100.0, "克", 500, 600, 60000, 10000, "招行"])
    ws_t = wb.create_sheet("黄金交易记录")
    ws_t.append(["交易日期", "资产类别", "标的名称", "交易类型", "金额",
                  "数量", "价格", "手续费", "交易账户"])
    ws_t.append([datetime(2025, 1, 15), "黄金", "纸黄金", "买入", 5000, 10, 500, 0, "招行"])
    wb.save(tmp_path / "Gold_transactions.xlsx")
    return {"source_registry": {"gold": {
        "enabled": True, "file_patterns": {"workbook": "Gold_transactions.xlsx"},
        "data_dir": str(tmp_path)}}}

class TestGoldSync:
    def test_returns_dataframes(self, gold_config):
        result = sync_gold(gold_config)
        assert "holdings" in result and "transactions" in result
        assert len(result["holdings"]) == 1

    def test_disabled_returns_empty(self):
        result = sync_gold({"source_registry": {"gold": {"enabled": False}}})
        assert result["holdings"].empty

    def test_file_not_found_returns_empty(self):
        result = sync_gold({"source_registry": {"gold": {
            "enabled": True, "data_dir": "/nonexistent"}}})
        assert result["holdings"].empty

    def test_uses_top_level_finance_dir_when_data_dir_null(self, gold_config):
        finance_dir = gold_config["source_registry"]["gold"]["data_dir"]
        gold_config["source_registry"]["gold"]["data_dir"] = None
        gold_config["finance_dir"] = finance_dir

        result = sync_gold(gold_config)

        assert len(result["holdings"]) == 1
