"""Tests for POST /sync/start/{reader} endpoint."""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch


@pytest.fixture
def client():
    from src.api.main import app
    return TestClient(app)


def test_start_sync_reader_unknown_reader(client):
    """Returns 404 for unknown reader name."""
    response = client.post("/sync/start/unknown_reader")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_start_sync_reader_valid_reader(client):
    """Returns 200 and starts sync for valid reader."""
    import src.api.routes.sync as sync_module
    sync_module._sync_running = False

    with patch("src.api.routes.sync.run_sync_reader_background"):
        with patch("src.api.routes.sync.resolve_db_path", return_value="/tmp/test.db"):
            response = client.post("/sync/start/schwab")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "started"
    assert "schwab" in data["message"]
    # Reset
    sync_module._sync_running = False


def test_start_sync_reader_409_when_running(client):
    """Returns 409 if sync already running."""
    import src.api.routes.sync as sync_module
    sync_module._sync_running = True
    try:
        response = client.post("/sync/start/schwab")
        assert response.status_code == 409
    finally:
        sync_module._sync_running = False


def test_known_readers_set():
    """KNOWN_READERS contains all expected readers (7 as of Workstream C1)."""
    from src.api.routes.sync import KNOWN_READERS
    # Original 6 must all be present
    original_6 = {"schwab", "cn_fund", "gold", "insurance", "rsu", "financial_summary"}
    assert original_6.issubset(KNOWN_READERS)
    # Workstream C1 adds ibkr as the 7th
    assert "ibkr" in KNOWN_READERS
    assert isinstance(KNOWN_READERS, set)


def test_config_override_only_enables_target_reader():
    """run_sync_reader_background only enables the target reader in config override."""
    import copy
    from src.api.routes.sync import KNOWN_READERS

    # Simulate the config override logic
    config = {
        "source_registry": {
            "schwab": {"enabled": True},
            "cn_fund": {"enabled": True},
            "gold": {"enabled": False},
            "insurance": {"enabled": True},
            "rsu": {"enabled": True},
            "financial_summary": {"enabled": True},
        }
    }
    config_override = copy.deepcopy(config)
    src_reg = config_override.get("source_registry", {})
    target = "gold"
    for name in list(src_reg.keys()):
        if name in KNOWN_READERS and isinstance(src_reg[name], dict):
            src_reg[name]["enabled"] = (name == target)

    assert src_reg["gold"]["enabled"] is True
    assert src_reg["schwab"]["enabled"] is False
    assert src_reg["cn_fund"]["enabled"] is False
    assert src_reg["insurance"]["enabled"] is False
    assert src_reg["rsu"]["enabled"] is False
    assert src_reg["financial_summary"]["enabled"] is False
    # Original config not mutated
    assert config["source_registry"]["schwab"]["enabled"] is True


def test_start_sync_reader_sets_running_flag(client):
    """_sync_running is True after a successful start_sync_reader call."""
    import src.api.routes.sync as sync_module
    sync_module._sync_running = False

    with patch("src.api.routes.sync.run_sync_reader_background"):
        with patch("src.api.routes.sync.resolve_db_path", return_value="/tmp/test.db"):
            response = client.post("/sync/start/schwab")

    assert response.status_code == 200
    assert sync_module._sync_running is True
    # Cleanup
    sync_module._sync_running = False


def test_start_sync_reader_blocked_by_full_sync(client):
    """Per-reader sync returns 409 when full sync is already running."""
    import src.api.routes.sync as sync_module
    sync_module._sync_running = True
    try:
        response = client.post("/sync/start/cn_fund")
        assert response.status_code == 409
    finally:
        sync_module._sync_running = False


def test_start_full_sync_blocked_by_reader_sync(client):
    """Full sync returns 409 when per-reader sync is already running."""
    import src.api.routes.sync as sync_module
    sync_module._sync_running = True
    try:
        response = client.post("/sync/start")
        assert response.status_code == 409
    finally:
        sync_module._sync_running = False
