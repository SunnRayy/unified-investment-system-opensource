# Design retrospective: what held up, what we'd rebuild

This project has been under continuous, real-money-tracking use for about
eight months. That's long enough to know which early decisions were right
and which ones were merely *survivable* — patched around often enough to
work, never quite worth the cost of redoing. This page is the honest
version of that account.

It also doubles as the closest thing this project has to a contribution
roadmap: the "what we'd rebuild" items below aren't vague direction, they're
scoped enough to actually start on. If you're looking for something
meaningful to work on, start here rather than with `git grep TODO`.

## What converged on being right

**Reader-first authority.** Exactly one of N data sources is authoritative
for a given holding at a given time; everything else — cached prices, an AI
advisor's opinion, a historical baseline import — is enrichment that can
read but never overwrite. This sounds obvious stated abstractly. It stopped
being obvious the first time two sources both claimed to know a position's
quantity and disagreed. Having a single, structural answer to "who wins" —
rather than a per-field heuristic decided at merge time — turned an entire
category of data-correctness incidents into a non-issue by construction.
The cost is that adding a new source means deciding its authority
relationship up front, which is friction a naive "just merge everything"
design doesn't have. That friction is the point.

**A config-driven reader engine.** Early readers were bespoke Python
modules — one per broker, each re-implementing "find the newest file →
parse it → normalize columns → hand off." Once seven of those existed, the
duplication was obvious, and so was the fix: a declarative YAML pipeline
(`src/sources/reader_config.py`'s schema) with an escape hatch (a named
"hook" function) for the genuinely bespoke 10% of logic each source has.
The generic dispatch this enables (`docs/adding-a-source.md`) wasn't
originally designed for third-party extensibility — it fell out of wanting
the *N*th internal source to not require touching the engine — and turned
out to generalize cleanly to "a source this project's maintainer doesn't
even know about" with zero additional engine work. That's a good sign a
design decomposed correctly.

**A post-sync integrity gate as a first-class artifact, not an afterthought.**
16 invariant checks run after every sync and are exposed at an API endpoint,
not just a log line. Concretely: "net worth changed by more than 2% since
last sync" or "a check has never had enough data to evaluate its own
invariant, so don't silently count it as passing" (`CheckResult` gained a
distinct `skipped` state after exactly that bug was found) are the kind of
thing that's easy to skip building and expensive to have skipped. Every
serious data-correctness incident in this project's history was caught
*because* a check like this existed to catch it, not because someone
happened to eyeball the number that day.

**Fail-closed read statuses.** A reader can fail four different ways: the
file is missing, it doesn't parse, it parses but fails format validation, or
it parses cleanly and legitimately contains zero rows this time. Those are
different states with different correct responses, and for a long time the
codebase collapsed all four into "empty DataFrame" — indistinguishable from
"the owner sold everything." A `read_status` enum with exactly one
affirmative value (`READ_STATUS_OK`: artifact found, validated, parsed) and
every consumer testing `== OK` rather than truthiness means a *new* failure
mode fails closed by default, instead of silently succeeding until someone
notices net worth is wrong.

## What we'd rebuild

**Normalization belongs in the reader's own contract, not a growing
if/elif ladder downstream.** `src/sync/phases/_ingest.py`'s
`_normalize_holdings_df` / `_normalize_transactions_df` accreted a
per-source branch for every column-naming quirk a new reader introduced —
`if source_system == "Schwab_CSV": rename cost_basis -> cost_price_unit`,
and five more like it. Each one was the locally-cheapest fix at the time it
was added. The honest shape is the inverse: every reader should emit the
contract's column names directly (the way the IBKR reader and this
project's own worked plugin example already do — see
`docs/adding-a-source.md`), and the six existing exceptions should be
migrated into their own hooks rather than a shared file that has to know
about every source that has ever existed. This is real, scoped,
independently-shippable work — six migrations, one source at a time, no
schema change.

