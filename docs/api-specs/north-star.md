# API Spec: North Star Panel (F3)

> Feature: First-order goal drivers — contributions, time-in-market, glide path, unforced errors; cash-flow tag classification.
> Status: Implemented (V7.4.0); WS-A classification API completeness 2026-07-12
> Last Updated: 2026-07-12

---

## Overview

PRD 2026-07-07 F3. The North Star panel surfaces investment-process KPIs that are
upstream of price outcomes: how much capital has been contributed, how long assets
have been held, how on-track the glide path is, and how many avoidable execution
errors have occurred.

Key tables: `cash_flow_tags` (migration 013/V70), `unforced_errors` (migration 013/V70).

---

## Section A: API Contract

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/north-star/panel` | Composed North Star block (contributions, time-in-market, glide path, errors) |
| POST | `/api/north-star/flows/classify` | Run heuristic cash-flow classification pass |
| POST | `/api/north-star/flows/classify/revert` | Revert specific heuristic-tagged rows (Undo) |
| GET | `/api/north-star/flows/unclassified` | List flows with no `cash_flow_tags` entry yet |
| GET | `/api/north-star/flows/classified` | List already-tagged flows (optional `?classification=` filter) |
| PUT | `/api/north-star/flows/tag` | Manual single-flow tag upsert (never overwritten by heuristic) |
| PUT | `/api/north-star/flows/tag/bulk` | Bulk manual tag upsert for multiple flows at once |
| DELETE | `/api/north-star/flows/tag` | Untag (remove) flows from `cash_flow_tags` (overlay-only delete) |
| GET | `/api/north-star/contributions` | Contribution metrics for the Cash Flow tab |
| GET | `/api/north-star/unforced-errors` | List unforced-error log entries, newest first |
| POST | `/api/north-star/unforced-errors` | Log a new execution failure |
| PATCH | `/api/north-star/unforced-errors/{id}` | Update `est_cost_cny` (appends to edit history) |

> **UI placement note (WS-A/B/C restructure, 2026-07-12)**: Following owner review,
> the panel UI is being reduced to Glide Path + Time in Market. Classification management
> moves to a new **Operations / Cash Flow Classification** page (uses `classified`,
> `unclassified`, `tag/bulk`, `DELETE /flows/tag`). Contribution metrics move to the
> **Cash Flow tab** (uses `GET /contributions`). Unforced Errors move to the **Strategy
> Alignment page** (uses `GET /unforced-errors`). The panel payload itself is **backward-
> compatible** — all fields remain; the UI simply stops rendering the relocated sections.

---

### GET `/api/north-star/panel`

The composed panel — primary data source for both the NorthStar page and
quarterly review generator's `north_star` block.

**Query params:**
- `monthly_contribution` (float, default 0.0) — owner's intended monthly contribution for
  glide-path calculation

**Response (200):**

```typescript
interface NorthStarPanel {
  contributions: ContributionMetrics;  // flows tagged external_contribution (cash_flow_tags)
  time_in_market: TimeInMarket;        // monthly equity+commodity+alternatives weight ratio
  unforced_errors: UnforcedError[];    // execution-error log, newest first
  glide_path: GlidePath;              // deterministic compounding projection to target NW
}

interface GlidePathAssumptions {
  current_nw: number;
  trailing_twr_pct: number | null;
  monthly_contribution: number;
  target: number;
  note: string;
  /** "annualized TWR, rebalanceable assets (Performance-page filter)" — never unbounded */
  twr_basis: string;
  /**
   * "trailing-12M average of flows tagged external_contribution"
   * (Fix 5, 2026-07-10: changed from income_expense_monthly 投资理财 which counted
   * internal capital recycling as new money)
   */
  run_rate_basis: string;
  /**
   * Actual monthly run-rate (null when contaminated or implausible).
   * (Fix 5, 2026-07-10)
   */
  current_run_rate_monthly: number | null;
  /**
   * Status of the run-rate computation:
   *   "available" | "pending flow classification (N untagged)" |
   *   "run-rate implausible — check flow tagging"
   * (Fix 5, 2026-07-10)
   */
  run_rate_status: string;
}

