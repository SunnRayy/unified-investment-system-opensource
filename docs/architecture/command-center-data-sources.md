# Command Center Data Source Map

> **Version**: V4.2 — 2026-03-17
> Maps all 19+ Command Center UI pages to their API endpoints and backend data sources.

---

## Core Portfolio Pages

| UI Component | API Endpoint | Primary Data Source | Calculation Logic |
|-------------|-------------|-------------------|-------------------|
| **Net Worth (KPI card)** | `/wealthos/summary` → `/performance/summary` | `holdings` (latest per asset) | Sum of `market_value` where `is_shadow=FALSE` |
| **Net Worth Trend Chart** | `/performance/history` | `balance_sheet_monthly` + `holdings` | Uses `snapshot_provider.py` to merge BS monthly points with current holdings |
| **Annualized Return (KPI)** | `/wealthos/summary` | XIRR from `transactions` + `holdings` | Internal Rate of Return using cash flows and current terminal value |
| **TWR Cumulative** | `/performance/returns` | **`balance_sheet_monthly`** + `transactions` | Time-Weighted Return over a dense spine of monthly snapshots |
| **TWR YTD / 1Y** | `/performance/returns` | **`balance_sheet_monthly`** + `transactions` | Filtered TWR for specific time windows |
| **Sharpe / Sortino / Calmar** | `/performance/risk-metrics` | **`balance_sheet_monthly`** | Monthly returns derived from balance sheet snapshots |
| **Max Drawdown** | `/performance/risk-metrics` | **`balance_sheet_monthly`** | Peak-to-trough decline over historical monthly total assets |
| **Volatility** | `/performance/risk-metrics` | **`balance_sheet_monthly`** | Standard deviation of monthly returns |
| **Allocation Pie** | `/dashboard/allocation` | `holdings` (latest per asset) | Snapshots from current authoritative readers |
| **Asset Table** | `/wealthos/assets` | `holdings` (latest) + `transactions` | Per-asset P&L calculation (Market - Cost) |
| **Risk Correlation** | `/risk/correlation` | `holdings` (all `is_shadow=FALSE` dates) | Per-class MAD jump masking, 180-day window, winsorization, overlap gate ≥ 8 |

---

## Dashboard (V4.1)

| UI Component | API Endpoint | Primary Data Source | Notes |
|-------------|-------------|-------------------|-------|
| **Portfolio KPI Card** | `/dashboard/kpi` | `holdings` + `balance_sheet_monthly` | Net worth + 1-day change |
| **VS Last Month KPI Card** | `/dashboard/kpi` | `balance_sheet_monthly` | Second-to-last snapshot as baseline |
| **Market Status Composite** | `/market/sentiment` + `/dashboard/kpi` | `market_daily` + `economic_indicators` | VIX + Brent Crude + US10Y composite status |
| **Allocation Pie** | `/dashboard/allocation` | `holdings` (latest per asset) | Pie by top-level asset class |

---

## Analytics & Balance Sheet Pages

| UI Component | API Endpoint | Primary Data Source | Notes |
|-------------|-------------|-------------------|-------|
| **Balance Sheet Monthly** | `/balance-sheet/monthly` | `balance_sheet_monthly` | Assets/liabilities time series |
| **Income/Expense Monthly** | `/income-expense/monthly` | `income_expense_monthly` | Monthly income and expense breakdown |
| **Cash Flow Forecast** | `/analytics/cashflow` | `transactions` + projections | Forward cash flow projection |
| **Goals / Projections** | `/analytics/goals` | `balance_sheet_monthly` + config | Monte Carlo projection to target |

---

## Compass Report

| UI Component | API Endpoint | Primary Data Source | Notes |
|-------------|-------------|-------------------|-------|
| **Allocation vs Target** | `/compass/allocation` | `holdings` + `target_allocations` | Drift by top-level and sub-class |
| **KPI Summary** | `/compass/summary` | `holdings` + `sync_audit_reports` | `last_sync_source` = reader source name (e.g. "Schwab_CSV"), NOT "PIS" |
| **Rebalance Actions** | `/compass/action` | `holdings` + `target_allocations` | `include_non_rebalanceable` query param filters RE/Insurance/Pension |
| **Markdown Export** | `/compass/markdown` | derived from `/compass/allocation` | Pre-formatted for AI chat copy-paste |

> **API Spec**: `docs/api-specs/compass-report.md`

---

## Market Sentiment Page (V4.1)

| UI Component | API Endpoint | Primary Data Source | Notes |
|-------------|-------------|-------------------|-------|
| **13 Indicator Cards** | `/market/sentiment` | `economic_indicators` + `market_daily` | VIX, PE ratio, Brent Crude, US10Y, yield curve, etc. |
| **On-Demand Refresh** | POST `/market/sentiment/refresh` | AKShare / DSA | Pulls latest indicators and caches |

---

## Operations Pages

