# Database Design & Decision Guide

> The database is the **highest-stakes decision you make in week one** and the hardest to change in
> month twelve. Application code is rewritten freely; a schema with a year of data in it is not. Spend
> real thought here before writing features. This guide is the "more orientation in setup" for the DB —
> use it alongside Phase 5 of the setup prompt library, and capture the outcome in an ADR.

The single most important lesson, learned the hard way (see §6): **enforce your invariants in the
database, not just in application code.** A schema with no constraints is a spreadsheet with SQL syntax.

---

## 1. Decision tree (answer these in your DB-choice ADR)

**Q1 — Embedded or server?**
- **Embedded (SQLite, DuckDB):** the DB is a file in your repo's data dir. Zero ops, trivial local dev,
  great for single-user/desktop/analytical tools and for *starting*. Limits: one writer at a time
  (mostly), no network access, scaling and concurrency are bounded.
- **Server (PostgreSQL, MySQL):** a process you connect to. Concurrency, multi-client, network access,
  rich ecosystem, easy cloud hosting. Cost: ops overhead, a service to run.
- **Default:** start embedded if single-user/local; choose **PostgreSQL** the moment you need
  multi-user, a mobile/web client hitting it concurrently, or managed cloud hosting. **Postgres is the
  safe default for anything that might grow** — it scales from hobby to production without a rewrite.

**Q2 — Transactional (OLTP) or analytical (OLAP)?** *(The trap Huinsight fell into.)*
- **Row-store OLTP (SQLite, Postgres):** optimized for many small reads/writes/updates — the normal app
  pattern (insert a row, update a row, fetch a record).
- **Column-store OLAP (DuckDB, ClickHouse):** optimized for scanning/aggregating large tables — analytics,
  reporting. **Painful for frequent single-row DELETE/UPDATE:** columnar overhead + MVCC dead versions
  bloat the file (Huinsight saw **645 MB for 0.8 MB of actual data** because the sync DELETE+re-inserts ~100
  rows every run). If your workload is "mutate individual records often," do **not** pick a columnar DB
  as your primary store.
- **Default:** OLTP row-store as the system of record. Add a columnar/analytics engine *alongside* only
  if you genuinely have heavy analytical queries — don't make it the primary mutable store.

**Q3 — Relational or document/other?**
- **Default to relational.** Your data almost certainly has relationships (a holding belongs to an asset,
  an order has line items), and relational + constraints catches integrity bugs the application would
  miss. Reach for document (Mongo), key-value (Redis), graph, or vector stores only for a *specific*
  need (caching, true schemaless blobs, graph traversal, embeddings) — usually as a *secondary* store.

**Q4 — Single-user or multi-user, now and later?**
- If multi-user/multi-tenant is even *possible*, model a `users`/`accounts` table from day 0. Retrofitting
  identity into a single-row-auth schema later is a migration through every table. (Huinsight's single-row
  `auth_credentials (id=1)` blocks family/advisor sharing without a schema change.)

---

## 2. Schema design principles (the part that ages well)

1. **Constraints are not optional — they're your cheapest, most reliable tests.**
   - `PRIMARY KEY` on every table; `FOREIGN KEY` on every real relationship (orphan rows are silent data
     corruption — Huinsight valuation/analysis rows reference assets by loose strings with no FK, so they can
     orphan or double-count).
   - `NOT NULL` on everything that must exist; `UNIQUE` on natural keys.
   - `CHECK` constraints for domain rules: enums (`status IN (...)`), ranges (`quantity >= 0`), and —
     critically — **unit/currency invariants** (`currency IN ('USD','CNY')`; if you canonicalize to one
     currency, enforce it here, not just by convention). Huinsight stores "all values in CNY" as a *convention*
     with **0 CHECK constraints** — one mis-written row corrupts net worth silently.
2. **Normalize first; denormalize only with a measured reason.** Each fact in one place. Wide tables with
   half the columns NULL (a table serving two unrelated purposes) are a smell.
3. **Don't store queryable data as JSON blobs.** A `payload JSON` column means the client must fetch and
   deserialize everything to filter/sort/aggregate — unqueryable at the SQL layer, and a wall for any
   future mobile client. Promote the fields you'll query into real columns. (Huinsight's monthly balance-sheet
   tables are JSON blobs — a known mobile blocker.)
4. **Stable identity.** Use a surrogate primary key, but define and enforce the *natural* key with a
   UNIQUE constraint. For external clients, expose stable IDs (and consider an `updated_at`/`etag` for
   cache invalidation and cursor pagination) — auto-increment gaps make cursor pagination fragile.
5. **Temporal modeling — decide up front how history works.** Snapshots vs. event log vs. valid-from/
   valid-to. If you append snapshots, define the retention/archival policy *now* (Huinsight's holdings table
   grows forever with no policy). And beware the "latest" query: a global `MAX(date)` across mixed-cadence
   sources silently drops the laggards — use per-entity latest. Encode that rule in a **view** so every
   consumer gets it right by default, instead of relying on each query to remember.
