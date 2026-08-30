# ADR-016: Co-Authority Multi-Broker Resolution

**Date:** 2026-06-14
**Status:** Accepted — implemented and live in V6.4.0 (Workstream C, owner-verified 2026-06-17)
**Deciders:** Ray (Owner), Claude Code (Architect)

---

## Context

Huinsight currently resolves a single authoritative source per asset. `AuthorityResolver.resolve()`
returns the first matching rule from `config/source_authority.yaml` (lower priority number wins,
per ADR-013), and `HoldingsAggregator` shadows every row whose `source_system` is not that
single winner. This single-winner model was adequate when Schwab was the only US-equity broker.

Interactive Brokers joins Schwab as a **permanent second US-equity broker — both active
long-term**. Evidence of the co-authority complexity was visible before any code was written:
the owner's IBKR statement (2026-06-08) shows an ACAT transfer of SGOV (200 shares) from
Schwab to IBKR on that date, while Schwab retains the remaining SGOV position. The same asset
legitimately lives in two sources simultaneously as it transits between brokers. A single-winner
resolver would silently shadow one broker's holdings in full.

> **Note:** AAPL (`US_STK_AAPL`) was used as an illustrative placeholder in earlier drafts.
> The real co-authority case is SGOV, the actual asset held across both brokers after the
> partial ACAT transfer. **ID-convention correction (C1, 2026-06-15):** the live Schwab CSV
> classifies SGOV/VOO/IEF as *stock*, so they canonicalize to `US_STK_SGOV` / `US_STK_VOO` /
> `US_STK_IEF` — NOT `US_ETF_*` as earlier drafts of this ADR assumed. Co-authority requires a
> single shared `asset_id` per asset across brokers, so the IBKR reader matches Schwab's holdings
> canonical (`US_STK_*`). ETF-ness lives in `asset_registry.asset_class`, not the ID prefix.

The holdings table grain `(snapshot_date, asset_id, source_system)` already permits both
brokers to coexist in the database — only the resolver and aggregator enforce the single-winner
constraint today. The authority model must be generalised to a set-based "authority group"
before Workstream C implementation begins.

Two separate concerns arise with co-authority and must be handled distinctly:

1. **Current holdings (market value / net worth):** the two brokers' latest-per-source snapshots
   must be combined into ONE position per asset. Example: SGOV = Schwab remaining shares + IBKR
   200 shares. See **§ Correction (C3, 2026-06-15)** below for HOW — the original "summed
   naturally" assumption was wrong against the real query implementation.

2. **FIFO cost basis / lifetime P&L (transactions):** a single merged ledger per `asset_id`
   across all co-authority sources; transfer events are excluded. See §Decision point 6 for
   full detail.

---

## Decision

**Authority is broadened from a single string to a named set of sources (an authority group).**
Broker-agnostic asset IDs (`US_STK_SGOV`, `US_STK_VOO`, `CASH_USD`) are preserved across brokers.
(The C3 dual-authority rule must cover `US_STK_*` since that is where SGOV/VOO/IEF actually live;
the `US_ETF_*` rule remains for any genuinely ETF-classified symbols.)

