# ADR-027: One Read-Only P&L Engine — Surfaces Are Thin Formatters

**Date:** 2026-08-03
**Status:** Accepted
**Deciders:** Ray (Owner), Claude Code (Architect)

---

## Context

Current portfolio P&L (cost basis, unrealized, realized, lifetime, return %) was computed by **seven
independent implementations**, each fetching current holdings, looping per asset, applying the
cash / traded / balance-only treatment rules, and aggregating separately: `get_wealthos_assets`
(`data.py`); `get_performance_summary`, `get_gains_analysis`, `get_performance_by_class`
(`performance.py`); `build_portfolio_summary_semantics`, `fetch_wealthos_active_holdings`
(`portfolio_semantics.py`); and `context_generator.py` (three sites). This is the two-sources-signature
failure class ([[two-sources-signature-bug]]): the same number derived in N places, so a fix — or a
bug — lands in one and not the others. It is exactly how the V7.8.3 balance-only phantom (a bond's
whole market value booked as profit) looked fixed on WealthOS while the Performance report still
showed the ¥386K phantom. Patching the same rule into seven loops was the trigger to unify them.

The leaf math was already single-source and correct (`calculate_cost_basis_cny`,
`calculate_unrealized_pl_values`, `calculate_realized_pnl` → FIFO, `unrealized_from_holdings_row`),
so this is an **orchestration refactor, not a math rewrite** — the risk is regression on the
duplicated glue, which a strict parity gate contains.

---

## Decision

Introduce one read-only engine `src/services/pnl/` — `models.py` (`AssetPnL`, `PortfolioPnL`,
`Treatment`, `Scope`), `snapshot.py` (the canonical current active-holdings query with explicit
`current` and `period(start_date)` **modes**, not one universal CTE that tries to absorb every
`latest_per_asset` copy in the repo), `pnl_math.py` (the pure leaf helpers **relocated out of the
API route** so the service layer never imports upward from `performance.py`), and
`engine.py::compute_portfolio_pnl` — and re-express each of the seven surfaces as a **thin formatter**
over it, deleting each private per-asset loop only after its parity test is green. The balance-only
exclusion (value counts in net worth; cost/unrealized excluded from the gain aggregates) lives once,
in the engine.

The migration is a **strangler with parity gates, and the parity bar is not uniform**: six surfaces
that already carried the V7.8.3 balance-only rule migrate **byte-identically** (field-by-field on
fixed FX + frozen clock); `context_generator` migrates as a **documented, owner-reviewed correction**
(it still charged balance-only at cost = market_value, padding the cost/return denominators — the
engine excludes it, so only Fixed-Income return-% in the LLM export moves; all market values and
absolute P&L are unchanged). A dormant `_load_manual_overrides` seam (returns `{}`; no table yet) is
built in so Release 2's `manual_asset_pnl` override needs only to populate the table and UI.

---

## Consequences

**Positive:**
- The two-sources-signature root cause behind the V7.8.3 phantom is structurally removed — one
  implementation, so a P&L rule cannot land in one surface and not the others again.
- ~1,159 lines of duplicated per-asset loops deleted for one ~1,165-line engine; every current-P&L
  surface now reads a per-asset snapshot, removing that many latent global-`MAX(snapshot_date)`
  (Rule 3) hazards.
- WealthOS and Performance now agree to the cent where they compute the same quantity.

**Negative / Trade-offs:**
- One documented behaviour change: `context_generator`'s Fixed-Income return-% shifts on the reduced
  denominator (e.g. ~−2.55% → ~−3.95% on the live mirror). Small, owner-reviewed, and only the LLM
  markdown export — no dashboard number moves.
- A single engine is now on the critical path for seven surfaces; the parity suite is the guard that
  keeps that safe.

