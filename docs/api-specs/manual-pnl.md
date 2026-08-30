# API Spec: Owner-Logged P&L (#7, Release 2)

> Feature: Let the owner log the cost and/or realized profit of bank-bought assets the
> readers cannot price (money-market / 理财 / 债券 / 美元债), so those holdings show a real
> P&L instead of "—".
> Status: Implemented
> Last Updated: 2026-08-09

---

## Overview

Design: internal implementation notes (Part C). Decision:
`docs/decisions/ADR-027-single-pnl-engine.md`.

V7.8.3 established that a holding with no cost and no transactions has an **unknown**
cost, not a zero one, and must display "—" rather than book its whole balance as profit.
That is honest but unhelpful for the assets the owner *does* know the economics of: they
know "I put in X and it earned Y", because the bank told them. This feature is the way to
say so.

It is a **direct P&L override**, not a reconstructed trade log — the owner logs
*outcomes*, not trades. Synthetic `Manual_Entry` transactions were considered and rejected
(plan §C.1): more general, but it forces the owner to think in buy/dividend rows.

Storage: `manual_asset_pnl` (one row per asset) + `manual_asset_pnl_audit` (append-only
before/after JSON), both created by migration **V86** in
`DatabaseConnector.run_migrations`. Owner data — **no reader or sync phase ever writes
it**, which is what makes it re-sync-safe (a structural test scans `src/sync`,
`src/sources`, `src/fetchers` for writers).

Reads go through the one P&L engine: `src/services/pnl/manual.py` loads the whole table
once per computation and `engine.py` overlays it *after* the base treatment. Router:
`src/api/routes/manual_pnl.py` (prefix `/holdings`), registered in `ALL_ROUTERS` so it
exists on both the unprefixed and `/api` mount points.

**Net worth is unchanged by construction.** The override sets cost and realized only;
market value — hence net worth — is never read from it.

---

