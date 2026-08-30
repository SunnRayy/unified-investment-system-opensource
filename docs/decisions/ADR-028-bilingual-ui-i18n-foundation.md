# ADR-028: Bilingual UI Foundation — react-i18next, `zh-CN`, and an English-Default Catalog

**Date:** 2026-08-21 · **Finalized:** 2026-08-24 (WS-7, after the program shipped)
**Status:** Accepted — implemented in full, V8.0.0
**Deciders:** Ray (Owner), Claude Code (Architect)

> This ADR was drafted in WS-0 while several things were still unknown. It was rewritten at
> WS-7 to describe what was **actually built and measured**, not what was projected. Where a
> WS-0 estimate turned out wrong, the real figure is used and the correction is stated.

---

## Context

Huinsight was published as open source on 2026-08-18. Its target user is a Chinese person with
overseas investments: the Financial Summary workbook is already in Chinese, the reader
mappings and `config/reference_sheet.yaml` key on Chinese Excel column headers, and
`tools/demo_data/persona.yaml` describes a Chinese household. The data model was built for
exactly that person. **The interface around it is English**, and the README says so:

> *"i18n covers the sidebar only. Page content, chart labels, and AI-generated text are not
> localized."*

So the system asks for a Chinese spreadsheet and answers in English.

The existing i18n was `ux-command-center/src/i18n/translations.ts`: a flat
`Record<Lang, Record<string, string>>` of ~37 entries in which **the English string was the
lookup key** (`t('Balance Sheet')`), read through a bespoke React context
(`src/context/useLanguage.tsx`). It covered the sidebar and nothing else.

Program BIL extends this to the whole UI. The WS-0 estimate was "roughly 2,500 strings across
94 files"; the delivered figure is **2,706 keys across 91 converted files** in 11 namespaces.
That is a different problem from 37 sidebar labels, and it forced the decisions below at the
foundation.

---

## Decision

### 1. `react-i18next` + `i18next` (MIT) replaces the hand-rolled catalog

`src/i18n/index.ts` initializes i18next with statically-imported JSON catalogs (no HTTP
backend — this is a self-hosted dashboard and the catalogs are small), `fallbackLng: 'en'`,
`supportedLngs: ['en', 'zh-CN']`, and 11 namespaces split along nav sections:
`common`, `portfolio`, `performance`, `reports`, `incomeExpense`, `valuation`, `aiAdvisor`,
`operations`, `management`, `system`, `errors`.

`translations.ts` is deleted. `useLanguage.tsx` survives as a **thin shim** over
`useTranslation()`, preserving `{ lang, setLang, toggleLang, t }` so `Layout.tsx`,
`LanguageCard.tsx`, `App.tsx` and `test-utils.tsx` need no churn. `LanguageProvider` is now
an `I18nextProvider`.

Why a library rather than growing the existing map:

- **English-string-as-key collides at scale.** "Total" needs different Chinese as a KPI
  title than as a table footer. Dot-path keys (`nav.balanceSheet`, `section.reports`) do not
  collide, and they survive an English copy edit — changing "Balance Sheet" to "Balance
  Sheet (Net)" in the old scheme silently orphaned the Chinese translation.
- **Tooling.** `i18next-parser` mechanically finds `t()` calls across the whole app and syncs
  both catalogs; a hand-rolled map has no equivalent.
- **Interpolation and plurals** are native, and i18next's plural suffixes degrade correctly
  for Chinese (single `_other` form).
- **A stranger contributing a translation to a public repo already knows this API.**

`<Trans>` is available and **is** needed — see Consequences; an earlier draft of the program
plan claimed zero rich-text call sites, which measurement disproved.

### 2. Locale code is BCP-47 `zh-CN`, not `zh`

`zh` is a macrolanguage. Committing to `zh-CN` now means adding `zh-TW`/`zh-HK` later is
additive rather than a rename across every catalog, key and stored preference. It is also
what `Intl.NumberFormat` / `Intl.DateTimeFormat` want in WS-1 (`zh-CN` yields 万/亿 compact
notation; bare `zh` is ambiguous about region).

The cost is a live migration: the shipped build stores `localStorage['uis-lang'] = 'zh'`.
`normalizeLegacyStoredLanguage()` in `src/i18n/index.ts` rewrites that value **once,
idempotently, before i18next initializes**. Without it, i18next — which does not do
non-explicit fallback — would resolve the stored `'zh'` to `'en'` and silently reset every
user who had already chosen Chinese. `document.documentElement.lang` moved out of a React
effect into a module-scope `i18n.on('languageChanged', …)` listener, because it is a
document concern that must hold regardless of which component tree is mounted.

