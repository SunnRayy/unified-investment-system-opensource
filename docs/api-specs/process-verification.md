# API Spec: Process Verification (F1.2/1.3)

> Feature: Rule-bucket-based process scoring for trade decisions — PASS/FAIL/UNSCORED for value trades; Compliant/Violation for non-value trades; quarterly outcome report.
> Status: Implemented (V7.4.0, flag-gated: `process_verification.enabled` in `config/verification.yaml`)
> Last Updated: 2026-07-08

---

## Overview

PRD 2026-07-07 F1. Trades are classified into one of four buckets
(`value` / `ratio` / `liquidity` / `compliance`); the scorer evaluates process quality
at decision time rather than short-horizon price outcome.

**Flag gate**: while `process_verification.enabled = false` (default), the scorer
returns byte-identical output to the V7.3.0 baseline. Flip to `true` after owner
CSV sign-off.

Key tables: `trade_logs` (extended with `rule_bucket`, `process_authorized`,
`process_params_ok`, `process_data_verified`, `process_checked_at`, `process_notes`,
`verdict_archived`, `memo_id`, `order_origin`), `metric_catalog`.

---

## Section A: API Contract

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| PUT | `/api/ai-advisor/trades/{trade_id}/process-checks` | Set process check flags for a trade (F1.2) |
| GET | `/api/decisions/quarterly-outcome-report` | Value-bucket entry/exit decisions vs. price outcome, grouped by memo (F1.3) |

---

### PUT `/api/ai-advisor/trades/{trade_id}/process-checks`

**Path params:**
- `trade_id` (int) — `trade_logs.id`

**Request Body:**

```typescript
interface ProcessChecksRequest {
  authorized?: boolean | null;     // Was the trade authorized per the investment framework?
  params_ok?: boolean | null;      // Were size/timing/price parameters within policy?
  data_verified?: boolean | null;  // Was the supporting data verified before execution?
  notes?: string | null;           // Free-form notes
}
```

At least one field must be non-null (422 if all are null — a no-op call must not
stamp `process_checked_at` falsely).

**Response (200):**

```typescript
interface ProcessChecksResponse {
  id: number;
  rule_bucket: string | null;         // "value" | "ratio" | "liquidity" | "compliance"
  memo_id: string | null;
  process_authorized: boolean | null;
  process_params_ok: boolean | null;
  process_data_verified: boolean | null;
  process_checked_at: string | null;  // ISO-8601 timestamp
  process_notes: string | null;
  updated_at: string | null;
}
```

**Error modes:**
- 404 — `trade_id` not found
- 422 — all fields are null
- 500 — unexpected server error

---

### GET `/api/decisions/quarterly-outcome-report`

**Query params:**
- `year` (int, required) — calendar year, e.g. 2026
- `quarter` (int, required) — 1–4

Works regardless of `process_verification.enabled` (informational only; no emotive
verdicts by construction).

**Response (200):**

```typescript
interface QuarterlyOutcomeReport {
  year: number;
  quarter: number;
  period_start: string;   // ISO date, e.g. "2026-04-01"
  period_end: string;     // ISO date, e.g. "2026-06-30"
  memo_groups: MemoGroup[];
  summary: {
    total_trades: number;
    with_outcome: number;
    median_outcome_pct: number | null;
  };
}

interface MemoGroup {
  memo_id: string;
  memo_title: string | null;
  trades: TradeSummary[];
}

interface TradeSummary {
  trade_id: number;
  asset_id: string;
  action: string;           // "buy" | "sell"
  trade_date: string;
  rule_bucket: string | null;
  outcome_pct: number | null;
  outcome_label: string | null; // "outcome so far" | matured label
}
```

**Error modes:**
- 400 — `quarter` not in 1–4
- 500 — unexpected server error

---

## Section F: Data Model Reference

### Key tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `trade_logs` | Canonical trade records | `id`, `rule_bucket`, `memo_id`, `order_origin`, `process_authorized`, `process_params_ok`, `process_data_verified`, `process_checked_at`, `process_notes`, `verdict_archived` |
| `verification_config` | `config/verification.yaml` loader | n/a (loaded at request time) |

### Rule bucket classifier

Buckets are assigned by `src/services/rule_bucket_classifier.py` using:
1. memo `name` / `rule` field keywords (compliance/ratio signals)
2. asset taxonomy class (liquidity assets)
3. default: `value`

The `verdict_archived` column preserves the pre-V7.4.0 verdict before process scoring
may change the displayed verdict (never destructive).
