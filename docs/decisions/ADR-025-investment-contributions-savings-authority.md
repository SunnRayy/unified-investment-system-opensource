# ADR-025: Investment Contributions & Savings — 月度收支 as the Authority

**Date**: 2026-07-21 | **Status**: Accepted | **Version**: V7.6.0 (merged to main 2026-07-24, PR #30)
**Plan**: internal implementation notes
**Reconciles with**: ADR-024 (attribution/flows) + the FS-cash-flows feature (commit `5cfbbbe`)

## Context

The owner wanted actual saving/investment reflected in the app. Money invested is recorded in the
Financial-Summary `月度收支` tab's `投资理财` columns (by destination: CN fund / US Schwab / gold / bank
wealth), 2020→now, already stored in `income_expense_monthly.payload`. The same economic event can appear
in three places — the `投资理财` ledger, brokerage buy transactions, and (when money moves bank→brokerage)
the FS-cash deposit deltas (the shipped feature) — so counting more than one double-counts. The prior
"savings rate" was unimplemented (always null) for lack of an income basis; `月度收支` supplies both the
investment and the income (`总收入合计`).

## Decisions

### 1. `月度收支` (投资理财) is the authoritative source for portfolio contributions + savings
The contribution/savings numbers are derived from the owner's monthly investment ledger, NOT from
`cash_flow_tags` sums. The FS-cash deposit deltas stay scoped to **attribution residual only** (per-asset
waterfall); brokerage-transaction flow tags stay the per-transaction/attribution view. **These sources are
never summed together** — a §Reconciliation regression test walks the full `/north-star/contributions`
response asserting no field equals `investment.net_external_ttm + trailing_12m_sum`.

### 2. Net external contribution & savings rate are TRAILING-window only — never per-month
Per-month `invested − redeemed` is meaningless: 2025-05 shows a 341% "savings rate" (lump-sum deploy of
previously-accumulated cash) and 2025-11 redeems more than it invests. So:
`net_external_ttm = max(Σ_ttm invested − Σ_ttm redeemed, 0)`,
`savings_rate_ttm = net_external_ttm / Σ_ttm income`, over the last 12 **data** months (anchored to the
latest ledger month, not `date.today()`, because the Excel lags). Per-month `savings_rate` stays null in
the attribution summary by design. The redemption columns (`收入_被动收入_基金赎回/黄金卖出/银行理财`)
subtract the CN→US reallocation the owner described (redeem CN fund → convert USD → reinvest US), so only
genuinely new money counts. Owner's stated rule holds by construction: pre-May-2025 redemptions ≈ 0 →
net ≈ gross (all new savings); post-May-2025 redemptions cancel the recycled portion.

### 3. `投资理财_股票基金_Schawab` and `_Schawab_USD` are the same money — never summed
Verified on every month: `Schawab(¥) == Schawab_USD($) × 参考_美元汇率`. The two columns are one Schwab
investment in two currencies. The service reads `_Schawab` (already CNY) alone and never reads
`_Schawab_USD`; **no FX conversion is used anywhere** in the contribution computation. (Summing both would
~2× US investment — caught in Lead review before implementation.)

### 4. Pre-classification is purely derived — no synthetic tags
The historical contribution series comes entirely from `income_expense_monthly` (read-only). No
`cash_flow_tags` rows are synthesized for `月度收支` months. `cash_flow_tags` remains the per-row
attribution-flow overlay only. This keeps a single, clean derivation and avoids a second orphan-able
tag class.

## Consequences

- `savings_rate` finally populates (trailing-12m; real value ≈ 13.7% on live data, net new ≈ ¥316K over
  2025-07→2026-06, vs ¥948K redeemed correctly cancelled).
- New read-only service `src/services/investment_contributions.py`
  (`monthly_investment_flows`, `contributions_summary_v2`); additive top-level ttm fields on
  `get_summary`; additive `investment` sub-object on `/north-star/contributions`. No net-worth/holdings
  change, no migration (read-only).
- **Deliberately deferred (owner follow-up):** `contribution_metrics` / `contributions_summary`'s legacy
  cash_flow_tags-based sums (`ytd_sum`, `trailing_12m_sum`, `by_classification`) are left UNCHANGED
  alongside the new authoritative `investment.*` fields. Fully retiring the tag-based number (making
  `月度收支` the *sole* contribution figure everywhere) is a reviewable follow-up, not done unsupervised.
  **Merge-gate decision (2026-07-24, Lead, at V7.6.0 merge):** deferral confirmed — retirement changes
  shipped numbers on the Cash Flow tab, so it stays an owner-reviewed follow-up; coexistence is safe
  because the §Reconciliation regression test guarantees the two sources are never summed.

