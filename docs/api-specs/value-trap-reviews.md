# API Spec: Value-Trap Reviews (F2)

> Feature: Loss-side mandatory review — trigger scan, escalation ladder, adversarial-ack liquidate gate, overdue alerts.
> Status: Implemented (V7.4.0)
> Last Updated: 2026-07-08

---

## Overview

PRD 2026-07-07 F2. When a position's unrealized return crosses a loss threshold
(−25% initial trigger → −35% → −45% escalation after a "hold_with_thesis" ruling),
a review record is opened. The owner must actively rule on each open review;
liquidate requires explicit adversarial acknowledgement.

Key table: `value_trap_reviews` (migration 011/V68).
Configuration: `config/verification.yaml` → `value_trap:` block.

---

## Section A: API Contract

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/reviews/value-trap/scan` | Run the F2.1/F2.2 trigger scan now |
| GET | `/api/reviews/value-trap` | List value-trap reviews |
| GET | `/api/reviews/value-trap/pending-count` | Dashboard badge: open + overdue counts |
| PUT | `/api/reviews/value-trap/{review_id}` | Submit a review ruling |

---

### POST `/api/reviews/value-trap/scan`

Runs the loss-side trigger scan against current portfolio positions (WealthOS P&L
formulas; cost≤0 positions skipped; compliance/ratio-bucket assets excluded).

Staleness semantics: an asset is deferred (not evaluated) when its *valuation
freshness* — the LATER of `snapshot_date` and `price_updated_at` — is older
than `staleness.slow_days` (config default: 7 days). `price_updated_at` is used
because the trigger evaluates the current market price, and a DSA-refreshed price
on an old snapshot date is still a reliable current valuation.

**Response (200):**

```typescript
interface ScanSummary {
  scanned: number;              // total non-shadow holdings rows examined
  hits: number;                 // assets whose return crossed the threshold
  opened: number;               // new value_trap_reviews rows created
  refreshed: number;            // existing open reviews updated with new return %
  skipped_bucket: number;       // compliance/ratio-bucket assets excluded from F2
  skipped_no_cost: number;      // positions with cost_price_unit <= 0 (zero-cost, RSU)
  deferred_unreliable: number;  // valuation freshness (max of snapshot_date, price_updated_at) > slow_days
  evaluated: number;            // assets that passed all gates and had a return computed
}
```

**Error modes:**
- 500 — unexpected server error (DB write failure)

---

### GET `/api/reviews/value-trap`

**Query params:**
- `status` — `"open"` (default) | `"ruled"` | `"all"`

**Response (200) — array of:**

```typescript
interface ValueTrapReview {
  id: number;
  asset_id: string;
  asset_name: string | null;
  status: "open" | "ruled";
  trigger_threshold_pct: number;        // e.g. -25 (percent)
  unrealized_return_pct: number | null; // current unrealized return at scan time
  memo_id: string | null;
  opened_at: string;                    // ISO-8601 datetime
  refreshed_at: string | null;
  thesis_restated: string | null;       // owner's answers to the three review questions
  falsification_check: string | null;
  would_buy_today: string | null;
  ruling: string | null;                // "hold_with_thesis" | "trim" | "liquidate"
  adversarial_ack: boolean;
  next_review_date: string | null;      // ISO date
  last_reviewed_at: string | null;
  last_ruling: string | null;
  next_trigger_threshold_pct: number | null; // next escalation threshold after hold_with_thesis
  days_open: number | null;             // computed: days since opened_at
  overdue: boolean;                     // true if open AND days_open > overdue_alert_days (config)
}
```

**Error modes:**
- 400 — invalid `status` value (pattern constraint)
- 500 — unexpected server error

---

### GET `/api/reviews/value-trap/pending-count`

Lightweight badge endpoint — one DB query.

**Response (200):**

```typescript
interface PendingCount {
  open: number;    // total open reviews
  overdue: number; // open AND days_open > overdue_alert_days
}
```

---

### PUT `/api/reviews/value-trap/{review_id}`

**Path params:**
- `review_id` (int) — `value_trap_reviews.id`

**Request Body:**

```typescript
interface ValueTrapRulingRequest {
  thesis_restated?: string | null;    // required by process: why still hold?
  falsification_check?: string | null;// what would prove the thesis wrong?
  would_buy_today?: string | null;    // would you initiate at current price?
  ruling: "hold_with_thesis" | "trim" | "liquidate";
  adversarial_ack: boolean;           // MUST be true for liquidate ruling (422 otherwise)
  next_review_date?: string | null;   // ISO date for follow-up
}
```

Process gate: `ruling = "liquidate"` requires `adversarial_ack = true`. This
enforces PRD F2.3: the owner must confirm an adversarial review before selling
a losing position (disposition-effect guard).

`ruling = "hold_with_thesis"` re-arms the escalation ladder:
`next_trigger_threshold_pct = trigger_threshold_pct - escalation_step_pp`
(configured in `config/verification.yaml`).

**Response (200):** full `ValueTrapReview` object with `status = "ruled"`.

**Error modes:**
- 404 — `review_id` not found
- 422 — invalid `ruling` value or `liquidate` without `adversarial_ack`
- 500 — unexpected server error

---

---

### GET `/api/reviews/value-trap/{review_id}/context`

WS2 — returns Huinsight context for the F2.3 review form context panel. Read-only.

**Path params:**
- `review_id` (int) — `value_trap_reviews.id`

**Response (200):**

```typescript
interface ValueTrapContext {
  review_id: number;
  asset_id: string;
  position: {
    qty: number;
    cost_price_unit: number;
    market_price_unit: number;
    market_value: number;
    snapshot_date: string | null;  // ISO date of the latest non-shadow holding
    currency: string;
  } | null;  // null if no current holding found
  loss: {
    unrealized_return_pct: number | null;  // from value_trap_reviews row
    trigger_threshold_pct: number;
    days_open: number | null;
  };
  originating_memo: {
    memo_id: string | null;  // most recent value-bucket trade_log with memo_id; null if none
  };
  decision_history: Array<{
    log_date: string | null;
    action: string | null;
    quantity: number | null;
    price: number | null;
    rule_bucket: string | null;
    verification_status: string | null;
  }>;  // last 5 trade_logs rows for the asset, newest first
  case_file: {
    asset_id: string;  // use to deep-link: /asset-case-file?asset_id=<asset_id>
  };
}
```

**Implementation notes:**
- Position uses per-asset `MAX(snapshot_date)` CTE (never global MAX).
- `originating_memo.memo_id`: latest `trade_logs` row where `asset_id = ?` AND `rule_bucket = 'value'` AND `memo_id IS NOT NULL`.
- `decision_history`: last 5 `trade_logs` rows for the asset ordered by `log_date DESC, id DESC`.

**Error modes:**
- 404 — `review_id` not found
- 500 — unexpected server error

---

### POST `/api/reviews/value-trap/{review_id}/draft`

WS2 — LLM pre-draft of the three F2.3 review questions. **Not persisted.** Returns draft text only; the owner fills in the textareas and saves a ruling via `PUT /{review_id}`.

**Path params:**
- `review_id` (int) — `value_trap_reviews.id`

**Request body:** none.

**Response (200):**

```typescript
interface ValueTrapDraft {
  thesis_draft: string;         // draft for "Thesis restated"
  falsification_draft: string;  // draft for "Falsification check"
  buy_today_draft: string;      // draft for "Would you buy today"
  model: string;                // LLM model that generated the draft (e.g. "gemini/gemini-2.5-flash")
}
```

**Error modes:**
- 404 — `review_id` not found
- 503 — no LLM API key configured (GEMINI_API_KEY / ANTHROPIC_API_KEY / DEEPSEEK_API_KEY)
- 503 — all configured LLM models failed (includes detail message)
- 500 — unexpected server error

---

### Additive field: `open_value_trap_review` on `GET /api/wealthos/assets`

WS2 F2.4 badge. Each asset object in the response now includes:

```typescript
open_value_trap_review: boolean;  // true when this asset has a status='open' review
```

Computed via a single `SELECT DISTINCT asset_id FROM value_trap_reviews WHERE status = 'open'` query — no N+1. Non-fatal if the table is absent (defaults false).

---

## Section F: Data Model Reference

### Key tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `value_trap_reviews` | Open/ruled review records | `id`, `asset_id`, `status`, `trigger_threshold_pct`, `unrealized_return_pct`, `ruling`, `adversarial_ack`, `next_trigger_threshold_pct`, `opened_at`, `last_reviewed_at` |
| `holdings` | Current positions (scan source) | `asset_id`, `market_value`, `cost_price_unit`, `qty`, `snapshot_date` |
| `trade_logs` | Used to look up `rule_bucket` for exclusion | `asset_id`, `rule_bucket` |

### Configuration

`config/verification.yaml` → `value_trap:` section:
- `trigger_threshold_pct: -25` — initial loss threshold (percent)
- `escalation_step_pp: 10` — deducted from threshold on each "hold_with_thesis" ruling
- `overdue_alert_days: 14` — days before an open review is flagged overdue
