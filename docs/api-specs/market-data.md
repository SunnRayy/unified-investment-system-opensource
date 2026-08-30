# API Spec: Market Data

Added in V4.6.0. All endpoints under `/market-data`.

---

## POST /market-data/refresh

Fetches live realtime prices for all active non-shadow portfolio holdings (US stocks, ETFs, RSUs via yfinance). CN funds are excluded (handled by daily NAV sync).

**Auth**: None (internal use)

**Response 200**:
```json
{
  "refreshed": 8,
  "skipped": 15,
  "errors": 0,
  "holdings_updated": 41,
  "timestamp": "2026-03-28T01:27:28.278189+00:00"
}
```

| Field | Description |
|-------|-------------|
| `refreshed` | Assets with prices successfully fetched and upserted to `market_daily` |
| `skipped` | Assets with unrecognized codes (CN funds, unsupported prefixes) |
| `errors` | Transient fetch failures |
| `holdings_updated` | Holdings rows updated by `_update_from_dsa()` propagation |
| `timestamp` | UTC ISO 8601 time of refresh completion |

**Side effects**: Persists result JSON to `sync_state` table with key `market_data_last_refresh`.

---

## GET /market-data/status

Returns market data provider status and last refresh info.

**Response 200**:
```json
{
  "last_refresh": {
    "refreshed": 8,
    "skipped": 15,
    "errors": 0,
    "holdings_updated": 41,
    "timestamp": "2026-03-28T01:27:28.278189+00:00"
  },
  "providers": [
    { "market": "us", "fetcher": "yfinance", "asset_count": 8, "status": "active" },
    { "market": "cn_fund", "fetcher": "akshare", "asset_count": 15, "status": "active" }
  ],
  "staleness": "fresh"
}
```

| Field | Description |
|-------|-------------|
| `last_refresh` | Last refresh result from `sync_state`, or `null` if never run |
| `providers[].market` | `"us"` or `"cn_fund"` |
| `providers[].asset_count` | Active holdings count by market prefix |
| `staleness` | `"fresh"` (<4h), `"aging"` (4–24h), `"stale"` (>24h), `"never"` |

---

## POST /ai-advisor/analyze

Run full single-asset technical analysis with LLM synthesis.

**Request body**:
```json
{ "asset_code": "US_STK_MSFT", "analysis_type": "full" }
```

**Response 200**: `AnalysisResult` — see `src/api/routes/ai_advisor.py` for full schema. Includes `technical_signals`, `llm_analysis` (structured), `llm_analysis_markdown` (Chinese prose).

---

## GET /ai-advisor/analyze/search

Search asset registry for analyzable assets with portfolio context.

**Query params**: `q` (min 2, max 100 chars)

**Response 200**:
```json
[
  { "code": "RSU_AMZN", "name": "Amazon RSU", "in_portfolio": true, "position_pct": 0.04 }
]
```

Fallback: if no registry match, returns `[{ "code": "QUERY_UPPER", "name": null, "in_portfolio": false }]`.

---

## GET /ai-advisor/analyze/history

Returns analysis history, optionally filtered by asset.

**Query params**: `asset_code` (optional), `limit` (1–100, default 10)

**Response 200**: Array of `AnalysisHistoryItem` with id, asset_code, asset_name, timing_signal, confidence, created_at, model_used.

---

## GET /ai-advisor/analyze/{analysis_id}

Fetch a specific analysis by ID (full result including technical_signals and llm_analysis_markdown).

**Response 200**: Full analysis object. **404** if not found.
