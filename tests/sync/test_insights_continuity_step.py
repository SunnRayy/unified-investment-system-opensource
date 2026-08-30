"""Tests for P9 insights_continuity orchestrator step.

Covers:
  - Unit: all five sub-calls invoked with the SAME conn object; one sub-call
    raising → others still run; step is advisory (never sets sync failure);
    warning logged on failure.
  - Freshness gate: verification_logs with a fresh row (<24 h) →
    compute_verification_report NOT called; stale/empty → called.
  - Integration: P9 registered in PIPELINE_MANIFEST after P8 with the
    correct runner name.
"""

from __future__ import annotations

import logging
from datetime import date
from unittest.mock import MagicMock, patch


from src.sync.phases._common import SyncResult
from src.sync.orchestrator import _run_phase9_insights_continuity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector(fresh_vl_count: int = 0):
    """Return a minimal mock connector for P9.

    The only real DB call P9 makes directly is the freshness-gate
    SELECT COUNT(*) on verification_logs.created_at.
    """
    connector = MagicMock()
    # Default: table is empty / stale → count = 0 → run verification report
    connector.execute.return_value.fetchone.return_value = (fresh_vl_count,)
    return connector


def _make_config(enabled: bool = True):
    return {"insights_continuity": {"enabled": enabled}}


# ---------------------------------------------------------------------------
# 1.  All five sub-calls invoked, each receives the SAME conn object
# ---------------------------------------------------------------------------

def test_all_subtasks_called_with_same_conn():
    """P9 must call every sub-task function exactly once, each with the same conn."""
    connector = _make_connector(fresh_vl_count=0)  # stale → verification runs
    result = SyncResult(success=True)

    with (
        patch(
            "src.services.ai_advisor.insight_manager.bridge_ai_insights_to_decision_hub",
            return_value=3,
        ) as mock_bridge,
        patch(
            "src.sync.orchestrator.score_all_trades",
            return_value=5,
        ) as mock_score,
        patch(
            "src.services.decision_links.recompute_auto_links",
            return_value=2,
        ),
        patch(
            "src.services.verification_service.compute_verification_report",
            return_value={},
        ) as mock_vr,
        patch(
            "src.services.ai_advisor.behavioral_metrics.BehavioralMetricsComputer",
        ),
    ):
        # P9 lazily imports (a)/(c)/(d)/(e) inside the function body, so those
        # are patched at their SOURCE modules above; (b) score_all_trades is a
        # module-level import in orchestrator, patched there.
        _run_phase9_insights_continuity(connector, _make_config(), result)

    # bridge sub-task
    mock_bridge.assert_called_once_with(connector)

    # score sub-task — score_all_trades is imported at module level in orchestrator
    mock_score.assert_called_once_with(connector)

    # Verification sub-task runs (count=0 → stale) with the same conn
    mock_vr.assert_called_once_with(connector)


def test_same_conn_object_passed_to_all_subtasks():
    """Every sub-task that accepts a connection arg must receive the SAME connector."""
    connector = _make_connector(fresh_vl_count=0)
    result = SyncResult(success=True)

    received_conns = {}

    def _capture_bridge(conn):
        received_conns["bridge"] = conn
        return 1

    def _capture_score(conn):
        received_conns["score"] = conn
        return 0

    def _capture_links(conn, **kwargs):
        received_conns["links"] = conn
        return 0

    def _capture_vr(conn):
        received_conns["vr"] = conn
        return {}

    mock_computer = MagicMock()
    mock_computer.compute_all.side_effect = lambda window_days, conn: (
        received_conns.__setitem__("bm_compute", conn) or []
    )
    mock_computer.save_to_db.side_effect = lambda results, conn: (
        received_conns.__setitem__("bm_save", conn)
    )

    import src.sync.orchestrator as _orch_mod

    with (
        patch("src.services.ai_advisor.insight_manager.bridge_ai_insights_to_decision_hub",
              side_effect=_capture_bridge),
        patch.object(_orch_mod, "score_all_trades", side_effect=_capture_score),
        patch(
            "src.services.decision_links.recompute_auto_links",
            side_effect=_capture_links,
        ),
        patch(
            "src.services.verification_service.compute_verification_report",
            side_effect=_capture_vr,
        ),
        patch(
            "src.services.ai_advisor.behavioral_metrics.BehavioralMetricsComputer",
            return_value=mock_computer,
        ),
    ):
        _run_phase9_insights_continuity(connector, _make_config(), result)

    for name, conn_obj in received_conns.items():
        assert conn_obj is connector, (
            f"Sub-task '{name}' received a different connection object "
            f"(expected id={id(connector)}, got id={id(conn_obj)})"
        )


