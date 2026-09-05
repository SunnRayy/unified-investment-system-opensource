"""Tests for src/services/verification_config.py (F1.1 config loader, Batch B1).

Covers: documented defaults when the file is missing, YAML values winning when
present, and bucket_map parsing. Never touches the real config/verification.yaml
(uses tmp_path fixtures + force_reload to bypass the module cache).
"""
from src.services import verification_config as vc


def test_defaults_when_file_missing(tmp_path):
    missing_path = tmp_path / "does_not_exist.yaml"
    cfg = vc.load_verification_config(str(missing_path), force_reload=True)

    assert cfg.process_verification.enabled is False
    assert cfg.process_verification.outcome_window_days == 180
    assert cfg.value_trap.trigger_threshold_pct == -25.0
    assert cfg.value_trap.escalation_step_pp == 10.0
    assert cfg.value_trap.overdue_alert_days == 14
    assert cfg.staleness.fast_hours == 24
    assert cfg.staleness.slow_days == 7
    assert cfg.contrarian.drawdown_window_trading_days == 10
    assert cfg.contrarian.drawdown_threshold_pct == 5.0
    assert cfg.contrarian.manual_alert_rate_pct == 30.0
    assert cfg.contrarian.manual_alert_monthly_count == 3
    # Default bucket_map still present.
    assert any(e.asset_pattern == "RSU_AMZN" for e in cfg.bucket_map["compliance"])


def test_yaml_values_win_when_present(tmp_path):
    custom = tmp_path / "verification.yaml"
    custom.write_text(
        """
process_verification:
  enabled: true
  outcome_window_days: 90
value_trap:
  trigger_threshold_pct: -15.0
  escalation_step_pp: 5.0
  overdue_alert_days: 7
staleness:
  fast_hours: 12
  slow_days: 3
contrarian:
  drawdown_window_trading_days: 20
  drawdown_threshold_pct: 8.0
  manual_alert_rate_pct: 40.0
  manual_alert_monthly_count: 5
bucket_map:
  compliance:
    - {asset_pattern: "TEST_COMPLY", actions: [sell]}
  ratio: []
  liquidity: []
"""
    )
    cfg = vc.load_verification_config(str(custom), force_reload=True)

    assert cfg.process_verification.enabled is True
    assert cfg.process_verification.outcome_window_days == 90
    assert cfg.value_trap.trigger_threshold_pct == -15.0
    assert cfg.value_trap.escalation_step_pp == 5.0
    assert cfg.value_trap.overdue_alert_days == 7
    assert cfg.staleness.fast_hours == 12
    assert cfg.staleness.slow_days == 3
    assert cfg.contrarian.drawdown_window_trading_days == 20
    assert cfg.contrarian.drawdown_threshold_pct == 8.0
    assert cfg.contrarian.manual_alert_rate_pct == 40.0
    assert cfg.contrarian.manual_alert_monthly_count == 5
    assert cfg.bucket_map["compliance"][0].asset_pattern == "TEST_COMPLY"
    assert cfg.bucket_map["ratio"] == ()


def test_cache_returns_same_object_without_force_reload(tmp_path):
    custom = tmp_path / "verification.yaml"
    custom.write_text("process_verification:\n  enabled: true\n")
    cfg1 = vc.load_verification_config(str(custom), force_reload=True)
    cfg2 = vc.load_verification_config(str(custom))
    assert cfg1 is cfg2


def test_force_reload_bypasses_cache(tmp_path):
    custom = tmp_path / "verification.yaml"
    custom.write_text("process_verification:\n  enabled: false\n")
    cfg1 = vc.load_verification_config(str(custom), force_reload=True)
    assert cfg1.process_verification.enabled is False

    custom.write_text("process_verification:\n  enabled: true\n")
    cfg2 = vc.load_verification_config(str(custom), force_reload=True)
    assert cfg2.process_verification.enabled is True


def test_clear_cache_hook(tmp_path):
    custom = tmp_path / "verification.yaml"
    custom.write_text("process_verification:\n  enabled: false\n")
    vc.load_verification_config(str(custom), force_reload=True)
    vc.clear_cache()
    custom.write_text("process_verification:\n  enabled: true\n")
    cfg = vc.load_verification_config(str(custom))  # cache cleared -> re-reads
    assert cfg.process_verification.enabled is True


def test_real_committed_config_loads_and_matches_documented_defaults():
    """config/verification.yaml as committed must parse to the exact documented
    defaults (this is the day-1 state — flag off, PRD-specified thresholds)."""
    vc.clear_cache()
    cfg = vc.load_verification_config("config/verification.yaml", force_reload=True)
    assert cfg.process_verification.enabled is False
    assert cfg.process_verification.outcome_window_days == 180
    assert cfg.value_trap.trigger_threshold_pct == -25.0
    assert cfg.value_trap.escalation_step_pp == 10.0
    assert cfg.value_trap.overdue_alert_days == 14
    assert cfg.staleness.fast_hours == 24
    assert cfg.staleness.slow_days == 7
    assert cfg.contrarian.drawdown_window_trading_days == 10
    assert cfg.contrarian.drawdown_threshold_pct == 5.0
    assert cfg.contrarian.manual_alert_rate_pct == 30.0
    assert cfg.contrarian.manual_alert_monthly_count == 3


def test_falls_back_to_example_template_when_real_file_missing(tmp_path, monkeypatch):
    """Program OSR WS-4b: a real verification.yaml missing but a committed
    .example twin present must load from the example, not skip straight to
    hardcoded dataclass defaults — this is what lets a clean clone/Docker
    image boot fully configured."""
    example = tmp_path / "verification.example.yaml"
    example.write_text(
        "process_verification:\n"
        "  enabled: true\n"
        "  outcome_window_days: 42\n",
        encoding="utf-8",
    )
    real_path = tmp_path / "verification.yaml"  # deliberately not created

    cfg = vc.load_verification_config(str(real_path), force_reload=True)

    assert cfg.process_verification.enabled is True
    assert cfg.process_verification.outcome_window_days == 42


def test_real_file_wins_over_example_when_both_present(tmp_path):
    example = tmp_path / "verification.example.yaml"
    example.write_text("process_verification:\n  enabled: true\n", encoding="utf-8")
    real = tmp_path / "verification.yaml"
    real.write_text("process_verification:\n  enabled: false\n", encoding="utf-8")

    cfg = vc.load_verification_config(str(real), force_reload=True)

    assert cfg.process_verification.enabled is False


def test_committed_example_template_loads_and_has_balance_sheet_section():
    """config/verification.example.yaml as committed must parse and carry
    the persona balance_sheet marker (Program OSR WS-4b/WS-5b)."""
    vc.clear_cache()
    cfg = vc.load_verification_config("config/verification.example.yaml", force_reload=True)
    assert "安泰人生" in cfg.balance_sheet.non_rebalanceable_history_markers
