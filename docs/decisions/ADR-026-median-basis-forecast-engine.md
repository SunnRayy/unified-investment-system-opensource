# ADR-026: Median-Basis Forecast Engine — One Engine, One Headline

**Date:** 2026-07-25
**Status:** Accepted
**Deciders:** Ray (Owner), Claude Code (Lead/Architect)
**Plan:** internal implementation notes (§2, §6b(a), workstreams R-1/R-2/R-5)

---

## Context

The Forecast & Planning page ran two math engines and displayed both, and every number
needed a caveat paragraph explaining why it disagreed with the other one. The owner's
own trigger for this redesign was exactly that caveat proliferation — quotes pulled
directly from the pre-redesign UI:

> "Probabilistic range (P1–P99, 10-yr default). For the deterministic ¥20,000,000 glide
> path at trailing TWR, see North Star"
>
> "from ~¥3.3M liquid NW (rebalanceable assets — excludes property, insurance,
> pension) at 10.8% annualized TWR (same basis) at ~¥45K/mo — current run-rate
> contributions — deterministic projection, not a forecast…"

The two tabs disagreed materially, not just cosmetically:

| Tab | Engine | Said |
|---|---|---|
| North Star | deterministic compounding at the **arithmetic** expected return | ¥20M target reached in **10.6 years** |
| Projections | Monte Carlo simulation at the same expected return + volatility | **~40% chance** of reaching the target by 11 years |

Both numbers were computed correctly from the same live inputs — they disagreed because
they are answering different statistical questions. The deterministic engine compounds
at the *arithmetic mean* annual return, which is the average of a right-skewed
distribution of possible compounding paths — a rate closer to the *best ~1-in-3*
outcome than to the *typical* one. Monte Carlo instead samples the full distribution and
reports what actually happens across thousands of simulated paths, which is dragged
below the arithmetic mean by volatility (a portfolio that returns +30% one year and −10%
the next does not compound at their +10% average). This is a well-known effect —
**volatility drag** — and it is large enough here (10.8% arithmetic vs. a computed
~1.76pp drag) to move the headline by more than a year.

Adding a fifth caveat paragraph could not fix a structural contradiction between two
correct-but-different numbers. Removing one engine could.

---

## Decision

**The Forecast page runs exactly one math engine.** The headline is the existing
deterministic compounding engine (`src.services.north_star_glide.future_value` /
`months_to_target` — unchanged, not reimplemented), evaluated at the
**volatility-drag-adjusted median return**

```
g = exp( ln(1+r) − σ²/2 ) − 1
```

instead of the raw arithmetic expected return `r`. This single formula
(`src.financial_analysis.projection_defaults.median_return`, R-1) is the only new piece
of math introduced by this ADR — everything downstream (the glide-path solver, the
`/forecast/levers` sensitivity grid, R-2) reuses it as a drop-in substitute for `r`.

`g` is the median (50/50) compound annual growth rate implied by `r` and `σ` under
lognormal-ish return assumptions — i.e. it is what the deterministic engine needs to be
told in order to reproduce **Monte Carlo's own median outcome**, without running any
simulation. On live 2026-07-25 data: `r = 10.8%`, `σ = 17.9%` → `g = 9.04%` (a 1.76pp
drag), and:

