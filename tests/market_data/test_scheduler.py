"""Tests for MarketDataScheduler.

All DB and service calls are mocked — no real database access.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.market_data.scheduler import MarketDataScheduler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the class-level singleton state between tests.

    This is critical: MarketDataScheduler._running is a class-level attribute
    shared across all instances. Each test must start and end with it False.
    """
    # Force reset before each test
    MarketDataScheduler._running = False
    yield
    # Force reset after each test (even if test failed or left state dirty)
    MarketDataScheduler._running = False


@pytest.fixture(autouse=True, scope="session")
def reset_singleton_session():
    """Ensure clean state at end of session."""
    yield
    MarketDataScheduler._running = False


# ---------------------------------------------------------------------------
# Singleton guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_sets_running_flag():
    scheduler = MarketDataScheduler()
    # Patch the internal loop so it doesn't actually run
    scheduler._run_loop = AsyncMock()
    await scheduler.start(interval_minutes=1)
    assert MarketDataScheduler._running is True
    await scheduler.stop()


@pytest.mark.asyncio
async def test_double_start_is_noop():
    """Second start() call should not create a second task."""
    scheduler1 = MarketDataScheduler()
    scheduler2 = MarketDataScheduler()
    scheduler1._run_loop = AsyncMock()
    scheduler2._run_loop = AsyncMock()

    await scheduler1.start(interval_minutes=1)
    assert scheduler1.is_running is True

    # Second start on a different instance — should be a no-op
    await scheduler2.start(interval_minutes=1)
    # Still only one task (scheduler2._task should be None)
    assert scheduler2._task is None

    await scheduler1.stop()


@pytest.mark.asyncio
async def test_stop_clears_running_flag():
    scheduler = MarketDataScheduler()
    scheduler._run_loop = AsyncMock()
    await scheduler.start(interval_minutes=1)
    assert scheduler.is_running is True
    await scheduler.stop()
    assert scheduler.is_running is False


@pytest.mark.asyncio
async def test_stop_when_not_running_is_safe():
    scheduler = MarketDataScheduler()
    # Should not raise
    await scheduler.stop()
    assert scheduler.is_running is False


# ---------------------------------------------------------------------------
# asyncio.to_thread usage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_do_refresh_called_via_to_thread():
    """Verify that _do_refresh is invoked (at least once) during _run_loop on a weekday."""
    scheduler = MarketDataScheduler()
    scheduler._interval_seconds = 0  # no wait

    refresh_calls = []

    def fake_refresh():
        refresh_calls.append(1)
        # Signal stop after first call so the loop exits
        MarketDataScheduler._running = False
        return {"refreshed": 1, "skipped": 0, "errors": 0, "holdings_updated": 1}

    scheduler._do_refresh = fake_refresh
    MarketDataScheduler._running = True

    # Force a weekday (Monday=0) so the weekend-skip branch is not triggered
    fake_now = MagicMock()
    fake_now.weekday.return_value = 0  # Monday
    fake_now.strftime.return_value = "Monday"

    with patch("src.market_data.scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        task = asyncio.create_task(scheduler._run_loop())
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    assert len(refresh_calls) >= 1, "_do_refresh should have been called at least once"


# ---------------------------------------------------------------------------
# Weekend skip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_weekend_skip_logs_and_continues():
    """On a weekend weekday, the loop should log skip and not call _do_refresh."""
    scheduler = MarketDataScheduler()
    scheduler._interval_seconds = 0

    refresh_called = []

    def fake_refresh():
        refresh_called.append(1)
        return {}

    scheduler._do_refresh = fake_refresh

    call_count = [0]

    async def fake_sleep(seconds):
        call_count[0] += 1
        if call_count[0] > 1:
            MarketDataScheduler._running = False


    # Saturday = weekday 5
    fake_now = MagicMock()
    fake_now.weekday.return_value = 5
    fake_now.strftime.return_value = "Saturday"

    MarketDataScheduler._running = True
    with patch("src.market_data.scheduler.asyncio.sleep", fake_sleep):
        with patch("src.market_data.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            task = asyncio.create_task(scheduler._run_loop())
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    assert len(refresh_called) == 0


# ---------------------------------------------------------------------------
# _do_refresh opens a fresh DatabaseConnector
# ---------------------------------------------------------------------------

def test_do_refresh_opens_and_closes_connector():
    """_do_refresh must open a DatabaseConnector and close it even on error."""
    scheduler = MarketDataScheduler()

    mock_connector = MagicMock()
    mock_result = {"refreshed": 2, "skipped": 0, "errors": 0, "holdings_updated": 2}

    mock_service = MagicMock()
    mock_service.refresh_portfolio_prices.return_value = mock_result

    # _do_refresh uses deferred imports, so patch via the source module path
    with patch("src.database.connector.DatabaseConnector", return_value=mock_connector) as mock_db_cls:
        with patch("src.market_data.service.MarketDataService", return_value=mock_service):
            result = scheduler._do_refresh()

    mock_db_cls.assert_called_once()
    mock_service.refresh_portfolio_prices.assert_called_once_with(mock_connector)
    mock_connector.close.assert_called_once()
    assert result == mock_result


def test_do_refresh_closes_connector_on_exception():
    """Connector must be closed even if refresh raises."""
    scheduler = MarketDataScheduler()
    mock_connector = MagicMock()

    with patch("src.database.connector.DatabaseConnector", return_value=mock_connector):
        with patch("src.market_data.service.MarketDataService") as mock_svc_cls:
            mock_svc_cls.return_value.refresh_portfolio_prices.side_effect = RuntimeError("boom")
            with pytest.raises(RuntimeError):
                scheduler._do_refresh()

    mock_connector.close.assert_called_once()
