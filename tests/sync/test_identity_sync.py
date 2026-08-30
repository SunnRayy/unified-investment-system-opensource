"""Tests for src/sync/identity_sync.py's sync_asset_registry() asset-class
determination (Program OSR WS-2 step 5).

Uses an in-memory DuckDB initialized from the real schema.sql (never a bare,
schema-less connector — see CLAUDE.md Database Safety Rules).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.sync.identity_sync import sync_asset_registry

pytestmark = pytest.mark.pipeline


@pytest.fixture
def connector():
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    yield conn
    conn.close()


def _asset_class(connector, canonical_id: str) -> "str | None":
    row = connector.execute(
        "SELECT asset_class FROM asset_registry WHERE canonical_id = ?", [canonical_id]
    ).fetchone()
    return row[0] if row else None


class TestAssetClassFromCanonicalIdPrefix:
    """canonical_id prefix -> asset_class. Wealth_ generalized (WS-2 step 5)
    from an exact-match hardcode of Ray's own "Wealth_CMB" — a self-hoster's
    own bank-wealth product (e.g. "Wealth_ICBC") must classify the same way
    without a code edit."""

    def _sync_with_mapping(self, connector, mapping: dict):
        with patch("src.sources.reader_hooks.FS_ASSET_MAPPING", mapping):
            return sync_asset_registry(connector, {})

    def test_wealth_cmb_classifies_as_bank_wealth(self, connector):
        """Zero-behavior-change guard: Ray's own real asset_id."""
        self._sync_with_mapping(
            connector, {"投资资产_银行理财_招行": ("Wealth_CMB", "招行理财", "CNY")}
        )
        assert _asset_class(connector, "Wealth_CMB") == "Bank Wealth"

    def test_a_different_banks_wealth_id_also_classifies(self, connector):
        """The generalization this step exists for: a fork's own bank name."""
        self._sync_with_mapping(
            connector, {"Some Bank Wealth Column": ("Wealth_ICBC", "ICBC理财", "CNY")}
        )
        assert _asset_class(connector, "Wealth_ICBC") == "Bank Wealth"

    def test_cash_prefix_classifies_as_cash_checking(self, connector):
        self._sync_with_mapping(
            connector, {"col": ("CASH_Deposit_Test_CNY", "Test Cash", "CNY")}
        )
        assert _asset_class(connector, "CASH_Deposit_Test_CNY") == "Cash Checking"

    def test_property_prefix_classifies_as_property(self, connector):
        self._sync_with_mapping(
            connector, {"col": ("Property_Test", "Test Property", "CNY")}
        )
        assert _asset_class(connector, "Property_Test") == "Property"

    def test_pension_prefix_classifies_as_pension(self, connector):
        self._sync_with_mapping(
            connector, {"col": ("Pension_Test", "Test Pension", "CNY")}
        )
        assert _asset_class(connector, "Pension_Test") == "Pension"

    def test_unrecognized_prefix_gets_no_asset_class(self, connector):
        self._sync_with_mapping(
            connector, {"col": ("Something_Unrecognized", "Unrecognized", "CNY")}
        )
        assert _asset_class(connector, "Something_Unrecognized") is None

    def test_synthetic_cash_usd_still_registered(self, connector):
        """1.6 registration (unrelated to FS_ASSET_MAPPING) is unaffected."""
        self._sync_with_mapping(connector, {})
        assert _asset_class(connector, "CASH_USD") == "Cash Checking"