6. **Index the queries you actually run.** At minimum a composite index for your hottest pattern (e.g.
   "latest row per entity" → `(entity_id, snapshot_date DESC)`). Don't index everything; index the real
   access paths.
7. **Money & units: pick a representation and enforce it.** Integer minor units (cents) or `DECIMAL` —
   never float for money. Store the currency/unit per row and CHECK it. If you convert, store the rate
   used per record for auditability.

---

## 3. Migrations — one source of truth, forward-only

- **Versioned, ordered, idempotent migrations** from commit #1 — even for an embedded DB. "Recreate the
  schema" is not a migration strategy once you have data.
- **One source of truth.** If you keep both a `schema.sql` and a migrations directory, they *will*
  diverge (Huinsight has migration files whose columns never made it into `schema.sql`, so a fresh install and
  a migrated install differ). Pick: either migrations are authoritative and `schema.sql` is generated, or
  `schema.sql` is authoritative and migrations only carry deltas — and enforce the choice.
- **Forward-only, additive by default.** `ADD COLUMN IF NOT EXISTS`; avoid destructive migrations without
  a backup + explicit human gate.
- **Never silently swallow a migration error** (`except: pass`) — a half-applied migration is worse than a
  loud failure. Log it and assert bootstrap completeness.
- A migration that drops/rewrites data is a destructive op → it goes behind the `guard-destructive.sh`
  hook and a human confirmation.

---

## 4. Designing for a future mobile/multi-client world

If a second client (mobile, public API) is plausible, the schema choices that make it painless are cheap
now and expensive later:
- Stable, exposed IDs + `updated_at`/`etag` (efficient sync + cache invalidation).
- Real columns over JSON blobs (so the client can paginate/filter server-side).
- FKs and CHECKs (so a consumer you didn't write can't corrupt data with an ad-hoc query).
- A `users` table and per-row ownership (so read-only sharing / multi-device doesn't need a schema
  migration).
- Latest-per-entity exposed as a view or a typed endpoint, not re-derived in every client.

---

## 5. Anti-patterns checklist (grep your own schema for these)

- [ ] Tables with 0 foreign keys / 0 check constraints → integrity is unenforced.
- [ ] A columnar/OLAP engine as the primary store for frequently-mutated rows → bloat + slow updates.
- [ ] `payload JSON` holding fields you need to query → unqueryable, mobile-hostile.
- [ ] Money in floats → rounding corruption.
- [ ] Global `MAX(date)` for "current state" across mixed-cadence sources → silent data loss.
- [ ] Single-row auth / no `users` table when multi-user is plausible → schema migration later.
- [ ] `schema.sql` and migrations both authoritative → fresh-install vs migrated drift.
- [ ] Append-only history with no retention policy → unbounded growth.
- [ ] Multiple overlapping tables for one concept with no FK linking them → ambiguous ownership.

---

## 6. Huinsight case studies (concrete consequences of skipping the above)

| Decision skipped | What happened in Huinsight |
|------------------|----------------------|
| Constraints (1 FK, 0 CHECK across 41 tables) | Currency/shadow/referential integrity rely on a runtime gate; one bad row corrupts net worth silently |
| OLAP engine (DuckDB) as the mutable store | 645 MB file for 0.8 MB of data; needs periodic manual compaction (~6 MB bloat/sync) |
| JSON-blob tables | Monthly financial history can't be queried/paginated server-side — a mobile blocker |
| Single-row auth | Can't add multi-user/sharing without a schema change |
| Two schema sources | Migration-only columns missing from `schema.sql`; fresh vs migrated installs diverge |

The pattern: each was a reasonable shortcut for "a single-user local tool," and each became a foundation
problem the moment the system tried to professionalize or add a client. **Decide deliberately, write the
ADR, and enforce in the schema.**

---

## 7. DB-choice ADR prompt (paste in setup Phase 5)

```
Write docs/decisions/ADR-00X-database.md using the ADR template. Decide and justify:
- Embedded vs server, and the specific engine (default: PostgreSQL unless single-user/local → SQLite).
- OLTP row-store as system of record? (If an analytical engine is wanted, it's a SECONDARY store.)
- Relational (default) vs other, and any secondary stores (cache/search/vector) with their specific need.
- Single- vs multi-user data model (model a users table now if multi-user is plausible).
- Migration authority (schema.sql-generated OR migrations-as-deltas — pick one).
- The core invariants to enforce as constraints (PK/FK/UNIQUE/NOT NULL/CHECK), especially units/currency.
- Temporal model (snapshot/event-log/valid-time) + retention policy + the latest-per-entity view.
Then scaffold schema with those constraints from the start — not "add them later."
```
