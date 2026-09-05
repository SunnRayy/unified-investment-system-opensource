"""Market data auto-refresh scheduler.

Runs refresh_portfolio_prices() on a configurable interval while the API server
is alive. Uses asyncio background task, running the synchronous refresh via
asyncio.to_thread() so it does not block the event loop.

Singleton guard: a class-level _running flag prevents double-start when
uvicorn --reload briefly overlaps old/new processes.
"""

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class MarketDataScheduler:
    """Schedules periodic market data refreshes.

    Usage (in FastAPI lifespan):
        scheduler = MarketDataScheduler()
        await scheduler.start(interval_minutes=30)
        yield
        await scheduler.stop()
    """

    # Class-level singleton guard: prevents double-start within one process
    _running: bool = False
    _lock: asyncio.Lock = None  # lazily created per-instance below

    def __init__(self):
        # Lock is created lazily in start()/stop() to avoid event-loop binding
        # issues in Python 3.9 when instantiated outside an async context.
        self._instance_lock: asyncio.Lock = None
        self._task: asyncio.Task = None
        self._interval_seconds: int = 1800  # default 30 minutes

    def _get_lock(self) -> asyncio.Lock:
        """Lazily create the asyncio.Lock to avoid event-loop binding issues."""
        if self._instance_lock is None:
            self._instance_lock = asyncio.Lock()
        return self._instance_lock

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self, interval_minutes: int = 30) -> None:
        """Start the refresh loop.

        No-op if already running (singleton guard). Thread-safe via asyncio.Lock.
        """
        async with self._get_lock():
            if MarketDataScheduler._running:
                logger.info(
                    "MarketDataScheduler.start() called but scheduler is already running — skipping"
                )
                return

            self._interval_seconds = interval_minutes * 60
            MarketDataScheduler._running = True
            self._task = asyncio.create_task(self._run_loop())
            logger.info(
                f"MarketDataScheduler started (interval={interval_minutes}m)"
            )

    async def stop(self) -> None:
        """Stop the refresh loop and await task cancellation."""
        async with self._get_lock():
            if not MarketDataScheduler._running:
                return
            MarketDataScheduler._running = False
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None
            logger.info("MarketDataScheduler stopped")

    @property
    def is_running(self) -> bool:
        return MarketDataScheduler._running

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Main background loop: sleep then refresh."""
        while MarketDataScheduler._running:
            try:
                await asyncio.sleep(self._interval_seconds)
            except asyncio.CancelledError:
                break

            if not MarketDataScheduler._running:
                break

            # Market-hours awareness: skip weekends
            now = datetime.now()
            weekday = now.weekday()  # 0=Monday … 6=Sunday
            if weekday >= 5:
                logger.info(
                    f"MarketDataScheduler: skipping weekend refresh "
                    f"(weekday={weekday}, {now.strftime('%A')})"
                )
                continue

            try:
                result = await asyncio.to_thread(self._do_refresh)
                logger.info(
                    f"MarketDataScheduler: refresh complete — "
                    f"refreshed={result.get('refreshed', 0)}, "
                    f"skipped={result.get('skipped', 0)}, "
                    f"errors={result.get('errors', 0)}, "
                    f"holdings_updated={result.get('holdings_updated', 0)}"
                )
            except Exception as exc:
                logger.warning(f"MarketDataScheduler: refresh failed — {exc}")

    def _do_refresh(self) -> dict:
        """Synchronous refresh: opens DB, calls MarketDataService, closes DB.

        Called via asyncio.to_thread() to avoid blocking the event loop.
        """
        from src.database.connector import DatabaseConnector
        from src.market_data.service import MarketDataService
        from src.storage.gcs_flush import mark_dirty

        connector = DatabaseConnector()
        try:
            result = MarketDataService().refresh_portfolio_prices(connector)
            mark_dirty()
            return result
        finally:
            connector.close()
