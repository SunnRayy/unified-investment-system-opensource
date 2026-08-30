# API Spec: Forecast Levers (R-2/R-5, W-1/W-2/W-3)

> Feature: Sensitivity grid answering "what do I change to reach the goal sooner?" — the
> savings / return / volatility levers that power the "What moves your date" section of
> the Forecast & Planning page ("Your Path" tab).
> Status: Implemented
> Last Updated: 2026-07-26

---

## Overview

internal implementation notes (R-1/R-2/R-5) and
internal implementation notes (W-1/W-2/W-3). The Forecast
page used to run two disagreeing math engines (a deterministic glide path at the raw
arithmetic return, and a Monte Carlo simulation) and show both. This endpoint is the
single deterministic engine, evaluated at the **volatility-drag-adjusted median return**
`g = exp(ln(1+r) − σ²/2) − 1` instead of the raw arithmetic return `r` — see
`ADR-026-median-basis-forecast-engine.md` for the full rationale (including the
2026-07-26 amendment, which documents the goal-source fix, the crossing-time
percentiles, and the accuracy trade-off behind `base.crossing_years`). Monte Carlo is not
used here at all (that stays cheap: no simulation in the hot path), and continues to
power the P10–P90 fan chart and per-goal probability elsewhere on the page.

Implementation: `src/services/forecast_levers.py::compute_levers`. Read-only, no writes.
Router: `src/api/routes/forecast.py` (prefix `/forecast`).

---

## Section A: API Contract

### Endpoint

| Method | Path | Description |
|--------|------|-------------|
| GET | `/forecast/levers` | Base case + savings/return/volatility sensitivity grid |

### Query parameters (W-2, all optional)

| Param | Range | Meaning |
|---|---|---|
| `savings_pct` | 0–60 | % increase over the current monthly-contribution run-rate (slider position; UI step 5) |
| `return_pp` | 0–6 | Percentage points added to expected return (slider position; UI step 0.5) |
| `volatility_pp` | 0–10 | Percentage points subtracted from volatility, floored above zero (slider position; UI step 0.5) |

- **All three omitted** (the default) → the response is byte-for-byte identical to the
  pre-W-2 shape — no `applied` key at all. Every other input is still derived live from
  the database (see "Derivation" below). No caching; safe to call on every page load.
- **Any subset supplied** → each supplied param adds exactly one extra row to its lever
  list (`levers.savings`/`levers.return`/`levers.volatility`), at the slider position,
  and `combined` is recomputed at the joint position of whichever params were supplied
  (unsupplied levers fall back to their first preset step, unchanged). Values are
  clamped server-side to the ranges above and echoed back in a top-level `applied` block
  so a clamped request stays visible to the caller — never silently substituted.
- **Out-of-range values** (e.g. `savings_pct=200`) → 422 from FastAPI's `Query`
  validation, before `compute_levers` runs at all.
- This is deliberately still server-side, closed-form math — not a client-side
  reimplementation of `years_to_target`. The frontend contains no projection math; it
  debounces slider input ~150ms and holds the previous response in flight while a new
  one is computed (single-digit-millisecond call, no spinner needed).

### Response Type

