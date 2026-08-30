# API Spec: Monthly Attribution (Attribution & Flows Program WS-1)

> Feature: Monthly per-asset Δ market-value decomposition — price vs trades vs transfers vs income ("这个月的变动是行情还是资金流").
> Status: SPEC LOCKED 2026-07-19 — implementation pending (WS-1)
> Plan: internal implementation notes
> Last Updated: 2026-07-20 (owner round-2 review: dq_reason explainability,
> month_to range, source_transition guard, summary flows null-vs-zero)

---

## Overview

Per month × per asset, decompose the change in market value:

```
Δmv = price_effect + trade_effect + transfer_effect + income_effect + residual
```

Rolled up at read time to sub-class / top-class / total via `taxonomy_classes`
(COALESCE parent pattern — NOT `asset_registry.is_rebalanceable`, Rule 7).
Flow semantics REUSE `cash_flow_tags` classifications so Attribution, North Star
Contributions, and the flows backfill reconcile to the same numbers.

**Owner decisions (2026-07-19 kickoff)**: UI granularity floor = per-asset
drill-down; history starts 2026-01 (reader-era only).

**Validation fixture**: June 2026 total must reproduce ≈ price −¥122K,
net flows −¥12K (manual decomposition from the 2026-07-19 investigation).

## Computation model (normative)

- **Month boundaries**: calendar months. Per-asset `mv_start` = market_value at
  the asset's own `MAX(snapshot_date)` ≤ last day of previous month; `mv_end` =
  the asset's own `MAX(snapshot_date)` within the month. NEVER global
  `MAX(snapshot_date)` (Rule 3). Assets first seen mid-month: `mv_start = 0`.
- **price_effect** = `qty_start × (p_end − p_start)` plus, for each mid-month
  qty event (buy/sell/vest — transfer legs excluded, see transfer_effect),
  `qty_event × (p_end − p_event)` — i.e. revaluation of held + acquired
  quantity to the asset's month-end price. `p_start`/`p_end` are **IMPLIED**
  prices (`mv / qty` from the boundary valuation rows, always CNY — no FX
  needed for this term), NOT `market_price_unit` (native currency, and can
  carry a different qty/price convention across a valuation-tier boundary —
  see source_transition guard below). `p_event` is the transaction's own
  native price converted to CNY via transaction-date FX before comparing to
  `p_end`. `transfer_effect` still values quantity via native
  `market_price_unit × FX` (a lookup, not a delta — unaffected by the
  convention-mismatch problem the implied-price switch fixes).
- **trade_effect** = Σ buy `amount_net` − Σ sell `amount_net` (CNY at
  **transaction-date FX**). Reinvest-dividend buy legs count here (their
  dividend legs are excluded from income_effect to avoid double count).
