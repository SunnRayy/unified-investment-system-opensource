"""Tests for the declarative pipeline manifest (Phase A2)."""

import pytest

pytestmark = pytest.mark.pipeline

import src.sync.orchestrator as orch
from src.sync.phases.manifest import PIPELINE_MANIFEST, PhaseContext, PhaseSpec


def test_manifest_ids_are_unique_and_ordered():
    ids = [spec.phase_id for spec in PIPELINE_MANIFEST]
    assert ids == sorted(set(ids), key=lambda x: int(x[1:]))
    assert ids[0] == "P0"


def test_every_manifest_runner_exists_and_is_dispatchable():
    for spec in PIPELINE_MANIFEST:
        assert hasattr(orch, spec.runner), f"orchestrator missing {spec.runner}"
        assert callable(getattr(orch, spec.runner))
        assert spec.runner in orch._PHASE_DISPATCH, f"no dispatch adapter for {spec.runner}"


def test_dispatch_has_no_orphan_entries():
    manifest_runners = {spec.runner for spec in PIPELINE_MANIFEST}
    assert set(orch._PHASE_DISPATCH) == manifest_runners


def test_dsa_ingest_not_in_pipeline():
    """Phase A2: the deprecated DSA SQLite ingest must not be reachable from the sync."""
    assert not hasattr(orch, "sync_market_data")
    assert all("dsa" not in spec.runner for spec in PIPELINE_MANIFEST)


def test_run_full_sync_v3_executes_phases_in_manifest_order(monkeypatch):
    """Patching the module attributes must intercept execution (late binding)."""
    executed = []

    def make_recorder(name):
        def recorder(*args, **kwargs):
            executed.append(name)
        return recorder

    for spec in PIPELINE_MANIFEST:
        monkeypatch.setattr(orch, spec.runner, make_recorder(spec.runner))
    monkeypatch.setattr(orch, "_capture_sync_summary", lambda connector: {})

    result = orch.run_full_sync_v3(connector=object(), config={})

    assert executed == [spec.runner for spec in PIPELINE_MANIFEST]
    assert result.success is True


def test_phase_context_carries_pre_sync_summary():
    ctx = PhaseContext(connector=None, config={}, dry_run=True, result=None,
                       pre_sync_summary={"net_worth": 1})
    assert ctx.pre_sync_summary == {"net_worth": 1}


def test_manifest_specs_are_documented():
    for spec in PIPELINE_MANIFEST:
        assert isinstance(spec, PhaseSpec)
        assert spec.name and spec.description, f"{spec.phase_id} lacks docs"
