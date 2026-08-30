"""Reader Mapping Management — WS-A golden tests (ADR-023).

Covers:
  - Migration V75: reader_mappings + reader_mapping_audit tables created,
    idempotent seed (13 rows == len(FS_ASSET_MAPPING_SEED)), re-run is a no-op.
  - load_reader_mappings(): defaults, DB override wins, archived row removes
    the key, missing table gracefully falls back to defaults.
  - GOLDEN TEST (the gate for this workstream): melt_financial_summary_holdings
    run through the config engine on the real FS fixture produces byte-identical
    output whether using the hardcoded default path or the DB-seeded metadata
    path — the whole point of the injection is that it must be a no-op change
    until the DB actually diverges from the code defaults.

All tests use tmp_path / ":memory:" DBs — never data/unified.duckdb.
"""
from pathlib import Path

import pandas as pd
import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import bootstrap_database, initialize_schema
from src.database.mapping_seeds import (
    CN_FUND_TYPE_MAP_SEED,
    FS_ASSET_MAPPING_SEED,
    FS_IGNORED_COLUMNS_SEED,
    ID_FIELD_MAP_SEEDS,
    IE_COLUMN_SEED,
    IE_CURRENCIES,
    IE_DESTINATION_BUCKETS,
    IE_PASS_THROUGH_BUCKETS,
    IE_ROLE_BUCKETS,
    IE_ROLES,
    IEColumn,
    SCHWAB_ACTION_MAPPING_SEED,
    SCHWAB_KNOWN_ETFS_SEED,
    SCHWAB_SYMBOL_NORMALIZATIONS_SEED,
    VOCAB_SEEDS,
)
from src.services.reader_mappings import (
    ALLOWED_TRANSACTION_TYPES,
    _get_defaults,
    get_ignored_map_keys,
    load_id_field_maps,
    load_reader_mappings,
    nest_id_field_map,
    scan_unmapped_columns,
    scan_unmapped_id_field_map_labels,
    scan_unmapped_vocab_values,
)
from src.sources.reader_hooks import (
    FS_ASSET_MAPPING,
    melt_financial_summary_holdings,
    schwab_transactions_from_csv,
)
from src.sources.config_driven_reader import ConfigDrivenReader
from src.sources.reader_config import load_reader_config

pytestmark = pytest.mark.pipeline

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "readers"
FIXTURE = FIXTURE_DIR / "Financial_Summary_new.xlsx"
CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "readers"
FS_YAML = CONFIG_DIR / "financial_summary.yaml"
GOLD_YAML = CONFIG_DIR / "gold.yaml"
INSURANCE_YAML = CONFIG_DIR / "insurance.yaml"
RSU_YAML = CONFIG_DIR / "rsu.yaml"
GOLD_FIXTURE = FIXTURE_DIR / "Gold_transactions.xlsx"
INSURANCE_FIXTURE = FIXTURE_DIR / "Insurance_Portfolio.xlsx"
RSU_FIXTURE = FIXTURE_DIR / "RSU_transactions.xlsx"

_SEED_COUNT = len(FS_ASSET_MAPPING_SEED)
_IGNORED_SEED_COUNT = len(FS_IGNORED_COLUMNS_SEED)
_TOTAL_SEED_COUNT = _SEED_COUNT + _IGNORED_SEED_COUNT  # financial_summary/fs_column only
_ID_FIELD_MAP_SEED_COUNT = sum(len(v) for v in ID_FIELD_MAP_SEEDS.values())  # V77 (WS-B)
_VOCAB_SEED_COUNT = sum(  # V78 (WS-C)
    len(seed) for kinds in VOCAB_SEEDS.values() for seed in kinds.values()
)
_IE_COLUMN_SEED_COUNT = len(IE_COLUMN_SEED)  # V82 (月度收支 column semantics)
# Whole-table row count across every reader/kind seeded so far
# (V75+V76+V77+V78+V82).
_ALL_SEED_COUNT = (
    _TOTAL_SEED_COUNT + _ID_FIELD_MAP_SEED_COUNT + _VOCAB_SEED_COUNT + _IE_COLUMN_SEED_COUNT
)
def _expected_fresh_db_fs_column() -> dict:
    """What load_reader_mappings(financial_summary, fs_column) should return
    on a freshly migrated DB with no archives/overrides (Program OSR WS-3b).

    V75 always seeds exactly FS_ASSET_MAPPING_SEED's rows, and in this public
    export that seed already IS the persona data (tools/release/
    mapping_seeds.public.py), so it has exactly 13 entries — the same
    固定资产_房产_阳光花园 key the "example" profile baseline already has, not
    an extra one. (The owner's private repo instead sees 14, because its
    unswapped seed module still keys that column under the real workbook's
    own name, a genuinely different key from the baseline's persona rename —
    see docs/plans/2026-08-16-ws1-swap-impact.md §5.2 for that case.)
    """
    expected = dict(_get_defaults()[("financial_summary", "fs_column")])
    expected.update(FS_ASSET_MAPPING)
    return expected


def _expected_fresh_db_ie_column() -> dict:
    """Same reasoning as _expected_fresh_db_fs_column(), for ie_column.

    V82 always seeds exactly IE_COLUMN_SEED_JSON's 46 keys, and in this
    public export they already carry the persona's 3 renamed insurance
    columns (安泰人生/公司团险/互联网保险) — the same keys the "example" profile
    baseline uses, so the merge stays at 46, not 49. (The owner's private
    repo instead sees 49, because its unswapped seed module still keys
    those 3 leaves under the real workbook's own product names, genuinely
    different keys from the baseline's persona renames — see
    docs/plans/2026-08-16-ws1-swap-impact.md §5.2 for that case.)
    """
    expected = dict(_get_defaults()[("financial_summary", "ie_column")])
    expected.update(IE_COLUMN_SEED)
    return expected


SCHWAB_TXN_FIXTURE = FIXTURE_DIR / "Individual_XXX342_Transactions_20260523-060417.csv"
CN_FUND_FIXTURE = FIXTURE_DIR / "funding_transactions.xlsx"
SCHWAB_YAML = CONFIG_DIR / "schwab.yaml"
CN_FUND_YAML = CONFIG_DIR / "cn_fund.yaml"


def _make_db(tmp_path, name="reader_mappings_test.duckdb"):
    """Fresh DB with schema + all migrations applied (includes V75)."""
    db_path = tmp_path / name
    connector = DatabaseConnector(str(db_path))
    bootstrap_database(connector)
    return connector


# ---------------------------------------------------------------------------
# Migration V75
# ---------------------------------------------------------------------------

class TestMigrationV75:
    def test_tables_created(self, tmp_path):
        connector = _make_db(tmp_path)
        tables = {
            r[0]
            for r in connector.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert "reader_mappings" in tables
        assert "reader_mapping_audit" in tables
        connector.close()

    def test_reader_mappings_columns(self, tmp_path):
        connector = _make_db(tmp_path)
        cols = {
            r[0]
            for r in connector.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'reader_mappings'"
            ).fetchall()
        }
        required = {
            "id", "reader_key", "mapping_kind", "map_key", "map_value",
            "status", "sort_order", "created_at", "updated_at",
        }
        assert required.issubset(cols)
        connector.close()

    def test_reader_mapping_audit_columns(self, tmp_path):
        connector = _make_db(tmp_path)
        cols = {
            r[0]
            for r in connector.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'reader_mapping_audit'"
            ).fetchall()
        }
        required = {"id", "mapping_id", "action", "old_value", "new_value", "at"}
        assert required.issubset(cols)
        connector.close()

    def test_seed_row_count(self, tmp_path):
        """V75 seeds _SEED_COUNT active fs_column mappings; V76 (A4.1) adds
        _IGNORED_SEED_COUNT status='ignored' rows for the same table —
        total row count is the sum of both."""
        connector = _make_db(tmp_path)
        count = connector.execute(
            "SELECT COUNT(*) FROM reader_mappings WHERE reader_key = 'financial_summary' AND mapping_kind = 'fs_column'"
        ).fetchone()[0]
        assert count == _TOTAL_SEED_COUNT
        active_count = connector.execute(
            "SELECT COUNT(*) FROM reader_mappings WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'fs_column' AND status = 'active'"
        ).fetchone()[0]
        assert active_count == _SEED_COUNT
        ignored_count = connector.execute(
            "SELECT COUNT(*) FROM reader_mappings WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'fs_column' AND status = 'ignored'"
        ).fetchone()[0]
        assert ignored_count == _IGNORED_SEED_COUNT
        connector.close()

    def test_seed_is_idempotent_on_rerun(self, tmp_path):
        connector = _make_db(tmp_path)
        count_before = connector.execute("SELECT COUNT(*) FROM reader_mappings").fetchone()[0]
        # Re-running migrations must not duplicate seed rows or fail.
        connector.run_migrations()
        count_after = connector.execute("SELECT COUNT(*) FROM reader_mappings").fetchone()[0]
        assert count_before == count_after == _ALL_SEED_COUNT
        connector.close()

    def test_migration_recorded_in_schema_version(self, tmp_path):
        connector = _make_db(tmp_path)
        row = connector.execute(
            "SELECT label FROM schema_version WHERE version = 75"
        ).fetchone()
        assert row is not None
        row76 = connector.execute(
            "SELECT label FROM schema_version WHERE version = 76"
        ).fetchone()
        assert row76 is not None
        row77 = connector.execute(
            "SELECT label FROM schema_version WHERE version = 77"
        ).fetchone()
        assert row77 is not None
        connector.close()


# ---------------------------------------------------------------------------
# load_reader_mappings()
# ---------------------------------------------------------------------------