### 3. English is the default locale, and every EN catalog value is byte-identical to the
literal it replaces

This is the load-bearing choice. `test-utils.tsx` wraps every test in `LanguageProvider`,
`vitest.setup.ts` pins the test locale to `en`, and ~304 existing assertions read
`getByText('<English literal>')`. Because the EN catalog reproduces those literals exactly,
**the existing test suite becomes the extraction's correctness gate at zero cost**:
paraphrase a string while wrapping it and a test goes red.

The failure mode this creates is that nothing then exercises the Chinese catalog, so
`tests/i18n-zh-canary.test.tsx` flips to `zh-CN` and asserts real Chinese renders in the
sidebar, that `resolvedLanguage === 'zh-CN'`, and that the legacy `'zh'` normalization is
idempotent.

Public/fresh installs default to English; Ray's own deployment is pinned to `zh-CN`.

### 4. A purpose-built literal ratchet, not ESLint

The program plan originally specified ESLint's `react/jsx-no-literals`. **This repo has no
ESLint dependency and no ESLint config anywhere.** Adopting one mid-program, in a public
repo, to serve a single rule means importing a whole lint toolchain plus the configuration
argument that comes with it — and then fighting a generic rule's false positives.

`ux-command-center/scripts/i18n-ratchet.mjs` does the job in 322 lines with **zero new
dependencies**: it parses real TSX with the `typescript` package that is already a
devDependency, and flags raw user-visible literals in JSX text nodes, in
`title=`/`label=`/`placeholder=`/`aria-label=`, and in JSX child expression containers.
It encodes this project's allowlist directly (punctuation and symbols, `WealthOS`/`Huinsight`,
short ALL-CAPS ticker and currency codes, Material Symbols ligature names, and equality
operands like `mode === 'day'`).

It scans **only** the files listed in `ux-command-center/i18n-converted-files.json`. That
list *grows* as workstreams convert files, so unconverted files stay silent — no wall of
noise to learn to ignore — and a converted file can never silently regress.

`scripts/i18n-parity-check.mjs` is its companion. It fails on any key present in one locale
but not the other, any empty value, and any missing namespace file — **plus a third check
added mid-program (Check 3): any namespace named by a `useTranslation()` call in a converted
file must have a non-empty catalog in every locale.** Check 3 exists because the first two
were provably blind to a whole failure mode; see Consequences. It is scoped to the
converted-files list, since an unconverted file may legitimately name a namespace that is not
populated yet. Both scripts run in `npm run i18n:check`.

### 5. The AI advisor's section identity is a stable ASCII ID, and language is resolved by one function

`src/services/ai_advisor/prompts.py` defined the LLM's JSON contract as **literal Chinese
strings** — `BRIEF_SECTION_KEYS = ["宏观形势", "持仓分析与风险预警", …]`. Those strings were
stored verbatim in `ai_reports.content_json`, returned as-is by the API, and matched by literal
in `BriefSection.tsx`. `brief_generator.py` already carried a Traditional→Simplified repair
map, so the coupling was known-fragile *before* adding a second output language.

- Section identity is now a stable ASCII ID (`macro_outlook`, `holdings_risk`, `risk_alerts`,
  `action_items`, `watchlist`); display labels come from the frontend catalog and are **never
  trusted from the LLM's output language**. This deletes the Traditional/Simplified drift class
  outright rather than adding a third repair entry to it.
- The same bug had a second instance: the `'高准确度'`/`'中准确度'`/`'低准确度'` scorecard badges
  were free-text *values* the frontend string-matched. Fixed at the root as an explicit
  `accuracy_tier: high|medium|low` enum. A third instance was found in production — see
  Consequences.
- **Legacy rows are adapted on read, never migrated.** Chinese-keyed rows map to IDs via the
  extended `_BRIEF_KEY_VARIANTS` through `adapt_stored_content_json`. Old briefs keep
  rendering; nothing stored is rewritten. An AST guard over the router enforces that every
  `content_json` read site goes through the adapter — added because the original wiring
  did not (see Consequences).
- **One prompt scaffold**, with EN/ZH as sibling values in the same literal so an edit to one
  is adjacent to the other in the diff. A structural test asserts key-set parity across
  variants.
