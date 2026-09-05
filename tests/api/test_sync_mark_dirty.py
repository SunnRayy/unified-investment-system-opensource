"""Tests that run_sync_background and run_sync_reader_background call mark_dirty
before flush_now, ensuring post-sync uploads actually reach GCS.

All heavy dependencies (DatabaseConnector, run_full_sync_v3, flush_now) are
patched — no real DB or network I/O occurs.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch


def _make_sync_result(success: bool = True) -> MagicMock:
    r = MagicMock()
    r.success = success
    r.info_messages = []
    r.warnings = []
    return r


def _make_flush_future(result: bool = True) -> MagicMock:
    """Returns a MagicMock that behaves like a concurrent.futures.Future."""
    f = MagicMock()
    f.result.return_value = result
    return f


# ---------------------------------------------------------------------------
# run_sync_background
# ---------------------------------------------------------------------------

def test_run_sync_background_calls_mark_dirty():
    """mark_dirty() must be called once after run_full_sync_v3 succeeds."""
    loop = asyncio.new_event_loop()
    try:
        with (
            patch("src.api.routes.sync.run_full_sync_v3", return_value=_make_sync_result()),
            patch("src.api.routes.sync.DatabaseConnector"),
            patch("src.api.routes.sync.load_config", return_value={}),
            patch("src.storage.gcs_flush.mark_dirty") as mock_mark_dirty,
            patch("asyncio.run_coroutine_threadsafe", return_value=_make_flush_future()),
        ):
            from src.api.routes.sync import run_sync_background
            run_sync_background("/tmp/test_mark_dirty.db", loop)

        mock_mark_dirty.assert_called_once()
    finally:
        loop.close()
        # Reset global sync state
        import src.api.routes.sync as sync_mod
        sync_mod._sync_running = False
        sync_mod._sync_started_at = None


def test_run_sync_background_mark_dirty_called_before_flush():
    """mark_dirty() must be called BEFORE flush_now to bump the write seq."""
    call_order: list[str] = []
    loop = asyncio.new_event_loop()

    def _fake_mark_dirty():
        call_order.append("mark_dirty")

    def _fake_run_coroutine_threadsafe(coro, _loop):
        # Consume the coroutine to avoid ResourceWarning
        try:
            loop2 = asyncio.new_event_loop()
            loop2.run_until_complete(coro)
            loop2.close()
        except Exception:
            pass
        call_order.append("flush_now")
        return _make_flush_future()

    try:
        with (
            patch("src.api.routes.sync.run_full_sync_v3", return_value=_make_sync_result()),
            patch("src.api.routes.sync.DatabaseConnector"),
            patch("src.api.routes.sync.load_config", return_value={}),
            patch("src.storage.gcs_flush.mark_dirty", side_effect=_fake_mark_dirty),
            patch("asyncio.run_coroutine_threadsafe", side_effect=_fake_run_coroutine_threadsafe),
        ):
            from src.api.routes.sync import run_sync_background
            run_sync_background("/tmp/test_order.db", loop)

        assert call_order.index("mark_dirty") < call_order.index("flush_now"), (
            f"mark_dirty must precede flush_now; order was {call_order}"
        )
    finally:
        loop.close()
        import src.api.routes.sync as sync_mod
        sync_mod._sync_running = False
        sync_mod._sync_started_at = None


# ---------------------------------------------------------------------------
# run_sync_reader_background
# ---------------------------------------------------------------------------

def test_run_sync_reader_background_calls_mark_dirty():
    """mark_dirty() must be called once after per-reader run_full_sync_v3 succeeds."""
    loop = asyncio.new_event_loop()
    try:
        with (
            patch("src.api.routes.sync.run_full_sync_v3", return_value=_make_sync_result()),
            patch("src.api.routes.sync.DatabaseConnector"),
            patch("src.api.routes.sync.load_config", return_value={"source_registry": {}}),
            patch("src.storage.gcs_flush.mark_dirty") as mock_mark_dirty,
            patch("asyncio.run_coroutine_threadsafe", return_value=_make_flush_future()),
        ):
            from src.api.routes.sync import run_sync_reader_background
            run_sync_reader_background("schwab", "/tmp/test_reader_mark_dirty.db", loop)

        mock_mark_dirty.assert_called_once()
    finally:
        loop.close()
        import src.api.routes.sync as sync_mod
        sync_mod._sync_running = False
        sync_mod._sync_started_at = None


def test_run_sync_reader_background_mark_dirty_before_flush():
    """mark_dirty() must be called before flush_now in per-reader sync path."""
    call_order: list[str] = []
    loop = asyncio.new_event_loop()

    def _fake_mark_dirty():
        call_order.append("mark_dirty")

    def _fake_run_coroutine_threadsafe(coro, _loop):
        try:
            loop2 = asyncio.new_event_loop()
            loop2.run_until_complete(coro)
            loop2.close()
        except Exception:
            pass
        call_order.append("flush_now")
        return _make_flush_future()

    try:
        with (
            patch("src.api.routes.sync.run_full_sync_v3", return_value=_make_sync_result()),
            patch("src.api.routes.sync.DatabaseConnector"),
            patch("src.api.routes.sync.load_config", return_value={"source_registry": {}}),
            patch("src.storage.gcs_flush.mark_dirty", side_effect=_fake_mark_dirty),
            patch("asyncio.run_coroutine_threadsafe", side_effect=_fake_run_coroutine_threadsafe),
        ):
            from src.api.routes.sync import run_sync_reader_background
            run_sync_reader_background("schwab", "/tmp/test_reader_order.db", loop)

        assert call_order.index("mark_dirty") < call_order.index("flush_now"), (
            f"mark_dirty must precede flush_now; order was {call_order}"
        )
    finally:
        loop.close()
        import src.api.routes.sync as sync_mod
        sync_mod._sync_running = False
        sync_mod._sync_started_at = None
