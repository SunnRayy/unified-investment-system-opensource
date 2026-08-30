# ADR-001: PIS vs AIA Holdings Conflict Resolution

**Date:** 2026-01-25
**Status:** ✅ IMPLEMENTED & ARCHIVED
**Archived:** 2026-01-25
**Location:** `docs/decisions/ADR-001-aia-pis-conflict.md`

## 1. The Issue

The Huinsight (V3) currently ingests holdings from two primary sources:

1. **PIS (Personal Investment System)**: Legacy system relying on manual entry in `investment_system.db`.
2. **AIA (AI Investment Advisor)**: New system syncing automated US trading positions via `aia_output.json`.

**The Conflict:**
When a US Asset (e.g., `US_STK_AAPL`) is tracked in **both** systems:

* PIS contains the record because the user manually logged the trade (Legacy behavior).
* AIA contains the record because it is actively managing the trade (New behavior).
* The Unified Database stores **both** records (distinct by `source_system`).
* **Result:** Reporting tools strictly sum `market_value` from the `holdings` table, causing **Double Counting** of assets.

## 2. V3 Architecture Context

Reference: `docs/architecture/data-pipeline-v4.md`

* **Identity Layer**: Correctly maps both PIS:`AAPL` and AIA:`AAPL` to the same Canonical ID `US_STK_AAPL`.
* **Storage**: The `holdings` table constraint is `UNIQUE(snapshot_date, asset_id, source_system)`, technically allowing co-existence.
* **Pipeline Logic**:
  * Section 6.4 addresses *Transaction Reconciliation* (linking AIA logs to PIS transactions), but does not explicitly define *Holdings Aggregation* logic for overlapping assets.
  * Section 4.3 (Taxonomy) explicitly defines "PIS Authoritative" for metadata, but no such rule exists for quantitative holdings data.

## 3. Decision Needed

We need a defined strategy to handle this overlap to ensure accurate Net Worth and Allocation reporting.

### Option A: Operational Segregation (Previously Proposed, Rejected?)

* **Logic**: User MUST NOT manually enter AIA-managed assets into PIS. PIS is for Non-US/Manual assets only.
* **Pros**: Simplest code. Clear "Source of Truth" separation.
* **Cons**: Breaks PIS as a "complete historical record". Requires user behavior change.

### Option B: Systematic Deduplication (Prioritize AIA)

* **Logic**: Modify `src/sync/allocation_sync.py` and reporting views.
  * *Rule*: If a Canonical ID exists in `source='AIA'`, **ignore** the `source='PIS'` record for aggregation/reporting.
* **Pros**: Allows PIS to remain a complete record (shadow copy) without affecting live reporting. Automation handles the conflict.
* **Cons**: Complexity in reporting queries. Potential confusion if PIS and AIA values diverge significantly (e.g., manual entry lag).

### Option C: Validation Warning (Enforce Single Source)

* **Logic**: The `validate_holdings` step detects duplicates.
  * *Rule*: If `count(source_systems) > 1` for any AssetID -> **Fail Sync** or **Alert User** to resolve.
* **Pros**: Guarantees data integrity by forcing resolution at the source.
* **Cons**: High friction. Blocks sync until resolved.

## 4. Recommendation

**Option B (Systematic Deduplication)** aligns best with a "Unified" system where data can ingest from everywhere but the system is smart enough to determine the active master.

* **PIS** remains the *Historical/Transaction* master (Manual entry backup).
* **AIA** becomes the *Live/Holdings* master for US assets.

---

## 5. Architect Decision (2026-01-25)

### Decision: **Option B Enhanced - Domain-Based Source Authority**

**APPROVED** with the following specifications:

### 5.1 Core Rules (REVISED - Presence-Based)

> **IMPORTANT UPDATE**: AIA manages positions across **all markets** (US, HK, CN), not just US equities.
> The authority rule is therefore **presence-based**, not pattern-based.

