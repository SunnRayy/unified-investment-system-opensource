"""Hermetic unit tests for src.services.feeds.feed_manager.

No network, no database, no filesystem I/O.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.services.feeds.feed_manager import FeedManager, FeedResult, FeedSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_THRESHOLD = timedelta(hours=24)


_SENTINEL = object()


def _make_spec(
    feed_id: str = "test_feed",
    fallback_chain=_SENTINEL,
    parser=None,
    staleness_threshold: timedelta = _THRESHOLD,
) -> FeedSpec:
    if fallback_chain is _SENTINEL:
        fallback_chain = [lambda: 42.0]
    return FeedSpec(
        feed_id=feed_id,
        unit="ratio",
        source="primary_src",
        update_frequency="daily",
        staleness_threshold=staleness_threshold,
        fallback_chain=fallback_chain,
        parser=parser,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_stores_spec(self):
        mgr = FeedManager()
        spec = _make_spec("feed_a")
        mgr.register(spec)
        assert "feed_a" in mgr.specs()
        assert mgr.specs()["feed_a"] is spec

    def test_register_multiple_specs(self):
        mgr = FeedManager()
        mgr.register(_make_spec("feed_a"))
        mgr.register(_make_spec("feed_b"))
        assert set(mgr.specs().keys()) == {"feed_a", "feed_b"}

    def test_register_dedupe_last_wins(self, caplog):
        """Registering the same feed_id twice: last spec wins, a warning is emitted."""
        mgr = FeedManager()
        spec_first = _make_spec("dup_feed", fallback_chain=[lambda: 1.0])
        spec_last = _make_spec("dup_feed", fallback_chain=[lambda: 99.0])

        import logging
        with caplog.at_level(logging.WARNING, logger="src.services.feeds.feed_manager"):
            mgr.register(spec_first)
            mgr.register(spec_last)

        # Last wins
        assert mgr.specs()["dup_feed"] is spec_last
        # Warning emitted
        assert any("overriding" in record.message.lower() for record in caplog.records)

    def test_specs_returns_copy(self):
        """Mutating the returned dict must not affect the manager's internal state."""
        mgr = FeedManager()
        mgr.register(_make_spec("feed_a"))
        copy = mgr.specs()
        copy["injected"] = _make_spec("injected")
        assert "injected" not in mgr.specs()


# ---------------------------------------------------------------------------
# Primary-success tests
# ---------------------------------------------------------------------------


class TestPrimarySuccess:
    def test_first_callable_used_when_returns_value(self):
        """Step 0 returns a value → used directly, not stale, source_used reflects step 0."""
        mgr = FeedManager()
        mgr.register(_make_spec("f1", fallback_chain=[lambda: 3.14]))

        result = mgr.get("f1")

        assert isinstance(result, FeedResult)
        assert result.feed_id == "f1"
        assert result.value == pytest.approx(3.14)
        assert result.is_stale is False
        assert result.updated_at is not None
        # Step 0 uses the spec's source label
        assert result.source_used == "primary_src"
        assert result.error is None

    def test_result_updated_at_is_close_to_now(self):
        """updated_at must be very recent (within 1 second) for a live fetch."""
        before = _utc_now()
        mgr = FeedManager()
        mgr.register(_make_spec("f2", fallback_chain=[lambda: 7.0]))
        result = mgr.get("f2")
        after = _utc_now()

        assert before <= result.updated_at <= after

    def test_explicit_now_used_as_updated_at(self):
        """Passing *now* overrides the internal datetime.now() call."""
        fixed_now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        mgr = FeedManager()
        mgr.register(_make_spec("f3", fallback_chain=[lambda: 5.0]))
        result = mgr.get("f3", now=fixed_now)

        assert result.updated_at == fixed_now


# ---------------------------------------------------------------------------
# Fallback chain tests
# ---------------------------------------------------------------------------


class TestFallbackChain:
    def test_first_raises_second_returns_value(self):
        """First callable raises → caught, second callable used."""
        def bad_fetcher():
            raise RuntimeError("network error")

        def good_fetcher():
            return 99.9

        mgr = FeedManager()
        mgr.register(_make_spec("fb1", fallback_chain=[bad_fetcher, good_fetcher]))

        result = mgr.get("fb1")

        assert result.value == pytest.approx(99.9)
        assert result.is_stale is False
        assert result.source_used == "step1"

    def test_first_returns_none_second_returns_value(self):
        """First callable returns None → miss, second callable used."""
        mgr = FeedManager()
        mgr.register(
            _make_spec("fb2", fallback_chain=[lambda: None, lambda: 55.5])
        )

        result = mgr.get("fb2")

        assert result.value == pytest.approx(55.5)
        assert result.source_used == "step1"

    def test_chain_stops_at_first_success(self):
        """Only the first successful callable is used; subsequent ones are not called."""
        third = MagicMock(return_value=999.0)
        mgr = FeedManager()
        mgr.register(
            _make_spec(
                "fb3",
                fallback_chain=[lambda: None, lambda: 1.0, third],
            )
        )

        result = mgr.get("fb3")

        assert result.value == pytest.approx(1.0)
        third.assert_not_called()

    def test_multiple_raises_before_success(self):
        """Several steps raise before a successful one."""
        def raise_a():
            raise ValueError("a")

        def raise_b():
            raise ConnectionError("b")

        mgr = FeedManager()
        mgr.register(
            _make_spec("fb4", fallback_chain=[raise_a, raise_b, lambda: 7.7])
        )

        result = mgr.get("fb4")

        assert result.value == pytest.approx(7.7)
        assert result.source_used == "step2"


