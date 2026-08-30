from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .models import FileReadResult


def _detect_delimiter(sample: str) -> str:
    for delim in [",", ";", "\t"]:
        if delim in sample:
            return delim
    return ","


def _read_csv(path: Path, nrows: int | None = None, header_row: int = 0) -> pd.DataFrame:
    text = path.read_text(encoding="utf-8", errors="replace")
    delim = _detect_delimiter("\n".join(text.splitlines()[:3]))
    skiprows = list(range(header_row)) if header_row > 0 else None
    return pd.read_csv(path, encoding="utf-8", sep=delim, nrows=nrows, header=0, skiprows=skiprows)


def _read_excel(path: Path, nrows: int | None = None, header_row: int = 0) -> pd.DataFrame:
    skiprows = list(range(header_row)) if header_row > 0 else None
    return pd.read_excel(path, sheet_name=0, nrows=nrows, header=0, skiprows=skiprows)


def read_tabular_file(path: Path, *, nrows: int | None = None, header_row: int = 0) -> FileReadResult:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        full_df = _read_csv(path, nrows=None, header_row=header_row)
    elif suffix in {".xls", ".xlsx"}:
        full_df = _read_excel(path, nrows=None, header_row=header_row)
    else:
        raise ValueError(f"Unsupported extension: {suffix}")

    preview_limit = nrows or 5
    preview_df = full_df.head(preview_limit)
    preview_rows = preview_df.fillna("").to_dict(orient="records")
    full_rows = full_df.fillna("").to_dict(orient="records")
    return FileReadResult(
        headers=[str(c) for c in full_df.columns.tolist()],
        preview_rows=preview_rows,
        total_rows=len(full_df.index),
        full_rows=full_rows,
    )


def clean_amount(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    if not raw:
        return None
    # Treat common placeholder strings as missing (e.g. Schwab uses "--")
    if raw in ("--", "-", "N/A", "n/a", "NA", "nan", "NaN"):
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = raw.replace("$", "").replace("¥", "").replace(",", "").strip("()")
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return -amount if negative else amount


def parse_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y.%m.%d"]:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
