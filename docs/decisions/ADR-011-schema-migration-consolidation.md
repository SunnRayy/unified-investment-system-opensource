# ADR-011: Schema / Migration Consolidation (Pass D)

**Date:** 2026-06-01
**Status:** Accepted
**Deciders:** Ray (Owner), Claude Code (Architect), Codex GPT-5.5 High (Reviewer)

---

## Context

Huinsight V5.10.4 defines its database schema in four separate places:

1. `src/database/schema.sql` — canonical base tables and sequences, run by `initialize_schema()` via naive `split(';')`.
2. `src/database/connector.py:run_migrations()` — incremental ALTERs, new table DDL, index creation, and data-fix statements. Runs at every server startup.
3. `src/classification/schema.py:create_classification_tables()` — 6 classification tables (`taxonomy_classes`, `asset_tiers`, `risk_profiles`, `risk_profile_allocations`, `classification_rules`, `classification_audit_log`). Called only during orchestrator sync, never at startup.
4. Ad-hoc `ensure_*` functions in route files — `ensure_sentiment_table()`, `ensure_goals_table()`, `_ensure_upload_history_table()`, `_ensure_financial_summary_tables()`. Called lazily at request or sync time.

Two concrete defects arise from this fragmentation (confirmed by Codex GPT-5.5 review):

**Defect 1 (Classification-table gap):** A server that had never run a full sync lacked the 6 classification tables entirely. These back the taxonomy API, risk-profile endpoints, and the rebalanceable filter used everywhere. Any endpoint touching taxonomy on a fresh or newly-deployed Cloud Run instance returned 500s until the first sync completed.

**Defect 2 (Request-time DDL on read-only connections):** `ensure_sentiment_table()` and `ensure_goals_table()` were called at request time. `get_db()` yields `read_only=True` connections. When those paths were hit before a writable path had been reached, the DDL failed — causing `GET /sentiment` to return 500 before the first `POST /sentiment/refresh`. This was the root cause of GitHub Issue #6, and the pattern could recur for any `ensure_*` function.

A third structural issue (not a current production defect but a latent risk): `initialize_schema()` is called only from the CLI `--init` and sync paths, not from the server lifespan. The server lifespan calls only `run_migrations()`. Since `run_migrations()` has `ALTER TABLE` migrations that assume base tables exist, running it against a brand-new empty server DB fails silently (every step is swallowed by `try/except`). The startup's `validate_operational_database()` then catches the resulting empty DB and refuses to start — so the failure is not silent to the operator, but the root cause is opaque.

---

## Decision

**Pass D (V5.11.0)** introduces `bootstrap_database(connector)` in `src/database/schema.py` as the single authoritative entry point for making any database current:

```
initialize_schema(connector)     # schema.sql — all base tables (CREATE IF NOT EXISTS)
connector.run_migrations()       # incremental ALTERs, new tables, indexes, data-fixes
_assert_bootstrap_complete(conn) # raises if any required object is missing
```

**What was changed:**

1. **`bootstrap_database()` created** in `src/database/schema.py`. Used by the FastAPI lifespan (`src/api/main.py`), the CLI `--init` path, and the CLI sync/check-integrity path. All three entry points now follow the same sequence.

2. **`run_migrations()` extended** with Migration 13 (6 classification tables — verbatim DDL from `create_classification_tables()`, no seeding), Migration 14 (3 `market_sentiment_cache` column ALTERs that previously lived only in `ensure_sentiment_table()`), and Migration 15 (4 hot-path indexes: `idx_holdings_source_system`, `idx_holdings_is_shadow`, `idx_transactions_asset_id`, `idx_trade_logs_linked_transaction_id`).

3. **`schema.sql` extended** with the same 6 classification tables and the 4 indexes so fresh `--init` installs also get them without needing a migration run.

4. **`_run_migration(label, stmt)` helper** introduced on `DatabaseConnector`. Catches only DuckDB "already exists"/"duplicate column" errors (safe idempotency) and logs everything else as `logger.warning` while appending to `self._migration_failures`. This ends the `except Exception: pass` silent-swallow pattern across all migration code (upgraded in existing migrations).

