"""Tests for dry-run sync (--dry-run flag).

Confirms that run_dry_sync:
  1. Leaves the original DB file mtime and row counts unchanged.
  2. Cleans up the tmp DB copy after completion.
  3. Returns a dict with the expected keys.
"""
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import duckdb
import pytest

pytestmark = pytest.mark.sync


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_db(path: Path) -> None:
    """Create a minimal DuckDB file with just the holdings table present."""
    conn = duckdb.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            asset_id VARCHAR,
            snapshot_date DATE,
            market_value DOUBLE,
            source_system VARCHAR,
            is_shadow BOOLEAN DEFAULT FALSE
        )
    """)
    conn.execute("""
        INSERT INTO holdings VALUES ('TEST_ASSET', '2026-01-01', 100.0, 'Test', FALSE)
    """)
    conn.close()


@dataclass
class _FakeSyncResult:
    success: bool = True
    transactions_synced: int = 5
    holdings_synced: int = 10
    market_records_synced: int = 20
    allocations_synced: int = 3
    taxonomy_created: int = 1
    taxonomy_updated: int = 2
    assets_registered: int = 4
    cost_basis_discrepancies: int = 0
    allocation_drifts: int = 0
    live_price_holdings_updated: int = 0
    position_deltas_detected: int = 0
    integrity_checks_passed: int = 14
    integrity_checks_total: int = 14
    sync_audit_id: Optional[str] = None
    sync_diff: Optional[Dict[str, Any]] = field(default_factory=lambda: {
        "net_worth_before": 1_000_000.0,
        "net_worth_after": 1_050_000.0,
        "net_worth_change_pct": 5.0,
        "asset_count_before": 10,
        "asset_count_after": 11,
        "by_source_before": {},
        "by_source_after": {},
        "alert": False,
    })
    warnings: List[str] = field(default_factory=list)
    info_messages: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    degraded: bool = False


# ---------------------------------------------------------------------------
# Test 1: original DB is untouched
# ---------------------------------------------------------------------------

def test_dry_run_does_not_modify_original_db(tmp_path):
    """run_dry_sync must leave the original DB file mtime and row counts unchanged."""
    from src.sync.dry_run import run_dry_sync

    live_db = tmp_path / "live.duckdb"
    _make_minimal_db(live_db)

    mtime_before = live_db.stat().st_mtime

    fake_result = _FakeSyncResult()

    with patch("src.sync.dry_run.run_full_sync_v3", return_value=fake_result), \
         patch("src.sync.dry_run.bootstrap_database"), \
         patch("src.sync.dry_run.resolve_db_path", return_value=str(live_db)):
        run_dry_sync(str(live_db), {})

    mtime_after = live_db.stat().st_mtime
    assert mtime_before == mtime_after, (
        f"Live DB mtime changed: {mtime_before} -> {mtime_after}"
    )

    # Row count must be unchanged
    conn = duckdb.connect(str(live_db), read_only=True)
    count = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
    conn.close()
    assert count == 1, f"Expected 1 row in live DB, got {count}"


# ---------------------------------------------------------------------------
# Test 2: tmp copy is cleaned up
# ---------------------------------------------------------------------------

def test_dry_run_cleans_up_tmp_copy(tmp_path):
    """The tmp DB copy is deleted after run_dry_sync completes (success or failure)."""
    from src.sync.dry_run import run_dry_sync

    live_db = tmp_path / "live.duckdb"
    _make_minimal_db(live_db)

    captured_tmp_paths: List[str] = []

    original_copy = shutil.copy2

    def spy_copy2(src, dst):
        if str(dst) != str(src):
            captured_tmp_paths.append(str(dst))
        return original_copy(src, dst)

    fake_result = _FakeSyncResult()

    with patch("src.sync.dry_run.run_full_sync_v3", return_value=fake_result), \
         patch("src.sync.dry_run.bootstrap_database"), \
         patch("src.sync.dry_run.resolve_db_path", return_value=str(live_db)), \
         patch("src.sync.dry_run.shutil.copy2", side_effect=spy_copy2):
        run_dry_sync(str(live_db), {})

    assert len(captured_tmp_paths) >= 1, "Expected at least one tmp copy"
    for tmp_p in captured_tmp_paths:
        assert not Path(tmp_p).exists(), f"Tmp DB copy was not cleaned up: {tmp_p}"


# ---------------------------------------------------------------------------
# Test 3: return dict has expected keys
# ---------------------------------------------------------------------------

def test_dry_run_returns_diff_dict(tmp_path):
    """run_dry_sync returns a dict with the expected keys."""
    from src.sync.dry_run import run_dry_sync

    live_db = tmp_path / "live.duckdb"
    _make_minimal_db(live_db)

    fake_result = _FakeSyncResult()

    with patch("src.sync.dry_run.run_full_sync_v3", return_value=fake_result), \
         patch("src.sync.dry_run.bootstrap_database"), \
         patch("src.sync.dry_run.resolve_db_path", return_value=str(live_db)):
        result = run_dry_sync(str(live_db), {})

    expected_keys = {
        "new_holdings",
        "changed_holdings",
        "removed_holdings",
        "sync_warnings",
        "integrity_status",
        "tmp_path",
    }
    assert expected_keys.issubset(set(result.keys())), (
        f"Missing keys: {expected_keys - set(result.keys())}"
    )
    assert result["integrity_status"] in ("ok", "degraded", "failed"), (
        f"Unexpected integrity_status: {result['integrity_status']}"
    )
    # tmp_path should be empty string (cleaned up) or the path string
    assert isinstance(result["tmp_path"], str)


# ---------------------------------------------------------------------------
# Test 4: tmp copy is cleaned up even when sync raises
# ---------------------------------------------------------------------------

def test_dry_run_cleans_up_on_exception(tmp_path):
    """run_dry_sync deletes the tmp DB copy even if run_full_sync_v3 raises."""
    from src.sync.dry_run import run_dry_sync

    live_db = tmp_path / "live.duckdb"
    _make_minimal_db(live_db)

    captured_tmp_paths: List[str] = []
    original_copy = shutil.copy2

    def spy_copy2(src, dst):
        if str(dst) != str(src):
            captured_tmp_paths.append(str(dst))
        return original_copy(src, dst)

    with patch("src.sync.dry_run.run_full_sync_v3", side_effect=RuntimeError("boom")), \
         patch("src.sync.dry_run.bootstrap_database"), \
         patch("src.sync.dry_run.resolve_db_path", return_value=str(live_db)), \
         patch("src.sync.dry_run.shutil.copy2", side_effect=spy_copy2):
        with pytest.raises(RuntimeError, match="boom"):
            run_dry_sync(str(live_db), {})

    # At least one tmp path was captured and all were cleaned up
    assert len(captured_tmp_paths) >= 1, "Expected at least one tmp copy"
    for tmp_p in captured_tmp_paths:
        assert not Path(tmp_p).exists(), f"Tmp DB copy was not cleaned up after exception: {tmp_p}"


# ---------------------------------------------------------------------------
# Test 5: WAL sibling is copied when it exists
# ---------------------------------------------------------------------------

def test_dry_run_copies_wal_if_present(tmp_path):
    """run_dry_sync copies the live WAL sibling alongside the main DB copy."""
    from src.sync.dry_run import run_dry_sync

    live_db = tmp_path / "live.duckdb"
    _make_minimal_db(live_db)

    # Create a fake WAL sibling next to the live DB
    live_wal = Path(str(live_db) + ".wal")
    live_wal.write_bytes(b"fake-wal-content")

    copy2_calls: List[tuple] = []
    original_copy = shutil.copy2

    def spy_copy2(src, dst):
        copy2_calls.append((src, dst))
        return original_copy(src, dst)

    fake_result = _FakeSyncResult()

    with patch("src.sync.dry_run.run_full_sync_v3", return_value=fake_result), \
         patch("src.sync.dry_run.bootstrap_database"), \
         patch("src.sync.dry_run.resolve_db_path", return_value=str(live_db)), \
         patch("src.sync.dry_run.shutil.copy2", side_effect=spy_copy2):
        run_dry_sync(str(live_db), {})

    # At least one copy2 call should have copied the WAL (src ends with .wal)
    wal_copies = [
        (src, dst) for src, dst in copy2_calls
        if str(src).endswith(".wal")
    ]
    assert len(wal_copies) >= 1, (
        f"Expected shutil.copy2 to be called with a .wal src. Calls: {copy2_calls}"
    )
    src_wal, dst_wal = wal_copies[0]
    assert src_wal == str(live_wal), f"Wrong WAL source: {src_wal}"
    assert dst_wal.endswith(".wal"), f"Expected WAL dst to end with .wal: {dst_wal}"