class TestLoadReaderMappings:
    def test_matches_code_defaults_on_fresh_seeded_db(self, tmp_path):
        connector = _make_db(tmp_path)
        loaded = load_reader_mappings(connector, "financial_summary", "fs_column")
        assert loaded == _expected_fresh_db_fs_column()
        connector.close()

    def test_missing_table_falls_back_to_defaults(self, tmp_path):
        """A DB that predates migration V75 (no reader_mappings table) must not
        crash the sync — the loader falls back to the code defaults (i.e. the
        active profile's baseline directly — there's no table to re-merge
        real rows from, unlike the fresh-seeded-DB case above)."""
        db_path = tmp_path / "premigration.duckdb"
        connector = DatabaseConnector(str(db_path))
        initialize_schema(connector)  # base schema.sql only — no migrations run
        loaded = load_reader_mappings(connector, "financial_summary", "fs_column")
        assert loaded == _get_defaults()[("financial_summary", "fs_column")]
        connector.close()

    def test_archived_row_removes_key_from_merged_dict(self, tmp_path):
        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET status = 'archived' WHERE map_key = ?",
            ["RMB现金现金"],
        )
        loaded = load_reader_mappings(connector, "financial_summary", "fs_column")
        assert "RMB现金现金" not in loaded
        assert len(loaded) == len(_expected_fresh_db_fs_column()) - 1
        connector.close()

    def test_db_override_wins_over_default(self, tmp_path):
        import json

        connector = _make_db(tmp_path)
        override_payload = json.dumps(
            {"asset_id": "Property_TEST_OVERRIDE", "asset_name": "override name", "currency": "CNY"}
        )
        connector.execute(
            "UPDATE reader_mappings SET map_value = ? WHERE map_key = ?",
            [override_payload, "固定资产_房产_阳光花园"],
        )
        loaded = load_reader_mappings(connector, "financial_summary", "fs_column")
        assert loaded["固定资产_房产_阳光花园"] == ("Property_TEST_OVERRIDE", "override name", "CNY")
        # Default value must actually have been overridden (sanity check).
        assert FS_ASSET_MAPPING["固定资产_房产_阳光花园"] != loaded["固定资产_房产_阳光花园"]
        connector.close()

    def test_new_active_row_is_added_to_merged_dict(self, tmp_path):
        import json

        connector = _make_db(tmp_path)
        new_payload = json.dumps(
            {"asset_id": "CASH_Deposit_NewBank_CNY", "asset_name": "新银行存款 (CNY)", "currency": "CNY"}
        )
        connector.execute(
            """
            INSERT INTO reader_mappings (reader_key, mapping_kind, map_key, map_value, status, sort_order)
            VALUES ('financial_summary', 'fs_column', 'RMB存款_新银行', ?, 'active', 99)
            """,
            [new_payload],
        )
        loaded = load_reader_mappings(connector, "financial_summary", "fs_column")
        assert loaded["RMB存款_新银行"] == ("CASH_Deposit_NewBank_CNY", "新银行存款 (CNY)", "CNY")
        assert len(loaded) == len(_expected_fresh_db_fs_column()) + 1
        connector.close()

    def test_ignored_row_removes_key_from_merged_dict(self, tmp_path):
        """A status='ignored' row (ADR-023 A4.1) behaves exactly like
        'archived' for the melt path — never reaches the merged dict."""
        connector = _make_db(tmp_path)
        ignored_key = FS_IGNORED_COLUMNS_SEED[0]
        loaded = load_reader_mappings(connector, "financial_summary", "fs_column")
        assert ignored_key not in loaded
        # Sanity: the seeded ignored keys were never in the active defaults
        # either (they're an owner decision about columns that were never
        # mapped, not a demotion of a previously-mapped key).
        assert ignored_key not in FS_ASSET_MAPPING
        assert len(loaded) == len(_expected_fresh_db_fs_column())
        connector.close()


class TestGetIgnoredMapKeys:
    def test_returns_seeded_ignored_map_keys(self, tmp_path):
        connector = _make_db(tmp_path)
        ignored = get_ignored_map_keys(connector, "financial_summary", "fs_column")
        assert set(ignored.keys()) == set(FS_IGNORED_COLUMNS_SEED)
        connector.close()

    def test_missing_table_falls_back_to_empty(self, tmp_path):
        db_path = tmp_path / "premigration.duckdb"
        connector = DatabaseConnector(str(db_path))
        initialize_schema(connector)  # base schema.sql only — no migrations run
        ignored = get_ignored_map_keys(connector, "financial_summary", "fs_column")
        assert ignored == {}
        connector.close()

    def test_ids_are_real_row_ids(self, tmp_path):
        connector = _make_db(tmp_path)
        ignored = get_ignored_map_keys(connector, "financial_summary", "fs_column")
        first_key = FS_IGNORED_COLUMNS_SEED[0]
        row_id = ignored[first_key]
        row = connector.execute(
            "SELECT map_key, status FROM reader_mappings WHERE id = ?", [row_id]
        ).fetchone()
        assert row == (first_key, "ignored")
        connector.close()


# ---------------------------------------------------------------------------
# scan_unmapped_columns() category classification (ADR-023 A4.1)
# ---------------------------------------------------------------------------

class TestScanUnmappedColumnsCategory:
    """One case per structural rule, in precedence order, plus the HSBC
    native-currency pair the live smoke test flagged. See
    docs/plans/2026-07-18-reader-mapping-management.md A4.1 refinement."""

    def _scan(self, columns, merged=None, ignored_keys=None):
        return scan_unmapped_columns(columns, merged or {}, ignored_keys=ignored_keys)

    def test_ignored_takes_precedence(self):
        result = self._scan(["创业股权投资"], ignored_keys={"创业股权投资": 42})
        assert result == [
            {"column": "创业股权投资", "ignored_native": False, "category": "ignored", "mapping_id": 42}
        ]

    def test_native_currency_suffix_usd(self):
        result = self._scan(["美元存款_Chase_USD"])
        assert result[0]["category"] == "native"
        assert result[0]["ignored_native"] is True
        assert result[0]["mapping_id"] is None

    def test_native_currency_suffix_hkd_hsbc_pair(self):
        """The HSBC HKD column and its CNY-mapped sibling: the mapped one is
        excluded entirely (present in `merged`), the _HKD sibling is 'native'."""
        merged = {"HKD存款_HSBC": ("CASH_Deposit_HSBC_HKD", "HSBC存款 (HKD)", "CNY")}
        result = self._scan(["HKD存款_HSBC", "HKD存款_HSBC_HKD"], merged=merged)
        assert len(result) == 1
        assert result[0]["column"] == "HKD存款_HSBC_HKD"
        assert result[0]["category"] == "native"

    def test_computed_totals_prefix(self):
        for col in ["合计流动资产", "合计净资产", "合计总资产"]:
            result = self._scan([col])
            assert result[0]["category"] == "computed", col

    def test_computed_ratio_substring(self):
        for col in ["即付比例 70%", "投资比例 50%", "资产负债率 50%"]:
            result = self._scan([col])
            assert result[0]["category"] == "computed", col

    def test_computed_usd_rate_exact(self):
        result = self._scan(["USD Rate"])
        assert result[0]["category"] == "computed"

    def test_liability_prefixes(self):
        for col in ["短期负债_信用卡_招行", "长期负债_房贷", "其他负债"]:
            result = self._scan([col])
            assert result[0]["category"] == "liability", col

    def test_liability_column_with_native_suffix_is_native_not_liability(self):
        """Precedence: native (suffix) is checked before liability (prefix) —
        matches the existing ignored_native semantics for these columns."""
        result = self._scan(["短期负债_信用卡_美国信用卡年费_USD"])
        assert result[0]["category"] == "native"
        assert result[0]["ignored_native"] is True

    def test_candidate_fallback(self):
        result = self._scan(["创业股权投资", "投资资产_股票基金_A股基金"])
        assert all(c["category"] == "candidate" for c in result)

    def test_date_and_blank_columns_never_reported(self):
        result = self._scan(["日期", "", "Unnamed: 3"])
        assert result == []

    def test_already_mapped_column_never_reported(self):
        merged = {"RMB现金现金": ("CASH_Cash_CNY", "现金 (CNY)", "CNY")}
        result = self._scan(["RMB现金现金"], merged=merged)
        assert result == []


# ---------------------------------------------------------------------------
# GOLDEN TEST — the gate for this whole workstream
# ---------------------------------------------------------------------------

