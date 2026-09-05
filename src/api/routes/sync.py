import asyncio
import concurrent.futures
import copy
import logging
from datetime import datetime
from typing import AsyncGenerator, Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException
from sse_starlette.sse import EventSourceResponse
from src.sync.orchestrator import run_full_sync_v3
from src.config import load_config
from src.database.connector import DatabaseConnector, resolve_db_path
from src.sources.registry import get_registry
from src.api import stream_tickets

router = APIRouter(prefix="/sync", tags=["Sync"])

# Seconds to wait for a GCS flush future before treating it as a background upload.
# 300s provides headroom for a 66 MiB DB on a slow connection; the periodic flush
# loop (UIS_GCS_FLUSH_INTERVAL, default 60s) will confirm success afterwards.
FLUSH_WAIT_SECONDS = 300

# Derived from registry — same name, same value, single source of truth.
KNOWN_READERS: set = set(get_registry().key_known_list())

# Global queue for log streaming
# In a production app with multiple workers, this wouldn't work (need Redis/PubSub).
# For single-worker local app, this is fine.
log_queue: asyncio.Queue = asyncio.Queue()

_sync_running: bool = False
_sync_started_at: Optional[str] = None


class QueueHandler(logging.Handler):
    def __init__(self, loop):
        super().__init__()
        self.loop = loop

    def emit(self, record):
        try:
            msg = self.format(record)
            # Put in queue properly from thread
            self.loop.call_soon_threadsafe(log_queue.put_nowait, msg)
        except Exception:
            self.handleError(record)


def _flush_and_report(loop: asyncio.AbstractEventLoop) -> None:
    """Flush the DB to GCS and queue a status message.

    Must be called from a worker thread (not the event loop thread).
    Handles three outcomes:
      - TimeoutError  → GCS_FLUSH_TIMEOUT (upload continues in background; NOT a failure)
      - flush_ok=False → GCS_FLUSH_FAILED
      - flush_ok=True + GCS bucket configured → GCS_FLUSH_OK
    """
    import os as _os

    try:
        from src.storage.gcs_flush import flush_now
        future = asyncio.run_coroutine_threadsafe(flush_now(), loop)
        flush_ok = future.result(timeout=FLUSH_WAIT_SECONDS)
    except concurrent.futures.TimeoutError:
        logging.getLogger(__name__).warning(
            "GCS flush did not complete within %ss — upload continues in background",
            FLUSH_WAIT_SECONDS,
        )
        loop.call_soon_threadsafe(
            log_queue.put_nowait,
            f"GCS_FLUSH_TIMEOUT: flush did not complete within {FLUSH_WAIT_SECONDS}s"
            " — upload continues in background; the periodic flush loop will confirm."
            " Check /health for flush status.",
        )
        return
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Failed to flush DB to GCS after sync: %s", repr(e)
        )
        flush_ok = False

    if not flush_ok:
        loop.call_soon_threadsafe(
            log_queue.put_nowait,
            "GCS_FLUSH_FAILED: Data was NOT persisted to GCS — sync computation succeeded"
            " but the database was not uploaded. The next periodic flush or sync will retry.",
        )
    else:
        if _os.getenv("UIS_GCS_BUCKET"):
            loop.call_soon_threadsafe(log_queue.put_nowait, "GCS_FLUSH_OK: Database persisted to GCS.")


def run_sync_background(db_path: str, loop: asyncio.AbstractEventLoop):
    """
    Run the sync process in background.
    """
    global _sync_running, _sync_started_at

    # Suppress periodic GCS flush while the sync holds a DuckDB write transaction.
    # This prevents "Cannot CHECKPOINT: other write transactions active" errors.
    from src.storage.gcs_flush import mark_dirty, set_sync_active
    set_sync_active(True)

    # Setup logging capture
    handler = QueueHandler(loop)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    conn = None
    try:
        loop.call_soon_threadsafe(log_queue.put_nowait, "STARTING SYNC...")

        config = load_config()

        # Create fresh connector for this thread
        conn = DatabaseConnector(db_path)

        result = run_full_sync_v3(conn, config)

        # Mark the DB dirty so flush_now() actually uploads.  Without this,
        # flush_now() short-circuits on _write_seq == _flushed_seq and silently
        # no-ops (logging "GCS_FLUSH_OK" even though nothing was uploaded).
        mark_dirty()

        loop.call_soon_threadsafe(log_queue.put_nowait, f"SYNC COMPLETED. Success: {result.success}")
        if result.info_messages:
            for m in result.info_messages:
                loop.call_soon_threadsafe(log_queue.put_nowait, f"INFO: {m}")
        if result.warnings:
            for w in result.warnings:
                loop.call_soon_threadsafe(log_queue.put_nowait, f"WARNING: {w}")
        # Allow periodic flush to resume, then perform the post-sync explicit flush.
        # Order: sync work done → clear flag → flush (no write transaction held here).
        set_sync_active(False)
        _flush_and_report(loop)

    except Exception as e:
        loop.call_soon_threadsafe(log_queue.put_nowait, f"ERROR: {str(e)}")
    finally:
        root_logger.removeHandler(handler)
        if conn:
            conn.close()
        _sync_running = False
        _sync_started_at = None
        set_sync_active(False)  # defensive — ensures flag is cleared even on exception
        # Signal end of stream
        loop.call_soon_threadsafe(log_queue.put_nowait, "DONE")

