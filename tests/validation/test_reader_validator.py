"""Tests for pre-insertion reader validation module."""

from datetime import datetime
import pandas as pd
import pytest

pytestmark = pytest.mark.pipeline



def test_asset_comparison_match():
    """An asset present in both reader and DB with matching quantity/value."""
    from src.validation.reader_validator import AssetComparison

    comp = AssetComparison(
        asset_id="US_STK_GOOGL",
        reader_id="US_STK_GOOGL",
        db_id="US_STK_GOOGL",
        reader_quantity=20.0,
        db_quantity=20.0,
        reader_value=6440.0,
        db_value=6440.0,
        status="match",
        notes="",
    )
    assert comp.status == "match"
    assert comp.quantity_diff == 0.0
    assert comp.value_diff == 0.0


def test_asset_comparison_mismatch():
    """An asset with different IDs between reader and DB."""
    from src.validation.reader_validator import AssetComparison

    comp = AssetComparison(
        asset_id="IEF",
        reader_id="US_ETF_IEF",
        db_id="US_STK_IEF",
        reader_quantity=172.0,
        db_quantity=172.0,
        reader_value=16525.76,
        db_value=16525.76,
        status="id_mismatch",
        notes="Reader uses US_ETF_ prefix, DB uses US_STK_",
    )
    assert comp.status == "id_mismatch"
    assert comp.reader_id != comp.db_id


def test_asset_comparison_reader_only():
    """An asset in reader output but not in DB."""
    from src.validation.reader_validator import AssetComparison

    comp = AssetComparison(
        asset_id="GOLD_PAPER_CMB",
        reader_id="GOLD_PAPER_CMB",
        db_id=None,
        reader_quantity=120.0,
        db_quantity=None,
        reader_value=91200.00,
        db_value=None,
        status="reader_only",
        notes="New per-account gold ID not in DB",
    )
    assert comp.status == "reader_only"
    assert comp.db_id is None


def test_reader_validation_result():
    """ReaderValidationResult aggregates comparisons for one reader."""
    from src.validation.reader_validator import AssetComparison, ReaderValidationResult

    result = ReaderValidationResult(
        reader_name="schwab",
        source_file="/path/to/file.csv",
        timestamp=datetime.now(),
        holdings_count=8,
        transactions_count=149,
        comparisons=[
            AssetComparison(
                "GOOGL",
                "US_STK_GOOGL",
                "US_STK_GOOGL",
                20.0,
                20.0,
                6440.0,
                6440.0,
                "match",
                "",
            ),
            AssetComparison(
                "IEF",
                "US_ETF_IEF",
                "US_STK_IEF",
                172.0,
                172.0,
                16525.0,
                16525.0,
                "id_mismatch",
                "ETF vs STK",
            ),
        ],
        schema_issues=[],
        warnings=[],
    )
    assert result.match_count == 1
    assert result.mismatch_count == 1
    assert result.total_comparisons == 2


def test_full_validation_report():
    """FullValidationReport aggregates all reader results."""
    from src.validation.reader_validator import FullValidationReport

    report = FullValidationReport(
        timestamp=datetime.now(),
        reader_results={},
        id_conflicts=[],
        schema_gaps=[],
        overall_status="pass",
    )
    assert report.overall_status == "pass"


def test_full_validation_report_to_json():
    """Report serializes to JSON."""
    from src.validation.reader_validator import FullValidationReport

    report = FullValidationReport(
        timestamp=datetime.now(),
        reader_results={},
        id_conflicts=[],
        schema_gaps=[],
        overall_status="pass",
    )
    json_str = report.to_json()
    assert '"overall_status": "pass"' in json_str


def test_full_validation_report_to_markdown():
    """Report renders as human-readable markdown."""
    from src.validation.reader_validator import FullValidationReport

    report = FullValidationReport(
        timestamp=datetime.now(),
        reader_results={},
        id_conflicts=[],
        schema_gaps=[],
        overall_status="pass",
    )
    md = report.to_markdown()
    assert "# Pre-Insertion Data Validation Report" in md
    assert "pass" in md.lower()


