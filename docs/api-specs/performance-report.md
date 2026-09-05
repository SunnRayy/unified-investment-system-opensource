# API Spec: Performance Report

> Feature: Portfolio performance analysis — P&L breakdown, gains by asset class, top/bottom performers
> Status: Implemented (V3.4)
> Last Updated: 2026-01-30

**Scope Assumption**: All queries target Unified DB only. Data correctness between Unified DB and PIS/AIA sources is the sync pipeline's responsibility (V3.2 workstream). If Unified DB values don't match PIS/AIA, debug the sync — not the API endpoint.

**Change Log (2026-01-30)**:

- **Waterfall Chart**: Replaced TWR chart with "Lifetime P&L Contribution by Asset Class".
- **Realized P&L**: Added to `/by-class` endpoint.
- **Cash P&L Fix**: Forced "Unrealized P&L" to 0 for Cash assets; Cost Basis = Market Value.
- **Filtering**: "Top / Bottom Performers" excludes Cash/BankWealth and shows Top 5/Bottom 5 by Total P&L.

---

## Section A: API Contract

### Endpoint 1: Performance Summary (KPI Cards)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/performance/summary` | KPI metrics: net worth, realized + unrealized P&L, cost basis, asset count |

#### Response Type

```typescript
interface PerformanceSummaryResponse {
  net_worth: number;              // Total portfolio market value (CNY)
  total_cost_basis: number;       // Total cost basis (CNY)
  total_unrealized_pl: number;    // net_worth - total_cost_basis (CNY)
  unrealized_pl_pct: number;      // total_unrealized_pl / total_cost_basis * 100
  total_realized_pl: number;      // FIFO realized P&L from all sell transactions (CNY)
  total_lifetime_pl: number;      // total_unrealized_pl + total_realized_pl (CNY)
  asset_count: number;            // Count of distinct non-shadow assets
  snapshot_date: string;          // Latest snapshot date "YYYY-MM-DD"
}
```

#### Example Response

```json
{
  "net_worth": 3500000.00,
  "total_cost_basis": 3050000.00,
  "total_unrealized_pl": 450000.00,
  "unrealized_pl_pct": 14.75,
  "total_realized_pl": 85000.00,
  "total_lifetime_pl": 535000.00,
  "asset_count": 41,
  "snapshot_date": "2026-01-28"
}
```

---

### Endpoint 2: Gains Analysis (P&L Breakdown)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/performance/gains` | Realized + unrealized P&L per asset, sorted by return %. Includes top/bottom performers. |

#### Response Type

```typescript
interface GainsResponse {
  total_unrealized_pl: number;    // Portfolio-level unrealized P&L (CNY)
  total_realized_pl: number;      // Portfolio-level realized P&L via FIFO (CNY)
  total_lifetime_pl: number;      // unrealized + realized (CNY)
  total_cost_basis: number;       // Portfolio-level cost basis (CNY)
  total_market_value: number;     // Portfolio-level market value (CNY)
  unrealized_pl_pct: number;      // Portfolio-level return %
  assets: GainsAsset[];           // Per-asset breakdown, sorted by return_pct DESC
}

interface GainsAsset {
  asset_id: string;               // Canonical ID (e.g., "CN_FUND_000001")
  name: string;                   // Display name (Chinese for CN, English for US)
  top_class: string;              // Top-level class from taxonomy (e.g., "Equity (股票)")
  currency: string;               // Original currency ("CNY" | "USD")
  cost_basis: number;             // cost_price_unit * quantity (CNY)
  market_value: number;           // Current market value (CNY)
  unrealized_pl: number;          // market_value - cost_basis (CNY)
  realized_pl: number;            // FIFO realized P&L from sell transactions (CNY)
  return_pct: number;             // unrealized_pl / cost_basis * 100 (e.g., 32.03)
}
```

#### Example Response

```json
{
  "total_unrealized_pl": 450000.00,
  "total_realized_pl": 85000.00,
  "total_lifetime_pl": 535000.00,
  "total_cost_basis": 3050000.00,
  "total_market_value": 3500000.00,
  "unrealized_pl_pct": 14.75,
  "assets": [
    {
      "asset_id": "CN_FUND_000001",
      "name": "示例混合基金A",
      "top_class": "Equity (股票)",
      "currency": "CNY",
      "cost_basis": 320000.00,
      "market_value": 465000.00,
      "unrealized_pl": 145000.00,
      "realized_pl": 0.0,
      "return_pct": 45.31
    },
    {
      "asset_id": "CN_FUND_000002",
      "name": "示例指数基金B",
      "top_class": "Equity (股票)",
      "currency": "CNY",
      "cost_basis": 350000.00,
      "market_value": 244000.00,
      "unrealized_pl": -106000.00,
      "realized_pl": 5000.00,
      "return_pct": -30.29
    }
  ]
}
```

---

### Endpoint 3: Performance by Asset Class

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/performance/by-class` | P&L aggregated by top-level class and sub-class |

#### Response Type

```typescript
interface PerformanceByClassResponse {
  total_market_value: number;           // Portfolio total (CNY)
  total_cost_basis: number;             // Portfolio cost basis (CNY)
  top_classes: ClassPerformance[];      // Top-level class breakdown
  sub_classes: SubClassPerformance[];   // Sub-class breakdown
}

interface ClassPerformance {
  class_name: string;           // "Equity (股票)" format
  market_value: number;         // CNY
  cost_basis: number;           // CNY
  unrealized_pl: number;        // CNY
  realized_pl: number;          // CNY (New in V3.4)
  lifetime_pl: number;          // CNY (New in V3.4)
  return_pct: number;           // unrealized_pl / cost_basis * 100
  weight_pct: number;           // market_value / total * 100
  asset_count: number;          // Number of assets in this class
}

interface SubClassPerformance {
  top_class: string;            // Parent class "Equity (股票)"
  sub_class: string;            // "CN Equity (A股)" format
  market_value: number;         // CNY
  cost_basis: number;           // CNY
  unrealized_pl: number;        // CNY
  return_pct: number;           // unrealized_pl / cost_basis * 100
  weight_pct: number;           // market_value / total * 100
  asset_count: number;          // Number of assets
}
```

#### Example Response

```json
{
  "total_market_value": 3500000.00,
  "total_cost_basis": 3050000.00,
  "top_classes": [
    {
      "class_name": "Real Estate (房地产)",
      "market_value": 1800000.00,
      "cost_basis": 1500000.00,
      "unrealized_pl": 300000.00,
      "return_pct": 20.00,
      "weight_pct": 51.43,
      "asset_count": 1
    },
    {
      "class_name": "Equity (股票)",
      "market_value": 1580000.00,
      "cost_basis": 1430000.00,
      "unrealized_pl": 150000.00,
      "return_pct": 10.49,
      "weight_pct": 45.14,
      "asset_count": 19
    }
  ],
  "sub_classes": [
    {
      "top_class": "Equity (股票)",
      "sub_class": "CN Equity (A股)",
      "market_value": 450000.00,
      "cost_basis": 400000.00,
      "unrealized_pl": 50000.00,
      "return_pct": 12.50,
      "weight_pct": 12.86,
      "asset_count": 11
    }
  ]
}
```

---

### Existing Endpoint (No Changes Needed)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/performance/history` | Historical net worth snapshots for chart (already implemented) |

This endpoint is already implemented in `src/api/routes/data.py:224-244` and returns `[{"name": "YYYY-MM-DD", "value": float}]`. No changes needed.

**Limitation**: Only 3 snapshot dates currently exist (2026-01-22, 2026-01-23, 2026-01-28). TWR calculation requires daily snapshots. The chart will show limited data points until the daily snapshot cron is active.

---

## Section B: Data Binding Map

### KPI Cards (Endpoint 1: `/api/performance/summary`)

| UI Element | Location in Mockup | API Field | Format |
|------------|-------------------|-----------|--------|
| Total Portfolio Value | KPI Card 1 main value | `net_worth` | currency:CNY |
| Unrealized P&L amount | KPI Card 2 main value | `total_unrealized_pl` | currency:CNY:signed |
| Unrealized P&L percent | KPI Card 2 delta | `unrealized_pl_pct` | percent:2:signed |
| Total Realized P&L | KPI Card 3 main value | `total_realized_pl` | currency:CNY:signed |
| Total Lifetime P&L | KPI Card 4 main value | `total_lifetime_pl` | currency:CNY:signed |
| Assets Tracked | Below KPI cards or subtitle | `asset_count` | integer |
| Data Freshness | Below KPI cards or subtitle | `snapshot_date` | text |

### P&L Breakdown (Endpoint 2: `/api/performance/gains`)

| UI Element | Location in Mockup | API Field | Format |
|------------|-------------------|-----------|--------|
| Total Unrealized P&L | P&L summary card 1 | `total_unrealized_pl` | currency:CNY:signed |
| Total Realized P&L | P&L summary card 2 | `total_realized_pl` | currency:CNY:signed |
| Total Lifetime P&L | P&L summary card 3 | `total_lifetime_pl` | currency:CNY:signed |
| Portfolio Return | P&L summary delta | `unrealized_pl_pct` | percent:2:signed |
| Asset name | Gains table row | `assets[].name` | text |
| Asset class | Gains table row | `assets[].top_class` | text |
| Cost Basis | Gains table row | `assets[].cost_basis` | currency:CNY |
| Market Value | Gains table row | `assets[].market_value` | currency:CNY |
| Unrealized P&L | Gains table row | `assets[].unrealized_pl` | currency:CNY:signed |
| Realized P&L | Gains table row | `assets[].realized_pl` | currency:CNY:signed |
| Return % | Gains table row | `assets[].return_pct` | percent:2:signed |

### Performance by Class (Endpoint 3: `/api/performance/by-class`)

| UI Element | Location in Mockup | API Field | Format |
|------------|-------------------|-----------|--------|
| Top-class name | Class table row | `top_classes[].class_name` | text |
| Top-class market value | Class table row | `top_classes[].market_value` | currency:CNY |
| Top-class unrealized P&L | Class table row | `top_classes[].unrealized_pl` | currency:CNY:signed |
| Top-class return % | Class table row | `top_classes[].return_pct` | percent:2:signed |
| Top-class weight | Class table row | `top_classes[].weight_pct` | percent:1 |
| Sub-class name | Sub-class table row | `sub_classes[].sub_class` | text |
| Sub-class market value | Sub-class table row | `sub_classes[].market_value` | currency:CNY |
| Sub-class return % | Sub-class table row | `sub_classes[].return_pct` | percent:2:signed |

---

## Section C: Demo Data Markers

### Placeholder Values (MUST be replaced)

| Demo Value in Mockup | Replace With | Format |
|---------------------|--------------|--------|
| `1.84` (Sharpe Ratio card) | Remove card or replace — see note below | — |
| `2.31` (Sortino Ratio card) | Remove card or replace — see note below | — |
| `-8.42%` (Max Drawdown card) | Remove card or replace — see note below | — |
| `+5.12%` (Alpha card) | Remove card or replace — see note below | — |
| `--%` (TWR value) | Keep as `--` until daily snapshots available | text |
| `$330,000.00` (Realized Gains) | `total_realized_pl` | currency:CNY:signed |
| `$95,000.00` (Unrealized Gains) | `total_unrealized_pl` | currency:CNY:signed |
| `$425,000.00` (Lifetime Gains) | `total_lifetime_pl` | currency:CNY:signed |

**Note on KPI Cards**: The current 4 KPI cards (Sharpe, Sortino, Max Drawdown, Alpha) are fully hardcoded with no backend support. Replace with:

1. **Total Portfolio Value** → `net_worth` | currency:CNY
2. **Unrealized P&L** → `total_unrealized_pl` | currency:CNY:signed (delta: `unrealized_pl_pct`)
3. **Realized P&L** → `total_realized_pl` | currency:CNY:signed (FIFO calculation)
4. **Lifetime P&L** → `total_lifetime_pl` | currency:CNY:signed (unrealized + realized)

**Note on P&L Cards**: The current 3 P&L cards show USD ($) amounts. Replace with CNY (¥) since `market_value` is CNY-normalized. Keep the labels but fix values:

1. "Total Realized Gains" → `total_realized_pl` (now calculated via FIFO)
2. "Total Unrealized Gains" → `total_unrealized_pl`
3. "Total Lifetime Gains" → `total_lifetime_pl`

### Static Values (DO NOT replace)

| Value | Reason |
|-------|--------|
| "Performance Analysis" | Page title |
| "Time-Weighted Return (TWR)" | Chart label |
| "Export Report" | Button label |
| "Benchmark" | Button label |
| Time period buttons (1M, 3M, YTD, 1Y, 5Y, ALL) | UI navigation |
| "Realized vs. Unrealized Gains Analysis" | Section title (relabel to "Portfolio P&L Summary") |
| Column headers in tables | UI labels |

---

## Section D: Component Reference

### Page Layout

```
┌─────────────────────────────────────────────────────────────┐
│  Performance Analysis                [Export] [Benchmark]    │
│  ● Live Feed: 2026-01-28 ...                                │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌── Chart Card ─────────────────────────────────────────┐ │
│  │ LIFETIME P&L CONTRIBUTION (WATERFALL)                 │ │
│  │ +¥535,000 (Cumulative)                                │ │
│  │                                                         │ │
│  │ [====== Waterfall Chart: /api/performance/by-class ==]  │ │
│  │ [ Real Estate ] [ Equity ] [ Fixed Income ] [ Total ]   │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌── KPI 1 ──┐ ┌── KPI 2 ──┐ ┌── KPI 3 ──┐ ┌── KPI 4 ──┐ │
│  │ PORTFOLIO  │ │ UNREALIZED │ │ REALIZED  │ │ LIFETIME  │ │
│  │ VALUE      │ │ P&L        │ │ P&L       │ │ P&L       │ │
│  │            │ │            │ │ (FIFO)    │ │           │ │
│  │ ¥3.50M     │ │ +¥450K     │ │ +¥85K     │ │ +¥535K    │ │
│  │            │ │ +14.75%    │ │           │ │           │ │
│  │ [▓▓▓▓▓▓░] │ │ [▓▓▓▓▓░░] │ │ [▓▓▓▓░░░] │ │ [▓▓▓▓▓░░]│ │
│  └────────────┘ └────────────┘ └───────────┘ └───────────┘ │
│  41 assets tracked · Last sync: 2026-01-28                   │
│                                                              │
│  ── Realized vs. Unrealized Gains ─── [All Time Periods]     │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐   │
│  │ REALIZED    │ │ UNREALIZED  │ │ LIFETIME            │   │
│  │ GAINS       │ │ GAINS       │ │ GAINS               │   │
│  │ +¥85,000    │ │ +¥450,000   │ │ +¥535,000           │   │
│  │ ✓ Locked    │ │ Market Val  │ │ Cumulative P/L      │   │
│  └─────────────┘ └─────────────┘ └─────────────────────┘   │
│                                                              │
│  ── Performance by Asset Class ──── (/api/performance/by-class)
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Class          │ Value      │ P&L       │ Ret%  │ Wt%  │ │
│  │ Real Estate    │ ¥7.80M     │ +¥4.98M   │+176%  │46.9% │ │
│  │ Equity (股票)   │ ¥5.43M     │ +¥260K    │+5.0%  │32.6% │ │
│  │ Fixed Income   │ ¥1.46M     │ -¥567     │-0.0%  │ 8.8% │ │
│  │ ...            │            │           │       │      │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ── Top/Bottom Investment Performers ── (/api/performance/gains)
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Asset              │ Class    │ Cost      │ P&L   │ Ret │ │
│  │ 易方达中证500ETF     │ Equity   │ ¥318K    │+¥145K │+46% │ │
│  │ 示例沪深300A   │ Equity   │ ¥583K    │+¥187K │+32% │ │
│  │ ... (Filtered: Top 5 & Bottom 5, No Cash)            ...| │
│  │ 示例沪深300A       │ Equity   │ ¥350K    │-¥107K │-30% │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### Styling Notes

- Positive P&L values: Green (`#22c55e`)
- Negative P&L values: Red (`#ef4444`)
- Neutral/zero: Default text color
- Currency: ¥ prefix, comma-separated thousands
- Percentages: Signed (+/-), 2 decimal places
- KPI cards: Progress bar below value (optional visual, not data-bound)

---

## Section E: Data Quality Requirements

### Language

- [x] Asset names: Original (Chinese for CN funds, English for US stocks)
- [x] Asset classes: English (Chinese) format — e.g., "Equity (股票)"
- [x] UI labels: English

### Currency

- [x] Display currency: CNY (all values are CNY-normalized in `market_value`)
- [x] Symbol: ¥
- [x] Decimal places: 2 (for values), 0 (for large display values like KPI cards)

### Number Formatting

| Field | Type | Precision | Sign Display |
|-------|------|-----------|--------------|
| `net_worth` | currency | 2 | never |
| `total_cost_basis` | currency | 2 | never |
| `total_unrealized_pl` | currency | 2 | always (+/-) |
| `total_realized_pl` | currency | 2 | always (+/-) |
| `total_lifetime_pl` | currency | 2 | always (+/-) |
| `unrealized_pl_pct` | percent | 2 | always (+/-) |
| `asset_count` | integer | 0 | never |
| `return_pct` | percent | 2 | always (+/-) |
| `weight_pct` | percent | 1 | never |
| `market_value` | currency | 2 | never |
| `cost_basis` | currency | 2 | never |
| `unrealized_pl` | currency | 2 | always (+/-) |
| `realized_pl` | currency | 2 | always (+/-) |

---

## Section F: Data Model Reference

> **CRITICAL**: This section prevents data correctness issues. The Architect has validated
> these queries against the actual database on 2026-01-29.

### Required Tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `holdings` | Position data: quantity, cost, market value | `asset_id`, `market_value`, `cost_price_unit`, `quantity`, `is_shadow`, `snapshot_date`, `currency` |
| `transactions` | Trade history for FIFO realized P&L | `asset_id`, `transaction_type`, `quantity`, `price_unit`, `amount_net`, `currency`, `transaction_date` |
| `asset_registry` | Asset identity and sub-class | `canonical_id`, `asset_class` (sub-class level, e.g., "CN Equity") |
| `asset_taxonomy` | Classification hierarchy: sub-class → top-class | `asset_subclass` → `asset_class` (e.g., "CN Equity" → "股票") |

### Required JOINs

```sql
-- JOIN path: holdings → asset_registry → asset_taxonomy
-- This gives us: asset → sub-class → top-class hierarchy
FROM holdings h
LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
LEFT JOIN asset_taxonomy t ON r.asset_class = t.asset_subclass
                           AND t.expired_date IS NULL

-- IMPORTANT: Filter out shadow holdings
WHERE h.is_shadow = FALSE
```

### Data Derivation Logic

| Output Field | Source | Calculation |
|--------------|--------|-------------|
| `net_worth` | `holdings.market_value` | `SUM(market_value) WHERE is_shadow = FALSE` |
| `total_cost_basis` | `holdings.cost_price_unit`, `holdings.quantity` | `SUM(cost_price_unit * quantity)` |
| `total_unrealized_pl` | derived | `net_worth - total_cost_basis` |
| `unrealized_pl_pct` | derived | `total_unrealized_pl / total_cost_basis * 100` |
| `total_realized_pl` | FIFO calculator | `SUM(calculator.realized_pnl)` across all assets (see integration pattern below) |
| `total_lifetime_pl` | derived | `total_unrealized_pl + total_realized_pl` |
| `realized_pl` (per asset) | FIFO calculator | `calculator.realized_pnl` for that asset |
| `asset_count` | `holdings.asset_id` | `COUNT(DISTINCT asset_id) WHERE is_shadow = FALSE` |
| `top_class` | taxonomy JOIN | `COALESCE(t.asset_class, r.asset_class, 'Unclassified')` |
| `return_pct` (per asset) | derived | `unrealized_pl / cost_basis * 100` (guard against zero cost_basis) |
| `weight_pct` | derived | `market_value / total_market_value * 100` |
| `class_name` display | DISPLAY_MAP | Chinese top-class → "English (Chinese)" format |

### FIFO Realized P&L Integration Pattern

The `CostBasisCalculator` at `src/financial_analysis/cost_basis.py` already computes realized P&L.
The proven integration pattern is at `src/validation/cost_basis_validator.py:96-134`.

**Backend agent: use this pattern to calculate realized P&L per asset:**

```python
import pandas as pd
from src.financial_analysis.cost_basis import CostBasisCalculator

def calculate_realized_pnl(db, asset_id: str) -> float:
    """Calculate realized P&L for a single asset using FIFO."""
    tx_rows = db.execute("""
        SELECT transaction_type, quantity, price_unit, amount_net, currency, transaction_date
        FROM transactions
        WHERE asset_id = ?
        ORDER BY transaction_date ASC
    """, (asset_id,)).fetchall()

    if not tx_rows:
        return 0.0

    df = pd.DataFrame(tx_rows, columns=[
        'transaction_type', 'quantity', 'price_unit', 'amount_net', 'currency', 'transaction_date'
    ])
    df['transaction_date'] = pd.to_datetime(df['transaction_date'])
    df.set_index('transaction_date', inplace=True)

    calculator = CostBasisCalculator(asset_id)
    calculator.process_transactions(df)

    return calculator.realized_pnl  # This is the FIFO realized P&L

# For portfolio total: loop over all asset_ids and sum
total_realized = sum(calculate_realized_pnl(db, aid) for aid in all_asset_ids)
```

**Performance note**: With ~41 assets and ~2,863 transactions, this runs in <1 second. The validator already does this for all assets. Cache the result if needed.

### Display Name Mapping

Use the bilingual format established by Compass:

| DB Value (asset_taxonomy.asset_class) | Display Value |
|---------------------------------------|---------------|
| `股票` | Equity (股票) |
| `固定收益` | Fixed Income (固定收益) |
| `现金` | Cash (现金) |
| `商品` | Commodities (商品) |
| `房地产` | Real Estate (房地产) |
| `另类投资` | Alternatives (另类投资) |
| `保险` | Insurance (保险) |
| `Unclassified` | Unclassified |

For sub-classes:

| DB Value (asset_registry.asset_class) | Display Value |
|---------------------------------------|---------------|
| `CN Equity` | CN Equity (A股) |
| `US Equity` | US Equity (美股) |
| `HK ETF` | HK ETF (港股ETF) |
| `美国政府债券` | US Gov Bonds (美国政府债券) |
| `货币市场` | Money Market (货币市场) |
| `住宅地产` | Residential (住宅地产) |
| `加密货币` | Crypto (加密货币) |
| `黄金` | Gold (黄金) |
| `现金` | Cash (现金) |
| `Insurance Products` | Insurance (保险产品) |

### Known Data Model Gotchas

1. **`market_value` is CNY-normalized**: For all assets (including USD), `market_value` is already in CNY. Do NOT apply additional FX conversion.

2. **USD assets have `market_price_unit = 0`**: The sync pipeline stores market value directly but doesn't always populate unit price for foreign currency assets. Use `market_value` directly, not `quantity * market_price_unit` for USD.

3. **`cost_price_unit` is also CNY-normalized**: Cost basis = `cost_price_unit * quantity` gives CNY value. The exchange rate was applied during sync.

4. **`exchange_rates` table is EMPTY**: All FX conversion happens in the sync pipeline, not at query time. Never query this table for conversion.

5. **`asset_registry.asset_class` is the SUB-CLASS level**: Despite the column name, it stores sub-class values like "CN Equity", "US Equity", "住宅地产". The top-level class comes from `asset_taxonomy.asset_class`.

6. **Some assets are Unclassified**: `CN_FUND_000001`, `BankWealth_招行`, `Pension_Personal` have no entry in `asset_registry` or the entry has `asset_class = NULL`. Use `COALESCE(t.asset_class, r.asset_class, 'Unclassified')`.

7. **Zero cost basis**: Some assets (e.g., Insurance) have `cost_price_unit = 0`. Guard against division by zero when calculating `return_pct`.

8. **Only PIS data currently**: All holdings have `source_system = 'PIS'`. No AIA data at this time.

9. **`map_class()` is an anti-pattern**: The function at `src/api/routes/data.py:68-76` is a hardcoded workaround. Use the taxonomy JOIN instead. It misclassifies assets like "住宅地产" → "另类投资" instead of "房地产".

### Pre-Implementation Verification Queries

Backend agent: Run these BEFORE implementing to verify you understand the data model.

**Query 1: Summary KPIs**

```sql
SELECT
    SUM(market_value) as net_worth,
    SUM(cost_price_unit * quantity) as total_cost_basis,
    SUM(market_value - cost_price_unit * quantity) as total_unrealized_pl,
    COUNT(DISTINCT asset_id) as asset_count,
    MAX(snapshot_date) as latest_snapshot
FROM holdings
WHERE is_shadow = FALSE;

-- Expected: net_worth ~16.6M, cost_basis ~11.0M, unrealized ~5.7M, assets ~41
```

**Query 2: Top-class performance (verifies taxonomy JOIN)**

```sql
SELECT
    COALESCE(t.asset_class, r.asset_class, 'Unclassified') as top_class,
    SUM(h.market_value) as market_value,
    SUM(h.cost_price_unit * h.quantity) as cost_basis,
    SUM(h.market_value - (h.cost_price_unit * h.quantity)) as unrealized_pl,
    COUNT(DISTINCT h.asset_id) as asset_count
FROM holdings h
LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
LEFT JOIN asset_taxonomy t ON r.asset_class = t.asset_subclass AND t.expired_date IS NULL
WHERE h.is_shadow = FALSE
GROUP BY 1
ORDER BY market_value DESC;

-- Expected: 房地产 ~7.8M (46.9%), 股票 ~5.4M (32.6%), 固定收益 ~1.5M, etc.
-- CRITICAL: 住宅地产 must map to 房地产, NOT 另类投资
```

**Query 3: Per-asset gains (verifies return calculation)**

```sql
SELECT
    h.asset_id,
    MAX(h.asset_name) as name,
    SUM(h.cost_price_unit * h.quantity) as cost_basis,
    SUM(h.market_value) as market_val,
    SUM(h.market_value - (h.cost_price_unit * h.quantity)) as unrealized_pl,
    CASE WHEN SUM(h.cost_price_unit * h.quantity) > 0
         THEN ROUND(SUM(h.market_value - (h.cost_price_unit * h.quantity))
                   / SUM(h.cost_price_unit * h.quantity) * 100, 2)
         ELSE 0 END as return_pct
FROM holdings h
WHERE h.is_shadow = FALSE
GROUP BY h.asset_id
ORDER BY return_pct DESC
LIMIT 5;

-- Expected top: 易方达中证500ETF ~+45.6%, 示例沪深300A ~+32.0%
```

---

## Validation Checklist

### Backend Validation

- [ ] Endpoint `/api/performance/summary` returns 200
- [ ] Endpoint `/api/performance/gains` returns 200
- [ ] Endpoint `/api/performance/by-class` returns 200
- [ ] Response matches TypeScript interface (all fields present)
- [ ] Field types correct (numbers are numbers)
- [ ] Language correct: asset classes in "English (Chinese)" format

**Data Model Verification (Section F):**

- [ ] Ran pre-implementation verification query 1 (summary KPIs)
- [ ] Ran pre-implementation verification query 2 (top-class via taxonomy JOIN)
- [ ] Ran pre-implementation verification query 3 (per-asset return %)
- [ ] All 3 queries match expected results
- [ ] All required JOINs implemented (holdings → asset_registry → asset_taxonomy)
- [ ] No hardcoded `map_class()` or dict mappings used
- [ ] Zero cost_basis case handled (no divide-by-zero)

**Actual Response (Endpoint 1 - Summary):**

```json
// Backend agent: paste curl output here
```

**Actual Response (Endpoint 2 - Gains, first 3 items):**

```json
// Backend agent: paste curl output here
```

**Actual Response (Endpoint 3 - By Class):**

```json
// Backend agent: paste curl output here
```

**Data Sanity Check:**
Pick 2-3 specific assets and trace from DB to API output:

| Asset ID | DB cost_basis | DB market_value | API cost_basis | API market_value | API return_pct | Match? |
|----------|---------------|-----------------|----------------|------------------|----------------|--------|
| `<pick a CN fund holding>` | _____________ | _____________ | _____________ | _____________ | _____________ | ______ |
| `<pick another holding>` | _____________ | _____________ | _____________ | _____________ | _____________ | ______ |
| `<pick a property/illiquid asset>` | _____________ | _____________ | _____________ | _____________ | _____________ | ______ |

---

### Annotation Validation

- [ ] All Section C placeholder values marked with BIND
- [ ] Static values NOT marked
- [ ] Component structure matches Section D
- [ ] Loop structures identified for arrays (`assets[]`, `top_classes[]`, `sub_classes[]`)

**Binding Count:**

- Expected from Section B: 26 total bindings
- Actual BIND comments: ___

---

### Frontend Implementation Validation

- [ ] All 3 API endpoints called correctly (verify in Network tab)
- [ ] Real data displayed (no demo values remain)
- [ ] Formatting matches Section B (currency, percent, signed numbers)
- [ ] Empty state handled (if no holdings data)
- [ ] Error state handled (if API unreachable)
- [ ] KPI cards show real data (not Sharpe/Sortino/Alpha placeholders)
- [ ] P&L cards show CNY (¥) not USD ($)

**Data Quality Check:**

- [ ] Language consistent: "English (Chinese)" for asset classes
- [ ] Currency symbols correct: ¥ throughout (not $)
- [ ] Number formats correct: %, decimals, commas
- [ ] Signed numbers show +/- for P&L and return values
- [ ] Positive values green, negative values red

**Screenshot Evidence:**

| Element | Spec Example | Actual Displayed |
|---------|--------------|------------------|
| KPI Card 1 (Net Worth) | ¥3,500,000 | _____________ |
| KPI Card 2 (Unrealized P&L) | +¥450,000 (+14.75%) | _____________ |
| KPI Card 3 (Realized P&L) | +¥xxx,xxx (FIFO) | _____________ |
| KPI Card 4 (Lifetime P&L) | +¥x,xxx,xxx | _____________ |
| Realized Gains card | +¥xxx,xxx (FIFO) | _____________ |
| Top performer return | +45.60% | _____________ |
| Bottom performer return | -30.49% | _____________ |
| 房地产 class row | ¥1,800,000 / +20.00% | _____________ |

---

## Sign-off

- [ ] Backend validated by: _______________
- [ ] Annotation validated by: _______________
- [ ] Frontend validated by: _______________
- [ ] Architect sign-off: _______________

**Date:** _______________
**Ready for merge:** Yes / No

---

## Period Query Parameter Reference (V5.5.2)

All performance endpoints that accept a `period` query param normalise via `normalize_period()` in `src/api/routes/performance.py`. Accepted values:

| Accepted value | Normalises to | Notes |
|----------------|---------------|-------|
| `all`, `all_time` | `all_time` | Default when omitted |
| `12m`, `last_12m` | `last_12m` | |
| `36m`, `last_36m` | `last_36m` | |
| `1m`, `last_1m`, `30d` | `last_1m` | `last_1m` and `1m` aliases added V5.5.2 |

Unrecognised values fall back to `all_time`.

---

## Future Enhancements (Out of Scope for V1)

| Feature | Blocker | When |
|---------|---------|------|
| **TWR Calculation** | Needs daily portfolio snapshots (only 3 exist); `market_daily` table has OHLCV data but needs verification of depth | After daily snapshot cron active |
| **Risk Metrics (Sharpe/Sortino/Alpha)** | Needs historical returns series | After TWR available |
| **Benchmark Comparison (vs S&P 500)** | Needs benchmark data in `market_daily` | After market_daily populated |
| **Time Period Filtering (1M/3M/YTD)** | Needs snapshot depth for date-range queries | After daily snapshot cron active |
| **P&L by Time Period** | Needs snapshots at period boundaries | After daily snapshot cron active |
