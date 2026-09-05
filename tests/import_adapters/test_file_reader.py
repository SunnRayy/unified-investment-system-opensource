from pathlib import Path

import pandas as pd

from src.import_adapters.file_reader import clean_amount, parse_date, read_tabular_file


def test_csv_delimiter_detection(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text("asset_id;quantity\nA;1\n", encoding="utf-8")
    result = read_tabular_file(p)
    assert result.headers == ["asset_id", "quantity"]


def test_excel_first_sheet(tmp_path: Path):
    p = tmp_path / "a.xlsx"
    pd.DataFrame([{"asset_id": "A", "quantity": 1}]).to_excel(p, index=False)
    result = read_tabular_file(p)
    assert result.total_rows == 1


def test_clean_amount_variants():
    assert clean_amount("$1,234.50") == 1234.5
    assert clean_amount("¥1,234.50") == 1234.5
    assert clean_amount("(1,234.50)") == -1234.5
    assert clean_amount("") is None


def test_parse_date_variants():
    assert str(parse_date("2026-01-02")) == "2026-01-02"
    assert str(parse_date("2026/01/02")) == "2026-01-02"


def test_full_rows_returns_all_data(tmp_path: Path):
    """full_rows should contain all rows, not just the preview (Fix #1)."""
    p = tmp_path / "big.csv"
    rows = [{"asset_id": f"ASSET_{i}", "qty": i} for i in range(10)]
    pd.DataFrame(rows).to_csv(p, index=False)
    result = read_tabular_file(p)
    assert result.total_rows == 10
    assert len(result.preview_rows) == 5  # default preview limit
    assert result.full_rows is not None
    assert len(result.full_rows) == 10


def test_full_rows_with_custom_nrows(tmp_path: Path):
    """When nrows is specified, preview_rows should respect it but full_rows has all."""
    p = tmp_path / "small.csv"
    rows = [{"asset_id": f"A_{i}", "qty": i} for i in range(7)]
    pd.DataFrame(rows).to_csv(p, index=False)
    result = read_tabular_file(p, nrows=3)
    assert len(result.preview_rows) == 3
    assert result.full_rows is not None
    assert len(result.full_rows) == 7