class TestGoldenMeltParity:
    """Asserts byte-identical melt output: hardcoded default path == DB-seeded
    metadata path. This is the safety net that lets the reader-mapping DB
    layer replace the hardcoded dict with zero behavior change until the
    owner actually edits a mapping via the (future) UI."""

    def test_melt_output_identical_default_vs_db_seeded(self, tmp_path, monkeypatch):
        # Path A (below) never calls load_reader_mappings — it goes through
        # reader_hooks.py's own hardcoded _FS_ASSET_MAPPING fallback, which
        # does NOT consult $UIS_SEED_PROFILE at all (Program OSR WS-3b
        # finding). This test's premise — "Path A == Path B with zero DB
        # divergence" — is therefore only a guarantee of the LEGACY default
        # profile (production today: no UIS_SEED_PROFILE set), not of an
        # arbitrary active profile. The session-wide conftest fixture sets
        # UIS_SEED_PROFILE=example for the rest of the suite; unset it here
        # so this test still proves what it was written to prove.
        monkeypatch.delenv("UIS_SEED_PROFILE", raising=False)

        cfg = load_reader_config(FS_YAML)

        # Path A: hardcoded default (no injected metadata — legacy behavior).
        reader_a = ConfigDrivenReader(cfg)
        data_a = reader_a.read(FIXTURE)
        holdings_default, _ = reader_a.transform(data_a)

        # Path B: DB-seeded metadata injection (mirrors orchestrator wiring).
        connector = _make_db(tmp_path)
        fs_mappings = load_reader_mappings(connector, "financial_summary", "fs_column")

        reader_b = ConfigDrivenReader(cfg)
        data_b = reader_b.read(FIXTURE)
        data_b.metadata["fs_asset_mappings"] = fs_mappings
        holdings_db, _ = reader_b.transform(data_b)
        connector.close()

        assert not holdings_default.empty
        assert holdings_default.shape == holdings_db.shape
        pd.testing.assert_frame_equal(holdings_default, holdings_db)

    def test_melt_directly_with_metadata_dict_matches_default(self):
        """Same parity check calling melt_financial_summary_holdings directly
        (bypassing the config engine) — confirms the hook's own fallback logic."""
        cfg = load_reader_config(FS_YAML)
        reader = ConfigDrivenReader(cfg)
        data = reader.read(FIXTURE)

        result_default = melt_financial_summary_holdings(data.holdings, metadata={})
        result_explicit_default_dict = melt_financial_summary_holdings(
            data.holdings, metadata={"fs_asset_mappings": dict(FS_ASSET_MAPPING)}
        )
        pd.testing.assert_frame_equal(result_default, result_explicit_default_dict)

    def test_archived_column_disappears_from_melt_output(self, tmp_path):
        """An archived mapping must stop producing its asset_id in the melted
        holdings — the concrete effect of the 'account closure' semantics.

        Program OSR WS-3b: the fixture's property column is
        固定资产_房产_阳光花园 (the persona rename). In this public export V75
        DOES seed an active DB row for that exact key (mapping_seeds.py is
        already the persona twin), so archiving it is an UPDATE against that
        row, not an INSERT — the owner's private repo instead sees this as an
        INSERT, because its V75-seeded key is the real workbook's own name, a
        different key from the persona rename this test archives (see
        docs/plans/2026-08-16-ws1-swap-impact.md §3.1 for that case). The
        pre-archive assertion below is what actually proves the archive had an
        effect.
        """
        cfg = load_reader_config(FS_YAML)
        reader = ConfigDrivenReader(cfg)

        connector = _make_db(tmp_path)
        fs_mappings_before = load_reader_mappings(connector, "financial_summary", "fs_column")
        data_before = reader.read(FIXTURE)
        data_before.metadata["fs_asset_mappings"] = fs_mappings_before
        holdings_before, _ = reader.transform(data_before)
        assert "Property_阳光花园" in set(holdings_before["asset_id"].unique())

        connector.execute(
            "UPDATE reader_mappings SET status = 'archived' "
            "WHERE reader_key = 'financial_summary' AND mapping_kind = 'fs_column' "
            "AND map_key = '固定资产_房产_阳光花园'"
        )
        fs_mappings_after = load_reader_mappings(connector, "financial_summary", "fs_column")
        connector.close()

        data_after = reader.read(FIXTURE)
        data_after.metadata["fs_asset_mappings"] = fs_mappings_after
        holdings_db, _ = reader.transform(data_after)

        assert "Property_阳光花园" not in set(holdings_db["asset_id"].unique())


# ---------------------------------------------------------------------------
# ADR-023 WS-B — Gold/Insurance/RSU id_field_map
# ---------------------------------------------------------------------------


def _id_template_fields_from_yaml(yaml_path: Path) -> "set[str]":
    """Union of id_template placeholder names across all of a reader's sheets."""
    import re

    cfg = load_reader_config(yaml_path)
    fields: "set[str]" = set()
    for sheet_cfg in cfg.parsing.sheets if cfg.parsing else []:
        if sheet_cfg.id_template:
            fields.update(re.findall(r"\{(\w+)\}", sheet_cfg.id_template))
    return fields


def _flatten_yaml_id_field_maps(yaml_path: Path) -> "dict[str, str]":
    cfg = load_reader_config(yaml_path)
    flat: "dict[str, str]" = {}
    for sheet_cfg in cfg.parsing.sheets if cfg.parsing else []:
        for field, labels in sheet_cfg.id_field_maps.items():
            for label, code in labels.items():
                key = f"{field}:{label}"
                # All sheets within a reader must agree (gold declares the
                # same id_field_maps on both its holdings + transactions sheets).
                existing = flat.get(key)
                assert existing is None or existing == code, (
                    f"{yaml_path}: conflicting code for {key}: {existing!r} vs {code!r}"
                )
                flat[key] = code
    return flat


class TestIdFieldMapSeedsMatchYaml:
    """Guards against ID_FIELD_MAP_SEEDS (src.database.mapping_seeds) drifting
    from config/readers/*.yaml — the seed dict must always mirror the YAML
    id_field_maps content exactly (the YAML stays the code-default source of
    truth; the seed is a one-time mirror into the DB, never re-derived)."""

    @pytest.mark.parametrize(
        "reader_key,yaml_path",
        [("gold", GOLD_YAML), ("insurance", INSURANCE_YAML), ("rsu", RSU_YAML)],
    )
    def test_seed_matches_yaml(self, reader_key, yaml_path):
        assert ID_FIELD_MAP_SEEDS[reader_key] == _flatten_yaml_id_field_maps(yaml_path)


class TestMigrationV77:
    def test_seed_row_counts_per_reader(self, tmp_path):
        connector = _make_db(tmp_path)
        for reader_key, seed in ID_FIELD_MAP_SEEDS.items():
            count = connector.execute(
                "SELECT COUNT(*) FROM reader_mappings WHERE reader_key = ? AND mapping_kind = 'id_field_map'",
                [reader_key],
            ).fetchone()[0]
            assert count == len(seed), f"{reader_key}: expected {len(seed)}, got {count}"
        connector.close()

    def test_seed_value_shape(self, tmp_path):
        import json

        connector = _make_db(tmp_path)
        row = connector.execute(
            "SELECT map_value FROM reader_mappings WHERE reader_key = 'gold' "
            "AND mapping_kind = 'id_field_map' AND map_key = 'account:招行'"
        ).fetchone()
        assert row is not None
        assert json.loads(row[0]) == {"code": "CMB"}
        connector.close()

    def test_seed_is_idempotent_on_rerun(self, tmp_path):
        connector = _make_db(tmp_path)
        count_before = connector.execute(
            "SELECT COUNT(*) FROM reader_mappings WHERE mapping_kind = 'id_field_map'"
        ).fetchone()[0]
        connector.run_migrations()
        count_after = connector.execute(
            "SELECT COUNT(*) FROM reader_mappings WHERE mapping_kind = 'id_field_map'"
        ).fetchone()[0]
        assert count_before == count_after == _ID_FIELD_MAP_SEED_COUNT
        connector.close()


class TestLoadIdFieldMaps:
    def test_matches_code_defaults_on_fresh_seeded_db(self, tmp_path):
        connector = _make_db(tmp_path)
        nested = load_id_field_maps(connector, "gold")
        connector.close()
        assert nested == nest_id_field_map(ID_FIELD_MAP_SEEDS["gold"])

    def test_insurance_defaults_to_empty(self, tmp_path):
        connector = _make_db(tmp_path)
        nested = load_id_field_maps(connector, "insurance")
        connector.close()
        assert nested == {}

    def test_db_override_wins_over_default(self, tmp_path):
        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET map_value = '{\"code\": \"CMB2\"}' "
            "WHERE reader_key = 'gold' AND mapping_kind = 'id_field_map' AND map_key = 'account:招行'"
        )
        nested = load_id_field_maps(connector, "gold")
        connector.close()
        assert nested["account"]["招行"] == "CMB2"
        # Sibling labels are unaffected.
        assert nested["account"]["工行"] == "ICBC"

    def test_archived_row_removes_label_falls_back_to_passthrough(self, tmp_path):
        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET status = 'archived' "
            "WHERE reader_key = 'gold' AND mapping_kind = 'id_field_map' AND map_key = 'account:招行'"
        )
        nested = load_id_field_maps(connector, "gold")
        connector.close()
        assert "招行" not in nested.get("account", {})


class TestNestIdFieldMap:
    def test_nests_flat_dict(self):
        flat = {"asset_name:纸黄金": "PAPER", "account:招行": "CMB", "account:工行": "ICBC"}
        assert nest_id_field_map(flat) == {
            "asset_name": {"纸黄金": "PAPER"},
            "account": {"招行": "CMB", "工行": "ICBC"},
        }

    def test_malformed_map_key_skipped(self):
        assert nest_id_field_map({"no_colon_here": "X"}) == {}


class TestScanUnmappedIdFieldMapLabels:
    def test_unmapped_label_is_candidate(self):
        merged = {"account:招行": "CMB"}
        scanned = scan_unmapped_id_field_map_labels({"account": ["招行", "工行"]}, merged)
        by_label = {item["label"]: item for item in scanned}
        assert by_label["招行"]["mapped"] is True
        assert by_label["招行"]["code"] == "CMB"
        assert by_label["工行"]["mapped"] is False
        assert by_label["工行"]["code"] is None

    def test_blank_and_nan_values_excluded(self):
        scanned = scan_unmapped_id_field_map_labels({"account": ["", "  ", "nan", "None", "招行"]}, {})
        assert [item["label"] for item in scanned] == ["招行"]

    def test_duplicates_deduplicated(self):
        scanned = scan_unmapped_id_field_map_labels({"account": ["招行", "招行", "招行"]}, {})
        assert len(scanned) == 1


# ---------------------------------------------------------------------------
# GOLDEN TESTS — Gold / Insurance / RSU (WS-B gate, mirrors TestGoldenMeltParity)
# ---------------------------------------------------------------------------