**Neutral / Future work:**
- Release 2 (#7): the `_load_manual_overrides` seam becomes live via `manual_asset_pnl` +
  `manual_asset_pnl_audit` (V86), the `PUT`/`DELETE /holdings/{asset_id}/manual-pnl` API, and the
  WealthOS "log P&L" UI — owner-logged cost/profit for bank-bought bonds/理财/money-market.
- Explicitly **out of scope** and not migrated: Brinson `financial_analysis/attribution.py`, the
  monthly flow-attribution engine `services/attribution.py`, `rsu_realized_gains.py`, and
  `reference_export.py` — different snapshot/contract semantics.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| Keep seven implementations, patch the balance-only rule into each | The exact failure that produced the V7.8.3 phantom; fixes drift out of sync across copies. |
| One universal snapshot CTE absorbing every `latest_per_asset` copy in the repo | Over-reach — period, historical-per-(asset,source), and broker-recovery queries have genuinely different semantics; forcing them into one CTE would flatten real behavioural differences. Modes, not one CTE. |
| Make `context_generator` byte-parity too (keep its cost = value padding) | Would preserve a known-wrong denominator in the LLM export; the owner accepted the small documented correction instead. |
| Ship the manual-override table in the same release | Sequenced separately (Part D): Release 1 is parity-gated and invisible on dashboards, Release 2 is owner-visible; bundling would gate an invisible refactor behind owner UI review. |

---

## References

- `src/services/pnl/` (`models.py`, `snapshot.py`, `pnl_math.py`, `engine.py`)
- Plan: internal implementation notes (Parts A–B, D)
- ADR-007 (native-currency P&L), the V7.8.3 balance-only P0 this unifies, V7.8.4
- [[two-sources-signature-bug]] — the failure class this removes

---

## Amendment — 2026-08-09 (Release 2, V7.9.0)

Release 2 shipped the manual-override half. Two decisions worth recording, because both were
forced by the *first* release's design rather than planned in it.

### 1. `treatment` is a classification; `has_known_cost` is the math precondition

Release 1 left four sites deciding "may this asset enter a cost/return denominator?" by asking
`treatment is Treatment.balance_only`. That is safe only while the enum and the nullability of
the cost agree — which they did, until Release 2 introduced the case the plan itself specifies:
**profit logged, cost still unknown** (§C.1 rule 4), where `treatment` becomes `manual` while
`cost_basis_cny` stays `None`. Every one of those four sites would then have read "not
balance_only", charged the asset in at cost 0, and booked its whole market value as profit —
the V7.8.3 phantom, reintroduced by the very feature built on top of its fix.

`AssetPnL.has_known_cost` (`cost_basis_cny is not None`) is now the only question a denominator
may ask. Equivalent on pre-Release-2 data, so the change moved no number, and mutation-tested:
restoring the enum check books ~¥190K of phantom profit in the guard test.

**The general rule this encodes:** when an enum is used both to describe *what something is* and
to decide *what may be computed*, adding a member breaks the second use silently. Split them
before adding the member, not after.

### 2. A surface that re-derives cannot see an override

`wealthos.py` recomputed its own balance-only and cash-equivalent treatment from raw inputs, by
design (Release 1 deliberately preserved its keyword cash convention). The consequence only
became visible when the owner clicked the button: the engine computed the logged figures and the
formatter discarded them, so the KPI total moved while the row still read "—". Its cash branch
also hard-zeroed realized, which would have silently killed the money-market yield channel §C.2
explicitly approved.

Fixed by having the formatter read the engine's numbers whenever `has_manual_data` is set — inert
otherwise, so the surface stays byte-parity. **The same lesson then repeated one level up**: the
UI decided where to offer logging by asking "does this row show a dash?", which skipped 招行理财
and 个人养老金 because they display a real-looking `+¥0.00`. Replaced by a backend-resolved
`can_log_manual_pnl`.

Twice in one release, a consumer re-deriving something the engine already knew produced a wrong
answer. The engine's job is not only to compute once but to be *asked* — a "thin formatter" that
re-derives is not thin.

### 3. Known limitation carried into Release 3

A logged cost covers the whole position, so it is invalidated by any change in position size.
V87 stamps `market_value_at_log` and warns on a ≥10% move; it deliberately does not adjust the
cost, because nothing distinguishes a deposit from interest accrual and guessing would be
inventing a number. Owner has asked for proper dated buy/sell tracking — **Release 3**.