---

## Amendment — 2026-07-25: §4 follow-up RESOLVED

**Status**: Accepted | **Plan**: internal implementation notes
**Supersedes**: the "Deliberately deferred (owner follow-up)" bullet in §Consequences above.

### What the investigation found

The deferral was correct but for the wrong reason. It assumed the legacy tag-based sums were a
rival *contributions* figure whose retirement would rewrite shipped numbers. Decomposition showed
otherwise:

`trailing_12m_sum` is **100% gross RSU vests and ¥0 of cash**. Confirmed live on production data —
`trailing_12m_sum == rsu.vest_gross_ttm` **exactly** (~¥600K). It was never a contributions
figure; it was gross RSU inflow wearing the "contributions" label. Retiring it outright would have
deleted the only place RSU inflow was reported anywhere.

A second gap surfaced: because §1 makes 月度收支 the authority and that ledger books RSU as
**income** (`收入_主动收入_RSU`), RSU shares that vest and are **kept** are real portfolio inflow
counted in neither `net_external_ttm` nor anywhere else.

### Decisions

**4a. The tag-based sums are retired as *contribution* figures — from the display, not the payload.**
`ytd_sum` / `trailing_12m_sum` / `by_classification` are removed from the Cash Flow tab. The
backend fields remain in `contributions_summary()` (other callers/tests depend on them; removing
them is a separate breaking change). `unclassified_count` is retained — it is still an accurate
and useful signal.

**4b. RSU becomes its own explicitly-named pair of fields, derived from the rule — not the tag sum.**
New read-only `src/services/rsu_contributions.py`:
- `rsu_vest_gross_ttm` — gross vest value in the window. Never netted against sells: the proceeds
  of a sold vest are reinvested and *already* counted by the ledger's `投资理财` columns, so
  subtracting the sale would double-subtract.
- `rsu_retained_ttm` — full-history FIFO lot replay; sums only surviving lots whose vest date falls
  inside the window, at vest price. This is the "vested and still held" measure, the only one that
  avoids both double-count and double-subtract.

Deriving these from the `rsu_vest` rule rather than the general tag sum is what makes them immune
to future tagging — the general sum stops being RSU-only the moment any other row is tagged
`external_contribution`.

**4c. The glide-path run-rate no longer derives from `cash_flow_tags` at all.**
`_contribution_run_rate` is now `(net_external_ttm + rsu_retained_ttm) / 12` over the same window.
The `flow_contamination_status` gate is removed from that path — it reads no tag data, so tag
completeness is irrelevant to it. This unblocks the Forecast page **without** requiring the
remaining FS-cash rows to be hand-tagged first. The 60%-of-gross-income sanity guard is unchanged.

**4d. `external_contribution` must never be used on an `fs_cash_delta` row.**
`attribution.py:437` treats `external_contribution` and `internal_transfer` **identically** (both
push residual into `transfer_effect`); only `income_reinvested` differs. So on an FS-cash row
`external_contribution` buys nothing in attribution while silently inflating the contributions
figure with money 月度收支 already counts. Safe vocabulary for those rows is `internal_transfer`
and `income_reinvested` only.

### Reconciliation invariant (strengthened)

There are now **three** independent sources — `investment.*` (月度收支), the legacy tag-based sums,
and `rsu.*`. **No two, and no three, may ever be summed.** The regression test was widened from a
single arithmetic combination to five across all three sources, with distinct non-zero fixtures and
an anti-vacuity self-check.

### Live values at amendment (window 2025-08 → 2026-07)

| Field | Value |
|---|---|
| `investment.net_external_ttm` | ~¥370K |
| `investment.savings_rate_ttm` | 15.94% |
| `rsu.vest_gross_ttm` | ~¥600K |
| `rsu.retained_ttm` | ~¥170K (~114 sh) |
| glide run-rate | ~¥45K/mo, status `available`, ~10.6y to target |

---

## Amendment — 2026-08-01: the savings-rate formula (supersedes §2's ratio)

