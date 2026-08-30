"""WS-2 acceptance test: an "8th source" (Demo Broker) syncs end-to-end
through the orchestrator's registry-driven auto-dispatch (ADR-018) WITHOUT
editing anything under src/sources/.

The three files that make this work all live outside src/sources/:
  examples/adding-a-source/reader.yaml    — identity + parsing declaration
  examples/adding-a-source/plugin_hook.py — the one hook, calls register_hook()
  examples/adding-a-source/holdings.csv   — the tiny fixture being read

This test doubles as the worked example for docs/adding-a-source.md (WS-5) —
see plugins/README.md for the plugins/hooks/ drop-in convention this
exercises via discover_plugin_hooks(plugins_dir=...).

Isolation: the real config/readers/ singleton (get_registry()) and the real
plugins/hooks/ scan are never touched — src.sources.registry._resolve_config_dir
and src.sources.reader_config.load_reader_config are monkeypatched for the
duration of this test only, redirecting to a tmp_path copy of the real 7
reader YAMLs plus this example's demo_broker.yaml (mirrors
tests/sources/test_registry.py::TestSeventhSourceExtension's established
pattern). The hook registry (src.sources.hooks.HOOKS) IS process-global and
genuinely mutated by discover_plugin_hooks() below — the autouse fixture
restores it after the test, same pattern as test_hooks_registry.py.
"""
from __future__ import annotations

import shutil
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.pipeline

import src.sources.hooks as hooks_module
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.sources import reader_config as reader_config_module
from src.sources import registry as registry_module
from src.sources.hooks import HOOKS, discover_plugin_hooks
from src.sources.registry import reset_registry
from src.sync.orchestrator import run_full_sync_v3

EXAMPLE_DIR = Path(__file__).parent.parent.parent / "examples" / "adding-a-source"
REAL_READERS_DIR = Path(__file__).parent.parent.parent / "config" / "readers"


@pytest.fixture(autouse=True)
def _clean_hooks_registry():
    hooks_before = dict(HOOKS)
    plugin_names_before = set(hooks_module._plugin_registered_names)
    discovered_before = hooks_module._plugins_discovered
    try:
        yield
    finally:
        HOOKS.clear()
        HOOKS.update(hooks_before)
        hooks_module._plugin_registered_names.clear()
        hooks_module._plugin_registered_names.update(plugin_names_before)
        hooks_module._plugins_discovered = discovered_before


@pytest.fixture
def demo_reader_dir(tmp_path):
    """A config/readers/-equivalent directory: the real 7 YAMLs + demo_broker.yaml."""
    readers_dir = tmp_path / "readers"
    readers_dir.mkdir()
    for f in REAL_READERS_DIR.glob("*.yaml"):
        shutil.copy(f, readers_dir / f.name)
    shutil.copy(EXAMPLE_DIR / "reader.yaml", readers_dir / "demo_broker.yaml")
    return readers_dir


@pytest.fixture
def isolated_registry(demo_reader_dir):
    """Point the registry singleton at demo_reader_dir for this test only,
    without ever touching the real config/readers/ directory or its cache."""
    reset_registry()

    def _fake_resolve_config_dir(override):
        return override if override is not None else demo_reader_dir

    # _run_config_reader (src/sync/orchestrator.py) hardcodes
    # Path(f"config/readers/{reader_key}.yaml") independently of the
    # registry's own resolved directory — redirect just the one filename
    # this test cares about; every other reader key's real YAML still loads
    # from the real config/readers/ via the untouched original function.
    original_load_reader_config = reader_config_module.load_reader_config

    def _redirecting_load_reader_config(path):
        path = Path(path)
        if path.name == "demo_broker.yaml":
            path = demo_reader_dir / "demo_broker.yaml"
        return original_load_reader_config(path)

    with patch.object(
        registry_module, "_resolve_config_dir", side_effect=_fake_resolve_config_dir
    ), patch.object(
        reader_config_module, "load_reader_config", side_effect=_redirecting_load_reader_config
    ):
        yield demo_reader_dir

    reset_registry()


@pytest.fixture
def connector():
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    yield conn
    conn.close()