# ---------------------------------------------------------------------------
# All-fail WITH prior_value
# ---------------------------------------------------------------------------


class TestAllFailWithPrior:
    def test_returns_prior_value_as_last_good(self):
        """All steps fail + prior_value present → return last-good, source_used='last_good'."""
        mgr = FeedManager()
        mgr.register(_make_spec("lf1", fallback_chain=[lambda: None]))

        prior_ts = _utc_now()  # recent → not stale
        result = mgr.get("lf1", prior_value=42.0, prior_updated_at=prior_ts)

        assert result.value == pytest.approx(42.0)
        assert result.source_used == "last_good"
        assert result.is_stale is False

    def test_prior_value_stale_when_old(self):
        """Prior value older than staleness_threshold → is_stale=True."""
        mgr = FeedManager()
        mgr.register(
            _make_spec("lf2", fallback_chain=[lambda: None], staleness_threshold=timedelta(hours=1))
        )

        old_ts = _utc_now() - timedelta(hours=2)  # 2 h old, threshold 1 h → stale
        result = mgr.get("lf2", prior_value=10.0, prior_updated_at=old_ts)

        assert result.value == pytest.approx(10.0)
        assert result.is_stale is True
        assert result.source_used == "last_good"

    def test_prior_value_not_stale_when_recent(self):
        """Prior value within staleness_threshold → is_stale=False."""
        mgr = FeedManager()
        mgr.register(
            _make_spec("lf3", fallback_chain=[lambda: None], staleness_threshold=timedelta(hours=2))
        )

        recent_ts = _utc_now() - timedelta(minutes=30)  # 30 min old, threshold 2 h → not stale
        result = mgr.get("lf3", prior_value=5.0, prior_updated_at=recent_ts)

        assert result.is_stale is False

    def test_prior_updated_at_none_is_stale(self):
        """Prior value present but prior_updated_at=None → is_stale=True."""
        mgr = FeedManager()
        mgr.register(_make_spec("lf4", fallback_chain=[lambda: None]))

        result = mgr.get("lf4", prior_value=3.0, prior_updated_at=None)

        assert result.value == pytest.approx(3.0)
        assert result.is_stale is True

    def test_all_raise_with_prior(self):
        """All steps raise, prior_value present → returns last-good."""
        def explode():
            raise RuntimeError("boom")

        mgr = FeedManager()
        mgr.register(_make_spec("lf5", fallback_chain=[explode, explode]))

        prior_ts = _utc_now()
        result = mgr.get("lf5", prior_value=88.0, prior_updated_at=prior_ts)

        assert result.value == pytest.approx(88.0)
        assert result.source_used == "last_good"


# ---------------------------------------------------------------------------
# All-fail WITHOUT prior_value
# ---------------------------------------------------------------------------


