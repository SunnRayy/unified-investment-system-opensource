"""Tests for src/database/seed_loader.py (Program OSR WS-3a).

Pure file-loading module — no DatabaseConnector, no schema, nothing here
touches a database (project DB-safety rule: N/A, this module has no DB
dependency at all).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.database.mapping_seeds import IEColumn
from src.database.seed_loader import (
    SeedPack,
    SeedProfileNotFoundError,
    list_profiles,
    load_seed_pack,
    resolve_profile,
)

REPO_ROOT = Path(__file__).parent.parent.parent
SEEDS_ROOT = REPO_ROOT / "seeds"


# ---------------------------------------------------------------------------
# Real profiles shipped in the repo
# ---------------------------------------------------------------------------


class TestExampleProfile:
    def test_loads_without_error(self):
        pack = load_seed_pack("example")
        assert isinstance(pack, SeedPack)
        assert pack.profile == "example"

    def test_fs_column_shape_matches_default_dict(self):
        """Loaded fs_column values must be the same 3-tuple shape
        FS_ASSET_MAPPING_SEED / _DEFAULTS holds — a drop-in for WS-3b."""
        pack = load_seed_pack("example")
        fs_column = pack.reader_mappings[("financial_summary", "fs_column")]
        assert len(fs_column) == 13, f"expected 13 mapped fs_column entries, got {len(fs_column)}"
        for key, value in fs_column.items():
            assert isinstance(value, tuple) and len(value) == 3, (key, value)

    def test_fs_column_uses_persona_rename(self):
        """The persona rename must be present in the example profile."""
        pack = load_seed_pack("example")
        fs_column = pack.reader_mappings[("financial_summary", "fs_column")]
        assert "固定资产_房产_阳光花园" in fs_column
        assert fs_column["固定资产_房产_阳光花园"][0] == "Property_阳光花园"

    def test_ie_column_returns_iecolumn_instances(self):
        pack = load_seed_pack("example")
        ie_column = pack.reader_mappings[("financial_summary", "ie_column")]
        assert len(ie_column) == 46
        sample = ie_column["收入_主动收入_工资"]
        assert isinstance(sample, IEColumn)
        assert sample.role == "income"
        assert sample.group == "active_income"

    def test_ie_column_insurance_renames_present(self):
        pack = load_seed_pack("example")
        ie_column = pack.reader_mappings[("financial_summary", "ie_column")]
        for renamed in ("必要开支_保险_安泰人生", "必要开支_保险_公司团险", "必要开支_保险_互联网保险"):
            assert renamed in ie_column, renamed
            assert ie_column[renamed].role == "expense"

    def test_computed_column_carries_validates(self):
        pack = load_seed_pack("example")
        ie_column = pack.reader_mappings[("financial_summary", "ie_column")]
        total_income = ie_column["总收入合计"]
        assert total_income.role == "computed"
        assert total_income.validates == {"groups": ["active_income", "passive_income"]}

    def test_id_field_maps(self):
        pack = load_seed_pack("example")
        gold = pack.reader_mappings[("gold", "id_field_map")]
        assert gold["account:招行"] == "CMB"
        assert gold["asset_name:纸黄金"] == "PAPER"
        rsu = pack.reader_mappings[("rsu", "id_field_map")]
        assert rsu["asset_name:Amazon RSU"] == "AMZN"
        insurance = pack.reader_mappings[("insurance", "id_field_map")]
        assert insurance == {}, "insurance has no YAML id_field_maps today — mirrors that"

    def test_schwab_vocab(self):
        pack = load_seed_pack("example")
        known_etf = pack.reader_mappings[("schwab", "known_etf")]
        assert known_etf.get("VOO") is True
        assert len(known_etf) == 74
        symbol_norm = pack.reader_mappings[("schwab", "symbol_norm")]
        assert symbol_norm["BRK/B"] == "BRK-B"
        action_map = pack.reader_mappings[("schwab", "action_map")]
        assert action_map["Buy"] == "buy"
        assert action_map["Security Transfer"] == "transfer"

    def test_cn_fund_type_map(self):
        pack = load_seed_pack("example")
        type_map = pack.reader_mappings[("cn_fund", "type_map")]
        assert type_map["申购"] == "buy"
        assert type_map["现金分红"] == "dividend_cash"

    def test_fs_ignored_columns(self):
        pack = load_seed_pack("example")
        assert len(pack.fs_ignored_columns) == 10
        assert "投资资产_长期保险_安泰人生" in pack.fs_ignored_columns

    def test_memos_reference_persona_holdings(self):
        pack = load_seed_pack("example")
        assert len(pack.memo_registry) >= 2
        asset_ids = {row["asset_id"] for row in pack.memo_asset_map}
        assert "US_STK_VOO" in asset_ids
        assert "RSU_AMZN" in asset_ids

    def test_data_fixes_and_unforced_errors_and_valuation(self):
        pack = load_seed_pack("example")
        assert len(pack.data_fixes) >= 1
        assert all("title" in row for row in pack.data_fixes)
        assert len(pack.unforced_errors) >= 1
        assert len(pack.valuation_reference) >= 1
        tickers = {row["ticker"] for row in pack.valuation_reference}
        assert "AAPL" in tickers  # persona.schwab.positions holding


class TestEmptyProfile:
    def test_loads_cleanly(self):
        pack = load_seed_pack("empty")
        assert pack.profile == "empty"

    def test_everything_is_empty(self):
        pack = load_seed_pack("empty")
        for key, value in pack.reader_mappings.items():
            assert value == {}, f"{key} should be empty in the empty profile, got {value}"
        assert pack.fs_ignored_columns == []
        assert pack.memo_registry == []
        assert pack.memo_asset_map == []
        assert pack.data_fixes == []
        assert pack.unforced_errors == []
        assert pack.valuation_reference == []

    def test_reader_mappings_has_all_nine_keys_even_when_empty(self):
        """Shape parity with _DEFAULTS: all 9 (reader_key, kind) pairs must be
        present as keys, even if their value is an empty dict."""
        pack = load_seed_pack("empty")
        expected_keys = {
            ("financial_summary", "fs_column"), ("financial_summary", "ie_column"),
            ("gold", "id_field_map"), ("insurance", "id_field_map"), ("rsu", "id_field_map"),
            ("schwab", "known_etf"), ("schwab", "symbol_norm"), ("schwab", "action_map"),
            ("cn_fund", "type_map"),
        }
        assert set(pack.reader_mappings.keys()) == expected_keys


class TestUnknownProfile:
    def test_raises_seed_profile_not_found(self):
        with pytest.raises(SeedProfileNotFoundError):
            load_seed_pack("does-not-exist-anywhere")

    def test_error_is_a_file_not_found_error(self):
        """Subclass relationship must hold — callers catching FileNotFoundError
        generically must still catch this."""
        with pytest.raises(FileNotFoundError):
            load_seed_pack("does-not-exist-anywhere")

    def test_error_message_lists_known_profiles(self):
        with pytest.raises(SeedProfileNotFoundError) as exc_info:
            load_seed_pack("nope")
        msg = str(exc_info.value)
        assert "example" in msg
        assert "empty" in msg


class TestListProfiles:
    def test_lists_example_and_empty(self):
        profiles = list_profiles()
        assert "example" in profiles
        assert "empty" in profiles

    def test_missing_root_returns_empty_list(self, tmp_path):
        assert list_profiles(tmp_path / "nonexistent") == []


class TestResolveProfile:
    def test_explicit_arg_wins(self, monkeypatch):
        monkeypatch.setenv("UIS_SEED_PROFILE", "empty")
        assert resolve_profile("example") == "example"

    def test_env_var_used_when_no_explicit_arg(self, monkeypatch):
        monkeypatch.setenv("UIS_SEED_PROFILE", "empty")
        assert resolve_profile(None) == "empty"

    def test_default_when_neither_set(self, monkeypatch):
        monkeypatch.delenv("UIS_SEED_PROFILE", raising=False)
        assert resolve_profile(None) == "example"


# ---------------------------------------------------------------------------
# Round-trip on a synthetic, hand-built profile (tmp_path) — exercises every
# decode path explicitly rather than relying on seeds/example's specific
# content, so a future edit to seeds/example can't silently stop testing
# the loader's actual decoding logic.
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestRoundTripSyntheticProfile:
    @pytest.fixture
    def synthetic_root(self, tmp_path):
        root = tmp_path / "seeds_root"
        profile = root / "roundtrip"
        rm = profile / "reader_mappings"

        _write(rm / "financial_summary.yaml", """
