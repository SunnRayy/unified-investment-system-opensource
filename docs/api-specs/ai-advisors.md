# API Spec: AI Advisors Intelligence Layer

> Feature: Decision Hub, Verification Dashboard, Strategy Alignment, Action Inbox, AI Advisor (Brief/Memos/Record Trade/Review/Insights)
> Status: Production (V4.5.0)
> Last Updated: 2026-03-26

---

## Overview

The AI Advisors Intelligence Layer integrates AIA (AI Investment Advisor) insights, trade verification, and strategy review into Huinsight as a first-class intelligence module. Data flows from AIA via file-based pull (JSON/Markdown) into Huinsight tables, and is surfaced through these APIs.

**Key tables**: `insights`, `trade_logs`, `deviation_actions`, `strategy_review_reports`, `strategy_memos`, `verification_logs`

**Frontend pages**: Decision Hub, Strategy Alignment, Verification Dashboard, Action Inbox (alerts badge on all pages)

---

## Section A: Decision Hub Endpoints

### GET `/decisions/timeline`

Merged chronological feed of recommendation insights, drift alerts, and executed trades.

`lesson` / growth-trajectory items are no longer mixed into the default timeline feed; they surface via `/decisions/intelligence`.

**Query Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 50 | Maximum items to return |
| `type` | string | `"all"` | Filter: `"all"` \| `"insight"` \| `"drift"` \| `"trade"` |

**Response**

```typescript
interface TimelineItem {
  id: string;            // "insight_123" | "drift_456" | "trade_789"
  type: "insight" | "drift" | "trade";
  date: string;          // ISO date (YYYY-MM-DD)
  title: string;
  content: string;
  source: string;        // AI model name or "system"
  status: "pending" | "adopted" | "rejected" | "executed" | "observing";
  subtype?: string | null;       // "recommendation" | "trade"
  display_source?: string | null;
  display_status?: string | null;
  match_status?: string | null;
  origin_ref?: string | null;    // "insights:123" | "trade_logs:456"
  metadata: {
    category?: string;   // insight: "recommendation"
    tags?: string[];     // insight only
    asset_class?: string; // drift only
    deviation_pct?: number; // drift only
    asset_id?: string;   // trade only
    action?: string;     // trade: "BUY" | "SELL"
    amount?: number;     // trade only (CNY)
  };
}

interface TimelineResponse {
  items: TimelineItem[];
  summary: {
    total: number;
    adopted: number;
    pending: number;
  };
}
```

---

### GET `/decisions/stats`

High-level decision statistics for dashboard KPI cards.

**Response**

```json
{
  "total_insights": 47,
  "adopted_count": 23,
  "pending_count": 18,
  "adoption_rate": 48.9,
  "total_trades": 31,
  "active_drift_alerts": 2,
  "ai_trades_total": 23,
  "ai_scored_total": 3,
  "ai_last_sync_date": "2026-03-16"
}
```

---

### GET `/decisions/scorecard`

AI-scoped trade decisions with verdict, grade, outcome percentage, and linkage metadata. Automatically runs the scoring algorithm (`decision_scorer.py`) before returning results.

Only AI-scoped decision trades are returned (manual/human/user rows are excluded from scorecard metrics unless explicitly linked to AIA transactions).

**Query Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 50 | Maximum trade records |

**Response**

```typescript
interface ScorecardItem {
  id: number;
  date: string;               // YYYY-MM-DD
  asset_id: string;           // e.g. "US_STK_AMZN"
  asset_name: string | null;
  action: string;             // "BUY" | "SELL"
  price: number | null;       // CNY
  quantity: number | null;
  amount: number | null;      // CNY total
  source: string | null;      // suggestion_source (AI model or "manual")
  verification_date: string | null;
  verification_result: string | null;  // "correct" | "incorrect" | "pending"
  verdict: string | null;     // "WIN" | "LOSS" | "HOLD" | "PENDING"
  outcome_pct: number | null; // Return % since trade date
  grade: string | null;       // "A" | "B" | "C" | "D" | "F"
  linked_insight_id?: number | null;
  linked_insight_title?: string | null;
  match_status?: "matched" | "inferred" | "unmatched" | "source_only" | null;
  why_unscored?: string | null; // "awaiting_verification_window" | "trade_without_insight_link" | ...
}

interface ScorecardResponse {
  items: ScorecardItem[];
}
```

---

### GET `/decisions/funnel`

Adoption funnel: shows how insights progress from generated → adopted → scored outcomes.

Scored outcome counts in this endpoint are AI-scoped (same scope as scorecard/leaderboard).

**Response**

```json
{
  "total_insights": 47,
  "adopted": 23,
  "adoption_rate": 48.9,
  "scored_outcomes": 12,
  "wins": 8,
  "losses": 4,
  "win_rate": 66.7
}
```

