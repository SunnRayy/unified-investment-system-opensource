"""Tests for the additive plugin registry API (Program OSR WS-2 step 2):
register_hook(), discover_plugin_hooks(), and get_hook()'s fallback-discovery
behavior.

Hermetic — no real filesystem scan of the project's own plugins/hooks/ (that
directory is empty in this repo anyway); each test that exercises directory
discovery points discover_plugin_hooks() at a tmp_path fixture instead.

src.sources.hooks.HOOKS / _plugin_registered_names / _plugins_discovered are
process-global mutable state (by design — see the module docstring), so every
test here restores them via the _clean_hooks_registry fixture. Without it,
a hook registered by one test would leak into every test that runs after it
in the same worker process.
"""
from __future__ import annotations

import pandas as pd
import pytest

pytestmark = pytest.mark.pipeline

import src.sources.hooks as hooks_module
from src.sources.hooks import HOOKS, discover_plugin_hooks, get_hook, register_hook


@pytest.fixture(autouse=True)
def _clean_hooks_registry():
    """Snapshot + restore the registry's mutable module state around each test."""
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


def _noop_hook(df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
    return df


class TestRegisterHook:
    def test_new_name_is_added_to_hooks(self):
        register_hook("test_plugin_hook_a", _noop_hook)
        assert HOOKS["test_plugin_hook_a"] is _noop_hook
        assert get_hook("test_plugin_hook_a") is _noop_hook

    def test_shadowing_a_builtin_warns_but_still_overrides(self, caplog):
        import logging

        assert "derive_rsu_holdings" in HOOKS
        with caplog.at_level(logging.WARNING, logger="src.sources.hooks"):
            register_hook("derive_rsu_holdings", _noop_hook)
        assert HOOKS["derive_rsu_holdings"] is _noop_hook
        assert any("shadowing a BUILT-IN hook" in r.message for r in caplog.records)

    def test_reregistering_own_plugin_name_does_not_warn(self, caplog):
        import logging

        register_hook("test_plugin_hook_b", _noop_hook)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="src.sources.hooks"):
            register_hook("test_plugin_hook_b", _noop_hook)
        assert not any("shadowing" in r.message for r in caplog.records)

    def test_builtin_hooks_are_unaffected_by_an_unrelated_registration(self):
        before = dict(HOOKS)
        register_hook("test_plugin_hook_c", _noop_hook)
        for name, fn in before.items():
            assert HOOKS[name] is fn


class TestDiscoverPluginHooks:
    def test_discovers_and_registers_from_directory(self, tmp_path):
        plugin_file = tmp_path / "demo.py"
        plugin_file.write_text(
            "from src.sources.hooks import register_hook\n"
            "def demo_fn(df, metadata):\n"
            "    return df\n"
            "register_hook('test_discovered_hook', demo_fn)\n"
        )
        newly = discover_plugin_hooks(plugins_dir=tmp_path)
        assert newly == ["test_discovered_hook"]
        assert "test_discovered_hook" in HOOKS
        fn = get_hook("test_discovered_hook")
        result = fn(pd.DataFrame({"a": [1]}), {})
        assert list(result["a"]) == [1]

    def test_underscore_prefixed_files_are_skipped(self, tmp_path):
        (tmp_path / "_helper.py").write_text("raise RuntimeError('must not import')")
        newly = discover_plugin_hooks(plugins_dir=tmp_path)
        assert newly == []

    def test_broken_plugin_file_is_skipped_not_fatal(self, tmp_path, caplog):
        import logging

        (tmp_path / "broken.py").write_text("raise RuntimeError('boom')")
        (tmp_path / "good.py").write_text(
            "from src.sources.hooks import register_hook\n"
            "def good_fn(df, metadata):\n"
            "    return df\n"
            "register_hook('test_good_hook', good_fn)\n"
        )
        with caplog.at_level(logging.WARNING, logger="src.sources.hooks"):
            newly = discover_plugin_hooks(plugins_dir=tmp_path)
        assert newly == ["test_good_hook"]
        assert any("failed to load" in r.message for r in caplog.records)

    def test_nonexistent_directory_returns_empty(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        assert discover_plugin_hooks(plugins_dir=missing) == []

    def test_explicit_plugins_dir_does_not_set_default_discovered_flag(self, tmp_path):
        hooks_module._plugins_discovered = False
        discover_plugin_hooks(plugins_dir=tmp_path)
        assert hooks_module._plugins_discovered is False


class TestGetHookFallbackDiscovery:
    def test_unknown_name_triggers_discovery_before_raising(self, tmp_path, monkeypatch):
        """get_hook() for a name that isn't a built-in must still resolve if a
        plugin registers it — even without the caller calling
        discover_plugin_hooks() directly — by pointing the DEFAULT scan path
        at tmp_path via monkeypatching the module's default-dir resolution."""
        plugin_file = tmp_path / "demo.py"
        plugin_file.write_text(
            "from src.sources.hooks import register_hook\n"
            "def demo_fn(df, metadata):\n"
            "    return df\n"
            "register_hook('test_lazy_discovered_hook', demo_fn)\n"
        )
        hooks_module._plugins_discovered = False
        original_discover = hooks_module.discover_plugin_hooks

        def _patched_discover(plugins_dir=None, *, force=False):
            if plugins_dir is None:
                plugins_dir = tmp_path
            return original_discover(plugins_dir=plugins_dir, force=force)

        monkeypatch.setattr(hooks_module, "discover_plugin_hooks", _patched_discover)

        # get_hook's own reference to discover_plugin_hooks is resolved via
        # the module namespace at call time, so patching the module
        # attribute above is visible to it.
        fn = hooks_module.get_hook("test_lazy_discovered_hook")
        assert fn(pd.DataFrame({"a": [1]}), {}) is not None

    def test_known_builtin_name_never_triggers_a_scan(self, monkeypatch):
        """The hot path (10 built-in names, every reader dispatch) must not
        pay for a directory scan — discover_plugin_hooks must not even be
        called when the name is already in HOOKS."""
        calls = []
        monkeypatch.setattr(
            hooks_module,
            "discover_plugin_hooks",
            lambda *a, **k: calls.append(1),
        )
        get_hook("derive_rsu_holdings")
        assert calls == []

    def test_truly_unknown_name_still_raises_keyerror(self, tmp_path):
        hooks_module._plugins_discovered = False
        with pytest.raises(KeyError, match="unknown hook 'not_a_real_hook'"):
            get_hook("not_a_real_hook")