**Status**: Accepted (owner-approved 2026-08-01) | **Plan**:
internal implementation notes (WS-A)
**Supersedes**: the `savings_rate_ttm = net_external_ttm / Σ_ttm income` formula in §2. Everything
else in §2 (trailing-window-only, never per-month, anchored to the latest DATA month) is unchanged.

### What triggered it

The owner said his real savings rate feels like **40–50%**, not the 15.94% the app showed. He was
right, and the gap was two independent defects — one in the numerator, one in the denominator. His
sanity check is what found them; that is worth recording as much as the fix.

### The new formula

```
savings_rate_ttm = (net_external_ttm + rsu_retained_ttm)
                   / (income_ttm − redemptions_ttm − reimbursements_ttm)
```

**Numerator — retained RSU included** (this was WS-D of the 2026-08-01 plan, previously blocked on
an owner decision). The denominator already books the FULL vest as income (`收入_主动收入_RSU`), so
excluding the shares that vested and were *kept* systematically understated the purest form of
saving. The North Star glide run-rate has summed exactly these two figures since §4c
(`(net_external_ttm + retained_cny) / 12`); the two surfaces now agree instead of disagreeing.

**Denominator — redemptions and reimbursements removed.** `总收入合计` is
`主动收入合计 + 被动收入合计`, and both halves contain money that is not income:

| Component | ¥ (window) | Kept? | Why |
|---|---:|---|---|
| `被动收入合计` = the redemption columns (基金赎回 / 黄金卖出 / 银行理财) | ~¥950K | **Removed** | A redemption is converting an asset to cash. It was penalised TWICE: subtracted in the numerator (`net_external_ttm = max(invested − redeemed, 0)`) and added back in the denominator as if it were earnings. |
| `收入_主动收入_报销` (expense reimbursement) | ¥38,364.38 | **Removed** | Repayment of money the owner already fronted — not earnings. Owner classification 2026-08-01. |
| `收入_主动收入_公积金` (two housing-fund withdrawals, late 2025 and mid-2026) | ~¥169K | **Kept** | Owner decision 2026-08-01. The housing-fund balance is not in the `资产负债` asset mapping (only `个人养老金` is), so the money genuinely enters the tracked system from outside. Deliberately NOT split into contribution-vs-withdrawal machinery. |
| `收入_主动收入_其他偶然` (two early-2026 bonus payments) | — | **Kept** | Bonus money (¥150,000 and ¥14,400). Income. Owner decision 2026-08-01. |
| `收入_被动收入_股票卖出收益` (realized gain on sold RSU shares) | 0.00 today | **Kept** | Genuine investment income. It is `role='income'`, NOT `role='redemption'` — its principal entered the ledger as RSU income and never as a `投资理财` column, so netting it out anywhere would double-subtract (§4b). It will start moving the denominator, correctly, once the owner fills the column in. |

### Why `reimbursement` is its own role, not `redemption`

`redemption` drives the **numerator** as well (`net_external = max(invested − redeemed, 0)`).
Tagging 报销 as a redemption would have subtracted it from contributions too — punishing it twice
in the opposite direction. `reimbursement` is defined as: *inside `总收入合计`, excluded from
`income_basis_ttm`, never touches the numerator.*

Both exclusions are derived from the `ie_column` **role** (migration V82/V83), never from a
hardcoded column list — the same governance decision this workstream exists to make. Note
`工作开支_出差/团建（全额报销）` contains the characters 报销 but is an outgoing expense: a
name-pattern rule would have mis-classified it, which is why roles are per-column data.

### Live before/after (window 2025-08 → 2026-07)

