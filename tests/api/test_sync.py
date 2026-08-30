import concurrent.futures
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def test_sync_start_endpoint():
    import src.api.routes.sync as sync_module
    from src.api.main import app

    client = TestClient(app)
    sync_module._sync_running = False

    try:
        with patch("src.api.routes.sync.run_sync_background"):
            with patch("src.api.routes.sync.resolve_db_path", return_value="/tmp/test.db"):
                response = client.post("/sync/start")

        assert response.status_code == 200
        assert response.json()["status"] == "started"
    finally:
        sync_module._sync_running = False
        sync_module._sync_started_at = None


# ---------------------------------------------------------------------------
# _flush_and_report tests
# ---------------------------------------------------------------------------

def _captured_messages(mock_loop: MagicMock) -> list[str]:
    """Extract queued messages from mock_loop.call_soon_threadsafe calls."""
    msgs = []
    for call in mock_loop.call_soon_threadsafe.call_args_list:
        args = call.args
        # call_soon_threadsafe(fn, *fn_args) — fn is log_queue.put_nowait, fn_arg is the message
        if len(args) >= 2:
            msgs.append(args[1])
    return msgs


class TestFlushAndReport:
    """Unit tests for the _flush_and_report helper in src/api/routes/sync.py."""

    def _make_loop(self) -> MagicMock:
        """Return a mock loop whose call_soon_threadsafe records calls."""
        loop = MagicMock()
        return loop

    def test_timeout_queues_flush_timeout_not_failed(self, monkeypatch):
        """TimeoutError → GCS_FLUSH_TIMEOUT queued; GCS_FLUSH_FAILED must NOT appear."""
        from src.api.routes.sync import _flush_and_report

        mock_loop = self._make_loop()
        mock_future = MagicMock()
        mock_future.result.side_effect = concurrent.futures.TimeoutError()

        monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)

        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_future), \
             patch("src.storage.gcs_flush.flush_now", new=MagicMock(return_value=None)):
            _flush_and_report(mock_loop)

        messages = _captured_messages(mock_loop)
        assert any("GCS_FLUSH_TIMEOUT" in m for m in messages), (
            f"Expected GCS_FLUSH_TIMEOUT in messages; got: {messages}"
        )
        assert not any("GCS_FLUSH_FAILED" in m for m in messages), (
            f"GCS_FLUSH_FAILED must not appear on timeout; got: {messages}"
        )

    def test_flush_failed_queues_flush_failed(self, monkeypatch):
        """flush_ok=False → GCS_FLUSH_FAILED queued."""
        from src.api.routes.sync import _flush_and_report

        mock_loop = self._make_loop()
        mock_future = MagicMock()
        mock_future.result.return_value = False

        monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)

        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_future), \
             patch("src.storage.gcs_flush.flush_now", new=MagicMock(return_value=None)):
            _flush_and_report(mock_loop)

        messages = _captured_messages(mock_loop)
        assert any("GCS_FLUSH_FAILED" in m for m in messages), (
            f"Expected GCS_FLUSH_FAILED in messages; got: {messages}"
        )
        assert not any("GCS_FLUSH_OK" in m for m in messages)

    def test_flush_ok_with_bucket_queues_flush_ok(self, monkeypatch):
        """flush_ok=True + UIS_GCS_BUCKET set → GCS_FLUSH_OK queued."""
        from src.api.routes.sync import _flush_and_report

        mock_loop = self._make_loop()
        mock_future = MagicMock()
        mock_future.result.return_value = True

        monkeypatch.setenv("UIS_GCS_BUCKET", "my-bucket")

        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_future), \
             patch("src.storage.gcs_flush.flush_now", new=MagicMock(return_value=None)):
            _flush_and_report(mock_loop)

        messages = _captured_messages(mock_loop)
        assert any("GCS_FLUSH_OK" in m for m in messages), (
            f"Expected GCS_FLUSH_OK in messages; got: {messages}"
        )
        assert not any("GCS_FLUSH_FAILED" in m for m in messages)

    def test_flush_ok_without_bucket_queues_nothing(self, monkeypatch):
        """flush_ok=True but no UIS_GCS_BUCKET → no status message queued."""
        from src.api.routes.sync import _flush_and_report

        mock_loop = self._make_loop()
        mock_future = MagicMock()
        mock_future.result.return_value = True

        monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)

        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_future), \
             patch("src.storage.gcs_flush.flush_now", new=MagicMock(return_value=None)):
            _flush_and_report(mock_loop)

        messages = _captured_messages(mock_loop)
        assert not any("GCS_FLUSH" in m for m in messages), (
            f"No GCS status message expected in local mode; got: {messages}"
        )

    def test_generic_exception_queues_flush_failed(self, monkeypatch):
        """A generic Exception (not TimeoutError) → GCS_FLUSH_FAILED queued."""
        from src.api.routes.sync import _flush_and_report

        mock_loop = self._make_loop()
        mock_future = MagicMock()
        mock_future.result.side_effect = RuntimeError("connection refused")

        monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)

        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_future), \
             patch("src.storage.gcs_flush.flush_now", new=MagicMock(return_value=None)):
            _flush_and_report(mock_loop)

        messages = _captured_messages(mock_loop)
        assert any("GCS_FLUSH_FAILED" in m for m in messages), (
            f"Expected GCS_FLUSH_FAILED after generic exception; got: {messages}"
        )
