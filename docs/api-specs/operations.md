# API Spec: Operations Endpoints

**Version**: V5.9.0
**Base path**: `/operations` (no `/api` prefix — Vite proxy rewrites `/api/*` → `/*`)
**Auth**: Bearer token required on all endpoints

---

## GET /operations/portfolio-audit

Returns the aggregated portfolio audit summary used by the Portfolio Audit page.

### Response

```json
{
  "total_assets": 87,
  "active_assets": 62,
  "open_cases": 3,
  "asset_classes": [
    {
      "class_name": "US Equity",
      "total_value": 1234567.89,
      "active_assets": 12,
      "value_issues": 0,
      "legacy_influenced": 2
    }
  ],
  "source_reconciliation": [
    {
      "source": "Schwab_CSV",
      "db_count": 15,
      "db_value": 987654.32,
      "prior_count": 14,
      "prior_value": 956000.00,
      "count_delta": 1,
      "value_delta_pct": 3.3,
      "status": "ok",
      "last_sync": "2026-05-25T10:00:00"
    }
  ],
  "integrity_checks": [
    {
      "name": "holdings_have_market_value",
      "status": "pass",
      "actual": 0,
      "threshold": 0,
      "message": "All holdings have market value"
    }
  ],
  "sync_changelog": [
    {
      "sync_id": "abc123",
      "timestamp": "2026-05-25T10:00:00",
      "title": "Cost basis recalculated for RSU_AMZN",
      "detail": "FIFO recalculation triggered by new vest event",
      "event_type": "info",
      "source": "RSU_Excel"
    }
  ]
}
```

### Fields

**`source_reconciliation[]`**

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | Reader source name (e.g. `Schwab_CSV`) |
| `db_count` | int | Holdings count in DB after last sync |
| `db_value` | float | Total market value (CNY) after last sync |
| `prior_count` | int \| null | Count before last sync (null if no prior sync) |
| `prior_value` | float \| null | Value before last sync (null if no prior sync) |
| `count_delta` | int \| null | `db_count - prior_count` |
| `value_delta_pct` | float \| null | `(db_value - prior_value) / prior_value * 100` |
| `status` | `"ok"` \| `"warning"` \| `"missing"` | warning when `count_delta < -5` |
| `last_sync` | string | ISO timestamp of last sync |

**`sync_changelog[]`**

| Field | Type | Description |
|-------|------|-------------|
| `sync_id` | string | Sync audit report ID |
| `timestamp` | string | ISO timestamp |
| `title` | string | Short event description |
| `detail` | string | Full detail text |
| `event_type` | `"warning"` \| `"info"` \| `"ok"` | Severity classification |
| `source` | string \| null | Source system that triggered the event |

**`integrity_checks[]`**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Check name (e.g. `trade_log_verdict_consistency`) |
| `status` | `"pass"` \| `"fail"` \| `"warn"` | Check result |
| `actual` | any | Measured value |
| `threshold` | any | Expected threshold |
| `message` | string | Human-readable result |

**`integrity` (top-level summary object; added V7.7.1)**

The live response also carries a run-time integrity summary (distinct from the persisted
per-check `integrity_checks[]` above, which comes from the last sync's stored
`sync_audit_reports` row): `src/api/routes/operations.py::get_portfolio_audit` calls
`run_integrity_checks()` on every request and returns:

| Field | Type | Description |
|-------|------|-------------|
| `passed` | int | Checks that ran **and** found no violation. Excludes skips (V7.7.1 — previously every skip counted as a pass). |
| `skipped` | int | **New in V7.7.1.** Checks that could not evaluate their invariant (missing table, insufficient data, a guard tripped) — not a failure, but not evidence of correctness either. |
| `total` | int | Total checks in the registry (16). |
| `all_passed` | bool | `true` iff no check FAILED. Skips are **not** failures — this gating semantic is unchanged by V7.7.1; only the `passed`/`skipped` breakdown became honest. |

A parallel `integrity_grouped[]` array (categories: Financial Bounds, Shadow & Structure, Source
Reconciliation, Return Metrics, Cross-Endpoint Consistency, Other) carries `{cat, pass, total,
fails[]}` per category; it does not currently carry a per-check `skipped` flag — only the
CLI (`main.py --check-integrity --json`) carries the per-check boolean today (see below).

**`main.py --check-integrity --json` (CLI, not an HTTP endpoint, documented here for completeness)**

Top level gains `skipped` (int) alongside the existing `count`/`passed`/`failed`/`all_passed`; each
entry in `checks[]` gains `"skipped": bool`. This is the only surface where the per-check flag is
exposed today — `GET /operations/portfolio-audit`'s `integrity_checks[]`/`integrity_grouped[].fails[]`
and `GET /audit/v2/integrity`'s `checks[]` (undocumented in any api-spec; see note below) do not
carry a per-check `skipped` field yet, only the top-level count.

