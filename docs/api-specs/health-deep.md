# API Spec: /health/deep

**Method**: GET  
**Auth**: None required  
**Added**: V5.10.2 (Pass 1)

## Purpose
Per-subsystem read-only health check for agents and CI. Polls system state without triggering a sync or integrity run. Returns only safe status fields — no secrets, tokens, filesystem paths, or bucket internals.

## Response

### 200 OK — all subsystems healthy
```json
{
  "status": "ok",
  "version": "5.10.1",
  "subsystems": {
    "db": { "ok": true, "tables_present": true, "active_holdings_count": 1427 },
    "readers": { "ok": true, "finance_dir_configured": true, "enabled_source_count": 6 },
    "feeds": { "ok": true, "sentiment": "fresh", "sentiment_updated_at": "2026-05-29T09:00:00", "last_sync": "2026-05-29T08:00:00" },
    "gcs": { "ok": true, "configured": false, "note": "local mode" }
  }
}
```

### 200 OK — degraded (one or more subsystems unhealthy)
```json
{
  "status": "degraded",
  "version": "5.10.1",
  "subsystems": {
    "db": { "ok": false, "error": "OperationalError" },
    "readers": { "ok": true, "finance_dir_configured": true, "enabled_source_count": 6 },
    "feeds": { "ok": true, "sentiment": "fresh", "sentiment_updated_at": "2026-05-29T09:00:00", "last_sync": "2026-05-29T08:00:00" },
    "gcs": { "ok": true, "configured": false, "note": "local mode" }
  }
}
```

## Notes
- GCS subsystem: checks config presence + bucket/object metadata only in Pass 1. No write→read round-trip.
- All error fields use exception type name only (e.g. `"error": "OperationalError"`) — never full str(e).
- DB connections use `read_only=True` with `try/finally close()`.
