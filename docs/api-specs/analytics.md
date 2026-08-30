# API Spec: Analytics

> Feature: Advanced portfolio analytics — Monte Carlo projection, cash flow analysis, and goal tracking
> Status: Implemented
> Last Updated: 2026-05-04

---

## Section A: API Contract

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics/projection` | Monte Carlo portfolio projection (N simulations) |
| GET | `/analytics/projection/defaults` | Historically-derived defaults for projection inputs |
| GET | `/analytics/cashflow-trends` | Monthly income/expense trends from Financial Summary |
| GET | `/analytics/cashflow-forecast` | Forecast future monthly income and expenses |
| GET | `/analytics/goals` | List all financial goals |
| POST | `/analytics/goals` | Create a new financial goal |
| DELETE | `/analytics/goals/{goal_id}` | Delete a goal |
| GET | `/analytics/goals/{goal_id}/probability` | Calculate probability of reaching a specific goal |

### GET /analytics/projection — Query Params

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `years` | int | 10 | Projection horizon in years |
| `monthly_contribution` | float | from defaults | Monthly investment amount (CNY) |
| `expected_return` | float | from defaults | Annual expected return (e.g. 0.07 = 7%) |
| `volatility` | float | from defaults | Annual volatility (e.g. 0.15 = 15%) |
| `simulations` | int | 1000 | Number of Monte Carlo paths |

```typescript
interface ProjectionResponse {
  percentiles: {
    p10: number[];    // pessimistic path, one value per year
    p50: number[];    // median path
    p90: number[];    // optimistic path
  };
  years: number[];
  current_value: number;   // CNY, starting portfolio value
}
```

### GET /analytics/projection/defaults

> Refreshed 2026-07-25 (R-5, internal implementation notes) —
> the previous `ProjectionDefaults` shape below predated the current implementation
> (`src/api/routes/analytics.py::get_projection_defaults`) and no longer matched it.

Historically-derived defaults for the Simulation Parameters what-if tool on the
Forecast & Planning page ("Your Path" tab). Delegates to
`src.financial_analysis.projection_defaults` (single source of truth shared with
the North Star glide path and `GET /forecast/levers`) for the return/run-rate
figures, and to `calculate_portfolio_metrics` for volatility — the exact same
call `GET /forecast/levers` uses, so the two endpoints never disagree on
volatility.

Both averaging windows are anchored to the **latest DATA month** present in
`income_expense_monthly`, never to `date.today()` — the FS Excel ledger lags
real time by 1-2 months, so a naive "today minus 12 calendar months" window
silently drops the most recent, most relevant months.

```typescript
interface ProjectionDefaults {
  /** Trailing annualized TWR, rebalanceable-only basis (decimal, e.g. 0.1083 = 10.83%).
   *  Null if TWR cannot be computed. */
  suggested_return: number | null;
  /** Trailing annualized volatility, rebalanceable-only basis (decimal). Null if
   *  volatility cannot be computed (e.g. insufficient price history). */
  suggested_volatility: number | null;
  /** Average monthly new-money investment over the trailing 12 data months (CNY).
   *  Gross figure — includes recycled/reallocated capital, unlike
   *  suggested_contribution_run_rate below. Always a number (0.0 if no data). */
  avg_monthly_investment_12m: number;
  /** Same as above, trailing 36 data months (CNY). Always a number. */
  avg_monthly_investment_36m: number;
  /** The SAME contribution run-rate the North Star glide path and
   *  `GET /forecast/levers` use: (net_external_ttm + rsu_retained_ttm) / 12
   *  (ADR-025 §5.2), via `src.services.north_star_glide._contribution_run_rate`.
   *  Null when that function's status is not "available" (e.g. no
   *  income_expense_monthly data, or the sanity guard fired) — never a
   *  fabricated 0. */
  suggested_contribution_run_rate: number | null;
}
```

**Example response** (live, 2026-07-25):

```json
{
  "suggested_return": 0.1083,
  "suggested_volatility": 0.1786,
  "avg_monthly_investment_12m": 109674.0,
  "avg_monthly_investment_36m": 58335.0,
  "suggested_contribution_run_rate": 44665.23
}
```

### GET /analytics/cashflow-trends

```typescript
interface CashflowTrendsResponse {
  months: string[];       // YYYY-MM
  income: number[];       // CNY
  expense: number[];      // CNY
  net: number[];          // income - expense
}
```

### GET /analytics/cashflow-forecast — Query Params

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `months` | int | 12 | Months ahead to forecast |

```typescript
interface CashflowForecastResponse {
  months: string[];
  income_forecast: number[];
  expense_forecast: number[];
}
```

### Goals CRUD

```typescript
// POST /analytics/goals — Request
interface GoalCreate {
  name: string;
  target_amount: number;    // CNY
  target_date: string;      // YYYY-MM-DD
  current_amount?: number;  // CNY, defaults to 0
}

// GET /analytics/goals — Response
interface GoalsResponse {
  goals: Goal[];
}
interface Goal {
  id: number;
  name: string;
  target_amount: number;
  target_date: string;
  current_amount: number;
  progress_pct: number;     // 0–100
}

// GET /analytics/goals/{goal_id}/probability — Response
interface GoalProbabilityResponse {
  goal_id: number;
  probability: number;      // 0.0–1.0
  required_monthly: number; // CNY, to reach goal at p50
  shortfall: number;        // CNY at p50 if goal is missed
}
```

---

## Section B: Key Behaviours

- **Monthly contribution default** is derived from the average of `投资理财_*` columns in `income_expense_monthly` — it reflects actual historical investment activity, not user input.
- **Monte Carlo** uses log-normal returns with annual drift and volatility parameters. Each path is an independent simulation.
- **Goal probability** runs a Monte Carlo against each goal's target amount and date.

---

## Section C: Router Registration

```python
# src/api/main.py
app.include_router(analytics_router, prefix="/analytics")
```