/**
 * Headline sub-dict (Fix 5, 2026-07-10): headline number and text must bind
 * to the same scenario to avoid the bug where headline said "17.3y with ¥42,000/mo"
 * but 17.3y was actually the ¥0/mo result.
 */
interface GlidePathHeadline {
  years_to_target: number | null;
  contribution_monthly: number;
  scenario_used: 'zero' | 'current_run_rate';
}

interface GlidePath {
  reachable: boolean;
  insufficient_data?: boolean;
  /** Years to target at the *scenario* (monthly_contribution param) level. */
  years_to_target?: number | null;
  /**
   * R2-4 (2026-07-10): years to target at each contribution level, same deterministic engine.
   * scenario is null when monthly_contribution == 0 (equals the zero column).
   * run_rate is null when run-rate is unavailable (R2-2 guard: UI renders "—").
   */
  years_to_target_by_scenario?: {
    zero: number | null;
    run_rate: number | null;     // null ⟺ run_rate_monthly is null
    scenario: number | null;     // null when monthly_contribution == 0
  };
  /**
   * Headline binding: UI must use this for the headline display (Fix 5, 2026-07-10).
   * headline.years_to_target equals the CAGR table column named by headline.scenario_used.
   */
  headline?: GlidePathHeadline;
  /** Null when flow dataset is contaminated or run-rate is implausible. */
  run_rate_monthly?: number | null;
  /** Status string for the run-rate column header. */
  run_rate_status?: string;
  required_cagr_grid?: RequiredCagrRow[];
  assumptions: GlidePathAssumptions;
}

// TWR basis (as of WS1 rework 2026-07-10):
// GET /north-star/panel glide_path.assumptions.trailing_twr_pct uses the SAME
// filter as GET /analytics/projection/defaults suggested_return:
//   fetch_included_asset_ids(db) + exclude_non_balanceable=True
// This avoids the FS-history inflation that produced ~35% annualized in the
// original unbounded calculate_portfolio_twr() call.
// Shared helper: src/financial_analysis/projection_defaults.suggested_return_basis()
//
// Run-rate basis (Fix 5, 2026-07-10):
// CHANGED from income_expense_monthly 投资理财 average to trailing-12M average
// of cash_flow_tags rows tagged external_contribution.  The old source counted
// internal capital recycling (SGOV→MSFT switches) as new money, inflating by ~4x.
// Contamination guard: if >5% of flow candidates are unclassified OR any
// untagged inflow >¥50K, run_rate_monthly = null, run_rate_status explains why.
// Sanity guard: if run-rate > 60% of trailing-12M gross income (income_expense_monthly
// 总收入合计), returns null + "run-rate implausible" status.
// The Contributions KPI (ytd_sum, trailing_12m_sum) is unchanged (flows-based).

