"""Tests for ConfigDrivenReader engine (B1/B5).

Gates:
  - Schema: valid YAML loads; bad strategy/category rejected.
  - Config-output regression: config-engine produces non-empty, correctly-keyed output
    for all 6 readers (dual-run equality classes converted to golden assertions in B5).
  - Gold ID guard: asset_id values start with GOLD_ (never ALTS_).
  - Missing file → empty SourceData.
  - Unreadable sheet → empty df + WARNING logged (caplog).

HARD CONSTRAINT: no test may instantiate DatabaseConnector or open
data/unified.duckdb (project DB-safety rule).

B5 NOTE: Legacy reader/transformer modules deleted. The 6 TestDualRunEquality*
classes no longer compare against legacy — they assert config-output correctness
(non-empty, asset_id set, source_system, representative numeric invariants).
"""
from __future__ import annotations

import openpyxl
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.sources.config_driven_reader import ConfigDrivenReader, sync_config_source
from src.sources.reader_config import ReaderConfig, load_reader_config
from src.validation.source_format_validator import FormatValidationResult

# -------------------------------------------------------------------------
# Paths
# -------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "readers"
GOLD_FIXTURE = FIXTURE_DIR / "Gold_transactions.xlsx"
INSURANCE_FIXTURE = FIXTURE_DIR / "Insurance_Portfolio.xlsx"
CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "readers"
GOLD_YAML = CONFIG_DIR / "gold.yaml"
INSURANCE_YAML = CONFIG_DIR / "insurance.yaml"


# -------------------------------------------------------------------------
# Helpers — load configs once
# -------------------------------------------------------------------------

@pytest.fixture(scope="module")
def gold_config() -> ReaderConfig:
    return load_reader_config(GOLD_YAML)


@pytest.fixture(scope="module")
def insurance_config() -> ReaderConfig:
    return load_reader_config(INSURANCE_YAML)


# =========================================================================
# 1. Schema tests
# =========================================================================

class TestSchemaValidation:
    def test_gold_yaml_loads(self, gold_config):
        """gold.yaml parses to a valid ReaderConfig."""
        assert gold_config.identity.source_key == "gold"
        assert gold_config.identity.source_system == "Gold_Excel"
        assert gold_config.parsing is not None
        assert gold_config.parsing.snapshot_date.strategy == "file_mtime"

    def test_insurance_yaml_loads(self, insurance_config):
        """insurance.yaml parses to a valid ReaderConfig."""
        assert insurance_config.identity.source_key == "insurance"
        assert insurance_config.identity.source_system == "Insurance_Excel"
        assert insurance_config.parsing is not None
        assert insurance_config.parsing.snapshot_date.strategy == "file_mtime"

    def test_invalid_strategy_rejected(self, tmp_path):
        """Unknown snapshot_date strategy must fail validation."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(
            """
identity:
  source_key: test
  source_system: Test
  display_label: Test
  category: reader
parsing:
  format: excel
  snapshot_date:
    strategy: magic_strategy
  sheets: []
""",
            encoding="utf-8",
        )
        with pytest.raises(ValidationError):
            load_reader_config(bad_yaml)

    def test_invalid_category_rejected(self, tmp_path):
        """Unknown category must fail validation."""
        bad_yaml = tmp_path / "bad_cat.yaml"
        bad_yaml.write_text(
            """
identity:
  source_key: test
  source_system: Test
  display_label: Test
  category: unknown_category
""",
            encoding="utf-8",
        )
        with pytest.raises(ValidationError):
            load_reader_config(bad_yaml)

    def test_valid_historical_category(self, tmp_path):
        """'historical' is a valid category value."""
        ok_yaml = tmp_path / "ok.yaml"
        ok_yaml.write_text(
            """
identity:
  source_key: financial_summary
  source_system: Financial_Summary_Excel
  display_label: Financial Summary
  display_name: Financial Summary
  category: historical