# ---------------------------------------------------------------------------
# 2.  One sub-task raising must not stop the others
# ---------------------------------------------------------------------------

def test_one_failing_subtask_does_not_stop_others(caplog):
    """If (a) raises, sub-tasks (b)–(e) must still execute; result stays advisory-ok."""
    connector = _make_connector(fresh_vl_count=0)
    result = SyncResult(success=True)

    called_tasks = []

    def _exploding_bridge(conn):
        raise RuntimeError("simulated bridge failure")

    def _capture_score(conn):
        called_tasks.append("score")
        return 2

    def _capture_links(conn, **kwargs):
        called_tasks.append("links")
        return 0

    def _capture_vr(conn):
        called_tasks.append("vr")
        return {}

    mock_computer = MagicMock()
    mock_computer.compute_all.return_value = []
    mock_computer.save_to_db.side_effect = lambda *a, **kw: called_tasks.append("bm")

    import src.sync.orchestrator as _orch_mod

    with (
        patch("src.services.ai_advisor.insight_manager.bridge_ai_insights_to_decision_hub",
              side_effect=_exploding_bridge),
        patch.object(_orch_mod, "score_all_trades", side_effect=_capture_score),
        patch(
            "src.services.decision_links.recompute_auto_links",
            side_effect=_capture_links,
        ),
        patch(
            "src.services.verification_service.compute_verification_report",
            side_effect=_capture_vr,
        ),
        patch(
            "src.services.ai_advisor.behavioral_metrics.BehavioralMetricsComputer",
            return_value=mock_computer,
        ),
        caplog.at_level(logging.WARNING, logger="src.sync.orchestrator"),
    ):
        _run_phase9_insights_continuity(connector, _make_config(), result)

    # Sub-tasks (b), (c), (d), (e) must have executed despite (a) failing.
    assert "score" in called_tasks, "score_all_trades was not called after bridge failure"
    assert "links" in called_tasks, "recompute_auto_links was not called after bridge failure"
    assert "vr" in called_tasks, "compute_verification_report was not called after bridge failure"
    assert "bm" in called_tasks, "behavioral_metrics was not called after bridge failure"

    # Sync result must remain advisory-ok (success and degraded untouched by P9 failures).
    assert result.success is True, "P9 failure must not set success=False"
    # The step for the failing sub-task must be recorded as failed/non-critical → degraded.
    failed_steps = [s for s in result.steps if s.status == "failed"]
    assert failed_steps, "Failing sub-task must produce a failed StepResult"
    for s in failed_steps:
        assert s.critical is False, "P9 sub-step must never be critical"

    # A warning must be logged
    assert any("bridge" in r.message.lower() for r in caplog.records), (
        "Expected a WARNING log mentioning 'bridge' failure"
    )


