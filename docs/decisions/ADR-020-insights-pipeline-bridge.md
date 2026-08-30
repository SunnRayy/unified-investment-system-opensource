# ADR-020: Insights pipeline — two-store bridge + P9 continuity step

**Date:** 2026-07-03
**Status:** Accepted
**Deciders:** Ray (Owner), Claude Code / Fable 5 (Architect)

---

## Context

The Huinsight AI Advisor pipeline had two disconnected insight stores with no automatic crosswalk:

- **`ai_insights`** (50 rows): written by `review_generator.extract_insights()` after every AI review. Consumed only by the Insight Library tab.
- **`insights`** (44 rows): written by manual entry + the `promote_insight()` bridge (used once ever) + cross-check lessons. Consumed by Review Center, Decision Hub, Growth Timeline, and the behavioral/verification machinery.

This architecture caused seven observable problems: counts diverged (23 vs 25 vs 25 across surfaces), adoption history stopped at 2026-04 (all post-April AI output went to `ai_insights` only), Portfolio-vs-Benchmark chart was always zero (benchmark columns omitted from the `verification_logs` INSERT), "good call" verdict was unreachable for sell trades (keyword branch returned on REGRET without evaluating GOOD_CALL), recurrence was inflated 173× (orphaned report re-incremented on every extraction), scoring and verification ran on-demand only (no post-sync trigger), and the two stores were operationally siloed even though they modeled the same concept.

The full diagnosis is in the internal implementation plan (§1).

---

## Decision

**Keep `ai_insights` as the raw library** (LLM output, status lifecycle: `raw → recurring → deprecated`) and **`insights` as the ledger** (Decision Hub, verification history, Growth Timeline). The two tables serve different consumers and a schema merge was rejected for blast radius (see Alternatives).

**Idempotent auto-bridge (B1)**: when an `ai_insights` row has `category='recommendation'` OR reaches `status='recurring'`, upsert an `insights` row keyed on `observation_source='ai_insights:<id>'` (the same key the Promote button already writes). `ai_model` is set to the actual generator model (Promote-first). Raw one-off lessons stay library-only. Promote button is retained as the manual override for anything below the auto-bar. Implementation: `review_generator.py` extraction flow.

**Additive advisory P9 `'Insights continuity'` step (B2)**: appended at the tail of `run_full_sync_v3()` after the integrity gate. Never blocks sync — each sub-call (`score_all_trades()`, `recompute_auto_links()`, `compute_verification_report()` guarded by a 24h gate, behavioral metrics) is wrapped in isolated `try/except` and logged at WARNING on failure. Enabled via `insights_continuity.enabled` in `settings.yaml`. Uses the shared write connection.

**Single shared adoption-metric function (B3)**: `compute_insight_adoption_metrics()` is the sole source for `adoption_rate` and `total_insights` consumed by both `decisions/stats` and `verification/latest`. Historical snapshots in `verification_logs` are preserved separately.

---

## Consequences

**Positive:**
- Counts converge going forward without any manual Promote step.
- Adoption history and the Growth Timeline resume updating after every sync.
- Verdicts, insight-trade links, verification snapshots, and behavioral radar are self-refreshing post-sync instead of page-load/manual-only.
- Single adoption-metric source eliminates the `decisions/stats` vs `verification/latest` divergence.
- First live proof confirmed Jul 4 2026-07-04 (4 rows bridged, verification computed, behavioral 6 dims).

**Negative / Trade-offs:**
- Bridge adds a tail dependency: if B1 extraction fails silently, counts diverge again. Mitigated by isolated `try/except` + WARNING log surfaced in the sync report.
- Two-table architecture remains — operational complexity is not reduced; developers must know both tables.
- Bridged 'review'-source insights never match trade `suggestion_source` → `linked_trades=0` for bridged rows. Known gap; documented internally.

**Neutral / Future work:**
- Full schema merge (`ai_insights` + `insights` → one table) explicitly rejected for blast radius (~44 rows of manually curated data at risk). Revisit only if the bridge proves insufficient after 3+ months of operation.
- `missed_opportunity` mixed-text edge case deferred.
- P7/P9 `match_trades` interplay should be documented.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| Full table merge (ai_insights + insights → single table) | Blast radius too large: 44 rows of manually maintained insight data, 17 `insight_trade_links`, and multiple consumer APIs would all need migration. Bridge achieves behavioral consistency at lower risk; table merge can follow later if needed. |
| Keep manual Promote only | Promote was used once ever since introduction. The silo would remain permanent. |
| Post-sync bridge as a separate cron job / scheduler | Cloud Scheduler infra is deferred; the P9 tail step reuses the existing sync event, requires no new infra, and runs at the right moment (immediately after new AI output is available). |

---

## References

- Internal implementation plan — full diagnosis + fix program
- `src/services/insights_continuity.py` — B2 step implementation
- `src/services/review_generator.py` — B1 bridge in extraction flow
- `src/api/routes/decisions.py` / `src/api/routes/verification.py` — B3 shared metric consumer
- ADR-019 — cloud settings persistence (companion infrastructure decision)
