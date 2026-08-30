"""Golden tests for the source registry (B1, ADR-014).

Every constant rewired to the registry must equal the exact literal value it
had before the rewiring. If any of these fail, the registry has DRIFTED from
the pre-B1 hardcoded values — that is a production behavior change and must
be treated as a bug, not a test to update.

HARD CONSTRAINT: no test here may instantiate DatabaseConnector or open
data/unified.duckdb (project DB-safety rule).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.sources.registry import get_registry

# ---------------------------------------------------------------------------
# Frozen pre-B1 literals (copied verbatim from the replaced hardcoded sites)
# ---------------------------------------------------------------------------

# Original 6 in canonical order (preserved for backward-compat reference)
_GOLDEN_KNOWN_READERS_6 = ["schwab", "cn_fund", "gold", "insurance", "rsu", "financial_summary"]

# Workstream C1 added ibkr as the 7th reader — key_known_list() returns 6 canonical + extras
GOLDEN_KNOWN_READERS = _GOLDEN_KNOWN_READERS_6 + ["ibkr"]

# Workstream C1: Broker_IBKR extends the canonical list as an "extra" (appended sorted).
# GOLDEN values for the original 6 are preserved; IBKR is additive.
GOLDEN_ALL_SOURCE_SYSTEMS = (
    "Schwab_CSV",
    "CN_Fund_Excel",
    "Financial_Summary_Excel",
    "Gold_Excel",
    "Insurance_Excel",
    "RSU_Excel",
    # Broker_IBKR appended (extras sorted after canonical 6):
    "Broker_IBKR",
)

GOLDEN_HOLDING_SOURCES = (
    "Schwab_CSV",
    "CN_Fund_Excel",
    "Gold_Excel",
    "Insurance_Excel",
    "RSU_Excel",
    # Broker_IBKR is category='reader' so it appears here too:
    "Broker_IBKR",
)

GOLDEN_HISTORICAL_SOURCES = {"Financial_Summary_Excel"}

GOLDEN_ALLOWED_EXTS = {
    "schwab": {".csv"},
    "cn_fund": {".xlsx", ".xls"},
    "gold": {".xlsx", ".xls"},
    "insurance": {".xlsx", ".xls"},
    "rsu": {".xlsx", ".xls"},
    "financial_summary": {".xlsx", ".xls"},
    # Workstream C1: IBKR Flex CSV files are .csv
    "ibkr": {".csv"},
}

GOLDEN_VALIDATOR_MAP = {
    "schwab": "validate_schwab_format",
    "cn_fund": "validate_cn_fund_format",
    "gold": "validate_gold_format",
    "insurance": "validate_insurance_format",
    "rsu": "validate_rsu_format",
    "financial_summary": "validate_financial_summary_format",
    # Workstream C1: IBKR format validator
    "ibkr": "validate_ibkr_format",
}

GOLDEN_KEY_TO_SYSTEM = {
    "schwab": "Schwab_CSV",
    "cn_fund": "CN_Fund_Excel",
    "gold": "Gold_Excel",
    "insurance": "Insurance_Excel",
    "rsu": "RSU_Excel",
    "financial_summary": "Financial_Summary_Excel",
    # Workstream C1:
    "ibkr": "Broker_IBKR",
}

GOLDEN_DISPLAY_NAMES = {
    "Schwab_CSV": "Schwab",
    "CN_Fund_Excel": "CN Funds",
    "Gold_Excel": "Gold",
    "Insurance_Excel": "Insurance",
    "RSU_Excel": "RSU",
    "Financial_Summary_Excel": "Financial Summary",
    # Workstream C1:
    "Broker_IBKR": "IBKR",
}

GOLDEN_DEFAULT_ACCOUNTS = {
    "Schwab_CSV": "Schwab",
    "CN_Fund_Excel": "CN Fund",  # NOT "CN Funds" — account name differs from display
    "Gold_Excel": "Gold",
    "Insurance_Excel": "Insurance",
    "RSU_Excel": "RSU",
    # Workstream C1: IBKR category='reader', account_name='IBKR'
    "Broker_IBKR": "IBKR",
}


# ---------------------------------------------------------------------------
# Registry accessor golden tests
# ---------------------------------------------------------------------------

class TestRegistryGoldenValues:
    def test_key_known_list(self):
        assert get_registry().key_known_list() == GOLDEN_KNOWN_READERS

    def test_all_source_systems(self):
        assert get_registry().all_source_systems() == GOLDEN_ALL_SOURCE_SYSTEMS

    def test_holding_source_systems(self):
        assert get_registry().holding_source_systems() == GOLDEN_HOLDING_SOURCES

    def test_historical_source_systems(self):
        assert get_registry().historical_source_systems() == GOLDEN_HISTORICAL_SOURCES

    def test_allowed_extensions(self):
        assert get_registry().allowed_extensions() == GOLDEN_ALLOWED_EXTS

    def test_validator_map(self):
        assert get_registry().validator_map() == GOLDEN_VALIDATOR_MAP

    def test_key_to_system(self):
        assert get_registry().key_to_system() == GOLDEN_KEY_TO_SYSTEM

    def test_system_to_key_is_inverse(self):
        assert get_registry().system_to_key() == {
            v: k for k, v in GOLDEN_KEY_TO_SYSTEM.items()
        }

    def test_source_display_names(self):
        assert get_registry().source_display_names() == GOLDEN_DISPLAY_NAMES

    def test_default_account_names(self):
        assert get_registry().default_account_names() == GOLDEN_DEFAULT_ACCOUNTS

    def test_seven_sources_loaded(self):
        """Workstream C1 added ibkr — now 7 sources total."""
        assert sorted(get_registry().reader_keys()) == sorted(GOLDEN_KNOWN_READERS)


# ---------------------------------------------------------------------------
# Rewired consumer constants — must equal pre-B1 literals exactly
# ---------------------------------------------------------------------------

class TestRewiredConstants:
    def test_sync_known_readers(self):
        from src.api.routes.sync import KNOWN_READERS
        assert KNOWN_READERS == set(GOLDEN_KNOWN_READERS)
        assert isinstance(KNOWN_READERS, set)

    def test_settings_constants(self):
        from src.api.routes.settings import (
            _KNOWN_SOURCE_READERS,
            _READER_ALLOWED_EXTS,
            _READER_LABEL_MAP,
            _VALIDATOR_MAP,
        )
        assert _KNOWN_SOURCE_READERS == GOLDEN_KNOWN_READERS
        assert _READER_ALLOWED_EXTS == GOLDEN_ALLOWED_EXTS
        assert _VALIDATOR_MAP == GOLDEN_VALIDATOR_MAP
        assert _READER_LABEL_MAP == GOLDEN_KEY_TO_SYSTEM

    def test_operations_constants(self):
        from src.api.routes.operations import READER_SOURCES, _SOURCE_DISPLAY_NAMES
        assert READER_SOURCES == GOLDEN_ALL_SOURCE_SYSTEMS
        assert isinstance(READER_SOURCES, tuple)
        assert _SOURCE_DISPLAY_NAMES == GOLDEN_DISPLAY_NAMES

    def test_data_sold_close_candidates(self):
        from src.api.routes.data import _SOLD_CLOSE_CANDIDATE_SOURCES
        assert _SOLD_CLOSE_CANDIDATE_SOURCES == frozenset(GOLDEN_HOLDING_SOURCES)

    def test_integrity_gate_reader_sources(self):
        from src.validation.data_integrity_gate import READER_SOURCES
        assert READER_SOURCES == GOLDEN_HOLDING_SOURCES
        assert isinstance(READER_SOURCES, tuple)

    def test_cost_basis_reader_sources(self):
        from src.validation.cost_basis_validator import READER_HOLDING_SOURCES
        assert READER_HOLDING_SOURCES == GOLDEN_HOLDING_SOURCES

    def test_common_phase_constants(self):
        from src.sync.phases._common import (
            HISTORICAL_HOLDING_SOURCES,
            LEGACY_HOLDING_SOURCES,
            READER_HOLDING_SOURCES,
            _default_account,
        )
        assert READER_HOLDING_SOURCES == set(GOLDEN_HOLDING_SOURCES)
        assert HISTORICAL_HOLDING_SOURCES == GOLDEN_HISTORICAL_SOURCES
        # PIS family stays hardcoded — never registry-derived
        assert LEGACY_HOLDING_SOURCES == {"PIS", "PIS_SQLite", "PIS_Excel", "PIS_Historical"}
        for system, account in GOLDEN_DEFAULT_ACCOUNTS.items():
            assert _default_account(system) == account
        assert _default_account("Financial_Summary_Excel") == "Unknown"
        assert _default_account("Nonexistent") == "Unknown"

    def test_sync_audit_maps(self):
        from src.validation.sync_audit import _KEY_TO_SYSTEM, _SYSTEM_TO_KEY
        assert _KEY_TO_SYSTEM == GOLDEN_KEY_TO_SYSTEM
        assert _SYSTEM_TO_KEY == {v: k for k, v in GOLDEN_KEY_TO_SYSTEM.items()}


# ---------------------------------------------------------------------------
# Forward compatibility: a new source must extend, never be dropped (ADR-014).
# Workstream C1 added Broker_IBKR (7th source). Test that an 8th source also
# appends correctly via the same mechanism.
# ---------------------------------------------------------------------------

class TestSeventhSourceExtension:
    def test_new_source_appends(self, tmp_path):
        """Adding an 8th source appends to all_source_systems / holding_source_systems."""
        import shutil

        from src.sources.registry import _load_registry

        src_dir = Path(__file__).parent.parent.parent / "config" / "readers"
        for f in src_dir.glob("*.yaml"):
            shutil.copy(f, tmp_path / f.name)
        # Add a hypothetical 8th source (e.g. Tiger Broker)
        (tmp_path / "tiger.yaml").write_text(
            """
