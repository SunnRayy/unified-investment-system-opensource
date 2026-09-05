# API Spec: FX Rate

> Feature: Single-endpoint USD/CNY exchange rate for display-only currency conversion
> Status: Implemented
> Last Updated: 2026-06-19

---

## Section A: API Contract

### Endpoint

| Method | Path | Description |
|--------|------|-------------|
| GET | `/market/fx-rate` | Latest USD→CNY rate for display-only conversion |

### Response Type

```typescript
interface FxRateResponse {
  pair: string;       // Always "USD/CNY"
  rate: number;       // Latest USD→CNY rate (e.g. 7.2345). All stored values are CNY; divide by this to convert to USD for display.
  as_of: string | null; // ISO 8601 timestamp of the rate, or null if unavailable
}
```

### Example Response

```json
{
  "pair": "USD/CNY",
  "rate": 7.2345,
  "as_of": "2026-06-19T08:00:00+00:00"
}
```

### Error Response

On service failure the endpoint returns HTTP 500:

```json
{
  "error": "fx-rate-fetch-failed",
  "detail": "<reason>",
  "status_code": 500
}
```

---

## Section B: Usage Contract

### Display Conversion (DISPLAY ONLY)

- All values stored in the database are in **CNY**.
- This rate is **for frontend display conversion only**. No stored values or backend financial calculations are altered.
- To display a CNY value in USD: `usd = cny_value / rate`
- The rate must never be used to mutate stored data.

### Fallback Behaviour

- If the currency service cannot fetch a live rate, `get_today_usd_cny_rate()` returns `7.0` (hardcoded fallback).
- When the fallback is used the frontend should display an "approx" warning near the converted figures.

---

## Section C: Data Source

| Field | Source |
|-------|--------|
| `rate` | `src.services.currency.get_today_usd_cny_rate()` → `src.data_manager.currency_converter.get_currency_service().get_latest_rate("USD", "CNY")` |
| `as_of` | `None` — the currency service does not expose a timestamp; field is always `null` |

---

## Validation Checklist

- [ ] Returns HTTP 200 with `pair`, `rate`, `as_of`
- [ ] `rate` is a positive float (≥ 1.0 for USD/CNY)
- [ ] `as_of` is ISO 8601 string or null
- [ ] Fallback 7.0 returned when service fails (no 500 unless unexpected exception)
