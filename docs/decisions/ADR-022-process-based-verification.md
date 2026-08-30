# ADR-022: Process-Based Verification Alongside Outcome Verdicts

**Date:** 2026-07-08
**Status:** Accepted
**Deciders:** Ray (Owner), Claude Code (Fable-5 Lead Architect)

---

## Context

The V7.3.0 verification layer evaluated trades primarily by short-horizon price outcomes
(+30-day Good Call / Regret / Missed Opportunity). A July 2026 audit identified four
structural problems:

1. **Bucket mismatch**: compliance-forced sells (RSU Divest) and ratio-managed assets
   (paper gold) were being evaluated by price judgment — which is forbidden by their
   own rules. A 30-day outcome verdict for a compliance sell is both meaningless and
   actively misleading.

2. **Disposition-effect gap**: no systematic trigger existed when a position crossed a
   loss threshold. The owner had no structured mechanism to force a "should I still hold
   this?" review before the loss deepened further.

3. **Missing first-order KPIs**: contributions, time-in-market, and glide-path progress
   are the primary drivers of long-term wealth, but none were measured in the system.
   The verification layer only measured second-order outcomes.

4. **Signal integrity defects**: the drift metric always read 0 (dead JOIN since ADR-003);
   VOO/IVV/SPY emitted conflicting signals; the Buffett indicator variant was mislabeled.

The core design principle established in the PRD (2026-07-07 §0):
> *Decisions are judged by process quality at decision time (authorization, parameters,
> data verification), never by short-horizon price outcomes.*

---

## Decision

1. **Rule-bucket classification** (`value` / `ratio` / `liquidity` / `compliance`) on
   every `trade_logs` row. Implemented via `src/services/rule_bucket_classifier.py`
   using memo keywords + asset taxonomy. Historical rows backfilled by
   an internal audit script (dry-run CSV audit; owner sign-off before execute).
   Buckets are stored in `trade_logs.rule_bucket` (migration 010/V67).

2. **Process scorer** (`src/services/process_scorer.py`): value-bucket trades receive
   PASS/FAIL/UNSCORED based on three process checks (authorized, params_ok, data_verified);
   non-value-bucket trades receive Compliant/Violation only. Old outcome verdicts are
   archived in `verdict_archived` (never destroyed). Flag-gated:
   `process_verification.enabled = false` → legacy behaviour byte-identical.

3. **Loss-side trigger** (`value_trap_reviews` table, migration 011/V68): automatic scan
   when unrealized return crosses −25% → −35% → −45% (escalation after each
   "hold_with_thesis" ruling). `liquidate` ruling requires explicit `adversarial_ack`
   (422 without it) as a disposition-effect guard. Compliance/ratio assets excluded.

4. **North Star panel** (`src/services/north_star.py`, migration 013/V70): first-order
   KPI tracking — external contributions, time-in-market (avg/median holding days),
   glide-path progress, and an unforced-error log. Cash-flow tags heuristic classifies
   only `internal_transfer` with confidence; all other rows require manual tagging.

5. **Signal governance** (`metric_catalog`, `data_fixes`, migration 012/V69):
   staleness-based reliability (`RELIABLE` / `UNRELIABLE`); overdue data-fix
   auto-downgrades to UNRELIABLE; compliance/ratio signals suppressed in
   `/valuation/snapshot/latest` when UNRELIABLE.

6. **Insight promote gate** (migration 014/V71): `promote_insight()` enforces
   ≥70% confidence OR ≥3 validated cases (422 + reason otherwise). Every promote
   button on the UI shows pre-flight eligibility via `promote_eligible` /
   `promote_blocked_reason` fields on the insights list.

All schema changes are additive (`ALTER TABLE ADD COLUMN` / `CREATE TABLE IF NOT EXISTS`)
via the existing numbered migration runner. No reader, authority, shadow, or orchestrator-
phase code was modified.

---

## Consequences

**Positive:**
- Process quality and price outcomes are now independently measurable; a good-process /
  bad-luck trade is no longer mis-scored as a "Regret."
- Loss-side mandatory review removes the disposition-effect blind spot.
- North Star KPIs give the owner a direct view of the actual drivers of long-term wealth.
- Signal integrity defects (drift=0, Buffett mislabeling) are fixed and have governed
  infrastructure to prevent recurrence.
- `verdict_archived` preserves full pre-V7.4.0 history — no data destruction.

**Negative / Trade-offs:**
- Two-phase rollout (flag=false → CSV review → flag=true) adds owner friction before
  new scoring is live.
- `promoted_this_quarter` count in the governance report is an upper-bound estimate
  (no status-transition log on `ai_insights`).
- F1.4 admin review screen for ambiguous bucket rows is deferred (CSV audit + PUT
  endpoints cover v1 adequately).

**Neutral / Future work:**
- Structured `band_position.target_band` data to fill ratio-bucket scoring null placeholders.
- Structured execution quota data to fill compliance-bucket `execution_progress` nulls.
- Cloud Scheduler wiring for F2 scan (on-demand trigger + API endpoint ship in V7.4.0;
  scheduled trigger is deferred, consistent with pre-existing Cloud Scheduler deferral).
- F1.4 admin screen for manual bucket assignment on ambiguous historical rows.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| Replace outcome verdicts entirely with process scores | Outcome data is useful for long-term calibration; archiving is the right balance |
| Single "process quality" boolean instead of four bucket types | Buckets capture fundamentally different evaluation logics; a single flag loses that signal |
| Suppress non-value-bucket verdicts immediately (no flag gate) | Backfill audit required before any live change; flag gate is the safety mechanism |
| Compute glide path from a fixed target-date model | Owner's 17.1y target is known from PRD fixture; a simple comparison is more actionable |

---

## References

- `docs/prd-2026-07-07-process-verification-improvements.md` — full PRD
- Internal implementation plan
- ADR-003 — PIS deprecation / reader-first authority (why `target_allocations` JOIN was dead)
- `src/services/process_scorer.py` — scorer implementation
- `src/services/value_trap.py` — value-trap scan logic
- `src/services/north_star.py` — North Star panel implementation
- `src/services/metric_governance.py` — metric catalog overview
- `src/database/migrations/010_process_verification.sql` (V67)
- `src/database/migrations/011_value_trap_reviews.sql` (V68)
- `src/database/migrations/012_metric_catalog.sql` (V69)
- `src/database/migrations/013_north_star.sql` (V70)
- `src/database/migrations/014_insight_governance.sql` (V71)