- **`resolve_language()` + migration V89 (`user_profile.language`)** give one resolver with
  explicit precedence: request override → `user_profile.language` → `settings.yaml` → `'en'`,
  mirroring the `goal_resolver.py` pattern from V7.7.0 rather than letting a localStorage value
  and a server value drift into the two-sources-for-one-number bug class. Interactive
  generation sends the frontend's current locale, so UI language drives AI language with no
  separate control; the persisted value exists because **scheduled and cloud jobs have no
  request locale**. A startup WARNING fires when the column is NULL, so the state is visible
  rather than silently English.
- **V89's data step is an UPSERT, not an UPDATE**, because `user_profile` has **zero rows** — a
  plain UPDATE would have matched nothing while still burning the migration version gate, which
  is exactly the V61 decimal-precision no-op already in this project's history. Verified
  read-only against the real database before shipping: the evidence query matches 43 rows, so
  the owner's instance seeds `zh-CN` on next boot, while a fresh install has no row and
  resolves to `en`.

### 6. The frontend enters CI

`.github/workflows/ci.yml` had **no Node steps at all** — the vitest suite ran only when a
human remembered to. A `frontend` job now runs `npm ci`, `npm run build`,
`npm run i18n:check` and `npx vitest run`. Build and i18n:check are hard gates. The test
step is a **ratchet against a recorded baseline** (`KNOWN_FAILING_TESTS: 35`), because the
suite carries 35 failures that predate this program; the job fails if the count grows and
warns if it shrinks. Nothing is skipped, deleted or `continue-on-error`'d.

---

## Consequences

**Positive:**

- Chinese is a first-class locale in the same catalog format a public contributor expects,
  with a mechanical parity gate instead of review-by-eyeball.
- The existing English test suite became free regression coverage for a 91-file refactor —
  the failure count never moved off the recorded 35 across the entire program.
- A user who had already chosen Chinese is not reset by the `zh` → `zh-CN` migration.
- The frontend has CI coverage for the first time — build, i18n, and a test ratchet.
- CJK fallbacks (`PingFang SC`, `Hiragino Sans GB`, `Microsoft YaHei`, `Noto Sans SC`) added
  to `--font-sans` and `--font-mono` in `src/styles/colors_and_type.css` and mirrored in
  `tailwind.config.js`. Neither Inter nor Roboto Mono ships a CJK glyph, and `--font-mono`
  drives `.uis-num`/`.uis-cell` — Chinese in a table cell was falling through to a bare
  system face with mismatched metrics, mid-row.

**Negative / Trade-offs:**

- Four new npm dependencies (`i18next`, `react-i18next`,
  `i18next-browser-languagedetector` runtime; `i18next-parser` dev). Bundle grew from
  ~1,504 kB to 1,545 kB raw / 393 kB gzip.
- `i18next-parser@9.4.0` is **deprecated upstream** ("use i18next-cli instead") and pulls
  ~149 transitive dev packages including outdated `glob`/`rimraf`/`inflight`. It was kept
  anyway because the maintained replacement, `i18next-cli`, requires Node >= 22 while this
  project's `engines` field still supports `^18 || ^20`. It is a devDependency only — it
  ships in no build output — and it is not run in CI. Revisit when the Node floor rises.
  Note its binary is named `i18next`, not `i18next-parser`.
- The ratchet is a bespoke tool with its own maintenance cost, and its known false-positive
  and false-negative classes are documented in a header comment rather than being someone
  else's problem. In particular it cannot see strings built in plain TS above the JSX, or
  passed through custom props other than the four enforced attributes.
- Two sources of font truth (`colors_and_type.css` and `tailwind.config.js`) must be kept in
  sync by hand; the file's own comment already acknowledged this.

**The dominant finding: five gates that were green because they could not go red.**

Every one was caught by accident or by a deliberate red-proof — **none by the gate itself.**
Recording them here because the pattern, not any individual bug, is the reusable lesson.

1. **The ratchet's attribute scan was silently inert.** `ts.forEachChild` aborts when the
   callback returns truthy, and the collector returned its accumulator array, so scanning
   stopped after the first child and rule 2 never ran. Found by the "prove it goes red" step
   on the very first gate.
2. **`vitest.setup.ts` made `lookupLocalStorage` untestable.** The language detector probes
   `window.localStorage` once at init and caches the result; the mock was installed in
   `beforeEach`, i.e. *after* i18next had loaded, so the probe failed and the detector
   abandoned storage entirely. **No test could ever have caught a broken persistence path.**
