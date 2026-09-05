"""Tests for RSU format validator (Excel)."""
import pytest

pytestmark = pytest.mark.pipeline

import openpyxl
from src.validation.source_format_validator import validate_rsu_format


@pytest.fixture
def valid_rsu_workbook(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transactions"
    ws.append(["交易日期", "资产名称", "交易类型", "单位", "数量",
               "单位价格_USD", "总金额_USD", "手续费_USD", "备注"])
    path = tmp_path / "RSU_transactions.xlsx"
    wb.save(path)
    return path


class TestRSUFormatValidator:
    def test_valid(self, valid_rsu_workbook):
        result = validate_rsu_format(valid_rsu_workbook)
        assert result.is_valid is True
        assert result.file_type == "rsu"

    def test_missing_sheet(self, tmp_path):
        wb = openpyxl.Workbook()
        wb.active.title = "WrongSheet"
        path = tmp_path / "bad.xlsx"
        wb.save(path)
        result = validate_rsu_format(path)
        assert result.is_valid is False

    def test_file_not_found(self, tmp_path):
        result = validate_rsu_format(tmp_path / "nope.xlsx")
        assert result.is_valid is False