def test_p9_failure_never_sets_sync_failure():
    """When ALL five sub-tasks fail, result.success stays True (advisory only)."""
    connector = _make_connector(fresh_vl_count=0)
    result = SyncResult(success=True)

    def _boom(*_a, **_kw):
        raise RuntimeError("all sub-tasks explode")

    mock_computer = MagicMock()
    mock_computer.compute_all.side_effect = _boom

    import src.sync.orchestrator as _orch_mod

    with (
        patch("src.services.ai_advisor.insight_manager.bridge_ai_insights_to_decision_hub",
              side_effect=_boom),
        patch.object(_orch_mod, "score_all_trades", side_effect=_boom),
        patch("src.services.decision_links.recompute_auto_links", side_effect=_boom),
        patch(
            "src.services.verification_service.compute_verification_report",
            side_effect=_boom,
        ),
        patch(
            "src.services.ai_advisor.behavioral_metrics.BehavioralMetricsComputer",
            return_value=mock_computer,
        ),
    ):
        _run_phase9_insights_continuity(connector, _make_config(), result)

    assert result.success is True, "All-sub-task failure must not set success=False"
    # All five failed steps must be non-critical
    failed = [s for s in result.steps if s.status == "failed"]
    assert len(failed) == 5, f"Expected 5 failed steps, got {len(failed)}"
    assert all(not s.critical for s in failed)


# ---------------------------------------------------------------------------
# 3.  Freshness gate for compute_verification_report
# ---------------------------------------------------------------------------

def test_verification_report_skipped_when_fresh():
    """If verification_logs has a row from <24 h ago, compute_verification_report must NOT be called."""
    # fresh_vl_count > 0 → step is fresh
    connector = _make_connector(fresh_vl_count=1)
    result = SyncResult(success=True)

    import src.sync.orchestrator as _orch_mod

    with (
        patch("src.services.ai_advisor.insight_manager.bridge_ai_insights_to_decision_hub",
              return_value=0),
        patch.object(_orch_mod, "score_all_trades", return_value=0),
        patch("src.services.decision_links.recompute_auto_links", return_value=0),
        patch(
            "src.services.verification_service.compute_verification_report",
        ) as mock_vr,
        patch(
            "src.services.ai_advisor.behavioral_metrics.BehavioralMetricsComputer",
        ) as MockComputer,
    ):
        mock_computer_inst = MagicMock()
        mock_computer_inst.compute_all.return_value = []
        MockComputer.return_value = mock_computer_inst

        _run_phase9_insights_continuity(connector, _make_config(), result)

    mock_vr.assert_not_called()

    # Info message must mention "skipped"
    assert any("skipped" in msg for msg in result.info_messages), (
        "Expected an info_message mentioning 'skipped' for fresh verification"
    )


def test_verification_report_runs_when_stale():
    """If verification_logs has NO fresh row (count=0), compute_verification_report IS called."""
    connector = _make_connector(fresh_vl_count=0)
    result = SyncResult(success=True)

    import src.sync.orchestrator as _orch_mod

    with (
        patch("src.services.ai_advisor.insight_manager.bridge_ai_insights_to_decision_hub",
              return_value=0),
        patch.object(_orch_mod, "score_all_trades", return_value=0),
        patch("src.services.decision_links.recompute_auto_links", return_value=0),
        patch(
            "src.services.verification_service.compute_verification_report",
            return_value={},
        ) as mock_vr,
        patch(
            "src.services.ai_advisor.behavioral_metrics.BehavioralMetricsComputer",
        ) as MockComputer,
    ):
        mock_computer_inst = MagicMock()
        mock_computer_inst.compute_all.return_value = []
        MockComputer.return_value = mock_computer_inst

        _run_phase9_insights_continuity(connector, _make_config(), result)

    mock_vr.assert_called_once_with(connector)


def test_verification_report_runs_when_table_empty():
    """Empty table (count=0) is treated the same as stale: compute_verification_report runs."""
    # Both empty (no rows) and all-rows-older-than-24h produce count=0.
    connector = _make_connector(fresh_vl_count=0)
    result = SyncResult(success=True)

    import src.sync.orchestrator as _orch_mod

    with (
        patch("src.services.ai_advisor.insight_manager.bridge_ai_insights_to_decision_hub",
              return_value=0),
        patch.object(_orch_mod, "score_all_trades", return_value=0),
        patch("src.services.decision_links.recompute_auto_links", return_value=0),
        patch(
            "src.services.verification_service.compute_verification_report",
            return_value={},
        ) as mock_vr,
        patch(
            "src.services.ai_advisor.behavioral_metrics.BehavioralMetricsComputer",
        ) as MockComputer,
    ):
        mock_computer_inst = MagicMock()
        mock_computer_inst.compute_all.return_value = []
        MockComputer.return_value = mock_computer_inst

        _run_phase9_insights_continuity(connector, _make_config(), result)

    mock_vr.assert_called_once()