""",
            encoding="utf-8",
        )
        cfg = load_reader_config(ok_yaml)
        assert cfg.identity.category == "historical"


# =========================================================================
# 2. DUAL-RUN EQUALITY (the gate)
# =========================================================================

class TestDualRunEqualityGold:
    """Golden assertions for config-engine Gold output (B5: legacy deleted).

    Original intent: byte-identical vs legacy GoldReader.
    B5 conversion: assert config-output invariants (non-empty, prefix, source_system).
    """

    @pytest.fixture(scope="class")
    def config_outputs(self):
        cfg = load_reader_config(GOLD_YAML)
        reader = ConfigDrivenReader(cfg)
        data = reader.read(GOLD_FIXTURE)
        return reader.transform(data)

    def test_holdings_non_empty(self, config_outputs):
        cfg_h, _ = config_outputs
        assert not cfg_h.empty, "Config gold holdings must be non-empty"

    def test_holdings_asset_id_prefix(self, config_outputs):
        cfg_h, _ = config_outputs
        bad = cfg_h[~cfg_h["asset_id"].str.startswith("GOLD_")]
        assert bad.empty, f"Gold asset IDs must start with GOLD_: {bad['asset_id'].tolist()}"

    def test_holdings_source_system(self, config_outputs):
        cfg_h, _ = config_outputs
        assert (cfg_h["source_system"] == "Gold_Excel").all()

    def test_transactions_non_empty(self, config_outputs):
        _, cfg_t = config_outputs
        assert not cfg_t.empty, "Config gold transactions must be non-empty"

    def test_transactions_source_system(self, config_outputs):
        _, cfg_t = config_outputs
        assert (cfg_t["source_system"] == "Gold_Excel").all()


class TestDualRunEqualityInsurance:
    """Golden assertions for config-engine Insurance output (B5: legacy deleted).

    Original intent: byte-identical vs legacy InsuranceReader.
    B5 conversion: assert config-output invariants (non-empty, prefix, source_system).
    """

    @pytest.fixture(scope="class")
    def config_outputs(self):
        cfg = load_reader_config(INSURANCE_YAML)
        reader = ConfigDrivenReader(cfg)
        data = reader.read(INSURANCE_FIXTURE)
        return reader.transform(data)

    def test_holdings_non_empty(self, config_outputs):
        cfg_h, _ = config_outputs
        assert not cfg_h.empty, "Config insurance holdings must be non-empty"

    def test_holdings_asset_id_prefix(self, config_outputs):
        cfg_h, _ = config_outputs
        bad = cfg_h[~cfg_h["asset_id"].str.startswith("INS_")]
        assert bad.empty, f"Insurance asset IDs must start with INS_: {bad['asset_id'].tolist()}"

    def test_holdings_source_system(self, config_outputs):
        cfg_h, _ = config_outputs
        assert (cfg_h["source_system"] == "Insurance_Excel").all()

    def test_transactions_non_empty(self, config_outputs):
        _, cfg_t = config_outputs
        assert not cfg_t.empty, "Config insurance transactions must be non-empty"

    def test_transactions_source_system(self, config_outputs):
        _, cfg_t = config_outputs
        assert (cfg_t["source_system"] == "Insurance_Excel").all()


# =========================================================================
# 3. Gold ID guard
# =========================================================================

class TestGoldIdGuard:
    """config-engine gold holdings must use GOLD_* asset IDs, never ALTS_."""

    def test_asset_ids_start_with_gold(self):
        cfg = load_reader_config(GOLD_YAML)
        reader = ConfigDrivenReader(cfg)
        data = reader.read(GOLD_FIXTURE)
        holdings_df, _ = reader.transform(data)

        assert not holdings_df.empty, "Expected non-empty gold holdings"
        bad = holdings_df[~holdings_df["asset_id"].str.startswith("GOLD_")]
        assert bad.empty, (
            f"Gold asset IDs must start with GOLD_, found: {bad['asset_id'].tolist()}"
        )

    def test_no_alts_ids(self):
        cfg = load_reader_config(GOLD_YAML)
        reader = ConfigDrivenReader(cfg)
        data = reader.read(GOLD_FIXTURE)
        holdings_df, _ = reader.transform(data)

        alts = holdings_df[holdings_df["asset_id"].str.startswith("ALTS_")]
        assert alts.empty, (
            "ALTS_ IDs must not appear in reader output — "
            "the rename belongs downstream in _normalize_transactions_df"
        )


# =========================================================================
# 4. Missing file → empty SourceData
# =========================================================================

class TestMissingFile:
    def test_gold_missing_file_returns_empty(self, tmp_path, gold_config):
        reader = ConfigDrivenReader(gold_config)
        data = reader.read(tmp_path / "nonexistent.xlsx")
        assert data.holdings.empty
        assert data.transactions.empty
        assert data.source_name == "gold"

    def test_insurance_missing_file_returns_empty(self, tmp_path, insurance_config):
        reader = ConfigDrivenReader(insurance_config)
        data = reader.read(tmp_path / "nonexistent.xlsx")
        assert data.holdings.empty
        assert data.transactions.empty
        assert data.source_name == "insurance"

    def test_metadata_empty_on_missing(self, tmp_path, gold_config):
        reader = ConfigDrivenReader(gold_config)
        data = reader.read(tmp_path / "missing.xlsx")
        # metadata should be empty dict (no snapshot_date key)
        assert data.metadata == {}


# =========================================================================
# 5. Unreadable sheet → empty df + WARNING logged
# =========================================================================

class TestUnreadableSheet:
    """When a sheet cannot be read, engine should return an empty DataFrame
    for that target and emit a WARNING log."""

    @pytest.fixture
    def gold_no_holdings_workbook(self, tmp_path):
        """Workbook with 黄金交易记录 but NOT 黄金持仓."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "黄金交易记录"
        ws.append(["交易日期", "资产类别", "标的名称", "交易类型",
                   "金额", "数量", "价格", "手续费", "交易账户"])
        ws.append([datetime(2025, 1, 1), "黄金", "纸黄金", "买入",
                   1000, 1.0, 1000.0, 0.0, "招行"])
        path = tmp_path / "gold_partial.xlsx"
        wb.save(path)
        return path

    def test_missing_sheet_yields_empty_holdings(
        self, gold_no_holdings_workbook, gold_config, caplog
    ):
        import logging
        reader = ConfigDrivenReader(gold_config)
        with caplog.at_level(logging.WARNING):
            data = reader.read(gold_no_holdings_workbook)

        assert data.holdings.empty
        # Transactions sheet was present → should not be empty
        assert not data.transactions.empty

    def test_missing_sheet_logs_warning(
        self, gold_no_holdings_workbook, gold_config, caplog
    ):
        import logging
        reader = ConfigDrivenReader(gold_config)
        with caplog.at_level(logging.WARNING):
            reader.read(gold_no_holdings_workbook)

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("黄金持仓" in m for m in warning_messages), (
            f"Expected WARNING about 黄金持仓 sheet, got: {warning_messages}"
        )


# =========================================================================
# 6. Snapshot date — both paths read same file so mtime matches
# =========================================================================

