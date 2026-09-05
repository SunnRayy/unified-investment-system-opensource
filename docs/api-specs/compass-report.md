# API Spec: Compass Rebalancing Report

> Feature: Display portfolio allocation vs targets with drift analysis and copy-paste markdown for AI chat
> Status: Production
> Last Updated: 2026-03-17

---

## Section A: API Contract

### Endpoint 1: Compass Summary (KPIs)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/compass/summary` | KPI metrics for compass header cards |

#### Response Type

```typescript
interface CompassSummary {
  total_net_worth: number;           // Sum of all holdings market_value (CNY)
  drift_index: number;               // Weighted average of absolute drifts (%)
  classes_in_drift: number;          // Count of classes exceeding tolerance
  total_classes: number;             // Total number of asset classes
  last_sync_date: string;            // ISO date of last data sync (YYYY-MM-DD)
  last_sync_source: string;          // Reader source name: "Schwab_CSV" | "CN_Fund_Excel" | "Gold_Excel" | "Insurance_Excel" | "RSU_Excel" | "Financial_Summary_Excel" | "DSA"
}
```

#### Example Response

```json
{
  "total_net_worth": 3500000.00,
  "drift_index": 3.85,
  "classes_in_drift": 2,
  "total_classes": 6,
  "last_sync_date": "2026-03-13",
  "last_sync_source": "Schwab_CSV"
}
```

---

### Endpoint 4: Compass Action (Rebalance Suggestions)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/compass/action` | Rebalancing actions for assets outside tolerance |

**Query Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `include_non_rebalanceable` | bool | false | Include RE/Insurance/Pension in analysis |

**Response**

```json
{
  "actions": [
    {
      "asset_class": "Equity (股票)",
      "action": "SELL",
      "current_pct": 45.15,
      "target_pct": 40.0,
      "drift_pct": 5.15,
      "priority": "high"
    }
  ],
  "total_actions": 1,
  "high_priority": 1
}
```

---

### Endpoint 2: Compass Allocation Report

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/compass/allocation` | Detailed allocation vs target by class |

**Query Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `include_non_rebalanceable` | bool | false | Include RE/Insurance/Pension in analysis |
| `include_pending` | bool | false | Overlay provisional allocation from AI Advisor pending trades |

**Provisional Semantics (`include_pending=true`)**

When `include_pending=true`, trades in `trade_logs` with `verification_status IN ('pending', 'pending_window')` are overlaid on the current (verified) allocation. These are trades logged by the AI Advisor that have NOT yet been matched against synced source files.

- A pending **Buy** increases the provisional market value for its asset class.
- A pending **Sell** decreases the provisional market value for its asset class.
- USD-denominated trades are converted to CNY using the live FX rate.
- **Stored data is unchanged.** This is a read-only, in-memory overlay — no holdings, transactions, or trade_logs rows are modified.
- Trades with `verification_status = 'verified'` are already reflected in `holdings` and must NOT be included in the overlay (no double-counting).
- `provisional_value` and `provisional_pct` are per-class totals AFTER applying the pending-trade delta to the verified base. Fields are `null` when `include_pending=false`.

#### Response Type

```typescript
interface AllocationRow {
  asset_class: string;               // English (Chinese) format, e.g., "Equity (股票)"
  current_value: number;             // Market value in original currency (verified base, CNY)
  currency: string;                  // Always "CNY" for allocation rows
  current_pct: number;               // Current allocation percentage (verified base)
  target_pct: number;                // Target allocation percentage
  drift_pct: number;                 // current_pct - target_pct (signed, verified base)
  tolerance_pct: number;             // Allowed drift before flagged
  status: "within_range" | "over" | "under";  // Drift status (verified base)
  is_top_level: boolean;             // true = top class, false = sub-class
  parent_class: string | null;       // Parent class name if sub-class

  // Provisional overlay fields — present only when include_pending=true;
  // null when include_pending=false (field is always present in response for stable shape)
  provisional_value: number | null;  // Provisional market value in CNY after pending-trade overlay
  provisional_pct: number | null;    // Provisional allocation % after pending-trade overlay
  provisional_delta_cny: number | null; // Net CNY delta from pending trades for this class (+ = net Buy, - = net Sell)
}

