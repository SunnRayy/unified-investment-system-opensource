import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.market_data.service import MarketDataService
from src.market_data.scheduler import MarketDataScheduler
from src.storage.gcs_flush import mark_dirty

router = APIRouter(prefix="/market-data", tags=["Market Data"])


def _open_writable(db: DatabaseConnector) -> DatabaseConnector:
    if getattr(db, "read_only", False):
        db_path = db.db_path
        db.close()
        return DatabaseConnector(db_path, read_only=False)
    return db


def _open_read_only(db: DatabaseConnector) -> DatabaseConnector:
    # DuckDB disallows a read-only connection when a read-write connection is already open
    # on the same file (single-process Cloud Run). Just return the existing connection.
    return db


def _load_last_refresh(db: DatabaseConnector) -> Optional[Dict]:
    row = db.execute(
        "SELECT value FROM sync_state WHERE key = 'market_data_last_refresh'"
    ).fetchone()
    if not row or not row[0]:
        return None
    return json.loads(row[0])


def _compute_staleness(last_refresh: Optional[Dict]) -> str:
    if not last_refresh:
        return "never"

    timestamp = last_refresh.get("timestamp")
    if not timestamp:
        return "never"

    refreshed_at = datetime.fromisoformat(timestamp)
    if refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)

    hours_since = (
        datetime.now(timezone.utc) - refreshed_at.astimezone(timezone.utc)
    ).total_seconds() / 3600

    if hours_since < 4:
        return "fresh"
    if hours_since <= 24:
        return "aging"
    return "stale"


def _load_provider_status(db: DatabaseConnector) -> List[Dict]:
    rows = db.execute(
        """
        WITH latest_active AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_snapshot
            FROM holdings
            WHERE is_shadow = FALSE
              AND quantity > 0
            GROUP BY asset_id
        ),
        active_holdings AS (
            SELECT DISTINCT h.asset_id
            FROM holdings h
            JOIN latest_active l
              ON h.asset_id = l.asset_id
             AND h.snapshot_date = l.latest_snapshot
            WHERE h.is_shadow = FALSE
              AND h.quantity > 0
        )
        SELECT
            CASE
                WHEN asset_id LIKE 'CN_FUND_%' THEN 'cn_fund'
                WHEN asset_id LIKE 'US_STK_%'
                  OR asset_id LIKE 'US_ETF_%'
                  OR asset_id LIKE 'RSU_%' THEN 'us'
            END AS market,
            CASE
                WHEN asset_id LIKE 'CN_FUND_%' THEN 'akshare'
                WHEN asset_id LIKE 'US_STK_%'
                  OR asset_id LIKE 'US_ETF_%'
                  OR asset_id LIKE 'RSU_%' THEN 'yfinance'
            END AS fetcher,
            COUNT(*) AS asset_count
        FROM active_holdings
        WHERE asset_id LIKE 'CN_FUND_%'
           OR asset_id LIKE 'US_STK_%'
           OR asset_id LIKE 'US_ETF_%'
           OR asset_id LIKE 'RSU_%'
        GROUP BY 1, 2
        ORDER BY market
        """
    ).fetchall()

    return [
        {
            "market": market,
            "fetcher": fetcher,
            "asset_count": asset_count,
            "status": "active",
        }
        for market, fetcher, asset_count in rows
    ]


@router.post("/refresh")
async def refresh_market_data(db: DatabaseConnector = Depends(get_db)):
    writable = None
    try:
        writable = _open_writable(db)
        result = MarketDataService().refresh_portfolio_prices(writable)
        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        writable.execute(
            """
            INSERT INTO sync_state (key, value, updated_at)
            VALUES ('market_data_last_refresh', ?, CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = EXCLUDED.updated_at
            """,
            (json.dumps(result),),
        )
        mark_dirty()
        return result
    finally:
        if writable and writable is not db:
            writable.close()


class ScheduleConfig(BaseModel):
    enabled: bool
    interval_minutes: int


def _load_auto_refresh_config() -> dict:
    """Load market_data.auto_refresh section from settings.yaml."""
    try:
        import yaml
        from pathlib import Path
        settings_path = Path(__file__).parents[3] / "config" / "settings.yaml"
        with open(settings_path) as f:
            settings = yaml.safe_load(f) or {}
        return settings.get("market_data", {}).get("auto_refresh", {"enabled": False, "interval_minutes": 30})
    except Exception:
        return {"enabled": False, "interval_minutes": 30}


def _save_auto_refresh_config(config: dict) -> None:
    """Persist market_data.auto_refresh to settings.yaml using the settings_manager pattern."""
    try:
        from src.services import settings_manager as _sm
        import yaml
        from pathlib import Path
        settings_path = Path(__file__).parents[3] / "config" / "settings.yaml"
        import fcntl, os, tempfile
        try:
            from ruamel.yaml import YAML
            yaml_rt = YAML()
            yaml_rt.preserve_quotes = True
            lock_fd = open(_sm.SETTINGS_LOCK_PATH, "w")
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                with open(settings_path) as f:
                    data = yaml_rt.load(f)
                if "market_data" not in data:
                    data["market_data"] = {}
                if "auto_refresh" not in data["market_data"]:
                    data["market_data"]["auto_refresh"] = {}
                data["market_data"]["auto_refresh"]["enabled"] = config["enabled"]
                data["market_data"]["auto_refresh"]["interval_minutes"] = config["interval_minutes"]
                tmp_fd, tmp_path = tempfile.mkstemp(dir=settings_path.parent, suffix=".tmp")
                try:
                    with os.fdopen(tmp_fd, "w") as tmp_f:
                        yaml_rt.dump(data, tmp_f)
                        tmp_f.flush()
                        os.fsync(tmp_f.fileno())
                    os.replace(tmp_path, settings_path)
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
        except ImportError:
            with open(settings_path) as f:
                data = yaml.safe_load(f) or {}
            if "market_data" not in data:
                data["market_data"] = {}
            data["market_data"]["auto_refresh"] = config
            with open(settings_path, "w") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save schedule config: {e}")


@router.get("/refresh/schedule")
async def get_refresh_schedule():
    """Return current auto-refresh schedule configuration and running status."""
    config = _load_auto_refresh_config()
    # Check singleton status
    status = "running" if MarketDataScheduler._running else "stopped"
    return {
        "enabled": config.get("enabled", False),
        "interval_minutes": config.get("interval_minutes", 30),
        "status": status,
    }


@router.put("/refresh/schedule")
async def update_refresh_schedule(body: ScheduleConfig):
    """Update auto-refresh schedule. Persists to settings.yaml.

    Note: Does not hot-reconfigure a running scheduler. Restart the API server to
    apply new settings.
    """
    config = {"enabled": body.enabled, "interval_minutes": body.interval_minutes}
    _save_auto_refresh_config(config)
    mark_dirty()
    status = "running" if MarketDataScheduler._running else "stopped"
    return {
        "enabled": body.enabled,
        "interval_minutes": body.interval_minutes,
        "status": status,
        "message": "Schedule config saved. Restart API server to apply changes.",
    }


@router.get("/status")
async def get_market_data_status(db: DatabaseConnector = Depends(get_db)):
    reader = None
    try:
        reader = _open_read_only(db)
        last_refresh = _load_last_refresh(reader)
        return {
            "last_refresh": last_refresh,
            "providers": _load_provider_status(reader),
            "staleness": _compute_staleness(last_refresh),
        }
    finally:
        if reader and reader is not db:
            reader.close()