# ---------------------------------------------------------------------------
# 4.  Config toggle: insights_continuity.enabled = false
# ---------------------------------------------------------------------------

def test_p9_disabled_by_config():
    """When insights_continuity.enabled=false the entire step is skipped (no sub-calls)."""
    connector = _make_connector()
    result = SyncResult(success=True)

    import src.sync.orchestrator as _orch_mod

    with (
        patch.object(_orch_mod, "bridge_ai_insights_to_decision_hub",
                     return_value=0, create=True) as mock_bridge,
        patch.object(_orch_mod, "score_all_trades", return_value=0) as mock_score,
    ):
        _run_phase9_insights_continuity(
            connector, _make_config(enabled=False), result
        )

    mock_bridge.assert_not_called()
    mock_score.assert_not_called()
    assert any("disabled" in msg.lower() for msg in result.info_messages)


# ---------------------------------------------------------------------------
# 5.  Integration: P9 is in PIPELINE_MANIFEST after P8
# ---------------------------------------------------------------------------

def test_p9_in_manifest_after_p8():
    """PIPELINE_MANIFEST must contain P9 as the last phase, directly after P8."""
    from src.sync.phases.manifest import PIPELINE_MANIFEST

    phase_ids = [spec.phase_id for spec in PIPELINE_MANIFEST]
    assert "P9" in phase_ids, "P9 must be in PIPELINE_MANIFEST"

    p8_idx = phase_ids.index("P8")
    p9_idx = phase_ids.index("P9")
    assert p9_idx == p8_idx + 1, f"P9 must immediately follow P8 (P8={p8_idx}, P9={p9_idx})"


def test_p9_manifest_runner_name():
    """P9 PhaseSpec runner must match the function name defined in orchestrator."""
    from src.sync.phases.manifest import PIPELINE_MANIFEST
    import src.sync.orchestrator as _orch_mod

    p9 = next(s for s in PIPELINE_MANIFEST if s.phase_id == "P9")
    assert p9.runner == "_run_phase9_insights_continuity"
    assert hasattr(_orch_mod, p9.runner), (
        f"Orchestrator must expose '{p9.runner}' for the manifest dispatch table"
    )


def test_p9_in_phase_dispatch():
    """_PHASE_DISPATCH must have an entry for the P9 runner."""
    import src.sync.orchestrator as _orch_mod

    assert "_run_phase9_insights_continuity" in _orch_mod._PHASE_DISPATCH, (
        "_PHASE_DISPATCH must include '_run_phase9_insights_continuity'"
    )


def test_manifest_import_sanity():
    """PIPELINE_MANIFEST must be importable without pulling in DB or pandas."""
    # This is the same check the existing manifest tests do.
    from src.sync.phases.manifest import PIPELINE_MANIFEST, PhaseSpec  # noqa: F401
    assert len(PIPELINE_MANIFEST) >= 9, "Expected at least P0–P8 plus P9"


# ---------------------------------------------------------------------------
# 6.  P9 sub-step (a0) — price continuity for pending-verification assets
# ---------------------------------------------------------------------------