class TestGoldenIdFieldMapParity:
    """Byte-identical id_template resolution: hardcoded YAML-default path ==
    DB-seeded (id_field_maps_override) path. Same safety-net principle as
    WS-A's TestGoldenMeltParity, but for id_template/canonical_id resolution
    (which happens inside read(), not transform() — see
    ConfigDrivenReader.__init__'s docstring)."""

    @pytest.mark.parametrize(
        "reader_key,yaml_path,fixture_path",
        [
            ("gold", GOLD_YAML, GOLD_FIXTURE),
            ("insurance", INSURANCE_YAML, INSURANCE_FIXTURE),
            ("rsu", RSU_YAML, RSU_FIXTURE),
        ],
    )
    def test_byte_identical_default_vs_db_seeded(self, tmp_path, reader_key, yaml_path, fixture_path):
        cfg = load_reader_config(yaml_path)

        # Path A: hardcoded YAML default (no override — legacy behavior).
        reader_a = ConfigDrivenReader(cfg)
        data_a = reader_a.read(fixture_path)
        holdings_a, transactions_a = reader_a.transform(data_a)

        # Path B: DB-seeded override (mirrors orchestrator wiring) — the V77
        # seed mirrors the YAML exactly, so this must be a no-op.
        connector = _make_db(tmp_path, name=f"{reader_key}_golden.duckdb")
        override = load_id_field_maps(connector, reader_key)
        connector.close()

        reader_b = ConfigDrivenReader(cfg, id_field_maps_override=override)
        data_b = reader_b.read(fixture_path)
        holdings_b, transactions_b = reader_b.transform(data_b)

        if not holdings_a.empty or not holdings_b.empty:
            pd.testing.assert_frame_equal(holdings_a, holdings_b)
        if not transactions_a.empty or not transactions_b.empty:
            pd.testing.assert_frame_equal(transactions_a, transactions_b)

    def test_db_override_changes_produced_asset_id_gold(self, tmp_path):
        """A DB override on an active id_field_map row must change the
        produced asset_id — the concrete effect of the UI-managed rename."""
        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET map_value = '{\"code\": \"CMBX\"}' "
            "WHERE reader_key = 'gold' AND mapping_kind = 'id_field_map' AND map_key = 'account:招行'"
        )
        override = load_id_field_maps(connector, "gold")
        connector.close()

        cfg = load_reader_config(GOLD_YAML)
        reader = ConfigDrivenReader(cfg, id_field_maps_override=override)
        data = reader.read(GOLD_FIXTURE)
        holdings_df, _ = reader.transform(data)

        asset_ids = set(holdings_df["asset_id"].unique())
        assert "GOLD_PAPER_CMBX" in asset_ids
        assert "GOLD_PAPER_CMB" not in asset_ids

    def test_db_override_changes_produced_asset_id_rsu(self, tmp_path):
        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET map_value = '{\"code\": \"AMZN2\"}' "
            "WHERE reader_key = 'rsu' AND mapping_kind = 'id_field_map' AND map_key = 'asset_name:Amazon RSU'"
        )
        override = load_id_field_maps(connector, "rsu")
        connector.close()

        cfg = load_reader_config(RSU_YAML)
        reader = ConfigDrivenReader(cfg, id_field_maps_override=override)
        data = reader.read(RSU_FIXTURE)
        _, transactions_df = reader.transform(data)

        asset_ids = set(transactions_df["asset_id"].unique())
        assert "RSU_AMZN2" in asset_ids
        assert "RSU_AMZN" not in asset_ids

    def test_new_db_mapping_changes_produced_asset_id_insurance(self, tmp_path):
        """Insurance has no YAML id_field_maps at all — a brand-new active
        mapping (not overriding a seed, since there is none) must still take
        effect: raw product_name passthrough -> mapped code."""
        connector = _make_db(tmp_path)
        connector.execute(
            "INSERT INTO reader_mappings (reader_key, mapping_kind, map_key, map_value, status) "
            "VALUES ('insurance', 'id_field_map', 'product_name:惠民定期重疾', '{\"code\": \"HMDQJ\"}', 'active')"
        )
        override = load_id_field_maps(connector, "insurance")
        connector.close()

        cfg = load_reader_config(INSURANCE_YAML)
        reader = ConfigDrivenReader(cfg, id_field_maps_override=override)
        data = reader.read(INSURANCE_FIXTURE)
        holdings_df, _ = reader.transform(data)

        asset_ids = set(holdings_df["asset_id"].unique())
        assert "INS_HMDQJ" in asset_ids
        assert "INS_惠民定期重疾" not in asset_ids

    def test_archived_row_removes_label_behaves_like_unknown_label(self, tmp_path):
        """Archiving a gold account mapping must fall back to raw-label
        passthrough (the engine's documented unknown-label behavior) — not an
        error, not a dropped row."""
        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET status = 'archived' "
            "WHERE reader_key = 'gold' AND mapping_kind = 'id_field_map' AND map_key = 'account:招行'"
        )
        override = load_id_field_maps(connector, "gold")
        connector.close()

        cfg = load_reader_config(GOLD_YAML)
        reader = ConfigDrivenReader(cfg, id_field_maps_override=override)
        data = reader.read(GOLD_FIXTURE)
        holdings_df, _ = reader.transform(data)

        asset_ids = set(holdings_df["asset_id"].unique())
        # Raw label "招行" is used verbatim (passthrough), matching the
        # engine's documented unknown-label behavior — not dropped, not an error.
        assert "GOLD_PAPER_招行" in asset_ids
        assert "GOLD_PAPER_CMB" not in asset_ids


# ---------------------------------------------------------------------------
# ADR-023 WS-C — Schwab/CN-fund vocabularies
# ---------------------------------------------------------------------------


class TestVocabSeedsMirrorHookConstants:
    """The reader_hooks module-level constants are re-exports of the
    mapping_seeds vocab data (single source of truth) — assert they never
    drift and keep the exact legacy names/shapes existing consumers use."""

    def test_known_etfs(self):
        from src.sources.reader_hooks import _SCHWAB_KNOWN_ETFS
        assert _SCHWAB_KNOWN_ETFS == set(SCHWAB_KNOWN_ETFS_SEED)

    def test_symbol_normalizations(self):
        from src.sources.reader_hooks import _SCHWAB_SYMBOL_NORMALIZATIONS
        assert _SCHWAB_SYMBOL_NORMALIZATIONS == SCHWAB_SYMBOL_NORMALIZATIONS_SEED

    def test_action_mapping(self):
        from src.sources.reader_hooks import _SCHWAB_ACTION_MAPPING
        assert _SCHWAB_ACTION_MAPPING == SCHWAB_ACTION_MAPPING_SEED

    def test_cn_fund_type_map(self):
        from src.sources.reader_hooks import _CN_FUND_TYPE_MAP
        assert _CN_FUND_TYPE_MAP == CN_FUND_TYPE_MAP_SEED

    def test_vocab_seeds_shape(self):
        assert set(VOCAB_SEEDS) == {"schwab", "cn_fund"}
        assert set(VOCAB_SEEDS["schwab"]) == {"known_etf", "symbol_norm", "action_map"}
        assert set(VOCAB_SEEDS["cn_fund"]) == {"type_map"}
        assert VOCAB_SEEDS["schwab"]["known_etf"]["QQQ"] == {"etf": True}
        assert VOCAB_SEEDS["schwab"]["symbol_norm"]["BRK/B"] == {"to": "BRK-B"}
        assert VOCAB_SEEDS["schwab"]["action_map"]["Buy"] == {"type": "buy"}
        assert VOCAB_SEEDS["cn_fund"]["type_map"]["申购"] == {"type": "buy"}

    def test_all_seed_types_in_allowed_enum(self):
        """Every seeded action_map/type_map target must be a member of the
        enum the API validates against — otherwise the API would reject
        re-saving a seed row unchanged."""
        for v in VOCAB_SEEDS["schwab"]["action_map"].values():
            assert v["type"] in ALLOWED_TRANSACTION_TYPES, v
        for v in VOCAB_SEEDS["cn_fund"]["type_map"].values():
            assert v["type"] in ALLOWED_TRANSACTION_TYPES, v


class TestMigrationV78:
    def test_seed_row_counts_per_reader_kind(self, tmp_path):
        connector = _make_db(tmp_path)
        for reader_key, kinds in VOCAB_SEEDS.items():
            for kind, seed in kinds.items():
                count = connector.execute(
                    "SELECT COUNT(*) FROM reader_mappings WHERE reader_key = ? AND mapping_kind = ?",
                    [reader_key, kind],
                ).fetchone()[0]
                assert count == len(seed), f"{reader_key}/{kind}: expected {len(seed)}, got {count}"
        connector.close()

    def test_seed_value_shapes(self, tmp_path):
        import json

        connector = _make_db(tmp_path)
        row = connector.execute(
            "SELECT map_value FROM reader_mappings WHERE reader_key = 'schwab' "
            "AND mapping_kind = 'known_etf' AND map_key = 'QQQ'"
        ).fetchone()
        assert row is not None and json.loads(row[0]) == {"etf": True}
        row = connector.execute(
            "SELECT map_value FROM reader_mappings WHERE reader_key = 'schwab' "
            "AND mapping_kind = 'symbol_norm' AND map_key = 'BRK/B'"
        ).fetchone()
        assert row is not None and json.loads(row[0]) == {"to": "BRK-B"}
        row = connector.execute(
            "SELECT map_value FROM reader_mappings WHERE reader_key = 'cn_fund' "
            "AND mapping_kind = 'type_map' AND map_key = '赎回'"
        ).fetchone()
        assert row is not None and json.loads(row[0]) == {"type": "sell"}
        connector.close()

    def test_seed_is_idempotent_on_rerun(self, tmp_path):
        connector = _make_db(tmp_path)
        count_before = connector.execute(
            "SELECT COUNT(*) FROM reader_mappings WHERE mapping_kind IN "
            "('known_etf', 'symbol_norm', 'action_map', 'type_map')"
        ).fetchone()[0]
        connector.run_migrations()
        count_after = connector.execute(
            "SELECT COUNT(*) FROM reader_mappings WHERE mapping_kind IN "
            "('known_etf', 'symbol_norm', 'action_map', 'type_map')"
        ).fetchone()[0]
        assert count_before == count_after == _VOCAB_SEED_COUNT
        connector.close()

    def test_migration_recorded(self, tmp_path):
        connector = _make_db(tmp_path)
        row = connector.execute("SELECT label FROM schema_version WHERE version = 78").fetchone()
        assert row is not None
        connector.close()


