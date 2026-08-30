# ADR-008: Valuation Methodology for Illiquid Assets

**Date:** 2026-05-29 (drafted 2026-06-20, accepted 2026-06-20)
**Status:** Accepted

---

## Context

Huinsight holds illiquid assets — residential property and insurance NAV — that have no
market price feed. The current approach uses the most-recent user-provided value
from `Financial_Summary_Excel` (for property) and cumulative premiums paid
(for insurance). Neither approach reflects true fair value or allows meaningful P&L
attribution.

An architecture decision is needed before implementing a valuation improvement for
these asset classes.

## Decision (proposed)

Keep illiquid assets on a **carrying-value model with an explicit, dated appraisal
history** rather than any synthetic mark-to-market. Specifically:

- **Property** — carrying value = the latest *user-provided appraisal* (sourced
  from the Financial Summary balance sheet today). Cost basis = original purchase
  price (already restored/back-filled via the `Property_` cost path). Unrealized
  P&L = carrying − cost. Add an optional dated appraisal history so a value change
  is attributable to *when* the owner re-appraised, not to a sync artifact.
- **Insurance** — carrying value = cash / surrender value (already
  `market_value = cash_value`). Cost basis = cumulative premiums paid.
  P&L = surrender − premiums. This is economically correct for whole-life / savings
  policies and is retained.
- Both classes remain **excluded from rebalanceable / liquid views** (existing
  `Property_` prefix rule + the illiquid toggle); they never participate in drift
  or rebalancing math.

Rationale: this is consistent with the reader-first authority model (the owner's
workbook is the source of truth) and avoids fabricating precision the data cannot
support.

## Options considered

1. **Status quo** (single latest value, no history) — simplest, but a value change
   has no timestamp, so P&L attribution is impossible. Rejected as the baseline.
2. **Carrying value + dated appraisal history (chosen)** — small schema/UI addition;
   makes property P&L attributable to appraisal events; keeps owner in control.
3. **Model-based estimation** (e.g. index-linked property appreciation, actuarial
   insurance NAV) — rejected: unreliable for a single property, adds an external
   data dependency, and overstates precision.

## Consequences

- Low implementation cost; reuses the existing balance-sheet ingest. The only new
  work is persisting an appraisal-dated history for property and surfacing it.
- P&L is meaningful only at appraisal granularity — documented as a known limitation.
- No new external feed, no new failure mode in the sync pipeline.
- Insurance behaviour is unchanged (this ADR ratifies the current approach).

---

## Implementation status (verified 2026-06-20)

A code-seam audit found the decision is **already implemented**; this ADR ratifies the
existing mechanism. No risky cost-basis rewrite is needed.

- **Carrying value** — property via the FS balance-sheet melt
  (`src/sources/reader_hooks.py::melt_financial_summary_holdings`, `_FS_ASSET_MAPPING`);
  insurance = cash/surrender value (`src/sync/insurance_sync.py`, `config/readers/insurance.yaml`).
- **Property cost basis** — backfilled from the legacy purchase-cost shadow row with a
  **conservative market-value fallback** (cost = market ⇒ unrealized P&L = 0, never a fake
  gain): `src/sync/phases/_post_reader.py` (`Property_` branch, ~L271-294). `Property_`/`INS_`
  are exempt from realized P&L (`src/services/transaction_source_selector.py`).
- **Dated appraisal history** — already persisted per month in `balance_sheet_monthly` and
  surfaced via `GET /balance-sheet/history` (`src/api/routes/balance_sheet.py`) and the
  Balance Sheet report.
- **Excluded from rebalancing** — `NON_TRADEABLE_PREFIXES`/`Property_` rules
  (`src/sync/phases/_common.py`, `src/services/rebalanceable_filter.py`).

**Remaining future enhancement (owner-gated, not blocking):** once PIS legacy shadow rows
are retired (Phase 10), allow the owner to supply a property purchase price / dated
appraisal directly as the cost source, replacing the legacy-shadow lookup. Until then the
conservative fallback applies. (Tangential cleanup, out of ADR-008 scope: `GET
/balance-sheet/history` still uses a return-`[]`-on-exception pattern — a Rule-12 follow-up.)

---

## References

- `src/sync/insurance_sync.py` — insurance carrying value = cash/surrender value
- `src/sync/financial_summary_sync.py` + `src/sources/reader_hooks.py` — property
  carrying value via the balance-sheet melt (legacy `financial_summary_transformer.py`
  was removed in Workstream B5)
- Deferred architecture items are tracked internally