## Section A: API Contract

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/holdings/manual-pnl` | List every override (UI hydration) |
| PUT | `/holdings/{asset_id}/manual-pnl` | Upsert cost and/or realized profit |
| DELETE | `/holdings/{asset_id}/manual-pnl` | Clear the override |

`PUT`/`DELETE` use `get_writable_db` and call `mark_dirty()` after the write, so a cloud
edit is flushed to GCS (`verify.sh` check `[k]` enforces the pairing).

### Request body (PUT)

| Field | Type | Meaning |
|-------|------|---------|
| `cost_basis_cny` | float, optional | What the owner put in, **CNY**. Yields `unrealized = market − cost`. |
| `realized_pnl_cny` | float, optional | Cumulative realized profit to date, **CNY**. |
| `as_of_date` | date string, optional | Display provenance — the date the cumulative figure is "as of". **Never used in math.** |
| `memo` | string, optional | Free text. |

Both figures are CNY by definition; there is deliberately **no `currency` field**, because
a second convention here would eventually contradict the first.

### Response

Adds two derived flags to the stored row:

| Field | Meaning |
|-------|---------|
| `cost_affects_unrealized` | `false` for a cash-equivalent asset — the cost is stored but produces no unrealized gain (see rule 1). Without this the UI would show a cost the P&L appears to ignore. |
| `superseded` | `true` when an authoritative reader ledger has taken the asset over; the engine ignores the override and it should be deleted. |

### Status codes

| Code | When |
|------|------|
| 400 | Both figures absent. An override with neither is indistinguishable from no override, so it would be a silent no-op — use `DELETE` to clear. |
| 404 | `PUT` on an unknown `asset_id`, or `DELETE` with no override logged. |

---

## Section B: Precedence — how an override changes the P&L

The base cash / traded / balance-only classification is computed **first**, exactly as
without this feature; the overlay then adjusts specific fields (plan §C.1).

| # | Condition | Effect |
|---|-----------|--------|
| 1 | Asset is **cash-equivalent** | Keeps `unrealized = 0` **even with a logged cost** — a cash balance has no price basis, so `market − cost` is not a meaningful gain. The base cash classification survives the overlay. |
| 2 | `cost_basis_cny` set, asset **not** cash | `unrealized = market − cost`, real `return_pct`; treatment → `manual`. |
| 3 | `realized_pnl_cny` set | Overlaid **after** the base suppression, so it survives both the cash `realized = 0` path and the balance-only exclusion. This is the money-market / 理财 yield channel (§C.2). |
| 4 | Realized logged, **no cost** | Shows the profit; `cost_basis_cny` / `unrealized_cny` stay `None`, and the asset stays **out of every cost/return denominator**. |
| 5 | Neither set | Base treatment untouched — balance-only still shows "—". |

⚑ Rule 4 is the one to understand. The asset's value stays in net worth, its logged profit
shows through the realized channel, but its cost remains unknown — so it must never enter
a cost denominator, or the missing cost books the whole balance as profit (the V7.8.3
phantom). This is enforced by `AssetPnL.has_known_cost`, which every denominator asks
instead of testing the treatment enum — the enum says `manual` in exactly this case.

### Period semantics (important)

`manual_asset_pnl` holds **one cumulative** realized figure, which cannot yield a month
delta. So **manual realized applies to all-time P&L only**; period-scoped views (1m / 12m
/ 36m) ignore it by design, rather than leak a lifetime number into a monthly window.

A logged **cost** is a position basis, not a period flow, so it *does* apply in period
mode — same as the FIFO cost it replaces.

If per-period manual data is ever wanted, the table has to become append-only /
as-of-dated with derived deltas. Explicitly out of v1.

### Reader supersession

If an asset later receives transactions from a **currently-authoritative reader source**,
the override is **superseded, not added** — otherwise the owner's cumulative profit
double-counts the reader-derived profit.

The test is authority-aware, not "has any transaction": legacy/PIS rows exist for many
assets and must not trigger it. It routes through `select_transaction_sources`, and a
one-time `[MANUAL-SUPERSEDED]` warning names the affected assets so the owner can delete
the stale rows.

---

## Section B.1: Which assets may be logged (owner ruling 2026-08-09)

Logging is for **investments bought through a bank** — 债券 / 理财 / 个人养老金 and anything similar
bought directly rather than through a broker or fund platform. `AssetPnL.can_log_manual_pnl` (and
the `can_log_manual_pnl` field on the WealthOS payload) is true when **both** hold:

1. no authoritative *reader* ledger feeds the asset — otherwise the override would be superseded,
   so offering it would invite a figure that is then ignored. Legacy/PIS rows do not disqualify an
   asset (ADR-003 baseline, not a live ledger);
2. the asset is not `CASH_*`, `INS_`/`Ins_`, or `Property_` — cash and deposits are not
   investments, insurance has its own reader and cash-value semantics, and property is not a bank
   product and already carries a real cost.

Deliberately an **exclusion list, not an allowlist**: a bank product bought next year is loggable
by default. Live result: exactly four assets — `Bond_CMB_CNY`, `Bond_CMB_USD`, `Wealth_CMB`,
`Pension_Personal`.

⚑ This governs the **affordance**, not the API. `PUT` does not reject an excluded asset, and an
override that already exists on one is still honoured, editable and clearable — silently
discarding owner data would be worse than not advertising the capability.

---

## Section C: What this does NOT change

- **Net worth** — untouched, asserted to the cent.
- **Integrity check `cash_pnl_is_zero`** — it checks holdings-derived *unrealized* P&L, not
  realized, so a manual money-market realized figure does not trip it. The check needs no
  loosening and was left alone.
- **Any endpoint contract** — all three routes are new; nothing existing changed shape.
- **Net worth** — restated because it is the invariant most worth re-checking: 个人养老金's V88
  cost clearing (V7.9.0) moved total cost basis and measurable value by −¥28,000 each and left
  unrealized, realized and net worth untouched.
