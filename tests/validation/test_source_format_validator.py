"""Tests for source format validator.

TDD: These tests are written FIRST, before the implementation.
Run: pytest tests/validation/test_source_format_validator.py -v
"""
import pytest

pytestmark = pytest.mark.pipeline



# ============================================================================
# FIXTURES (reuse from schwab_reader tests)
# ============================================================================

@pytest.fixture
def valid_positions_csv(tmp_path):
    """Valid Schwab positions CSV."""
    csv_content = '''"Positions for account Individual ...XXX342 as of 10:11 PM ET, 02/06/2026"

"Symbol","Description","Qty (Quantity)","Price","Price Chng $","Price Chng %","Mkt Val (Market Value)","Day Chng $","Day Chng %","Cost Basis","Gain $ (Gain/Loss $)","Gain % (Gain/Loss %)","Reinvest?","Reinvest Capital Gains?","Security Type"
"QQQ","INVESCO QQQ TRUST SERIES 1","10","$529.78","$3.50","0.66%","$5,297.80","$35.00","0.66%","$4,500.00","$797.80","17.73%","No","N/A","ETF"
"Cash & Cash Investments","--","--","--","--","--","$6,440.00","$0.00","0%","--","--","--","--","--","--"
"Account Total","--","--","--","--","--","$12,714.05","$29.00","0.23%","--","--","--","--","--","--"
'''
    csv_path = tmp_path / "Individual-Positions-2026-02-06.csv"
    csv_path.write_text(csv_content)
    return csv_path


@pytest.fixture
def valid_transactions_csv(tmp_path):
    """Valid Schwab transactions CSV."""
    csv_content = '''"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"
"02/05/2026","Buy","QQQ","INVESCO QQQ TRUST SERIES 1","2","$529.78","","$-1059.56"
"02/04/2026","Sell","AAPL","APPLE INC","5","$195.25","$0.02","$976.23"
'''
    csv_path = tmp_path / "Individual_XXX342_Transactions_20260206.csv"
    csv_path.write_text(csv_content)
    return csv_path


# ============================================================================
# POSITIONS FORMAT TESTS
# ============================================================================

class TestValidatePositionsFormat:
    """Tests for positions CSV format validation."""

    def test_validate_schwab_positions_valid(self, valid_positions_csv):
        """Valid positions CSV passes validation."""
        from src.validation.source_format_validator import validate_schwab_format

        result = validate_schwab_format(valid_positions_csv)

        assert result.is_valid is True
        assert len(result.warnings) == 0

    def test_validate_schwab_positions_missing_columns(self, tmp_path):
        """CSV with missing required columns fails."""
        csv_path = tmp_path / "Individual-Positions-bad.csv"
        csv_path.write_text('"Header"\n\n"Symbol","Price"\n"AAPL","$100"\n')

        from src.validation.source_format_validator import validate_schwab_format

        result = validate_schwab_format(csv_path)

        assert result.is_valid is False
        assert any("missing" in w.lower() or "column" in w.lower() for w in result.warnings)

    def test_validate_schwab_positions_empty(self, tmp_path):
        """Empty file fails validation."""
        csv_path = tmp_path / "Individual-Positions-empty.csv"
        csv_path.write_text("")

        from src.validation.source_format_validator import validate_schwab_format

        result = validate_schwab_format(csv_path)

        assert result.is_valid is False
        assert any("empty" in w.lower() for w in result.warnings)

    def test_validate_schwab_positions_accepts_asset_type_alias(self, tmp_path):
        """Asset Type should be treated as Security Type alias."""
        csv_path = tmp_path / "Individual-Positions-2026-03-13.csv"
        csv_path.write_text(
            '''"Positions for account Individual ...342 as of 10:54 PM ET, 2026/03/13"

"Symbol","Description","Qty (Quantity)","Price","Price Chng $","Price Chng %","Mkt Val (Market Value)","Day Chng $","Day Chng %","Cost Basis","Gain $ (Gain/Loss $)","Gain % (Gain/Loss %)","Reinvest?","Reinvest Capital Gains?","Asset Type"
"GOOGL","ALPHABET INC","20","$301.74","$-0.53","-0.18%","$6,034.88","$-10.72","-0.18%","$6,017.15","$17.73","0.29%","No","N/A","Equity"
'''
        )

        from src.validation.source_format_validator import validate_schwab_format

        result = validate_schwab_format(csv_path)

        assert result.is_valid is True


# ============================================================================
# TRANSACTIONS FORMAT TESTS
# ============================================================================

class TestValidateTransactionsFormat:
    """Tests for transactions CSV format validation."""

    def test_validate_schwab_transactions_valid(self, valid_transactions_csv):
        """Valid transactions CSV passes validation."""
        from src.validation.source_format_validator import validate_schwab_format

        result = validate_schwab_format(valid_transactions_csv)

        assert result.is_valid is True
        assert len(result.warnings) == 0

    def test_validate_schwab_transactions_bad_dates(self, tmp_path):
        """Transactions with wrong date format warns."""
        csv_path = tmp_path / "Individual_XXX_Transactions_bad.csv"
        # Wrong format: YYYY-MM-DD instead of MM/DD/YYYY
        csv_path.write_text('''"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"
"2026-02-05","Buy","QQQ","TEST","2","$100","","$-200"
''')

        from src.validation.source_format_validator import validate_schwab_format

        result = validate_schwab_format(csv_path)

        # Should still be valid but with warning about date format
        assert any("date" in w.lower() for w in result.warnings)