def test_extract_symbol():
    """Canonical IDs should be normalized to raw symbols."""
    from src.validation.reader_validator import extract_symbol

    assert extract_symbol("US_STK_GOOGL") == "GOOGL"
    assert extract_symbol("US_ETF_IEF") == "IEF"
    assert extract_symbol("CN_FUND_900001") == "900001"
    assert extract_symbol("RSU_RSU_AMZN") == "AMZN"
    assert extract_symbol("RSU_AMZN") == "AMZN"


def test_build_symbol_map_detects_conflicts():
    """Reader/DB IDs with same symbol but different canonical IDs are mapped."""
    from src.validation.reader_validator import build_symbol_map

    symbol_map = build_symbol_map(
        reader_ids=["US_ETF_IEF", "US_STK_GOOGL"],
        db_ids=["US_STK_IEF", "US_STK_GOOGL"],
    )
    assert symbol_map["IEF"] == {"reader": "US_ETF_IEF", "db": "US_STK_IEF"}
    assert "GOOGL" not in symbol_map


def test_compare_holdings_exact_match():
    """Reader and DB have same assets with same quantities."""
    from src.validation.reader_validator import compare_holdings

    reader_df = pd.DataFrame(
        [
            {"asset_id": "US_STK_GOOGL", "quantity": 20.0, "market_value": 6440.0},
            {"asset_id": "US_STK_MSFT", "quantity": 5.0, "market_value": 1970.0},
        ]
    )
    db_df = pd.DataFrame(
        [
            {"asset_id": "US_STK_GOOGL", "quantity": 20.0, "market_value": 6440.0},
            {"asset_id": "US_STK_MSFT", "quantity": 5.0, "market_value": 1970.0},
        ]
    )
    comparisons = compare_holdings(reader_df, db_df, id_column="asset_id")
    assert len(comparisons) == 2
    assert all(item.status == "match" for item in comparisons)


def test_compare_holdings_id_mismatch():
    """Reader US_ETF_IEF vs DB US_STK_IEF should be flagged as id_mismatch."""
    from src.validation.reader_validator import compare_holdings

    reader_df = pd.DataFrame(
        [{"asset_id": "US_ETF_IEF", "quantity": 172.0, "market_value": 16525.0}]
    )
    db_df = pd.DataFrame(
        [{"asset_id": "US_STK_IEF", "quantity": 172.0, "market_value": 16525.0}]
    )
    symbol_map = {"IEF": {"reader": "US_ETF_IEF", "db": "US_STK_IEF"}}

    comparisons = compare_holdings(reader_df, db_df, id_column="asset_id", symbol_map=symbol_map)
    assert len(comparisons) == 1
    assert comparisons[0].status == "id_mismatch"
    assert comparisons[0].reader_id == "US_ETF_IEF"
    assert comparisons[0].db_id == "US_STK_IEF"


def test_compare_holdings_reader_only():
    """Asset in reader but not in DB should be reader_only."""
    from src.validation.reader_validator import compare_holdings

    reader_df = pd.DataFrame(
        [{"asset_id": "GOLD_PAPER_CMB", "quantity": 120.0, "market_value": 91200.0}]
    )
    db_df = pd.DataFrame(columns=["asset_id", "quantity", "market_value"])

    comparisons = compare_holdings(reader_df, db_df, id_column="asset_id")
    assert len(comparisons) == 1
    assert comparisons[0].status == "reader_only"


def test_compare_holdings_db_only():
    """Asset in DB but not in reader output should be db_only."""
    from src.validation.reader_validator import compare_holdings

    reader_df = pd.DataFrame(columns=["asset_id", "quantity", "market_value"])
    db_df = pd.DataFrame(
        [{"asset_id": "CN_FUND_900001", "quantity": 93857.0, "market_value": 259233.0}]
    )

    comparisons = compare_holdings(reader_df, db_df, id_column="asset_id")
    assert len(comparisons) == 1
    assert comparisons[0].status == "db_only"