```
┌─────────────────────────────────────────────────────────────────────┐
│            HOLDINGS SOURCE AUTHORITY RULES (REVISED)                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  FOR EACH canonical_id IN holdings:                                 │
│                                                                      │
│    RULE 1: AIA Presence = AIA Authority                             │
│    ─────────────────────────────────────────                        │
│    IF exists_in(source='AIA'):                                      │
│       → AIA is AUTHORITATIVE (AIA actively manages this position)   │
│       → PIS record (if exists) marked is_shadow = TRUE              │
│                                                                      │
│    RULE 2: No AIA = PIS Authority                                   │
│    ─────────────────────────────────────────                        │
│    ELSE:                                                            │
│       → PIS is AUTHORITATIVE (AIA doesn't manage this asset)        │
│       → No shadow record exists                                     │
│                                                                      │
│    RULE 3: Reporting Aggregation                                    │
│    ─────────────────────────────────────────                        │
│    SELECT SUM(market_value) FROM holdings                           │
│    WHERE is_shadow = FALSE                                          │
│       → Only authoritative records in net worth                     │
│       → Shadow records excluded from allocation %                   │
│                                                                      │
│    RULE 4: Divergence Monitoring                                    │
│    ─────────────────────────────────────────                        │
│    IF |auth_value - shadow_value| / auth_value > 0.10:             │
│       → Log WARNING to sync_audit_logs                              │
│       → Generate user alert                                         │
│       → DO NOT block sync (informational only)                      │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Why Presence-Based is Better:**

| Pattern-Based (Original) | Presence-Based (Revised) |
|--------------------------|--------------------------|
| `US_STK_%` → AIA | If in AIA → AIA |
| `CN_STK_%` → PIS | If not in AIA → PIS |
| Needs pattern per market | One universal rule |
| Hardcoded assumptions | Self-describing by data |
| AIA scope changes = code changes | AIA scope changes = automatic |

### 5.2 Rationale

| Design Choice | Decision | Rationale |
|---------------|----------|-----------|
| Data Retention | Keep ALL records | Audit trail, reconciliation, regulatory compliance |
| Authority Model | **Presence-based** | If AIA manages it, AIA knows best; else PIS |
| Blocking Behavior | No sync blocking | High friction kills adoption; prefer warnings |
| PIS Integrity | Shadow flag, not deletion | Preserves complete historical record |
| Extensibility | Data-driven | AIA expanding to new markets = no code changes |

### 5.3 Schema Changes Required

```sql
-- Add to holdings table (src/database/schema.py)
ALTER TABLE holdings ADD COLUMN is_shadow BOOLEAN DEFAULT FALSE;
ALTER TABLE holdings ADD COLUMN authority_source VARCHAR(50);  -- 'AIA' | 'PIS' | etc.

