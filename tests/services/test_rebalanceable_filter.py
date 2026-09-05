import duckdb
import pytest
from src.services.rebalanceable_filter import (
    adjust_balance_sheet_payload,
    fetch_non_rebalanceable_asset_ids,
)
from src.services.verification_config import BalanceSheetSection, VerificationConfig


class DuckDBAdapter:
    def __init__(self, conn):
        self.connection = conn

    def execute(self, query, params=None):
        if params is None:
            return self.connection.execute(query)
        return self.connection.execute(query, params)


@pytest.fixture
def filter_db(tmp_path):
    conn = duckdb.connect(str(tmp_path / "filter_test.duckdb"))
    conn.execute("""
        CREATE TABLE asset_registry (
            canonical_id VARCHAR,
            asset_class VARCHAR,
            is_rebalanceable BOOLEAN
        )
    """)
    conn.execute("""
        CREATE TABLE taxonomy_classes (
            id INTEGER,
            name VARCHAR,
            parent_id INTEGER,
            is_rebalanceable BOOLEAN
        )
    """)
    # Equity class (rebalanceable)
    conn.execute("INSERT INTO taxonomy_classes VALUES (1, 'Equity', NULL, TRUE)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (2, 'US Equity', 1, TRUE)")
    # Real Estate (NOT rebalanceable)
    conn.execute("INSERT INTO taxonomy_classes VALUES (3, 'Real Estate', NULL, FALSE)")
    conn.execute("INSERT INTO taxonomy_classes VALUES (4, 'Property', 3, FALSE)")
    # Insurance (NOT rebalanceable)
    conn.execute("INSERT INTO taxonomy_classes VALUES (5, 'Insurance', NULL, FALSE)")

    conn.execute("""
        INSERT INTO asset_registry VALUES
            ('EQ1', 'US Equity', TRUE),
            ('RE1', 'Property', TRUE),
            ('INS1', 'Insurance', TRUE)
    """)
    try:
        yield DuckDBAdapter(conn)
    finally:
        conn.close()


def test_returns_non_rebalanceable_ids(filter_db):
    result = fetch_non_rebalanceable_asset_ids(filter_db)
    assert "RE1" in result
    assert "INS1" in result
    assert "EQ1" not in result


def test_returns_empty_set_on_missing_tables(tmp_path):
    conn = duckdb.connect(str(tmp_path / "empty.duckdb"))
    db = DuckDBAdapter(conn)
    result = fetch_non_rebalanceable_asset_ids(db)
    assert result == set()
    conn.close()


class TestAdjustBalanceSheetPayload:
    """Program OSR WS-5b: non_balanceable_history_markers used to include a
    hardcoded real product name. Generic category words (property/insurance/
    房产/保险 etc.) stay as code defaults; a self-hoster's own product name is
    supplied via config/verification.yaml's balance_sheet section — the same
    cash_like_id_prefixes extension idiom freshness.py uses."""

    def test_generic_markers_match_with_default_config(self):
        payload = {"投资资产_长期保险_某产品": 10000.0, "股票基金_招行": 5000.0}
        result = adjust_balance_sheet_payload(payload)
        assert result == 10000.0

    def test_property_marker_matches(self):
        payload = {"固定资产_房产_某小区": 2_000_000.0, "现金": 1000.0}
        result = adjust_balance_sheet_payload(payload)
        assert result == 2_000_000.0

    def test_usd_and_gram_suffixes_are_skipped_entirely(self):
        payload = {"投资资产_黄金_纸黄金(克)": 500.0, "投资资产_股票基金_美股基金_USD": 300.0}
        result = adjust_balance_sheet_payload(payload)
        assert result == 0.0

    def test_config_extension_marker_matches_only_when_configured(self):
        # No generic marker substring (property/insurance/房产/保险/...) in this
        # key — an owner-specific product name with no category word in it.
        payload = {"投资资产_某专属产品名": 8000.0}

        assert adjust_balance_sheet_payload(payload) == 0.0

        cfg = VerificationConfig(
            balance_sheet=BalanceSheetSection(
                non_rebalanceable_history_markers=("某专属产品名",)
            )
        )
        assert adjust_balance_sheet_payload(payload, cfg=cfg) == 8000.0

    def test_config_extension_is_additive_not_replacing(self):
        """The config list UNIONs onto the code defaults — it must not
        suppress the generic markers when non-empty."""
        payload = {"投资资产_长期保险_某产品": 100.0, "某专属产品名": 200.0}
        cfg = VerificationConfig(
            balance_sheet=BalanceSheetSection(
                non_rebalanceable_history_markers=("某专属产品名",)
            )
        )
        result = adjust_balance_sheet_payload(payload, cfg=cfg)
        assert result == 300.0

    def test_non_numeric_values_are_skipped(self):
        payload = {"投资资产_长期保险_某产品": "not a number"}
        assert adjust_balance_sheet_payload(payload) == 0.0