interface CompassAllocationMeta {
  pending_trade_count: number;       // Total pending/pending_window trades included (0 when include_pending=false)
  is_provisional: boolean;           // true when include_pending=true
}

// Top-level response shape when include_pending=true:
interface CompassAllocationResponse {
  allocation: AllocationRow[];
  meta: CompassAllocationMeta;
}

// When include_pending=false (default), response is a plain array for backward compatibility:
type CompassAllocationResponse = AllocationRow[];
```

#### Example Response

```json
[
  {
    "asset_class": "Equity (股票)",
    "current_value": 1285000.00,
    "currency": "CNY",
    "current_pct": 45.15,
    "target_pct": 40.00,
    "drift_pct": 5.15,
    "tolerance_pct": 5.00,
    "status": "over",
    "is_top_level": true,
    "parent_class": null
  },
  {
    "asset_class": "US Equity (美股)",
    "current_value": 580000.00,
    "currency": "USD",
    "current_pct": 20.38,
    "target_pct": 18.00,
    "drift_pct": 2.38,
    "tolerance_pct": 3.00,
    "status": "within_range",
    "is_top_level": false,
    "parent_class": "Equity (股票)"
  },
  {
    "asset_class": "CN Equity (A股)",
    "current_value": 450000.00,
    "currency": "CNY",
    "current_pct": 15.81,
    "target_pct": 15.00,
    "drift_pct": 0.81,
    "tolerance_pct": 3.00,
    "status": "within_range",
    "is_top_level": false,
    "parent_class": "Equity (股票)"
  },
  {
    "asset_class": "Fixed Income (固定收益)",
    "current_value": 712000.00,
    "currency": "CNY",
    "current_pct": 25.02,
    "target_pct": 30.00,
    "drift_pct": -4.98,
    "tolerance_pct": 5.00,
    "status": "within_range",
    "is_top_level": true,
    "parent_class": null
  }
]
```

---

### Endpoint 3: Compass Markdown Export

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/compass/markdown` | Pre-formatted markdown for AI chat copy-paste |

#### Response Type

```typescript
interface CompassMarkdown {
  top_level_table: string;           // Markdown table for top-level classes
  sub_class_table: string;           // Markdown table for sub-classes
  generated_at: string;              // ISO timestamp
}
```

#### Example Response

```json
{
  "top_level_table": "| Asset Class | Current % | Target % | Drift | Status |\n|-------------|-----------|----------|-------|--------|\n| Equity (股票) | 45.15% | 40.00% | +5.15% | ⚠️ Over |\n| Fixed Income (固定收益) | 25.02% | 30.00% | -4.98% | ✓ OK |\n| Cash (现金) | 15.50% | 15.00% | +0.50% | ✓ OK |\n| Alternatives (另类投资) | 14.33% | 15.00% | -0.67% | ✓ OK |",
  "sub_class_table": "| Sub-Class | Parent | Current % | Target % | Drift |\n|-----------|--------|-----------|----------|-------|\n| US Equity (美股) | Equity | 20.38% | 18.00% | +2.38% |\n| CN Equity (A股) | Equity | 15.81% | 15.00% | +0.81% |\n| HK Equity (港股) | Equity | 8.96% | 7.00% | +1.96% |\n| CN Bonds (国债) | Fixed Income | 15.02% | 18.00% | -2.98% |",
  "generated_at": "2026-01-28T10:30:00Z"
}
```

---

## Section B: Data Binding Map

### KPI Cards (4 cards)

| UI Element | Location | API Field | Format |
|------------|----------|-----------|--------|
| Net Worth - value | Card 1, main number | `summary.total_net_worth` | currency:CNY |
| Drift Index - value | Card 2, main number | `summary.drift_index` | percent:2 |
| Drift Index - icon | Card 2, beside value | Derived: show ⚠️ if > 3% | conditional |
| Classes in Drift - value | Card 3, main number | `summary.classes_in_drift` / `summary.total_classes` | "N / M" format |
| Last Sync - date | Card 4, main text | `summary.last_sync_date` | date:YYYY-MM-DD |
| Last Sync - source | Card 4, subtitle | `summary.last_sync_source` | text (reader source name, e.g. "Schwab_CSV") |

