import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_write_seq: int = 0
_flushed_seq: int = 0
_flush_task: Optional[asyncio.Task] = None
_upload_lock: Optional[asyncio.Lock] = None

# Observability state — written by flush_now(), read by health endpoint
_last_flush_time: Optional[datetime] = None  # UTC timestamp of last *successful* flush
_last_flush_error: Optional[str] = None       # Error message from last *failed* flush (None on success)
_last_flush_generation: Optional[int] = None  # GCS object generation from last successful upload

# Set to True while a sync is running so _flush_loop skips its periodic call.
# This avoids "Cannot CHECKPOINT: there are other write transactions active"
# errors when the 60s periodic flush fires mid-sync.
# flush_now() itself is *not* gated by this flag — the post-sync explicit flush
# still works because the sync worker calls it after releasing the write transaction.
_sync_active: bool = False

FLUSH_INTERVAL_SECONDS = int(os.getenv("UIS_GCS_FLUSH_INTERVAL", "60"))


def mark_dirty() -> None:
    global _write_seq
    if os.getenv("UIS_GCS_BUCKET"):
        _write_seq += 1


def set_sync_active(active: bool) -> None:
    """Signal whether a full-sync is currently running.

    When active=True the periodic flush loop (_flush_loop) skips its flush_now()
    call and logs at DEBUG level instead — preventing CHECKPOINT collisions while
    DuckDB holds an active write transaction.

    flush_now() itself is NOT gated by this flag; the post-sync explicit flush
    (called by the sync worker after committing) always runs.

    Thread-safe: simple bool write is atomic under the GIL; the event-loop
    reads the flag at the top of each sleep interval.
    """
    global _sync_active
    _sync_active = active


def _do_checkpoint_upload() -> Optional[int]:
    """Checkpoint the DB and upload to GCS.  Returns the GCS generation of the canonical upload,
    or None when GCS is not configured (local mode).  Raises on any upload failure."""
    from pathlib import Path

    from src.database.connector import DatabaseConnector
    from src.storage.gcs import upload_db_to_gcs, upload_settings_to_gcs

    bucket = os.getenv("UIS_GCS_BUCKET")
    if not bucket:
        return None

    db_path = os.getenv("UIS_DB_PATH", "data/unified.duckdb")
    conn = None
    try:
        conn = DatabaseConnector(db_path)
        conn.execute("CHECKPOINT")
    finally:
        if conn:
            conn.close()

    result = upload_db_to_gcs(bucket, db_path)
    # upload_db_to_gcs now returns GCSUploadResult and raises on failure — no silent swallowing.

    # Also persist settings.yaml so model/prompt changes survive restarts
    settings_path = str(Path(__file__).parents[2] / "config" / "settings.yaml")
    try:
        upload_settings_to_gcs(bucket, settings_path)
    except Exception as e:
        logger.warning("Could not upload settings.yaml to GCS: %s", e)

    return result.generation


async def flush_now() -> bool:
    """Flush dirty DB to GCS.  Returns True on success (or when already up-to-date / GCS disabled),
    False when the upload failed.  Never raises — callers check the return value.

    Observability globals updated on every call:
      _last_flush_time      — set to UTC now on success
      _last_flush_error     — set to error string on failure, cleared to None on success
      _last_flush_generation — set to GCS generation on success
    """
    global _flushed_seq, _upload_lock, _last_flush_time, _last_flush_error, _last_flush_generation
    if _upload_lock is None:
        # GCS not configured — nothing to do, not a failure
        return True

    captured_seq = _write_seq
    if captured_seq == _flushed_seq:
        return True

    async with _upload_lock:
        try:
            generation = await asyncio.get_event_loop().run_in_executor(None, _do_checkpoint_upload)
            _flushed_seq = captured_seq
            _last_flush_time = datetime.now(timezone.utc)
            _last_flush_error = None
            _last_flush_generation = generation
            logger.info("GCS flush complete (seq %d, generation=%s)", captured_seq, generation)
            return True
        except Exception as e:
            _last_flush_error = str(e)
            logger.warning("GCS flush failed: %s", e)
            return False


async def _flush_loop() -> None:
    while True:
        try:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            if _sync_active:
                # Skip the periodic flush while a sync holds a write transaction.
                # The post-sync explicit flush_now() call will upload the result.
                logger.debug("GCS periodic flush skipped — sync is active")
            else:
                # flush_now() never raises — it records failure in _last_flush_error and returns False.
                # The loop stays alive regardless; a future periodic flush may succeed.
                await flush_now()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Unexpected error outside flush_now (e.g. sleep interrupted).  Keep loop alive.
            logger.error("Flush loop unexpected exception (continuing): %s", e)


async def start_flush_task() -> None:
    global _flush_task, _upload_lock
    if not os.getenv("UIS_GCS_BUCKET"):
        return
    if _flush_task and not _flush_task.done():
        return

    _upload_lock = asyncio.Lock()
    _flush_task = asyncio.create_task(_flush_loop())
    logger.info("GCS flush task started (interval=%ds)", FLUSH_INTERVAL_SECONDS)


async def stop_flush_task() -> None:
    global _flush_task
    if _flush_task:
        _flush_task.cancel()
        try:
            await _flush_task
        except asyncio.CancelledError:
            pass
        _flush_task = None
    await flush_now()
    logger.info("GCS flush task stopped")


def get_flush_status() -> dict:
    """Return a snapshot of the current GCS flush state — safe to call from any context.

    Keys:
      write_seq          — monotonically increasing counter bumped by mark_dirty()
      flushed_seq        — seq of the last successfully flushed write
      dirty              — True when write_seq > flushed_seq (unflushed changes exist)
      last_flush_time    — ISO 8601 UTC string of the last successful flush, or None
      last_flush_error   — error string from the last failed flush, or None
      last_flush_generation — GCS object generation from last successful upload, or None
    """
    return {
        "write_seq": _write_seq,
        "flushed_seq": _flushed_seq,
        "dirty": _write_seq > _flushed_seq,
        "last_flush_time": _last_flush_time.isoformat() if _last_flush_time is not None else None,
        "last_flush_error": _last_flush_error,
        "last_flush_generation": _last_flush_generation,
    }
