import sys
import types
from pathlib import Path
from typing import Optional

import pytest

from src.validation import source_format_validator as sfv


class FakeWorksheet:
    def __init__(self, rows=None, err: Optional[Exception] = None):
        self.rows = [] if rows is None else rows
        self.err = err

    def iter_rows(self, **_kwargs):
        if self.err is not None:
            raise self.err
        return iter(self.rows)


class FakeWorkbook:
    def __init__(self, sheets: dict[str, FakeWorksheet]):
        self._sheets = sheets
        self.sheetnames = list(sheets.keys())
        self.closed = False

    def __getitem__(self, item: str) -> FakeWorksheet:
        return self._sheets[item]

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    ("validator", "file_name", "workbook", "expected_exception"),
    [
        (
            sfv.validate_cn_fund_format,
            "cn_fund.xlsx",
            FakeWorkbook(
                {
                    "基金持仓汇总": FakeWorksheet(rows=[]),
                    "基金交易记录": FakeWorksheet(rows=[[object()]]),
                }
            ),
            StopIteration,
        ),
        (
            sfv.validate_gold_format,
            "gold.xlsx",
            FakeWorkbook(
                {
                    "黄金持仓": FakeWorksheet(err=RuntimeError("holdings parse failed")),
                    "黄金交易记录": FakeWorksheet(rows=[[object()]]),
                }
            ),
            RuntimeError,
        ),
        (
            sfv.validate_insurance_format,
            "insurance.xlsx",
            FakeWorkbook(
                {
                    "保险汇总": FakeWorksheet(err=RuntimeError("summary parse failed")),
                    "保费记录": FakeWorksheet(rows=[[object()]]),
                }
            ),
            RuntimeError,
        ),
        (
            sfv.validate_rsu_format,
            "rsu.xlsx",
            FakeWorkbook({"Transactions": FakeWorksheet(rows=[])}),
            StopIteration,
        ),
        (
            sfv.validate_financial_summary_format,
            "financial_summary.xlsx",
            FakeWorkbook(
                {
                    "资产负债": FakeWorksheet(rows=[]),
                    "月度收支": FakeWorksheet(rows=[[object()]]),
                }
            ),
            StopIteration,
        ),
    ],
)
def test_excel_validators_close_workbook_when_validation_raises(
    monkeypatch,
    tmp_path: Path,
    validator,
    file_name: str,
    workbook: FakeWorkbook,
    expected_exception: type[Exception],
):
    file_path = tmp_path / file_name
    file_path.write_text("placeholder")

    fake_openpyxl = types.SimpleNamespace(load_workbook=lambda *_args, **_kwargs: workbook)
    monkeypatch.setitem(sys.modules, "openpyxl", fake_openpyxl)

    with pytest.raises(expected_exception):
        validator(file_path)

    assert workbook.closed is True
