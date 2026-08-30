"""Base interface for all source file readers.

Each source reader reads raw data files (CSV, Excel) directly from brokers
or other sources and returns standardized SourceData that can be transformed
and synced to the Huinsight database.

Usage:
    from src.sources.base import BaseSourceReader, SourceData, ValidationResult
    
    class MyReader(BaseSourceReader):
        def read(self, file_path: Path) -> SourceData:
            # Parse the file and return SourceData
            ...
        
        def validate(self, data: SourceData) -> ValidationResult:
            # Check data quality, return warnings (never block)
            ...
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import pandas as pd


# ---------------------------------------------------------------------------
# Reader read-status vocabulary (task #16 — empty-source phantom snapshot)
#
# A reader that returns zero holdings rows is AMBIGUOUS: it may mean "the owner
# genuinely holds nothing here any more" or it may mean "the workbook was not
# found / the reader is off / the file was half-uploaded".  Zeroing a live
# portfolio because someone's OneDrive was still syncing is far more damaging
# than carrying a stale row for one more day, so the pipeline is allowed to act
# on emptiness ONLY when the source artifact was positively verified.
#
# ``READ_STATUS_OK`` is therefore the single affirmative value: the declared
# source artifact was located, its format validator passed (or none is
# declared), and the reader parsed it without raising.  EVERY other value —
# including the absence of the key — means "do not treat zero rows as a real
# zero".  Downstream code must test ``status == READ_STATUS_OK``, never
# ``status != <some failure>``, so that a new failure mode added later fails
# closed.
# ---------------------------------------------------------------------------
READ_STATUS_KEY = "read_status"

READ_STATUS_OK = "ok"                                # artifact found + validated + parsed
READ_STATUS_DISABLED = "disabled"                    # reader switched off in source_registry
READ_STATUS_NO_DATA_DIR = "no_data_dir"              # no data_dir and no finance_dir fallback
READ_STATUS_SOURCE_MISSING = "source_missing"        # workbook / data directory not on disk
READ_STATUS_VALIDATION_FAILED = "validation_failed"  # format validator returned is_valid=False
READ_STATUS_READ_ERROR = "read_error"                # reader raised (set by the caller)


def read_status_of(reader_result: Optional[dict]) -> str:
    """Return a reader result dict's read_status, defaulting to READ_STATUS_READ_ERROR.

    Fail-closed: a reader that predates this contract (or a mock that omits the
    key) is treated as unverified, never as an affirmative zero.
    """
    if not isinstance(reader_result, dict):
        return READ_STATUS_READ_ERROR
    value = reader_result.get(READ_STATUS_KEY)
    return value if isinstance(value, str) and value else READ_STATUS_READ_ERROR


@dataclass
class SourceData:
    """Standardized output from any source reader.
    
    Attributes:
        source_name: Identifier for the source (e.g., "schwab", "cn_fund")
        read_timestamp: When the data was read
        holdings: Current positions/holdings DataFrame
        transactions: Trade history DataFrame (can be empty)
        metadata: Reader-specific metadata (e.g., cash_balance, account_id)
    """
    source_name: str
    read_timestamp: datetime
    holdings: pd.DataFrame
    transactions: pd.DataFrame
    metadata: dict = field(default_factory=dict)


@dataclass
class ValidationResult:
    """Validation output - warnings only, never blocks.
    
    Per Decision 8: warn + log, don't block. Discrepancies are logged
    for review, not blocking the import pipeline.
    
    Attributes:
        is_valid: True if no critical issues (warnings are still valid)
        warnings: List of non-blocking warning messages
        stats: Statistics about the data (row counts, date ranges, etc.)
    """
    is_valid: bool
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


class BaseSourceReader(ABC):
    """Abstract base class for all source file readers.
    
    Design principles (from Architecture Section 1):
    1. Each reader is a self-contained module
    2. Readers don't write to DB - they return clean DataFrames
    3. Schema validation happens at the reader boundary
    4. Returns SourceData that the orchestrator transforms and persists
    """

    @abstractmethod
    def read(self, file_path: Path) -> SourceData:
        """Read and parse a raw source file.
        
        Args:
            file_path: Path to the source file (CSV, Excel, etc.)
            
        Returns:
            SourceData containing holdings, transactions, and metadata
        """
        pass

    @abstractmethod
    def validate(self, data: SourceData) -> ValidationResult:
        """Validate data quality before sync.
        
        This should check for:
        - Required columns present
        - Data types correct
        - No critical missing values
        - Reasonable date ranges
        
        Args:
            data: SourceData to validate
            
        Returns:
            ValidationResult with warnings (never blocks per Decision 8)
        """
        pass
