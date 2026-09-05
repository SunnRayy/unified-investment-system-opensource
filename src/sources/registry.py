"""Source registry — single source of truth for reader/source-name constants.

Loads every config/readers/*.yaml at first use (lazy singleton via
get_registry()).  Provides typed accessors whose shapes match every consumer
in the touchpoint inventory so the constants in api/validation/sync/services
modules can be derived here at import time.

IMPORT CONSTRAINT: this module imports only stdlib, yaml, pydantic, and
src.sources.reader_config.  It NEVER imports from api/, validation/, sync/, or
services/ — those layers import the registry, so any reverse import would
create a cycle.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


from src.sources.reader_config import ReaderConfig, load_reader_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical source-system ordering
# ---------------------------------------------------------------------------
# This ordering is fixed to match today's operations.READER_SOURCES tuple.
# It is the ONLY place the order is declared — consumers derive it from here.
# Order: Schwab_CSV, CN_Fund_Excel, Financial_Summary_Excel,
#        Gold_Excel, Insurance_Excel, RSU_Excel
_CANONICAL_SYSTEM_ORDER: Tuple[str, ...] = (
    "Schwab_CSV",
    "CN_Fund_Excel",
    "Financial_Summary_Excel",
    "Gold_Excel",
    "Insurance_Excel",
    "RSU_Excel",
)

# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------

class SourceRegistry:
    """Immutable view over all loaded reader configs.

    Do not instantiate directly — use get_registry() for the singleton.
    """

    def __init__(self, configs: Dict[str, ReaderConfig]) -> None:
        # keyed by source_key (e.g. "schwab", "cn_fund")
        self._configs: Dict[str, ReaderConfig] = configs

        # Warn + degrade on a missing canonical reader (Program OSR WS-2 step
        # 4) rather than hard-fail: a self-hoster with, say, no RSU vests
        # should be able to omit config/readers/rsu.yaml and run with the
        # other 6, not get a startup crash. Every accessor below derives from
        # self._configs (the readers that ARE present) rather than assuming
        # all 6 canonical systems exist — see all_source_systems()'s
        # canonical_present filter, the fix that makes this safe: before this
        # change, __init__ was the ONLY thing guaranteeing that assumption
        # held, so relaxing it here without that fix would have let a missing
        # reader silently reappear downstream with no config behind it.
        loaded_systems = {c.identity.source_system for c in configs.values()}
        missing = set(_CANONICAL_SYSTEM_ORDER) - loaded_systems
        if missing:
            logger.warning(
                "SourceRegistry: missing configs for source system(s): %s — "
                "running with %d of %d built-in readers. Add "
                "config/readers/<key>.yaml to enable a missing one, or "
                "ignore this if you intentionally don't use it.",
                sorted(missing),
                len(loaded_systems & set(_CANONICAL_SYSTEM_ORDER)),
                len(_CANONICAL_SYSTEM_ORDER),
            )

    # -----------------------------------------------------------------------
    # Core accessors
    # -----------------------------------------------------------------------

    def reader_keys(self) -> List[str]:
        """All 6 source keys (lowercase), e.g. ['schwab', 'cn_fund', ...]."""
        return list(self._configs.keys())

    def all_source_systems(self) -> Tuple[str, ...]:
        """All source_system strings: canonical order first, then any newly
        registered systems (e.g. Broker_IBKR in Workstream C) appended sorted.

        Canonical prefix matches today's operations.READER_SOURCES exactly:
        Schwab_CSV, CN_Fund_Excel, Financial_Summary_Excel,
        Gold_Excel, Insurance_Excel, RSU_Excel

        A canonical system with no loaded config (Program OSR WS-2 step 4 —
        __init__ warns rather than hard-fails on a missing reader) is
        SKIPPED, not included: every entry returned here must have a real
        config behind it, or a caller doing registry.key_to_system() /
        validator_map() etc. for it would hit a silent gap.
        """
        loaded = {c.identity.source_system for c in self._configs.values()}
        canonical_present = tuple(s for s in _CANONICAL_SYSTEM_ORDER if s in loaded)
        extras = sorted(loaded - set(_CANONICAL_SYSTEM_ORDER))
        return canonical_present + tuple(extras)

    def holding_source_systems(self) -> Tuple[str, ...]:
        """Reader-category source systems (tuple, no Financial_Summary_Excel).

        These are the sources that contribute non-shadow holdings — matches
        READER_HOLDING_SOURCES in _common.py / data_integrity_gate.py /
        cost_basis_validator.py / portfolio_semantics.py. New reader-category
        sources (Workstream C) are included automatically.
        """
        return tuple(
            sys
            for sys in self.all_source_systems()
            if self._system_category(sys) == "reader"
        )

    def historical_source_systems(self) -> Set[str]:
        """Source systems with category == 'historical' (e.g. {Financial_Summary_Excel}).

        Returned as a set — matches HISTORICAL_HOLDING_SOURCES in _common.py.
        """
        return {
            c.identity.source_system
            for c in self._configs.values()
            if c.identity.category == "historical"
        }

    def key_to_system(self) -> Dict[str, str]:
        """Map source_key → source_system, e.g. {'schwab': 'Schwab_CSV', ...}.

        Matches _READER_LABEL_MAP in settings.py.
        """
        return {
            key: cfg.identity.source_system
            for key, cfg in self._configs.items()
        }

    def system_to_key(self) -> Dict[str, str]:
        """Map source_system → source_key (inverse of key_to_system)."""
        return {
            cfg.identity.source_system: key
            for key, cfg in self._configs.items()
        }

    def allowed_extensions(self) -> Dict[str, Set[str]]:
        """Map source_key → set of allowed file extensions.

        Matches _READER_ALLOWED_EXTS in settings.py.
        """
        return {
            key: set(cfg.identity.allowed_extensions)
            for key, cfg in self._configs.items()
        }

    def validator_map(self) -> Dict[str, str]:
        """Map source_key → validator function name (if any).

        Matches _VALIDATOR_MAP in settings.py.  Keys with no validator are
        omitted (validator field is None).
        """
        return {
            key: cfg.identity.validator
            for key, cfg in self._configs.items()
            if cfg.identity.validator is not None
        }

    def asset_prefixes(self) -> Dict[str, List[str]]:
        """Map source_key → list of asset ID prefixes for this source."""
        return {
            key: list(cfg.identity.asset_prefixes)
            for key, cfg in self._configs.items()
        }

    # -----------------------------------------------------------------------
    # Display-name accessors
    # -----------------------------------------------------------------------

    def source_display_names(self) -> Dict[str, str]:
        """Map source_system → short English display name.

        Matches _SOURCE_DISPLAY_NAMES in operations.py (freshness panel).
        e.g. {'Schwab_CSV': 'Schwab', 'CN_Fund_Excel': 'CN Funds', ...}
        """
        return {
            cfg.identity.source_system: cfg.identity.display_name
            for cfg in self._configs.values()
        }

    def default_account_names(self) -> Dict[str, str]:
        """Map source_system → default account string.

        Matches the dict literal in _default_account() in _common.py.
        Excludes Financial_Summary_Excel (category='historical') which has
        no entry in the original dict.

        NOTE: CN_Fund_Excel resolves to 'CN Fund' (account_name field), not
        'CN Funds' (display_name) — this is the only source where these differ.
        """
        result: Dict[str, str] = {}
        for cfg in self._configs.values():
            if cfg.identity.category != "reader":
                continue
            identity = cfg.identity
            name = identity.account_name if identity.account_name is not None else identity.display_name
            result[identity.source_system] = name
        return result

    def key_known_list(self) -> List[str]:
        """Ordered list of source keys (matches _KNOWN_SOURCE_READERS in settings.py).

        Preserves today's hardcoded order from settings.py; any newly
        registered keys (e.g. 'ibkr' in Workstream C) are appended sorted so
        new sources are never silently dropped.
        """
        _KEY_ORDER = ["schwab", "cn_fund", "gold", "insurance", "rsu", "financial_summary"]
        known = [k for k in _KEY_ORDER if k in self._configs]
        extras = sorted(set(self._configs) - set(_KEY_ORDER))
        return known + extras

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _system_category(self, source_system: str) -> Optional[str]:
        """Return category for a source_system string, or None if not found."""
        for cfg in self._configs.values():
            if cfg.identity.source_system == source_system:
                return cfg.identity.category
        return None

    def _config_for_key(self, key: str) -> Optional[ReaderConfig]:
        return self._configs.get(key)


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

_registry_instance: Optional[SourceRegistry] = None
_registry_lock = threading.Lock()


def reset_registry() -> None:
    """Drop the cached singleton so the next get_registry() reloads config/readers/*.yaml.

    Called by generate_reader_artifacts() after writing a new reader YAML so
    the freshly-created config is picked up without a server restart (ADR-018 Phase 3).
    """
    global _registry_instance
    with _registry_lock:
        _registry_instance = None


def get_registry(config_dir: Optional[Path] = None) -> SourceRegistry:
    """Return the singleton SourceRegistry, loading it on first call.

    Args:
        config_dir: override the config/readers/ directory (useful in tests).
            Default: <repo-root>/config/readers/ resolved from this file's
            location (Path(__file__).resolve().parents[2] / "config" / "readers").
    """
    global _registry_instance

    if _registry_instance is not None and config_dir is None:
        return _registry_instance

    with _registry_lock:
        # Double-checked locking: another thread may have initialised it.
        if _registry_instance is not None and config_dir is None:
            return _registry_instance

        resolved_dir = _resolve_config_dir(config_dir)
        instance = _load_registry(resolved_dir)

        if config_dir is None:
            _registry_instance = instance

        return instance


def _resolve_config_dir(override: Optional[Path]) -> Path:
    if override is not None:
        return override
    # src/sources/registry.py → parents[0]=src/sources, parents[1]=src, parents[2]=repo root
    return Path(__file__).resolve().parents[2] / "config" / "readers"


def _load_registry(config_dir: Path) -> SourceRegistry:
    if not config_dir.exists():
        raise FileNotFoundError(
            f"SourceRegistry: config directory not found: {config_dir}"
        )
    yaml_files = sorted(config_dir.glob("*.yaml"))
    if not yaml_files:
        raise FileNotFoundError(
            f"SourceRegistry: no *.yaml files found in {config_dir}"
        )
    configs: Dict[str, ReaderConfig] = {}
    for path in yaml_files:
        rc = load_reader_config(path)
        key = rc.identity.source_key
        if key in configs:
            raise ValueError(
                f"SourceRegistry: duplicate source_key '{key}' found in "
                f"{path} and an earlier file."
            )
        configs[key] = rc
    return SourceRegistry(configs)
