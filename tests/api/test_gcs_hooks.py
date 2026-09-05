import asyncio
import io
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.settings import router as settings_router


def _mock_settings(source_dir: Path, reader: str = "schwab"):
    ext = "*.csv" if reader == "schwab" else "*.xlsx"
    return {
        "source_registry": {
            reader: {
                "data_dir": str(source_dir),
                "file_patterns": {"main": ext},
                "enabled": True,
                "reader": f"{reader}_reader",
                "asset_prefixes": [],
            }
        },
        "sources": {"pis": {}},
        "subsystems": {},
    }


def test_run_sync_background_uploads_checkpoint_to_gcs(monkeypatch):
    import src.api.routes.sync as sync_module

    loop = asyncio.new_event_loop()
    try:
        sync_conn = MagicMock()
        sync_module._sync_running = True
        sync_module._sync_started_at = "2026-04-08T00:00:00"

        monkeypatch.setenv("UIS_GCS_BUCKET", "bucket-demo")
        monkeypatch.setenv("UIS_DB_PATH", "/tmp/data/unified.duckdb")

        with patch("src.api.routes.sync.load_config", return_value={"source_registry": {}}):
            with patch("src.api.routes.sync.DatabaseConnector", return_value=sync_conn):
                with patch(
                    "src.api.routes.sync.run_full_sync_v3",
                    return_value=SimpleNamespace(success=True, info_messages=[], warnings=[]),
                ):
                    with patch("src.storage.gcs_flush.flush_now", new_callable=AsyncMock) as mock_flush:
                        future = MagicMock()

                        def _capture_coroutine(coro, scheduled_loop):
                            assert scheduled_loop is loop
                            assert asyncio.iscoroutine(coro)
                            coro.close()
                            return future

                        with patch(
                            "src.api.routes.sync.asyncio.run_coroutine_threadsafe",
                            side_effect=_capture_coroutine,
                        ) as mock_schedule:
                            sync_module.run_sync_background("/tmp/runtime.duckdb", loop)

        mock_flush.assert_called_once_with()
        mock_schedule.assert_called_once()
        future.result.assert_called_once_with(timeout=sync_module.FLUSH_WAIT_SECONDS)
        sync_conn.close.assert_called_once()
        assert sync_module._sync_running is False
        assert sync_module._sync_started_at is None
    finally:
        sync_module._sync_running = False
        sync_module._sync_started_at = None
        loop.close()


def test_run_sync_background_sets_sync_active_flag(monkeypatch):
    """run_sync_background must call set_sync_active(True) at start and set_sync_active(False)
    in the finally block, ensuring the GCS flush loop knows when a sync is running."""
    import src.api.routes.sync as sync_module
    import src.storage.gcs_flush as gcs_flush_module

    loop = asyncio.new_event_loop()
    try:
        sync_conn = MagicMock()
        sync_module._sync_running = True
        sync_module._sync_started_at = "2026-07-05T00:00:00"
        # Reset the module flag.
        gcs_flush_module._sync_active = False

        monkeypatch.setenv("UIS_GCS_BUCKET", "bucket-demo")
        monkeypatch.setenv("UIS_DB_PATH", "/tmp/data/unified.duckdb")

        active_states: list[bool] = []

        original_set_sync_active = gcs_flush_module.set_sync_active

        def _tracking_set_sync_active(active: bool) -> None:
            active_states.append(active)
            original_set_sync_active(active)

        with patch("src.api.routes.sync.load_config", return_value={"source_registry": {}}):
            with patch("src.api.routes.sync.DatabaseConnector", return_value=sync_conn):
                with patch(
                    "src.api.routes.sync.run_full_sync_v3",
                    return_value=SimpleNamespace(success=True, info_messages=[], warnings=[]),
                ):
                    with patch("src.storage.gcs_flush.flush_now", new_callable=AsyncMock):
                        with patch(
                            "src.api.routes.sync.asyncio.run_coroutine_threadsafe",
                            return_value=MagicMock(),
                        ):
                            with patch.object(gcs_flush_module, "set_sync_active", side_effect=_tracking_set_sync_active):
                                sync_module.run_sync_background("/tmp/runtime.duckdb", loop)

        # First call must be True (start), last call must be False (finally).
        assert active_states[0] is True, f"First set_sync_active call must be True, got: {active_states}"
        assert active_states[-1] is False, f"Last set_sync_active call must be False, got: {active_states}"
        # At least two calls: set True + set False before flush + defensive False in finally.
        assert len(active_states) >= 2
        # Flag must be cleared at function exit.
        assert gcs_flush_module._sync_active is False
    finally:
        gcs_flush_module._sync_active = False
        sync_module._sync_running = False
        sync_module._sync_started_at = None
        loop.close()


def test_run_sync_background_clears_sync_active_on_exception(monkeypatch):
    """set_sync_active(False) must be called in the finally block even when sync raises."""
    import src.api.routes.sync as sync_module
    import src.storage.gcs_flush as gcs_flush_module

    loop = asyncio.new_event_loop()
    try:
        sync_conn = MagicMock()
        sync_module._sync_running = True
        sync_module._sync_started_at = "2026-07-05T00:00:00"
        gcs_flush_module._sync_active = False

        monkeypatch.setenv("UIS_GCS_BUCKET", "bucket-demo")

        with patch("src.api.routes.sync.load_config", return_value={"source_registry": {}}):
            with patch("src.api.routes.sync.DatabaseConnector", return_value=sync_conn):
                with patch(
                    "src.api.routes.sync.run_full_sync_v3",
                    side_effect=RuntimeError("simulated sync crash"),
                ):
                    with patch(
                        "src.api.routes.sync.asyncio.run_coroutine_threadsafe",
                        return_value=MagicMock(),
                    ):
                        sync_module.run_sync_background("/tmp/runtime.duckdb", loop)

        # Flag must be False after the function exits (via finally).
        assert gcs_flush_module._sync_active is False
    finally:
        gcs_flush_module._sync_active = False
        sync_module._sync_running = False
        sync_module._sync_started_at = None
        loop.close()


def test_upload_source_file_uploads_to_gcs_when_bucket_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("UIS_GCS_BUCKET", "bucket-demo")
    app = FastAPI()
    app.include_router(settings_router)
    client = TestClient(app)

    source_dir = tmp_path / "source_data"
    source_dir.mkdir()
    mock_settings = _mock_settings(source_dir)
    settings_yaml_path = tmp_path / "config" / "settings.yaml"
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    with patch("src.api.routes.settings.settings_manager.load_settings", return_value=mock_settings):
        with patch("src.api.routes.settings._validate_file_at_path", return_value=(True, [], "csv")):
            with patch("src.api.routes.settings.settings_manager.SETTINGS_PATH", settings_yaml_path):
                with patch("src.api.routes.settings.upload_source_to_gcs") as mock_upload:
                    response = client.post(
                        "/settings/sources/upload/schwab",
                        files={"file": ("Schwab-2026-04-08.csv", io.BytesIO(b"h,d\n1,2"), "text/csv")},
                    )

    assert response.status_code == 200
    payload = response.json()
    mock_upload.assert_called_once_with("bucket-demo", "schwab", payload["file_path"])
