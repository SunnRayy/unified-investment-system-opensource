# API Spec: Insight Governance (F4.3–4.6 + F5 + F6)

> Feature: Signal governance (metric catalog, staleness-based reliability, data-fix backlog); contrarian decomposition; insight promote gate; governance report; checklist export.
> Status: Implemented (V7.4.0)
> Last Updated: 2026-07-08

---

## Overview

PRD 2026-07-07 F4.3–4.6 + F5 + F6. Three related areas:

**F4 Signal governance** (`/governance/*`): `metric_catalog` records metadata and
freshness class for each metric. Stale metrics auto-downgrade reliability. A
`data_fixes` backlog tracks open correction tasks with staleness-based due dates.

**F5 Contrarian decomposition**: `ai_insights.order_origin` splits the contrarian
metric into systematic (auto-watched) vs manual — surfaced on existing behavioral
metrics endpoints (no new route; additive field on `ai_insights`).

**F6 Insight governance** (`/ai-advisor/insights/*`): promote gate (≥70% confidence OR
≥3 validated cases); validated-case counter + links; rule-layer classification; quarterly
rule citation log; one-in-one-out governance report; markdown checklist export.

---

## Section A: API Contract

### Endpoints — F4 Signal Governance

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/governance/metrics` | metric_catalog overview with open/overdue data-fix counts |
| GET | `/api/governance/data-fixes` | List data_fixes backlog |
| POST | `/api/governance/data-fixes` | Create a new data-fix item |
| PUT | `/api/governance/data-fixes/{fix_id}` | Update data-fix status |

### Endpoints — F6 Insight Governance

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/ai-advisor/insights/{insight_id}/validated-cases` | Increment validated_cases + append case link |
| PUT | `/api/ai-advisor/insights/{insight_id}/rule-layer` | Set rule_layer (principle/checklist_item) |
| POST | `/api/ai-advisor/insights/{insight_id}/citations` | Record a rule citation for this quarter |
| GET | `/api/ai-advisor/insights/{insight_id}/citations` | List rule citations for this insight |
| GET | `/api/ai-advisor/insights/governance-report` | Quarterly one-in-one-out governance report |
| GET | `/api/ai-advisor/insights/checklist-export` | Markdown checklist export (text/markdown) |

### Additive field on `GET /api/ai-advisor/insights`

Every item in the existing insights list now includes (F6):

```typescript
{
  validated_cases: number | null;
  rule_layer: "principle" | "checklist_item" | null;
  promote_eligible: boolean;
  promote_blocked_reason: string | null;  // null when eligible; reason string when blocked
}
```

---

## F4: Signal Governance Endpoints

### GET `/api/governance/metrics`

**Response (200) — array of:**

```typescript
interface MetricCatalogEntry {
  metric_key: string;          // e.g. "drift_pct", "buffett_fed_z1_corp_equities_gdp"
  display_name: string | null;
  description: string | null;
  freshness_class: "fast" | "slow" | null;  // fast=24h, slow=7d staleness threshold
  methodology_tag: string | null;           // e.g. "fed_z1" for Buffett variant
  reliability: "RELIABLE" | "UNRELIABLE" | null;
  last_updated_at: string | null;           // ISO-8601 datetime
  open_data_fixes: number;                  // count of open data_fixes for this metric_key
  overdue_data_fixes: number;               // open AND due_at < now
}
```

---

### GET `/api/governance/data-fixes`

**Query params:**
- `status` — `"open"` (default) | `"overdue"` | `"done"` | `"wontfix"` | `"all"`

Note: `"overdue"` is a computed filter (status='open' AND due_at < now). The
response always includes `overdue_count` regardless of the active filter.

**Response (200):**

```typescript
interface DataFixesResponse {
  items: DataFix[];
  overdue_count: number;  // total overdue across all open fixes, regardless of filter
}

interface DataFix {
  id: number;
  title: string;
  description: string | null;
  metric_key: string | null;
  opened_at: string;       // ISO-8601 datetime
  due_at: string;          // ISO-8601 datetime — NEVER null (defaulted at creation)
  status: "open" | "done" | "wontfix";
  closed_at: string | null;
}
```

---

### POST `/api/governance/data-fixes`

**Request Body:**

```typescript
interface CreateDataFixRequest {
  title: string;
  description?: string | null;
  metric_key?: string | null;   // links to metric_catalog for freshness-class lookup
  due_at?: string | null;       // ISO-8601 datetime; defaults from metric freshness if omitted
}
```

`due_at` default logic (PRD F4.6):
- If `metric_key` is known and has `freshness_class = 'fast'`: now + 7 days
- Otherwise: now + 30 days

**Response (200):** the created `DataFix` object.

**Error modes:**
- 422 — `due_at` provided but not parseable as ISO-8601

---