class TestLoadVocabMappings:
    def test_known_etf_matches_defaults_on_fresh_seeded_db(self, tmp_path):
        connector = _make_db(tmp_path)
        merged = load_reader_mappings(connector, "schwab", "known_etf")
        connector.close()
        assert set(merged.keys()) == set(SCHWAB_KNOWN_ETFS_SEED)
        assert all(v is True for v in merged.values())

    def test_symbol_norm_matches_defaults(self, tmp_path):
        connector = _make_db(tmp_path)
        merged = load_reader_mappings(connector, "schwab", "symbol_norm")
        connector.close()
        assert merged == SCHWAB_SYMBOL_NORMALIZATIONS_SEED

    def test_action_map_matches_defaults(self, tmp_path):
        connector = _make_db(tmp_path)
        merged = load_reader_mappings(connector, "schwab", "action_map")
        connector.close()
        assert merged == SCHWAB_ACTION_MAPPING_SEED

    def test_type_map_matches_defaults(self, tmp_path):
        connector = _make_db(tmp_path)
        merged = load_reader_mappings(connector, "cn_fund", "type_map")
        connector.close()
        assert merged == CN_FUND_TYPE_MAP_SEED

    def test_db_override_wins(self, tmp_path):
        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET map_value = '{\"type\": \"dividend\"}' "
            "WHERE reader_key = 'schwab' AND mapping_kind = 'action_map' AND map_key = 'Credit Interest'"
        )
        merged = load_reader_mappings(connector, "schwab", "action_map")
        connector.close()
        assert merged["Credit Interest"] == "dividend"
        assert merged["Buy"] == "buy"  # siblings unaffected

    def test_archived_row_removes_entry(self, tmp_path):
        """An archived vocab row REMOVES the key from the merged dict — the
        consuming hook then applies its documented unknown handling."""
        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET status = 'archived' "
            "WHERE reader_key = 'schwab' AND mapping_kind = 'known_etf' AND map_key = 'SGOV'"
        )
        merged = load_reader_mappings(connector, "schwab", "known_etf")
        connector.close()
        assert "SGOV" not in merged
        assert "QQQ" in merged

    def test_missing_table_falls_back_to_defaults(self, tmp_path):
        db_path = tmp_path / "premigration_vocab.duckdb"
        connector = DatabaseConnector(str(db_path))
        initialize_schema(connector)  # base schema.sql only — no migrations run
        merged = load_reader_mappings(connector, "cn_fund", "type_map")
        connector.close()
        assert merged == CN_FUND_TYPE_MAP_SEED


class TestScanUnmappedVocabValues:
    def test_mapped_and_unmapped(self):
        merged = {"Buy": "buy", "Sell": "sell"}
        scanned = scan_unmapped_vocab_values(["Buy", "Journal Fee"], merged, "action_map")
        by_value = {i["value"]: i for i in scanned}
        assert by_value["Buy"]["mapped"] is True
        assert by_value["Buy"]["mapped_value"] == {"type": "buy"}
        assert by_value["Journal Fee"]["mapped"] is False
        assert by_value["Journal Fee"]["mapped_value"] is None

    def test_known_etf_value_shape(self):
        scanned = scan_unmapped_vocab_values(["QQQ"], {"QQQ": True}, "known_etf")
        assert scanned[0]["mapped_value"] == {"etf": True}

    def test_symbol_norm_value_shape(self):
        scanned = scan_unmapped_vocab_values(["BRK/B"], {"BRK/B": "BRK-B"}, "symbol_norm")
        assert scanned[0]["mapped_value"] == {"to": "BRK-B"}

    def test_dedup_and_blank_excluded(self):
        scanned = scan_unmapped_vocab_values(["申购", "申购", "", "  ", "nan"], {}, "type_map")
        assert [i["value"] for i in scanned] == ["申购"]


# ---------------------------------------------------------------------------
# GOLDEN TESTS — Schwab / CN Fund vocab (WS-C gate, mirrors the WS-A/WS-B gates)
# ---------------------------------------------------------------------------


def _read_schwab_txn_fixture():
    return pd.read_csv(SCHWAB_TXN_FIXTURE)


def _read_cn_fund_txn_fixture():
    return pd.read_excel(CN_FUND_FIXTURE, sheet_name="基金交易记录")


class TestGoldenVocabParity:
    """Byte-identical hook output: hardcoded module-default path (metadata
    without vocab keys) == DB-seeded metadata path. The vocab constants are
    consumed at TRANSFORM time (schwab_transactions_from_csv /
    schwab_holdings_from_csv / cn_fund_transactions_from_sheet hooks +
    ibkr hooks via _schwab_normalize_to_canonical_id), so metadata injection
    is the correct mechanism — nothing here runs inside read() (unlike
    WS-B's id_field_map)."""

    def test_schwab_transactions_byte_identical_default_vs_db_seeded(self, tmp_path):
        from src.sources.reader_hooks import schwab_transactions_from_csv

        raw = _read_schwab_txn_fixture()
        result_default = schwab_transactions_from_csv(raw.copy(), {})

        connector = _make_db(tmp_path)
        metadata = {
            "schwab_known_etf": set(load_reader_mappings(connector, "schwab", "known_etf").keys()),
            "schwab_symbol_norm": load_reader_mappings(connector, "schwab", "symbol_norm"),
            "schwab_action_map": load_reader_mappings(connector, "schwab", "action_map"),
        }
        connector.close()
        result_db = schwab_transactions_from_csv(raw.copy(), metadata)

        assert not result_default.empty
        pd.testing.assert_frame_equal(
            result_default.reset_index(drop=True), result_db.reset_index(drop=True)
        )

    def test_schwab_holdings_byte_identical_default_vs_db_seeded(self, tmp_path):
        from unittest.mock import patch

        from src.sources.reader_hooks import schwab_holdings_from_csv

        raw = pd.read_csv(
            FIXTURE_DIR / "Individual-Positions-2026-05-23-060406.csv", skiprows=2
        )
        connector = _make_db(tmp_path)
        symbol_norm = load_reader_mappings(connector, "schwab", "symbol_norm")
        connector.close()

        # Pin the live FX fetch so both paths see the same cash-row rate.
        with patch(
            "src.market_data.fetchers.yfinance_fetcher.fetch_fx_rates",
            return_value={"USD": 7.0, "HKD": 0.9},
        ):
            result_default = schwab_holdings_from_csv(raw.copy(), {})
            result_db = schwab_holdings_from_csv(raw.copy(), {"schwab_symbol_norm": symbol_norm})

        assert not result_default.empty
        pd.testing.assert_frame_equal(
            result_default.reset_index(drop=True), result_db.reset_index(drop=True)
        )

    def test_cn_fund_transactions_byte_identical_default_vs_db_seeded(self, tmp_path):
        from src.sources.reader_hooks import cn_fund_transactions_from_sheet

        raw = _read_cn_fund_txn_fixture()
        result_default = cn_fund_transactions_from_sheet(raw.copy(), {})

        connector = _make_db(tmp_path)
        type_map = load_reader_mappings(connector, "cn_fund", "type_map")
        connector.close()
        result_db = cn_fund_transactions_from_sheet(raw.copy(), {"cn_fund_type_map": type_map})

        assert not result_default.empty
        pd.testing.assert_frame_equal(
            result_default.reset_index(drop=True), result_db.reset_index(drop=True)
        )

    def test_db_override_changes_schwab_action_type(self, tmp_path):
        """A DB override on an action_map row must change the produced
        transaction_type — the concrete effect of the UI-managed edit."""
        from src.sources.reader_hooks import schwab_transactions_from_csv

        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET map_value = '{\"type\": \"interest\"}' "
            "WHERE reader_key = 'schwab' AND mapping_kind = 'action_map' AND map_key = 'Credit Interest'"
        )
        metadata = {"schwab_action_map": load_reader_mappings(connector, "schwab", "action_map")}
        connector.close()

        raw = _read_schwab_txn_fixture()
        result = schwab_transactions_from_csv(raw, metadata)
        interest_rows = result[result["transaction_type"] == "interest"]
        assert not interest_rows.empty
        # Credit Interest rows carry Schwab's account-tagged interest description.
        assert (interest_rows["description"].str.contains("INT", case=False)).all()
        # Default path types these same rows 'other' — the override moved them.
        result_default = schwab_transactions_from_csv(_read_schwab_txn_fixture(), {})
        assert not (result_default["transaction_type"] == "interest").any()

    def test_db_override_changes_etf_classification(self, tmp_path):
        """Adding a new known_etf row must flip that ticker's transaction
        asset_id from US_STK_* to US_ETF_*. AAPL is a stock in the fixture
        (persona.schwab.positions — Buy AAPL is one of the required rows)."""
        from src.sources.reader_hooks import schwab_transactions_from_csv

        connector = _make_db(tmp_path)
        connector.execute(
            "INSERT INTO reader_mappings (reader_key, mapping_kind, map_key, map_value, status) "
            "VALUES ('schwab', 'known_etf', 'AAPL', '{\"etf\": true}', 'active')"
        )
        metadata = {
            "schwab_known_etf": set(load_reader_mappings(connector, "schwab", "known_etf").keys()),
        }
        connector.close()

        raw = _read_schwab_txn_fixture()
        result = schwab_transactions_from_csv(raw, metadata)
        asset_ids = set(result["asset_id"].unique())
        assert "US_ETF_AAPL" in asset_ids
        assert "US_STK_AAPL" not in asset_ids

    def test_db_override_changes_cn_fund_type(self, tmp_path):
        from src.sources.reader_hooks import cn_fund_transactions_from_sheet

        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET map_value = '{\"type\": \"transfer_in\"}' "
            "WHERE reader_key = 'cn_fund' AND mapping_kind = 'type_map' AND map_key = '活期宝即充即用'"
        )
        metadata = {"cn_fund_type_map": load_reader_mappings(connector, "cn_fund", "type_map")}
        connector.close()

        raw = _read_cn_fund_txn_fixture()
        result_default = cn_fund_transactions_from_sheet(raw.copy(), {})
        result_db = cn_fund_transactions_from_sheet(raw.copy(), metadata)

        # The fixture has 活期宝即充即用 rows (default 'buy') — override moves them.
        mask = raw["操作类型"] == "活期宝即充即用"
        assert mask.any()
        assert (result_default.loc[mask.values, "transaction_type"] == "buy").all()
        assert (result_db.loc[mask.values, "transaction_type"] == "transfer_in").all()

    def test_archived_known_etf_behaves_as_unknown_ticker(self, tmp_path):
        """Archiving VOO's known_etf row: the ticker falls back to the
        unknown-ticker default — US_STK_VOO (exactly today's behavior for
        any ticker not in the list). Not an error, not a dropped row.
        VOO is persona.schwab.positions[0], a required Buy row."""
        from src.sources.reader_hooks import schwab_transactions_from_csv

        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET status = 'archived' "
            "WHERE reader_key = 'schwab' AND mapping_kind = 'known_etf' AND map_key = 'VOO'"
        )
        metadata = {
            "schwab_known_etf": set(load_reader_mappings(connector, "schwab", "known_etf").keys()),
        }
        connector.close()

        raw = _read_schwab_txn_fixture()
        result = schwab_transactions_from_csv(raw, metadata)
        asset_ids = set(result["asset_id"].unique())
        assert "US_STK_VOO" in asset_ids
        assert "US_ETF_SGOV" not in asset_ids
        # Other ETFs unaffected.
        assert "US_ETF_QQQ" in asset_ids

    def test_archived_action_behaves_as_unknown_action(self, tmp_path):
        """Archiving an action_map row: that action falls back to 'other'
        (exactly today's behavior for any unknown action string)."""
        from src.sources.reader_hooks import schwab_transactions_from_csv

        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET status = 'archived' "
            "WHERE reader_key = 'schwab' AND mapping_kind = 'action_map' AND map_key = 'NRA Tax Adj'"
        )
        metadata = {"schwab_action_map": load_reader_mappings(connector, "schwab", "action_map")}
        connector.close()

        raw = _read_schwab_txn_fixture()
        result_default = schwab_transactions_from_csv(raw.copy(), {})
        result_db = schwab_transactions_from_csv(raw.copy(), metadata)

        assert (result_default["transaction_type"] == "tax_adjustment").any()
        assert not (result_db["transaction_type"] == "tax_adjustment").any()
        # The rows are still present, typed 'other' — not dropped.
        assert len(result_db) == len(result_default)

    def test_archived_type_map_row_behaves_as_unknown_type(self, tmp_path):
        """Archiving cn_fund's 快速取现 row: those rows fall back to 'other'
        (exactly today's .get(raw_type, 'other') / .fillna('other') path)."""
        from src.sources.reader_hooks import cn_fund_transactions_from_sheet

        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET status = 'archived' "
            "WHERE reader_key = 'cn_fund' AND mapping_kind = 'type_map' AND map_key = '快速取现'"
        )
        metadata = {"cn_fund_type_map": load_reader_mappings(connector, "cn_fund", "type_map")}
        connector.close()

        raw = _read_cn_fund_txn_fixture()
        result_default = cn_fund_transactions_from_sheet(raw.copy(), {})
        result_db = cn_fund_transactions_from_sheet(raw.copy(), metadata)

        mask = (raw["操作类型"] == "快速取现").values
        assert mask.any()
        assert (result_default.loc[mask, "transaction_type"] == "sell").all()
        assert (result_db.loc[mask, "transaction_type"] == "other").all()
        assert len(result_db) == len(result_default)


