# Architecture Decision Records (ADRs)

This directory contains architectural decisions made for the Huinsight.

## Index

| ADR | Title | Date | Status |
|-----|-------|------|--------|
| [ADR-001](ADR-001-aia-pis-conflict.md) | PIS vs AIA Holdings Conflict Resolution | 2026-01-25 | Implemented |
| ADR-002 | *(intentional gap — number reserved, no decision was made at this number)* | — | — |
| [ADR-003](ADR-003-phase9-pis-deprecation.md) | Phase 9 PIS Deprecation — Remove Legacy Sync Paths | 2026-03-02 | Implemented |
| [ADR-004](ADR-004-import-adapter-authority.md) | Import Adapter Authority Injection | 2026-05-09 | Implemented |
| [ADR-005](ADR-005-aia-deprecation.md) | AIA Integration Deprecation | 2026-05-22 | Implemented |
| [ADR-006](ADR-006-gcs-cloud-persistence-topology.md) | Cloud / GCS Persistence Topology | 2026-05-29 | Accepted |
| [ADR-007](ADR-007-native-currency-pnl.md) | Native-Currency P&L (V5.2.0+) | 2026-05-29 | Accepted |
| [ADR-008](ADR-008-illiquid-asset-valuation.md) | Valuation Methodology for Illiquid Assets | 2026-05-29 | Proposed |
| [ADR-009](ADR-009-sentiment-feed-integration.md) | Sentiment / External-Feed Integration | 2026-05-29 | Proposed |
| [ADR-010](ADR-010-ai-advisor-llm-integration.md) | AI-Advisor / LLM Integration | 2026-05-29 | Proposed |
| [ADR-011](ADR-011-schema-migration-consolidation.md) | Schema / Migration Consolidation (Pass D) | 2026-06-01 | Accepted |
| [ADR-012](ADR-012-pass-f-db-evolution.md) | Pass F — DB Evolution (Version-Ledger, Compaction, Dry-Run, Orphan Drop) | 2026-06-04 | Accepted |
| [ADR-013](ADR-013-authority-resolver-semantics.md) | Authority Resolver Priority Semantics | 2026-06-07 | Accepted |
| [ADR-014](ADR-014-config-driven-reader-engine.md) | Config-Driven Reader Engine (Workstream B1) | 2026-06-12 | Accepted |
| [ADR-016](ADR-016-co-authority-multi-broker.md) | Co-Authority Multi-Broker Resolution (Workstream C) | 2026-06-15 | Accepted |
| [ADR-017](ADR-017-ibkr-flex-ingestion.md) | IBKR Flex Query Ingestion (Workstream C) | 2026-06-15 | Accepted |
| [ADR-018](ADR-018-import-adapter-config-convergence.md) | Import-Adapter ↔ Config-Driven Reader Convergence | 2026-06-20 | Accepted |
| [ADR-019](ADR-019-cloud-settings-persistence.md) | Cloud Settings Persistence — Reader-Seed and Sync-Flush Invariants | 2026-06-26 | Accepted |
| [ADR-020](ADR-020-insights-pipeline-bridge.md) | Insights Pipeline — Two-Store Bridge + P9 Continuity Step | 2026-07-03 | Accepted |
| [ADR-021](ADR-021-single-production-cloud-mirror.md) | Single-Production Topology — Cloud Is Production, Local Is a Verified Mirror | 2026-07-06 | Accepted |
| [ADR-022](ADR-022-process-based-verification.md) | Process-Based Verification Alongside Outcome Verdicts | 2026-07-08 | Accepted |
| [ADR-023](ADR-023-reader-mapping-management.md) | Reader Mapping Management (DB-Backed Reader-Mapping Layer) | 2026-07-18 | Accepted |
| [ADR-024](ADR-024-monthly-attribution-and-durable-flow-tags.md) | Monthly Attribution Engine & Durable Flow-Tag Identity | 2026-07-20 | Accepted |
| [ADR-025](ADR-025-investment-contributions-savings-authority.md) | Investment Contributions & Savings — 月度收支 as the Authority | 2026-07-21 | Accepted |
| [ADR-026](ADR-026-median-basis-forecast-engine.md) | Median-Basis Forecast Engine — One Engine, One Headline | 2026-07-25 | Accepted |
| [ADR-027](ADR-027-single-pnl-engine.md) | One Read-Only P&L Engine — Surfaces Are Thin Formatters | 2026-08-03 | Accepted |
| [ADR-028](ADR-028-bilingual-ui-i18n-foundation.md) | Bilingual UI Foundation — react-i18next, `zh-CN`, and an English-Default Catalog | 2026-08-21 | Accepted |

### Date-Named Decisions (not sequentially numbered)

| File | Topic | Date |
|------|-------|------|
| [2026-05-05-duckdb-size-management.md](2026-05-05-duckdb-size-management.md) | DuckDB file compaction strategy | 2026-05-05 |

---

## Numbering Policy

- ADR numbers are assigned **sequentially** starting from 001.
- **ADR-002 is an intentional gap.** A grep of the entire repository confirms no code
  or documentation references ADR-002. The number is reserved; no stub is created
  because fabricating a "superseded" record for a decision that never existed would
  be misleading.
- Date-named files (e.g., `2026-05-05-duckdb-size-management.md`) are also valid
  decision records. They are indexed in the separate table above. New decisions
  should prefer the `ADR-NNN-title.md` format for discoverability.
- **ADR-015** was never written — B3 was descoped; the number is reserved as an intentional gap.
- Next free number: **ADR-029**.

---

## ADR Format

Each ADR uses `docs/decisions/template.md`. Sections:

1. **Context** — What problem forces a decision, and when
2. **Decision** — What was chosen and why (reference specific files)
3. **Consequences** — Positive, negative, and neutral/future
4. **Alternatives Considered** — Why other options were rejected

## Creating a New ADR

1. Copy template: `cp docs/decisions/template.md docs/decisions/ADR-NNN-title.md`
2. Fill in all sections
3. Update this index
4. Commit before merging the branch that implements the decision

**When an ADR is required:** Any branch that introduces a new architecture decision
must write the ADR before the branch merges — see [`CONTRIBUTING.md`](../../CONTRIBUTING.md)
for the general PR checklist.

---

*Last Updated: 2026-08-21*