def test_p9_a0_fires_and_reports_refreshed_count():
    """Sub-step (a0) must call refresh_prices_for_asset_ids with pending assets
    and append an info_message with the refreshed count."""
    connector = _make_connector(fresh_vl_count=0)
    result = SyncResult(success=True)

    # Mock connector.execute(...).fetchall() to return two pending asset rows
    connector.execute.return_value.fetchall.return_value = [
        ("CN_FUND_900008",),
        ("US_ETF_SGOV",),
    ]
    connector.execute.return_value.fetchone.return_value = (0,)  # freshness gate

    captured_ids = []

    def _capture_refresh(conn, asset_ids):
        captured_ids.extend(asset_ids)
        return len(asset_ids)

    import src.sync.orchestrator as _orch_mod

    with (
        patch("src.market_data.service.MarketDataService.refresh_prices_for_asset_ids",
              side_effect=_capture_refresh),
        patch("src.services.ai_advisor.insight_manager.bridge_ai_insights_to_decision_hub",
              return_value=0),
        patch.object(_orch_mod, "score_all_trades", return_value=0),
        patch("src.services.decision_links.recompute_auto_links", return_value=0),
        patch("src.services.verification_service.compute_verification_report", return_value={}),
        patch("src.services.ai_advisor.behavioral_metrics.BehavioralMetricsComputer") as MockComp,
    ):
        mock_inst = MagicMock()
        mock_inst.compute_all.return_value = []
        MockComp.return_value = mock_inst
        _run_phase9_insights_continuity(connector, _make_config(), result)

    assert "CN_FUND_900008" in captured_ids
    assert "US_ETF_SGOV" in captured_ids
    assert any("price continuity" in msg for msg in result.info_messages), (
        f"Expected 'price continuity' in info_messages; got: {result.info_messages}"
    )


def test_p9_a0_failure_does_not_block_other_subtasks(caplog):
    """(a0) failure must not block (a)–(e); result stays advisory-ok."""
    connector = _make_connector(fresh_vl_count=0)
    result = SyncResult(success=True)

    called_tasks = []

    def _capture_bridge(conn):
        called_tasks.append("bridge")
        return 0

    def _capture_score(conn):
        called_tasks.append("score")
        return 0

    import src.sync.orchestrator as _orch_mod

    with (
        patch("src.market_data.service.MarketDataService.refresh_prices_for_asset_ids",
              side_effect=RuntimeError("a0 network failure")),
        patch("src.services.ai_advisor.insight_manager.bridge_ai_insights_to_decision_hub",
              side_effect=_capture_bridge),
        patch.object(_orch_mod, "score_all_trades", side_effect=_capture_score),
        patch("src.services.decision_links.recompute_auto_links", return_value=0),
        patch("src.services.verification_service.compute_verification_report", return_value={}),
        patch("src.services.ai_advisor.behavioral_metrics.BehavioralMetricsComputer") as MockComp,
        caplog.at_level(logging.WARNING, logger="src.sync.orchestrator"),
    ):
        mock_inst = MagicMock()
        mock_inst.compute_all.return_value = []
        MockComp.return_value = mock_inst
        _run_phase9_insights_continuity(connector, _make_config(), result)

    assert result.success is True, "a0 failure must not set success=False"
    assert "bridge" in called_tasks, "(a) must still run after (a0) failure"
    assert "score" in called_tasks, "(b) must still run after (a0) failure"
    a0_warnings = [r.message for r in caplog.records if "price continuity" in r.message.lower()]
    assert a0_warnings, "Expected a warning mentioning 'price continuity'"


def test_p9_a0_query_includes_blocked_assets():
    """Regression (code-review fix 8): the a0 asset-collection query must include
    verification_blocked assets (120-day window) so blocked rows can actually recover
    once prices exist — not just pending/pending_window (45-day window)."""
    connector = _make_connector(fresh_vl_count=0)
    result = SyncResult(success=True)

    connector.execute.return_value.fetchall.return_value = []
    connector.execute.return_value.fetchone.return_value = (0,)

    import src.sync.orchestrator as _orch_mod

    with (
        patch("src.market_data.service.MarketDataService.refresh_prices_for_asset_ids",
              return_value=0),
        patch("src.services.ai_advisor.insight_manager.bridge_ai_insights_to_decision_hub",
              return_value=0),
        patch.object(_orch_mod, "score_all_trades", return_value=0),
        patch("src.services.decision_links.recompute_auto_links", return_value=0),
        patch("src.services.verification_service.compute_verification_report", return_value={}),
        patch("src.services.ai_advisor.behavioral_metrics.BehavioralMetricsComputer") as MockComp,
    ):
        mock_inst = MagicMock()
        mock_inst.compute_all.return_value = []
        MockComp.return_value = mock_inst
        _run_phase9_insights_continuity(connector, _make_config(), result)

    # Find the a0 trade_logs asset-collection query among connector.execute calls
    a0_sqls = [
        str(call.args[0])
        for call in connector.execute.call_args_list
        if call.args and "trade_logs" in str(call.args[0]) and "asset_id" in str(call.args[0])
    ]
    assert a0_sqls, "Expected the a0 asset-collection query to run against trade_logs"
    a0_sql = a0_sqls[0]
    assert "'pending'" in a0_sql and "'pending_window'" in a0_sql, (
        "a0 query must cover pending/pending_window assets"
    )
    assert "verification_blocked" in a0_sql, (
        "a0 query must include verification_blocked assets (recovery path)"
    )
    assert "120" in a0_sql, (
        "blocked assets must use the wider 120-day log_date window"
    )
    assert "45" in a0_sql, (
        "pending assets must keep the 45-day log_date window"
    )


