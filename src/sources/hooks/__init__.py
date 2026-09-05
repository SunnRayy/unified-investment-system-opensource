"""Named hook registry for the config-driven reader engine (Program OSR WS-2).

This package is the mechanical split of what used to be a single 1,578-line
module, src/sources/reader_hooks.py — one submodule per reader family
(schwab, ibkr, cn_fund, financial_summary, rsu, gold_insurance, wizard). This
__init__ assembles the same HOOKS dict / get_hook() that used to live
directly in that module; src/sources/reader_hooks.py is now a thin
backward-compatible re-export shim over this package (see that module's
docstring) — every existing `from src.sources.reader_hooks import X` keeps
working unchanged.

Hooks are optional post-transactions callables declared in a reader's
YAML via  parsing.holdings_hook: <name>.  When absent, the engine uses
its normal declarative path (unchanged for gold, insurance, etc.).

IMPORT CONSTRAINT (mirrors registry.py, unchanged from the pre-split module):
  This package imports ONLY stdlib and pandas at module level (each submodule
  enforces this independently — see their own docstrings). It NEVER imports
  from src.sync.*, src.api.*, etc. Those layers may import this package, so
  any reverse import would create a cycle. Lazy imports INSIDE a function
  body are allowed (e.g. cn_fund_raw_process, schwab's live-FX fetch).

Registry API:
  HOOKS: Dict[str, Callable]      — name → hook function
  get_hook(name) -> Callable      — raises KeyError with clear message if missing
  register_hook(name, fn)         — add/override a hook, e.g. from a plugin (WS-2 step 2)
  discover_plugin_hooks(dir=None) — import plugins/hooks/*.py so their
                                    register_hook() calls run (WS-2 step 2)

Hook signature contract:
  fn(transactions_df: pd.DataFrame, metadata: dict) -> pd.DataFrame
  The returned DataFrame represents derived holdings.

Adding a source WITHOUT editing this package (Program OSR WS-2 step 2):
  1. Write config/readers/<your-source>.yaml declaring a
     holdings_from_sheet_hook (or holdings_hook / transactions_from_sheet_hook)
     naming your hook.
  2. Drop a .py file under <repo-root>/plugins/hooks/ whose top level calls
     register_hook("your_hook_name", your_function) — see plugins/README.md
     and examples/adding-a-source/ for a complete worked example (also the
     acceptance test at tests/sources/test_plugin_hook_end_to_end.py).
  get_hook() discovers plugins/hooks/ automatically (once per process) the
  first time a name isn't already a built-in, so no other wiring is needed.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional

from src.sources.hooks.rsu import derive_rsu_holdings
from src.sources.hooks.financial_summary import (
    FS_ASSET_MAPPING,
    FS_TOMBSTONE_BLAST_RADIUS_WARN,
    _FS_ASSET_MAPPING,
    _FS_DATE_COLUMN,
    _fs_trailing_blank_tombstones,
    melt_financial_summary_holdings,
)
from src.sources.hooks.cn_fund import (
    _CN_FUND_TYPE_MAP,
    _TRANSACTION_COL_MAP,
    cn_fund_holdings_from_sheet,
    cn_fund_raw_process,
    cn_fund_transactions_from_sheet,
    normalize_fund_code,
)
from src.sources.hooks.schwab import (
    _SCHWAB_ACTION_MAPPING,
    _SCHWAB_COLUMN_ALIASES,
    _SCHWAB_KNOWN_ETFS,
    _SCHWAB_SYMBOL_NORMALIZATIONS,
    _schwab_map_action,
    _schwab_normalize_symbol,
    _schwab_normalize_to_canonical_id,
    _schwab_normalize_transaction_symbol,
    _schwab_parse_date,
    _schwab_parse_dollar,
    _schwab_parse_number,
    _schwab_parse_percent,
    schwab_holdings_from_csv,
    schwab_transactions_from_csv,
)
from src.sources.hooks.ibkr import ibkr_holdings_from_flex, ibkr_transactions_from_flex
from src.sources.hooks.wizard import (
    _WIZARD_DATE_FIELDS,
    _WIZARD_NUMERIC_FIELDS,
    wizard_holdings_from_sheet,
    wizard_transactions_from_sheet,
)

logger = logging.getLogger(__name__)

__all__ = [
    "HOOKS",
    "get_hook",
    "register_hook",
    "discover_plugin_hooks",
    "derive_rsu_holdings",
    "FS_ASSET_MAPPING",
    "FS_TOMBSTONE_BLAST_RADIUS_WARN",
    "_FS_ASSET_MAPPING",
    "_FS_DATE_COLUMN",
    "_fs_trailing_blank_tombstones",
    "melt_financial_summary_holdings",
    "normalize_fund_code",
    "cn_fund_raw_process",
    "cn_fund_holdings_from_sheet",
    "cn_fund_transactions_from_sheet",
    "_CN_FUND_TYPE_MAP",
    "_TRANSACTION_COL_MAP",
    "schwab_holdings_from_csv",
    "schwab_transactions_from_csv",
    "_SCHWAB_ACTION_MAPPING",
    "_SCHWAB_COLUMN_ALIASES",
    "_SCHWAB_KNOWN_ETFS",
    "_SCHWAB_SYMBOL_NORMALIZATIONS",
    "_schwab_map_action",
    "_schwab_normalize_symbol",
    "_schwab_normalize_to_canonical_id",
    "_schwab_normalize_transaction_symbol",
    "_schwab_parse_date",
    "_schwab_parse_dollar",
    "_schwab_parse_number",
    "_schwab_parse_percent",
    "ibkr_holdings_from_flex",
    "ibkr_transactions_from_flex",
    "wizard_holdings_from_sheet",
    "wizard_transactions_from_sheet",
    "_WIZARD_DATE_FIELDS",
    "_WIZARD_NUMERIC_FIELDS",
]

HOOKS: Dict[str, Callable] = {
    "derive_rsu_holdings": derive_rsu_holdings,
    "melt_financial_summary_holdings": melt_financial_summary_holdings,
    "cn_fund_raw_process": cn_fund_raw_process,
    "cn_fund_holdings_from_sheet": cn_fund_holdings_from_sheet,
    "cn_fund_transactions_from_sheet": cn_fund_transactions_from_sheet,
    "schwab_holdings_from_csv": schwab_holdings_from_csv,
    "schwab_transactions_from_csv": schwab_transactions_from_csv,
    "ibkr_holdings_from_flex": ibkr_holdings_from_flex,
    "ibkr_transactions_from_flex": ibkr_transactions_from_flex,
    # Wizard hooks (import-adapter convergence — A1)
    "wizard_holdings_from_sheet": wizard_holdings_from_sheet,
    "wizard_transactions_from_sheet": wizard_transactions_from_sheet,
}

# Names added via register_hook() (built-ins are never in this set) — lets
# register_hook() tell "a plugin re-registering its own name" apart from
# "a plugin shadowing a built-in" without a second dict to keep in sync.
_plugin_registered_names: "set[str]" = set()

# Whether the default plugins/hooks/ directory has already been scanned this
# process — discover_plugin_hooks() only rescans on an explicit force=True or
# an explicit plugins_dir override (tests).
_plugins_discovered = False


def get_hook(name: str) -> Callable:
    """Return the named hook callable.

    Args:
        name: Hook name as declared in parsing.holdings_hook (YAML).

    Raises:
        KeyError: If name is not registered — with a clear message listing
            available hooks.

    Returns:
        Callable with signature (transactions_df, metadata) -> pd.DataFrame.
    """
    if name not in HOOKS:
        # Lazy, once-per-process: only scan the filesystem when a name isn't
        # already a known built-in, so the 10 built-in names (the hot path —
        # every reader dispatch) never pay for a directory scan (WS-2 step 2).
        discover_plugin_hooks()
    if name not in HOOKS:
        available = ", ".join(sorted(HOOKS.keys()))
        raise KeyError(
            f"reader_hooks: unknown hook '{name}'. "
            f"Available hooks: [{available}]"
        )
    return HOOKS[name]


def register_hook(name: str, fn: Callable) -> None:
    """Register (or override) a hook callable under `name`.

    Merges OVER the built-in hooks — the intended caller is plugin code
    living OUTSIDE src/sources/ (see discover_plugin_hooks() and
    plugins/README.md), typically at the top level of a module dropped into
    plugins/hooks/. Shadowing a built-in name is allowed (this registry is
    deliberately open, not gatekept) but is logged at WARNING so an
    accidental name collision is visible rather than a silent behavior
    change.

    Args:
        name: Hook name — what a reader YAML's holdings_hook /
            holdings_from_sheet_hook / transactions_from_sheet_hook declares.
        fn: Callable with signature (df, metadata) -> pd.DataFrame.
    """
    if name in HOOKS and name not in _plugin_registered_names:
        logger.warning(
            "reader_hooks: plugin hook '%s' is shadowing a BUILT-IN hook of "
            "the same name. If this is unintentional, rename your hook.",
            name,
        )
    HOOKS[name] = fn
    _plugin_registered_names.add(name)


def discover_plugin_hooks(
    plugins_dir: Optional[Path] = None, *, force: bool = False
) -> List[str]:
    """Import every top-level .py file under a plugins/hooks directory so its
    module-level register_hook(...) calls run.

    Idempotent by default: the default directory is only scanned once per
    process (get_hook() already triggers this lazily — most callers never
    need to call this directly). A broken plugin file is logged and skipped;
    it never breaks resolution of the built-in hooks or of any other plugin.

    Args:
        plugins_dir: override the scanned directory. Default:
            <repo-root>/plugins/hooks/ — gitignored and empty in the public
            tree; a real user drops their own .py file there (see
            plugins/README.md). Passing an explicit directory (tests, the
            "8th source" acceptance test) always scans, regardless of
            `force`, and does NOT set the default-directory discovered flag.
        force: rescan the default directory even if already scanned this
            process. No effect when plugins_dir is given explicitly.

    Returns:
        Sorted list of hook names newly registered by this call (empty if
        the directory doesn't exist or nothing new was registered).
    """
    global _plugins_discovered

    if plugins_dir is None and _plugins_discovered and not force:
        return []

    resolved_dir = (
        Path(plugins_dir) if plugins_dir is not None
        else Path(__file__).resolve().parents[3] / "plugins" / "hooks"
    )

    newly_registered: List[str] = []
    if resolved_dir.is_dir():
        before = set(_plugin_registered_names)
        for py_file in sorted(resolved_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"uis_plugin_hooks.{py_file.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "reader_hooks: plugin file %s failed to load — skipped, "
                    "other hooks unaffected: %s",
                    py_file,
                    exc,
                )
        newly_registered = sorted(set(_plugin_registered_names) - before)

    if plugins_dir is None:
        _plugins_discovered = True

    return newly_registered