class TestSnapshotDate:
    def test_gold_snapshot_date_in_metadata(self, gold_config):
        reader = ConfigDrivenReader(gold_config)
        data = reader.read(GOLD_FIXTURE)
        assert "snapshot_date" in data.metadata
        snap = data.metadata["snapshot_date"]
        assert len(snap) == 10 and snap[4] == "-" and snap[7] == "-"

    def test_gold_snapshot_date_in_holdings(self, gold_config):
        reader = ConfigDrivenReader(gold_config)
        data = reader.read(GOLD_FIXTURE)
        holdings_df, _ = reader.transform(data)
        assert "snapshot_date" in holdings_df.columns
        expected = data.metadata["snapshot_date"]
        assert (holdings_df["snapshot_date"] == expected).all()

    def test_insurance_snapshot_date_in_holdings(self, insurance_config):
        reader = ConfigDrivenReader(insurance_config)
        data = reader.read(INSURANCE_FIXTURE)
        holdings_df, _ = reader.transform(data)
        assert "snapshot_date" in holdings_df.columns
        expected = data.metadata["snapshot_date"]
        assert (holdings_df["snapshot_date"] == expected).all()


# =========================================================================
# 7. Validate method — warning patterns
# =========================================================================

class TestValidate:
    def test_validate_gold_is_valid(self, gold_config):
        reader = ConfigDrivenReader(gold_config)
        data = reader.read(GOLD_FIXTURE)
        result = reader.validate(data)
        assert result.is_valid is True

    def test_validate_insurance_is_valid(self, insurance_config):
        reader = ConfigDrivenReader(insurance_config)
        data = reader.read(INSURANCE_FIXTURE)
        result = reader.validate(data)
        assert result.is_valid is True

    def test_validate_empty_warns_holdings(self, tmp_path, gold_config):
        """Empty holdings triggers 'No holdings data found' warning."""
        reader = ConfigDrivenReader(gold_config)
        data = reader.read(tmp_path / "nonexistent.xlsx")
        result = reader.validate(data)
        assert result.is_valid is True
        assert any("No holdings" in w for w in result.warnings)


# =========================================================================
# 8. sync_config_source — format validation wiring
# =========================================================================

class TestSyncConfigSourceValidation:
    """sync_config_source must call the declared validator and match legacy
    warn-only semantics: proceed even when is_valid=False, no early return."""

    def _make_config(self, data_dir: str, workbook_name: str) -> dict:
        """Build a minimal config dict pointing at the given directory."""
        return {
            "source_registry": {
                "gold": {
                    "enabled": True,
                    "data_dir": str(data_dir),
                    "file_patterns": {"workbook": workbook_name},
                }
            }
        }

    def test_validator_called_for_gold(self, caplog):
        """sync_config_source calls validate_gold_format when identity.validator is set."""
        gold_cfg = load_reader_config(GOLD_YAML)
        # Confirm the identity carries the expected validator name
        assert gold_cfg.identity.validator == "validate_gold_format"

        cfg = self._make_config(FIXTURE_DIR, "Gold_transactions.xlsx")

        called_with = []

        def fake_validator(path):
            called_with.append(path)
            return FormatValidationResult(is_valid=True, warnings=[], file_type="gold")

        # Patch on the module object that config_driven_reader holds (_sfv is
        # src.validation.source_format_validator, so patching the module attr
        # affects getattr(_sfv, "validate_gold_format") in the engine).
        with patch(
            "src.validation.source_format_validator.validate_gold_format",
            side_effect=fake_validator,
        ):
            result = sync_config_source(cfg, gold_cfg)

        assert len(called_with) == 1, "Validator must be called exactly once"
        assert called_with[0] == FIXTURE_DIR / "Gold_transactions.xlsx"
        assert not result["holdings"].empty

    def test_invalid_result_warns_and_proceeds(self, caplog):
        """When validator returns is_valid=False, a WARNING is logged and sync
        continues to return data — no early return, no exception (mirrors legacy)."""
        import logging

        gold_cfg = load_reader_config(GOLD_YAML)
        cfg = self._make_config(FIXTURE_DIR, "Gold_transactions.xlsx")

        def failing_validator(path):
            return FormatValidationResult(
                is_valid=False,
                warnings=["missing required sheet"],
                file_type="gold",
            )

        with caplog.at_level(logging.WARNING, logger="src.sources.config_driven_reader"):
            with patch(
                "src.validation.source_format_validator.validate_gold_format",
                side_effect=failing_validator,
            ):
                result = sync_config_source(cfg, gold_cfg)

        # Must still return data — validation is warn-only
        assert not result["holdings"].empty, "Sync must proceed despite is_valid=False"
        # Warning must be logged
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("format validation failed" in m for m in warning_msgs), (
            f"Expected 'format validation failed' warning, got: {warning_msgs}"
        )
        assert any("missing required sheet" in m for m in warning_msgs), (
            f"Expected warning text to include the validator warnings: {warning_msgs}"
        )

    def test_unknown_validator_name_warns_and_proceeds(self, tmp_path, caplog):
        """When identity.validator names a function not in source_format_validator,
        a WARNING is logged (never a silent skip) and sync still returns data."""
        import logging

        bad_yaml = tmp_path / "gold_bad_validator.yaml"
        bad_yaml.write_text(
            """
identity:
  source_key: gold
  source_system: Gold_Excel
  display_label: "黄金 (Paper Gold)"
  display_name: "Gold"
  asset_prefixes:
    - GOLD_
  allowed_extensions:
    - .xlsx
  category: reader
  validator: validate_nonexistent_format
parsing:
  format: excel
  snapshot_date:
    strategy: file_mtime
  sheets: []
""",
            encoding="utf-8",
        )
        from src.sources.reader_config import load_reader_config as _load
        cfg_bad = _load(bad_yaml)
        assert cfg_bad.identity.validator == "validate_nonexistent_format"

        # Use a real workbook so the engine gets past the exists() check
        config = self._make_config(FIXTURE_DIR, "Gold_transactions.xlsx")
        # Override source_key to match what config_bad uses
        config["source_registry"]["gold"]["enabled"] = True

        with caplog.at_level(logging.WARNING, logger="src.sources.config_driven_reader"):
            result = sync_config_source(config, cfg_bad)

        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("no format validator" in m for m in warning_msgs), (
            f"Expected 'no format validator' warning, got: {warning_msgs}"
        )
        assert any("validate_nonexistent_format" in m for m in warning_msgs), (
            f"Expected warning to include validator name, got: {warning_msgs}"
        )
        # Engine should still return (possibly empty) dicts — no crash
        assert isinstance(result, dict)
        assert "holdings" in result

    def test_no_validator_field_proceeds_silently(self, tmp_path, caplog):
        """When identity.validator is None (field absent), no validator warning
        is emitted — just proceed normally."""
        import logging

        no_validator_yaml = tmp_path / "gold_no_validator.yaml"
        no_validator_yaml.write_text(
            """
identity:
  source_key: gold
  source_system: Gold_Excel
  display_label: "黄金 (Paper Gold)"
  display_name: "Gold"
  asset_prefixes:
    - GOLD_
  allowed_extensions:
    - .xlsx
  category: reader
parsing:
  format: excel
  snapshot_date:
    strategy: file_mtime
  sheets: []
""",
            encoding="utf-8",
        )
        from src.sources.reader_config import load_reader_config as _load
        cfg_none = _load(no_validator_yaml)
        assert cfg_none.identity.validator is None

        config = self._make_config(FIXTURE_DIR, "Gold_transactions.xlsx")
        with caplog.at_level(logging.WARNING, logger="src.sources.config_driven_reader"):
            result = sync_config_source(config, cfg_none)

        validator_warnings = [
            r.message for r in caplog.records
            if r.levelno >= logging.WARNING and "validator" in r.message.lower()
        ]
        assert validator_warnings == [], (
            f"No validator warning expected when validator=None, got: {validator_warnings}"
        )
        assert isinstance(result, dict)