```typescript
interface ForecastLeversBase {
  current_nw: number | null;         // liquid (rebalanceable) net worth, CNY
  expected_return: number | null;    // trailing annualized TWR (decimal, e.g. 0.1083)
  volatility: number | null;         // trailing annualized volatility (decimal)
  /** Volatility-drag-adjusted median return: exp(ln(1+r) - volatility^2/2) - 1.
   *  The headline years_to_target below is computed at THIS rate, not
   *  expected_return. */
  median_return: number | null;
  monthly_contribution: number | null;  // current run-rate, CNY/mo (0.0 when unavailable)
  target: number | null;             // resolved goal amount, CNY — see top-level `goal` for provenance
  years_to_target: number | null;    // the headline number
  /**
   * W-3 (ADR-026 amendment): analytic crossing-TIME percentiles — for each p,
   * the year t at which P[value(t) >= target] = p/100. NOT first-passage time
   * (systematically earlier); see ADR-026 amendment §(b) for the exact
   * definition and its ~5-9% tail error vs. Monte Carlo. null when
   * expected_return or volatility is unavailable, or a percentile's crossing
   * year would exceed the 60-year solver horizon (never fabricated).
   * Ascending by construction (p25 <= p50 <= p75) — no reordering needed on
   * the frontend, unlike the pre-W-3 percentile-value-path approximation.
   */
  crossing_years: { p25: number | null; p50: number | null; p75: number | null };
}

/**
 * W-1 (ADR-026 amendment §(a)): the resolved forecast target and its
 * provenance. Every consumer of `base.target` should read this object to
 * know WHERE that number came from — never assume it's the config value.
 */
interface ForecastGoal {
  target_amount: number;             // same value as base.target; never null
  source: "goals" | "config_fallback";
  goal_id: number | null;            // null when source === "config_fallback"
  name: string | null;               // null when source === "config_fallback"
  target_date: string | null;        // ISO YYYY-MM-DD; null when source === "config_fallback"
  fallback_reason: string | null;    // null when source === "goals"; e.g. "no active retirement goal"
}

/** W-2: echoes back the clamped slider values actually used, one key per
 *  supplied param (unsupplied params stay null). Present on the response
 *  ONLY when at least one of savings_pct/return_pp/volatility_pp was
 *  supplied on the request — omitted entirely otherwise (this is what makes
 *  the no-params response byte-identical to the pre-W-2 shape). */
interface ForecastLeversApplied {
  savings_pct: number | null;
  return_pp: number | null;
  volatility_pp: number | null;
}

/** One row of the "Save more" lever. monthly_contribution is the lever's
 *  contribution level (a step above the base run-rate), not the base itself. */
interface ForecastSavingsLeverRow {
  label: string;                     // e.g. "+25% (¥37,500/mo)" — pre-formatted, do not re-derive
  monthly_contribution: number | null;
  years_to_target: number | null;
  delta_years: number | null;        // years_to_target - base.years_to_target (negative = sooner)
}

/** One row of the "Earn more" lever. */
interface ForecastReturnLeverRow {
  label: string;                     // e.g. "+1pp"
  expected_return: number | null;    // base.expected_return + step, BEFORE median-return adjustment
  years_to_target: number | null;
  delta_years: number | null;
}

/** One row of the "Take less risk" lever. */
interface ForecastVolatilityLeverRow {
  label: string;                     // e.g. "-5pp"
  volatility: number | null;         // base.volatility - step, floored above 0
  years_to_target: number | null;
  delta_years: number | null;
}

/** First step of all three levers applied together. */
interface ForecastCombinedLever {
  label: string;                     // e.g. "+25% savings, +1pp return, -5pp volatility"
  years_to_target: number | null;
  delta_years: number | null;
}

interface ForecastLevers {
  base: ForecastLeversBase;
  levers: {
    /** 3 preset rows (+25%/+50%/+100% of run-rate), PLUS one extra row at
     *  the end when savings_pct was supplied on the request (4 rows total
     *  in that case). Same pattern for return/volatility below. */
    savings: ForecastSavingsLeverRow[];    // 3 rows, +1 if savings_pct supplied
    return: ForecastReturnLeverRow[];      // 2 rows, +1 if return_pp supplied
    volatility: ForecastVolatilityLeverRow[]; // 2 rows, +1 if volatility_pp supplied
  };
  combined: ForecastCombinedLever;
  /** W-1 (ADR-026 amendment §(a)) — resolved goal + provenance. Always present. */
  goal: ForecastGoal;
  /** W-2 — present ONLY when at least one slider param was supplied on the request. */
  applied?: ForecastLeversApplied;
}
```