-- NOTE: Pattern-based rules table removed. Authority is now presence-based:
--   IF canonical_id exists with source_system='AIA' → AIA authoritative
--   ELSE → PIS authoritative
-- This is simpler and handles AIA managing any market (US/HK/CN) automatically.
```

### 5.4 Implementation Plan

---

#### Phase 1-5: Huinsight Implementation ✅ COMPLETE

> **Status**: Implemented in Huinsight worktree branch
> **Date Completed**: 2026-01-25

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | Schema & Foundation | ✅ Complete |
| Phase 2 | Authority Resolution Logic (presence-based) | ✅ Complete |
| Phase 3 | Sync Pipeline Integration | ✅ Complete |
| Phase 4 | Reporting Layer Updates | ✅ Complete |
| Phase 5 | Divergence Monitoring | ✅ Complete |

**Key Implementation Details (for AIA reference):**

* Authority resolver uses **presence-based** logic: `IF source='AIA' exists → AIA authoritative`
* Holdings table has: `is_shadow BOOLEAN`, `authority_source VARCHAR(50)`
* Reporting queries filter: `WHERE is_shadow = FALSE`
* Divergence threshold: 10% triggers warning (no blocking)

---

#### Phase 5.5: Integration Handoff (Architect → AIA Team)

**Purpose**: Define the contract between Huinsight and AIA for holdings data exchange.

##### 5.5.1 Huinsight Expectations from AIA

Huinsight `src/sync/aia_sync.py` expects to read holdings from AIA. The sync process:

```
1. Read AIA holdings output file
2. Normalize to canonical_id format
3. Insert/update holdings with source_system='AIA'
4. Run authority resolver (marks PIS duplicates as shadow)
5. Run divergence checker (warns if PIS shadow differs >10%)
```

##### 5.5.2 Required AIA Output Contract

**File Location**: `{AIA_PROJECT}/output/holdings_snapshot.json`

**Schema (REQUIRED fields):**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["sync_timestamp", "holdings"],
  "properties": {
    "sync_timestamp": {
      "type": "string",
      "format": "date-time",
      "description": "ISO 8601 timestamp when snapshot was generated"
    },
    "holdings": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["symbol", "market", "quantity", "avg_cost_local", "currency"],
        "properties": {
          "symbol": {
            "type": "string",
            "description": "Ticker symbol (e.g., 'AAPL', '600519', '00700')"
          },
          "market": {
            "type": "string",
            "enum": ["US", "CN_SH", "CN_SZ", "HK"],
            "description": "Market identifier for canonical_id generation"
          },
          "quantity": {
            "type": "number",
            "description": "Number of shares/units held"
          },
          "avg_cost_local": {
            "type": "number",
            "description": "Average cost per unit in local currency"
          },
          "currency": {
            "type": "string",
            "enum": ["USD", "CNY", "HKD"],
            "description": "Local currency of the position"
          },
          "market_price": {
            "type": "number",
            "description": "Current market price (optional, Huinsight can fetch)"
          },
          "market_value_local": {
            "type": "number",
            "description": "quantity * market_price (optional, Huinsight can calculate)"
          }
        }
      }
    }
  }
}
```

**Example Output:**

```json
{
  "sync_timestamp": "2026-01-25T10:30:00Z",
  "holdings": [
    {
      "symbol": "AAPL",
      "market": "US",
      "quantity": 100,
      "avg_cost_local": 175.50,
      "currency": "USD",
      "market_price": 192.50,
      "market_value_local": 19250.00
    },
    {
      "symbol": "600519",
      "market": "CN_SH",
      "quantity": 10,
      "avg_cost_local": 1650.00,
      "currency": "CNY",
      "market_price": 1580.00,
      "market_value_local": 15800.00
    },
    {
      "symbol": "00700",
      "market": "HK",
      "quantity": 200,
      "avg_cost_local": 320.00,
      "currency": "HKD",
      "market_price": 385.00,
      "market_value_local": 77000.00
    }
  ]
}
```

##### 5.5.3 Canonical ID Mapping (Huinsight handles this)

| AIA `market` | Huinsight `canonical_id` Pattern | Example |
|--------------|---------------------------|---------|
| `US` | `US_STK_{symbol}` | `US_STK_AAPL` |
| `CN_SH` | `CN_STK_{symbol}.SH` | `CN_STK_600519.SH` |
| `CN_SZ` | `CN_STK_{symbol}.SZ` | `CN_STK_000001.SZ` |
| `HK` | `HK_STK_{symbol}` | `HK_STK_00700` |

##### 5.5.4 Sync Trigger Options

AIA can trigger Huinsight sync in two ways:

**Option A: File-based (Recommended for initial implementation)**
* AIA writes `holdings_snapshot.json` to agreed location
* User manually runs `python main.py --sync-aia`
* Or: cron job polls for file changes

**Option B: Event-based (Future enhancement)**
* AIA calls Huinsight webhook/API after trade execution
* Huinsight syncs immediately
* Requires Huinsight API server (not yet implemented)

---

#### Phase 6: AIA Holdings Export Implementation (AIA Team)

**Owner**: AIA Team
**Dependencies**: Phase 5.5 contract finalized
**Estimated Effort**: 4-6 hours

##### 6.1 Task Breakdown