---

### GET `/decisions/leaderboard`

Per-source AI model hit rates for the Intelligence Leaderboard card.

Manual sources are excluded from leaderboard aggregation.

**Response**

```json
{
  "sources": [
    { "source": "gemini", "total": 15, "wins": 10, "hit_rate": 66.7 },
    { "source": "claude", "total": 8, "wins": 5, "hit_rate": 62.5 },
    { "source": "committee", "total": 12, "wins": 9, "hit_rate": 75.0 }
  ]
}
```

---

### GET `/decisions/intelligence`

Structured decision-intelligence payload for the `Intelligence` tab. This endpoint combines recommendation-pattern metrics, lesson/growth records, and expandable raw `Insight.md` excerpts.

```typescript
interface DecisionIntelligenceResponse {
  decision_patterns: {
    funnel: DecisionFunnel;
    leaderboard: LeaderboardSource[];
    sources: Array<{
      source: string;
      total: number;
      adopted: number;
      rejected: number;
      pending: number;
    }>;
  };
  growth_timeline: Array<{
    id: string;
    date: string;
    title: string;
    content: string;
    source: string;
    origin_ref: string;
  }>;
  raw_sections: Array<{
    section: string;
    title: string;
    content: string;
    entry_count: number;
  }>;
}
```

---

### GET `/decisions/alerts`

Strategy-aware action alerts for the Action Inbox (alert badge count + priority triage).

**Response**

```typescript
interface Alert {
  id: string;
  priority: "high" | "medium" | "low";
  type: "drift" | "strategy" | "verification" | "market";
  title: string;
  description: string;
  action_url?: string;    // Frontend route to navigate to
  created_at: string;     // ISO datetime
}

interface AlertsResponse {
  alerts: Alert[];
  counts: {
    high: number;
    medium: number;
    low: number;
  };
}
```

---

## Section B: Strategy Alignment Endpoints

### GET `/strategy/alignment`

Returns the latest strategy review report. Auto-computes a fresh report if none exists or if the latest is older than 24 hours.

**Response**

```typescript
interface AllocationClass {
  asset_class: string;    // Top-level class (7 classes: 股票/债券/黄金/现金/保险/房产/RSU)
  current_pct: number;
  target_pct: number;
  deviation_pct: number;  // current_pct - target_pct (signed)
  status: "aligned" | "over" | "under";
}

interface StrategyAlignmentResponse {
  report: {
    review_date: string;
    allocation_alignment: {
      classes: AllocationClass[];
      overall_score: number;        // 0–100
      aligned_count: number;
      total_count: number;
    };
    trading_frequency: {
      trades_last_30d: number;
      avg_trade_size: number;
      frequency_assessment: string;
    };
    contrarian_score: number | null;      // 0–100 (null if market data unavailable)
    contrarian_details: {
      description: string;
      rows: Array<{
        asset_class: string;
        portfolio_weight: number;
        market_return_pct: number;   // ⚠️ 0.00% if no market_daily date match (see Known Issues)
        contrarian_signal: string;
      }>;
    };
    profile_discrepancies: {
      count: number;                // Items where actual class ≠ AIA profile class
      items: Array<{
        asset_id: string;
        actual_class: string;
        profile_class: string;
      }>;
    };
    overall_alignment: string;      // "ALIGNED" | "REVIEW_NEEDED" | "ACTION_REQUIRED"
  } | null;
  message?: string;   // Present only if no report available
}
```

**Known Issues (as of 2026-03-17)**:
- `contrarian_details[].market_return_pct` shows 0.00% on weekends/holidays — exact date match in `market_daily` fails. Fix pending in Batch 10 (nearest-prior-day 3-day lookback).
- `profile_discrepancies.count` shows 27 items — raw `asset_class` compared against two naming systems. Fix pending in Batch 11 (`_AIA_TO_TOP_CLASS` mapping in `generate_strategy_report()`).

---

### POST `/strategy/review`

Trigger an immediate strategy review computation and persist the result.

**Response**

```json
{
  "status": "ok",
  "overall_alignment": "REVIEW_NEEDED",
  "report": { ... }  // Same shape as GET /strategy/alignment report field
}
```

---

### GET `/strategy/memos`

List recent strategy memos (imported from AIA strategy Markdown files).

**Response**

```json
{
  "memos": [
    {
      "id": 1,
      "date": "2026-03-15",
      "title": "2026-03 Strategy Review",
      "bias": "defensive",
      "directives": ["Reduce US equity exposure", "Add gold hedge"],
      "source_file": "strategy_2026_03.md"
    }
  ]
}
```

---

### GET `/strategy/targets`