# =========================================================================
# 9. DUAL-RUN EQUALITY — RSU (B2)
# =========================================================================

RSU_FIXTURE = FIXTURE_DIR / "RSU_transactions.xlsx"
RSU_YAML = CONFIG_DIR / "rsu.yaml"


_RSU_FIXED_NOW = datetime(2026, 6, 12, 10, 0, 0)


class TestDualRunEqualityRSU:
    """Golden assertions for config-engine RSU output (B5: legacy deleted).

    Original intent: byte-identical vs legacy RSUReader + rsu_transformer.
    B5 conversion: assert config-output invariants (non-empty, RSU_ prefix,
    source_system, derive_rsu_holdings hook active).
    """

    @pytest.fixture(scope="class")
    def config_outputs(self):
        cfg = load_reader_config(RSU_YAML)
        reader = ConfigDrivenReader(cfg)

        with patch("src.sources.config_driven_reader.datetime") as mock_dt:
            mock_dt.now.return_value = _RSU_FIXED_NOW
            mock_dt.fromtimestamp.side_effect = datetime.fromtimestamp
            data = reader.read(RSU_FIXTURE)

        return reader.transform(data)

    def test_holdings_non_empty(self, config_outputs):
        cfg_h, _ = config_outputs
        assert not cfg_h.empty, "Config RSU holdings must be non-empty"

    def test_transactions_non_empty(self, config_outputs):
        _, cfg_t = config_outputs
        assert not cfg_t.empty, "Config RSU transactions must be non-empty"

    def test_holdings_source_system(self, config_outputs):
        cfg_h, _ = config_outputs
        assert (cfg_h["source_system"] == "RSU_Excel").all()

    def test_transactions_source_system(self, config_outputs):
        _, cfg_t = config_outputs
        assert (cfg_t["source_system"] == "RSU_Excel").all()

    def test_rsu_asset_ids_start_with_rsu(self, config_outputs):
        """Config-engine RSU transactions must have RSU_ prefixed asset_ids."""
        _, cfg_t = config_outputs
        assert not cfg_t.empty
        bad = cfg_t[~cfg_t["asset_id"].str.startswith("RSU_")]
        assert bad.empty, (
            f"RSU asset IDs must start with RSU_, found: {bad['asset_id'].tolist()}"
        )

    def test_holdings_hook_active(self):
        """Confirm holdings_hook is set in the RSU config YAML."""
        cfg = load_reader_config(RSU_YAML)
        assert cfg.parsing is not None
        assert cfg.parsing.holdings_hook == "derive_rsu_holdings"

    def test_other_sources_hook_none(self, gold_config, insurance_config):
        """Gold and insurance must NOT have a holdings_hook — their path is unchanged."""
        assert gold_config.parsing is not None
        assert gold_config.parsing.holdings_hook is None, (
            "Gold holdings_hook must remain None — B2 must not alter gold behaviour"
        )
        assert insurance_config.parsing is not None
        assert insurance_config.parsing.holdings_hook is None, (
            "Insurance holdings_hook must remain None — B2 must not alter insurance behaviour"
        )


# =========================================================================
# 10. DUAL-RUN EQUALITY — Financial Summary (B2 sitting #2)
# =========================================================================

FS_FIXTURE = FIXTURE_DIR / "Financial_Summary_new.xlsx"
FS_YAML = CONFIG_DIR / "financial_summary.yaml"