| Task | File | Description | Priority |
|------|------|-------------|----------|
| 6.1.1 | `src/holdings/exporter.py` | **NEW** - Generate holdings snapshot JSON | P0 |
| 6.1.2 | `src/holdings/position_tracker.py` | **UPDATE** - Track all managed positions | P0 |
| 6.1.3 | `config/uis_integration.yaml` | **NEW** - Configure output path, markets | P1 |
| 6.1.4 | `tests/test_holdings_export.py` | Unit tests for export format | P1 |
| 6.1.5 | CLI command | Add `aia export-holdings` command | P1 |

##### 6.2 Implementation Details

**6.2.1 Holdings Exporter (`src/holdings/exporter.py`)**

```python
"""
Holdings snapshot exporter for Huinsight integration.

Generates JSON output conforming to Huinsight AIA Holdings Contract v1.0
"""
from datetime import datetime
from pathlib import Path
import json
from typing import List, Optional

from .position_tracker import PositionTracker
from ..config import get_config


class HoldingsExporter:
    """Export current holdings to Huinsight-compatible format."""

    def __init__(self, position_tracker: PositionTracker):
        self.tracker = position_tracker
        self.config = get_config()

    def export_snapshot(self, output_path: Optional[Path] = None) -> dict:
        """
        Generate holdings snapshot for Huinsight consumption.

        Returns:
            dict conforming to Huinsight AIA Holdings Contract
        """
        positions = self.tracker.get_all_positions()

        holdings = []
        for pos in positions:
            holding = {
                "symbol": pos.symbol,
                "market": self._get_market_code(pos),
                "quantity": float(pos.quantity),
                "avg_cost_local": float(pos.avg_cost),
                "currency": pos.currency,
            }

            # Optional fields if available
            if pos.current_price:
                holding["market_price"] = float(pos.current_price)
                holding["market_value_local"] = float(pos.quantity * pos.current_price)

            holdings.append(holding)

        snapshot = {
            "sync_timestamp": datetime.utcnow().isoformat() + "Z",
            "holdings": holdings
        }

        # Write to file if path provided
        if output_path is None:
            output_path = Path(self.config.uis_output_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)

        return snapshot

    def _get_market_code(self, position) -> str:
        """Map internal market representation to Huinsight market code."""
        market_map = {
            "NYSE": "US",
            "NASDAQ": "US",
            "SSE": "CN_SH",      # Shanghai Stock Exchange
            "SZSE": "CN_SZ",     # Shenzhen Stock Exchange
            "HKEX": "HK",
        }
        return market_map.get(position.exchange, "US")
```

**6.2.2 Position Tracker Updates (`src/holdings/position_tracker.py`)**

AIA must track ALL managed positions with:

```python
@dataclass
class Position:
    symbol: str
    exchange: str           # NYSE, NASDAQ, SSE, SZSE, HKEX
    quantity: Decimal
    avg_cost: Decimal       # In local currency
    currency: str           # USD, CNY, HKD
    current_price: Optional[Decimal] = None
    last_updated: Optional[datetime] = None

class PositionTracker:
    def get_all_positions(self) -> List[Position]:
        """Return all currently held positions across all markets."""
        # Implementation depends on AIA's internal data model
        pass

    def update_position(self, trade: Trade) -> None:
        """Update position after trade execution."""
        # Called after every buy/sell
        pass
```

**6.2.3 Configuration (`config/uis_integration.yaml`)**

```yaml
uis_integration:
  enabled: true
  output_path: "output/holdings_snapshot.json"

  # Markets AIA manages (for documentation)
  managed_markets:
    - US      # NYSE, NASDAQ
    - CN_SH   # Shanghai
    - CN_SZ   # Shenzhen
    - HK      # Hong Kong

  # Export settings
  export:
    include_market_price: true
    include_market_value: true

  # Validation
  validation:
    require_all_positions: true
    warn_on_stale_price_hours: 24
```

**6.2.4 CLI Command**

```bash
# Manual export
aia export-holdings

# Export to specific path
aia export-holdings --output /path/to/holdings_snapshot.json

# Export with validation
aia export-holdings --validate
```

##### 6.3 Validation Checklist

Before considering Phase 6 complete:

