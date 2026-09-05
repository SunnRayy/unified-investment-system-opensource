# Verification Report API Specification

> Status: Production (V4.2 — Batch 8 overhaul)
> Last Updated: 2026-03-17

## Overview

API endpoints for accessing monthly verification reports with adoption rate, drift, verdict breakdown, and trend history. The `/verification/latest` endpoint auto-computes a fresh report if no result was stored within the last 24 hours.

**Frontend page**: Verification Dashboard (Decision Hub section)
**Key tables**: `verification_logs`, `insights`, `trade_logs`

---

## Endpoints

### GET /verification/latest

**Description:** Get the most recent verification report. Auto-computes fresh if none exists within 24 hours.

**Response (full shape from `verification_service.compute_verification_report()`):**

```json
{
  "adoption_rate": 75.0,
  "adoption_rate_by_model": {
    "gemini": 80.0,
    "claude": 70.0,
    "committee": 75.0
  },
  "total_insights": 47,
  "portfolio_return": 3.2,
  "benchmark_return": 2.1,
  "alpha": 1.1,
  "max_allocation_drift": 7.5,
  "drift_details": [
    {
      "asset_class": "US Equity",
      "current_pct": 27.5,
      "target_pct": 20.0,
      "deviation_pct": 7.5
    }
  ],
  "period": {
    "start": "2026-03-01",
    "end": "2026-03-17"
  },
  "verdict_breakdown": [
    { "verdict": "WIN",     "count": 8, "pct": 66.7 },
    { "verdict": "LOSS",    "count": 4, "pct": 33.3 },
    { "verdict": "PENDING", "count": 2, "pct": 0.0  }
  ],
  "adoption_history": [
    {
      "month": "2026-01",
      "adoption_rate": 60.0,
      "total_insights": 15
    },
    {
      "month": "2026-02",
      "adoption_rate": 72.0,
      "total_insights": 18
    }
  ]
}
```

**Caching behavior**: If a `verification_logs` row exists with `created_at` within last 24 hours, returns cached KPIs + freshly-computed trend arrays. If stale or missing, computes and persists a new report.

---

### POST /verification/run

**Description:** Force a fresh verification computation, bypassing the 24-hour cache. Persists result to `verification_logs`.

**Response**: Same shape as `GET /verification/latest`.

---

### GET /verification/trends

**Description:** Monthly adoption rate trend from `insights` table (richer than `verification_logs` alone — covers months without a persisted verification run).

**Response:**

```json
{
  "periods": [
    {
      "period_start": "2026-01-01",
      "period_end": "2026-01-31",
      "adoption_rate": 60.0,
      "portfolio_return": null,
      "benchmark_return": null,
      "alpha": null,
      "max_drift": null,
      "total_insights": 15
    }
  ]
}
```

---

### GET /verification/history

**Description:** List of persisted `verification_logs` records (stored by `POST /verification/run` or auto-compute).

**Query Parameters:**

- `limit` (int, default: 12, max: 100): Maximum records to return

**Response:**

```json
[
  {
    "verification_date": "2026-03-01",
    "verification_type": "monthly",
    "period_start": "2026-03-01",
    "period_end": "2026-03-17",
    "adoption_rate": 75.0,
    "max_allocation_drift": 7.5,
    "total_insights": 47
  }
]
```

---

## Frontend Usage — Verification Dashboard (Batch 8)

The Verification Dashboard page calls these endpoints to display:

| UI Component | Endpoint | Field |
|-------------|----------|-------|
| Adoption Rate KPI | `/verification/latest` | `adoption_rate` |
| By-Model Breakdown | `/verification/latest` | `adoption_rate_by_model` |
| Insights Count KPI | `/verification/latest` | `total_insights` |
| Max Drift KPI | `/verification/latest` | `max_allocation_drift` |
| Drift Details Table | `/verification/latest` | `drift_details` |
| Verdict Breakdown (pie/bar) | `/verification/latest` | `verdict_breakdown` (sorted DESC by count) |
| Monthly Adoption Trend Chart | `/verification/trends` | `periods[].adoption_rate` |
| Alpha KPI | `/verification/latest` | `alpha` |

---

## Change Log

| Date | Change |
|------|--------|
| 2026-02-01 | Initial spec (GET /latest + /history only) |
| 2026-03-17 | Batch 8 overhaul: added `verdict_breakdown`, `adoption_history`, `period` to /latest response; added POST /verification/run; added GET /verification/trends; updated to reflect auto-compute caching behavior; updated adoption_rate_by_model keys to actual AI model names |