> ### ⚠️ Correction (C3 implementation, 2026-06-15)
> **The claim that "analytics groups by `asset_id` and sums across all non-shadow rows naturally"
> was FALSE.** Investigation found ~35 production current-holdings queries (`data.py`,
> `context_generator.py`, performance/compass/operations/valuation/…) all do
> `SELECT asset_id, MAX(snapshot_date) … WHERE is_shadow=FALSE GROUP BY asset_id`, which picks the
> **single newest-dated source** per asset and never sums two brokers. With C1 data this UNDER-counts
> (SGOV showed only Schwab 453; IBKR 200 dropped — net worth 5,763,331 vs true ≈5,905,906).
>
> **Chosen mechanism (owner-approved): sync-time consolidation.** During sync, a new pipeline phase
> writes ONE merged `source_system='Consolidated'` holdings row per co-authority asset (qty/value
> summed across each source's latest, cost from the merged FIFO ledger, dated `today`) and sets
> `is_shadow=TRUE` on the contributing per-broker rows. Every existing `GROUP BY asset_id` query then
> returns the correct summed value with NO query changes. A scoped, deterministic zero-qty **tombstone**
> (co-authority broker sources only — not CN funds, to avoid QDII-lag false positives) runs first to
> drop fully-transferred holdings (Schwab's stale VOO/IEF) so consolidation only merges genuinely
> co-held assets. Per-broker holdings display (reading the shadowed broker rows / `account` / txns) is
> deferred to C5. Plan: internal implementation notes. The resolver-set, merged
> FIFO, and tombstone decisions below remain valid; the aggregator shadow-by-set is retained mainly as
> a same-date guard since consolidation does the real combination work.
>
> **Real-data validation (2026-06-15, an internal validation record):** the three
> net-worth figures (5,763,331 / 6,112,285 / 5,905,906) reproduce EXACTLY against the live DB; the
> co-authority set (SGOV partial, VOO/IEF full transfer, CASH_USD) and tombstone targets confirmed against
> the real Schwab/IBKR CSVs. Two correctness requirements were sharpened: (a) `select_transaction_sources()`
> must resolve co-authority via the **authority rule**, not the latest-holding source — otherwise IBKR
> VOO/IEF cost basis computes to $0 (the latest holding is IBKR, so Schwab's buy lots get dropped); (b) the
> merged ledger is correct only while BOTH ACAT legs stay non-realizing — guarded by an extended
> `consolidated_equals_sum` check (merged-FIFO-open-qty == surviving-holding-qty). Blast radius of the
> `Consolidated` synthetic source proved near-zero (registry-driven), smaller than this ADR budgeted.

**1. `config/source_authority.yaml` — list-form `authorities` key**

The `authority` field is extended to accept either a string (backward compatible, existing rules
unchanged) or a list:

```yaml
# New dual-authority rule for US equities
- pattern: "US_STK_*"
  priority: 8
  authorities: [Schwab_CSV, Broker_IBKR]

- pattern: "US_ETF_*"
  priority: 8
  authorities: [Schwab_CSV, Broker_IBKR]

- pattern: "CASH_USD"
  priority: 7
  authorities: [Schwab_CSV, Broker_IBKR]
```

String `authority:` rules continue to work without modification. The loader normalises both
forms to a Python `frozenset` internally.

**2. `AuthorityResolver` — `resolve_authorities()` returns a set**

A new method `resolve_authorities(asset_id, available_sources) -> frozenset[str]` replaces
the single-return `resolve()` on the co-authority path. `resolve()` is retained for
backward compatibility (single-authority rules return a one-element set via the same logic).
The resolution algorithm is unchanged: ascending priority, first matching rule wins — but
the winner is now a set of allowed sources rather than a single string.

**3. `HoldingsAggregator` — shadow by set membership**

`HoldingsAggregator` is updated to call `resolve_authorities()` and shadow only rows whose
`source_system` is NOT in the resolved authority set. Non-shadow rows each carry
`authority_source = source_system` (their own system name), preserving the existing pattern
that `authority_source` identifies which source "owns" that row. The `account` column (already
present on all holdings rows) carries broker identity for per-broker display and attribution,
but does NOT partition the FIFO ledger (see §6).

**4. Cash — per-source rows, summed in aggregation**

`CASH_USD` rows remain separate per `source_system` and are SUMMED during net-worth
aggregation (not de-duplicated). Explicit `CASH_<CCY>` rules (e.g. `CASH_EUR`, `CASH_GBP`)
are added before the priority-9 catch-all to handle IBKR multi-currency cash correctly.
The catch-all (`priority: 9, authority: Financial_Summary_Excel`) is unchanged.

**5. Integrity check #6 — redefined**

`shadow_mutual_exclusion` (check #6) is redefined from "every non-shadow row comes from the
single resolved authority" to "every non-shadow row's `source_system` is a member of the
resolved authority set for that asset." Check #9 (`authority_source_non_null`) is unchanged.

**6. FIFO cost basis and lifetime P&L — single merged ledger per `asset_id`**

FIFO cost basis and P&L are computed per `asset_id` over a **single lifetime ledger merged
across all co-authority sources/accounts, time-ordered** — the same lifetime model used for
CN funds. Realized and unrealized P&L accrue over the entire holding lifetime per asset;
buying and selling across accounts both contribute. An IBKR sell consumes the oldest open lot
even if that lot originated as a Schwab buy — this is the correct economic lifetime P&L the
owner wants.

The `account` column tags lots and holdings for per-broker display and attribution but does
NOT partition the FIFO ledger. There is no `(asset_id, account)` keying.

**Mechanism (verified — this is the implementation basis):**

The authority-set change makes `src/services/transaction_source_selector.py::select_transaction_sources()`
return ALL co-authority sources for an asset (it intersects transaction sources with the
authority set). Consequently `CostBasisCalculator` (`src/financial_analysis/cost_basis.py`)
receives the merged Schwab + IBKR transaction stream per `asset_id` — lifetime, across
accounts. No `(asset_id, account)` partitioning occurs.

**Transfers are NOT realized events and are excluded from the buy/sell ledger.** Schwab records
an ACAT as action `Security Transfer` (quantity only, no price, no amount);
`_SCHWAB_ACTION_MAPPING.get(action, 'other')` currently maps it to `'other'`, which
`CostBasisCalculator._process_single_transaction` ignores (no buy/sell branch) — so cost basis
stays continuous and the original buy lot persists to be consumed by the eventual sell at either
broker. IBKR records the ACAT in Deposits & Withdrawals (NOT in Trades), so it likewise
produces no buy/sell event. Precedent: CN Fund `超级转换-转入/转出` already maps to
`transfer_in`/`transfer_out` and is ignored by the calculator.

**Required for correctness (locked decisions):**

- (a) The Schwab reader must keep `Security Transfer` as a non-realizing type — verify
  `_SCHWAB_ACTION_MAPPING` does NOT map it to `'sell'`; prefer mapping it explicitly to
  `transfer_out` for clarity.
- (b) The IBKR reader (C1) must emit ACAT rows from the Deposits & Withdrawals section as
  `transfer_in`/`transfer_out` (or exclude them from the trade ledger), and emit the Trades
  section as buy/sell only.
- (c) Optionally add an explicit no-op `transfer_in`/`transfer_out` branch to
  `CostBasisCalculator` for robustness and clarity (currently they fall through to ignored).

**Huinsight does NOT depend on IBKR's reported cost basis for transferred shares** — it recomputes
lifetime cost from the merged buy/sell history, so IBKR's 2–4 week cost-basis-transfer delay
does not affect Huinsight accuracy. The IBKR Flex `CostBasis` field from Open Positions is captured
as a cross-check only (reconciliation), not as the source of truth.

**7. Full-transfer double-count — zero-qty tombstone (C3 requirement, validated)**

Because the same asset can transfer between brokers via ACAT, net worth must equal the SUM of
each source's latest-per-asset snapshot. The **2026-06-15 cross-broker validation against real
data** (an internal validation record) confirmed the partial-transfer
case reconciles perfectly (SGOV: Schwab 453.122 + IBKR 200 = 653.122 = FIFO open qty exactly),
but exposed a real defect in the **full-transfer** case:

- VOO and IEF were *fully* transferred out of Schwab, so they are **absent from Schwab's latest
  positions file**. Huinsight writes per-asset snapshots, so no new Schwab VOO/IEF row is written — the
  **stale prior Schwab snapshot (2026-05-23) stays the latest-per-asset for that source** and
  remains active. Combined with the new IBKR VOO/IEF rows, that is a **double-count**.
- The only existing guard is `_shadow_stale_reader_holdings` (`STALE_READER_SHADOW_DAYS=7` +
  `net_post_snapshot_qty <= 0`, AGENTS.md Rule 14). That is heuristic and **windowed**: it leaves
  a double-count window of up to 7 days (≈5 already elapsed in this real case — at the edge), and
  it depends on the transfer-out being interpretable as reducing post-snapshot quantity.

**Decision (C3):** add an explicit **zero-qty tombstone** rule. When a reader produces a fresh
snapshot, any `asset_id` that was active for that `source_system` in the prior snapshot but is
**absent from the new file** gets a current-dated zero-quantity (shadowed) row written for that
`(asset_id, source_system)`. Per-asset-MAX then resolves to qty 0 → the asset is no longer summed
for that source → no double-count, deterministically and immediately (no 7-day window). This
generalizes beyond IBKR — it correctly handles any reader where a holding is fully sold or
transferred and simply drops off the export. The integrity-check #6 rewrite is the automated
guard that this holds.

**Additional watch items:**
- Confirm `_SCHWAB_ACTION_MAPPING` maps `Security Transfer` to `'other'` (or better,
  `'transfer_out'`) and NOT to `'sell'`. MoneyLink cash transfers are a separate concern (F1).
- **Tax note (out of Huinsight scope, flagged for owner):** the validation observed an IEF realized
  loss (−$257.23) in Schwab in Feb 2026 followed by 172 IEF shares transferring into IBKR — a
  possible wash-sale interaction. Huinsight computes *economic lifetime* P&L, not tax-lot wash-sale
  adjustments, so this does not affect the pipeline; noted for the owner's tax records only.

---

## Consequences

**Positive:**
- Both brokers coexist in the database with no wrongful shadowing; analytics and net-worth
  calculations sum both brokers' holdings correctly by default.
- Broker identity is carried by the existing `account` column — no schema change required.
- The authority YAML is backward compatible; all 6 existing single-authority rules continue
  working without modification.
- Lifetime cross-account FIFO matches the owner's economic P&L intent: an ACAT transfer is
  not a realized event, lifetime cost basis is preserved, and Huinsight is independent of IBKR's
  delayed cost-basis transfer (2–4 weeks post-ACAT).
- The integrity check #6 rewrite gives a concrete automated guard against double-counting.

**Negative / Trade-offs:**
- `AuthorityResolver` and `HoldingsAggregator` require non-trivial changes (resolver must
  return a set; aggregator must shadow by set membership rather than equality). Existing unit
  tests for both modules must be updated.
- Integrity check #6 semantic change means any existing test that asserts the old single-winner
  behaviour must be updated before C3 can merge.
- A new zero-qty tombstone step (Decision §7) is required in C3 to close the full-transfer
  double-count window the 2026-06-15 validation exposed; adds a small amount of post-reader
  bookkeeping but removes the residual ACAT double-count risk entirely (deterministic, no window).
- UI source management (showing both brokers as active data sources) is deferred to C5 and
  does not block C1–C4 implementation.

**Neutral / Future work:**
- UI DataSourceManager redesign (C5, separate phase): the current source management UI
  predates the config engine and cannot cleanly handle co-authority sources. Decoupled from
  C1–C4 so it does not block IBKR imports.
- If a third broker is added, the `authorities:` list-form accommodates it without further
  ADR changes — just add a new YAML entry.
- Behavioral consolidation of the two `is_shadow` writers (P5 staleness archival, P6
  source-conflict resolution) was deferred in A2; C3 is the natural point to address this if
  co-authority semantics make the distinction cleaner.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| Broker-suffixed asset IDs (`US_ETF_SGOV_IBKR`) | Breaks analytics grouping (cross-broker allocation, correlation, net-worth roll-up would need explicit union logic). Also breaks cross-broker lifetime FIFO. |
| Keep single-winner authority, pick one broker as canonical | Silently drops the other broker's holdings. Defeats the purpose of having two active brokers. Produces wrong net worth. |
| Separate top-level `authority_group:` config key | Adds a third config concept alongside `authority` (string) and `authorities` (list). The list-form extension is backward compatible and simpler. |
| Shadow by account rather than source_system | `account` is more granular than `source_system` (each broker can have multiple accounts); logic becomes more complex. `source_system` is the right authority grain. |
| Per-account FIFO (lots keyed by `(asset_id, account)`) | Would treat an ACAT transfer as a sell + rebuy, breaking lifetime P&L and double-realizing gains on transfers. Also depends on IBKR's delayed cost-basis transfer (2–4 weeks post-ACAT). Rejected in favour of the lifetime merged-ledger model. |

---

## References

- ADR-013: `docs/decisions/ADR-013-authority-resolver-semantics.md` — priority semantics this
  ADR partially supersedes (the single-winner parts only; ascending-priority-wins convention is
  unchanged)
- ADR-014: `docs/decisions/ADR-014-config-driven-reader-engine.md` — registry context; adding
  `Broker_IBKR` to the registry is a C1 deliverable
- ADR-017: `docs/decisions/ADR-017-ibkr-flex-ingestion.md` — IBKR reader contract (depends on
  this ADR's authority model)
- Program plan: internal implementation notes (§C0 and §C3)
- Workstream C plan: internal implementation notes
- `src/identity/authority_resolver.py` — `resolve()` (to be extended with `resolve_authorities()`)
- `src/sync/holdings_aggregator.py` — shadow logic (to be updated for set membership)
- `src/services/transaction_source_selector.py` — `select_transaction_sources()` (returns all
  co-authority sources; feeds the merged transaction stream to `CostBasisCalculator`)
- `src/financial_analysis/cost_basis.py` — `CostBasisCalculator` (receives merged stream;
  `_process_single_transaction` ignores `'other'`/transfer types)
- `config/source_authority.yaml` — live rules (to gain list-form `authorities` entries in C3)
- AGENTS.md Rule 14 — stale-reader-shadow semantics (`STALE_READER_SHADOW_DAYS=7`)