* [ ] `holdings_snapshot.json` generates without errors
* [ ] All managed positions included (US, CN, HK)
* [ ] JSON validates against schema in 5.5.2
* [ ] `sync_timestamp` is current (not stale)
* [ ] `market` field correctly maps to Huinsight canonical format
* [ ] `avg_cost_local` reflects true average cost (FIFO or weighted)
* [ ] Unit tests pass
* [ ] Manual test: Huinsight can parse the output with `python main.py --sync-aia`

##### 6.4 Edge Cases to Handle

| Scenario | Expected Behavior |
|----------|-------------------|
| Position sold to 0 | Exclude from holdings (or include with quantity=0?) |
| Partial position | Include remaining quantity |
| Multiple lots same symbol | Single entry with weighted avg cost |
| Pending order (not filled) | Exclude until filled |
| Currency mismatch | Always use position's local currency |
| Stale price data | Include with warning, or omit `market_price` |

---

#### Phase 7: End-to-End Integration Testing (Both Teams)

**Owner**: Architect coordinates, both teams execute
**Dependencies**: Phase 6 complete
**Estimated Effort**: 2-3 hours

| Task | Description | Owner |
|------|-------------|-------|
| 7.1 | AIA generates test snapshot with known values | AIA |
| 7.2 | Huinsight syncs AIA snapshot | Huinsight |
| 7.3 | Verify no double-counting in reports | Both |
| 7.4 | Verify shadow records created for overlaps | Huinsight |
| 7.5 | Test divergence warning (create intentional mismatch) | Both |
| 7.6 | Validate canonical_id mapping across markets | Both |

**Test Data Requirements:**

```
Scenario 1: US stock in both AIA and PIS
  - AIA: AAPL, 100 shares, $175.50 avg cost
  - PIS: AAPL, 100 shares, $170.00 avg cost (manual entry lag)
  - Expected: AIA authoritative, PIS shadow, ~3% divergence warning

Scenario 2: CN stock in AIA only
  - AIA: 600519, 10 shares, ¥1650 avg cost
  - PIS: (none)
  - Expected: AIA authoritative, no shadow

Scenario 3: CN fund in PIS only
  - AIA: (none)
  - PIS: 900002, 5000 units, ¥2.50 avg cost
  - Expected: PIS authoritative, no shadow

Scenario 4: HK stock in both
  - AIA: 00700, 200 shares, HK$320 avg cost
  - PIS: 00700, 200 shares, HK$315 avg cost
  - Expected: AIA authoritative, PIS shadow, ~1.5% divergence (no warning)
```

---

#### Phase 8: Documentation & Handoff (Architect)

**Owner**: Architect
**Dependencies**: Phase 7 pass
**Estimated Effort**: 1-2 hours

| Task | Description |
|------|-------------|
| 8.1 | Update `data-pipeline-v4.md` with AIA holdings sync details |
| 8.2 | Add AIA integration section to Huinsight README |
| 8.3 | Create runbook for common sync issues |
| 8.4 | Archive this decision document to `docs/decisions/` |

### 5.5 Migration Plan (Huinsight - Already Applied)

**Presence-Based Authority Logic (simplified):**

```sql
-- Applied during each sync run (not one-time migration)

-- Step 1: All AIA holdings are authoritative
UPDATE holdings
SET
    is_shadow = FALSE,
    authority_source = 'AIA'
WHERE source_system = 'AIA';

-- Step 2: PIS holdings become shadow IF same canonical_id exists in AIA
UPDATE holdings h
SET
    is_shadow = TRUE,
    authority_source = 'AIA'
WHERE h.source_system = 'PIS'
  AND EXISTS (
      SELECT 1 FROM holdings h2
      WHERE h2.canonical_id = h.canonical_id
      AND h2.source_system = 'AIA'
      AND h2.snapshot_date = h.snapshot_date
  );

-- Step 3: PIS holdings without AIA counterpart remain authoritative
UPDATE holdings
SET
    is_shadow = FALSE,
    authority_source = 'PIS'
WHERE source_system = 'PIS'
  AND is_shadow IS NULL;  -- Not yet marked
```

