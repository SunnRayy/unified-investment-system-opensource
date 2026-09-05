"""Tests for Financial Summary sync."""
import pytest

pytestmark = pytest.mark.pipeline

import openpyxl
from datetime import datetime
from src.sync.financial_summary_sync import sync_financial_summary

@pytest.fixture
def fs_config(tmp_path):
    wb = openpyxl.Workbook()
    ws_bs = wb.active
    ws_bs.title = "资产负债"
    ws_bs.append(["", "", ""])
    ws_bs.append(["", "", ""])
    ws_bs.append(["", "", ""])
    ws_bs.append(["Date", "Total Assets", "Total Liabilities", "Net Worth"])
    ws_bs.append([datetime(2025, 1, 1), 100000, 20000, 80000])
    
    ws_ie = wb.create_sheet("月度收支")
    ws_ie.append(["", "", ""])
    ws_ie.append(["", "", ""])
    ws_ie.append(["", "", ""])
    ws_ie.append(["Month", "Income", "Expense", "Savings"])
    ws_ie.append([datetime(2025, 1, 1), 5000, 2000, 3000])
    
    wb.save(tmp_path / "Financial Summary_new.xlsx")
    return {"source_registry": {"financial_summary": {
        "enabled": True, "file_patterns": {"workbook": "Financial Summary_new.xlsx"},
        "data_dir": str(tmp_path)}}}

class TestFinancialSummarySync:
    def test_sync_returns_dfs(self, fs_config):
        result = sync_financial_summary(fs_config)
        assert len(result["holdings"]) == 1
        assert len(result["transactions"]) == 1
        assert "Total Assets" in result["holdings"].columns

    def test_disabled(self):
        result = sync_financial_summary({"source_registry": {"financial_summary": {"enabled": False}}})
        assert result["holdings"].empty

    def test_file_not_found(self):
         result = sync_financial_summary({"source_registry": {"financial_summary": {
            "enabled": True, "data_dir": "/nonexistent"}}})
         assert result["holdings"].empty

    def test_uses_top_level_finance_dir_when_data_dir_null(self, fs_config):
        finance_dir = fs_config["source_registry"]["financial_summary"]["data_dir"]
        fs_config["source_registry"]["financial_summary"]["data_dir"] = None
        fs_config["finance_dir"] = finance_dir

        result = sync_financial_summary(fs_config)

        assert len(result["holdings"]) == 1
