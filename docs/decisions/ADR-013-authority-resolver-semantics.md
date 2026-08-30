# ADR-013: Authority Resolver Priority Semantics

**Date:** 2026-06-07
**Status:** Accepted
**Deciders:** Ray (Owner), Claude Code (Architect)

---

## Context

`src/identity/authority_resolver.py` (`AuthorityResolver.resolve()`) determines which data
source is authoritative for a given asset ID. Rules are loaded from
`config/source_authority.yaml` and carry a numeric `priority` field. The resolver sorts rules
**ascending** by priority and returns the **first matching rule** — meaning a **lower priority
number wins** (has higher authority).

This semantics exists and works correctly in production: RSU assets (`priority: 5`) are
resolved to `RSU_Excel` before per-reader rules (`priority: 8`) and the Financial Summary
catch-all (`priority: 9`). ADR-004 stated this convention in passing ("lower number = higher
authority") but did not explain the rationale or make it the focus of a decision record.

Two symptoms reveal that the semantics are under-documented:

1. The sort comment in the code is bare: `# Sort by priority (asc)` with no explanation
   of why ascending sort produces "lower wins" behaviour.
2. A comment inside `tests/identity/test_authority_resolver.py` reads: *"Wait, usually
   specific matches or higher priority (lower number) wins..."* — the author had to reason
   aloud to verify the semantics. This is a signal the convention needs a canonical reference.

There is also a logging gap: when `resolve()` returns `None` (no rule matches the asset ID
with the available sources), the resolver logs nothing. The only downstream warning lives in
`src/sync/holdings_aggregator.py:62` and does not record which rules were considered or why
resolution failed.

---

## Decision

**The "lower priority number = higher authority" convention is correct and intentional.** No
inversion is made. The implementation logic is:

1. Rules are sorted ascending by `priority` (defaulting to 100 when unspecified).
2. `resolve()` iterates in that order and returns the **first** rule whose pattern matches the
   `asset_id` AND (if `available_sources` is supplied) whose `authority` is in that set.
3. A lower number is therefore evaluated first, giving it precedence.

The live configuration (`config/source_authority.yaml`) intentionally uses:

| Priority | Rule | Authority |
|----------|------|-----------|
| 5 | `RSU_*` | `RSU_Excel` (most specific — always wins) |
| 8 | `US_STK_*`, `CN_FUND_*`, etc. | per-reader sources |
| 9 | `*` (catch-all) | `Financial_Summary_Excel` |

This ordering mirrors how specificity works in CSS selectors and routing tables: more-specific
rules get lower numbers so they fire first. The convention was also re-applied verbatim in
`src/sync/phases/_post_reader.py` (`_load_adapter_authority_rules`) when dynamically
injecting adapter-approval rules — making it a de facto project-wide convention.

A `logger.debug(...)` call is added inside `resolve()` at the `return None` path to expose
which asset ID failed, how many rules were considered, and what sources were available. This
makes the aggregator's "No authority found" warning diagnosable without adding log noise in
normal operation (the `*` catch-all means `None` is rare in production).

---

## Consequences

**Positive:**
- Convention is now canonically documented; agents and humans can cite this ADR instead of
  re-reading the code or trusting the pass-through comment in ADR-004.
- Debug logging makes `resolve() → None` diagnosable: operators can see `asset_id`,
  `rules=N`, `available_sources=[...]` in DEBUG log output.
- No config changes required; existing `config/source_authority.yaml` and adapter-injection
  code remain correct as-is.

**Negative / Trade-offs:**
- The convention remains counter-intuitive in plain English ("priority 5" sounds *lower* than
  "priority 9", but it is *higher authority*). Future contributors must read this ADR.
- Cannot use plain `priority: 1` for "least important" — the number ordering is inverted from
  naive expectation.

**Neutral / Future work:**
- If a future team decides to invert the convention (making higher numbers = higher authority),
  the change touches: `config/source_authority.yaml`, `_load_adapter_authority_rules` in
  `_post_reader.py`, and the 3 integration test files in `tests/sync/`. This ADR should be
  superseded at that point.
- The `*` catch-all rule (`priority: 9, authority: Financial_Summary_Excel`) means `resolve()`
  should almost never return `None` in production. If the debug log fires, it signals a missing
  catch-all or an asset with no available sources — which warrants investigation.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|-----------------|
| Invert convention (higher = stronger) | Breaking change to `source_authority.yaml`, `_post_reader.py`, and 3 integration test files. No functional benefit — the current ordering works correctly in production. |
| Rename `priority` to `specificity` | Requires changing the YAML key and all loader code. Improves readability marginally but adds migration cost. Deferred until/if a broader identity-module refactor happens. |
| Add `logger.warning` (not `debug`) on `None` return | With a `*` catch-all always present, a warning would fire only in genuine misconfiguration. `debug` is sufficient; the aggregator layer already issues a `warning`. |

---

## References

- `src/identity/authority_resolver.py` — `AuthorityResolver._parse_rules()` (ascending sort,
  line ~48) and `resolve()` (first-match return, line ~72)
- `config/source_authority.yaml` — live priority values (5 / 8 / 9)
- `src/sync/phases/_post_reader.py` — `_load_adapter_authority_rules()` (dynamic rule
  injection, reuses the same ascending sort)
- `src/sync/holdings_aggregator.py:62` — upstream `logger.warning` for no-match
- ADR-004: `docs/decisions/ADR-004-import-adapter-authority.md` — original statement of the
  convention ("lower number = higher authority")
- ADR-001: `docs/decisions/ADR-001-aia-pis-conflict.md` — origin of authority arbitration