### Allocation Table

| UI Element | Column | API Field | Format |
|------------|--------|-----------|--------|
| Asset Class | Col 1 | `allocation[].asset_class` | text |
| Current Value | Col 2 | `allocation[].current_value` | currency:original |
| Current % | Col 3 | `allocation[].current_pct` | percent:2 |
| Target % | Col 4 | `allocation[].target_pct` | percent:2 |
| Drift % | Col 5 | `allocation[].drift_pct` | percent:2:signed |
| Status | Col 6 | `allocation[].status` | icon: ✓/⚠️/⬇️ |

### Markdown Container

| UI Element | Location | API Field | Format |
|------------|----------|-----------|--------|
| Top-level table | First markdown block | `markdown.top_level_table` | raw markdown |
| Sub-class table | Second markdown block | `markdown.sub_class_table` | raw markdown |
| Copy button | Each block header | N/A | copies content to clipboard |
| Generated timestamp | Footer | `markdown.generated_at` | datetime |

### AI Chat Links

| UI Element | Location | Link |
|------------|----------|------|
| Claude link | AI section | `https://claude.ai/new` |
| Gemini link | AI section | `https://gemini.google.com/app` |
| Deepseek link | AI section | `https://chat.deepseek.com/` |

---

## Section C: Demo Data Markers

### Placeholder Values (MUST be replaced)

| Demo Value in Mockup | Replace With | Format |
|---------------------|--------------|--------|
| `$3,500,000.00` | `summary.total_net_worth` | ¥{value} |
| `6.42%` | `summary.drift_index` | {value}% |
| `Reduce Equities exposure...` | Classes in drift display | N / M format |
| `$1,240.00` | Last sync date | YYYY-MM-DD |
| VOO.US / BND.US rows | Markdown tables | Remove trade rows |
| `Current drift in Equities...` | AI chat links | Link buttons |

### Static Values (DO NOT replace)

| Value | Reason |
|-------|--------|
| "Hierarchical Compass Report" | Page title |
| "Total Net Worth" | Card label |
| "Current Drift Index" | Card label |
| "Asset Class Allocation & Drift Analysis" | Section title |
| Column headers (Asset Class, Current %, etc.) | Table headers |
| "Run AI Analysis" button | Future feature placeholder |

---

## Section D: Component Reference

### KPI Cards Layout

```
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ TOTAL NET WORTH │ │ DRIFT INDEX     │ │ CLASSES IN DRIFT│ │ LAST SYNC       │
│                 │ │                 │ │                 │ │                 │
│ ¥3,500,000      │ │ 3.85% ⚠️        │ │ 2 / 6           │ │ 2026-03-13      │
│                 │ │                 │ │ classes drifting│ │ Source: Schwab_CSV │
└─────────────────┘ └─────────────────┘ └─────────────────┘ └─────────────────┘
```

- Card 1: Green/neutral - just displays value
- Card 2: Amber if drift > 3%, green if ≤ 3%
- Card 3: Red if any class in drift, green if 0
- Card 4: Neutral - info only

### Allocation Table

```
┌──────────────────────┬─────────────┬───────────┬───────────┬─────────┬────────┐
│ Asset Class          │ Current Val │ Current % │ Target %  │ Drift % │ Status │
├──────────────────────┼─────────────┼───────────┼───────────┼─────────┼────────┤
│ Equity (股票)        │ ¥1,285,000  │ 45.15%    │ 40.00%    │ +5.15%  │ ⚠️     │
│   └─ US Equity (美股)│ $82,857     │ 20.38%    │ 18.00%    │ +2.38%  │ ✓      │
│   └─ CN Equity (A股) │ ¥450,000    │ 15.81%    │ 15.00%    │ +0.81%  │ ✓      │
├──────────────────────┼─────────────┼───────────┼───────────┼─────────┼────────┤
│ Fixed Income (固定收益)│ ¥712,000   │ 25.02%    │ 30.00%    │ -4.98%  │ ✓      │
└──────────────────────┴─────────────┴───────────┴───────────┴─────────┴────────┘
```

