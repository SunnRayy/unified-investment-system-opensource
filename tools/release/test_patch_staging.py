"""Fail-loud guard tests for patch_staging.py (Program OSR WS-3c).

Not under tests/ deliberately — tests/** and ux-command-center/tests/** are
reassigned to another worker for the fund-code triage; this tests the export
TOOL itself, not the app, so it lives beside the tool it covers. Run
directly: .venv/bin/python -m pytest tools/release/test_patch_staging.py -q

Why this exists: an export-time substitution (mapping_seeds.py's persona
swap, the connector.py/cost_basis_validator.py load-bearing-constant swaps)
lives ONLY in patch_staging.py — invisible when reading the private repo. If
the target file drifts so a substitution's "old" text no longer matches
exactly, a silent no-op would ship the real value untouched, with --strict
as the only remaining backstop. These tests prove that can't happen quietly:
a missing or ambiguous target must raise, not pass through.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from patch_staging import PatchError, _delete_line_range, _replace_once  # noqa: E402


def test_replace_once_raises_on_zero_matches(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("value = 1\n", encoding="utf-8")
    with pytest.raises(PatchError, match="found 0"):
        _replace_once(f, "value = 2", "value = 3")
    # Fail loudly means fail WITHOUT writing — file must be untouched.
    assert f.read_text(encoding="utf-8") == "value = 1\n"


def test_replace_once_raises_on_multiple_matches(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("value = 1\nvalue = 1\n", encoding="utf-8")
    with pytest.raises(PatchError, match="found 2"):
        _replace_once(f, "value = 1", "value = 9")
    assert f.read_text(encoding="utf-8") == "value = 1\nvalue = 1\n"


def test_replace_once_succeeds_on_exactly_one_match(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("value = 1\nother = 2\n", encoding="utf-8")
    _replace_once(f, "value = 1", "value = 9")
    assert f.read_text(encoding="utf-8") == "value = 9\nother = 2\n"


def test_delete_line_range_raises_when_start_marker_missing(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("def foo():\n    pass\n", encoding="utf-8")
    with pytest.raises(PatchError, match="start marker"):
        _delete_line_range(f, start_marker="def bar():", end_marker="    pass")
    assert f.read_text(encoding="utf-8") == "def foo():\n    pass\n"


def test_delete_line_range_raises_when_start_marker_ambiguous(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("def foo():\n    pass\n\n\ndef foo():\n    pass\n", encoding="utf-8")
    with pytest.raises(PatchError, match="found 2"):
        _delete_line_range(f, start_marker="def foo():", end_marker="    pass")


def test_delete_line_range_raises_when_end_marker_missing_after_start(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("def foo():\n    return 1\n", encoding="utf-8")
    with pytest.raises(PatchError, match="end marker"):
        _delete_line_range(f, start_marker="def foo():", end_marker="    pass")
    assert f.read_text(encoding="utf-8") == "def foo():\n    return 1\n"


def test_delete_line_range_succeeds_and_removes_exact_span(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text(
        "before\ndef foo():\n    a = 1\n    return a\nafter\n", encoding="utf-8"
    )
    _delete_line_range(f, start_marker="def foo():", end_marker="    return a")
    assert f.read_text(encoding="utf-8") == "before\nafter\n"


def test_real_patch_staging_targets_currently_all_match(tmp_path):
    """Anti-drift check: run the actual patch_staging.patch_staging() against
    a copy of every file it touches in this repo, right now. If any of the
    real substitutions in patch_staging.py have drifted out of sync with the
    current source (the exact scenario this whole test module exists to
    catch), this fails with the same PatchError a broken export would hit —
    catching it here, in CI, instead of silently in a shipped export."""
    import shutil

    from patch_staging import patch_staging

    repo_root = Path(__file__).resolve().parents[2]
    staging = tmp_path / "staging"

    targets = [
        "ux-command-center/pages/BalanceSheet.tsx",
        "ux-command-center/tests/balance-sheet-batch3.test.tsx",
        "tests/api/test_reader_mappings_api.py",
        "tests/sources/test_financial_summary_transformer.py",
        "tests/sources/test_config_driven_reader.py",
        "tests/services/test_reader_mappings.py",
        "tests/database/test_seed_loader.py",
        "tests/services/test_investment_contributions.py",
        "tests/docs/test_referenced_artifacts_exist.py",
        "tools/release/leak_gate.py",
        "tests/services/test_insight_manager.py",
        "tests/database/test_migrations.py",
        "tests/services/test_value_trap.py",
        "src/validation/cost_basis_validator.py",
        "src/database/connector.py",
        # 6a7 — market-regime proxy codes (kept real privately)
        "src/financial_analysis/regime.py",
        "tests/financial_analysis/test_regime.py",
        "tests/verification/test_monthly_verifier.py",
        "tests/api/test_verification.py",
        # 6a8 — load-bearing real fund codes in src/ lookup constants
        "src/services/valuation/collector.py",
        "src/services/ai_advisor/behavioral_metrics.py",
        "src/services/strategy_reviewer.py",
        "src/services/position_lots.py",
    ]
    for rel in targets:
        src = repo_root / rel
        if not src.exists():
            pytest.skip(f"{rel} not found in this checkout — repo layout changed")
        dst = staging / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # Should not raise. If it does, a target has drifted — fix patch_staging.py.
    patch_staging(staging)