### PUT `/api/governance/data-fixes/{fix_id}`

**Request Body:**

```typescript
interface UpdateDataFixRequest {
  status: "open" | "done" | "wontfix";
}
```

Setting status to `done` or `wontfix` stamps `closed_at`; reverting to `open` clears it.

**Response (200):** updated `DataFix` object.

**Error modes:**
- 404 — `fix_id` not found
- 422 — invalid `status`

---

## F6: Insight Governance Endpoints

### POST `/api/ai-advisor/insights/{insight_id}/validated-cases`

Adds one validated-case link and increments the `validated_cases` counter.
Each call appends `{link, note, added_at}` to `ai_insights.validated_case_links` (JSON array).

**Request Body:**

```typescript
interface ValidatedCaseRequest {
  link: string;          // URL or reference to the case being cited as evidence
  note?: string | null;
}
```

**Response (200):**

```typescript
interface ValidatedCasesResponse {
  insight_id: number;
  validated_cases: number;             // new total
  validated_case_links: CaseLink[];
}

interface CaseLink {
  link: string;
  note: string | null;
  added_at: string;  // ISO-8601 datetime
}
```

**Error modes:** 404 (insight not found)

---

### PUT `/api/ai-advisor/insights/{insight_id}/rule-layer`

**Request Body:**

```typescript
interface RuleLayerRequest {
  rule_layer: "principle" | "checklist_item";
}
```

**Response (200):**

```typescript
{ insight_id: number; rule_layer: string; }
```

**Error modes:** 404, 422 (invalid rule_layer value)

---

### POST `/api/ai-advisor/insights/{insight_id}/citations`

Records a manual rule citation for the current quarter. Quarter is derived server-side
from `now` (e.g. `"2026-Q3"`), enabling the governance report's per-quarter count.

**Request Body:**

```typescript
interface CitationRequest {
  memo_id: string;        // memo that applied this insight's rule
  note?: string | null;
}
```

**Response (200):**

```typescript
interface CitationCreated {
  id: number;
  insight_id: number;
  memo_id: string;
  cited_at: string;   // ISO-8601 datetime
  quarter: string;    // e.g. "2026-Q3"
  note: string | null;
}
```

---

### GET `/api/ai-advisor/insights/{insight_id}/citations`

**Response (200) — array of `CitationCreated` objects.**

---

### GET `/api/ai-advisor/insights/governance-report`

**Query params:**
- `year` (int, optional) — defaults to current year
- `quarter` (int 1–4, optional) — defaults to current quarter

**Response (200):**

```typescript
interface GovernanceReport {
  year: number;
  quarter: string;          // e.g. "2026-Q3"
  period_start: string;     // ISO date
  period_end: string;       // ISO date
  promoted_this_quarter: number;     // status='principle' + updated_at within quarter (upper-bound estimate)
  zero_citation_principles: ZeroCitationItem[];  // promoted principles with no citations this quarter
  basis: string;            // methodology caveat (ai_insights has no status-transition log)
}

interface ZeroCitationItem {
  id: number;
  title: string;
}
```

**Important caveat** (documented in `basis` field): `promoted_this_quarter` uses
`status = 'principle' AND updated_at >= period_start` as a proxy for promotion events;
`updated_at` is overwritten by any field edit, so this is an upper-bound estimate.

**Error modes:** 422 (quarter not 1–4)

---

### GET `/api/ai-advisor/insights/checklist-export`

Returns a Markdown document grouping `rule_layer = 'checklist_item'` insights by category.

**Response:** `Content-Type: text/markdown` (streamed)

Format:
```markdown
# Insight Checklist Export

## Category Name
- [ ] Insight title: body (if body != title)
- [ ] Another insight
```

---

## Section F: Data Model Reference

### Key tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `metric_catalog` | Metric metadata + freshness class | `metric_key`, `freshness_class`, `methodology_tag`, `reliability`, `last_updated_at` |
| `data_fixes` | Open correction tasks | `id`, `metric_key`, `title`, `due_at`, `status`, `closed_at` |
| `ruling_deferred_events` | Log of stale-asset deferrals in value-trap scan | `event_type`, `asset_id`, `deferred_at` |
| `ai_insights` | Insights + F5/F6 additive fields | `validated_cases`, `validated_case_links`, `rule_layer`, `order_origin` |
| `rule_citations` | Per-quarter rule application log | `id`, `insight_id`, `memo_id`, `cited_at`, `quarter`, `note` |

### Promote gate (enforced by `promote_insight()` in `src/services/ai_advisor/insight_manager.py`)

```
PASS when: confidence >= 0.70 OR validated_cases >= 3
FAIL → HTTPException 422 with human-readable reason
```

This gate is also surfaced on the insights list via `promote_eligible` / `promote_blocked_reason`.
