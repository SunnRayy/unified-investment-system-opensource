# ADR-024: Monthly Attribution Engine & Durable Flow-Tag Identity

**Date**: 2026-07-20 | **Status**: Accepted | **Version**: V7.5.1
**Plan**: internal implementation notes | **Spec**: `docs/api-specs/attribution.md`

## Context

Owner needed to answer "这个月的变动是行情还是资金流" (is this month's change market
action or capital flow). The 2026-07-19 investigation also surfaced: ACAT legs typed
`other` (invisible to flow classification), IBKR totals understated by the co-authority
merge in the reference export, and — found during implementation — the owner's manual
cash-flow classifications silently orphaning on every sync.

## Decisions

### 1. Attribution decomposition is computed, stored, and explained — never silently wrong
`Δmv = price_effect + trade_effect + transfer_effect + income_effect + residual` per
(month, asset), stored in `attribution_monthly` (V80, derived cache, idempotent per-month
rewrite). When the engine cannot honestly decompose (valuation source changed across the
month, snapshot predates trades, asset first seen), it puts the delta in `residual`,
sets `dq_flag`, and derives a `dq_reason` at read time. **A labeled residual beats a
fabricated effect** — the Feb-2026 PIS→reader transition produced a phantom −¥1.15M
"price effect" before this guard.

### 2. Historical valuation ignores `is_shadow` and evaluates per source
`is_shadow=TRUE` on old reader rows means *superseded by newer data*, not *invalid* —
filtering it erases history (33 assets lost their pre-June baseline; phantom ¥3.92M
residual). Month-boundary valuation: per (asset, source) latest row ≤ boundary; drop
legacy/PIS sources when a reader source is present (they are the pre-2026 floor);
if a `Consolidated` row is present, drop the co-authority broker rows entirely
(a broker tombstone must only zero its own source). Price terms use implied prices
(`mv/qty`, always CNY) — `market_price_unit` is native currency and must never enter
CNY arithmetic.

### 3. Flow-tag identity is the import's own natural key
`cash_flow_tags.source_row_key` = `nk:{source}|{date}|{asset}|{type}|{amount_gross}` —
exactly the delete-match identity `_replace_transactions` uses, so tags survive
re-import *by construction* (row ids regenerate every sync; 52/74 owner tags had
orphaned). V81 re-keys live tags, relinks unambiguous orphans, and never deletes
unresolved ones — they surface as "Orphaned" in the UI instead of silent dashes.
Rule: **any table referencing transactions rows across syncs must use the natural
key, never `transactions.id`.**

### 4. Directionally ambiguous reader actions use resolved pseudo-types
Schwab `Security Transfer` covers both ACAT directions; the action_map targets the
pseudo-type `transfer`, resolved by quantity sign in the reader hook and never
persisted. Pseudo-types are kind-scoped (action_map only; type_map 422s) because
only hooks that resolve them may accept them.

## Consequences

- Attribution, North Star Contributions, and the flows backfill reconcile against the
  same `cash_flow_tags` semantics (external vs internal).
- Integrity count 15 → 16 (`unmatched_security_transfer`, advisory).
- Known limitation: historical FX uses the current USD/CNY rate (TODO in
  `attribution.py` header; ties to the fx-constant known issue).
- Transition months (2026-01/02) legitimately report large labeled residuals — this is
  the honest representation of a source migration, not a defect.