**One P&L/valuation engine, not six independently-recomputing surfaces.**
For a period, WealthOS's asset list, the Performance page's summary/gains/
by-class views, the portfolio-semantics aggregate, and the AI context
export each had their own per-asset P&L loop — nominally computing the same
thing, actually drifting apart in small ways nobody noticed until a
specific number looked wrong on one page and right on another. The fix that
shipped (`src/services/pnl/`, one `compute_portfolio_pnl` engine every
surface now calls) replaced roughly 1,150 duplicated lines with about the
same number in one place, migrated one surface at a time behind a
byte-parity gate. The lesson generalizes past P&L: **any number computed
more than once, independently, by construction drifts.** The retrospective
item isn't "we fixed this" (we did) — it's "we should have built it this
way from the start," because the single-engine version was always going to
be correct, and the six-surfaces version was always going to eventually
disagree with itself. Watch for the next instance of this shape before it
becomes six copies too.

**Append-only snapshots with validity windows, instead of a mutable
`is_shadow` flag.** Point-in-time correctness — "what did the portfolio
look like as of a past date" — currently depends on an `is_shadow` boolean
that gets flipped by several independent sweep passes (staleness, co-
authority resolution, historical-baseline demotion), each with its own
threshold logic and its own edge cases about *which* row is the "current"
one for a given asset. It works, but reasoning about it requires holding
several sweep passes' interaction in your head at once, and every new sweep
is a new way to get that interaction wrong. An append-only design — every
snapshot immutable once written, "current" derived by a validity-window
query instead of a mutable flag several writers touch — would make a whole
class of "which pass ran last and what did it leave behind" bugs
structurally impossible, at the cost of a real migration and a genuinely
different mental model for anyone extending it. This is the single largest
item on this list, and the one most worth a fresh design doc before anyone
starts.

**A storage layer where compaction isn't a symptom of mutation-heavy
writes.** DuckDB's per-sync delete-and-reinsert pattern (see
[`docs/operations.md`](operations.md) for the mechanics) means the database
file grows independent of actual data volume, and the fix is a recurring
maintenance routine rather than something the storage model prevents. An
append-only snapshot design (the item above) would incidentally reduce this
— fewer deletes, less MVCC dead-version accumulation — but a storage engine
or access pattern chosen with write-amplification in mind from the start
would remove the need for `scripts/maint_db.py --compact-local` to exist at
all. Lower priority than the `is_shadow` item because it's a consequence of
that one, not an independent root cause.

## Frontend test suite: 207/242 passing, and what's actually in the 35

> **Update 2026-08-30:** the count is now **28 failing / 271 total**. The 7
> `operations-redesign.test.tsx` failures below were the one item on this list that no
> amount of code reading could settle, and the owner ruled them obsolete; deleting them
> took 35 → 28 with the passing count unchanged at 243. `KNOWN_FAILING_TESTS` in
> `.github/workflows/ci.yml` was lowered to match. Everything else on the list stands.

Found during the open-source release prep: every frontend test called
`render()` straight from `@testing-library/react`, and none of them wrapped
the component under test in the app's own provider tree (`AuthProvider` >
`ThemeProvider` > `PortfolioFilterProvider` > `CurrencyProvider` >
`LanguageProvider` > `DemoModeProvider`, plus a Router — see `index.tsx` /
`App.tsx`). Any test rendering a component that touched one of those contexts
crashed on mount. That was 33 of the original 50 failures, collapsed to one
cause and fixed with one shared helper (`ux-command-center/test-utils.tsx`,
exported as `render` so call sites didn't need to change, just the import
line). Two more mechanical layers came out from under the crash once it
stopped masking them — a couple of tests nested their own `<MemoryRouter>`
around already-Router-wrapped components (React Router throws on nested
routers), and a few `vi.mock()` the whole `services/api` module without
stubbing methods the shared providers now also call — both fixed the same
way: complete the mock, don't work around it.

