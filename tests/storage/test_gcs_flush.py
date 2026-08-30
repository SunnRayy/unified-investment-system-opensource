import asyncio
import importlib
from unittest.mock import AsyncMock, patch

import pytest

import src.storage.gcs_flush as gcs_flush_module


def _reload_module():
    mod = importlib.reload(gcs_flush_module)
    mod._write_seq = 0
    mod._flushed_seq = 0
    mod._flush_task = None
    mod._upload_lock = None
    mod._last_flush_time = None
    mod._last_flush_error = None
    mod._last_flush_generation = None
    mod._sync_active = False
    return mod


def test_mark_dirty_increments_write_seq_when_bucket_configured(monkeypatch):
    mod = _reload_module()
    monkeypatch.setenv("UIS_GCS_BUCKET", "test-bucket")

    mod.mark_dirty()

    assert mod._write_seq == 1


def test_mark_dirty_noop_when_bucket_unset(monkeypatch):
    mod = _reload_module()
    monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)

    mod.mark_dirty()

    assert mod._write_seq == 0


@pytest.mark.asyncio
async def test_flush_now_noop_when_seq_unchanged(monkeypatch):
    mod = _reload_module()
    monkeypatch.setenv("UIS_GCS_BUCKET", "test-bucket")
    mod._write_seq = 3
    mod._flushed_seq = 3
    mod._upload_lock = asyncio.Lock()

    with patch.object(mod, "_do_checkpoint_upload") as upload_mock:
        result = await mod.flush_now()

    upload_mock.assert_not_called()
    assert mod._flushed_seq == 3
    assert result is True  # up-to-date counts as success


@pytest.mark.asyncio
async def test_flush_now_advances_flushed_seq_on_success(monkeypatch):
    mod = _reload_module()
    monkeypatch.setenv("UIS_GCS_BUCKET", "test-bucket")
    mod._write_seq = 2
    mod._flushed_seq = 0
    mod._upload_lock = asyncio.Lock()

    with patch.object(mod, "_do_checkpoint_upload", return_value=99) as upload_mock:
        result = await mod.flush_now()

    upload_mock.assert_called_once()
    assert mod._flushed_seq == 2
    assert result is True


@pytest.mark.asyncio
async def test_flush_now_does_not_advance_flushed_seq_on_failure(monkeypatch):
    mod = _reload_module()
    monkeypatch.setenv("UIS_GCS_BUCKET", "test-bucket")
    mod._write_seq = 4
    mod._flushed_seq = 1
    mod._upload_lock = asyncio.Lock()

    with patch.object(mod, "_do_checkpoint_upload", side_effect=RuntimeError("boom")) as upload_mock:
        result = await mod.flush_now()

    upload_mock.assert_called_once()
    assert mod._flushed_seq == 1
    assert result is False  # failure must be surfaced, not swallowed


@pytest.mark.asyncio
async def test_flush_now_records_last_flush_time_and_generation_on_success(monkeypatch):
    """Observability: successful flush sets _last_flush_time, _last_flush_generation, clears _last_flush_error."""
    mod = _reload_module()
    monkeypatch.setenv("UIS_GCS_BUCKET", "test-bucket")
    mod._write_seq = 5
    mod._flushed_seq = 0
    mod._upload_lock = asyncio.Lock()
    mod._last_flush_error = "previous error"

    with patch.object(mod, "_do_checkpoint_upload", return_value=77):
        await mod.flush_now()

    assert mod._last_flush_time is not None
    assert mod._last_flush_generation == 77
    assert mod._last_flush_error is None  # cleared on success


@pytest.mark.asyncio
async def test_flush_now_records_last_flush_error_on_failure(monkeypatch):
    """Observability: failed flush sets _last_flush_error and does NOT update _last_flush_time."""
    mod = _reload_module()
    monkeypatch.setenv("UIS_GCS_BUCKET", "test-bucket")
    mod._write_seq = 3
    mod._flushed_seq = 0
    mod._upload_lock = asyncio.Lock()

    with patch.object(mod, "_do_checkpoint_upload", side_effect=RuntimeError("network timeout")):
        result = await mod.flush_now()

    assert result is False
    assert mod._last_flush_error == "network timeout"
    assert mod._last_flush_time is None  # was never set


@pytest.mark.asyncio
async def test_flush_now_returns_true_when_no_lock_gcs_disabled():
    """When _upload_lock is None (GCS not configured), flush_now returns True without doing anything."""
    mod = _reload_module()
    # _upload_lock is None — GCS is not configured
    mod._write_seq = 5
    mod._flushed_seq = 0

    result = await mod.flush_now()

    assert result is True


