from abc import ABC, abstractmethod
from datetime import datetime
import logging
import threading
import traceback
from typing import Optional

from src.market_data.fetchers.types import RealtimeQuote

logger = logging.getLogger(__name__)


class DataFetchError(Exception):
    """Raised when all fetchers fail to retrieve data."""


class NoDataError(Exception):
    """Soft error: symbol not found, holiday, or no trading data.
    Does NOT increment the circuit breaker."""


class ProviderError(Exception):
    """Hard error: network timeout or API error.
    Increments the circuit breaker."""


class InsufficientDataError(Exception):
    """Raised when bars returned are fewer than the min_bars threshold."""


class UnsupportedCodeError(Exception):
    """Raised for asset code patterns that cannot be mapped to any fetcher."""


class BaseFetcher(ABC):
    """Abstract base for all market data fetchers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this fetcher."""

    @abstractmethod
    def fetch_ohlcv(self, code: str, days: int) -> list:
        """Fetch historical OHLCV bars.

        Returns:
            list[OHLCVBar]

        Raises:
            NoDataError: symbol not found or no data available
            ProviderError: network / API error
            UnsupportedCodeError: code cannot be mapped to this provider
        """

    @abstractmethod
    def fetch_realtime(self, code: str) -> RealtimeQuote:
        """Fetch latest realtime quote.

        Returns:
            RealtimeQuote

        Raises:
            NoDataError: price unavailable
            ProviderError: network / API error
            UnsupportedCodeError: code cannot be mapped to this provider
        """


class FetcherManager:
    """Orchestrates a list of fetchers with per-fetcher circuit breakers."""

    def __init__(
        self,
        fetchers: list,
        min_bars: int = 20,
        circuit_open_threshold: int = 3,
        circuit_reset_seconds: int = 300,
    ):
        self._fetchers = fetchers
        self._min_bars = min_bars
        self._circuit_open_threshold = circuit_open_threshold
        self._circuit_reset_seconds = circuit_reset_seconds

        # Keyed by (fetcher_name, operation) where operation is "ohlcv" or "realtime"
        self._failures: dict = {}
        self._circuit_open_at: dict = {}
        self._lock = threading.Lock()

    def _is_circuit_open(self, fetcher_name: str, operation: str) -> bool:
        """Return True if circuit is open and reset time has not elapsed.
        Auto-resets the circuit if the timeout has passed."""
        key = (fetcher_name, operation)
        with self._lock:
            open_at = self._circuit_open_at.get(key)
            if open_at is None:
                return False
            elapsed = (datetime.now() - open_at).total_seconds()
            if elapsed >= self._circuit_reset_seconds:
                # Auto-reset
                del self._circuit_open_at[key]
                self._failures[key] = 0
                logger.info(
                    f"Circuit breaker auto-reset for {fetcher_name}/{operation}"
                )
                return False
            return True

    def _record_failure(self, fetcher_name: str, operation: str) -> None:
        """Increment failure counter; open circuit if threshold reached."""
        key = (fetcher_name, operation)
        with self._lock:
            self._failures[key] = self._failures.get(key, 0) + 1
            if self._failures[key] >= self._circuit_open_threshold:
                if key not in self._circuit_open_at:
                    self._circuit_open_at[key] = datetime.now()
                    logger.warning(
                        f"Circuit breaker opened for {fetcher_name}/{operation} "
                        f"after {self._failures[key]} consecutive failures"
                    )

    def _record_success(self, fetcher_name: str, operation: str) -> None:
        """Reset failure counter on success."""
        key = (fetcher_name, operation)
        with self._lock:
            self._failures[key] = 0
            self._circuit_open_at.pop(key, None)

    def get_ohlcv(self, code: str, days: int = 60) -> list:
        """Fetch OHLCV bars, trying fetchers in order with circuit-breaker logic.

        Returns:
            list[OHLCVBar]

        Raises:
            DataFetchError: all fetchers failed or all circuits are open
        """
        last_error: Optional[Exception] = None
        all_skipped = True

        for fetcher in self._fetchers:
            if self._is_circuit_open(fetcher.name, "ohlcv"):
                logger.debug(
                    f"Skipping {fetcher.name}/ohlcv — circuit open"
                )
                continue

            all_skipped = False
            try:
                bars = fetcher.fetch_ohlcv(code, days)
                self._record_success(fetcher.name, "ohlcv")
                if len(bars) < self._min_bars:
                    raise InsufficientDataError(
                        f"{fetcher.name} returned {len(bars)} bars, "
                        f"minimum required is {self._min_bars}"
                    )
                return bars
            except (NoDataError, UnsupportedCodeError) as e:
                # Soft error — skip silently, no circuit increment
                logger.debug(f"{fetcher.name}/ohlcv soft-skip for {code}: {e}")
                last_error = e
            except ProviderError as e:
                logger.warning(f"{fetcher.name}/ohlcv provider error for {code}: {e}")
                self._record_failure(fetcher.name, "ohlcv")
                last_error = e
            except InsufficientDataError as e:
                logger.warning(str(e))
                last_error = e
            except Exception as e:
                logger.error(
                    f"Unexpected error in {fetcher.name}/ohlcv for {code}:\n"
                    + traceback.format_exc()
                )
                last_error = e

        if all_skipped:
            raise DataFetchError(
                f"All fetchers have open circuits for ohlcv/{code}"
            )
        raise DataFetchError(
            f"All fetchers failed for ohlcv/{code}. Last error: {last_error}"
        )

    def get_realtime_quote(self, code: str) -> RealtimeQuote:
        """Fetch realtime quote, trying fetchers in order with circuit-breaker logic.

        Returns:
            RealtimeQuote

        Raises:
            DataFetchError: all fetchers failed or all circuits are open
        """
        last_error: Optional[Exception] = None
        all_skipped = True

        for fetcher in self._fetchers:
            if self._is_circuit_open(fetcher.name, "realtime"):
                logger.debug(
                    f"Skipping {fetcher.name}/realtime — circuit open"
                )
                continue

            all_skipped = False
            try:
                quote = fetcher.fetch_realtime(code)
                self._record_success(fetcher.name, "realtime")
                return quote
            except (NoDataError, UnsupportedCodeError) as e:
                logger.debug(f"{fetcher.name}/realtime soft-skip for {code}: {e}")
                last_error = e
            except ProviderError as e:
                logger.warning(f"{fetcher.name}/realtime provider error for {code}: {e}")
                self._record_failure(fetcher.name, "realtime")
                last_error = e
            except Exception as e:
                logger.error(
                    f"Unexpected error in {fetcher.name}/realtime for {code}:\n"
                    + traceback.format_exc()
                )
                last_error = e

        if all_skipped:
            raise DataFetchError(
                f"All fetchers have open circuits for realtime/{code}"
            )
        raise DataFetchError(
            f"All fetchers failed for realtime/{code}. Last error: {last_error}"
        )