**What's left (28) is genuine per-component drift, not one thing** — verified
by actually rereading each new failure list after every fix, not just
watching the count drop, because two of the fixes above briefly introduced
*new* failures a bare pass/fail number would have hidden.

| File | Failing | What's actually wrong |
|---|---:|---|
| ~~`tests/operations-redesign.test.tsx`~~ | ~~7 / 13~~ → 0 / 6 | **Closed 2026-08-30 — the owner ruled the 7 no longer warrant dedicated tests, and they were deleted.** The question was never answerable from the code: the assertions describe *intended* Operations-redesign behaviour ("shows source-level discrepancy instead of misleading 0 mismatches"), and only the owner could say whether the design still stood. One incidental finding while removing them: the `Audit.tsx:313` crash they surfaced (`summary.sync_changelog.length` on `undefined`) is **not** a component bug — `operations.py:435` always returns the field (`[]` when there is no report) and the TS type declares it non-optional, so it was purely a mock predating the field. The 6 surviving tests (Asset Case File healthy-signal, four Sync/Import History, integrity-colour) all pass; dead fixtures for the deleted pages were pruned with them. |
| `tests/analytics-batch5.test.tsx` | 6 / 6 | One mock gap led to another: fixing `AnalyticsAPI.getProjectionDefaults` (below) got further, then `renderGoals` crashed on `g.live.current_amount` — the file's `listGoals` mock is also incomplete. Stopped excavating once it stopped being a one-line fix; a good first PR if you're willing to read `pages/Analytics.tsx`'s goal-card rendering path. |
| `tests/import-adapters-panel.test.tsx` | 5 / 5 | Buttons the test looks for by name (Upload/Stage/Approve/Holdings/Transactions) aren't found — the component's UI was restructured after the test was written. |
| `tests/audit-page.test.tsx` | 4 / 4 | Page heading/copy text no longer matches. |
| `tests/performance-batch6.test.tsx` | 4 / 12 | CSS class assertions (`border-green-200` and similar) — styling drifted, not logic. |
| `tests/taxonomy-pages.test.tsx` | 2 / 10 | Content text not found ("Market Price", a column header) — likely a label rename. |
| `tests/routes-smoke.test.tsx` | 2 / 8 | Two pages' `data-testid` no longer matches — route or page-shell structure changed. |
| `components/ai-advisor/AssetAnalyzer.test.tsx` | 1 / 6 | Single disabled-state assertion mismatch. |
| `tests/analytics-batch1.test.tsx` | 1 / 4 | Single content-text assertion mismatch. |
| `tests/balance-sheet-api.test.ts` | 1 / 1 | API call made with different arguments than the test expects. |
| `tests/dashboard-batch2.test.tsx` | 1 / 1 | Same shape — a mock's call arguments don't match. |
| `tests/wealthos-regression.test.tsx` | 1 / 1 | Content text ("AAPL") not found. |

Already closed in the same pass, worth naming because it looked like content
drift and wasn't: `AnalyticsAPI.getProjectionDefaults` is a real, called API
method that two separate test files' mocks predated and never stubbed —
one-line fix, not a component bug.

Verify any of this yourself: `cd ux-command-center && npm test`. If the
numbers here stop matching what you see, that's a doc bug — open an issue.

## A pattern worth naming on its own

Several of the incidents behind the items above share one shape: **two
things that are supposed to represent the same value, computed or read
independently, agreeing at the moment someone looks and silently diverging
later.** The six P&L surfaces are one instance. A forecast target that used
to read from two independent config sources that happened to agree at one
specific number is another — editing the value in the UI silently didn't
move the number computed downstream, because downstream was reading the
*other* copy. This project now treats "can this value be derived instead of
duplicated" as a design question to ask explicitly, rather than trusting
that two things which agree today will still agree after the next edit.
It's a small habit with an outsized effect on which future bugs are even
possible to write.