def _sync_baseline_patches():
    """Mirrors tests/sync/test_orchestrator_reader_insertion.py's
    _patch_baseline(): mock every orchestrator dependency NOT under test, so
    only P2's registry-driven dispatch is exercised for real."""
    mock_refresh_result = {
        "refreshed": 0, "skipped": 0, "errors": 0, "holdings_updated": 0,
        "fx_rates": {}, "refreshed_assets": [], "skipped_assets": [], "error_assets": [],
    }
    stack = ExitStack()
    stack.enter_context(patch("src.sync.orchestrator.create_backup", return_value="/tmp/mock.duckdb"))
    stack.enter_context(patch("src.sync.orchestrator.create_classification_tables"))
    stack.enter_context(
        patch("src.sync.orchestrator.sync_asset_registry", return_value={"registry_inserted": 0})
    )
    stack.enter_context(
        patch("src.sync.orchestrator.sync_current_allocations", return_value={"synced": 0})
    )
    stack.enter_context(patch("src.sync.orchestrator.validate_cost_basis", return_value=[]))
    stack.enter_context(patch("src.sync.orchestrator.validate_allocations", return_value=[]))
    stack.enter_context(
        patch(
            "src.market_data.service.MarketDataService.refresh_portfolio_prices",
            return_value=mock_refresh_result,
        )
    )
    return stack


class TestPluginHookEndToEnd:
    def test_demo_broker_syncs_through_orchestrator_auto_dispatch(
        self, connector, isolated_registry
    ):
        # 1. Register the plugin hook exactly the way a real self-hoster's
        #    drop-in file would be discovered — via the documented
        #    plugins/hooks/ convention, pointed at this example directory
        #    instead of the (gitignored, empty-in-this-repo) real one.
        newly_registered = discover_plugin_hooks(plugins_dir=EXAMPLE_DIR)
        assert newly_registered == ["demo_broker_holdings_from_csv"]

        # 2. Build a config that disables every built-in reader and enables
        #    only demo_broker, pointed at the example's tiny CSV fixture.
        config = {
            "sources": {"pis": {}},
            "validation": {
                "freshness": {"enabled": False},
                "taxonomy": {"enabled": False},
                "cost_basis": {"threshold_pct": 1.0},
                "allocations": {"drift_threshold_pct": 5.0},
            },
            "source_registry": {
                "schwab": {"enabled": False},
                "cn_fund": {"enabled": False},
                "gold": {"enabled": False},
                "insurance": {"enabled": False},
                "rsu": {"enabled": False},
                "financial_summary": {"enabled": False},
                "ibkr": {"enabled": False},
                "demo_broker": {"enabled": True, "data_dir": str(EXAMPLE_DIR)},
            },
        }

        # 3. Run the REAL orchestrator entry point — the "end-to-end through
        #    the orchestrator's auto-dispatch" requirement. Only the pieces
        #    unrelated to what's under test are mocked (P0/P1/P3/allocations
        #    — same set test_orchestrator_reader_insertion.py mocks).
        with _sync_baseline_patches():
            result = run_full_sync_v3(connector, config)

        # 4. The two CSV rows landed in `holdings`, produced entirely via
        #    the plugin hook + registry-driven dispatch — zero src/sources/
        #    code changes.
        rows = connector.execute(
            """
            SELECT asset_id, asset_name, quantity, market_price_unit, market_value,
                   currency, source_system, account
            FROM holdings
            WHERE source_system = 'Demo_Broker_CSV'
            ORDER BY asset_id
            """
        ).fetchall()

        assert len(rows) == 2
        demo_row, widget_row = rows

        assert demo_row[0] == "DEMO_DEMO"
        assert demo_row[1] == "DEMO"
        assert float(demo_row[2]) == 10.0
        assert float(demo_row[3]) == 25.50
        assert float(demo_row[4]) == 255.0
        assert demo_row[5] == "CNY"
        assert demo_row[6] == "Demo_Broker_CSV"
        assert demo_row[7] == "Demo Broker"

        assert widget_row[0] == "DEMO_WIDGET"
        assert float(widget_row[2]) == 5.0
        assert float(widget_row[3]) == 100.0
        assert float(widget_row[4]) == 500.0

        assert result.holdings_synced >= 2