fs_column:
  mapped:
    - excel_col: "现金"
      asset_id: "CASH_TEST"
      asset_name: "Test Cash"
      currency: "CNY"
  ignored:
    - excel_col: "忽略列"
      reason: "test reason"
ie_column:
  - excel_col: "收入_工资"
    role: "income"
    bucket: null
    currency: "CNY"
    group: "active_income"
  - excel_col: "总收入合计"
    role: "computed"
    bucket: null
    currency: "CNY"
    validates:
      groups: ["active_income"]
""")
        _write(rm / "gold.yaml", """
id_field_map:
  - field: "account"
    label: "测试银行"
    code: "TESTBANK"
""")
        _write(rm / "insurance.yaml", "id_field_map: []\n")
        _write(rm / "rsu.yaml", """
id_field_map:
  - field: "asset_name"
    label: "Test RSU"
    code: "TESTRSU"
""")
        _write(rm / "schwab.yaml", """
known_etf: ["TESTETF"]
symbol_norm:
  - from: "TEST/A"
    to: "TEST-A"
action_map:
  - raw: "Buy"
    type: "buy"
""")
        _write(rm / "cn_fund.yaml", """
type_map:
  - raw: "申购"
    type: "buy"
""")
        _write(profile / "memos.yaml", """
memo_registry:
  - memo_id: "T-1"
    title: "Test memo"
    status: "active"
    falsification_summary: "test"
    doc_link: null