# ---------------------------------------------------------------------------
# Attribution & Flows WS-3.1 (V79) — Security Transfer typing
# ---------------------------------------------------------------------------


def _security_transfer_txn_df(quantity: float) -> pd.DataFrame:
    """A single raw Schwab transactions row shaped like _read_schwab_txn_fixture()
    (same columns as the real CSV), with Action='Security Transfer'."""
    return pd.DataFrame([{
        "Date": "06/09/2026",
        "Action": "Security Transfer",
        "Symbol": "VOO",
        "Description": "TRANSFER OF SECURITY",
        "Quantity": quantity,
        "Price": "$0.00",
        "Fees & Comm": "",
        "Amount": "$0.00",
    }])


class TestSecurityTransferTyping:
    """WS-3.1: 'Security Transfer' is a directionally-ambiguous Schwab action —
    action_map maps it to the pseudo-type 'transfer', resolved by quantity
    sign inside schwab_transactions_from_csv (never persisted as literal
    'transfer'). Uses the code-default path (metadata={}) — mapping_seeds.py
    already seeds 'Security Transfer': 'transfer' directly (V79 mirrors it)."""

    def test_negative_quantity_resolves_to_transfer_out(self):
        result = schwab_transactions_from_csv(_security_transfer_txn_df(-21.0), {})
        assert len(result) == 1
        assert result.iloc[0]["transaction_type"] == "transfer_out"
        assert result.iloc[0]["quantity"] == -21.0

    def test_positive_quantity_resolves_to_transfer_in(self):
        result = schwab_transactions_from_csv(_security_transfer_txn_df(21.0), {})
        assert len(result) == 1
        assert result.iloc[0]["transaction_type"] == "transfer_in"
        assert result.iloc[0]["quantity"] == 21.0

    def test_never_emits_literal_transfer_pseudo_type(self):
        for qty in (-21.0, 21.0, 0.0):
            result = schwab_transactions_from_csv(_security_transfer_txn_df(qty), {})
            assert not (result["transaction_type"] == "transfer").any()

    def test_unmapped_action_still_falls_back_to_other(self):
        """Regression guard: an action with no action_map entry must still
        resolve to 'other' — the pseudo-type resolution must not fire for
        unrelated unmapped actions."""
        df = _security_transfer_txn_df(-5.0)
        df.loc[0, "Action"] = "Totally Unknown Action"
        result = schwab_transactions_from_csv(df, {})
        assert result.iloc[0]["transaction_type"] == "other"

    def test_existing_mappings_unchanged_byte_identical(self):
        """Byte-identical philosophy: adding 'Security Transfer' must not
        perturb the output for any OTHER action in the same file."""
        raw = _read_schwab_txn_fixture()
        result = schwab_transactions_from_csv(raw.copy(), {})
        # None of the real fixture's actions are 'Security Transfer', so this
        # is effectively byte-identical to pre-WS-3.1 output for this fixture.
        assert not (result["transaction_type"] == "transfer").any()
        assert not result.empty

    def test_db_seeded_path_matches_code_default(self, tmp_path):
        """The V79-seeded DB action_map row produces the same resolution as
        the code-default path (mapping_seeds.py's SCHWAB_ACTION_MAPPING_SEED)."""
        connector = _make_db(tmp_path)
        metadata = {"schwab_action_map": load_reader_mappings(connector, "schwab", "action_map")}
        connector.close()

        result_default = schwab_transactions_from_csv(_security_transfer_txn_df(-21.0), {})
        result_db = schwab_transactions_from_csv(_security_transfer_txn_df(-21.0), metadata)
        pd.testing.assert_frame_equal(
            result_default.reset_index(drop=True), result_db.reset_index(drop=True)
        )


class TestAllowedTransactionTypesAcceptsTransferPseudoType:
    """'transfer' must be a valid action_map/type_map API-write target (the
    ALLOWED_TRANSACTION_TYPES enum both the API validation gate and the
    frontend dropdown draw from) — and a genuinely invalid value must still
    be rejected."""

    def test_transfer_is_allowed(self):
        assert "transfer" in ALLOWED_TRANSACTION_TYPES

    def test_bogus_type_still_rejected(self):
        assert "not_a_real_type" not in ALLOWED_TRANSACTION_TYPES

    def test_seeded_security_transfer_row_type_is_allowed(self):
        assert VOCAB_SEEDS["schwab"]["action_map"]["Security Transfer"] == {"type": "transfer"}
        assert VOCAB_SEEDS["schwab"]["action_map"]["Security Transfer"]["type"] in ALLOWED_TRANSACTION_TYPES


# ===========================================================================
# ie_column — 月度收支 column semantics (plan 2026-08-01 WS-A, migration V82)
# ===========================================================================