class TestAllFailNoPrior:
    def test_returns_none_value_and_error(self):
        """All steps fail and no prior_value → value=None, is_stale=True, error set."""
        mgr = FeedManager()
        mgr.register(_make_spec("nf1", fallback_chain=[lambda: None]))

        result = mgr.get("nf1")

        assert result.value is None
        assert result.is_stale is True
        assert result.updated_at is None
        assert result.error == "all_sources_failed"
        assert result.source_used is None

    def test_all_raise_no_prior(self):
        """All steps raise and no prior_value → error='all_sources_failed'."""
        def explode():
            raise RuntimeError("boom")

        mgr = FeedManager()
        mgr.register(_make_spec("nf2", fallback_chain=[explode]))

        result = mgr.get("nf2")

        assert result.value is None
        assert result.error == "all_sources_failed"
        assert result.is_stale is True

    def test_empty_chain_no_prior(self):
        """Empty fallback_chain with no prior → all_sources_failed."""
        mgr = FeedManager()
        mgr.register(_make_spec("nf3", fallback_chain=[]))

        result = mgr.get("nf3")

        assert result.value is None
        assert result.error == "all_sources_failed"


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParser:
    def test_parser_applied_to_raw_value(self):
        """Parser transforms the raw value returned by the fetcher."""
        mgr = FeedManager()
        mgr.register(
            _make_spec(
                "p1",
                fallback_chain=[lambda: "  12.5  "],
                parser=lambda v: float(v.strip()),
            )
        )

        result = mgr.get("p1")

        assert result.value == pytest.approx(12.5)

    def test_parser_returning_none_treated_as_miss_then_fallback(self):
        """Parser returning None → treated as miss; chain continues to next step."""
        mgr = FeedManager()
        mgr.register(
            _make_spec(
                "p2",
                fallback_chain=[lambda: "bad", lambda: 7.0],
                parser=lambda v: None if v == "bad" else float(v),
            )
        )

        result = mgr.get("p2")

        # Second step returns 7.0 → parser converts it (not "bad")
        assert result.value == pytest.approx(7.0)
        assert result.source_used == "step1"

    def test_parser_raising_treats_step_as_miss(self):
        """Parser that raises on a value → treat that step as a miss."""
        mgr = FeedManager()
        mgr.register(
            _make_spec(
                "p3",
                fallback_chain=[lambda: "not-a-number", lambda: 3.0],
                parser=float,  # float("not-a-number") raises ValueError
            )
        )

        result = mgr.get("p3")

        # First step fails parser → second step used
        assert result.value == pytest.approx(3.0)

    def test_parser_not_called_when_all_miss(self):
        """Parser is irrelevant when all steps return None."""
        parser_mock = MagicMock(return_value=99.0)
        mgr = FeedManager()
        mgr.register(
            _make_spec(
                "p4",
                fallback_chain=[lambda: None],
                parser=parser_mock,
            )
        )

        result = mgr.get("p4")

        parser_mock.assert_not_called()
        assert result.value is None


# ---------------------------------------------------------------------------
# Staleness threshold boundary tests
# ---------------------------------------------------------------------------


class TestStalenessBoundary:
    def test_value_just_over_threshold_is_stale(self):
        """Age just over staleness_threshold → is_stale=True on last-good."""
        threshold = timedelta(hours=6)
        mgr = FeedManager()
        mgr.register(
            _make_spec("sb1", fallback_chain=[lambda: None], staleness_threshold=threshold)
        )

        fixed_now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        # 6 hours and 1 second old — just over the threshold
        prior_ts = fixed_now - threshold - timedelta(seconds=1)

        result = mgr.get("sb1", prior_value=1.0, prior_updated_at=prior_ts, now=fixed_now)

        assert result.is_stale is True

    def test_value_just_under_threshold_is_not_stale(self):
        """Age just under staleness_threshold → is_stale=False on last-good."""
        threshold = timedelta(hours=6)
        mgr = FeedManager()
        mgr.register(
            _make_spec("sb2", fallback_chain=[lambda: None], staleness_threshold=threshold)
        )

        fixed_now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        # 1 second under the threshold
        prior_ts = fixed_now - threshold + timedelta(seconds=1)

        result = mgr.get("sb2", prior_value=1.0, prior_updated_at=prior_ts, now=fixed_now)

        assert result.is_stale is False

    def test_naive_prior_updated_at_treated_as_utc(self):
        """Naive prior_updated_at (no tzinfo) is coerced to UTC before comparison."""
        threshold = timedelta(hours=1)
        mgr = FeedManager()
        mgr.register(
            _make_spec("sb3", fallback_chain=[lambda: None], staleness_threshold=threshold)
        )

        fixed_now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Naive datetime — 30 min ago → should NOT be stale
        naive_ts = datetime(2026, 6, 1, 11, 30, 0)  # no tzinfo

        result = mgr.get("sb3", prior_value=5.0, prior_updated_at=naive_ts, now=fixed_now)

        assert result.is_stale is False

    def test_live_fetch_with_now_not_stale(self):
        """A live fetch sets updated_at=now, which is never older than threshold (same instant)."""
        mgr = FeedManager()
        mgr.register(
            _make_spec("sb4", fallback_chain=[lambda: 1.0], staleness_threshold=timedelta(seconds=0))
        )

        # timedelta(0) threshold: age > 0 ⇒ stale. But updated_at == now ⇒ age == 0 ⇒ NOT stale.
        fixed_now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = mgr.get("sb4", now=fixed_now)

        # updated_at == now → (now - now) = 0 which is NOT > timedelta(0) → not stale
        assert result.is_stale is False


# ---------------------------------------------------------------------------
# Unknown feed_id
# ---------------------------------------------------------------------------


class TestUnknownFeed:
    def test_unknown_feed_returns_error_result(self):
        """Requesting an unregistered feed_id returns a graceful error FeedResult."""
        mgr = FeedManager()

        result = mgr.get("no_such_feed")

        assert result.feed_id == "no_such_feed"
        assert result.value is None
        assert result.is_stale is True
        assert result.error is not None
        assert "no_such_feed" in result.error