# ---------------------------------------------------------------------------
# 7.  P9 sub-step (a0b) — historical price backfill for verification windows
# ---------------------------------------------------------------------------

def _make_routing_connector(a0_assets=None, a0b_trades=None, fresh_vl=0):
    """Return a MagicMock connector that returns different results for different queries.

    a0_assets: list of asset_id strings for the (a0) SELECT DISTINCT query.
    a0b_trades: list of (asset_id, log_date) tuples for the (a0b) query.
    """
    a0_assets = a0_assets or []
    a0b_trades = a0b_trades or []

    def _execute_side_effect(query, *args, **kwargs):
        q = str(query)
        result_mock = MagicMock()
        if "DISTINCT asset_id" in q and "outcome_pct" not in q:
            # a0 — SELECT DISTINCT asset_id
            result_mock.fetchall.return_value = [(a,) for a in a0_assets]
        elif "outcome_pct IS NULL" in q:
            # a0b — SELECT asset_id, log_date WHERE outcome_pct IS NULL
            result_mock.fetchall.return_value = list(a0b_trades)
        else:
            result_mock.fetchall.return_value = []
        result_mock.fetchone.return_value = (fresh_vl,)
        return result_mock

    connector = MagicMock()
    connector.execute.side_effect = _execute_side_effect
    return connector


def test_p9_a0b_fires_with_pending_trades_and_reports_count():
    """(a0b) must call backfill_trade_window_prices with the collected trades
    and append an info_message mentioning 'historical backfill'."""
    trade = ("CN_FUND_900002", date(2026, 1, 19))
    connector = _make_routing_connector(a0b_trades=[trade], fresh_vl=0)
    result = SyncResult(success=True)

    captured_trades = []

    def _capture_backfill(conn, trades, max_fetches=20):
        captured_trades.extend(trades)
        return 1

    import src.sync.orchestrator as _orch_mod

    with (
        patch("src.market_data.service.MarketDataService.refresh_prices_for_asset_ids",
              return_value=0),
        patch("src.market_data.service.MarketDataService.backfill_trade_window_prices",
              side_effect=_capture_backfill),
        patch("src.services.ai_advisor.insight_manager.bridge_ai_insights_to_decision_hub",
              return_value=0),
        patch.object(_orch_mod, "score_all_trades", return_value=0),
        patch("src.services.decision_links.recompute_auto_links", return_value=0),
        patch("src.services.verification_service.compute_verification_report", return_value={}),
        patch("src.services.ai_advisor.behavioral_metrics.BehavioralMetricsComputer") as MockComp,
    ):
        mock_inst = MagicMock()
        mock_inst.compute_all.return_value = []
        MockComp.return_value = mock_inst
        _run_phase9_insights_continuity(connector, _make_config(), result)

    assert any("historical backfill" in msg for msg in result.info_messages), (
        f"Expected 'historical backfill' in info_messages; got: {result.info_messages}"
    )
    assert len(captured_trades) > 0, "backfill_trade_window_prices must receive trades"
    assert captured_trades[0][0] == "CN_FUND_900002"