class TestIeColumnSeedShape:
    """The seed is the contract src.services.investment_contributions reads.
    Every one of these assertions is a bug class that has bitten this project:
    an out-of-vocabulary value, an invested column with nowhere to land, a
    native-currency sibling silently summed, or a second income total."""

    def test_every_value_uses_the_declared_vocabulary(self):
        for map_key, spec in IE_COLUMN_SEED.items():
            assert isinstance(spec, IEColumn), map_key
            assert spec.role in IE_ROLES, f"{map_key}: bad role {spec.role!r}"
            assert spec.currency in IE_CURRENCIES, f"{map_key}: bad currency {spec.currency!r}"
            allowed = IE_ROLE_BUCKETS[spec.role]
            assert spec.bucket is None or spec.bucket in allowed, (
                f"{map_key}: bucket {spec.bucket!r} not valid for role {spec.role!r}"
            )

    def test_every_invested_column_has_a_destination_bucket(self):
        """An invested column with no bucket contributes to no destination and
        would vanish out of gross_invested — the exact silent failure WS-A
        exists to remove."""
        for map_key, spec in IE_COLUMN_SEED.items():
            if spec.role == "invested":
                assert spec.bucket, f"{map_key}: role='invested' with no bucket"

    def test_no_excel_aggregate_is_ever_summed(self):
        """Supersedes the old "exactly one total_income column" guard.

        That guard existed because the income basis was READ from the Excel's
        own 总收入合计 (bucket='total_income'), so a second such column would
        have double-counted income. The owner retired that design on
        2026-08-01 — no Excel-computed aggregate may be a calculation input at
        all — so the invariant is now stronger and blunter: every 合计/支出/
        理财 aggregate is role='computed', and `computed` carries no bucket and
        is summed by nothing (src/services/ie_ledger.py). The retired bucket
        must never reappear on any row.
        """
        aggregates = [
            "主动收入合计", "被动收入合计", "总收入合计",
            "必要支出", "非必要支出", "工作支出", "理财", "总支出",
        ]
        for map_key in aggregates:
            spec = IE_COLUMN_SEED[map_key]
            assert spec.role == "computed", f"{map_key} must never be a calculation input"
            assert spec.bucket is None, f"{map_key}: a computed aggregate carries no bucket"
            assert spec.validates, f"{map_key}: a computed aggregate must declare its cross-check"
        assert IE_ROLE_BUCKETS["computed"] == frozenset()
        assert all(s.bucket != "total_income" for s in IE_COLUMN_SEED.values()), (
            "the 'total_income' bucket was retired on 2026-08-01 — the income basis is the "
            "sum of the income LEAF columns, never an Excel aggregate"
        )

    def test_every_usd_suffixed_column_is_currency_usd(self):
        """Rule 2 at the ledger layer: a native-currency sibling must never be
        summed. currency='USD' is the mechanism that guarantees it."""
        for map_key, spec in IE_COLUMN_SEED.items():
            if map_key.endswith("_USD"):
                assert spec.currency == "USD", f"{map_key} is a native-currency sibling"

    def test_no_seeded_map_key_has_stray_whitespace(self):
        for map_key in IE_COLUMN_SEED:
            assert map_key == map_key.strip(), f"{map_key!r} has stray whitespace"

    def test_settled_semantics_2026_08_01(self):
        """Owner session 2026-08-01 (plan §1) — these four are the acceptance
        criteria of the workstream, not incidental values."""
        assert IE_COLUMN_SEED["投资理财_股票基金_IBKR"] == IEColumn("invested", "us_ibkr", "CNY")
        assert IE_COLUMN_SEED["投资理财_股票基金_IBKR_USD"] == IEColumn("invested", "us_ibkr", "USD")
        # NOT a redemption: the principal entered the ledger as RSU income, never
        # as a 投资理财 column, so subtracting it would double-subtract (ADR-025 §4b).
        assert IE_COLUMN_SEED["收入_被动收入_股票卖出收益"].role == "income"
        assert IE_COLUMN_SEED["收入_被动收入_股票卖出收益"].role != "redemption"
        assert IE_COLUMN_SEED["收入_被动收入_股票卖出收益_USD"].currency == "USD"

    def test_schwab_usd_is_never_summable(self):
        """ADR-025 §3: Schawab == Schawab_USD x FX, every month."""
        assert IE_COLUMN_SEED["投资理财_股票基金_Schawab"] == IEColumn("invested", "us_schwab", "CNY")
        assert IE_COLUMN_SEED["投资理财_股票基金_Schawab_USD"].currency == "USD"

    def test_pre_v82_hardcoded_columns_keep_their_meaning(self):
        """The six literals investment_contributions.py used to hardcode, plus
        the income total — a behaviour-preserving refactor means these cannot
        move. (The income total moved exactly once, deliberately: V84 retired
        it as a calculation input — see test_no_excel_aggregate_is_ever_summed.)
        """
        assert IE_COLUMN_SEED["投资理财_股票基金_天天基金"] == IEColumn("invested", "cn_fund", "CNY")
        assert IE_COLUMN_SEED["投资理财_黄金_招行纸黄金"] == IEColumn("invested", "gold", "CNY")
        assert IE_COLUMN_SEED["投资理财_黄金_黄金ETF"] == IEColumn("invested", "gold", "CNY")
        assert IE_COLUMN_SEED["投资理财_银行理财_招行"] == IEColumn("invested", "bank_wealth", "CNY")
        assert IE_COLUMN_SEED["收入_被动收入_基金赎回"].role == "redemption"
        assert IE_COLUMN_SEED["收入_被动收入_黄金卖出"].role == "redemption"
        assert IE_COLUMN_SEED["收入_被动收入_银行理财"].role == "redemption"
        assert IE_COLUMN_SEED["总收入合计"].role == "computed"

    def test_gram_quantity_column_is_reference_not_money(self):
        """收入_被动收入_黄金卖出(克) is a WEIGHT, not CNY — summing it into
        redemptions alongside 收入_被动收入_黄金卖出 would add grams to yuan."""
        assert IE_COLUMN_SEED["收入_被动收入_黄金卖出(克)"].role == "reference"


class TestMigrationV82:
    def test_seed_row_count(self, tmp_path):
        connector = _make_db(tmp_path)
        count = connector.execute(
            "SELECT COUNT(*) FROM reader_mappings WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'ie_column' AND status = 'active'"
        ).fetchone()[0]
        assert count == _IE_COLUMN_SEED_COUNT
        connector.close()

    def test_seed_value_shape(self, tmp_path):
        import json

        connector = _make_db(tmp_path)
        row = connector.execute(
            "SELECT map_value FROM reader_mappings WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'ie_column' AND map_key = '投资理财_股票基金_IBKR'"
        ).fetchone()
        assert row is not None
        assert json.loads(row[0]) == {"role": "invested", "bucket": "us_ibkr", "currency": "CNY"}
        row = connector.execute(
            "SELECT map_value FROM reader_mappings WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'ie_column' AND map_key = '收入_被动收入_股票卖出收益'"
        ).fetchone()
        assert row is not None
        # A LEAF also carries its `group` (which Excel subtotal it belongs to) —
        # the tag the aggregate cross-check matches on, so a column rename can
        # never break a check.
        assert json.loads(row[0]) == {
            "role": "income", "bucket": None, "currency": "CNY", "group": "passive_income",
        }
        # A `computed` aggregate carries `validates` instead of `group`.
        row = connector.execute(
            "SELECT map_value FROM reader_mappings WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'ie_column' AND map_key = '总支出'"
        ).fetchone()
        assert row is not None
        value = json.loads(row[0])
        assert value["role"] == "computed"
        assert value["bucket"] is None
        assert "group" not in value
        # ⚠️ the Excel's 总支出 bundles 理财 (investment) in with the expense
        # groups — dropping `roles: [invested]` here would silently move every
        # "total expense" figure the Cash Flow tab has ever shown.
        assert value["validates"]["roles"] == ["invested"]
        assert set(value["validates"]["groups"]) == {
            "essential_expense", "discretionary_expense", "work_expense",
        }
        connector.close()

    def test_seed_is_idempotent_on_rerun(self, tmp_path):
        connector = _make_db(tmp_path)
        count_before = connector.execute(
            "SELECT COUNT(*) FROM reader_mappings WHERE mapping_kind = 'ie_column'"
        ).fetchone()[0]
        connector.run_migrations()
        count_after = connector.execute(
            "SELECT COUNT(*) FROM reader_mappings WHERE mapping_kind = 'ie_column'"
        ).fetchone()[0]
        assert count_before == count_after == _IE_COLUMN_SEED_COUNT
        connector.close()

    def test_does_not_touch_fs_column_rows(self, tmp_path):
        connector = _make_db(tmp_path)
        count = connector.execute(
            "SELECT COUNT(*) FROM reader_mappings WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'fs_column'"
        ).fetchone()[0]
        assert count == _TOTAL_SEED_COUNT
        connector.close()

    def test_migration_recorded(self, tmp_path):
        connector = _make_db(tmp_path)
        row = connector.execute("SELECT label FROM schema_version WHERE version = 82").fetchone()
        assert row is not None
        connector.close()


class TestLoadIeColumnMappings:
    def test_matches_defaults_on_fresh_seeded_db(self, tmp_path):
        connector = _make_db(tmp_path)
        merged = load_reader_mappings(connector, "financial_summary", "ie_column")
        assert merged == _expected_fresh_db_ie_column()
        connector.close()

    def test_defaults_returned_when_table_missing(self, tmp_path):
        """Schema-only DB (no migrations): the loader must still return the code
        defaults so the ledger math never silently zeroes out."""
        connector = DatabaseConnector(":memory:")
        initialize_schema(connector)
        merged = load_reader_mappings(connector, "financial_summary", "ie_column")
        assert merged == _get_defaults()[("financial_summary", "ie_column")]
        connector.close()

    def test_db_override_wins(self, tmp_path):
        """The point of the workstream: a semantics change is a DB edit, not a
        code edit."""
        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET map_value = ? WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'ie_column' AND map_key = '投资理财_股票基金_天天基金'",
            ['{"role": "invested", "bucket": "gold", "currency": "CNY"}'],
        )
        merged = load_reader_mappings(connector, "financial_summary", "ie_column")
        assert merged["投资理财_股票基金_天天基金"] == IEColumn("invested", "gold", "CNY")
        connector.close()

    def test_archived_row_removes_the_column(self, tmp_path):
        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET status = 'archived' WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'ie_column' AND map_key = '投资理财_黄金_黄金ETF'"
        )
        merged = load_reader_mappings(connector, "financial_summary", "ie_column")
        assert "投资理财_黄金_黄金ETF" not in merged
        connector.close()

    def test_malformed_override_is_skipped_not_fatal(self, tmp_path):
        """A row missing `role` is a malformed override — the loader logs and
        keeps the code default rather than handing a half-decoded value to the
        ledger math."""
        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET map_value = ? WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'ie_column' AND map_key = '总收入合计'",
            ['{"bucket": "total_income"}'],
        )
        merged = load_reader_mappings(connector, "financial_summary", "ie_column")
        assert merged["总收入合计"] == IE_COLUMN_SEED["总收入合计"]
        connector.close()


