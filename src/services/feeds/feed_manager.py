"""Feed registry — pure logic, no DB, no network.

Provides a declarative feed-staleness / fallback core that sentiment and
valuation paths can delegate to (ADR-009 / WS2 foundation).

Resolution semantics mirror the last-good-value preservation in
``src.api.routes.sentiment.refresh_sentiment``:
  - A fetch returning None keeps the prior good value and marks is_stale=True.
  - Only a successful fetch overwrites the prior value.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------


@dataclass
class FeedResult:
    """The resolved outcome for a single feed lookup."""

    feed_id: str
    value: Optional[Any]
    is_stale: bool
    updated_at: Optional[datetime]  # when the returned value was actually produced
    source_used: Optional[str]      # which fallback step produced it (or 'last_good')
    error: Optional[str] = None


@dataclass
class FeedSpec:
    """Declarative specification for a single data feed."""

    feed_id: str
    unit: str                                          # e.g. 'ratio', 'pct', 'index_pe'
    source: str                                        # human label of the primary source
    update_frequency: str                              # e.g. 'daily', 'monthly' (documentation)
    staleness_threshold: timedelta                     # value older than this ⇒ is_stale
    fallback_chain: list[Callable[[], Optional[Any]]] # ordered fetchers; each returns value or None/raises
    parser: Optional[Callable[[Any], Any]] = None     # optional post-process of the raw value


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------


def _make_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Return a timezone-aware version of *dt* (UTC if naive), or None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _is_stale(updated_at: Optional[datetime], threshold: timedelta, now: datetime) -> bool:
    """Return True when *updated_at* is older than *threshold* relative to *now*.

    Both *updated_at* and *now* are normalised to UTC before comparison.
    A missing *updated_at* is always considered stale.
    """
    if updated_at is None:
        return True
    aware_updated = _make_aware(updated_at)
    aware_now = _make_aware(now)
    return (aware_now - aware_updated) > threshold  # type: ignore[operator]


# ---------------------------------------------------------------------------
# FeedManager
# ---------------------------------------------------------------------------


class FeedManager:
    """Registry of feed specs with a uniform resolution / fallback API."""

    def __init__(self) -> None:
        self._specs: dict[str, FeedSpec] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, spec: FeedSpec) -> None:
        """Register a feed spec.

        If a spec with the same ``feed_id`` already exists it is replaced and
        a warning is emitted (last wins).
        """
        if spec.feed_id in self._specs:
            logger.warning(
                "FeedManager: overriding already-registered feed '%s'", spec.feed_id
            )
        self._specs[spec.feed_id] = spec

    def specs(self) -> dict[str, FeedSpec]:
        """Return a shallow copy of the registered spec mapping."""
        return dict(self._specs)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def get(
        self,
        feed_id: str,
        *,
        prior_value: Optional[Any] = None,
        prior_updated_at: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> FeedResult:
        """Resolve a feed through its fallback chain.

        Resolution order
        ----------------
        1. Try each callable in ``fallback_chain`` in order.
           - If the callable returns a non-None value it is passed through
             ``parser`` (if configured).  The first non-None result wins.
           - If the callable raises, the exception is caught and logged; the
             chain continues to the next step.
           - If the callable returns None the chain continues.
        2. If ALL steps miss:
           - If *prior_value* is not None, return it as the last-good value.
             ``is_stale`` is computed from *prior_updated_at* vs the spec's
             ``staleness_threshold``; ``source_used`` is ``'last_good'``.
           - Otherwise return
             ``FeedResult(value=None, is_stale=True, updated_at=None,
             error='all_sources_failed')``.
        3. Even on a fresh-fetch success, ``is_stale`` is set to True when the
           returned ``updated_at`` (``now`` for a live fetch) is older than
           ``staleness_threshold`` — relevant when a source embeds its own
           timestamp.

        This method never raises; it always returns a :class:`FeedResult`.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        if feed_id not in self._specs:
            # Unknown feed — fail gracefully.
            logger.warning("FeedManager.get: unknown feed_id '%s'", feed_id)
            return FeedResult(
                feed_id=feed_id,
                value=None,
                is_stale=True,
                updated_at=None,
                source_used=None,
                error=f"unknown_feed_id:{feed_id}",
            )

        spec = self._specs[feed_id]

        # ------------------------------------------------------------------
        # Walk the fallback chain
        # ------------------------------------------------------------------
        for i, fetcher in enumerate(spec.fallback_chain):
            step_label = f"step{i}" if i > 0 else spec.source
            try:
                raw = fetcher()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "FeedManager: feed '%s' step %d (%s) raised: %s",
                    feed_id,
                    i,
                    step_label,
                    exc,
                )
                continue  # treat as a miss

            if raw is None:
                continue  # explicit miss; next step

            # Apply optional parser
            if spec.parser is not None:
                try:
                    value = spec.parser(raw)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "FeedManager: feed '%s' parser raised on step %d value %r: %s",
                        feed_id,
                        i,
                        raw,
                        exc,
                    )
                    continue  # parser failure → treat step as a miss

            else:
                value = raw

            if value is None:
                continue  # parser returned None → miss

            # Fresh fetch succeeded.
            result_updated_at = now
            stale = _is_stale(result_updated_at, spec.staleness_threshold, now)
            return FeedResult(
                feed_id=feed_id,
                value=value,
                is_stale=stale,
                updated_at=result_updated_at,
                source_used=step_label,
            )

        # ------------------------------------------------------------------
        # All steps missed
        # ------------------------------------------------------------------
        if prior_value is not None:
            stale = _is_stale(prior_updated_at, spec.staleness_threshold, now)
            return FeedResult(
                feed_id=feed_id,
                value=prior_value,
                is_stale=stale,
                updated_at=prior_updated_at,
                source_used="last_good",
            )

        return FeedResult(
            feed_id=feed_id,
            value=None,
            is_stale=True,
            updated_at=None,
            source_used=None,
            error="all_sources_failed",
        )