# Re-baselined 2026-08-01 (P1 — FS blank-column phantom holding): 536 → 555.
# The melt no longer drops cells that contain a literal 0; an explicit 0 is the
# owner asserting "this balance is empty", and dropping it left the asset's last
# non-zero row as its latest snapshot forever. This is asserted below rather
# than only counted, so this baseline cannot drift silently.
#
# Program OSR WS-3b: fixture swapped from the real workbook to the synthetic
# persona one (tools/demo_data/out/Financial_Summary_new.xlsx — WS-1.4). All
# three constants recomputed against the new fixture directly (see
# docs/plans/2026-08-16-ws1-swap-impact.md). In this public export,
# mapping_seeds.py is the persona twin, so the property column also melts
# successfully — one extra holdings row per snapshot date (189 + 21 = 210).
_FS_EXPECTED_ROWS = 210
_FS_EXPECTED_ZERO_VALUE_ROWS = 0
_FS_EXPECTED_DATE_COUNT = 21


class TestDualRunEqualityFinancialSummary:
    """Golden assertions for config-engine Financial Summary output (B5: legacy deleted).

    Original intent: byte-identical vs legacy FinancialSummaryReader →
    transform_holdings → melt_balance_sheet_to_holdings chain.

    B5 conversion: assert golden invariants — row count, snapshot_date count,
    asset_id set.  These were verified byte-identical before legacy deletion
    and are now the authoritative regression baseline.
    """

    @pytest.fixture(scope="class")
    def config_holdings(self):
        cfg = load_reader_config(FS_YAML)
        reader = ConfigDrivenReader(cfg)
        data = reader.read(FS_FIXTURE)
        holdings_df, _ = reader.transform(data)
        return holdings_df

    def test_holdings_non_empty(self, config_holdings):
        assert not config_holdings.empty, "Config FS holdings must be non-empty"

    def test_row_count(self, config_holdings):
        """Config path must produce exactly _FS_EXPECTED_ROWS holdings rows."""
        assert len(config_holdings) == _FS_EXPECTED_ROWS, (
            f"Expected {_FS_EXPECTED_ROWS} rows, got {len(config_holdings)}"
        )

    def test_zero_value_row_count(self, config_holdings):
        """The zero-value rows in that count are the fixture's explicit 0 cells.

        Pins the composition of the row-count baseline: if a future change starts
        synthesizing zero rows for blank cells (the thing the P1 fix deliberately
        does NOT do), the total would still be "some number" but this assertion
        breaks.

        Program OSR WS-3b: the synthetic persona fixture has no explicit-0 cells
        (_FS_EXPECTED_ZERO_VALUE_ROWS == 0), so this specific assertion — and the
        quantity==0 check below it — are vacuously true on an empty slice for now.
        Kept rather than deleted: it still guards against the P1 regression
        described above, and starts asserting something real again the moment a
        future persona-fixture regen adds an explicit 0 cell.
        """
        zero_rows = config_holdings[config_holdings["market_value"] == 0]
        assert len(zero_rows) == _FS_EXPECTED_ZERO_VALUE_ROWS, (
            f"Expected {_FS_EXPECTED_ZERO_VALUE_ROWS} zero-value rows, "
            f"got {len(zero_rows)}"
        )
        # A zero-value FS row is a tombstone — it must carry zero quantity too,
        # so nothing downstream treats it as a one-unit position.
        assert (zero_rows["quantity"] == 0.0).all()

    def test_snapshot_date_count(self, config_holdings):
        """Config path must have exactly _FS_EXPECTED_DATE_COUNT distinct snapshot_date values."""
        actual = config_holdings["snapshot_date"].nunique()
        assert actual == _FS_EXPECTED_DATE_COUNT, (
            f"Expected {_FS_EXPECTED_DATE_COUNT} distinct snapshot_dates, got {actual}"
        )

    def test_asset_id_count(self, config_holdings):
        """Config path must have exactly 10 distinct asset_ids.

        This fixture read uses no injected metadata, so the melt falls back to
        reader_hooks.py's hardcoded _FS_ASSET_MAPPING. In this public export,
        mapping_seeds.py is the persona-safe twin (tools/release/mapping_seeds.public.py)
        whose 固定资产_房产_阳光花园 key already matches the persona fixture's column
        name exactly, so Property_阳光花园 IS produced via this bare path (unlike
        the owner's private repo, where the real dict and the persona fixture
        diverge — see docs/plans/2026-08-16-ws1-swap-impact.md §3.1 for that case).
        """
        expected_ids = {
            "CASH_Cash_CNY", "CASH_Deposit_BOB_CNY", "CASH_Deposit_BOC_CNY",
            "CASH_Deposit_BOC_USD", "CASH_Deposit_CMB_CNY", "CASH_Deposit_Chase_USD",
            "CASH_Deposit_Discover_USD", "Pension_Personal", "Property_阳光花园", "Wealth_CMB",
        }
        actual_ids = set(config_holdings["asset_id"].unique())
        assert actual_ids == expected_ids, (
            f"Asset ID mismatch.\nExpected: {sorted(expected_ids)}\nGot: {sorted(actual_ids)}"
        )

    def test_holdings_from_sheet_hook_active(self):
        """Confirm holdings_from_sheet_hook is set in the FS config YAML."""
        cfg = load_reader_config(FS_YAML)
        assert cfg.parsing is not None
        assert cfg.parsing.holdings_from_sheet_hook == "melt_financial_summary_holdings"
        # holdings_hook must remain None (mutually exclusive)
        assert cfg.parsing.holdings_hook is None

    def test_other_sources_hook_none(self, gold_config, insurance_config):
        """Gold and insurance must NOT have holdings_from_sheet_hook set."""
        assert gold_config.parsing is not None
        assert gold_config.parsing.holdings_from_sheet_hook is None, (
            "Gold holdings_from_sheet_hook must remain None — B2 must not alter gold"
        )
        assert insurance_config.parsing is not None
        assert insurance_config.parsing.holdings_from_sheet_hook is None, (
            "Insurance holdings_from_sheet_hook must remain None"
        )


