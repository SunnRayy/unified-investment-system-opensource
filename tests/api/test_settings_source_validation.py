from pathlib import Path

import pytest

from src.api.routes import settings as settings_route
from src.validation.source_format_validator import FormatValidationResult


@pytest.mark.parametrize(
    ("reader", "validator_name"),
    [
        ("schwab", "validate_schwab_format"),
        ("cn_fund", "validate_cn_fund_format"),
        ("gold", "validate_gold_format"),
        ("insurance", "validate_insurance_format"),
        ("rsu", "validate_rsu_format"),
        ("financial_summary", "validate_financial_summary_format"),
    ],
)
def test_validate_file_at_path_dispatches_to_reader_validator(
    monkeypatch,
    tmp_path: Path,
    reader: str,
    validator_name: str,
):
    path = tmp_path / ("test.csv" if reader == "schwab" else "test.xlsx")
    path.write_text("placeholder")
    called = {"path": None}

    def fake_validator(candidate: Path):
        called["path"] = candidate
        return FormatValidationResult(
            is_valid=True,
            warnings=["warning from validator"],
            file_type=f"type-{reader}",
        )

    monkeypatch.setattr(f"src.validation.source_format_validator.{validator_name}", fake_validator)

    is_valid, warnings, file_type = settings_route._validate_file_at_path(reader, path)

    assert called["path"] == path
    assert is_valid is True
    assert warnings == ["warning from validator"]
    assert file_type == f"type-{reader}"


def test_validate_file_at_path_returns_validation_error_when_validator_raises(
    monkeypatch,
    tmp_path: Path,
):
    path = tmp_path / "bad.xlsx"
    path.write_text("placeholder")

    def fail(_path: Path):
        raise StopIteration("no header row")

    monkeypatch.setattr("src.validation.source_format_validator.validate_cn_fund_format", fail)

    is_valid, warnings, file_type = settings_route._validate_file_at_path("cn_fund", path)

    assert is_valid is False
    assert warnings == ["Validation error: no header row"]
    assert file_type is None


def test_validate_file_at_path_unknown_reader_falls_back_to_extension_check(tmp_path: Path):
    excel = tmp_path / "test.xlsx"
    excel.write_text("placeholder")
    assert settings_route._validate_file_at_path("unknown_reader", excel) == (True, [], None)

    bad = tmp_path / "test.txt"
    bad.write_text("placeholder")
    is_valid, warnings, file_type = settings_route._validate_file_at_path("unknown_reader", bad)
    assert is_valid is False
    assert warnings == ["Unexpected file extension: '.txt'"]
    assert file_type is None


def test_validator_map_contains_all_supported_source_validators():
    assert settings_route._VALIDATOR_MAP == {
        "schwab": "validate_schwab_format",
        "cn_fund": "validate_cn_fund_format",
        "gold": "validate_gold_format",
        "insurance": "validate_insurance_format",
        "rsu": "validate_rsu_format",
        "financial_summary": "validate_financial_summary_format",
        # Workstream C1: IBKR Flex Query reader added
        "ibkr": "validate_ibkr_format",
    }