**Note on `GET /audit/v2/integrity` and `GET /integrity/status`**: both also call
`run_integrity_checks()` and both gained (or, for `/integrity/status`, did NOT gain — it was outside
the V7.7.1 change's scope) a top-level `skipped_count`/`skipped`. Neither endpoint is documented by
any file under `docs/api-specs/` as of V7.7.1 (pre-existing gap, not introduced by this release) —
`GET /audit/v2/integrity` returns `{all_passed, passed_count, skipped_count, total_count, run_at,
checks: [{name, passed, actual_value, threshold, details}]}` (no per-check `skipped`);
`GET /integrity/status` and `GET /integrity/audit` return `{all_passed, passed_count, total_count,
run_at, checks: [...]}` with **no** `skipped`/`skipped_count` at all — that pair of routes was not
touched by the V7.7.1 change.

---

## GET /operations/asset-class-audit

Returns asset breakdown for a specific asset class.

### Query Parameters

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `class_name` | string | Yes | Asset class name (e.g. `US Equity`) |

### Response

```json
{
  "class_name": "US Equity",
  "active_assets": 12,
  "total_value": 1234567.89,
  "groups": [
    {
      "source_system": "Schwab_CSV",
      "assets": [
        {
          "asset_id": "US_STK_AMZN",
          "display_name": "Amazon.com Inc",
          "market_value": 85000.00,
          "quantity": 12,
          "currency": "USD",
          "legacy_influence": false,
          "value_issue": false
        }
      ]
    }
  ]
}
```

### Notes

- `market_value` is always in CNY (converted at sync time)
- `currency` is the native currency of the asset (e.g. `USD` for Schwab holdings)
- `quantity` is null for assets without share counts (e.g. gold, insurance)
- Non-CNY assets show a sub-line in the UI: `{currency} · {quantity} shares`

---

## GET /operations/asset-audit

Returns all assets with their audit classification status.

### Response

```json
{
  "assets": [
    {
      "asset_id": "US_STK_AMZN",
      "display_name": "Amazon.com Inc",
      "asset_class": "US Equity",
      "is_active": true,
      "is_shadow": false,
      "legacy_influence": false,
      "value_issue": false,
      "market_value": 85000.00,
      "cost_basis": 72000.00,
      "sources": ["Schwab_CSV"]
    }
  ]
}
```

---

## GET /operations/asset-case-file

Returns the case file for a specific asset.

### Query Parameters

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `asset_id` | string | Yes | Asset ID |

### Response

```json
{
  "asset_id": "US_STK_AMZN",
  "display_name": "Amazon.com Inc",
  "asset_class": "US Equity",
  "current_status": {
    "market_value": 85000.00,
    "cost_basis": 72000.00,
    "is_active": true,
    "legacy_influence": false,
    "value_issue": false,
    "sources": ["Schwab_CSV"]
  },
  "cases": [
    {
      "case_id": "CASE-001",
      "title": "Value issue detected",
      "severity": "warning",
      "opened_at": "2026-05-20T09:00:00",
      "closed_at": null,
      "notes": "Market value appears below cost basis — investigate data source"
    }
  ],
  "timeline": [
    {
      "date": "2026-05-20",
      "event": "Value issue flagged",
      "source": "integrity_gate"
    }
  ]
}
```

### Notes

- `cases` may be empty — use the `/asset-case-file?asset_id=` lookup to investigate any asset regardless
- The frontend provides an asset ID search input for direct lookup without waiting for cases to be flagged

---

## GET /operations/transactions

Search and filter transactions.

### Query Parameters

| Param | Type | Description |
|-------|------|-------------|
| `asset_id` | string | Filter by asset ID (partial match) |
| `source` | string | Filter by source system |
| `normalized_type` | string | Filter by normalized transaction type |
| `raw_type` | string | Filter by raw transaction type |
| `account` | string | Filter by account |
| `verified` | `"true"` \| `"false"` | Filter by verification status |
| `date_from` | string | Start date (YYYY-MM-DD) |
| `date_to` | string | End date (YYYY-MM-DD) |

### Response

```json
{
  "transactions": [
    {
      "id": "txn-001",
      "transaction_date": "2026-05-15",
      "asset_id": "US_STK_AMZN",
      "asset_name": "Amazon.com Inc",
      "transaction_type": "buy",
      "source_system": "Schwab_CSV",
      "amount_net": 85000.00,
      "price_unit": 7083.33,
      "currency": "USD",
      "account": "Individual",
      "memo": null,
      "verified": true
    }
  ],
  "total": 1
}
```

---

## GET /operations/transaction-filters

Returns available filter options for the Transaction Browser.

### Response

```json
{
  "sources": ["Schwab_CSV", "CN_Fund_Excel", "Financial_Summary_Excel"],
  "raw_types": ["Buy", "Sell", "Dividend", "Interest"],
  "normalized_types": ["buy", "sell", "dividend", "interest"],
  "accounts": ["Individual", "Roth IRA"]
}
```