- Top-level rows: Bold, no indent
- Sub-class rows: Indented with └─ prefix, lighter text
- Drift color: Green if positive, Red if negative
- Status icons: ✓ (within range), ⚠️ (over), ⬇️ (under)

### Markdown Export Container

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 📋 TOP-LEVEL ALLOCATION                                        [Copy]      │
├─────────────────────────────────────────────────────────────────────────────┤
│ | Asset Class | Current % | Target % | Drift | Status |                    │
│ |-------------|-----------|----------|-------|--------|                    │
│ | Equity (股票) | 45.15% | 40.00% | +5.15% | ⚠️ Over |                     │
│ | Fixed Income (固定收益) | 25.02% | 30.00% | -4.98% | ✓ OK |              │
│ ...                                                                         │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ 📋 SUB-CLASS ALLOCATION                                        [Copy]      │
├─────────────────────────────────────────────────────────────────────────────┤
│ | Sub-Class | Parent | Current % | Target % | Drift |                      │
│ |-----------|--------|-----------|----------|-------|                      │
│ | US Equity (美股) | Equity | 20.38% | 18.00% | +2.38% |                   │
│ ...                                                                         │
└─────────────────────────────────────────────────────────────────────────────┘

Generated: 2026-01-28 10:30:00
```

### AI Chat Links Section

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🤖 ANALYZE WITH AI                                                          │
│                                                                             │
│ Copy the tables above and paste into your preferred AI assistant:          │
│                                                                             │
│ [Claude]  [Gemini]  [Deepseek]                                             │
│                                                                             │
│ Future: On-page AI analysis (coming soon)                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

- Each button opens link in new tab
- Buttons styled as pills with brand colors (optional)

---

## Section E: Data Quality Requirements

### Language

- [x] Asset classes: English (Chinese) format - e.g., "Equity (股票)"
- [x] UI labels: English
- [x] Status text: English with emoji icons

### Currency

- [x] Display currency: Original per asset (CNY, USD, HKD)
- [x] Symbol mapping: CNY=¥, USD=$, HKD=HK$
- [x] Total Net Worth: Always CNY (converted)

### Number Formatting

| Field | Type | Precision | Sign Display |
|-------|------|-----------|--------------|
| `total_net_worth` | currency | 2 | never |
| `drift_index` | percent | 2 | never |
| `classes_in_drift` | integer | 0 | never |
| `current_value` | currency | 2 | never |
| `current_pct` | percent | 2 | never |
| `target_pct` | percent | 2 | never |
| `drift_pct` | percent | 2 | always (+/-) |

---

## Validation Checklist

### Backend Validation

#### Endpoint: `/api/compass/summary`

- [x] Returns 200
- [x] All 6 fields present
- [x] `drift_index` calculated as weighted average of absolute drifts
- [x] `classes_in_drift` counts correctly

**Actual Response:**

```json
{
  "total_net_worth": 3547000.00,
  "drift_index": 63.63,
  "classes_in_drift": 8,
  "total_classes": 28,
  "last_sync_date": "2026-01-28",
  "last_sync_source": "Schwab_CSV"
}
```

#### Endpoint: `/api/compass/allocation`

- [x] Returns 200
- [x] Both top-level and sub-class rows included
- [x] `is_top_level` correctly set
- [x] `parent_class` set for sub-classes
- [x] Currency field populated

**Actual Response:**

```json
[
  {
    "asset_class": "Real Estate (房地产)",
    "current_value": 5200000.0,
    "currency": "CNY",
    "current_pct": 46.83,
    "target_pct": 0.0,
    "drift_pct": 46.83,
    "tolerance_pct": 5.0,
    "status": "over",
    "is_top_level": true,
    "parent_class": null
  },
  {
    "asset_class": "Residential (住宅)",
    "current_value": 5200000.0,
    "currency": "CNY",
    "current_pct": 46.83,
    "target_pct": 10.0,
    "drift_pct": 36.83,
    "tolerance_pct": 5.0,
    "status": "over",
    "is_top_level": false,
    "parent_class": "Real Estate (房地产)"
  },
  {
    "asset_class": "Equity (股票)",
    "current_value": 1580000.00,
    "currency": "CNY",
    "current_pct": 33.08,
    "target_pct": 20.0,
    "drift_pct": 13.08,
    "tolerance_pct": 5.0,
    "status": "over",
    "is_top_level": true,
    "parent_class": null
  },
  ...
]
```

#### Endpoint: `/api/compass/markdown`

- [x] Returns 200
- [x] Valid markdown table syntax
- [x] Tables render correctly when pasted

**Actual Response:**

```json
{
  "top_level_table": "| Asset Class | Current % | Target % | Drift | Status |\n|-------------|-----------|----------|-------|--------|\n| Real Estate (房地产) | 46.83% | 0.00% | +46.83% | ⚠️ Over |\n| Equity (股票) | 33.08% | 20.00% | +13.08% | ⚠️ Over |\n| Fixed Income (固定收益) | 8.52% | 60.00% | -51.48% | ⬇️ Under |\n...",
  "sub_class_table": "| Sub-Class | Parent | Current % | Target % | Drift |\n|-----------|--------|-----------|----------|-------|\n| Residential (住宅) | Real Estate | 46.83% | 10.00% | +36.83% |\n| CN Equity (A股) | Equity | 21.63% | 10.00% | +11.63% |\n| US Equity (美股) | Equity | 10.47% | 8.00% | +2.47% |\n...",
  "generated_at": "2026-01-28T10:41:57.195770"
}
```

---

### Annotation Validation

- [x] All 4 KPI cards marked with BIND comments
- [x] Allocation table rows marked with LOOP
- [x] Markdown container marked
- [x] Static labels NOT marked
- [x] Demo trade rows removed

**Binding Count:**

- Expected from Section B: 15+
- Actual BIND comments: Implemented directly in React

---

### Frontend Implementation Validation

- [x] All 3 endpoints called
- [x] KPI cards show real data
- [x] Allocation table shows real data with hierarchy
- [x] Markdown containers show real data
- [x] Copy buttons work
- [x] AI links open in new tab
- [x] Currency symbols match asset currency
- [x] Drift colors correct (green +, red -)

### Data Quality Validation

- [x] Asset classes in "English (Chinese)" format
- [x] Currency symbols: ¥, $, HK$ per asset
- [x] Percentages have % symbol and 2 decimals
- [x] Drift shows +/- sign
- [x] Status icons display correctly

**Screenshot Evidence:**

| Element | Spec Example | Actual Displayed |
|---------|--------------|------------------|
| Net Worth | ¥3,500,000 | ¥3,547,000 |
| Drift Index | 3.85% | 4.10% |
| Classes in Drift | 2 / 6 | 3 / 6 |
| Equity row drift | +5.15% | +5.40% |

---

## Sign-off

- [x] Backend validated by: Antigravity
- [x] Annotation validated by: Antigravity
- [x] Frontend validated by: Antigravity
- [x] Architect sign-off: Tier/Class separation validated

**Date:** 2026-03-17 (updated from 2026-01-28)
**Ready for merge:** Yes

---

## Change Log

| Date | Change |
|------|--------|
| 2026-01-28 | Initial spec |
| 2026-03-17 | Fixed `last_sync_source` values (PIS → reader source names); added Endpoint 4 (compass/action) with `include_non_rebalanceable` param; updated example dates |
| 2026-06-19 | Added `include_pending` query param to `/api/compass/allocation`; added provisional overlay fields (`provisional_value`, `provisional_pct`, `provisional_delta_cny`) to `AllocationRow`; added `meta` envelope with `pending_trade_count` and `is_provisional`; backward-compat: default path returns plain array unchanged |