3. **An empty catalog is perfectly "in sync".** `pages/Performance.tsx` called
   `useTranslation('performance')` while `performance.json` was `{}` in **both** locales, so
   every `t()` in that file rendered its raw key in the UI — with both gates green throughout,
   because parity compares two empty objects happily and the ratchet only looks for
   *un*-wrapped literals, never for wrapped ones resolving to nothing. Surfaced only because a
   worker noticed extra test failures. Closed structurally by parity Check 3.
4. **A shell check that never ran.** A scope-coverage command began `cd ux-command-center`
   while the shell was already there; the `cd` failed, the `&&` chain skipped the entire loop,
   and the trailing `echo "(none listed above = scope complete)"` still printed — reading
   exactly like a clean pass while hiding one unconverted file. Prefer absolute paths, or
   verify `pwd` in the same command.
5. **`_normalize_brief_keys` was wired at zero read sites.** It ran at generation time only, so
   the legacy-row adapter was decorative and every read returned `json.loads` raw. And there
   were **five** read sites, not the three the program plan listed — the two missed ones being
   `/brief/{id}` and `/review/{id}`, the *view-a-past-report* path, which is the likeliest
   place a legacy row is ever read at all.

The standing rule adopted mid-program, and worth carrying forward: **when a gate is added, the
acceptance criterion is watching it fail, not watching it pass.** A corollary learned the hard
way — when a mutation *fails* to turn a gate red, verify the mutation actually landed before
concluding the gate is inert.

**Latent bugs this program exposed** (all one family: code keyed on Chinese literals that
silently did nothing):

- **`actions[].action` had been broken in production the whole time the feature shipped.** The
  prompt schema asked the LLM for `买入/卖出/持有` while the frontend matched `buy/sell/hold`, so
  **every action badge fell through to grey.** Found only because translating the contract
  forced someone to read both ends of it. Now an enum, alongside `accuracy_tier` and `status`.
- **`extract_insights` keyed on `经验沉淀` / `准则更新建议`** and would have extracted nothing,
  forever.
- `BRIEF_SECTION_ORDER` / `REVIEW_SECTION_ORDER` were a second Chinese-literal list sitting
  outside the file scope the plan had declared.

**Neutral / Future work:**

- Language *preference* resolution (`resolve_language()`, migration V89,
  `user_profile.language`) shipped in WS-5 and is now documented in Decision 6 above.
  `localStorage['uis-lang']` remains the browser-side preference; the DB column is what
  scheduled and cloud jobs read.
- `src/services/context_generator.py` stays English: it is LLM *input*, not user-visible
  text. Recorded as a deliberate asymmetry.
- Backend API errors, the sync log stream, integrity-check prose and the `main.py` CLI stay
  English by owner decision, stated in the README limitations rather than hidden.
- The inconsistent bilingual schema columns (`taxonomy_classes.name_cn` is English-primary;
  `asset_tiers.name_en` / `risk_profiles.name_en` are Chinese-primary) are untouched and
  deserve their own ADR.
- **`<Trans>` was needed after all — the program plan was wrong.** The plan concluded rich-text
  interpolation was "not a driver" from a grep of `<strong>`, `<em>`, `<a href=` and `<Link `
  inside text nodes, which returned zero hits. **That grep missed `<b>`, which is the tag this
  codebase actually uses.** An AST scan of every `.tsx` under `components/`, `pages/` and
  `src/` found **23 call sites** holding both prose and inline markup — concentrated in
  `components/forecast/AnswerSection.tsx` (3), `pages/MonthlyAttribution.tsx` (4),
  `pages/CashFlowClassification.tsx` (3), `pages/Valuation.tsx` (3),
  `components/settings/ReaderMappingsPanel.tsx` (2), `components/wealthos/LogPnlDialog.tsx`
  (2), plus `PromptManager`, `AssetAudit`, `ValueTrapReviews`, `AssetCaseFile`, `DecisionHub`
  and `Compass`. The framework choice was unaffected — react-i18next ships `<Trans>` — but the
  extraction workstreams were re-briefed to **mandate** it at those sites, because splitting
  such a sentence into fragments produces unusable Chinese: Chinese word order does not follow
  the English fragment boundaries. All 23 were converted with `<Trans>`.
- **`pages/Valuation.tsx` was the inverse problem.** It was Chinese-**only** (64 CJK lines), so
  its **English had to be authored**, not translated — as did the English in
  `aiAdvisor.json > briefSection`. Both are new authored prose rather than a mechanical
  extraction, and both remain **open owner-review surfaces** at the time of this ADR. The
  `PE-TTM` distortion caveat and the GuruFocus / FactSet / 东吴证券 citations were verified
  intact in both locales.