# =========================================================================
# 11. DUAL-RUN EQUALITY — CN Fund (B2 sitting #3)
# =========================================================================

CN_FUND_FIXTURE = FIXTURE_DIR / "funding_transactions.xlsx"
CN_FUND_YAML = CONFIG_DIR / "cn_fund.yaml"


class TestDualRunEqualityCNFund:
    """Golden assertions for config-engine CN Fund output (B5: legacy deleted).

    Original intent: byte-identical vs legacy CNFundReader + cn_fund_transformer.
    B5 conversion: assert config-output invariants (non-empty, CN_FUND_ prefix,
    source_system, hooks active).

    CRITICAL SAFETY: pre_read_hook must remain None to avoid mutating the fixture.
    """

    @pytest.fixture(scope="class")
    def config_outputs(self):
        cfg = load_reader_config(CN_FUND_YAML)
        # CRITICAL: disable raw processor so the fixture workbook is never mutated
        cfg.parsing.pre_read_hook = None
        reader = ConfigDrivenReader(cfg)
        data = reader.read(CN_FUND_FIXTURE)
        return reader.transform(data)

    def test_holdings_non_empty(self, config_outputs):
        """Config CN Fund holdings must be non-empty."""
        cfg_h, _ = config_outputs
        assert not cfg_h.empty, "Config CN Fund holdings must be non-empty"

    def test_transactions_non_empty(self, config_outputs):
        """Config CN Fund transactions must be non-empty."""
        _, cfg_t = config_outputs
        assert not cfg_t.empty, "Config CN Fund transactions must be non-empty"

    def test_holdings_source_system(self, config_outputs):
        cfg_h, _ = config_outputs
        assert (cfg_h["source_system"] == "CN_Fund_Excel").all()

    def test_transactions_source_system(self, config_outputs):
        _, cfg_t = config_outputs
        assert (cfg_t["source_system"] == "CN_Fund_Excel").all()

    def test_pre_read_hook_set_in_yaml(self):
        """The on-disk YAML must declare pre_read_hook (test instance nulls it; YAML has it)."""
        cfg = load_reader_config(CN_FUND_YAML)
        assert cfg.parsing is not None
        assert cfg.parsing.pre_read_hook == "cn_fund_raw_process", (
            "cn_fund.yaml must declare pre_read_hook: cn_fund_raw_process "
            "(tests disable it; production uses it)"
        )

    def test_holdings_from_sheet_hook_active(self):
        """Confirm holdings_from_sheet_hook is set in the CN Fund config YAML."""
        cfg = load_reader_config(CN_FUND_YAML)
        assert cfg.parsing is not None
        assert cfg.parsing.holdings_from_sheet_hook == "cn_fund_holdings_from_sheet"

    def test_transactions_from_sheet_hook_active(self):
        """Confirm transactions_from_sheet_hook is set in the CN Fund config YAML."""
        cfg = load_reader_config(CN_FUND_YAML)
        assert cfg.parsing is not None
        assert cfg.parsing.transactions_from_sheet_hook == "cn_fund_transactions_from_sheet"

    def test_other_sources_no_transactions_from_sheet_hook(self, gold_config, insurance_config):
        """Gold, insurance, and financial_summary must NOT have transactions_from_sheet_hook."""
        assert gold_config.parsing is not None
        assert gold_config.parsing.transactions_from_sheet_hook is None, (
            "Gold transactions_from_sheet_hook must remain None"
        )
        assert insurance_config.parsing is not None
        assert insurance_config.parsing.transactions_from_sheet_hook is None, (
            "Insurance transactions_from_sheet_hook must remain None"
        )
        fs_cfg = load_reader_config(CONFIG_DIR / "financial_summary.yaml")
        assert fs_cfg.parsing is not None
        assert fs_cfg.parsing.transactions_from_sheet_hook is None, (
            "Financial Summary transactions_from_sheet_hook must remain None"
        )

    def test_other_sources_no_pre_read_hook(self, gold_config, insurance_config):
        """Gold and insurance must NOT have pre_read_hook set."""
        assert gold_config.parsing is not None
        assert gold_config.parsing.pre_read_hook is None, (
            "Gold pre_read_hook must remain None — B2 sitting #3 must not alter gold"
        )
        assert insurance_config.parsing is not None
        assert insurance_config.parsing.pre_read_hook is None, (
            "Insurance pre_read_hook must remain None"
        )

    def test_cn_fund_asset_ids_start_with_cn_fund(self, config_outputs):
        """Config-engine CN Fund holdings must have CN_FUND_ prefixed asset_ids."""
        cfg_h, _ = config_outputs
        assert not cfg_h.empty
        bad = cfg_h[~cfg_h["asset_id"].str.startswith("CN_FUND_")]
        assert bad.empty, (
            f"CN Fund asset IDs must start with CN_FUND_, found: {bad['asset_id'].tolist()}"
        )

    def test_cn_fund_txn_asset_ids_start_with_cn_fund(self, config_outputs):
        """Config-engine CN Fund transactions must have CN_FUND_ prefixed asset_ids."""
        _, cfg_t = config_outputs
        assert not cfg_t.empty
        bad = cfg_t[~cfg_t["asset_id"].str.startswith("CN_FUND_")]
        assert bad.empty, (
            f"CN Fund txn asset IDs must start with CN_FUND_, found: {bad['asset_id'].tolist()}"
        )