### Example Response, no query params (live, 2026-07-26)

```json
{
  "base": {
    "current_nw": 3500000.00,
    "expected_return": 0.10832,
    "volatility": 0.1786,
    "median_return": 0.090784,
    "monthly_contribution": 30000.00,
    "target": 20000000.0,
    "years_to_target": 11.75,
    "crossing_years": { "p25": 8.76, "p50": 11.75, "p75": 16.25 }
  },
  "levers": {
    "savings": [
      { "label": "+25% (¥37,500/mo)", "monthly_contribution": 37500.00, "years_to_target": 10.67, "delta_years": -1.08 },
      { "label": "+50% (¥45,000/mo)", "monthly_contribution": 45000.00, "years_to_target": 9.78, "delta_years": -1.97 },
      { "label": "+100% (¥60,000/mo)", "monthly_contribution": 60000.00, "years_to_target": 8.4, "delta_years": -3.35 }
    ],
    "return": [
      { "label": "+1pp", "expected_return": 0.11832, "years_to_target": 11.08, "delta_years": -0.67 },
      { "label": "+2pp", "expected_return": 0.12832, "years_to_target": 10.49, "delta_years": -1.26 }
    ],
    "volatility": [
      { "label": "-5pp", "volatility": 0.1286, "years_to_target": 11.17, "delta_years": -0.58 },
      { "label": "-8pp", "volatility": 0.0986, "years_to_target": 10.93, "delta_years": -0.82 }
    ]
  },
  "combined": {
    "label": "+25% savings, +1pp return, -5pp volatility",
    "years_to_target": 9.67,
    "delta_years": -2.08
  },
  "goal": {
    "target_amount": 20000000.0,
    "source": "goals",
    "goal_id": 2,
    "name": "FIRE",
    "target_date": "2040-12-31",
    "fallback_reason": null
  }
}
```

(Verified 2026-07-26 via `curl -s http://localhost:8008/forecast/levers` against the live
local backend — figures above are copied verbatim from that response, not hand-typed.
`base.crossing_years` and `goal` are new since the 2026-07-25 version of this spec, W-1/W-3.)

### Example Response, with slider params (live, 2026-07-26)

`GET /forecast/levers?savings_pct=25&return_pp=2&volatility_pp=3` — same shape as above,
plus one extra row per lever list and the `applied` block. Only the changed tail is shown:

```json
{
  "levers": {
    "volatility": [
      { "label": "-5pp", "volatility": 0.1286, "years_to_target": 11.17, "delta_years": -0.58 },
      { "label": "-8pp", "volatility": 0.0986, "years_to_target": 10.93, "delta_years": -0.82 },
      { "label": "-3pp", "volatility": 0.1486, "years_to_target": 11.37, "delta_years": -0.38 }
    ]
  },
  "combined": {
    "label": "+25% savings, +2pp return, -3pp volatility",
    "years_to_target": 9.35,
    "delta_years": -2.4
  },
  "applied": { "savings_pct": 25.0, "return_pp": 2.0, "volatility_pp": 3.0 }
}
```

An out-of-range value, e.g. `GET /forecast/levers?savings_pct=200`, returns **422** from
FastAPI's `Query(ge=0, le=60)` validation before `compute_levers` ever runs.

---

## Section B: Nullability — every numeric field can be `null`

This is a hard contract, not a suggestion: **every numeric field in this response can be
`null`**, and frontend callers must render an em-dash, never fabricate a `0` or a fake
year count. The reasons differ by input:

| Field | Null when | Why not fabricate |
|---|---|---|
| `base.current_nw` | Never in practice (net worth always resolves to a number, possibly 0) | — |
| `base.expected_return` | Insufficient TWR history (e.g. new account, no snapshots) | A guessed return would silently mislead every downstream lever |
| `base.volatility` | `calculate_portfolio_metrics` can't compute (insufficient price history) | Same — volatility drag math requires a real σ |
| `base.median_return` | Either `expected_return` or `volatility` is null | It's a pure function of the other two; can't be computed from one |
| `base.years_to_target` | `median_return` is null, OR the target is unreachable within the 60-year solver horizon used by `months_to_target` | "Not reachable" and "can't compute" are different facts and must not collapse to the same 0 |
| `base.crossing_years.p25`/`.p50`/`.p75` (W-3) | `expected_return` or `volatility` is null, OR that percentile's crossing year would exceed the 60-year solver horizon | Same "not reachable" vs. "can't compute" distinction as `years_to_target`, per percentile |
| every `*LeverRow.years_to_target` / `delta_years` | Same as `base.years_to_target` for that row's adjusted inputs | Same reasoning, per-row |
| `base.monthly_contribution` | Never null — falls back to `0.0` when the run-rate is unavailable (`_contribution_run_rate` status != `"available"`), matching the glide-path's own zero-fallback convention | This one field intentionally has a different contract: `0.0` is itself a meaningful, actionable value ("you're contributing nothing"), not a missing-data placeholder |
| `goal.target_amount` | Never null (always a real number, possibly the config fallback) | The whole point of `goal_resolver.py` is to never return a missing target — see `goal.source` for provenance |
| `goal.goal_id` / `.name` / `.target_date` | null when `goal.source === "config_fallback"` | There is no `goals` row to report these from |
| `goal.fallback_reason` | null when `goal.source === "goals"` | Only meaningful when explaining why the fallback fired |

## Section C: Configuration vs. derived — what's a constant, what's live

Per the plan's §4b hard requirement ("nothing static"), it matters which numbers in this
response are configuration and which are computed from live data:

- **Configuration (lever step sizes)** — plan-specified constants in
  `src/services/forecast_levers.py`: `_SAVINGS_STEPS_PCT = (25, 50, 100)` (percent of
  current run-rate), `_RETURN_STEPS_PP = (1, 2)` (percentage points),
  `_VOLATILITY_STEPS_PP = (5, 8)` (percentage points, floored above zero), plus the W-2
  slider *ranges* (not values) `_SAVINGS_PCT_RANGE = (0, 60)`, `_RETURN_PP_RANGE = (0, 6)`,
  `_VOLATILITY_PP_RANGE = (0, 10)`. These are the *sizes of the hypothetical* — "what if
  you saved 25% more" — and are allowed to be literals because they define the
  sensitivity grid itself, not a result.
- **Derived (everything else)** — `base.*` (including `crossing_years`), every
  `years_to_target`/`delta_years`, every lever row's adjusted input
  (`monthly_contribution`, `expected_return`, `volatility`), the `applied` echo when
  slider params are supplied, and `goal.*` are all computed live from the database on
  every request: current liquid net worth, trailing TWR, trailing volatility, the
  contribution run-rate, and the resolved goal target
  (`src.services.goal_resolver.resolve_north_star_goal` since W-1 — **not**
  `src.services.verification_config` directly; see ADR-026 amendment §(a)). None of these
  are hardcoded, and a regression test asserts the headline changes when any of the five
  underlying inputs changes (see the internal implementation plan
  §4b).

---

## Section D: Router Registration

`forecast_router` (defined with `prefix="/forecast"` in `src/api/routes/forecast.py`)
is part of `ALL_ROUTERS` in `src/api/main.py`, mounted both unprefixed
(`/forecast/levers`, local-dev convenience) and under `/api`
(`/api/forecast/levers` — the cloud-safe surface once bearer-token auth is on; see the
`_is_cloud_run` / `_auth_enabled` gating comment above `ALL_ROUTERS` in that file). The
frontend (`ux-command-center/src/services/api/forecast.ts`) always calls the `/api`-
prefixed path via `API_BASE = '/api'`, which the Vite dev proxy and the cloud
reverse-proxy both resolve to this same router either way.