Show AIA profile targets vs Huinsight profile targets side-by-side for comparison.

**Response**

```json
{
  "aia_profile": [
    { "asset_class": "股票", "target_pct": 45.0 },
    { "asset_class": "债券", "target_pct": 20.0 }
  ],
  "uis_profile": [
    { "asset_class": "股票", "target_pct": 40.0 },
    { "asset_class": "债券", "target_pct": 25.0 }
  ]
}
```

---

## Section C: Verification Dashboard Endpoints

> See also: `docs/api-specs/verification-report.md` for base spec. Below are additions/corrections post-Batch 8.

### GET `/verification/latest`

Auto-computes a fresh verification if no result exists within the last 24 hours.

**Full Response Shape** (from `verification_service.compute_verification_report()`):

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
  "period": { "start": "2026-03-01", "end": "2026-03-17" },
  "verdict_breakdown": [
    { "verdict": "WIN", "count": 8, "pct": 66.7 },
    { "verdict": "LOSS", "count": 4, "pct": 33.3 }
  ],
  "adoption_history": [
    { "month": "2026-01", "adoption_rate": 60.0, "total_insights": 15 },
    { "month": "2026-02", "adoption_rate": 72.0, "total_insights": 18 }
  ]
}
```

### POST `/verification/run`

Force a fresh computation, bypassing the 24-hour cache.

### GET `/verification/trends`

Monthly adoption rate history from `insights` table (richer than `verification_logs`).

```json
{
  "periods": [
    {
      "period_start": "2026-01-01",
      "period_end": "2026-01-31",
      "adoption_rate": 60.0,
      "total_insights": 15
    }
  ]
}
```

### GET `/verification/history`

List of stored `verification_logs` records (persisted reports only).

**Query Parameters**: `limit` (int, default 12)

---

## Section D: AIA Integration Model

### Data Flow

```
AIA (AI Investment Advisor)
  │
  ├── output/holdings_snapshot.json   →  Huinsight --refresh-prices (US stock prices)
  ├── 股市信息/market_data.json        →  Huinsight --refresh-prices (CN fund NAV)
  ├── trade_logs (Markdown)           →  AIA sync → trade_logs table
  ├── insights (Insight.md)           →  AIA sync → insights table
  └── strategy memos (Markdown)       →  AIA sync → strategy_memos table