class TestIeColumnPassThroughRole:
    """ADR-025 Amendment 2026-08-01 (WS-G) — 报销 (income side) and 工作开支
    (expense side) are the two ends of ONE round trip: the owner fronts a work
    expense and is repaid. Both carry role='pass_through' and are excluded from
    BOTH bases.

    Supersedes the short-lived `reimbursement` role (2026-08-01, same day),
    which excluded only the income half. Every assertion below is the same
    invariant that role encoded, re-expressed on the pair.
    """

    def test_role_is_pass_through_not_income(self):
        assert IE_COLUMN_SEED["收入_主动收入_报销"] == IEColumn(
            "pass_through", "inflow", "CNY", "active_income"
        )
        assert IE_COLUMN_SEED["收入_主动收入_报销"].role != "income", (
            "repayment of money already fronted is not earnings"
        )

    def test_pass_through_is_not_a_redemption(self):
        """The reason it is not simply tagged 'redemption': that role also
        subtracts from the investment NUMERATOR
        (net_external = max(invested − redeemed, 0)), which would punish a
        repayment as if it were money taken back out of an investment."""
        assert IE_COLUMN_SEED["收入_主动收入_报销"].role != "redemption"
        assert "pass_through" in IE_ROLES
        assert "reimbursement" not in IE_ROLES, "retired 2026-08-01 in favour of pass_through"
        # The bucket names WHICH END of the round trip a column is — it is never
        # an investment destination.
        assert IE_ROLE_BUCKETS["pass_through"] == IE_PASS_THROUGH_BUCKETS
        assert not (IE_PASS_THROUGH_BUCKETS & IE_DESTINATION_BUCKETS)

    def test_pass_through_is_exactly_the_two_ends_of_one_round_trip(self):
        """Exactly two columns are classified this way today, one per end —
        the pairing is what makes the exclusion structural rather than two
        unrelated exclusions a future editor could half-fix. 公积金
        (housing-fund withdrawal) and 其他偶然 (bonus) are owner-confirmed
        income and stay in the basis."""
        by_bucket = {
            s.bucket: k for k, s in IE_COLUMN_SEED.items() if s.role == "pass_through"
        }
        assert by_bucket == {
            "inflow": "收入_主动收入_报销",
            "outflow": "工作开支_出差/团建（全额报销）",
        }
        assert IE_COLUMN_SEED["收入_主动收入_公积金"].role == "income"
        assert IE_COLUMN_SEED["收入_主动收入_其他偶然"].role == "income"

    def test_the_column_containing_the_same_word_is_the_OUTFLOW_end(self):
        """'工作开支_出差/团建（全额报销）' contains 报销 but is the money going
        OUT — a name-substring rule would have tagged it as the inflow half and
        cancelled the pair against itself, which is why roles and buckets are
        per-column data, not pattern matching."""
        spec = IE_COLUMN_SEED["工作开支_出差/团建（全额报销）"]
        assert spec.role == "pass_through"
        assert spec.bucket == "outflow"
        assert spec.group == "work_expense", "still inside the Excel's 工作支出 subtotal"


def _rearm_migration_gate(connector, version: int) -> None:
    """Move a schema_version marker out of the way so run_migrations() re-runs
    that one migration. Uses an UPDATE rather than a destructive row-removal
    statement — the project's PreToolUse DB-safety guard forbids those, and a
    test has no business modelling one. tmp_path DB only."""
    connector.execute(
        "UPDATE schema_version SET version = ? WHERE version = ?", [version * 100, version]
    )


class TestIeColumnRoleHealMigrations:
    """V83 → V84 → V85: V82's natural-key seed is a no-op on a DB that already
    holds the row, so every classification the owner changed AFTER V82 shipped
    has to be healed in place on an already-seeded DB.

    All three ran on 2026-08-01, in the order the owner's rulings landed:
      V83 — 报销 'income' -> 'reimbursement'   (a role retired hours later)
      V84 — 总收入合计 'income'/'total_income' -> 'computed'
            (no Excel aggregate may be a calculation input, WS-E)
      V85 — 报销 + 工作开支 -> 'pass_through' inflow/outflow (WS-G), which
            supersedes V83

    A fresh DB needs none of them (IE_COLUMN_SEED already carries the final
    values); the tests that re-arm a gate model the already-deployed DB.
    """

    def test_seeded_values_are_the_final_ones(self, tmp_path):
        import json

        connector = _make_db(tmp_path)
        rows = dict(connector.execute(
            "SELECT map_key, map_value FROM reader_mappings WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'ie_column'"
        ).fetchall())
        assert json.loads(rows["收入_主动收入_报销"]) == {
            "role": "pass_through", "bucket": "inflow", "currency": "CNY", "group": "active_income",
        }
        assert json.loads(rows["工作开支_出差/团建（全额报销）"]) == {
            "role": "pass_through", "bucket": "outflow", "currency": "CNY", "group": "work_expense",
        }
        assert json.loads(rows["总收入合计"])["role"] == "computed"
        connector.close()

    def test_heals_a_db_seeded_with_the_pre_amendment_role(self, tmp_path):
        """The V82-era DB: 报销 tagged as plain income, 工作开支 as plain
        expense. Both ends must end up paired."""
        import json

        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET map_value = ? WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'ie_column' AND map_key = '收入_主动收入_报销'",
            [json.dumps({"role": "income", "bucket": None, "currency": "CNY"}, ensure_ascii=False)],
        )
        connector.execute(
            "UPDATE reader_mappings SET map_value = ? WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'ie_column' AND map_key = '工作开支_出差/团建（全额报销）'",
            [json.dumps({"role": "expense", "bucket": None, "currency": "CNY"}, ensure_ascii=False)],
        )
        _rearm_migration_gate(connector, 83)
        _rearm_migration_gate(connector, 85)
        connector.run_migrations()
        merged = load_reader_mappings(connector, "financial_summary", "ie_column")
        assert merged["收入_主动收入_报销"].role == "pass_through"
        assert merged["收入_主动收入_报销"].bucket == "inflow"
        assert merged["工作开支_出差/团建（全额报销）"].role == "pass_through"
        assert merged["工作开支_出差/团建（全额报销）"].bucket == "outflow"
        connector.close()

    def test_heals_a_db_left_on_the_short_lived_reimbursement_role(self, tmp_path):
        """A DB that applied V83 before V85 existed carries a role name no
        longer in IE_ROLES — ie_ledger would warn and count it in NOTHING, so
        the heal is what keeps that column classified at all."""
        import json

        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET map_value = ? WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'ie_column' AND map_key = '收入_主动收入_报销'",
            [json.dumps(
                {"role": "reimbursement", "bucket": None, "currency": "CNY"}, ensure_ascii=False
            )],
        )
        _rearm_migration_gate(connector, 85)
        connector.run_migrations()
        merged = load_reader_mappings(connector, "financial_summary", "ie_column")
        assert merged["收入_主动收入_报销"] == IEColumn(
            "pass_through", "inflow", "CNY", "active_income"
        )
        connector.close()

    def test_v84_heals_the_excel_income_total_out_of_the_calculation(self, tmp_path):
        """The WS-E ruling as a migration: a DB seeded when 总收入合计 was the
        savings-rate denominator (role='income', bucket='total_income') must
        stop feeding it."""
        import json

        connector = _make_db(tmp_path)
        connector.execute(
            "UPDATE reader_mappings SET map_value = ? WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'ie_column' AND map_key = '总收入合计'",
            [json.dumps(
                {"role": "income", "bucket": "total_income", "currency": "CNY"}, ensure_ascii=False
            )],
        )
        _rearm_migration_gate(connector, 84)
        connector.run_migrations()
        merged = load_reader_mappings(connector, "financial_summary", "ie_column")
        assert merged["总收入合计"].role == "computed"
        assert merged["总收入合计"].bucket is None
        connector.close()

    def test_does_not_overwrite_an_owner_edit(self, tmp_path):
        """ADR-023's model is that DB rows override code, never the reverse — a
        column the owner has re-classified in the UI must survive every heal.
        Each migration is a guarded UPDATE matching the EXACT prior seed value,
        so an owner value matches none of them."""
        import json

        connector = _make_db(tmp_path)
        owner_value = json.dumps(
            {"role": "expense", "bucket": None, "currency": "CNY"}, ensure_ascii=False
        )
        connector.execute(
            "UPDATE reader_mappings SET map_value = ? WHERE reader_key = 'financial_summary' "
            "AND mapping_kind = 'ie_column' AND map_key = '收入_主动收入_报销'",
            [owner_value],
        )
        for version in (83, 84, 85):
            _rearm_migration_gate(connector, version)
        connector.run_migrations()
        merged = load_reader_mappings(connector, "financial_summary", "ie_column")
        assert merged["收入_主动收入_报销"].role == "expense", "owner edit must win"
        connector.close()

    @pytest.mark.parametrize("version", [83, 84, 85])
    def test_migration_recorded(self, tmp_path, version):
        connector = _make_db(tmp_path)
        row = connector.execute(
            "SELECT label FROM schema_version WHERE version = ?", [version]
        ).fetchone()
        assert row is not None
        connector.close()