5. **`_assert_bootstrap_complete(connector)`** checks required tables, columns, and indexes after every bootstrap. If any non-idempotent failure was collected OR a required object is missing, raises `RuntimeError` before the server serves traffic. Required sets: `_REQUIRED_TABLES` (20 tables), `_REQUIRED_COLUMNS` (3 sentiment cols), `_REQUIRED_INDEXES` (4 hot-path indexes).

6. **`ensure_sentiment_table()`** simplified — the three ALTER statements are removed (now in Migration 14); the body retains only the `CREATE TABLE IF NOT EXISTS` as a safety no-op. The GET path (`GET /sentiment`) already had its DDL call removed in V5.10.4 (Issue #6 fix).

**What is NOT changed:** The underlying `ensure_goals_table()` / `POST /goals` read-only write-path bug (Codex finding F6) is logged internally and deferred — it requires a route-level fix (writable dependency), not schema changes.

---

## Consequences

**Positive:**
- Server startup now always reconciles a populated-but-incomplete DB (classification tables, sentiment columns) without requiring a sync.
- `GET /sentiment` and other read-path queries are safe on `read_only=True` connections from day one after bootstrap.
- Migration failures are now logged loudly and cause the server to refuse startup rather than silently serving broken data.
- Classification tables are in the canonical schema, not dependent on orchestrator execution order.
- 15 new tests cover the migration behaviors explicitly (idempotency, loud-fail, read-only safety, populated-but-incomplete regression).

**Negative / Trade-offs:**
- `initialize_schema()` now runs on every server startup (in addition to the existing `run_migrations()`). It is ~40 `CREATE TABLE IF NOT EXISTS` statements against an existing DB — benchmarked at < 100ms on local DuckDB — acceptable overhead.
- `ensure_*` functions are now partially redundant. They remain callable for safety but are no-ops on bootstrapped databases. Removing them entirely is deferred.

**Neutral / Future work:**
- The deferred full unification (version-ledger table, iterating `migrations/` directory including orphaned 001–007 files, replacing naive `split(';')` in `initialize_schema()`) is documented but not built in this pass. The blast radius and sequencing risk of that rewrite warrant a separate pass.
- `POST /goals` and `DELETE /goals` still pass a `read_only=True` connection from `get_db()` to `ensure_goals_table()` and `create_goal()`. The INSERT fails silently via the `except Exception: return {"error": ...}` wrapper. This is a pre-existing bug, logged internally by this pass.
- Orphaned tables (`committee_decisions`, `market_events`, `economic_indicators`, `exchange_rates`, `schema_snapshots`, `rsu_vesting_schedules`, `source_authority_rules`, `asset_taxonomy`) are reviewed and confirmed as low-risk; dropping them is deferred pending a usage audit.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| Build the full version-ledger migration runner now | Roadmap explicitly defers: large blast radius, risk of green-but-broken merge (see Pass 1/C lessons). Sequenced as a separate pass. |
| Keep lazy `ensure_*` functions without folding into migrations | Perpetuates the read-only-connection hazard and the classification-table sync dependency. Root cause is not addressed. |
| Add a DB sentinel check (only run schema.sql if base tables absent) | Weaker: would skip schema.sql even when newer objects (classification tables, sentiment columns) are missing from an existing DB. |
| Keep all classification DDL in orchestrator only | Requires a sync before any taxonomy endpoint works; fragile on new deploys and Cloud Run cold starts. |

---

## References

- `src/database/schema.py` — `bootstrap_database()`, `_assert_bootstrap_complete()`, `_REQUIRED_TABLES/_COLUMNS/_INDEXES`
- `src/database/connector.py` — `_run_migration()`, Migration 13/14/15, `_migration_failures`
- `src/database/schema.sql` — classification tables + indexes appended at bottom
- `src/classification/schema.py` — `create_classification_tables()` (DDL source for Migration 13)
- `src/api/routes/sentiment.py` — `ensure_sentiment_table()` simplified
- `tests/database/test_migrations.py` — 15 new Pass D tests
- Internal implementation plan (Pass D schema-migration index)
- Codex GPT-5.5 (High) review findings F1–F9 incorporated before implementation
- GitHub Issue #6 — `GET /sentiment` 500 on read-only connection (root cause class closed)