interface UnforcedError {
  id: number;
  error_date: string;      // ISO date
  description: string;
  est_cost_cny: number | null;
  root_cause: string | null;
  linked_rule: string | null;
  created_at: string;      // ISO-8601 datetime
}
```

**Error modes:**
- 500 — unexpected server error

---

### POST `/api/north-star/flows/classify`

Runs the heuristic classifier over `transactions` rows that lack a `cash_flow_tags`
entry. Only tags rows as `internal_transfer` where the heuristic is confident
(e.g. same-day same-amount cross-account pairs); all others remain unclassified for
manual review.

> **Scope (2026-07-13, owner decision):** `income_expense_monthly` "Monthly summary"
> rows are **excluded** from the flow classifier entirely — they are Excel monthly
> aggregates, not actual transactions. The candidate universe is `transactions` only;
> such rows never appear in the unclassified/classified lists or contribution counts.

**Response (200):**

```typescript
interface ClassifySummary {
  classified: number;   // new tags written
  skipped: number;      // already tagged (manual or prior heuristic)
  unclassified: number; // still awaiting manual review
}
```

---

### GET `/api/north-star/flows/unclassified`

Returns candidate rows from `transactions` and `income_expense_monthly` with no
corresponding `cash_flow_tags` entry, for use in a manual tagging UI.

**Response (200) — array of:**

```typescript
interface UnclassifiedFlow {
  source_table: "transactions" | "income_expense_monthly";
  source_row_key: string;   // primary-key string for the row
  date: string;             // ISO date
  amount_cny: number;
  description: string | null;
  asset_id: string | null;
}
```

---

### PUT `/api/north-star/flows/tag`

Manual tag upsert. Always `tagged_by = 'manual'` — never overwritten by a
later heuristic run.

**Request Body:**

```typescript
interface FlowTagRequest {
  source_table: "transactions" | "income_expense_monthly";
  source_row_key: string;
  classification: "external_contribution" | "internal_transfer" | "income_reinvested";
  note?: string | null;
}
```

**Response (200):**

```typescript
interface FlowTagResult {
  source_table: string;
  source_row_key: string;
  classification: string;
  tagged_by: "manual";
  note: string | null;
  tagged_at: string;   // ISO-8601 datetime
}
```

**Error modes:**
- 404 — `source_row_key` not found in `source_table`
- 422 — invalid `classification` or `source_table`

---

### GET `/api/north-star/flows/classified`

Returns already-tagged `cash_flow_tags` rows, newest `flow_date` first. Mirrors
`list_unclassified_flows` in structure; also resolves `asset_id` for `transactions` rows.

**Query params:**
- `classification` (optional string) — filter to `external_contribution`, `internal_transfer`,
  or `income_reinvested`. Returns 422 for any other value.

**Response (200) — array of:**

```typescript
interface ClassifiedFlow {
  source_table: "transactions" | "income_expense_monthly";
  source_row_key: string;
  classification: "external_contribution" | "internal_transfer" | "income_reinvested";
  tagged_by: "heuristic" | "manual";
  amount_cny: number | null;
  flow_date: string | null;      // ISO date
  asset_id: string | null;       // resolved for transactions rows; null for income_expense_monthly
  note: string | null;
}
```

**Error modes:**
- 422 — invalid `classification` query param
- 500 — unexpected server error

---

### PUT `/api/north-star/flows/tag/bulk`

Bulk manual tag upsert. All items are tagged with `tagged_by='manual'` (D6: never
overwritten by a later heuristic run). Rows whose `source_row_key` is not found in the
source table are counted in `not_found` rather than causing a hard failure, since the UI
may race with deletions.

**Request Body:**

```typescript
interface FlowTagBulkRequest {
  items: Array<{ source_table: "transactions" | "income_expense_monthly"; source_row_key: string }>;
  classification: "external_contribution" | "internal_transfer" | "income_reinvested";
}
```

**Response (200):**

```typescript
interface FlowTagBulkResult {
  tagged: number;     // rows successfully upserted
  not_found: number;  // items whose source row was not found (soft skip)
}
```

**Error modes:**
- 422 — invalid `classification` value

---

### DELETE `/api/north-star/flows/tag`

Remove (untag) rows from `cash_flow_tags`. Scoped to the overlay table only —
never touches `transactions` or `income_expense_monthly`. Same precedent as
`POST /flows/classify/revert` which also deletes from the overlay.

**Request Body:**

```typescript
interface FlowUntagRequest {
  items: Array<{ source_table: string; source_row_key: string }>;
}
```

**Response (200):**

```typescript
interface FlowUntagResult {
  deleted: number;
}
```

**Error modes:**
- 500 — unexpected server error

---

### GET `/api/north-star/contributions`

Contribution metrics for the **Cash Flow tab** (`Analytics.tsx renderCashFlow`).
Returns `ytd_sum` and `trailing_12m_sum` from `contribution_metrics()` (external_contribution
only — same as the panel), plus a per-classification breakdown using the same trailing-12M
window for consistency.

**Response (200):**

```typescript
interface ContributionsSummary {
  /** YTD sum of flows tagged external_contribution (same as panel contributions.ytd_sum) */
  ytd_sum: number;
  /** Trailing-12M sum of flows tagged external_contribution */
  trailing_12m_sum: number;
  /** Count of candidate flow rows with no cash_flow_tags entry yet */
  unclassified_count: number;
  /** Trailing-12M sums by classification (all three keys always present, default 0.0) */
  by_classification: {
    external_contribution: number;
    internal_transfer: number;
    income_reinvested: number;
  };
  /**
   * V7.6.0 (ADR-025): authoritative 月度收支-derived portfolio
   * contribution/savings figure — a DIFFERENT source than the tag-based
   * ytd_sum/trailing_12m_sum/by_classification above. NEVER sum the two
   * (§Reconciliation regression test enforces no field equals
   * investment.net_external_ttm + trailing_12m_sum). Legacy tag-based
   * fields unchanged; their retirement is a deferred owner follow-up
   * (ADR-025 §4).
   */
  investment: {
    series: Array<{
      month: string;                       // "YYYY-MM"
      // Keys are DERIVED from the ie_column mapping's `invested` buckets
      // (reader_mappings, migration V82) — a destination is added by adding a
      // mapping row, not by editing code. Today: cn_fund, us_schwab, us_ibkr,
      // gold, bank_wealth (us_ibkr added 2026-08-01, ¥0 until the owner's Excel
      // lands on cloud). A bucket with no money in the window is present at 0.
      by_destination: Record<string, number>;
      gross_invested: number;              // CNY; _Schawab_USD never read (same money as _Schawab)
      redemptions: number;                 // Σ ie_column role='redemption' (基金赎回 + 黄金卖出 + 银行理财)
      // Both ends of the 报销 / 工作开支 round trip (role='pass_through',
      // bucket 'inflow'/'outflow') — NEW 2026-08-01. Excluded from BOTH bases.
      pass_through_in: number;
      pass_through_out: number;
      income_basis: number;                // Σ role='income' (CNY) — the LEAF income columns
      expense_basis: number;               // Σ role='expense' (CNY) — investment is NOT expense
      // The Excel-equivalent 总收入合计, DERIVED by Huinsight from the leaves
      // (income_basis + redemptions + pass_through_in) — the 总收入合计 COLUMN
      // itself is role='computed' and is never read as an input (owner ruling
      // 2026-08-01: 所有 excel 里的计算/合计值都不应该被 Huinsight 读取使用).
      income: number;
    }>;
    gross_invested_ttm: number;
    redemptions_ttm: number;
    pass_through_in_ttm: number;           // NEW 2026-08-01 (报销, repaid)
    pass_through_out_ttm: number;          // NEW 2026-08-01 (工作开支, fronted)
    income_ttm: number;                    // Excel-equivalent 总收入合计, derived — NOT a rate denominator
    /**
     * NEW 2026-08-01 (ADR-025 amendment, owner-approved). The DENOMINATOR of
     * both rates below: Σ(ie_column role='income', currency='CNY') over the
     * window — the LEAF columns, never an Excel aggregate. It is narrower than
     * income_ttm on purpose: a redemption converts an asset to cash (not
     * earning) and both ends of the 报销 pass-through cancel. 公积金
     * withdrawals and 其他偶然 bonus deliberately STAY in (owner decisions).
     * Live window 2025-08→2026-07: ¥420,000.
     */
    income_basis_ttm: number;
    /**
     * NEW 2026-08-01. Σ(role='expense', CNY) — 必要开支 + 非必要开支 leaves.
     * ⚠️ Deliberately NOT the Excel's 总支出, which BUNDLES 理财 (investment)
     * in. Investing is not spending. Live window: ¥180,000.
     */
    expense_basis_ttm: number;
    net_external_ttm: number;              // max(gross − redemptions, 0), trailing 12 DATA months
    internal_realloc_ttm: number;          // min(gross, redemptions) — the recycled portion
    /**
     * NEW 2026-08-01. RSU vested inside this same window and still held, CNY
     * (src/services/rsu_contributions.py::rsu_retained_ttm, given THIS window's
     * bounds). The savings-rate NUMERATOR is net_external_ttm + this. Sourced from
     * rsu.* — the one owner-approved exception to ADR-025's "the three sources are
     * never summed", scoped to this metric only and asserted positively by the
     * §Reconciliation test.
     */
    rsu_retained_ttm: number;
    investment_numerator_ttm: number;      // net_external_ttm + rsu_retained_ttm
    /**
     * TWO metrics, not one (owner ruling 2026-08-01, plan §WS-G). Huinsight used to
     * compute "money that reached an investment account / income" and call it
     * the savings rate. That is an INVESTMENT rate — money earned and left in
     * a bank account is saved, just not deployed. Both are shipped, separately;
     * the gap between them is undeployed_cash_ttm.
     *
     *   savings_rate_ttm    = (income_basis_ttm − expense_basis_ttm) / income_basis_ttm
     *   investment_rate_ttm = investment_numerator_ttm / income_basis_ttm
     *
     * Both null when income_basis_ttm <= 0. Live window 2025-08→2026-07:
     * 60.25% and 41.56% (the shipped figure under the superseded §2 formula
     * was 15.94%, corrected via 23.18% -> 39.33% -> 40.47% -> 41.56%).
     */
    savings_rate_ttm: number | null;
    investment_rate_ttm: number | null;
    // income_basis_ttm − expense_basis_ttm − investment_numerator_ttm: money
    // kept but not deployed. Live: ¥42,000, cross-checked against the
    // Financial_Summary CASH_* net change over the same window (+¥40,500; the
    // residual is the flat-7.0 USD conversion, the internal fx-constant tech debt entry).
    undeployed_cash_ttm: number;
    by_destination_ttm: Record<string, number>;   // same key set as series[].by_destination
    window_start_month: string | null;
    window_end_month: string | null;
    /**
     * V7.7.x (owner decision 2026-07-26, docs/design/2026-07-26-your-path.dc.html.md
     * §3.1/§3.4, "Your Path" implementation W-5). A PARTICIPATION signal — count of
     * months in the SAME trailing window (window_start_month..window_end_month) that
     * had ANY non-zero investment inflow (gross_invested > 0) — NOT a per-month AMOUNT.
     * This distinction matters: the design mock's rejected "Contribution Consistency"
     * tile compared a per-month amount against the run-rate, which ADR-025 §2 proves
     * meaningless on this data (a single ¥45,000 lump-sum month showed a 341%
     * "savings rate"). Participation ("did you invest anything this month?") carries
     * no such per-month-amount claim, so it sidesteps that trap.
     * months_with_contribution_window is the number of months actually examined
     * (== len(window); equals window_months, i.e. 12, when that much history exists,
     * else the shorter true history — never padded to look like a full window).
     * Both are null (never fabricated) when the window is empty — render an empty
     * state, not a phantom "0/0".
     */
    months_with_contribution: number | null;
    months_with_contribution_window: number | null;
  };
  /**
   * V7.7.0 (plan 2026-07-25-cash-flow-classification-completion.md §3.3,
   * §5.1 — owner decision: "RSU gets its own line so it's clear to see
   * both"). RSU shares that vest and are KEPT are real portfolio inflow but
   * appear nowhere in `investment.*` — the 月度收支 ledger books RSU vests as
   * INCOME, not investment, and only reinvested sale proceeds show up in
   * 投资理财. This is a THIRD independent source. `vest_gross_ttm` and
   * `retained_ttm` are derived directly from `transactions`
   * (source_system='RSU_Excel') via a full-history FIFO lot replay — never
   * from the tag-based sums or the 月度收支 ledger. Uses the EXACT SAME
   * trailing window as `investment.*` (read off investment's own
   * window_start_month/window_end_month, never recomputed independently).
   *
   * NEVER SUM ANY OF THE THREE SOURCES ON THIS RESPONSE
   * (ytd_sum/trailing_12m_sum/by_classification, investment.*, rsu.*) —
   * each measures a different quantity and summing any pair double-counts
   * money the 月度收支 ledger and RSU_Excel already record separately. The
   * §Reconciliation regression test enforces this for all pairwise/triple
   * combinations of net_external_ttm, trailing_12m_sum, rsu.vest_gross_ttm,
   * and rsu.retained_ttm.
   */
  rsu: {
    vest_gross_ttm: number;                // gross CNY value of RSU vests in the window; NOT netted against sells
    retained_ttm: number;                  // CNY value of shares vested in-window and still held, at vest price
    retained_shares: number;
    /**
     * Total FIFO over-sold quantity across ALL RSU_Excel history (a
     * data-health signal, not window-scoped — 0.0 when clean). > 0 means a
     * sell exceeded all open lots for that asset, most likely a data error
     * in the source Excel (e.g. a mis-dated transaction reordering the
     * vest/sell chronology — this happened once, see plan §8.5).
     * retained_ttm/retained_shares may be understated by this amount when
     * non-zero. The backend logs a warning (never raises) when this occurs;
     * the UI should render it as a visible amber notice, not silently drop it.
     */
    oversold_shares: number;
    window_start_month: string | null;     // mirrors investment.window_start_month exactly
    window_end_month: string | null;       // mirrors investment.window_end_month exactly
  };
}
```

> **Note on `internal_transfer.amount_cny`**: internal_transfer rows are stored with
> `amount_cny = 0.0` by convention (they represent capital recycling, not new money).
> The `by_classification.internal_transfer` sum will therefore always be 0.0.

> **UI display note (V7.7.0)**: the Cash Flow tab (`Analytics.tsx renderCashFlow`)
> no longer renders `ytd_sum` / `trailing_12m_sum` / `by_classification` as
> "contributions" — plan §3.4 found the tag-based trailing_12m_sum to be 100%
> gross RSU vests wearing the "contributions" label. Those fields remain in
> this response (other callers/tests may use them; removing them is a
> separate breaking change) but the tab now shows three clearly separated
> panels: Net New Invested (`investment.*`), RSU (`rsu.*`), and Flow Tags
> (`unclassified_count` pending-classification pointer only).

**Error modes:**
- 500 — unexpected server error

---

### GET `/api/north-star/unforced-errors`

**Response (200) — array of `UnforcedError` (newest first):**

```typescript
interface UnforcedError {
  id: number;
  error_date: string;
  description: string;
  est_cost_cny: number | null;
  root_cause: string | null;
  linked_rule: string | null;
  created_at: string;
}
```

---

### POST `/api/north-star/unforced-errors`

Log an avoidable execution error (wrong order type, premature exit, missed limit order, etc.).

**Request Body:**

```typescript
interface UnforcedErrorRequest {
  error_date: string;             // ISO date (YYYY-MM-DD)
  description: string;            // what happened
  est_cost_cny?: number | null;   // estimated opportunity cost in CNY
  root_cause?: string | null;     // cognitive bias, system error, etc.
  linked_rule?: string | null;    // AGENTS.md rule number this violated, if any
}
```

**Response (200):** the created `UnforcedError` object.

**Error modes:**
- 422 — malformed `error_date`
- 500 — unexpected server error

---

## Section F: Data Model Reference

### Key tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `cash_flow_tags` | Classification of transaction / income rows | `source_table`, `source_row_key`, `classification`, `tagged_by`, `note`, `tagged_at` |
| `unforced_errors` | Execution-error log | `id`, `error_date`, `description`, `est_cost_cny`, `root_cause`, `linked_rule`, `created_at` |
| `transactions` | Source for flow candidate rows | `id`, `transaction_date`, `memo`, `amount_cny` |
| `income_expense_monthly` | Source for flow candidate rows | compound key |