def test_p9_a0b_failure_does_not_block_other_subtasks(caplog):
    """(a0b) failure must not block (a)–(e); result stays advisory-ok."""
    connector = _make_routing_connector(fresh_vl=0)
    result = SyncResult(success=True)

    called_tasks = []

    def _capture_bridge(conn):
        called_tasks.append("bridge")
        return 0

    def _capture_score(conn):
        called_tasks.append("score")
        return 0

    import src.sync.orchestrator as _orch_mod

    with (
        patch("src.market_data.service.MarketDataService.refresh_prices_for_asset_ids",
              return_value=0),
        patch("src.market_data.service.MarketDataService.backfill_trade_window_prices",
              side_effect=RuntimeError("a0b network failure")),
        patch("src.services.ai_advisor.insight_manager.bridge_ai_insights_to_decision_hub",
              side_effect=_capture_bridge),
        patch.object(_orch_mod, "score_all_trades", side_effect=_capture_score),
        patch("src.services.decision_links.recompute_auto_links", return_value=0),
        patch("src.services.verification_service.compute_verification_report", return_value={}),
        patch("src.services.ai_advisor.behavioral_metrics.BehavioralMetricsComputer") as MockComp,
        caplog.at_level(logging.WARNING, logger="src.sync.orchestrator"),
    ):
        mock_inst = MagicMock()
        mock_inst.compute_all.return_value = []
        MockComp.return_value = mock_inst
        _run_phase9_insights_continuity(connector, _make_config(), result)

    assert result.success is True, "a0b failure must not set success=False"
    assert "bridge" in called_tasks, "(a) must still run after (a0b) failure"
    assert "score" in called_tasks, "(b) must still run after (a0b) failure"

    # A warning must be logged and a failed step recorded
    a0b_warnings = [r.message for r in caplog.records if "historical backfill" in r.message.lower()]
    assert a0b_warnings, "Expected a WARNING log mentioning 'historical backfill'"

    failed_steps = [s for s in result.steps if s.status == "failed" and "a0b" in s.name]
    assert failed_steps, "Failed (a0b) step must be recorded as failed/non-critical"
    assert all(not s.critical for s in failed_steps)


def test_p9_a0b_query_uses_outcome_pct_null_and_display_scope():
    """(a0b) asset-collection query must filter on outcome_pct IS NULL and
    include the display-scope predicate (suggestion_source or ai_suggestion …)."""
    connector = _make_routing_connector(fresh_vl=0)
    result = SyncResult(success=True)

    import src.sync.orchestrator as _orch_mod

    with (
        patch("src.market_data.service.MarketDataService.refresh_prices_for_asset_ids",
              return_value=0),
        patch("src.market_data.service.MarketDataService.backfill_trade_window_prices",
              return_value=0),
        patch("src.services.ai_advisor.insight_manager.bridge_ai_insights_to_decision_hub",
              return_value=0),
        patch.object(_orch_mod, "score_all_trades", return_value=0),
        patch("src.services.decision_links.recompute_auto_links", return_value=0),
        patch("src.services.verification_service.compute_verification_report", return_value={}),
        patch("src.services.ai_advisor.behavioral_metrics.BehavioralMetricsComputer") as MockComp,
    ):
        mock_inst = MagicMock()
        mock_inst.compute_all.return_value = []
        MockComp.return_value = mock_inst
        _run_phase9_insights_continuity(connector, _make_config(), result)

    # Find the a0b query among execute calls
    a0b_sqls = [
        str(call.args[0])
        for call in connector.execute.call_args_list
        if call.args and "outcome_pct IS NULL" in str(call.args[0])
    ]
    assert a0b_sqls, "Expected the a0b asset-collection query with outcome_pct IS NULL"
    a0b_sql = a0b_sqls[0]

    assert "log_date" in a0b_sql, "a0b query must select log_date"
    assert "400" in a0b_sql, "a0b query must use 400-day window for pending trades"
    assert "suggestion_source" in a0b_sql, (
        "a0b query must include display-scope filter (suggestion_source present)"
    )
