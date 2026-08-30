# ADR-009: Sentiment / External-Feed Integration (Buffett Indicator, CAPE, etc.)

**Date:** 2026-05-29 (drafted 2026-06-20, accepted 2026-06-20)
**Status:** Accepted

---

## Context

Huinsight uses several external sentiment and valuation feeds: Buffett indicator (total
market cap / GDP), Multpl S&P 500 and Nasdaq100 CAPE, Baidu Finance HK ETF PE/PB,
and yfinance for US equity and bond yields. Each feed has different units, update
frequencies, staleness thresholds, and fallback behaviour.

Pass 1 (L8) and AGENTS.md Rule 22 added per-call unit documentation requirements.
An architecture decision is needed to formalize the feed registry, staleness policy,
and fallback chain across all sentiment feeds — currently each fetcher implements
these independently.

## Decision (proposed)

Introduce a **declarative feed registry** and a thin `FeedManager` that owns
staleness and fallback uniformly, instead of each fetcher re-implementing them.

- Each feed is described once by a `FeedSpec`: `id`, `unit`, `source`,
  `update_frequency`, `staleness_threshold`, ordered `fallback_chain`, and the
  parser entry point. This makes Rule 22's unit/staleness/fallback discipline
  *data*, not scattered code.
- `FeedManager.get(feed_id)` resolves the value through the fallback chain,
  stamps `updated_at`, and sets the staleness sentinels already present on
  `market_sentiment_cache` (`is_stale`, `last_refresh_attempt`, `error_detail`
  from Migration 14) — so the dashboard's per-card "as of" / stale badges read
  from one consistent source.
- Individual fetchers stay as pure value-producers behind their `FeedSpec`; the
  manager handles caching, staleness, and error surfacing.

## Options considered

1. **Status quo** (per-fetcher staleness/fallback) — works, but each new feed
   re-derives the same logic and the staleness contract drifts. Rejected.
2. **Declarative registry + FeedManager (chosen)** — one place to add or tune a
   feed; consistent staleness UI; directly satisfies Rule 22. Moderate refactor.
3. **Third-party market-data aggregator** — rejected: recurring cost, vendor
   lock-in, and most needed series are already free via the current sources.

## Consequences

- Adding or retuning a feed becomes a registry edit, not a code path.
- Uniform staleness/fallback removes a class of silent-stale bugs (cf. Issue #10
  VIX lag) and gives every card the same freshness semantics.
- One-time refactor to register the existing ~half-dozen feeders behind specs.
- No change to the underlying data sources or the sync pipeline.

---

## References

- `src/services/valuation/fetchers/` — per-feed fetcher modules
- `src/market_data/` — market data scrapers
- AGENTS.md Rule 22 (External-Feed Unit/Staleness/Fallback Discipline)
- Deferred architecture items are tracked internally