### 5.6 Testing Strategy

| Test Type | Description | Owner | Status |
|-----------|-------------|-------|--------|
| Unit | Authority resolver presence-based logic | Huinsight | ✅ Done |
| Unit | Shadow marking logic | Huinsight | ✅ Done |
| Integration | Full sync with mock duplicates | Huinsight | ✅ Done |
| E2E | AIA real output + PIS real data | Both | ⏳ Phase 7 |

**Test Scenarios (Updated for all markets):**

1. **US stock in both sources** → AIA authoritative, PIS shadow
2. **CN stock in AIA only** → AIA authoritative, no shadow
3. **CN fund in PIS only** → PIS authoritative, no shadow
4. **HK stock in both** → AIA authoritative, PIS shadow
5. **Divergence > 10%** → Warning logged, sync continues
6. **Asset removed from AIA** → PIS becomes authoritative again

### 5.7 Rollback Plan

If issues arise:

```sql
-- Remove shadow flag, revert to original behavior
UPDATE holdings SET is_shadow = FALSE, authority_source = NULL;

-- Reporting queries can ignore the columns until fix deployed
```

### 5.8 Success Criteria

**Huinsight (Phases 1-5):** ✅ Complete
* [x] Schema supports `is_shadow`, `authority_source`
* [x] Presence-based authority resolver implemented
* [x] Reporting filters `is_shadow = FALSE`
* [x] Divergence checker logs warnings

**AIA (Phase 6):** ⏳ Pending
* [ ] `holdings_snapshot.json` generates correctly
* [ ] All managed positions included (US/CN/HK)
* [ ] JSON conforms to contract schema
* [ ] CLI command `aia export-holdings` works

**Integration (Phase 7):** ⏳ Pending
* [ ] No double-counting in net worth reports
* [ ] Shadow records created for overlapping positions
* [ ] Divergence warning triggers correctly
* [ ] Canonical IDs map correctly across markets

### 5.9 Timeline (Updated)

| Phase | Owner | Status | Notes |
|-------|-------|--------|-------|
| Phase 1-5 | Huinsight Team | ✅ Complete | In worktree branch |
| Phase 5.5 | Architect | ✅ Complete | Contract defined (this doc) |
| Phase 6 | AIA Team | ⏳ **NEXT** | Est. 4-6 hours |
| Phase 7 | Both | ⏳ Blocked | Requires Phase 6 |
| Phase 8 | Architect | ⏳ Blocked | Requires Phase 7 |

**Remaining: 1-2 days (AIA implementation + integration testing)**

---

## 6. Open Questions (For Future)

1. **RSU Handling**: Should RSU vests from AIA (if any) follow same authority rules?
   * **Answer**: Yes, presence-based rule applies universally
2. **Historical Reconciliation**: Should we backfill shadow markers for historical snapshots?
   * **Answer**: Not needed; authority is computed on each sync
3. **Multi-Source Future**: If DSA provides holdings, how does it fit the priority?
   * **Answer**: DSA is market data only (prices), not holdings source
4. **Position Removal**: What happens when AIA sells entire position?
   * **Answer**: AIA excludes it from export, PIS record becomes authoritative again

---

## 7. Summary for AIA Team

**Your mission**: Implement Phase 6 to generate `holdings_snapshot.json`.

**Key deliverables:**

1. `src/holdings/exporter.py` - Export function
2. `src/holdings/position_tracker.py` - Track all positions across markets
3. CLI command: `aia export-holdings`

**Contract to follow:** Section 5.5.2 (JSON schema)

**Markets to support:** US, CN_SH, CN_SZ, HK

**Validation:** Run `python main.py --sync-aia` in Huinsight after generating output

**Questions?** Escalate to Architect.

---

*Decision Approved By: Architect*
*Date: 2026-01-25*
*Last Updated: 2026-01-25 (Revised to presence-based rules, added AIA implementation plan)*
*Implementation Owner: Huinsight Team (✅ Complete) + AIA Team (⏳ Phase 6)*
