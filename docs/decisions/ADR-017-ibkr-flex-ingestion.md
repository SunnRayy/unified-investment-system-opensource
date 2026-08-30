# ADR-017: IBKR Flex Query Ingestion — First-Class Reader + Phased Fetch

**Date:** 2026-06-14
**Status:** Accepted — IBKR reader live in V6.4.0 (C1); Flex auto-fetch scheduling deferred to C4
**Deciders:** Ray (Owner), Claude Code (Architect)

---

## Context

Interactive Brokers is a permanent second US-equity broker (ADR-016). Huinsight must ingest IBKR
positions, trades, and cash balances on a recurring basis with the same data quality and
freshness guarantees as the existing 6 readers.

The owner's first attempt used an IBKR **performance/analytics** statement. That statement
format was evaluated and rejected as a data contract: it lacks cost basis, has no clean Trades
section, and cannot be cleanly parsed into the per-asset, per-transaction schema Huinsight requires.

The correct data contract is an IBKR **Flex Query** — a fully user-customisable statement
format (CSV or XML) downloaded from the IBKR reporting portal or fetched via the Flex Web
Service REST API. A Flex Query can be configured to include exactly the sections and fields
Huinsight requires (Open Positions with cost basis, Trades, Cash Report, Account Information) and
nothing else.

Two fetch mechanisms exist: manual download (immediately available, no secrets required) and
the Flex Web Service token API (automated, requires a read-only token and a saved query ID).
The owner's decision is to start with manual download and layer the API fetch later without
changing the reader or sync path.

Since IBKR is a permanent data source — not an ad-hoc approved import — it belongs in the
same reader engine, registry, freshness monitoring, and integrity checks as the other 6
sources. The adapter framework (ADR-004) is not appropriate here.

---

## Decision

**IBKR is a first-class config-driven reader** registered in `config/readers/ibkr.yaml` with
`source_system: Broker_IBKR`. It participates fully in `READER_SOURCES`, freshness reporting,
integrity checks, and the `reconcile_readers.py` harness from the moment it is enabled.

**Data contract: IBKR Flex Query**

The owner must define and save an IBKR Flex Query (e.g. named "UIS_Activity") in the IBKR
reporting portal, configured to export the following sections and fields:

| Section | Required Fields |
|---------|----------------|
| Open Positions | Symbol, AssetClass, Quantity, CostBasisPrice (or CostBasisMoney), MarkPrice, PositionValue, Currency, AccountId, ReportDate |
| Trades | Symbol, AssetClass, DateTime, Quantity, TradePrice, Buy/Sell, IBCommission, Currency, AccountId |
| Cash Report | Currency, EndingCash (StartingCash if available) |
| Account Information | AccountId, BaseCurrency |

> **Must-have sections:** Open Positions (qty, mark price, currency, accountId), Trades
> (date, qty, price, buy/sell), Cash Report, Account Info. The `CostBasis` field from Open
> Positions is captured as a **reconciliation cross-check only** — Huinsight recomputes lifetime
> FIFO cost basis from the merged buy/sell transaction history and does not use IBKR's reported
> cost basis as the source of truth (which may be delayed 2–4 weeks after an ACAT transfer).
>
> **ACAT transfers** appear in a Deposits & Withdrawals section — NOT in the Trades section.
> The IBKR reader must classify ACAT rows as `transfer_in`/`transfer_out` (not buy/sell) so
> they are excluded from the FIFO ledger and do not trigger false realization events.

The file format may be CSV or XML; the engine plugin handles both. A Flex Query file uses a
multi-section layout with a `SectionName` column and alternating `Header` / `Data` rows per
section — the reader plugin parses this structure before handing off to the config engine.

**Phased fetch (owner decision — locked)**

- **Phase 1 (C1, now):** Owner manually downloads the Flex Query output from the IBKR
  reporting portal and drops it in the Finance directory. Huinsight reads it like any file source.
  No secrets, no network calls, no scheduler. The reader runs immediately.

- **Phase 2 (C4, later):** `src/fetchers/ibkr_flex.py` and `scripts/fetch_ibkr.py`
  implement the two-step Flex Web Service protocol:
  1. `SendRequest` with `token` + `queryId` → IBKR returns a `ReferenceCode`.
  2. `GetStatement` with the reference code → IBKR returns the statement XML/CSV.
  Retry/backoff is required (IBKR may return a "not ready" status on the first poll).
  The fetcher writes a timestamped file to the Finance directory. Sync reads the file
  exactly as in Phase 1 — the fetch and sync paths are fully decoupled.

**Fetch is decoupled from sync — always.** A fetch failure produces a stale-file warning
in the freshness panel, never a blocked sync. The existing stale-reader-shadow mechanism
handles old files. This keeps the deployed Cloud Run instance offline-capable (sync does
not require a live network call to IBKR).

**Secrets management (Phase 2):** `IBKR_FLEX_TOKEN` and `IBKR_FLEX_QUERY_ID` are stored in
the gitignored `.env` file locally and as Cloud Run environment secrets in production. The
Flex token is a **read-only reporting credential** — it cannot place or modify orders. This
is a key safety property: the token being compromised cannot cause financial harm.

**Asset ID normalisation**

IBKR Open Positions use IBKR symbol notation (e.g. `AAPL`, `BRK B`, `VOO`). Asset IDs are
produced via `src/identity/normalizer.py` using the same rules as Schwab:

