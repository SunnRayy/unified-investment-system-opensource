"""Tests for GET /operations/pipeline + steps persistence (A3b).

Contract: docs/api-specs/operations-pipeline.md
"""

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_db
from src.api.main import app
from src.api.routes.operations import _staleness_bucket
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.sync.phases.manifest import PIPELINE_MANIFEST

TODAY = date.today()
D_FRESH = (TODAY - timedelta(days=4)).isoformat()
D_OLDER = (TODAY - timedelta(days=30)).isoformat()
D_AGING = (TODAY - timedelta(days=20)).isoformat()

STEPS_JSON = json.dumps([
    {"name": "P0", "status": "ok", "critical": False, "error": None, "duration_ms": 412},
    {"name": "P2", "status": "ok", "critical": False, "error": None, "duration_ms": 8231},
    {"name": "live_price_refresh", "status": "ok", "critical": False, "error": None, "duration_ms": 12000},
    {"name": "P3", "status": "failed", "critical": False, "error": "boom", "duration_ms": 90},
])


@pytest.fixture
def client():
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    connector.execute(
        f"""
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, quantity, market_value,
            currency, source_system, is_shadow, price_updated_at
        ) VALUES
          ('{D_OLDER}', 'US_STK_AAPL', 'AAPL', 5, 50000, 'CNY', 'Schwab_CSV', FALSE, NULL),
          ('{D_FRESH}', 'US_STK_AAPL', 'AAPL', 10, 100000, 'CNY', 'Schwab_CSV', FALSE, '{TODAY.isoformat()} 09:00:00'),
          ('{D_FRESH}', 'US_STK_MSFT', 'MSFT', 1, 20000, 'CNY', 'Schwab_CSV', FALSE, NULL),
          ('{D_FRESH}', 'US_STK_SHDW', 'SHDW', 1, 99999, 'CNY', 'Schwab_CSV', TRUE, NULL),
          ('{D_AGING}', 'CN_FUND_000001', 'Fund-A', 100, 30000, 'CNY', 'CN_Fund_Excel', FALSE, NULL)
        """
    )

    # Older run with steps; newest run is a legacy row (steps NULL)
    connector.execute(
        f"""
        INSERT INTO sync_audit_reports (
            id, created_at, report_type, net_worth_after, net_worth_change_pct,
            integrity_passed, integrity_total, integrity_checks, warnings, alert,
            is_no_change, steps
        ) VALUES (
            'run-with-steps', '2026-06-09 10:00:00', 'sync', 5900000.0, -1.15,
            13, 14,
            '[{{"name": "ok_check", "passed": true, "blocking": true}},
              {{"name": "adv_check", "passed": false, "blocking": false}}]',
            '["w1", "w2"]', FALSE, FALSE, '{STEPS_JSON}'
        )
        """
    )

    def _override_get_db():
        return connector

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_phases_match_manifest_order(client):
    body = client.get("/operations/pipeline").json()
    assert [p["phase_id"] for p in body["phases"]] == [s.phase_id for s in PIPELINE_MANIFEST]
    assert body["phases"][3]["name"] == "Live price refresh"
    assert isinstance(body["phases"][2]["tables_written"], list)


def test_last_run_mapping_and_step_filtering(client):
    body = client.get("/operations/pipeline").json()
    run = body["last_run"]
    assert run["id"] == "run-with-steps"
    assert run["integrity_result"] == "13/14"
    assert run["integrity_status"] == "degraded"
    assert run["warning_count"] == 2
    assert run["net_worth_change_pct"] == -1.15
    # Only P-entries surface; live_price_refresh is filtered out
    ids = [s["phase_id"] for s in run["steps"]]
    assert ids == ["P0", "P2", "P3"]
    p3 = run["steps"][2]
    assert p3["status"] == "failed"
    assert p3["error"] == "boom"
    assert run["steps"][0]["name"] == "Backup & schema setup"


