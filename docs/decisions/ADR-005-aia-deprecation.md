# ADR-005: AIA Integration Deprecation

**Status**: Accepted  
**Date**: 2026-05-22  
**Version**: V5.7.0

## Context

Huinsight V5.1 introduced an AIA (AI Investment Advisor) file sync layer that read trade logs, insights, and strategy profiles from AIA's exported JSON/Markdown files. This created a tight coupling between Huinsight and AIA's file export format, and introduced several maintenance problems:

1. **Brittle file parsing**: AIA trade logs had malformed date headings, oscillating decision_reason fields, and format drift that required defensive workarounds.
2. **Redundant data layer**: AIA's "insights" and "memos" were being hand-transcribed into Huinsight `insights`/`strategy_memos` tables anyway — the file sync was a redundant path.
3. **Confusing dual scope**: Strategy Alignment had both "AIA Profile" scope and "Strategic Targets" scope, requiring UI bridge aliases and schema versioning.
4. **AIA source as authority**: `source_authority.yaml` had an AIA catch-all rule (priority 10) that could override Financial_Summary_Excel for unrecognized assets.
5. **Dead code accumulation**: 7 sync modules, 3 API endpoints, and dozens of test fixtures accumulated around the AIA path.

RSU price was already replaced by yfinance in V5.2.1. The only remaining use of AIA was as a suggestion_source for historical trades and the file-sync path for insights/memos.

## Decision

Remove the AIA file sync layer entirely in a 6-phase staged deprecation:

1. **Phase 1**: Re-tag historical AIA trade sources to `imported` where appropriate.
2. **Phase 2**: Rename `aia_scope_*` DB keys to `target_scope_*` with bridge aliases for stale rows.
3. **Phase 3**: Collapse frontend dual-scope UI to single Strategic Targets scope.
4. **Phase 4**: Delete 7 AIA sync modules; remove 3 AIA-specific API endpoints; clean orchestrator, config, and schema.
5. **Phase 5**: Test suite cleanup — remove stale AIA test fixtures and update tests for new APIs.
6. **Phase 6**: Documentation updates — ADR, CHANGELOG, architecture docs, archive integration docs.

Historical AIA transactions remain in the DB as `source_system='AIA'` and are treated as LEGACY_TRANSACTION_SOURCES (lower priority than Schwab_CSV for dedup).

## Consequences

**Positive**:
- ~1,200 LOC deleted across 7 sync modules + 3 API endpoints
- No more AIA file format brittleness or oscillating sync warnings
- Strategy Alignment scope is simpler and consistent
- `raw_sections` in `/decisions/intelligence` always returns `[]` (no file parsing)

**Negative / Accepted**:
- Historical AIA-sourced trades remain with `suggestion_source='AIA'` — no behavioral change, just no new AIA imports
- Old `strategy_review_reports` rows with `aia_scope_*` format trigger one-time recompute on first GET /strategy/alignment request (self-healing)

## Alternatives Considered

- **Keep file sync, fix brittleness**: Would require ongoing maintenance for AIA format changes. Rejected — AIA is being phased out independently.
- **Keep the API endpoints**: `/decisions/audit` and `/strategy/memos/import-from-files` had no active callers. Rejected — dead endpoints are maintenance burden.

## Related

- ADR-001: AIA-PIS conflict resolution (historical)  
- ADR-003: Phase 9 PIS deprecation  
- Deprecation plan: internal (archived planning doc)