| Field | Before | After |
|---|---:|---:|
| `income_ttm` (总收入合计, unchanged and still returned) | ~¥2.31M | ~¥2.31M |
| `redemptions_ttm` | ~¥950K | ~¥950K |
| `reimbursements_ttm` | — (new) | ¥38,364.38 |
| `income_basis_ttm` (new — the rate's denominator) | — | ~¥1.32M |
| `net_external_ttm` | ~¥370K | ~¥370K |
| `rsu_retained_ttm` (new on this payload) | — | ~¥167K |
| `savings_numerator_ttm` (new) | — | ~¥535K |
| **`savings_rate_ttm`** | **15.94%** | **40.47%** |

Intermediate figures, recorded so a partial revert is recognisable: 23.18% (numerator fix only),
39.33% (numerator + redemptions, before 报销 was classified). The final 40.47% lands inside the
owner's stated 40–50% expectation.

`income_ttm` is deliberately **not** redefined — the raw ledger total stays visible next to the new
`income_basis_ttm` so the two can never be confused, and the response is arithmetically
self-consistent on its face (`income_basis_ttm == income_ttm − redemptions_ttm −
reimbursements_ttm`, `savings_numerator_ttm == net_external_ttm + rsu_retained_ttm`, using the
rounded figures the API actually returns).

### Reconciliation invariant (narrowed by exactly one, deliberately)

`rsu.*` now legitimately feeds `savings_rate_ttm`. That is a single, owner-approved exception to
"no two of the three sources may ever be summed", scoped to this one metric. The §Reconciliation
regression test was updated **precisely**:

- `net_external_ttm + rsu.retained_ttm` moved from the forbidden list to a **positive** assertion —
  `investment.savings_numerator_ttm` must equal it, so the exception is a tested requirement rather
  than a hole.
- Every other combination stays forbidden, including `net_external_ttm + trailing_12m_sum` (the
  original invariant), `net_external_ttm + rsu.vest_gross_ttm`, `trailing_12m_sum +
  rsu.vest_gross_ttm`, and the triple `net_external_ttm + rsu.retained_ttm + trailing_12m_sum`.
- An anti-vacuity check now asserts the field sweep actually walked the fields it protects.

---

## Amendment 2 — 2026-08-01 (later the same session): supersedes Amendment 1

**Status**: Accepted (owner rulings 2026-08-01) | **Plan**:
internal implementation notes (§WS-E, §WS-F, §WS-G)
**Supersedes**: Amendment 1's single `savings_rate_ttm` formula, its `reimbursement`
role, and its `income_basis_ttm = income_ttm − redemptions − reimbursements`
derivation. Amendment 1 is kept above **as the record of how the numbers moved**;
none of its formulas are what ships.

Three owner rulings landed after Amendment 1 was drafted, each superseding part of it.

### WS-E — no Excel-computed aggregate is ever a calculation input

> 所有 excel 里的计算/合计值都不应该被 Huinsight 读取使用，Huinsight 应该用自己计算逻辑下的分类汇总保持
> 灵活性和准确性

Amendment 1 derived `income_basis_ttm` by subtracting from the Excel's `总收入合计`. That
made correctness depend on whether the owner's SUM ranges had auto-expanded over newly
inserted columns and correctly skipped the `_USD` siblings — an invisible dependency living
in a spreadsheet.

Every total is now derived from the LEAF columns via the `ie_column` role/bucket
classification (`src/services/ie_ledger.py`, the ONE implementation shared by all four
consumers). `总收入合计` / `主动收入合计` / `被动收入合计` / `必要支出` / `非必要支出` /
`工作支出` / `理财` / `总支出` are all `role='computed'` — classified and visible, never
read for calculation. Migration **V84** retires the `total_income` bucket.

Behaviour-identical on live data (`Σ` income leaves matched the subtraction-based figure to
the cent on the live mirror) but robust against SUM-range drift, and a new income column counts
automatically once mapped. A cross-validation (`validate_ie_totals`) compares each aggregate
against Huinsight's leaf sum and **warns** — validation only, never an input. Which aggregate
covers which leaves is DATA: each leaf carries a `group` tag, each aggregate a `validates`
target, so a column rename cannot break a check.

**Trap, recorded because it is not obvious**: the Excel's `总支出` **includes** `理财`
(a representative month: roughly `¥36K + ¥500 + ¥0 + ¥37K ≈ ¥74K`). A naive `Σ(role='expense')`
excludes investment and would move shipped Cash Flow numbers — use
`LedgerTotals.total_outflow` when you mean 总支出 and `.expense` when you mean consumption.

### WS-G — the metric itself was wrong: TWO rates ship, not one

Huinsight computed "money that reached an investment account / income" and called it the **savings
rate**. That is an *investment* rate. Money earned and left in a bank account is saved — just
not deployed. This is why every correction still felt too low to the owner: 15.94% → 23.18%
→ 40.47% → 41.56%, right direction, wrong metric.

```
income_basis_ttm    = Σ(role='income', CNY)                     — LEAF columns
expense_basis_ttm   = Σ(role='expense', CNY)                    — 必要 + 非必要开支 leaves
savings_rate_ttm    = (income_basis_ttm − expense_basis_ttm) / income_basis_ttm
investment_rate_ttm = (net_external_ttm + rsu_retained_ttm) / income_basis_ttm
undeployed_cash_ttm = income_basis_ttm − expense_basis_ttm − (net_external + retained)
```

**Pass-through replaces `reimbursement` (both ends, not one).**
`工作开支_出差/团建（全额报销）` (¥37,464.38) and `收入_主动收入_报销` (¥38,364.38) are two
ends of the SAME money — fronted, then repaid. Both are excluded from BOTH bases, under one
shared role `pass_through` with `bucket='inflow'|'outflow'` naming the end, so the pairing is
**structural**: a future editor cannot half-fix it by reclassifying one side. They differ by
exactly ¥900.00 (a repayment whose spend fell outside the window) — timing, not a
defect. Migration **V85** heals DBs seeded by V82 (报销 as `income`) or V83 (as
`reimbursement`, a role that lived for hours). `role='invested'` is **not** expense — the
same 理财 trap as `总支出`.

### WS-F — one lot rule everywhere

> 既然你认定 3 月那批已经卖掉，那它就不该出现在"仍持有"里

`rsu_retained_ttm` moves from strict FIFO to specific-lot matching, matching WS-C. Under
strict FIFO the surviving 106.8 sh were attributed to the 2026-03-15 vest — the batch the
owner ruled was liquidated the next day. What he still holds is the 2025-09-15 batch. Two lot
rules answering differently about the same physical shares is the `two-sources-signature-bug`
class.

| | surviving lots | in-window `retained_cny` |
|---|---|---:|
| strict FIFO | ~107 sh @ ~$210 (a March 2026 vest) + a small GOOG lot | ~¥167K |
| **specific-lot** | ~1 sh (2025, out of window) + ~106 sh @ ~$230 (a Sep 2025 vest) + a small GOOG lot | **~¥182K** |

Retained is **~113 sh / ~$27K basis**. The CNY figure drifts with live USD/CNY between runs
(two same-day runs differed by a few ¥, purely from the live FX tick) — **assert on the USD
basis or a pinned-FX fixture, never a live-FX CNY number** (`fx-constant` tech debt).

### Live before/after (window 2025-08 → 2026-07) — the figures that ship

| Field | Amendment 1 | **Ships** |
|---|---:|---:|
| `income_ttm` (Excel-equivalent 总收入合计, now DERIVED) | ~¥2.31M | ~¥2.31M¹ |
| `redemptions_ttm` | ~¥950K | ~¥950K |
| `reimbursements_ttm` | ¥38,364.38 | *retired* → `pass_through_in_ttm` ¥38,364.38 |
| — | — | `pass_through_out_ttm` ¥37,464.38 |
| `income_basis_ttm` | ~¥1.32M (by subtraction) | **~¥1.32M** (Σ income leaves) |
| `expense_basis_ttm` | — | **~¥526K** |
| `net_external_ttm` | ~¥370K | ~¥370K |
| `rsu_retained_ttm` | ~¥167K (strict FIFO) | **~¥182K** (specific-lot, pinned FX) |
| `savings_numerator_ttm` | ~¥535K | *renamed* → `investment_numerator_ttm` **~¥550K** |
| **`savings_rate_ttm`** | 40.47% | **60.2507%** — a different metric |
| **`investment_rate_ttm`** | — | **41.5615%** |
| `undeployed_cash_ttm` | — | **~¥247K** |

¹ One cent of per-month rounding vs the workbook's own `总收入合计` cell — inside the
divergence check's ¥0.01 tolerance, and no longer load-bearing now that Huinsight derives its own.

**Independently verified against the balance sheet**: implied undeployed cash ~¥247K vs
actual `Financial_Summary_Excel` `CASH_*` net change over the same window **~+¥233K**
(per-asset latest snapshot, Rule 3 respected). The small residual (~¥14K) is the flat-7.0 USD
conversion (`fx-constant`) plus snapshot alignment. The money is real.

### Reconciliation invariant — unchanged in substance

The one owner-approved exception is now asserted on `investment.investment_numerator_ttm`
(the field `savings_numerator_ttm` was renamed to match what it actually feeds). Every other
cross-source combination stays forbidden, and the sweep gained two WS-E shapes: a field
equalling an Excel aggregate summed alongside its own leaves.

### Process note worth keeping

The owner's "this number feels wrong" check found **three** independent defects that three
agents and a lead review had all passed over: a double-penalised redemption, a repayment
counted as earnings, and — the largest — the metric being the wrong metric. None was
reachable from the code; all three required knowing what the money actually was.