def test_last_run_null_steps_for_legacy_row(client):
    # Insert a NEWER legacy row without steps — it becomes last_run with steps=None
    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO sync_audit_reports (
            id, created_at, report_type, integrity_passed, integrity_total, alert
        ) VALUES ('run-legacy', '2026-06-10 10:00:00', 'sync', 14, 14, FALSE)
        """
    )
    run = client.get("/operations/pipeline").json()["last_run"]
    assert run["id"] == "run-legacy"
    assert run["steps"] is None


def test_freshness_per_asset_latest_and_shadow_exclusion(client):
    body = client.get("/operations/pipeline").json()
    by_source = {s["source_system"]: s for s in body["sources"]}

    schwab = by_source["Schwab_CSV"]
    # AAPL counts once at its NEWEST snapshot (older row ignored); SHDW excluded
    assert schwab["active_assets"] == 2
    assert schwab["total_value_cny"] == 120000.0
    assert schwab["latest_snapshot"] == D_FRESH
    assert schwab["snapshot_age_days"] == 4
    assert schwab["staleness"] == "fresh"
    assert schwab["price_refreshed_assets"] == 1
    assert schwab["last_price_refresh"] is not None
    assert schwab["display_name"] == "Schwab"

    cn = by_source["CN_Fund_Excel"]
    assert cn["active_assets"] == 1
    assert cn["staleness"] == "aging"
    assert cn["last_price_refresh"] is None
    assert cn["display_name"] == "CN Funds"

    # Ordered by latest_snapshot DESC
    assert body["sources"][0]["source_system"] == "Schwab_CSV"


def test_staleness_bucket_edges():
    assert _staleness_bucket(14) == "fresh"
    assert _staleness_bucket(15) == "aging"
    assert _staleness_bucket(45) == "aging"
    assert _staleness_bucket(46) == "stale"


def test_sync_history_detail_includes_steps(client):
    body = client.get("/operations/sync-history/run-with-steps").json()
    assert [s["phase_id"] for s in body["steps"]] == ["P0", "P2", "P3"]


def test_pipeline_with_empty_audit_table():
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    app.dependency_overrides[get_db] = lambda: connector
    try:
        body = TestClient(app).get("/operations/pipeline").json()
        assert body["last_run"] is None
        assert body["sources"] == []
        assert len(body["phases"]) == len(PIPELINE_MANIFEST)
    finally:
        app.dependency_overrides.clear()


def test_orchestrator_records_phase_steps(monkeypatch):
    """run_full_sync_v3 records one StepResult per manifest phase."""
    import src.sync.orchestrator as orch

    for spec in PIPELINE_MANIFEST:
        monkeypatch.setattr(orch, spec.runner, lambda *a, **k: None)
    monkeypatch.setattr(orch, "_capture_sync_summary", lambda connector: {})

    result = orch.run_full_sync_v3(connector=object(), config={})

    phase_steps = [s for s in result.steps if s.name in {x.phase_id for x in PIPELINE_MANIFEST}]
    assert [s.name for s in phase_steps] == [s.phase_id for s in PIPELINE_MANIFEST]
    assert all(s.status == "ok" and s.duration_ms >= 0 for s in phase_steps)


def test_orchestrator_records_failed_step_and_continues(monkeypatch):
    import src.sync.orchestrator as orch

    def boom(*a, **k):
        raise RuntimeError("phase crashed")

    for spec in PIPELINE_MANIFEST:
        monkeypatch.setattr(orch, spec.runner, lambda *a, **k: None)
    monkeypatch.setattr(orch, "_run_phase3_price_refresh", boom)
    monkeypatch.setattr(orch, "_capture_sync_summary", lambda connector: {})

    result = orch.run_full_sync_v3(connector=object(), config={})

    by_name = {s.name: s for s in result.steps}
    assert by_name["P3"].status == "failed"
    assert "phase crashed" in (by_name["P3"].error or "")
    # later phases still ran
    assert by_name["P8"].status == "ok"
    assert any("P3" in w for w in result.warnings)


def test_persist_round_trip_with_steps():
    from src.validation.sync_audit import (
        SyncAuditReport, get_sync_audit_detail, persist_sync_audit,
    )

    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    report = SyncAuditReport(
        sync_id="rt-1", timestamp="2026-06-10T12:00:00",
        net_worth_before=1.0, net_worth_after=2.0, net_worth_change_pct=0.5,
        asset_count_before=1, asset_count_after=1,
        by_source_before={}, by_source_after={},
        integrity_passed=14, integrity_total=14, integrity_checks=[],
        reader_counts={}, warnings=[], alert=False,
        steps=[{"name": "P0", "status": "ok", "critical": False, "error": None, "duration_ms": 5}],
    )
    persist_sync_audit(connector, report)
    detail = get_sync_audit_detail(connector, "rt-1")
    assert detail["steps"] == [
        {"name": "P0", "status": "ok", "critical": False, "error": None, "duration_ms": 5}
    ]


def test_full_sync_persists_all_phase_steps_including_p8():
    """End-to-end: a real run_full_sync_v3 against an in-memory DB persists
    P0..P8 step entries (P8 via the synthetic entry appended in
    _run_phase8_audit — review F1)."""
    from unittest.mock import patch

    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    with patch("src.sync.orchestrator.create_backup"), \
         patch("src.sync.orchestrator.sync_asset_registry", return_value={"registry_inserted": 0}), \
         patch("src.sync.orchestrator.sync_current_allocations", return_value={"synced": 0}), \
         patch("src.sync.orchestrator.validate_cost_basis", return_value=[]), \
         patch("src.sync.orchestrator.validate_allocations", return_value=[]):
        from src.sync.orchestrator import run_full_sync_v3
        run_full_sync_v3(connector, {"source_registry": {}})

    row = connector.execute(
        "SELECT steps FROM sync_audit_reports ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert row is not None and row[0] is not None
    names = [s["name"] for s in json.loads(row[0])]
    phase_names = [n for n in names if len(n) == 2 and n.startswith("P")]
    assert phase_names == ["P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"]