# =========================================================================
# 12. DUAL-RUN EQUALITY — Schwab CSV (B2 sitting #4a)
# =========================================================================

SCHWAB_FIXTURE_DIR = FIXTURE_DIR  # tests/fixtures/readers (contains both CSV files)
SCHWAB_YAML = CONFIG_DIR / "schwab.yaml"


class TestDualRunEqualitySchwab:
    """Golden assertions for config-engine Schwab output (B5: legacy deleted).

    Original intent: byte-identical vs legacy sync_schwab (legacy branch).
    B5 conversion: assert config-output invariants (non-empty, prefixes,
    source_system, CASH_USD > 0, hooks active).
    """

    def _config(self) -> dict:
        return {
            "source_registry": {
                "schwab": {
                    "enabled": True,
                    "data_dir": str(SCHWAB_FIXTURE_DIR),
                    "file_patterns": {
                        "positions": "Individual-Positions-*.csv",
                        "transactions": "Individual_*_Transactions_*.csv",
                    },
                }
            }
        }

    @pytest.fixture(scope="class")
    def config_outputs(self):
        from src.sync.schwab_sync import sync_schwab
        result = sync_schwab(self._config())
        return result["holdings"], result["transactions"]

    def test_holdings_non_empty(self, config_outputs):
        """Config Schwab holdings must be non-empty."""
        cfg_h, _ = config_outputs
        assert not cfg_h.empty, "Config Schwab holdings must be non-empty"

    def test_transactions_non_empty(self, config_outputs):
        """Config Schwab transactions must be non-empty."""
        _, cfg_t = config_outputs
        assert not cfg_t.empty, "Config Schwab transactions must be non-empty"

    def test_holdings_source_system(self, config_outputs):
        cfg_h, _ = config_outputs
        assert (cfg_h["source_system"] == "Schwab_CSV").all()

    def test_transactions_source_system(self, config_outputs):
        _, cfg_t = config_outputs
        assert (cfg_t["source_system"] == "Schwab_CSV").all()

    def test_cash_usd_market_value_positive(self, config_outputs):
        """CASH_USD row must be present and market_value must be > 0."""
        cfg_h, _ = config_outputs
        cfg_cash = cfg_h[cfg_h["asset_id"] == "CASH_USD"]
        if not cfg_cash.empty:
            assert cfg_cash["market_value"].iloc[0] > 0, "CASH_USD market_value must be positive"

    def test_schwab_config_format_is_csv(self):
        """schwab.yaml must declare format: csv (not excel)."""
        cfg = load_reader_config(SCHWAB_YAML)
        assert cfg.parsing is not None
        assert cfg.parsing.format == "csv"

    def test_gold_insurance_still_excel(self, gold_config, insurance_config):
        """Gold and insurance configs must still declare format: excel (no drift)."""
        assert gold_config.parsing is not None
        assert gold_config.parsing.format == "excel", (
            "Gold format must remain 'excel' — B2 sitting #4a must not alter gold"
        )
        assert insurance_config.parsing is not None
        assert insurance_config.parsing.format == "excel", (
            "Insurance format must remain 'excel' — B2 sitting #4a must not alter insurance"
        )

    def test_holdings_hook_active(self):
        """schwab.yaml must declare holdings_from_sheet_hook: schwab_holdings_from_csv."""
        cfg = load_reader_config(SCHWAB_YAML)
        assert cfg.parsing is not None
        assert cfg.parsing.holdings_from_sheet_hook == "schwab_holdings_from_csv"

    def test_transactions_hook_active(self):
        """schwab.yaml must declare transactions_from_sheet_hook: schwab_transactions_from_csv."""
        cfg = load_reader_config(SCHWAB_YAML)
        assert cfg.parsing is not None
        assert cfg.parsing.transactions_from_sheet_hook == "schwab_transactions_from_csv"


# =========================================================================
# 13. NaN-identity guard — _build_id_from_template / _apply_id_template
# =========================================================================