def test_get_flush_status_returns_expected_shape(monkeypatch):
    """get_flush_status() returns a dict with all expected keys."""
    from datetime import datetime, timezone
    mod = _reload_module()
    mod._write_seq = 3
    mod._flushed_seq = 2
    now = datetime.now(timezone.utc)
    mod._last_flush_time = now
    mod._last_flush_generation = 42
    mod._last_flush_error = None

    status = mod.get_flush_status()

    assert status["write_seq"] == 3
    assert status["flushed_seq"] == 2
    assert status["dirty"] is True
    assert status["last_flush_time"] == now.isoformat()
    assert status["last_flush_generation"] == 42
    assert status["last_flush_error"] is None


def test_get_flush_status_null_fields_before_first_flush():
    """Before any flush runs, time/generation/error fields are all None."""
    mod = _reload_module()

    status = mod.get_flush_status()

    assert status["last_flush_time"] is None
    assert status["last_flush_generation"] is None
    assert status["last_flush_error"] is None
    assert status["dirty"] is False  # write_seq == flushed_seq == 0


@pytest.mark.asyncio
async def test_stop_flush_task_calls_flush_now(monkeypatch):
    mod = _reload_module()
    monkeypatch.setenv("UIS_GCS_BUCKET", "test-bucket")
    mod._flush_task = asyncio.create_task(asyncio.sleep(30))

    with patch.object(mod, "flush_now", new=AsyncMock()) as flush_mock:
        await mod.stop_flush_task()

    flush_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# set_sync_active / _flush_loop sync-gate tests
# ---------------------------------------------------------------------------

def test_set_sync_active_sets_flag():
    """set_sync_active(True/False) toggles the module-level _sync_active flag."""
    mod = _reload_module()
    assert mod._sync_active is False

    mod.set_sync_active(True)
    assert mod._sync_active is True

    mod.set_sync_active(False)
    assert mod._sync_active is False


@pytest.mark.asyncio
async def test_flush_loop_skips_flush_now_when_sync_active(monkeypatch):
    """_flush_loop must skip flush_now() (debug-log only) when _sync_active is True."""
    mod = _reload_module()
    monkeypatch.setenv("UIS_GCS_BUCKET", "test-bucket")
    mod._upload_lock = asyncio.Lock()
    mod._write_seq = 1
    mod._flushed_seq = 0
    mod._sync_active = True

    flush_call_count = 0

    async def _fake_flush_now():
        nonlocal flush_call_count
        flush_call_count += 1
        return True

    # Run one iteration of the loop: sleep is 0 for the test, then cancel.
    original_sleep = asyncio.sleep

    async def _instant_sleep(seconds):
        await original_sleep(0)  # yield control but don't wait

    with patch.object(mod, "flush_now", side_effect=_fake_flush_now):
        with patch("asyncio.sleep", side_effect=_instant_sleep):
            task = asyncio.create_task(mod._flush_loop())
            await asyncio.sleep(0)  # let the loop body run once
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # flush_now must NOT have been called (sync was active).
    assert flush_call_count == 0, (
        f"flush_now() must be skipped when _sync_active=True, was called {flush_call_count} time(s)"
    )


@pytest.mark.asyncio
async def test_flush_loop_calls_flush_now_when_sync_inactive(monkeypatch):
    """_flush_loop calls flush_now() normally when _sync_active is False."""
    mod = _reload_module()
    monkeypatch.setenv("UIS_GCS_BUCKET", "test-bucket")
    mod._upload_lock = asyncio.Lock()
    mod._write_seq = 1
    mod._flushed_seq = 0
    mod._sync_active = False

    flush_call_count = 0

    async def _fake_flush_now():
        nonlocal flush_call_count
        flush_call_count += 1
        return True

    original_sleep = asyncio.sleep

    async def _instant_sleep(seconds):
        await original_sleep(0)

    with patch.object(mod, "flush_now", side_effect=_fake_flush_now):
        with patch("asyncio.sleep", side_effect=_instant_sleep):
            task = asyncio.create_task(mod._flush_loop())
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # flush_now must have been called (sync was inactive).
    assert flush_call_count >= 1, (
        "flush_now() must be called by _flush_loop when _sync_active=False"
    )


@pytest.mark.asyncio
async def test_flush_now_works_while_sync_active(monkeypatch):
    """flush_now() itself is NOT gated by _sync_active — it always runs when called directly.
    The post-sync explicit flush must still work."""
    mod = _reload_module()
    monkeypatch.setenv("UIS_GCS_BUCKET", "test-bucket")
    mod._write_seq = 3
    mod._flushed_seq = 0
    mod._upload_lock = asyncio.Lock()
    mod._sync_active = True  # sync is active — but flush_now() ignores this

    with patch.object(mod, "_do_checkpoint_upload", return_value=42) as upload_mock:
        result = await mod.flush_now()

    # flush_now() must have run despite _sync_active=True.
    upload_mock.assert_called_once()
    assert result is True
    assert mod._flushed_seq == 3
