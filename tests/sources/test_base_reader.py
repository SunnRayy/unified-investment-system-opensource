"""Tests for base source reader interface.

TDD: These tests are written FIRST, before the implementation.
Run: pytest tests/sources/test_base_reader.py -v
Expected: All tests should FAIL initially (RED phase).
"""
import pytest

pytestmark = pytest.mark.pipeline

from datetime import datetime
import pandas as pd


class TestSourceData:
    """Tests for SourceData dataclass."""

    def test_source_data_fields(self):
        """SourceData dataclass holds expected fields."""
        from src.sources.base import SourceData

        holdings = pd.DataFrame({'asset_id': ['US_STK_AAPL'], 'quantity': [10]})
        transactions = pd.DataFrame({'date': ['2026-01-01'], 'type': ['buy']})
        metadata = {'cash_balance': 1000.0}

        data = SourceData(
            source_name="test_source",
            read_timestamp=datetime(2026, 2, 8, 12, 0, 0),
            holdings=holdings,
            transactions=transactions,
            metadata=metadata
        )

        assert data.source_name == "test_source"
        assert data.read_timestamp == datetime(2026, 2, 8, 12, 0, 0)
        assert len(data.holdings) == 1
        assert len(data.transactions) == 1
        assert data.metadata['cash_balance'] == 1000.0

    def test_source_data_empty_transactions(self):
        """SourceData can have empty transactions DataFrame."""
        from src.sources.base import SourceData

        data = SourceData(
            source_name="positions_only",
            read_timestamp=datetime.now(),
            holdings=pd.DataFrame({'asset_id': ['A']}),
            transactions=pd.DataFrame(),
            metadata={}
        )

        assert len(data.transactions) == 0

    def test_source_data_default_metadata(self):
        """SourceData metadata defaults to empty dict."""
        from src.sources.base import SourceData

        data = SourceData(
            source_name="test",
            read_timestamp=datetime.now(),
            holdings=pd.DataFrame(),
            transactions=pd.DataFrame()
        )

        assert data.metadata == {}


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_validation_result_fields(self):
        """ValidationResult has is_valid, warnings, and stats fields."""
        from src.sources.base import ValidationResult

        result = ValidationResult(
            is_valid=True,
            warnings=["Minor issue found"],
            stats={'row_count': 10, 'date_range': '2025-2026'}
        )

        assert result.is_valid is True
        assert result.warnings == ["Minor issue found"]
        assert result.stats['row_count'] == 10

    def test_validation_result_defaults(self):
        """ValidationResult has sensible defaults for warnings and stats."""
        from src.sources.base import ValidationResult

        result = ValidationResult(is_valid=True)

        assert result.is_valid is True
        assert result.warnings == []
        assert result.stats == {}


class TestBaseSourceReader:
    """Tests for BaseSourceReader abstract class."""

    def test_base_reader_is_abstract(self):
        """BaseSourceReader cannot be instantiated directly."""
        from src.sources.base import BaseSourceReader

        with pytest.raises(TypeError) as exc_info:
            BaseSourceReader()

        assert "abstract" in str(exc_info.value).lower() or "instantiate" in str(exc_info.value).lower()

    def test_concrete_reader_must_implement_read(self):
        """Subclass missing read() cannot be instantiated."""
        from src.sources.base import BaseSourceReader, SourceData, ValidationResult

        class IncompleteReader(BaseSourceReader):
            def validate(self, data: SourceData) -> ValidationResult:
                return ValidationResult(is_valid=True)
            # Missing read() method

        with pytest.raises(TypeError):
            IncompleteReader()

    def test_concrete_reader_must_implement_validate(self):
        """Subclass missing validate() cannot be instantiated."""
        from src.sources.base import BaseSourceReader, SourceData
        from pathlib import Path

        class IncompleteReader(BaseSourceReader):
            def read(self, file_path: Path) -> SourceData:
                return SourceData(
                    source_name="test",
                    read_timestamp=datetime.now(),
                    holdings=pd.DataFrame(),
                    transactions=pd.DataFrame()
                )
            # Missing validate() method

        with pytest.raises(TypeError):
            IncompleteReader()

    def test_complete_reader_can_instantiate(self):
        """Subclass with both read() and validate() can be instantiated."""
        from src.sources.base import BaseSourceReader, SourceData, ValidationResult
        from pathlib import Path

        class CompleteReader(BaseSourceReader):
            def read(self, file_path: Path) -> SourceData:
                return SourceData(
                    source_name="complete",
                    read_timestamp=datetime.now(),
                    holdings=pd.DataFrame(),
                    transactions=pd.DataFrame()
                )

            def validate(self, data: SourceData) -> ValidationResult:
                return ValidationResult(is_valid=True)

        reader = CompleteReader()
        assert reader is not None