def test_compare_holdings_value_mismatch():
    """Same asset ID but different quantity/value should be value_mismatch."""
    from src.validation.reader_validator import compare_holdings

    reader_df = pd.DataFrame(
        [{"asset_id": "US_STK_FBTC", "quantity": 130.0, "market_value": 50043.0}]
    )
    db_df = pd.DataFrame(
        [{"asset_id": "US_STK_FBTC", "quantity": 125.0, "market_value": 48000.0}]
    )

    comparisons = compare_holdings(reader_df, db_df, id_column="asset_id")
    assert len(comparisons) == 1
    assert comparisons[0].status == "value_mismatch"
    assert comparisons[0].quantity_diff == 5.0


def test_compare_transaction_counts():
    """Transaction counts should match and be grouped case-insensitively by type."""
    from src.validation.reader_validator import compare_transaction_counts

    reader_df = pd.DataFrame(
        [
            {"asset_id": "US_STK_GOOGL", "transaction_type": "buy"},
            {"asset_id": "US_STK_GOOGL", "transaction_type": "buy"},
            {"asset_id": "US_STK_GOOGL", "transaction_type": "dividend"},
        ]
    )
    db_df = pd.DataFrame(
        [
            {"asset_id": "US_STK_GOOGL", "transaction_type": "Buy"},
            {"asset_id": "US_STK_GOOGL", "transaction_type": "Buy"},
            {"asset_id": "US_STK_GOOGL", "transaction_type": "dividend"},
        ]
    )

    result = compare_transaction_counts(reader_df, db_df)
    assert result["reader_total"] == 3
    assert result["db_total"] == 3
    assert result["reader_by_type"]["buy"] == 2
    assert result["db_by_type"]["buy"] == 2


def test_validate_schema_mapping_holdings_schwab():
    """Schwab holdings columns should surface rename and extra-column gaps."""
    from src.validation.reader_validator import validate_schema_mapping

    transformer_columns = [
        "asset_id",
        "quantity",
        "market_price_unit",
        "market_value",
        "cost_basis",
        "gain_dollar",
        "gain_percent",
        "snapshot_date",
        "source_system",
    ]
    db_columns = [
        "asset_id",
        "snapshot_date",
        "asset_name",
        "asset_type",
        "quantity",
        "unit",
        "cost_price_unit",
        "market_price_unit",
        "market_value",
        "currency",
        "account",
        "source_system",
    ]

    gaps = validate_schema_mapping("schwab", "holdings", transformer_columns, db_columns)
    rename_gaps = [item for item in gaps if item.gap_type == "rename_needed"]
    assert any(item.transformer_column == "cost_basis" for item in rename_gaps)

    extra_gaps = [item for item in gaps if item.gap_type == "extra_in_transformer"]
    assert any(item.transformer_column == "gain_dollar" for item in extra_gaps)


def test_validate_schema_mapping_transactions_schwab():
    """Schwab transaction columns should surface required rename mappings."""
    from src.validation.reader_validator import validate_schema_mapping

    transformer_columns = [
        "asset_id",
        "transaction_date",
        "transaction_type",
        "quantity",
        "price",
        "amount",
        "fees",
        "description",
        "source_system",
    ]
    db_columns = [
        "asset_id",
        "transaction_date",
        "asset_name",
        "transaction_type",
        "quantity",
        "price_unit",
        "amount_gross",
        "amount_net",
        "commission_fee",
        "currency",
        "account",
        "memo",
        "source_system",
    ]

    gaps = validate_schema_mapping("schwab", "transactions", transformer_columns, db_columns)
    rename_gaps = [item for item in gaps if item.gap_type == "rename_needed"]
    assert any(
        item.transformer_column == "price" and item.db_column == "price_unit"
        for item in rename_gaps
    )
    assert any(item.transformer_column == "amount" for item in rename_gaps)


