# ADR-004: Import Adapter Authority Resolution via Dynamic Rule Injection

**Date:** 2026-05-09
**Status:** ✅ IMPLEMENTED
**Branch:** `feature/import-adapter-framework`

## 1. Context

The Import Adapter Framework (V5.6.1) allows users to define custom data sources (e.g. a second brokerage CSV). When an adapter is approved, its metadata is stored in `import_adapter_approvals`:

```sql
import_adapter_approvals (
    adapter_key      VARCHAR PRIMARY KEY,
    source_system    VARCHAR NOT NULL,       -- e.g. "Broker_IBKR"
    asset_prefixes_json JSON NOT NULL,       -- e.g. ["US_STK_", "US_ETF_"]
    authority_priority  INTEGER NOT NULL,    -- e.g. 7
    enabled          BOOLEAN NOT NULL DEFAULT TRUE
)
```

The authority resolver (`src/identity/authority_resolver.py`) determines which source "wins" when multiple sources report the same asset. It loads rules from `config/source_authority.yaml` — a static file with entries like `US_STK_* → Schwab_CSV at priority 8`.

**The gap:** Approved adapter metadata is stored but never reaches the resolver's rule list. If an adapter source overlaps with an existing reader on the same asset prefix, the resolver doesn't know the adapter exists and cannot apply its priority.

## 2. Decision Drivers

- **Source deprecation trajectory:** PIS was deprecated in V5.0.4. AIA deprecation is being designed. The long-term direction is toward Huinsight database as the sole authority source, eliminating static YAML configuration.
- **Cloud compatibility:** On Cloud Run, `source_authority.yaml` is baked into the Docker image. Runtime writes to YAML don't persist across deploys.
- **Simplicity:** Authority resolution only runs inside the sync pipeline (orchestrator Phase 2.7). No external callers need adapter rules.

## 3. Options Considered

### Option A: Dynamic injection at sync time ✅ Selected

Inject adapter rules into the resolver's in-memory rule list in orchestrator Phase 2.7, immediately after constructing the `AuthorityResolver`.

```python
# orchestrator.py Phase 2.7, after AuthorityResolver(config=config)
adapter_rules = _load_adapter_authority_rules(connector)
resolver.rules.extend(adapter_rules)
resolver.rules.sort(key=lambda x: x.get('priority', 100))
```

| Pro | Con |
|-----|-----|
| ~15 lines, minimal change | Rules are transient (in-memory only) |
| DB is single source of truth for adapter state | — |
| No YAML drift risk | — |
| Cloud-compatible (DB-backed) | — |
| `enabled` flag in DB toggles authority on/off | — |
| Aligns with trajectory toward DB-only authority | — |

### Option B: Write to `source_authority.yaml` on approval — Rejected

Append rules to YAML when adapter is approved. Resolver loads them naturally.

| Pro | Con |
|-----|-----|
| Resolver code unchanged | YAML ↔ DB drift risk |
| Single config file for all rules | Disabling adapter doesn't auto-remove YAML rules |
| — | Cloud Run: YAML lost on redeploy |
| — | Harder to test (file I/O) |

### Option C: Resolver reads DB directly — Rejected (for now)

Modify `AuthorityResolver.__init__` to accept a DB connector and query approvals.

| Pro | Con |
|-----|-----|
| Works outside sync context | Resolver gains DB dependency (currently pure config) |
| Clean separation: YAML for built-in, DB for adapters | More invasive change |

## 4. Decision

**Option A — Dynamic injection at sync time.**

Rationale:
- The resolver's only consumer is `HoldingsAggregator` inside the sync pipeline. No other code path queries authority rules.
- The transient-rules downside is irrelevant since rules are only needed during sync.
- This is the smallest change with the lowest risk surface.
- It naturally evolves toward the end state where `source_authority.yaml` itself could be loaded from DB, since the injection point is already in place.

## 5. Implementation

### Files Changed

| File | Change |
|------|--------|
| `src/sync/orchestrator.py` | New `_load_adapter_authority_rules()` helper; called in Phase 2.7 after resolver init |
| `tests/sync/test_adapter_authority_injection.py` | Unit test: adapter rules injected, resolver returns adapter source for matching assets |

### How It Works

1. During sync Phase 2.7, after `AuthorityResolver(config=config)` loads static YAML rules
2. `_load_adapter_authority_rules(connector)` queries `import_adapter_approvals WHERE enabled = TRUE`
3. For each approval, `asset_prefixes_json` is expanded into individual pattern rules (e.g. `["US_STK_", "US_ETF_"]` → two rules with `US_STK_*` and `US_ETF_*` patterns)
4. Rules are appended to `resolver.rules` and re-sorted by priority
5. `HoldingsAggregator.apply_authority_rules()` then sees adapter sources alongside built-in sources

### Priority Convention

- Built-in readers (Schwab, CN Fund, Gold, Insurance, RSU): priority 8
- Financial Summary catch-all: priority 9
- AIA catch-all: priority 10
- Adapters: user-specified (default 8, can be lower to override a built-in reader)

Lower priority number = higher authority. If an adapter is approved with priority 7 and prefix `US_STK_*`, it will take precedence over Schwab_CSV (priority 8) for US stocks.

## 6. Future Direction

When AIA is fully deprecated and all sources are reader-based:
1. `source_authority.yaml` rules can be migrated to a DB table (e.g. `source_authority_rules`)
2. `AuthorityResolver.__init__` would read from DB instead of YAML
3. The adapter injection would become unnecessary — all rules would already be in the same table
4. The resolver could optionally be given a DB connector (Option C) at that stage

This ADR's approach (Option A) is a stepping stone that doesn't block the future migration.

---

*Decision By: Human + Agent*
*Date: 2026-05-09*