def run_sync_reader_background(reader: str, db_path: str, loop: asyncio.AbstractEventLoop):
    """Run sync for a single reader in background."""
    global _sync_running, _sync_started_at

    # Suppress periodic GCS flush while the sync holds a DuckDB write transaction.
    # This prevents "Cannot CHECKPOINT: other write transactions active" errors.
    from src.storage.gcs_flush import mark_dirty, set_sync_active
    set_sync_active(True)

    handler = QueueHandler(loop)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    conn = None
    try:
        loop.call_soon_threadsafe(log_queue.put_nowait, f"STARTING SYNC for {reader}...")

        config = load_config()
        config_override = copy.deepcopy(config)
        src_reg = config_override.get("source_registry", {})
        for name in list(src_reg.keys()):
            if name in KNOWN_READERS and isinstance(src_reg[name], dict):
                src_reg[name]["enabled"] = (name == reader)

        if not src_reg.get(reader, {}).get("enabled"):
            loop.call_soon_threadsafe(
                log_queue.put_nowait,
                f"WARNING: Reader '{reader}' not found in source_registry config — sync will run but may produce no reader data"
            )

        conn = DatabaseConnector(db_path)
        result = run_full_sync_v3(conn, config_override)

        # Mark the DB dirty so flush_now() actually uploads.  Without this,
        # flush_now() short-circuits on _write_seq == _flushed_seq and silently
        # no-ops (logging "GCS_FLUSH_OK" even though nothing was uploaded).
        mark_dirty()

        loop.call_soon_threadsafe(log_queue.put_nowait, f"SYNC COMPLETED. Success: {result.success}")
        if result.info_messages:
            for m in result.info_messages:
                loop.call_soon_threadsafe(log_queue.put_nowait, f"INFO: {m}")
        if result.warnings:
            for w in result.warnings:
                loop.call_soon_threadsafe(log_queue.put_nowait, f"WARNING: {w}")
        # Allow periodic flush to resume, then perform the post-sync explicit flush.
        # Order: sync work done → clear flag → flush (no write transaction held here).
        set_sync_active(False)
        _flush_and_report(loop)
        loop.call_soon_threadsafe(
            log_queue.put_nowait,
            "NOTE: Shadow cleanup, FIFO backfill, and integrity checks ran on full dataset (required for data consistency)"
        )

    except Exception as e:
        loop.call_soon_threadsafe(log_queue.put_nowait, f"ERROR: {str(e)}")
    finally:
        root_logger.removeHandler(handler)
        if conn:
            conn.close()
        _sync_running = False
        _sync_started_at = None
        set_sync_active(False)  # defensive — ensures flag is cleared even on exception
        loop.call_soon_threadsafe(log_queue.put_nowait, "DONE")

@router.post("/start")
async def start_sync(background_tasks: BackgroundTasks):
    """Start the sync process in the background."""
    global _sync_running, _sync_started_at
    if _sync_running:
        raise HTTPException(status_code=409, detail="Sync already running")
    _sync_running = True
    _sync_started_at = datetime.now().isoformat()
    db_path = resolve_db_path()

    # Clear queue roughly
    while not log_queue.empty():
        try:
            log_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    loop = asyncio.get_running_loop()
    try:
        background_tasks.add_task(run_sync_background, db_path, loop)
    except Exception:
        _sync_running = False
        _sync_started_at = None
        raise
    return {"status": "started", "message": "Sync started in background"}

@router.post("/start/{reader}")
async def start_sync_reader(reader: str, background_tasks: BackgroundTasks):
    """Start sync for a single reader in the background."""
    global _sync_running, _sync_started_at

    if reader not in KNOWN_READERS:
        raise HTTPException(status_code=404, detail=f"Reader '{reader}' not found")

    if _sync_running:
        raise HTTPException(status_code=409, detail="Sync already running")

    _sync_running = True
    _sync_started_at = datetime.now().isoformat()
    db_path = resolve_db_path()

    while not log_queue.empty():
        try:
            log_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    loop = asyncio.get_running_loop()
    try:
        background_tasks.add_task(run_sync_reader_background, reader, db_path, loop)
    except Exception:
        _sync_running = False
        _sync_started_at = None
        raise
    return {"status": "started", "message": f"Sync started for {reader}"}

@router.get("/status")
async def get_sync_status():
    """Return whether a sync is currently running."""
    return {"running": _sync_running, "started_at": _sync_started_at}

@router.post("/stream-ticket")
async def issue_stream_ticket():
    """Issue a short-lived, stream-scoped ticket for SSE authentication.

    The caller must already be authenticated via the normal Authorization header
    (enforced by BearerTokenMiddleware — this endpoint is not a carve-out).
    The returned ticket may be used as ``?ticket=<ticket>`` on GET /sync/stream;
    it expires after 10 minutes and is reusable within that window (so
    EventSource auto-reconnects work without fetching a new ticket).
    """
    return {"ticket": stream_tickets.issue()}


@router.get("/stream")
async def stream_logs():
    """Stream logs via SSE."""
    async def event_generator() -> AsyncGenerator[str, None]:
        while True:
            # Wait for new log
            message = await log_queue.get()

            if message == "DONE":
                yield {"data": message, "event": "end"}
                break

            yield {"data": message, "event": "log"}

    return EventSourceResponse(event_generator())
