# API Spec: Top Movers (Timeframe Windows + Level Toggle)

> Feature: Dashboard Top Movers card — price-driven movement over selectable windows (GitHub #27)
> Status: Draft (V7.2.x)
> Last Updated: 2026-07-04

**Goal**: answer "what moved my portfolio over the last 7d/30d/3m/6m/12m" at asset OR class level. This is *price-driven movement*, deliberately distinct from `/performance/gains` (lifetime P&L): flows (buys/sells) must not masquerade as movement.

## Endpoint

`GET /performance/movers?window=7d|30d|3m|6m|12m&level=asset|sub_class|top_class&limit=10`

- `window` (required): lookback from today. `window_start = today − {7,30,91,182,365} days`.
- `level` (default `asset`): aggregation level. Class levels use the canonical taxonomy join (`COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified')` for top_class — AGENTS.md Rule 7 pattern).
- `limit` (default 10, max 50): rows returned after sorting by `ABS(pl_impact_cny) DESC`.
- "All time" is NOT served here — the frontend keeps using `/performance/gains?period=all_time` for that tab.

## Semantics (price-ratio method — no FX, no flow contamination)

Per priced asset:
- `mv_now` = latest non-shadow holdings row per asset (**per-asset MAX(snapshot_date)**, never global), `market_value` already CNY.
- `p_now` = latest `market_daily.close` for `code = extract_symbol(asset_id)`;
  `p_then` = latest close ≤ `window_start`.
- `pct_change = (p_now/p_then − 1) × 100`
- `pl_impact_cny = mv_now × (1 − p_then/p_now)` — the CNY value change implied by the price move on the current position. Same-currency ratio → FX-free by construction.
- **Partial coverage**: if no close exists ≤ `window_start`, use the earliest available close and set `window_covered: false` (frontend shows a `~` marker). If the asset has <2 closes, exclude it.
- **Unpriced assets** (no `market_daily` rows: deposits, insurance, property, FS assets) are excluded — they are not market movers. Cash-equivalents excluded by the same rule.
- Approximation note (accepted): quantity changes inside the window attribute the full price move to the current position size.

Class levels aggregate the asset rows: `pl_impact_cny = Σ impact`, `pct_change = Σ impact / Σ (mv_now × p_then/p_now) × 100`, `window_covered = AND(children)`, `asset_count` included.

## Response

```json
{
  "window": "30d",
  "window_start": "2026-06-04",
  "level": "asset",
  "movers": [
    {
      "key": "US_STK_MSFT",            // asset_id | sub_class name | top_class name
      "name": "Microsoft",              // registry name; class name at class levels
      "top_class": "Equity (股票)",     // asset level only
      "sub_class": "US Equity",         // asset + sub_class levels
      "pct_change": -4.2,
      "pl_impact_cny": -12345.67,
      "market_value": 293000.0,
      "window_covered": true,
      "asset_count": 1                  // >1 at class levels
    }
  ],
  "excluded_unpriced_count": 24
}
```

Errors follow Rule 12 (`ApiErrorResponse` via `_errors.py`) — no silent `[]`-with-200 on failure. Invalid `window`/`level` → 422.

## Frontend (Movers card, `DashboardCards.tsx`)

- Timeframe pills: `7D · 30D · 3M · 6M · 12M · ALL` (default `30D`; `ALL` keeps today's gains-based rendering).
- Level segmented control: `类别 | 子类 | 资产` (top_class | sub_class | asset), default `资产`.
- Row: name + class chip, `pct_change` (signed, colored), `pl_impact_cny` (formatted ¥K), `~` prefix when `window_covered=false`.
- Component state only (no persistence). Loading/error states per existing card conventions.
