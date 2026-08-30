# ADR-003: Phase 9 PIS Legacy Sync Deprecation

**Date:** 2026-03-02
**Status:** Accepted
**Deciders:** Ray (architect)

---

## Context

Huinsight was built to replace the Personal Investment System (PIS) as the source of truth for investment data. As of V3.23.1, all asset types are covered by six independent source readers (Schwab CSV, CN Fund Excel, Gold Excel, Insurance Excel, RSU Excel, Financial Summary Excel). These readers write directly to DuckDB and supersede all PIS sync paths.

Three legacy sync modules exist in `src/sync/`:

| Module | Original role | Current state |
|--------|--------------|---------------|
| `pis_sync.py` | PIS Excel → holdings with FIFO cost basis | Superseded by 6 readers |
| `pis_sqlite_sync.py` | PIS SQLite → transactions, target allocations, tier assignments | Superseded by 6 readers + taxonomy engine |
| `aia_sync.py` (partial) | AIA JSON → US stock holdings snapshot | `sync_aia_holdings()` superseded by Schwab CSV reader; `reconcile_aia_trades()` still active |

---

## Decision

**Active calls and imports removed from `src/sync/orchestrator.py` in Phase 9:**
- `sync_pis_transactions()` — was section 2.1
- `sync_holdings_with_cost_basis()` — was section 2.2
- `sync_target_allocations()` — was section 2.5
- `sync_tier_assignments()` — was section 2.6
- `sync_aia_holdings()` — was section 2.4 (already commented out since Phase 1)
- All corresponding imports

**Module files retained** (`pis_sync.py`, `pis_sqlite_sync.py`):

Rationale:
1. These modules contain hard-won logic (PIS Excel format parsing, FIFO cost basis calculation from PIS data structures) that may be needed if the new readers encounter regressions.
2. No active calls means zero runtime risk from retaining them.
3. Deletion is a one-way operation with no recovery path — retention is free.

**`aia_sync.py` retained and partially active:**
- `sync_aia_holdings()` is dead (replaced by Schwab reader in Phase 1) — its import removed
- `reconcile_aia_trades()` is still called (orchestrator line ~1597) — AIA provisional trade reconciliation is still the intended workflow for time-sensitive trades

---

## Reversal

To re-activate PIS legacy sync (e.g., if a reader regresses):

1. Re-add imports to `src/sync/orchestrator.py`:
   ```python
   from src.sync.pis_sqlite_sync import sync_pis_transactions, sync_target_allocations, sync_tier_assignments
   from src.sync.pis_sync import sync_holdings_with_cost_basis
   ```
2. Re-add the function calls in sections 2.1, 2.2, 2.5, 2.6 (before the reader dispatch block)
3. Verify the PIS path is still configured in `config/settings.yaml` under `subsystems.pis.path`

---

## Criteria for Full Deletion (Phase 10+)

Delete `pis_sync.py` and `pis_sqlite_sync.py` when **all** of the following are true:

- [ ] All 6 source readers have run at least 3 consecutive successful monthly syncs
- [ ] FIFO cost basis validation shows 0 discrepancies for 2 consecutive syncs
- [ ] The PIS Legacy repo is formally decommissioned or archived
- [ ] Human explicitly approves deletion after reviewing the above

---

## Consequences

- **Positive:** Orchestrator no longer has a dependency on PIS file paths or SQLite DB. Huinsight is fully self-contained for data ingestion.
- **Positive:** Removes four error-prone sync paths that had known issues (PIS phantom transactions, SQLite lock contention, target allocation drift from stale PIS data).
- **Neutral:** ~1,200 lines of legacy code retained but unreachable at runtime.
- **Risk:** If a new reader silently fails, there is no PIS fallback. Completeness checker (`src/validation/completeness_checker.py`) is the safety net.