| Method | Years to target |
|---|---|
| deterministic @ arithmetic return (the old North Star headline) | 10.62y |
| **deterministic @ median return (this ADR's headline)** | **11.75y** |
| Monte Carlo P50 crossing (10k simulations, seed 42 — see Validation) | 11.63y |

(The R-1 unit test pins the same equivalence at 20k simulations against a fixture rather
than the live DB; both runs agree with the median-basis figure to within ~1%.)

The median-basis headline and the Monte Carlo P50 crossing agree within **~1%** — see
Validation below. That agreement is the whole point: it is what makes a live,
interactive lever table (`GET /forecast/levers`, R-2) feasible at all. Running full
Monte Carlo (thousands of simulated paths) for every keystroke on a savings/return/
volatility sensitivity grid would not be interactively cheap; evaluating a closed-form
deterministic formula at an adjusted rate is.

**The headline visibly changed** as a direct, expected consequence: ≈10.6y → ≈11.75y.
This is not a regression — it is the more honest number. 10.6y was the arithmetic-mean
path, roughly a **1-in-3** likely outcome (Monte Carlo showed ~40% probability of hitting
the target within 11 years, i.e. worse-than-even odds at that horizon). 11.75y is the
**50/50 point** — half of simulated futures reach the goal sooner, half later. The owner
reviewed and accepted this change explicitly conditioned on the plan's §4b hard
requirement: every one of the five inputs (net worth, TWR, volatility, monthly
contribution, target) must be a live computation, never a value frozen into code. (Two
recent incidents motivated that condition: a Buffett-indicator figure that had gone
stale against an unrefreshed FRED series, and a same-day `_Schawab_USD` double-counting
bug — both cases of a number that *looked* live but wasn't.)

Enforcement is split across two layers, and it is worth being precise about what each
actually proves:
- **Frontend** (`ux-command-center/tests/forecast-answer-section.test.tsx`): asserts the
  rendered headline tracks `base.years_to_target` — i.e. that the UI cannot have a year
  count baked into it. It does *not* exercise the five inputs; it proves only that the
  display is derived.
- **Backend** (`src/services/forecast_levers.py::compute_levers` + `tests/services/test_forecast_levers.py`):
  every one of the five inputs is resolved at request time from its existing
  single-source-of-truth function, so `years_to_target` moves when the underlying data
  moves.
- **Static**: a grep over the diff confirms none of the plan document's worked-example
  figures appear as literals anywhere in `src/` or `ux-command-center/`.

No single test asserts "the headline changes when any of the five inputs changes" end to
end; that would need a fixture DB per input. The combination above is what is actually
in place.

**Monte Carlo is not removed.** It continues to do the two things a closed-form formula
cannot: produce the P10–P90 fan chart (the shape of the full outcome distribution, not
just its median) and the per-goal probability figure. Only the single *headline* number
moved off the arithmetic-return deterministic engine and onto the median-return one.

### The ordering trap (§6b(a)) — SUPERSEDED 2026-07-26, see Amendment below

This subsection described a frontend percentile-of-value-path approximation that no
longer exists in the codebase (replaced by `crossing_time_percentiles()`, W-3). See
"Amendment (2026-07-26) — Your Path implementation, W-1…W-6" at the end of this document
for what replaced it and why the ordering trap itself is now structurally impossible
rather than merely documented.

---

## Consequences

**Positive:**
- One number, one page — no cross-tab reconciliation, no caveat paragraph explaining why
  two correct numbers disagree. All four caveats quoted in the plan's §4 table become
  structural non-issues rather than better-worded prose.
- The volatility lever becomes genuinely actionable: because the headline is now
  drag-sensitive, reducing portfolio volatility measurably moves the goal date sooner
  with **zero** extra return — a concrete, correct answer to the owner's ask for a link
  from "risk metrics" to "goal date."
- The lever table (`GET /forecast/levers`) is cheap enough to be interactive (no
  simulation in the hot path), because it reuses the same closed-form median-return
  substitution instead of re-running Monte Carlo per lever.
- Every number remains fully reproducible and instantly recomputable — no sampling
  noise, no simulation seed dependence, for the headline specifically.

**Negative / Trade-offs:**
- The headline visibly moved (~10.6y → ~11.75y) — a real, user-facing change the owner
  had to be walked through and explicitly accept, not a silent internal refactor.
- `median_return` is an approximation (lognormal-ish assumption on the return
  distribution); it is validated empirically against Monte Carlo's own median rather
  than derived as an exact identity for an arbitrary return distribution.
- Percentile-of-value vs. percentile-of-crossing-time (the §6b(a) ordering trap) is a
  standard approximation, not a mathematically exact confidence interval — must be
  labeled "likely range," never "confidence interval," in any UI copy. **SUPERSEDED
  2026-07-26**: the range is no longer derived from percentile value paths at all (see
  Amendment below) — but the "likely range, not a confidence interval" labeling rule
  still applies, now for a different reason (analytic-approximation tail error, not
  order-statistic mismatch).

**Neutral / Future work:**
- If the portfolio's return distribution is later found to deviate materially from
  lognormal (e.g. heavy negative skew from a concentrated single-stock position), the
  median-return formula's accuracy should be re-validated against a fresh Monte Carlo
  median rather than assumed to still hold.

---

## Validation

Measured live on 2026-07-25 data (25-year horizon, 10,000 simulations, seed 42):

| Percentile path | Crosses target at |
|---|---|
| P10 | 18.55y |
| P25 | 14.77y |
| **P50** | **11.63y** |
| P75 | 9.22y |
| P90 | 7.48y |

`/forecast/levers`'s `base.years_to_target` (the deterministic engine at
`median_return(r, σ)`) computed **11.75y** on the same data — within **~1%** of the
Monte Carlo P50 crossing (11.63y). This agreement is asserted as a regression test, not
just a one-time observation: if the two ever diverge by more than ~5%, the median-basis
unification has broken and needs re-investigation (e.g. a change in how volatility or
the horizon is computed on one side but not the other).

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| Keep both engines, add clearer caveat copy | Was the status quo the owner explicitly rejected — a fifth caveat cannot fix a structural contradiction between two correct-but-different numbers. |
| Make the headline the Monte Carlo P50 crossing directly (skip the closed-form formula) | Would require running/re-running a full simulation for every lever combination in `GET /forecast/levers`, making the interactive sensitivity table too slow to recompute per keystroke. The closed-form median-return substitution is what makes R-2's lever table cheap. |
| Show the Monte Carlo mean final value instead of switching the deterministic rate | Doesn't fix the underlying problem — the mean *final value* and the median *crossing time* are still different statistics computed differently; the disagreement would resurface as soon as anyone compared the two. |
| Drop the deterministic engine and show only Monte Carlo everywhere (including the lever table) | Rejected for cost: Monte Carlo per-lever-per-keystroke is not interactively cheap. Monte Carlo is retained only where it does something a closed form cannot (the fan chart, goal probability). |

---

## References

- Internal implementation plan — §2 (the unifying formula),
  §4 (caveat table), §4b (hard requirement: nothing static), §6b(a) (percentile ordering
  trap, superseded — see Amendment), §6b(b) (Section 1 must not read the what-if form)
- Internal implementation plan — W-1…W-6, the Amendment's
  source plan
- `src/financial_analysis/projection_defaults.py::median_return` (R-1) and
  `::crossing_time_percentiles` (W-3, see Amendment)
- `src/services/forecast_levers.py::compute_levers` (R-2/W-2, `GET /forecast/levers`)
- `src/services/goal_resolver.py::resolve_north_star_goal` (W-1, see Amendment)
- `docs/api-specs/forecast.md` — full response shape for `GET /forecast/levers`,
  including the W-1/W-2/W-3 additions (`goal`, `applied`, `base.crossing_years`)
- `ux-command-center/src/utils/crossingYear.ts` — `deriveCrossingYear`, retained ONLY
  for the p50 fan-chart marker after W-3 (chart geometry, not projection math — its
  module docstring still describes the pre-W-3 percentile-value-path approximation and
  is stale; the function's actual remaining call site is
  `ux-command-center/components/forecast/FanChart.tsx`)
- `ux-command-center/components/forecast/AnswerSection.tsx` — Section 1 ("The Answer"),
  the primary consumer of this ADR's headline and, since W-3, of `base.crossing_years`

---

## Amendment (2026-07-26) — Your Path implementation, W-1…W-6

**Plan**: internal implementation notes. Landed in three
commits: W-1 goal resolver (`20a8f3d`), W-2/W-3 server-side levers + crossing-time
percentiles (`15ab548`), W-4/W-5 page rebuild (`ae85845`).

This amendment records two changes to how this ADR's engine is invoked, plus the
correction to §6b(a) above. It does not revise the Decision itself (one engine, median
basis) — that stands unchanged.

### (a) Goal source

At the time of the original decision, both consumers of this ADR's engine
(`forecast_levers.compute_levers` and `north_star_glide.glide_path`) read the forecast
target directly from `config/verification.yaml: north_star.target_net_worth_cny`. The
Goals page, separately, has its own `goals` table with an owner-editable FIRE goal. These
were two unrelated sources that happened to agree at ¥20,000,000 on every date this ADR
was written and reviewed — which is exactly why nobody noticed that editing the FIRE goal
in the UI would not have moved the forecast headline. This is the same failure class as
`_Schawab_USD` (ADR-025 §3, two representations of the same money silently diverging) and
D-1 in the forecast page design brief (two goal dates on one page).

**Resolution (W-1, `src/services/goal_resolver.py::resolve_north_star_goal`)**: both
consumers now resolve the target through a single function:

```sql
SELECT id, name, target_amount, target_date
FROM goals
WHERE status = 'active' AND LOWER(goal_type) = 'retirement'
ORDER BY target_date DESC, id DESC
LIMIT 1
```

- Retirement-type only — a house or education goal must never hijack the lifetime
  projection.
- Furthest `target_date` wins a tie (the retirement horizon, not an interim milestone);
  `id DESC` is the final tiebreak.
- `LOWER(goal_type)` because `goal_type` is free text entered via the Goals form.

`config/verification.yaml: target_net_worth_cny` is **retained as a documented
fallback** (owner decision, 2026-07-26) — it is not deleted, and the resolver still reads
it when no active retirement goal exists, or the `goals` query itself fails. The fallback
is always a labelled state, never a silent substitution (AGENTS.md Rule 12): the response
carries `source: "config_fallback"` plus a `fallback_reason` string, and the resolver
never returns `None` and never raises out. A structural AST guard
(`tests/services/test_goal_resolver_structural_guard.py`) pins every read of
`target_net_worth_cny` in `src/` to exactly two files —
`src/services/verification_config.py` (the config loader itself) and
`src/services/goal_resolver.py` (the one permitted fallback reader) — with an
anti-vacuity assertion so the guard cannot silently stop testing anything.

Verified on live data at ship time: resolver returns `source="goals"`, name `FIRE`,
`target_amount=¥20,000,000` — identical to the prior hardcoded value, so the visible
headline did not move. The fix was for the *next* time the owner edits the goal, not for
today's number.

### (b) Crossing-time percentiles replace the ordering-trap approximation

The "likely range" under the headline used to be computed in the frontend
(`ux-command-center/src/utils/crossingYear.ts::deriveCrossingYear`) by taking the Monte
Carlo run's P25/P75 percentile **value** paths and finding where each crosses the target
— the approximation §6b(a) above described, including its counter-intuitive inversion (a
higher percentile of *value* crosses *sooner*, so P75 supplied the low year and P25 the
high year).

**W-3 replaces this** with `crossing_time_percentiles()` in
`src/financial_analysis/projection_defaults.py`, computed server-side and returned as
`base.crossing_years: {p25, p50, p75}` on `GET /forecast/levers`. For each percentile
`p`, it solves `P(t_p) = p/100` directly for `t_p`, where `P(t) = Pr[value(t) >= target]`
is evaluated analytically (lognormal approximation around the median-drift path, standard
normal CDF via `statistics.NormalDist`). `P(t)` is monotone non-decreasing in `t` by
construction, so `p25 < p50 < p75` always holds — **the ordering trap is deleted, not
just re-explained**: there is no longer an inversion to get backwards. The frontend
computes nothing; it renders three numbers from the response.

Two things about the new method must be stated precisely, because both are easy to get
wrong when reading the code later:

1. **The definition is "value at time t ≥ target", NOT first-passage time.** These are
   systematically different for a volatile portfolio — first-passage (the first moment
   the portfolio ever touches the target, even if it later dips back below) is always
   earlier than the "value at t" crossing this function reports. Any test pinning this
   function against Monte Carlo MUST compute the Monte Carlo side the same "value at t"
   way (`tests/financial_analysis/test_crossing_time_percentiles.py` does this); pinning
   against a first-passage Monte Carlo computation would show a spurious mismatch that
   looks like a bug but isn't.

2. **`median_return(r, σ) = exp(ln(1+r) − σ²/2) − 1` is retained unmodified** — this
   amendment does not touch it. The log-space drift `μ = ln(1+r) − σ²/2` used inside
   `crossing_time_percentiles`'s probability math satisfies `median_return = e^μ − 1`, so
   this is one formula expressed two ways (discrete vs. log-space), not a second formula
   competing with the first. The design mock's continuous `er − 0.5σ²` shorthand was
   evaluated and **not adopted** — see the accuracy comparison below, where it is also
   the less accurate of the two.

**Accuracy, and its consequence for the UI.** Benchmarked on live parameters against a
20,000-path Monte Carlo (2026-07-26, `docs/design/2026-07-26-your-path.dc.html.md` §3.4):

| Percentile | MC (truth) | Ours | Error | Mock's continuous formula | Error |
|---|---|---|---|---|---|
| p25 | 9.20y | 8.76y | 4.8% | 10.03y | 9.0% |
| p50 | 11.66y | 11.75y | 0.8% | 11.75y | 0.8% |
| p75 | 14.89y | 16.25y | 9.2% | 13.27y | 10.9% |

Ours overstates the spread (wider than truth on both tails); the mock's alternative
understates it — and is also less accurate on both tails, which is why it was rejected
rather than adopted alongside `median_return`. This is a **known limitation, not a solved
problem**: tail error of ~5–9% means the raw range (e.g. `8.76`–`16.25`) would be false
precision if rendered to more than one decimal. The UI renders the range rounded to 1
decimal and the method popover states this is an analytic approximation pinned to Monte
Carlo, not an exact confidence interval — the same "likely range, never confidence
interval" labeling rule this ADR already required, now justified by tail error rather
than by the (now-deleted) order-statistic mismatch.

### (c) No projection math in the frontend

W-2 extended `GET /forecast/levers` with three optional query params —
`savings_pct` (0–60), `return_pp` (0–6), `volatility_pp` (0–10) — so the lever sliders on
the redesigned page can query an arbitrary slider position, not only the fixed preset
steps. All three are computed server-side inside `compute_levers`, reusing the exact same
`median_return`/`future_value`/`months_to_target` chain as everything else in this ADR.
Omitting all three params returns a response byte-identical to the pre-W-2 shape (no new
key at all); supplying any subset adds one extra row to the corresponding lever list(s)
plus a `combined` row at the joint position, with clamped values echoed back in an
`applied` block so a clamped request stays visible to the caller. Out-of-range values
422 at the FastAPI `Query` layer before reaching `compute_levers`. The frontend debounces
slider input ~150ms and holds the previous response in flight — no spinner on a
single-digit-millisecond call, no flicker.

The alternative considered — a pre-computed grid the frontend could look up instead of
calling the API per slider move — was assessed and rejected: 13 (savings) × 13 (return) ×
21 (volatility) = 3,549 cells, ≈140 KB of payload, to save a 60-step bisection over a
closed-form exponential that costs microseconds. There is no compute-cost problem here to
solve; R-1/ADR-026 removed Monte Carlo from this hot path specifically so a live
sensitivity grid would be affordable, and it is.

`deriveCrossingYear` (`ux-command-center/src/utils/crossingYear.ts`) was NOT deleted —
it survives for exactly one purpose: positioning the p50 marker on the fan chart so it
aligns pixel-for-pixel with the drawn p50 curve (`ux-command-center/components/forecast/FanChart.tsx`).
That is chart geometry — finding where an already-rendered line crosses a
already-rendered horizontal target line — not projection math, and does not duplicate
`crossing_time_percentiles`. Its module docstring still describes the pre-W-3
percentile-value-path approximation and has not been updated to reflect this narrower
role; that is a stale-comment cleanup, not a behavioral discrepancy.