```

**Authority**: AIA is NOT a holdings authority. Holdings come exclusively from the 6 Excel/CSV readers. AIA provides:
1. **Price data** (pull model) via JSON files
2. **Trade suggestions** (provisional, stored in `trade_logs.is_provisional=TRUE`)
3. **Insights** (decision intelligence, stored in `insights` table)
4. **Strategy memos** (stored in `strategy_memos` table)

### Integration Spec References

- `docs/integration/2026-01-25-aia-json-spec.md` — Holdings JSON format (price sync)
- `docs/integration/2026-02-27-aia-price-export-requirements.md` — Price export requirements

---

## Section E: Strategy Memos CRUD Endpoints (V4.5)

### POST `/strategy/memos`

Create a memo from pasted LLM text. Auto-extracts title, date, bias, directives. `source_file=NULL` marks UI-created records.

**Request**: `{ content: string, memo_date?: string (YYYY-MM-DD) }`

**Response**: `{ id, date, title, bias, directives, source_file, content }`

**Status codes**: 201 created | 409 UNIQUE(memo_date, title) conflict (returns `existing_id`)

---

### GET `/strategy/memos/{id}`

Returns full memo including `content` field.

### PUT `/strategy/memos/{id}`

Update title and/or content. Re-extracts title from content if `title` not explicitly provided.

### DELETE `/strategy/memos/{id}`

Hard delete. No guard (any source).

---

## Section F: Trade Recording Endpoints (V4.5)

### GET `/ai-advisor/assets/search?q=`

Asset autocomplete. Min 2 chars. Escapes `%` and `_` wildcards. Returns up to 20 matches from `asset_registry`.

**Response**: `[{ canonical_id, display_name, asset_class, base_currency }]`

---

### POST `/ai-advisor/trades`

Record a manual trade to `trade_logs`. Does NOT touch `transactions` table (reader-first authority).

**Request**:
```typescript
{
  log_date: string;        // YYYY-MM-DD
  asset_id: string;        // any string — unknown IDs accepted (no 422)
  asset_name?: string;     // auto-resolved from asset_registry if omitted
  action: "Buy" | "Sell";
  price?: number;
  quantity?: number;
  amount?: number;          // required if price+quantity absent
  currency?: string;        // defaults to asset_registry.base_currency or "USD"
  decision_reason?: string;
  linked_memo_id?: number;  // explicit memo link; takes precedence over auto-link
}
```

**Response**: `{ id, ...trade fields }` — 201 created. Triggers `score_single_trade()` best-effort after INSERT.

---

### GET `/ai-advisor/trades?limit=50`

List recent trades from `trade_logs`.

### DELETE `/ai-advisor/trades/{id}`

Delete trade. Guard: `LOWER(TRIM(suggestion_source)) IN ('manual', 'human', 'user')` → 403 if AIA-imported.

---

## Section F: Decision Feedback Loop Endpoints (V5.8.0)

### GET `/ai-advisor/trades/pending-verification`

Returns trades awaiting human verdict (status `pending` or `pending_window`).

**Query Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `since` | date (YYYY-MM-DD) | required | Filter trades logged on or after this date |
| `limit` | int | 50 | Max trades to return |

**Response**

```json
{
  "items": [
    {
      "id": 42,
      "log_date": "2026-05-01",
      "asset_id": "US_STK_AMZN",
      "action": "Buy",
      "verification_status": "pending_window",
      "is_matured": true,
      "outcome_pct_preview": 8.3,
      "suggested_verdict": "good_call",
      "linked_insight_id": 17
    }
  ]
}
```

### POST `/ai-advisor/trades/{id}/verify`

Submit a manual verdict for a trade. Uses optimistic concurrency (`updated_at` guard).

**Request Body**

```json
{
  "verdict": "good_call",
  "verification_result": "AMZN rallied 8% after buy",
  "updated_at": "2026-05-24T10:00:00"
}
```

**Response**: `200 OK` with updated trade row, or `409 Conflict` if `updated_at` mismatch.

### POST `/ai-advisor/trades/{id}/reopen-verification`

Resets a `verified` trade back to `pending` so the user can revise the verdict.

**Response**: `200 OK` with updated status.

### POST `/ai-advisor/cross-check`

Generate an AI cross-check narrative comparing insights to actual trade outcomes for a period.

**Request Body**

```json
{
  "period_start": "2026-03-01",
  "period_end": "2026-03-31"
}
```

**Response**: `{ "review": "...", "model_used": "gemini" }` or `422` if period > 90 days or too many insights/trades.

### GET `/ai-advisor/diagnostics`

Returns scorer health statistics.

**Response**

```json
{
  "pending_count": 5,
  "pending_window_count": 3,
  "verification_blocked_count": 2,
  "verified_count": 47,
  "verification_rate_pct": 85.5
}
```

---

## Section G: V5.10.0 Decision Intelligence Phase 2

### GET `/ai-advisor/trades/pending-verification` (updated)

Added `status` query parameter.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `since` | date | required | Log date lower bound |
| `until` | date | today | Log date upper bound |
| `limit` | int | 50 | Max rows |
| `status` | string | `pending` | `pending` \| `verified` \| `all` |

Response items now include additional fields: `verdict`, `outcome_pct`, `verification_result`, `verification_date`.

### GET `/ai-advisor/insights/{insight_id}/links`

List all attribution links for an insight.

**Response**

```json
{
  "links": [
    {
      "id": 1,
      "insight_id": 7,
      "trade_id": 42,
      "link_type": "auto_source",
      "confidence": 0.75,
      "rationale": null,
      "created_at": "2026-05-27T10:00:00",
      "trade_log_date": "2026-05-24",
      "trade_asset_id": "US_STK_AMZN",
      "trade_action": "Buy"
    }
  ]
}
```

### POST `/ai-advisor/links`

Create a manual attribution link. Idempotent on `(insight_id, trade_id)`.

**Request Body**

```json
{ "insight_id": 7, "trade_id": 42, "rationale": "User manually confirmed" }
```

**Response**: `201` `{ "id": 1, "insight_id": 7, "trade_id": 42, "link_type": "manual" }` or `409` if already exists.

### DELETE `/ai-advisor/links/{link_id}`

Remove an attribution link. Returns `204 No Content`.

### POST `/ai-advisor/memos/{memo_id}/propose-updates`

Generate LLM-proposed edits to a strategy memo, grounded in the latest cross-check audit. **Never auto-applies** to `strategy_memos`.

**Request Body** (all optional)

```json
{ "audit_report_id": 12 }
```

Omit `audit_report_id` to use the most recent `cross_check_audit` report.

**Response**

```json
{
  "proposals": [
    {
      "section": "risk rules",
      "current_text": "止损5%",
      "proposed_text": "止损5% — 仅限流动性充足资产",
      "rationale": "Lesson 2: stop-losses on illiquid names caused forced exits at bad prices"
    }
  ],
  "report_id": 55,
  "model_used": "gemini-2.5-flash",
  "memo_id": 1,
  "generated_at": "2026-05-27T14:00:00"
}
```

Returns `404` if the memo doesn't exist, `422` if no audit report is available.