def test_validate_schema_mapping_no_gaps():
    """Perfect schema alignment should return no gaps."""
    from src.validation.reader_validator import validate_schema_mapping

    columns = ["asset_id", "quantity", "source_system"]
    gaps = validate_schema_mapping("test", "holdings", columns, columns)
    assert len(gaps) == 0


def test_validate_schema_mapping_missing_in_transformer():
    """DB columns absent in transformer output should be flagged."""
    from src.validation.reader_validator import validate_schema_mapping

    transformer_columns = ["asset_id", "quantity", "source_system"]
    db_columns = ["asset_id", "quantity", "market_value", "source_system"]

    gaps = validate_schema_mapping("test", "holdings", transformer_columns, db_columns)
    missing = [item for item in gaps if item.gap_type == "missing_in_transformer"]
    assert any(item.db_column == "market_value" for item in missing)


def test_known_id_conflicts_catalog():
    """Known ID conflicts should include Schwab, Gold, Insurance, and RSU cases."""
    from src.validation.reader_validator import KNOWN_ID_CONFLICTS

    schwab_conflicts = [item for item in KNOWN_ID_CONFLICTS if item["reader"] == "schwab"]
    assert any(item["reader_id"].startswith("US_ETF_") for item in schwab_conflicts)

    gold_conflicts = [item for item in KNOWN_ID_CONFLICTS if item["reader"] == "gold"]
    assert len(gold_conflicts) >= 1

    insurance_conflicts = [item for item in KNOWN_ID_CONFLICTS if item["reader"] == "insurance"]
    assert len(insurance_conflicts) >= 1

    rsu_conflicts = [item for item in KNOWN_ID_CONFLICTS if item["reader"] == "rsu"]
    assert len(rsu_conflicts) >= 1


def test_annotate_known_conflicts():
    """Known conflicts should be annotated with recommended resolutions."""
    from src.validation.reader_validator import IDConflict, annotate_known_conflicts

    conflicts = [
        IDConflict("IEF", "US_ETF_IEF", "US_STK_IEF", "schwab", "needs_decision", ""),
        IDConflict("UNKNOWN_ASSET", "NEW_ID", "OLD_ID", "schwab", "needs_decision", ""),
    ]

    annotated = annotate_known_conflicts(conflicts)
    ief = [item for item in annotated if item.asset_symbol == "IEF"][0]
    unknown = [item for item in annotated if item.asset_symbol == "UNKNOWN_ASSET"][0]

    assert ief.resolution != "needs_decision"
    assert unknown.resolution == "needs_decision"


def test_annotate_known_conflicts_us_fund_variant():
    """US_FUND_ Schwab IDs should map to same resolution strategy as US_ETF_."""
    from src.validation.reader_validator import IDConflict, annotate_known_conflicts

    conflicts = [IDConflict("IEF", "US_FUND_IEF", "US_STK_IEF", "schwab", "needs_decision", "")]
    annotated = annotate_known_conflicts(conflicts)
    assert annotated[0].resolution == "use_db"


def test_compare_gold_multi_to_single():
    """Gold reader per-account holdings should compare to combined DB gold position."""
    from src.validation.reader_validator import compare_gold_holdings

    reader_df = pd.DataFrame(
        [
            {"asset_id": "GOLD_PAPER_CMB", "quantity": 120.0000, "market_value": 91200.00},
            {"asset_id": "GOLD_PAPER_ICBC", "quantity": 40.0000, "market_value": 30400.00},
        ]
    )
    db_df = pd.DataFrame(
        [{"asset_id": "ALTS_Paper_Gold", "quantity": 160.0000, "market_value": 121600.00}]
    )

    comparison = compare_gold_holdings(reader_df, db_df)
    assert comparison.status in ("match", "value_mismatch")
    assert comparison.reader_quantity == pytest.approx(160.0000, abs=0.01)
    assert comparison.db_quantity == pytest.approx(160.0000, abs=0.01)
    assert "per-account" in comparison.notes.lower() or "multi" in comparison.notes.lower()