- **transfer_effect** = Σ transfer_in qty × month-end price − Σ transfer_out
  qty × month-end price (ACAT legs carry amount=0; value them at the receiving
  asset's month-end price so the two legs net ≈ 0 at total level).
- **income_effect** = vest events (qty × vest price). Cash dividends move value
  to cash assets, not the paying asset — they appear in the cash asset's flows,
  not here.
- **residual** = Δmv − Σ(effects). `dq_flag = |residual| >
  max(1% × max(|mv_start|, |mv_end|), ¥500)` → data-quality flag per
  asset-month (surfaced in UI + summary).
- **source_transition guard** (2026-07-20 owner round-2 review): price terms
  use IMPLIED prices — `mv/qty` from the boundary valuation rows — never
  `market_price_unit` (native currency; mv is always CNY, never mix). If the
  mv_start and mv_end boundary rows come from incompatible valuation tiers
  (a PIS/legacy-only boundary vs a reader boundary — ADR-003 tiers) OR their
  implied unit prices disagree by more than one order of magnitude even
  within the same tier (a qty/price convention mismatch), `price_effect` is
  forced to `0` for that asset-month, the whole delta lands in `residual`,
  and `dq_flag = TRUE` regardless of the residual threshold. Verified root
  cause of the Jan–Feb 2026 PIS→reader transition dq spike (e.g. Feb-2026
  US_STK_AGG: price_effect was −398,403.95 / residual +601,603.46 purely
  from this mismatch — now price_effect=0, residual carries the full delta,
  labeled `source_transition`).
- **dq_reason / dq_detail** (read-time, no schema change): for any
  `dq_flag=true` row at `level=asset` (and every row from
  `GET /attribution/asset/{asset_id}`), the engine derives a `dq_reason`
  string plus a machine-readable `dq_detail` by checking, in order:
  1. `source_transition` — see guard above (checked first — the root cause
     even if post-boundary transactions also happen to exist).
  2. `snapshot_lag` — transactions exist after mv_end's own snapshot date,
     within the month (the classic "Excel/reader data is a few days stale"
     case, e.g. 纸黄金 buys landing after the last Gold_Excel snapshot).
  3. `first_seen` — no snapshot ≤ month start (`mv_start=0` is a true
     absence, not a zero-value snapshot).
  4. `stale_end_snapshot` — mv_end's snapshot date exists but is >7 days
     before month-end with no explaining transactions (case 2 covers it
     otherwise).
  5. `unexplained` — dq_flag stays set, generic reason (no known root
     cause yet).
  Rollup rows (`sub_class`/`top_class`/`total`) always carry
  `dq_reason: null, dq_detail: null` — a reason is only meaningful for a
  single asset. Non-flagged asset rows also carry `null` for both (present
  keys, not omitted).
- **Cash-like assets** (Cash/BankWealth/MoneyMarket): cost=0 semantics (Rule:
  attribution cash cost). Their Δmv is attributed to flows via classified
  `cash_flow_tags` rows where available; unexplained remainder stays in
  residual (expected for FS-melt deposit columns).
- **Savings metrics** (derived ONLY from `cash_flow_tags`):
  `external_in`, `external_out`, `net_external` per month;
  `投资比例` = net external flowing into rebalanceable assets ÷ total external inflows.

## Storage

Migration **V80**: table `attribution_monthly`
(`month DATE` first-of-month, `asset_id`, `mv_start`, `mv_end`, `price_effect`,
`trade_effect`, `transfer_effect`, `income_effect`, `residual`,
`dq_flag BOOLEAN`, `computed_at TIMESTAMP`), unique `(month, asset_id)`.
Recompute is idempotent per month (delete month partition + rewrite).
Trigger: post-sync advisory step (non-blocking, after P8 — same slot pattern as
reference export) + on-demand recompute endpoint.

---

## Section A: API Contract

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/attribution/monthly` | One month's decomposition at a roll-up level |
| GET | `/api/attribution/asset/{asset_id}` | Per-asset attribution history |
| GET | `/api/attribution/summary` | Multi-month totals series + savings metrics |
| POST | `/api/attribution/recompute` | Recompute N months (write — MUST `mark_dirty()`) |

### GET /api/attribution/monthly

Query: `month=YYYY-MM` (required), `month_to=YYYY-MM` (optional, inclusive —
aggregates the whole `[month, month_to]` range; same ≥2026-01 floor as
`month`; 400 if invalid, before the floor, or before `month`),
`level=asset|sub_class|top_class|total` (default `sub_class`),
`include_non_rebalanceable=true|false` (default true).

```json
{
  "month": "2026-06",
  "level": "sub_class",
  "rows": [
    {
      "key": "US Equity",
      "top_class": "Equity",
      "mv_start": 0,
      "mv_end": 0,
      "delta": 0,
      "price_effect": 0,
      "trade_effect": 0,
      "transfer_effect": 0,
      "income_effect": 0,
      "residual": 0,
      "dq_flag": false,
      "dq_reason": null,
      "dq_detail": null,
      "asset_count": 12
    }
  ],
  "totals": { "delta": 0, "price_effect": 0, "trade_effect": 0,
              "transfer_effect": 0, "income_effect": 0, "residual": 0 },
  "dq_flagged_assets": ["CN_FUND_000002"],
  "computed_at": "2026-07-19T14:00:00"
}
```

`level=asset` adds `asset_id`, `asset_name`, `sub_class`, `top_class` per
row (drill-down floor) and a populated `dq_reason`/`dq_detail` when
`dq_flag=true` (see Computation model → dq_reason above), e.g.:

```json
{
  "asset_id": "ALTS_Paper_Gold", "dq_flag": true,
  "dq_reason": "月末快照 2026-06-15 早于 2 笔交易 (共 ¥20,000) — Excel/reader 数据滞后",
  "dq_detail": {
    "kind": "snapshot_lag", "snapshot_end_date": "2026-06-15",
    "post_snapshot_tx_count": 2, "post_snapshot_tx_sum": 20000.0
  }
}
```
`dq_detail.kind` ∈ `source_transition | snapshot_lag | first_seen |
stale_end_snapshot | unexplained`.

**Range mode** (`month_to` present, even if equal to `month`): `month` in
the response becomes `"YYYY-MM..YYYY-MM"`. Rows are aggregated per key
across the range: `mv_start` = the first month's `mv_start`, `mv_end` = the
last month's `mv_end` (NOT summed — see Computation model), all five effects
+ `residual` summed, `dq_flag` = OR across months, `dq_reason`/`dq_detail`
(asset-level only) come from the worst-residual flagged month in the range.
Rollup levels (`sub_class`/`top_class`/`total`) aggregate the same way, via
the per-asset range aggregate (never a flat SQL `SUM` across months, which
would double-count intermediate mv_start/mv_end values). Asset-level rows
keep `sub_class`/`top_class`.

### GET /api/attribution/asset/{asset_id}

Query: `months=N` (default 6, max 18 — history floor 2026-01).
Returns the same row shape, one element per month, newest first, plus the
underlying qty events for the expanded month (`events`: date, type, qty,
amount_cny, price). Each month also carries `dq_reason`/`dq_detail` when
flagged (same derivation as `/monthly`, computed at read time).

### GET /api/attribution/summary

Query: `months=N` (default 12, capped at history start 2026-01).

```json
{
  "months": [
    {
      "month": "2026-06",
      "delta": -134000.0,
      "price_effect": -122000.0,
      "trade_effect": 0,
      "transfer_effect": 0,
      "income_effect": 0,
      "residual": 0,
      "flows": { "external_in": 0, "external_out": -12000.0, "net_external": -12000.0 },
      "savings_rate": null,
      "invest_ratio": null,
      "dq_count": 0
    },
    {
      "month": "2026-05",
      "delta": 4200.0, "price_effect": 4200.0, "trade_effect": 0,
      "transfer_effect": 0, "income_effect": 0, "residual": 0,
      "flows": null,
      "savings_rate": null, "invest_ratio": null, "dq_count": 0
    }
  ],
  "savings_rate_ttm": 0.602507,
  "investment_rate_ttm": 0.415615,
  "income_basis_ttm": 420000.00,
  "expense_basis_ttm": 180000.00,
  "undeployed_cash_ttm": 42000.00,
  "net_external_ttm": 150000.00,
  "rsu_retained_ttm": 48000.00,
  "internal_realloc_ttm": 50000.00,
  "gross_invested_ttm": 200000.00,
  "income_ttm": 600000.00,
  "window_start_month": "2025-08",
  "window_end_month": "2026-07"
}
```

`savings_rate` / `invest_ratio` are `null` when the month has no classified
external flows (no fabricated zeros — Rule 12 adjacent).

**Response-level `*_ttm` fields (V7.6.0, ADR-025):** trailing-12-DATA-month
contribution/savings figures sourced from
`investment_contributions.contributions_summary_v2` (月度收支 `投资理财`
ledger — the contributions/savings authority), NOT from the per-month
`flows`/`invest_ratio` above (cash_flow_tags-derived). **Never sum the two
sources.** The window is anchored to the latest ledger DATA month (the Excel
lags real time), so `window_start_month`/`window_end_month` are returned
explicitly. Per-month `savings_rate` inside `months[]` stays `null` by
design (per-month rates are meaningless under lump-sum investing — ADR-025
§2). `savings_rate_ttm` is `null` when the ledger has no income basis in the
window; the money fields default to `0.0` and the window months to `null`
on an empty `income_expense_monthly`.

**`savings_rate_ttm` changed meaning on 2026-08-01** (ADR-025 Amendment,
owner-approved, plan §WS-D/E/G). It is now a true savings rate —
`(income_basis_ttm − expense_basis_ttm) / income_basis_ttm`, **60.25%** on live
data. What this field used to carry (net new money invested ÷ income, 15.94%
as shipped) is now the separate `investment_rate_ttm`, **41.56%**.

`income_ttm` in this payload keeps its meaning (the Excel-equivalent
`总收入合计`) but is now DERIVED by Huinsight from the ledger's leaf columns rather
than read out of the workbook's own aggregate — no Excel-computed value is a
calculation input anywhere (owner ruling: 所有 excel 里的计算/合计值都不应该被
Huinsight 读取使用). It is not the denominator of either rate.

**Two rates, both echoed here (2026-08-01).** They measure different things and
are ~19pp apart on live data, so a consumer must never render one under the
other's label:

| field | meaning | live |
|---|---|---|
| `savings_rate_ttm` | everything not spent — `(income_basis − expense_basis) / income_basis` | 60.25% |
| `investment_rate_ttm` | the share that reached an investment account — `(net_external + rsu_retained) / income_basis` | 41.56% |
| `undeployed_cash_ttm` | their difference in CNY — saved but not yet deployed | ¥42,000 |

`income_basis_ttm` is the denominator of both: `Σ(role='income')` over the
ledger's leaf columns. It excludes redemptions (asset→cash conversion, already
subtracted from `net_external_ttm` — leaving them in penalised the same money
twice) and both ends of the 报销 / 工作开支 round trip (`role='pass_through'`).
`expense_basis_ttm` excludes `role='invested'` — investing is not spending, even
though the workbook's own `总支出` bundles them.

The empty-data default set carries exactly this key set — the response shape
does not depend on whether the ledger has rows
(`test_get_summary_ttm_shape_is_identical_on_empty_data`).

`GET /north-star/contributions` exposes the same figures plus the per-month
series under `investment.*` (see `docs/api-specs/north-star.md`).

**`flows` is `null`, not a zero-valued object** (2026-07-20 owner round-2
review, Item E), when a month has ZERO classified `cash_flow_tags` rows —
`{external_in: 0, external_out: 0, net_external: 0}` was misleading (reads
as "confirmed no money moved" when it actually means "nothing has been
classified yet"). `flows` is only a numeric object when at least one
`cash_flow_tags` row with `classification='external_contribution'` exists
for that month; `income_effect` is unaffected (always the real computed
value). Frontend should render "—" when `flows` is `null`.

### POST /api/attribution/recompute

Body: `{ "months": 6 }`. Recomputes newest N months. Response: per-month row
counts + dq counts. MUST call `mark_dirty()` (cloud GCS flush — V7.3.0 lesson).

### Errors

All endpoints use the Rule-12 `ApiErrorResponse` helper
(`src/api/routes/_errors.py`). No silent `[]`-with-200: a month before
2026-01 → 400; unknown asset → 404; recompute failure → 500 with detail.
Router MUST be added to `ALL_ROUTERS` in `src/api/main.py` (parity guard —
2026-07-17 incident class).

---

## Section B: Frontend

New Reports page "Monthly Attribution" (Analytics area): month picker,
level toggle (top-class default → sub-class → per-asset drill-down),
waterfall/stacked-bar of the five effects, dq-flag badges, and a summary
strip reusing the classified-flows Contributions numbers. Currency display via
`useCurrency` context (NOT hardcoded ¥ — V7.4.0 lesson; there is no
`useFormatCurrency`).
