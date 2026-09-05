import pytest
pytestmark = pytest.mark.pipeline

"""Tests for reader validation orchestration."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import subprocess
import sys


def test_validate_single_reader_schwab(tmp_path):
    """validate_single_reader should run Schwab sync and compare with DB holdings."""
    from src.validation.run_reader_validation import validate_single_reader

    csv_content = (
        '"Positions for account Individual ...XXX342 as of 10:11 PM ET, 02/06/2026"\n'
        "\n"
        '"Symbol","Description","Qty (Quantity)","Price","Price Chng $ (Price Change $)",'
        '"Price Chng % (Price Change %)","Mkt Val (Market Value)","Day Chng $ (Day Change $)",'
        '"Day Chng % (Day Change %)","Cost Basis","Gain $ (Gain/Loss $)","Gain % (Gain/Loss %)",'
        '"Reinvest?","Reinvest Capital Gains?","Security Type"\n'
        '"GOOGL","ALPHABET INC CLASS A","20","$322.00","$0.00","0%","$6,440.00","$0.00","0%",'
        '"$5,000.00","$1,440.00","28.80%","No","N/A","Common Stock"\n'
        '"Cash & Cash Investments","--","--","--","--","--","$100.00","$0.00","0%","--","--","--","--","--","--"\n'
        '"Account Total","--","--","--","--","--","$6,540.00","$0.00","0%","--","--","--","--","--","--"\n'
    )
    csv_file = tmp_path / "Individual-Positions-2026-02-06-020456.csv"
    csv_file.write_text(csv_content)

    db_holdings = pd.DataFrame(
        [{"asset_id": "US_STK_GOOGL", "quantity": 20.0, "market_value": 45080.0}]
    )

    mock_config = {
        "source_registry": {
            "schwab": {
                "enabled": True,
                "file_patterns": {"positions": "Individual-Positions-*.csv"},
                "data_dir": str(tmp_path),
            }
        }
    }

    result = validate_single_reader("schwab", mock_config, db_holdings=db_holdings)
    assert result is not None
    assert result.reader_name == "schwab"
    assert result.holdings_count >= 1
    assert any(comp.status == "match" for comp in result.comparisons)


def test_validate_single_reader_skips_disabled():
    """Disabled readers should return None."""
    from src.validation.run_reader_validation import validate_single_reader

    config = {"source_registry": {"schwab": {"enabled": False}}}
    assert validate_single_reader("schwab", config) is None


def test_collect_db_holdings_by_prefix():
    """DB query helper should return fetched DataFrame."""
    from src.validation.run_reader_validation import collect_db_holdings_by_prefix

    mock_conn = MagicMock()
    mock_conn.sql.return_value.fetchdf.return_value = pd.DataFrame(
        [{"asset_id": "US_STK_GOOGL", "quantity": 20.0, "market_value": 6440.0}]
    )

    df = collect_db_holdings_by_prefix(mock_conn, ["US_STK_%", "US_ETF_%", "CASH_USD"])
    assert len(df) >= 1
    assert "asset_id" in df.columns


def test_collect_db_holdings_by_prefix_empty_patterns():
    """Empty pattern list should return an empty holdings-shaped DataFrame."""
    from src.validation.run_reader_validation import collect_db_holdings_by_prefix

    mock_conn = MagicMock()
    df = collect_db_holdings_by_prefix(mock_conn, [])
    assert df.empty
    assert set(["asset_id", "quantity", "market_value"]).issubset(set(df.columns))


def test_detect_id_conflicts():
    """ID mismatch comparisons should be converted to IDConflict objects."""
    from src.validation.reader_validator import AssetComparison, ReaderValidationResult
    from src.validation.run_reader_validation import detect_id_conflicts

    results = {
        "schwab": ReaderValidationResult(
            reader_name="schwab",
            source_file="test.csv",
            timestamp=datetime.now(),
            holdings_count=1,
            transactions_count=0,
            comparisons=[
                AssetComparison(
                    "IEF",
                    "US_ETF_IEF",
                    "US_STK_IEF",
                    172.0,
                    172.0,
                    16525.0,
                    16525.0,
                    "id_mismatch",
                    "",
                )
            ],
        )
    }

    conflicts = detect_id_conflicts(results)
    assert len(conflicts) >= 1
    assert conflicts[0].reader_id == "US_ETF_IEF"
    assert conflicts[0].db_id == "US_STK_IEF"


def test_cli_validate_readers_flag():
    """main.py --help should expose validate-readers flag."""
    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "--validate-readers" in result.stdout


def test_validate_single_reader_financial_summary_without_asset_id():
    """Financial summary output without asset_id should not raise reader error."""
    from src.validation.run_reader_validation import validate_single_reader

    config = {
        "source_registry": {
            "financial_summary": {
                "enabled": True,
                "file_patterns": {"workbook": "Financial Summary_new.xlsx"},
                "data_dir": "/tmp/does-not-matter",
            }
        }
    }

    mocked_result = {
        "holdings": pd.DataFrame(
            [
                {"date": "2026-01-31", "net_worth": 1_000_000.0, "source_system": "Financial_Summary"}
            ]
        ),
        "transactions": pd.DataFrame(
            [
                {"date": "2026-01-31", "income": 50_000.0, "expense": 30_000.0, "source_system": "Financial_Summary"}
            ]
        ),
    }
    db_holdings = pd.DataFrame(
        [{"asset_id": "US_STK_GOOGL", "quantity": 20.0, "market_value": 6440.0}]
    )

    with patch("src.sync.financial_summary_sync.sync_financial_summary", return_value=mocked_result):
        result = validate_single_reader("financial_summary", config, db_holdings=db_holdings)

    assert result is not None
    assert result.reader_name == "financial_summary"
    assert result.holdings_count == 1
    assert result.transactions_count == 1
    assert not any("Reader error:" in warning for warning in result.warnings)
    assert any("Skipping holdings comparison" in warning for warning in result.warnings)


def test_validate_single_reader_financial_summary_with_asset_id_compares():
    """When asset_id exists, financial_summary should produce reader_only comparisons."""
    from src.validation.run_reader_validation import validate_single_reader

    config = {
        "source_registry": {
            "financial_summary": {
                "enabled": True,
                "file_patterns": {"workbook": "Financial Summary_new.xlsx"},
                "data_dir": "/tmp/does-not-matter",
            }
        }
    }
    mocked_result = {
        "holdings": pd.DataFrame(
            [
                {"asset_id": "BS_TOTAL_ASSETS", "net_worth": 1_000_000.0, "source_system": "Financial_Summary"}
            ]
        ),
        "transactions": pd.DataFrame(
            [
                {"asset_id": "IE_INCOME", "income": 50_000.0, "source_system": "Financial_Summary"}
            ]
        ),
    }
    db_holdings = pd.DataFrame(columns=["asset_id", "quantity", "market_value"])

    with patch("src.sync.financial_summary_sync.sync_financial_summary", return_value=mocked_result):
        result = validate_single_reader("financial_summary", config, db_holdings=db_holdings)

    assert result is not None
    assert any(comp.status == "reader_only" for comp in result.comparisons)
    assert not any("Skipping holdings comparison" in warning for warning in result.warnings)


def test_validate_single_reader_gold_combined_comparison():
    """Gold reader should include combined comparison and per-account reader-only rows."""
    from src.validation.run_reader_validation import validate_single_reader

    config = {
        "source_registry": {
            "gold": {
                "enabled": True,
                "file_patterns": {"workbook": "Gold_transactions.xlsx"},
                "data_dir": "/tmp/does-not-matter",
            }
        }
    }

    mocked_result = {
        "holdings": pd.DataFrame(
            [
                {"asset_id": "GOLD_PAPER_CMB", "quantity": 120.0000, "market_value": 91200.00},
                {"asset_id": "GOLD_PAPER_ICBC", "quantity": 40.0000, "market_value": 30400.00},
            ]
        ),
        "transactions": pd.DataFrame(
            [
                {"asset_id": "GOLD_PAPER_CMB", "transaction_type": "buy"},
                {"asset_id": "GOLD_PAPER_ICBC", "transaction_type": "buy"},
            ]
        ),
    }
    db_holdings = pd.DataFrame(
        [{"asset_id": "ALTS_Paper_Gold", "quantity": 160.0000, "market_value": 121600.00}]
    )

    with patch("src.sync.gold_sync.sync_gold", return_value=mocked_result):
        result = validate_single_reader("gold", config, db_holdings=db_holdings)

    assert result is not None
    assert any(comp.asset_id == "Gold (combined)" for comp in result.comparisons)
    assert any(comp.asset_id == "GOLD_PAPER_CMB" and comp.status == "reader_only" for comp in result.comparisons)
    assert any(comp.asset_id == "GOLD_PAPER_ICBC" and comp.status == "reader_only" for comp in result.comparisons)