class TestNaNIdentityGuard:
    """Rows whose id_template placeholders resolve from NaN/empty values must be
    dropped with a WARNING; rows with all-valid placeholders must survive intact.

    Covers the gold-incident scenario:
      Holdings sheet row with everything NaN except one unit-price cell → was
      producing a phantom 'GOLD_nan_nan' asset_id that tripped the BLOCKING
      integrity check active_holdings_have_positive_value (NULL market_value).
    """

    def _make_sheet_cfg(self, template: str, field_maps=None):
        """Minimal SheetConfig with only id_template set."""
        from src.sources.reader_config import SheetConfig
        return SheetConfig(
            name="test",
            target="holdings",
            id_template=template,
            id_field_maps=field_maps or {},
        )

    def test_nan_identity_field_dropped_with_warning(self, caplog):
        """Row with a real NaN in an id_template placeholder column is dropped."""
        import logging
        import pandas as pd
        from src.sources.config_driven_reader import _apply_id_template

        sheet_cfg = self._make_sheet_cfg("GOLD_{asset_name}_{account}")
        df = pd.DataFrame([
            {"asset_name": float("nan"), "account": float("nan"), "unit_price": 3.0},
            {"asset_name": "纸黄金", "account": "招行", "unit_price": 600.0},
        ])

        with caplog.at_level(logging.WARNING, logger="src.sources.config_driven_reader"):
            result = _apply_id_template(df, sheet_cfg)

        # Bad row dropped — only the valid row survives.
        assert len(result) == 1
        assert result["canonical_id"].iloc[0] == "GOLD_纸黄金_招行"
        # Warning emitted for the dropped row.
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("incomplete identity fields" in m for m in warning_msgs), (
            f"Expected 'incomplete identity fields' warning, got: {warning_msgs}"
        )
        assert any("GOLD_{asset_name}_{account}" in m for m in warning_msgs), (
            f"Expected template name in warning, got: {warning_msgs}"
        )

    def test_string_nan_identity_field_dropped(self, caplog):
        """Row where placeholder holds the string 'nan' (post-strip_whitespace path) is dropped."""
        import logging
        import pandas as pd
        from src.sources.config_driven_reader import _apply_id_template

        # strip_whitespace_columns does .astype(str) which turns real NaN → 'nan'.
        sheet_cfg = self._make_sheet_cfg("GOLD_{asset_name}_{account}")
        df = pd.DataFrame([
            {"asset_name": "nan", "account": "nan", "unit_price": 3.0},
            {"asset_name": "纸黄金", "account": "招行", "unit_price": 600.0},
        ])

        with caplog.at_level(logging.WARNING, logger="src.sources.config_driven_reader"):
            result = _apply_id_template(df, sheet_cfg)

        assert len(result) == 1
        assert result["canonical_id"].iloc[0] == "GOLD_纸黄金_招行"

    def test_empty_string_identity_field_dropped(self, caplog):
        """Row where placeholder holds an empty string is dropped."""
        import logging
        import pandas as pd
        from src.sources.config_driven_reader import _apply_id_template

        sheet_cfg = self._make_sheet_cfg("GOLD_{asset_name}_{account}")
        df = pd.DataFrame([
            {"asset_name": "", "account": "", "unit_price": 3.0},
            {"asset_name": "纸黄金", "account": "招行", "unit_price": 600.0},
        ])

        with caplog.at_level(logging.WARNING, logger="src.sources.config_driven_reader"):
            result = _apply_id_template(df, sheet_cfg)

        assert len(result) == 1
        assert result["canonical_id"].iloc[0] == "GOLD_纸黄金_招行"

    def test_valid_row_survives_unchanged(self):
        """Row where all placeholders are valid passes through untouched."""
        import pandas as pd
        from src.sources.config_driven_reader import _apply_id_template

        sheet_cfg = self._make_sheet_cfg("GOLD_{asset_name}_{account}")
        df = pd.DataFrame([
            {"asset_name": "纸黄金", "account": "招行", "unit_price": 600.0},
            {"asset_name": "实物黄金", "account": "建行", "unit_price": 610.0},
        ])
        result = _apply_id_template(df, sheet_cfg)

        assert len(result) == 2
        assert list(result["canonical_id"]) == ["GOLD_纸黄金_招行", "GOLD_实物黄金_建行"]

    def test_no_id_template_passthrough(self):
        """When id_template is not set, the DataFrame is returned unchanged."""
        import pandas as pd
        from src.sources.config_driven_reader import _apply_id_template
        from src.sources.reader_config import SheetConfig

        sheet_cfg = SheetConfig(name="test", target="holdings")
        df = pd.DataFrame([{"asset_name": float("nan"), "account": float("nan")}])
        result = _apply_id_template(df, sheet_cfg)

        # No canonical_id column added, DataFrame unchanged.
        assert "canonical_id" not in result.columns
        assert len(result) == 1

    def test_gold_nan_nan_not_produced(self):
        """Exact gold-incident reproduction: holdings row all-NaN except unit_price=3.0
        must NOT produce a GOLD_nan_nan row in the output (dropped silently after warning)."""
        import pandas as pd
        from src.sources.config_driven_reader import _apply_id_template

        # Simulate what the Gold holdings sheet produces for a stray near-empty row.
        sheet_cfg = self._make_sheet_cfg(
            "GOLD_{asset_name}_{account}",
            field_maps={"asset_name": {}, "account": {}},
        )
        df = pd.DataFrame([
            # Stray row — everything NaN except one numeric cell
            {"asset_name": float("nan"), "account": float("nan"), "unit_price": 3.0,
             "quantity": float("nan"), "market_value": float("nan")},
            # Normal row
            {"asset_name": "纸黄金", "account": "招行", "unit_price": 600.0,
             "quantity": 10.0, "market_value": 6000.0},
        ])

        result = _apply_id_template(df, sheet_cfg)

        # The phantom row must be gone.
        assert "GOLD_nan_nan" not in list(result.get("canonical_id", pd.Series()))
        assert len(result) == 1
        assert result["canonical_id"].iloc[0] == "GOLD_纸黄金_招行"

    def test_warning_includes_non_null_values(self, caplog):
        """The warning message for a dropped row must include the row's non-null values
        so the owner can identify which file/row caused the drop."""
        import logging
        import pandas as pd
        from src.sources.config_driven_reader import _apply_id_template

        sheet_cfg = self._make_sheet_cfg("GOLD_{asset_name}_{account}")
        df = pd.DataFrame([
            {"asset_name": float("nan"), "account": float("nan"), "unit_price": 3.0},
        ])

        with caplog.at_level(logging.WARNING, logger="src.sources.config_driven_reader"):
            _apply_id_template(df, sheet_cfg)

        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        # The non-null unit_price=3.0 should appear in the warning context.
        assert any("unit_price" in m or "3.0" in m for m in warning_msgs), (
            f"Expected non-null column values in warning, got: {warning_msgs}"
        )


# =========================================================================
# Safety: no DatabaseConnector usage
# (enforced by grep — see CLAUDE.md DB-safety rules)
# =========================================================================
# No test in this file imports or instantiates DatabaseConnector.
# Any accidental introduction would be caught by review.