| UI Component | API Endpoint | Primary Data Source | Notes |
|-------------|-------------|-------------------|-------|
| **Sync Audit Report** | `/audit/latest` | `sync_audit_reports` | Created at sync time; wired Sync Now + Run New Audit |
| **Asset Drill-down** | `/audit/asset/{asset_id}` | `holdings` + `transactions` | Full asset event history |
| **Source Cross-Reference** | `/audit/cross-reference` | `holdings` grouped by source | Active Sources chips use asset counts (not anomaly counts) |
| **Import Workbench** | `/sync/status` + POST `/sync/start` | `sync_audit_logs` | Log severity classification; Sync Now wired |
| **Transaction Browser** | `/data/transactions` | `transactions` | Date chips, Asset Name/Type columns, CSV export |

---

## AI Advisors Intelligence Layer (V4.2)

| UI Component | API Endpoint | Primary Data Source | Notes |
|-------------|-------------|-------------------|-------|
| **Decision Timeline** | `/decisions/timeline` | `insights` + `deviation_actions` + `trade_logs` | Merged feed; filter by type |
| **Decision Scorecard** | `/decisions/scorecard` | `trade_logs` (scored) | Verdict/grade/outcome_pct auto-computed by `decision_scorer.py` |
| **Decision Stats** | `/decisions/stats` | `insights` + `trade_logs` | Adoption rate, total trades, drift alerts |
| **Intelligence Leaderboard** | `/decisions/leaderboard` | `trade_logs` | Per-source hit rates |
| **Adoption Funnel** | `/decisions/funnel` | `insights` + `trade_logs` | Funnel: insights → adopted → scored wins |
| **Action Inbox Alerts** | `/decisions/alerts` | derived | `alert_generator.py` — priority triage across drift/strategy/verification |
| **Strategy Alignment Chart** | `/strategy/alignment` | `holdings` + `target_allocations` + `strategy_review_reports` | 7 top-level classes; auto-computes if report > 24h old |
| **Strategy Memos** | `/strategy/memos` | `strategy_memos` | Imported from AIA Markdown strategy files |
| **Profile Targets Comparison** | `/strategy/targets` | `target_allocations` (by source) | AIA_Profile vs Huinsight profile side-by-side |
| **Verification KPIs** | `/verification/latest` | `verification_logs` + `insights` | Auto-computes if no result within 24h |
| **Verification Trends** | `/verification/trends` | `insights` (monthly aggregation) | Monthly adoption history |
| **Verdict Breakdown** | `/verification/latest` | `trade_logs` | WIN/LOSS breakdown from scorecard |

> **API Spec**: `docs/api-specs/ai-advisors.md`

---

## Management / Taxonomy Pages

| UI Component | API Endpoint | Primary Data Source | Notes |
|-------------|-------------|-------------------|-------|
| **Taxonomy Manager** | `/taxonomy/*` | `taxonomy_classes` | Class/subclass hierarchy CRUD |
| **Classification Rules** | `/management/rules` | `classification_rules` | Pattern-based auto-tag rules |
| **Tier Audit** | `/management/tier-audit` | `asset_registry` + `taxonomy_classes` | Tier target vs actual with drift |
| **Risk Profiles** | `/risk-profiles/*` | `risk_profiles` + `target_allocations` | Allocation targets by profile |

---

## AI Context Export

| UI Component | API Endpoint | Primary Data Source | Notes |
|-------------|-------------|-------------------|-------|
| **Export Markdown** | `/export/context` | Per-asset-latest CTE across all sections | Holdings, allocations, returns, risk metrics, market regime (CC-DATA-007/008 fixed) |

---

## Data Source Rationale

---

## Data Source Rationale

### Why use `balance_sheet_monthly` for TWR/Risk Metrics?

The `holdings` table only maintains active snapshots for currently tracked assets. Historical snapshots in `holdings` are often sparse or incomplete (e.g., only tracking a few assets before a full reader sync was established). Using `holdings` for time-series metrics leads to "deep dips" and nonsensical drawdown numbers.

`balance_sheet_monthly` contains aggregate net worth totals captured manually or via Financial Summary Excel over several years, providing a reliable and continuous time series for portfolio performance analysis.

### Handling Segmented TWR (Filtering)

When the user filters for "Rebalanceable Assets" only:

1. **Transactions**: Filtered exactly by `asset_id`.
2. **Snapshots**: The aggregate `合计总资产` from the balance sheet is adjusted by subtracting the estimated values of non-rebalanceable assets (Property, Insurance, etc.) using historical markers. This provides a high-fidelity proxy for rebalanceable-only performance.

### Why use per-asset-latest CTE for AI Context Export?

All sections of `context_generator.py` use per-asset `MAX(snapshot_date)` rather than a global max. Global `MAX(snapshot_date)` misses QDII assets (T+2 lag) and creates internal inconsistency across sections (CC-DATA-007/008 root cause).

### Risk Correlation Robustness (CC-DATA-006)

Raw Pearson correlation was inflated by class-level structural jumps (e.g. when a new large position was added). The current implementation applies:
- Per-class MAD jump masking
- 180-day stable window
- 5%/95% winsorization
- Minimum overlap gate of 8 observations
- Payload includes `{value, overlap, low_confidence}` per cell for frontend confidence display