memo_asset_map:
  - memo_id: "T-1"
    asset_id: "CASH_TEST"
""")
        _write(profile / "data_fixes.yaml", """
data_fixes:
  - title: "Test fix"
    description: "test"
    metric_key: null
    due_days: 10
    status: "open"
""")
        _write(profile / "unforced_errors.yaml", """
unforced_errors:
  - error_date: "2026-01-01"
    description: "test error"
    est_cost_cny: 100.0
    root_cause: "test cause"
    linked_rule: "test rule"
""")
        _write(profile / "valuation_reference.yaml", """
valuation_reference:
  - ticker: "TEST"
    metric: "pe_ttm"
    low_threshold: 10.0
    high_threshold: 20.0
    historical_mean: 15.0
    rate_sensitive: false
    notes: "test"
""")
        return root

    def test_full_round_trip(self, synthetic_root):
        pack = load_seed_pack("roundtrip", seeds_root=synthetic_root)

        assert pack.reader_mappings[("financial_summary", "fs_column")] == {
            "现金": ("CASH_TEST", "Test Cash", "CNY"),
        }
        assert pack.fs_ignored_columns == ["忽略列"]

        ie = pack.reader_mappings[("financial_summary", "ie_column")]
        assert ie["收入_工资"] == IEColumn(role="income", bucket=None, currency="CNY", group="active_income")
        assert ie["总收入合计"] == IEColumn(
            role="computed", bucket=None, currency="CNY", group=None,
            validates={"groups": ["active_income"]},
        )

        assert pack.reader_mappings[("gold", "id_field_map")] == {"account:测试银行": "TESTBANK"}
        assert pack.reader_mappings[("insurance", "id_field_map")] == {}
        assert pack.reader_mappings[("rsu", "id_field_map")] == {"asset_name:Test RSU": "TESTRSU"}

        assert pack.reader_mappings[("schwab", "known_etf")] == {"TESTETF": True}
        assert pack.reader_mappings[("schwab", "symbol_norm")] == {"TEST/A": "TEST-A"}
        assert pack.reader_mappings[("schwab", "action_map")] == {"Buy": "buy"}
        assert pack.reader_mappings[("cn_fund", "type_map")] == {"申购": "buy"}

        assert pack.memo_registry == [{
            "memo_id": "T-1", "title": "Test memo", "status": "active",
            "falsification_summary": "test", "doc_link": None,
        }]
        assert pack.memo_asset_map == [{"memo_id": "T-1", "asset_id": "CASH_TEST"}]

        assert pack.data_fixes == [{
            "title": "Test fix", "description": "test", "metric_key": None,
            "due_days": 10, "status": "open",
        }]
        assert pack.unforced_errors == [{
            "error_date": "2026-01-01", "description": "test error", "est_cost_cny": 100.0,
            "root_cause": "test cause", "linked_rule": "test rule",
        }]
        assert pack.valuation_reference == [{
            "ticker": "TEST", "metric": "pe_ttm", "low_threshold": 10.0,
            "high_threshold": 20.0, "historical_mean": 15.0, "rate_sensitive": False,
            "notes": "test",
        }]

    def test_missing_optional_files_treated_as_empty(self, tmp_path):
        """A profile directory that exists but has no YAML files at all
        must still load cleanly with every collection empty."""
        root = tmp_path / "seeds_root2"
        (root / "bare").mkdir(parents=True)

        pack = load_seed_pack("bare", seeds_root=root)

        assert pack.reader_mappings[("financial_summary", "fs_column")] == {}
        assert pack.fs_ignored_columns == []
        assert pack.memo_registry == []
        assert pack.data_fixes == []
        assert pack.unforced_errors == []
        assert pack.valuation_reference == []

    def test_unknown_profile_under_custom_root_raises(self, tmp_path):
        root = tmp_path / "seeds_root3"
        root.mkdir()
        with pytest.raises(SeedProfileNotFoundError):
            load_seed_pack("ghost", seeds_root=root)