- Stocks → `US_STK_<SYMBOL>` (e.g. `US_STK_AAPL`)
- ETFs → `US_ETF_<SYMBOL>` (e.g. `US_ETF_VOO`)
- STK vs ETF classification: primary lookup via `asset_registry` / classification tables
  (populated by Schwab reader); heuristic fallback on `AssetClass` field from IBKR
- BRK-B variant normalisation is reused (`BRK B` → `US_STK_BRKB`)
- Cash → `CASH_<CCY>` (e.g. `CASH_USD`, `CASH_EUR`)

Trades produce buy/sell transaction rows with `account = IBKR_<accountId>`. Per ADR-016 §6,
FIFO cost basis is computed over a single lifetime ledger merged across all co-authority sources
per `asset_id`; the `account` column tags rows for per-broker display and attribution but does
not partition the FIFO lot ledger.

**Reader YAML and engine plugin**

`config/readers/ibkr.yaml` carries the standard `identity:` block (source_key, display_label,
source_system, asset_prefixes, allowed_extensions, category, validator name) and a `parsing:`
block with a `format: flex_csv` or `format: flex_xml` tag. A small section-parsing plugin in
the reader engine handles the multi-section Flex file layout before the standard column-mapping
and asset-ID-template path runs. Where possible, the plugin reuses the Schwab CSV/multi-file
engine path (which handles multiple logical tables in a single file).

**Non-authoritative during C1**

The IBKR reader runs as a parallel (non-authority) source from C1 until C3. It populates
the holdings table with its rows, but the authority resolver continues to give full authority
to Schwab for shared assets during C1–C2. Co-authority semantics (ADR-016) are activated in
C3 when the resolver and aggregator changes land. This staging ensures no shadowing of Schwab
data before the dual-broker tests are green.

---

## Consequences

**Positive:**
- The data contract (Flex Query) is fully owner-controlled — fields, date range, and format
  are defined in the IBKR portal, not hardcoded in Huinsight.
- Phase 1 requires zero new secrets or infrastructure; the owner can start importing IBKR
  data immediately after C1 ships.
- Fetch decoupled from sync means a stale or missing IBKR file never blocks a full sync;
  the system degrades gracefully to the last available file.
- The Flex token is read-only — leaking it cannot result in unauthorised trades.
- Registering IBKR as a config-engine reader costs only one YAML file (plus the section-
  parsing plugin); the registry derives all downstream touchpoints automatically (ADR-014).
- Phase 2 (auto-fetch) is additive — no reader or sync changes, just a fetcher and a
  scheduler script.

**Negative / Trade-offs:**
- The Flex Query multi-section file format requires a dedicated engine plugin (not a straight
  application of the existing Excel or flat-CSV paths). This is a modest implementation cost
  but creates a format-specific branch in the reader engine.
- Manual download (Phase 1) is a recurring owner action — no automation until Phase 2 ships.
  If the owner forgets to download, IBKR data goes stale and the freshness panel will flag it.
- The two-step Flex Web Service protocol (Phase 2) requires retry/backoff logic and a
  stateful reference-code cache; it is more complex than a simple HTTP GET.
- IBKR MCP tools (available to the agent at dev time) are not usable by the deployed Cloud
  Run app at sync time — they cannot substitute for the Flex API in production.

**Neutral / Future work:**
- Phase 2 fetch scheduling: `launchd` on macOS for local development; a Cloud Scheduler
  job or cron-triggered Cloud Run job for the deployed instance. Out of C4 merge scope.
- If IBKR adds a native REST API for positions/trades that does not require a persistent
  gateway, this ADR should be revisited. The Flex API is the recommended approach as of 2026.
- UI DataSourceManager (C5) will surface `Broker_IBKR` alongside the other 6 sources once
  the source management redesign is complete. C1–C4 does not depend on C5.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| IBKR TWS API or Client Portal API | Requires a persistent TWS/gateway process or an authenticated Client Portal session with 2FA. Not Cloud-Run-feasible; adds operational complexity. |
| IBKR performance/analytics statement (evaluated) | Lacks cost basis and a clean Trades section. Cannot be parsed into Huinsight's per-asset, per-transaction schema. Rejected after hands-on evaluation of the owner's downloaded statement. |
| IBKR MCP tool for ingestion | MCP tools are agent-side only. Not callable by the deployed app at sync time. Useful only for dev-time ad-hoc reconciliation; cannot serve as the production data pipeline. |
| Import adapter framework (ADR-004) | Adapters are for ad-hoc approved imports, not permanent sources. IBKR is permanent and must participate in freshness monitoring, integrity checks, and the reconciliation harness. |
| Broker-specific fetcher that directly writes to the DB | Breaks the file-based reader contract. Fetch decoupled from sync is a deliberate design: it keeps the sync path testable with static fixtures and makes the system offline-capable. |

---

## References

- ADR-016: `docs/decisions/ADR-016-co-authority-multi-broker.md` — authority model IBKR
  participates in; must be approved before C3 implementation
- ADR-014: `docs/decisions/ADR-014-config-driven-reader-engine.md` — config-driven reader
  engine and registry that IBKR plugs into
- ADR-004: `docs/decisions/ADR-004-import-adapter-authority.md` — adapter framework (rejected
  for IBKR; cited for contrast)
- Program plan: internal implementation notes (§C1, §C2, §C4)
- Workstream C plan: internal implementation notes
- `src/identity/normalizer.py` — asset ID normalisation (to be extended for IBKR symbols)
- `config/readers/` — existing YAML reader configs (structural reference for `ibkr.yaml`)
- IBKR Flex Web Service documentation: https://www.interactivebrokers.com/en/software/am/am/reports/activityflexqueries.htm
