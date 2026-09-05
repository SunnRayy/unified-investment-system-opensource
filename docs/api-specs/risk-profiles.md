# API Spec: Risk Profiles

> Feature: Risk profile management — named allocation targets that drive the Strategy Adherence score
> Status: Implemented
> Last Updated: 2026-05-04

---

## Section A: API Contract

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/risk-profiles` | List all risk profiles |
| POST | `/risk-profiles` | Create a new risk profile |
| GET | `/risk-profiles/{profile_id}/allocations` | Get allocation targets for a profile |
| PUT | `/risk-profiles/{profile_id}/allocations` | Replace all allocation targets for a profile |
| POST | `/risk-profiles/{profile_id}/activate` | Set a profile as active (deactivates all others) |

### Response Types

```typescript
// GET /risk-profiles
interface RiskProfilesResponse {
  profiles: RiskProfile[];
}
interface RiskProfile {
  id: number;
  name: string;
  is_active: boolean;
  created_at: string;   // ISO datetime
}

// POST /risk-profiles — Request
interface CreateProfileRequest {
  name: string;
}

// GET /risk-profiles/{profile_id}/allocations
interface AllocationsResponse {
  profile_id: number;
  allocations: AllocationTarget[];
}
interface AllocationTarget {
  asset_class: string;    // sub-class level, e.g. "CN Equity", "US Equity"
  target_pct: number;     // 0–100
}

// PUT /risk-profiles/{profile_id}/allocations — Request
interface UpdateAllocationsRequest {
  allocations: AllocationTarget[];  // replaces all existing targets
}

// POST /risk-profiles/{profile_id}/activate — Response
interface ActivateResponse {
  activated_id: number;
  deactivated_ids: number[];
}
```

---

## Section B: Key Behaviours

- **Allocation targets are sub-class level** (CN Equity, US Equity, Bonds, etc.), not top-class. The Strategy Adherence score aggregates these to top-class by summing targets within the same parent before comparing to actual portfolio weights.
- **Activate is exclusive**: activating a profile deactivates all others. There is always at most one active profile.
- **PUT allocations replaces** — it is not a partial update. Send the full desired allocation list each time.
- **`taxonomy_classes.is_rebalanceable` is the authority** for whether an asset class participates in adherence scoring, not `asset_registry.is_rebalanceable` (which is unreliable for Insurance/Property).

---

## Section C: Router Registration

```python
# src/api/main.py
app.include_router(risk_profiles_router, prefix="/risk-profiles")
```