- **"Source" splits into two different Chinese concepts by context** — 数据源 (the reader /
  ingest source) versus 来源标签 (a raw label on a source document, the `id_field_map` case). A
  blanket find-replace on "source" would be wrong. Recorded because it is the kind of thing a
  future translator will get wrong by default.
- **Owner terminology rulings are binding precedent** and live in `docs/i18n-glossary.md`:
  Tier → 梯队 (not 层级 — it matches `asset_tiers.name`), illiquid/non-rebalanceable → 固定资产,
  unrealized P&L → 浮动盈亏 (the broker idiom), attribution → 业绩归因, and reader/source both →
  数据源, deliberately not reproducing an English inconsistency. Shadow row → 影子记录, with the
  semantics pinned: `is_shadow=TRUE` means SUPERSEDED — an older snapshot kept for
  point-in-time history, **not invalid** — and no Chinese around it may imply discardable data.
  The governing principle behind all of them: **UI vocabulary matches the vocabulary in the
  owner's own workbook and data, not a dictionary translation.**
- **"Compass Report" was renamed "Allocation Report" in English** to match the approved Chinese
  资产配置报告 — the single sanctioned exception to the byte-identical-English rule, and the only
  place where test assertions on a literal were allowed to change.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| Grow the existing `translations.ts` map | English-string-as-key collides ("Total" as KPI vs. footer), breaks on any English copy edit, and ships no extraction or parity tooling for 2,706 keys across parallel workers |
| `react-intl` / FormatJS | ICU message syntax is more machinery than this needs, and its `defineMessages` id discipline is heavier for contributors than plain JSON catalogs |
| `zh` instead of `zh-CN` | Macrolanguage; adding `zh-TW` later would be a rename across every catalog and stored preference, and `Intl` wants a region for 万/亿 compact notation |
| Chinese as the default locale | Would invalidate ~304 existing English `getByText` assertions on day one, destroying the free correctness harness — and the public repo's audience is English-first |
| ESLint `react/jsx-no-literals` | No ESLint anywhere in the repo; one rule does not justify importing a lint toolchain and its config debate into a public repo mid-program |
| Regex-based ratchet (no `typescript`) | Cannot reliably tell a JSX text node from a `};` in plain TS, and cannot exclude `className` template literals. The AST version uses a dependency already installed |
| `continue-on-error: true` on the CI test step | Makes the step's status decorative and lets a real regression through unnoticed; the baseline ratchet fails on *growth* instead |
| Skipping / deleting the 35 known-failing tests to get a green CI | Hides behaviour the owner has not yet ruled on (`tests/operations-redesign.test.tsx`) and destroys the record of what is broken |

---

## References

- `ux-command-center/src/i18n/index.ts` — init, `zh` → `zh-CN` shim, `<html lang>` listener
- `ux-command-center/src/context/useLanguage.tsx` — the shim over `useTranslation()`
- `ux-command-center/scripts/i18n-parity-check.mjs`, `scripts/i18n-ratchet.mjs`
- `ux-command-center/i18n-converted-files.json` — the ratchet's growing enforced list
- `ux-command-center/tests/i18n-zh-canary.test.tsx` — the one test that runs in Chinese
- `.github/workflows/ci.yml` — the `frontend` job and its baseline gate
- `docs/i18n-glossary.md` — the terminology authority and the owner's binding rulings (public)
- `README.zh-CN.md`, `docs/quickstart.zh-CN.md`, `docs/operations.zh-CN.md` — the Chinese docs
- `src/services/ai_advisor/` + `src/api/routes/ai_advisor.py` — section IDs, `accuracy_tier`,
  `adapt_stored_content_json` and the AST guard that keeps every read site wired to it
- `src/database/connector.py` — migration **V89** and its UPSERT data step
- ADR-023 (Reader Mapping Management) — the Chinese values in `reader_mappings` are
  data-matching keys against real spreadsheet headers, **not** display strings; translating
  them breaks ingestion

**Development-history documents** (`docs/plans/` and `docs/archive/` are private-repo only and
are deliberately not part of the public export, so these paths will not resolve in a public
clone): the Program BIL plan `docs/archive/2026-08-21-bilingual-program.md`, the append-only
decisions log `docs/plans/2026-08-21-bil-autonomous-state.md`, and the per-workstream reports
`docs/plans/reports/2026-08-21-bil-ws{1..7}-report.md` — which carry the full list of authored
English awaiting owner sign-off (WS-4 for `Valuation.tsx`, WS-5 for
`aiAdvisor.json > briefSection`).