identity:
  source_key: tiger
  source_system: Broker_Tiger
  display_label: "Tiger Broker"
  display_name: "Tiger"
  asset_prefixes: [US_STK_]
  allowed_extensions: [.csv]
  category: reader
  validator: null
""",
            encoding="utf-8",
        )
        reg = _load_registry(tmp_path)
        # Broker_Tiger sorts after Broker_IBKR alphabetically
        assert "Broker_Tiger" in reg.all_source_systems()
        assert "Broker_Tiger" in reg.holding_source_systems()
        assert "tiger" in reg.key_known_list()
        # Existing sources are not dropped
        assert "Broker_IBKR" in reg.all_source_systems()
        assert "Schwab_CSV" in reg.all_source_systems()


class TestMissingReaderDegradesInsteadOfCrashing:
    """Program OSR WS-2 step 4 — a self-hoster who doesn't use every reader
    (e.g. no RSU vests) must be able to omit that YAML and still boot, not
    hit a startup crash. registry.py:__init__ used to raise ValueError on
    any missing canonical reader; it now warns and degrades."""

    def _copy_only(self, tmp_path, keep: "set[str]"):
        """Copy config/readers/*.yaml into tmp_path, keeping only `keep`
        filenames (e.g. {'schwab.yaml', 'cn_fund.yaml'})."""
        import shutil

        src_dir = Path(__file__).parent.parent.parent / "config" / "readers"
        for f in src_dir.glob("*.yaml"):
            if f.name in keep:
                shutil.copy(f, tmp_path / f.name)

    def test_boots_with_only_2_of_7_readers(self, tmp_path, caplog):
        import logging

        from src.sources.registry import _load_registry

        self._copy_only(tmp_path, {"schwab.yaml", "cn_fund.yaml"})

        with caplog.at_level(logging.WARNING, logger="src.sources.registry"):
            reg = _load_registry(tmp_path)  # must NOT raise

        assert set(reg.reader_keys()) == {"schwab", "cn_fund"}
        assert any(
            "missing configs for source system" in r.message for r in caplog.records
        )

    def test_all_source_systems_excludes_the_missing_ones(self, tmp_path):
        """The degrade-safety fix: a missing canonical reader must not appear
        as a phantom entry with no config behind it (all_source_systems()
        used to unconditionally include the full canonical tuple)."""
        from src.sources.registry import _load_registry

        self._copy_only(tmp_path, {"schwab.yaml", "cn_fund.yaml"})
        reg = _load_registry(tmp_path)

        systems = reg.all_source_systems()
        assert "Schwab_CSV" in systems
        assert "CN_Fund_Excel" in systems
        for missing_system in (
            "Financial_Summary_Excel", "Gold_Excel", "Insurance_Excel", "RSU_Excel",
        ):
            assert missing_system not in systems

    def test_holding_source_systems_and_key_known_list_also_degrade(self, tmp_path):
        from src.sources.registry import _load_registry

        self._copy_only(tmp_path, {"schwab.yaml", "cn_fund.yaml"})
        reg = _load_registry(tmp_path)

        assert set(reg.holding_source_systems()) == {"Schwab_CSV", "CN_Fund_Excel"}
        assert reg.key_known_list() == ["schwab", "cn_fund"]

    def test_all_7_present_is_unaffected(self, tmp_path, caplog):
        """Zero-behavior-change guard: the real config/readers/ (all 7
        present) must not warn and must return the exact golden values."""
        import logging
        import shutil

        from src.sources.registry import _load_registry

        src_dir = Path(__file__).parent.parent.parent / "config" / "readers"
        for f in src_dir.glob("*.yaml"):
            shutil.copy(f, tmp_path / f.name)

        with caplog.at_level(logging.WARNING, logger="src.sources.registry"):
            reg = _load_registry(tmp_path)

        assert reg.all_source_systems() == GOLDEN_ALL_SOURCE_SYSTEMS
        assert not any(
            "missing configs for source system" in r.message for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Import hygiene
# ---------------------------------------------------------------------------

class TestImportHygiene:
    def test_registry_imports_clean_in_fresh_interpreter(self):
        """Registry must import (and load) without pulling in api/validation/sync
        layers and without touching the database."""
        code = (
            "import sys\n"
            "from src.sources.registry import get_registry\n"
            "r = get_registry()\n"
            # Workstream C1 added ibkr as the 7th source; assert >= 7 to be forward-compatible
            "assert len(r.reader_keys()) >= 7, f'expected >=7 reader keys, got {len(r.reader_keys())}'\n"
            "banned = [m for m in sys.modules if m.startswith(('src.api', 'src.validation', 'src.sync', 'src.services', 'src.database'))]\n"
            "assert not banned, f'registry import pulled in: {banned}'\n"
            "print('OK')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout
