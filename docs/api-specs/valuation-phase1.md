# API Spec: Valuation Phase 1

> Feature: Valuation dashboard with dual-view holdings/index rows, persisted percentile history, and user watchlist support
> Status: Shipped (v5.3.1)
> Last Updated: 2026-04-26

**Routing note:** Frontend requests use `/api/valuation/...` via Vite proxy. FastAPI routes are implemented as `/valuation/...` with no `/api` prefix.

---

## Section A: API Contract

### Endpoint 1: Latest Valuation Snapshot

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/valuation/snapshot/latest` | Returns the latest valuation rows for holdings, tracked indexes, and watchlist items |

#### Response Type

```typescript
type ValuationRowKind = "holding" | "tracked_index" | "watchlist";

type ValuationSignal = "LOW" | "FAIR" | "HIGH" | "N/A";

interface ValuationSnapshotRow {
  id: number;
  snapshot_date: string;              // YYYY-MM-DD
  ticker: string;                     // e.g. "110020", "000300", "VOO", "HSTECH"
  display_name: string;               // Chinese display name for UI
  row_kind: ValuationRowKind;
  linked_ticker: string | null;       // Fund→index linkage, null otherwise
  asset_id: string | null;            // Canonical holdings asset_id for holding rows
  asset_class: string;                // CN_FUND | CN_INDEX | HK_INDEX | US_INDEX | US_STOCK | US_BOND_ETF
  pe_ttm: number | null;
  pe_forward: number | null;
  pb_ratio: number | null;
  peg_ratio: number | null;
  fcf_yield: number | null;
  dividend_yield: number | null;
  ev_ebitda: number | null;
  sec_yield: number | null;
  percentile_value: number | null;    // 0-100, null when no seeded history
  percentile_metric: string | null;   // pe_ttm | pe_forward | pb_ratio | sec_yield | dividend_yield
  pct_years: number | null;           // whole years of history backing percentile
  valuation_signal: ValuationSignal;
  signal_basis: string | null;
  rate_adjustment_factor: number | null;
  data_source: string | null;
  is_estimable: boolean;
  notes: string | null;
  created_at: string | null;          // ISO timestamp
}
```

#### Example Response

```json
[
  {
    "id": 101,
    "snapshot_date": "2026-04-24",
    "ticker": "110020",
    "display_name": "易方达沪深300ETF联接A",
    "row_kind": "holding",
    "linked_ticker": "000300",
    "asset_id": "CN_FUND_110020",
    "asset_class": "CN_FUND",
    "pe_ttm": null,
    "pe_forward": null,
    "pb_ratio": null,
    "peg_ratio": null,
    "fcf_yield": null,
    "dividend_yield": null,
    "ev_ebitda": null,
    "sec_yield": null,
    "percentile_value": null,
    "percentile_metric": null,
    "pct_years": 0,
    "valuation_signal": "N/A",
    "signal_basis": "holding_row_no_direct_pe",
    "rate_adjustment_factor": 1.0,
    "data_source": "none",
    "is_estimable": false,
    "notes": "holding_row_for_accounting_only",
    "created_at": "2026-04-24T10:02:15"
  },
  {
    "id": 102,
    "snapshot_date": "2026-04-24",
    "ticker": "000300",
    "display_name": "沪深300",
    "row_kind": "tracked_index",
    "linked_ticker": "110020",
    "asset_id": null,
    "asset_class": "CN_INDEX",
    "pe_ttm": 13.87,
    "pe_forward": null,
    "pb_ratio": 1.42,
    "peg_ratio": null,
    "fcf_yield": null,
    "dividend_yield": null,
    "ev_ebitda": null,
    "sec_yield": null,
    "percentile_value": 46.0,
    "percentile_metric": "pe_ttm",
    "pct_years": 14,
    "valuation_signal": "FAIR",
    "signal_basis": "pe_ttm within reference band",
    "rate_adjustment_factor": 1.0,
    "data_source": "akshare_index_pe",
    "is_estimable": true,
    "notes": null,
    "created_at": "2026-04-24T10:02:15"
  },
  {
    "id": 103,
    "snapshot_date": "2026-04-24",
    "ticker": "QQQ",
    "display_name": "Nasdaq 100 ETF",
    "row_kind": "watchlist",
    "linked_ticker": null,
    "asset_id": null,
    "asset_class": "US_INDEX",
    "pe_ttm": 34.41,
    "pe_forward": null,
    "pb_ratio": null,
    "peg_ratio": null,
    "fcf_yield": null,
    "dividend_yield": 0.62,
    "ev_ebitda": null,
    "sec_yield": null,
    "percentile_value": null,
    "percentile_metric": "pe_ttm",
    "pct_years": 0,
    "valuation_signal": "HIGH",
    "signal_basis": "pe_ttm above reference band",
    "rate_adjustment_factor": 0.88,
    "data_source": "yfinance_index_proxy",
    "is_estimable": true,
    "notes": "history_accumulating",
    "created_at": "2026-04-24T10:02:15"
  }
]
```

---

### Endpoint 2: Percentile Detail

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/valuation/percentile/{ticker}/{metric}` | Returns percentile detail for a specific ticker/metric using `valuation_history` |

#### Response Type

```typescript
interface ValuationPercentileResponse {
  ticker: string;
  metric: string;
  latest_value: number | null;
  percentile: number | null;         // 0-100
  years: number;                     // whole years of usable history
  sample_size: number;               // number of observations used
  source: string | null;
  has_seed_history: boolean;
  note: string | null;               // e.g. "history_accumulating"
}
```

#### Example Response

```json
{
  "ticker": "000300",
  "metric": "pe_ttm",
  "latest_value": 13.87,
  "percentile": 46.0,
  "years": 14,
  "sample_size": 5112,
  "source": "akshare_index_pe",
  "has_seed_history": true,
  "note": null
}
```

---

### Endpoint 3: Watchlist List

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/valuation/watchlist` | Returns user-curated valuation watchlist entries |

#### Response Type

```typescript
interface ValuationWatchlistItem {
  ticker: string;
  display_name: string;
  asset_type: "CN_INDEX" | "HK_INDEX" | "US_INDEX" | "US_STOCK";
  note: string | null;
  added_at: string;
}
```

#### Example Response

```json
[
  {
    "ticker": "QQQ",
    "display_name": "Nasdaq 100 ETF",
    "asset_type": "US_INDEX",
    "note": "Monitor for buy-in window",
    "added_at": "2026-04-24T10:00:00"
  }
]
```

---

### Endpoint 4: Watchlist Create

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/valuation/watchlist` | Adds a watchlist entry and triggers best-effort history seed |

#### Request Body

```typescript
interface CreateValuationWatchlistRequest {
  ticker: string;
  display_name: string;
  asset_type: "CN_INDEX" | "HK_INDEX" | "US_INDEX" | "US_STOCK";
  note?: string | null;
}
```

#### Response Type

```typescript
interface CreateValuationWatchlistResponse {
  ticker: string;
  status: "created" | "exists";
  backfill_status: "seeded" | "deferred" | "unsupported";
}
```

#### Example Response

```json
{
  "ticker": "QQQ",
  "status": "created",
  "backfill_status": "deferred"
}
```

---

### Endpoint 5: Watchlist Delete

| Method | Path | Description |
|--------|------|-------------|
| DELETE | `/api/valuation/watchlist/{ticker}` | Deletes a watchlist entry |

#### Response Type

```typescript
interface DeleteValuationWatchlistResponse {
  ticker: string;
  status: "deleted" | "not_found";
}
```

#### Example Response

```json
{
  "ticker": "QQQ",
  "status": "deleted"
}
```

---

## Section B: Data Binding Map

| UI Element | Location in Mockup | API Field | Format |
|------------|-------------------|-----------|--------|
| 页面标题“估值仪表盘”下更新时间 | Header subtitle | latest `created_at` across rows | datetime:date_only |
| 宏观说明条中的美国10年期收益率 | Macro banner | separate macro endpoint, existing contract | percent:2 |
| Holdings 区块行名称 | Holdings table | `display_name` | text:Chinese |
| Holdings 区块行“关联指数” | Holdings table secondary text | `linked_ticker` + linked row lookup | text |
| Holdings 区块当前估值 | Holdings table | primary metric from row | metric_label+number |
| Holdings 区块历史%位 | Holdings table | `percentile_value` | percent:0_or_null_text |
| Tracked Indexes 区块名称 | Tracked indexes table | `display_name` | text:Chinese |
| Tracked Indexes 当前估值 | Tracked indexes table | `pe_ttm` / `pb_ratio` / `sec_yield` | metric_label+number |
| Tracked Indexes 历史%位 | Tracked indexes table | `percentile_value` | percent:0 |
| Watchlist 名称 | Watchlist table | `display_name` | text |
| Watchlist 备注 | Watchlist table | `note` | text |
| 信号徽标 | Any row | `valuation_signal` | badge |
| 来源说明 | Any row | `data_source` | text:lowercase |
| 不可估值提示 | Any row | `is_estimable`, `notes` | text |

---

## Section C: Demo Data Markers

### Placeholder Values (MUST be replaced)

| Demo Value in Mockup | Replace With | Format |
|---------------------|--------------|--------|
| `沪深300 13.9x` | `display_name` + `pe_ttm` | text + number:1 |
| `46%` | `percentile_value` | percent:0 |
| `估值偏高` | `valuation_signal` | badge |
| `历史%位积累中` | derived from `percentile_value === null` and `notes` | text |
| `Nasdaq 100 ETF` | `display_name` | text |
| `Akshare / yfinance` | `data_source` | text |

### Static Values (DO NOT replace)

| Value | Reason |
|-------|--------|
| `估值仪表盘` | Page title |
| `持仓` / `跟踪指数` / `观察列表` | Section headers |
| Column headers | UI labels |
| `刷新数据` / `添加观察项` / `删除` | Button labels |

---

## Section D: Component Reference

### Main Component Structure

```text
┌──────────────────────────────────────────────────────────────────┐
│ 估值仪表盘                                最后更新: 2026-04-24  │
│ 宏观说明条: US10Y / 利率调整系数                                 │
├──────────────────────────────────────────────────────────────────┤
│ 持仓                                                             │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │ 名称 | 关联指数 | 当前估值 | 历史%位 | PB/收益率 | 信号     │ │
│ └──────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────┤
│ 跟踪指数                                                         │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │ 名称 | 来源持仓/观察项 | 当前估值 | 历史%位 | 来源 | 信号   │ │
│ └──────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────┤
│ 观察列表                                           [添加观察项] │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │ 名称 | 当前估值 | 历史%位 | 备注 | 删除                      │ │
│ └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### Styling Notes

- Use the same design-system card and table language as `BalanceSheet.tsx`.
- Avoid inline background colors for signal state; use badge styles and subtle row accents.
- Dates display as `YYYY-MM-DD`, not full ISO timestamps.
- Unsupported rows use muted explanatory text instead of blank cells where possible.

---

## Section E: Data Quality Requirements

### Language

- [x] Asset names: `Chinese` for CN/HK indexes and held CN funds, English permitted for US tickers/watchlist labels
- [x] Asset classes: `English` internal values, frontend maps to friendly Chinese labels
- [x] UI labels: `Chinese`

### Currency

- [x] Holdings context currency: `CNY` where monetary amounts appear elsewhere on page
- [x] Valuation metrics: raw metric units, no currency conversion for PE/PB/yield
- [x] Yield values: display as percent points with `%`

### Number Formatting

| Field | Type | Precision | Sign Display |
|-------|------|-----------|--------------|
| `pe_ttm` | multiple | 1 | never |
| `pe_forward` | multiple | 1 | never |
| `pb_ratio` | multiple | 2 | never |
| `sec_yield` | percent | 2 | never |
| `dividend_yield` | percent | 2 | never |
| `percentile_value` | percent | 0 | never |
| `pct_years` | integer | 0 | never |

### Explicit Empty-State Rules

- If no trustworthy PE-like metric exists, return metric fields as `null`, `is_estimable=false`, and a human-readable `notes` reason.
- If history exists only from recent accumulation, return `percentile_value=null`, `pct_years=0`, and `notes="history_accumulating"`.
- HSI/HSCEI remain price-only until a proven PE source exists; do not fabricate PE or percentile values.

---

## Section F: Data Model Reference

### Required Tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `holdings` | Authoritative position source for current held assets | `asset_id`, `asset_name`, `snapshot_date`, `is_shadow`, `market_value` |
| `valuation_snapshots` | Latest per-ticker valuation rows shown in UI | `ticker`, `snapshot_date`, `row_kind`, `display_name`, metric columns |
| `valuation_history` | Persisted metric time series for percentile computation | `ticker`, `metric`, `observed_date`, `value`, `source` |
| `valuation_watchlist` | User-curated non-held tracking set | `ticker`, `display_name`, `asset_type`, `added_at` |
| `valuation_reference` | Manual threshold bands for signal classification | `ticker`, `metric`, `low_threshold`, `high_threshold`, `rate_sensitive` |
| `market_sentiment_cache` | Macro rate input for existing rate adjustment logic | `indicator_key`, `value`, `updated_at` |

### Required JOINs / Source Paths

```sql
-- Held assets originate from latest authoritative holdings rows
WITH latest_per_asset AS (
  SELECT asset_id, MAX(snapshot_date) AS max_date
  FROM holdings
  WHERE is_shadow = FALSE
  GROUP BY asset_id
)
SELECT h.asset_id, h.asset_name, h.snapshot_date
FROM holdings h
JOIN latest_per_asset l
  ON h.asset_id = l.asset_id
 AND h.snapshot_date = l.max_date
WHERE h.is_shadow = FALSE;

-- Latest valuation page rows come from valuation_snapshots only
WITH latest AS (
  SELECT ticker, row_kind, MAX(snapshot_date) AS max_date
  FROM valuation_snapshots
  GROUP BY ticker, row_kind
)
SELECT vs.*
FROM valuation_snapshots vs
JOIN latest l
  ON vs.ticker = l.ticker
 AND vs.row_kind = l.row_kind
 AND vs.snapshot_date = l.max_date;

-- Percentile detail comes from valuation_history, not valuation_snapshots
SELECT observed_date, value, source
FROM valuation_history
WHERE ticker = ? AND metric = ?
ORDER BY observed_date ASC;
```

### Data Derivation Logic

| Output Field | Source | Calculation |
|--------------|--------|-------------|
| `row_kind` | collector routing | `holding`, `tracked_index`, or `watchlist` |
| `display_name` | holdings/watchlist mapping | Canonical display label for UI |
| `linked_ticker` | fund→index mapping or inverse linkage | Direct mapping field, not inferred on frontend |
| `percentile_value` | `valuation_history.value` series | `compute_percentile(series, latest_value)` |
| `pct_years` | `valuation_history.observed_date` range | whole-year span of usable history |
| `valuation_signal` | current metric + `valuation_reference` | `classify_signal(...)` |

### Known Data Model Gotchas

- **Do not use global `MAX(snapshot_date)` on `holdings`.** Held asset extraction must use per-asset latest logic.
- **`valuation_snapshots` is for latest rows, not long history.** Percentiles must read from `valuation_history`.
- **Holding rows and tracked-index rows are both needed.** Funds stay in the holdings view for accounting context even when the valuation signal comes from a mapped index row.
- **Missing historical seed is not an error.** US broad indexes and some CN/HK assets may legitimately return `percentile_value=null` during accumulation.

### Pre-Implementation Verification Query

```sql
WITH latest_per_asset AS (
  SELECT asset_id, MAX(snapshot_date) AS max_date
  FROM holdings
  WHERE is_shadow = FALSE
  GROUP BY asset_id
)
SELECT h.asset_id, h.asset_name, h.snapshot_date, h.market_value
FROM holdings h
JOIN latest_per_asset l
  ON h.asset_id = l.asset_id
 AND h.snapshot_date = l.max_date
WHERE h.is_shadow = FALSE
  AND (h.asset_id LIKE 'CN_FUND_%' OR h.asset_id LIKE 'US_STK_%')
ORDER BY h.asset_id
LIMIT 10;
```

Expected:
- latest active CN fund and US stock rows only
- no shadow rows
- enough samples to drive holding-row generation in valuation refresh

---

## Validation Checklist

### Backend Validation

- [ ] `GET /valuation/snapshot/latest` returns 200
- [ ] `GET /valuation/percentile/{ticker}/{metric}` returns 200
- [ ] `GET /valuation/watchlist` returns 200
- [ ] `POST /valuation/watchlist` validates request shape
- [ ] `DELETE /valuation/watchlist/{ticker}` is idempotent
- [ ] Response matches TypeScript interfaces
- [ ] No fabricated percentile values where no seed history exists

**Data Model Verification (Section F):**
- [ ] Ran pre-implementation verification query
- [ ] Holding extraction uses per-asset latest logic
- [ ] Percentile route reads `valuation_history`
- [ ] No frontend-only hardcoded tracked-index rows

**Actual Response:**
```json
// Backend agent: paste curl output here
```

### Annotation Validation

- [ ] All Section C placeholder values marked with BIND
- [ ] Static labels not marked
- [ ] Three sections exist: holdings, tracked indexes, watchlist

### Frontend Implementation Validation

- [ ] API calls match Section A
- [ ] Data formatting matches Section B/E
- [ ] `null` percentile shows honest accumulation copy
- [ ] Unsupported rows show explanatory text
- [ ] All buttons are wired or explicitly disabled

**Screenshot Evidence:**

| Element | Spec Example | Actual Displayed |
|---------|--------------|------------------|
| Tracked index row | `沪深300 13.9x 46% FAIR` | _____________ |
| Watchlist row | `Nasdaq 100 ETF` | _____________ |
| Unsupported row copy | `历史%位积累中` | _____________ |

---

## Sign-off

- [x] Backend validated by: Claude Code (2026-04-26)
- [ ] Frontend validated by: _______________
- [ ] Architect sign-off: _______________

**Date:** 2026-04-26
**Ready for merge:** Yes / No

---

## v5.3.1 Addenda

### 10-year percentile window (changed in v5.3.1)

`_get_history_percentile` now defaults to `years=10` (was: full history ~21yr). The `/valuation/percentile/{ticker}/{metric}` endpoint accepts `?years=N` (default 10, `0`=full history). Response now includes `window_years` (actual years of data in window).

`pct_years` in snapshot rows reflects the filtered window — will show `~10` for mature indexes, not `21`.

### Percentile-based signal (new in v5.3.1)

`classify_signal` now accepts an optional `percentile` parameter. When supplied, it takes priority:
- `percentile ≥ 75` → **HIGH**
- `percentile ≤ 25` → **LOW**
- else → **FAIR**

Absolute PE thresholds remain fallback when `percentile=None` (used for US individual stocks with no seeded history).

`signal_basis` in snapshot rows will contain e.g. `pe_ttm_pct 85th ≥ 75th` when percentile path is taken.

### S&P500 + Nasdaq100 tracked indexes (new in v5.3.1)

Two new `tracked_index` rows added to `/valuation/snapshot/latest`:
- `ticker="S&P500"`, `asset_class="US_INDEX"`, PE from yfinance on SPY
- `ticker="Nasdaq100"`, `asset_class="US_INDEX"`, PE from yfinance on QQQ

Historical PE seeded from `multpl.com` (monthly, free). After seeding, `pct_years` will show `~10` and `valuation_signal` will be percentile-based.

### PB history for CN broad indexes (in v5.3.0 P0-B)

CN broad indexes (沪深300, 中证500, 上证50) now have PB history seeded via `ak.stock_index_pb_lg`. `pb_pct` column in snapshot rows populated after refresh.
