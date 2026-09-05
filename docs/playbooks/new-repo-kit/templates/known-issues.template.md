# Known Issues — Automated Checks

Maps recurring error patterns to automated checks in `scripts/verify.sh`. A **living checklist**, not a
design doc (decisions → ADRs). 

**Feedback-loop rule:** if a check triggers more than once on the same issue class, document the root
cause and fix pattern here. If a "Not Yet Automated" pattern recurs in production and is automatable,
add it to `verify.sh` and **promote** it into the Automated section below.

---

## Automated Checks

Patterns caught by `scripts/verify.sh` before code reaches production.

### §{{check-id}} — {{Short pattern name}}

- **verify.sh check:** `[{{a}}]` — exit {{1/2/3}}
- **Detection:** {{grep pattern or tool}}
- **Error prevented:** {{what bug this stops, with the real incident that motivated it}}
- **How to fix a failure:**
  ```
  {{wrong example}}
  {{right example}}
  ```
- **Pre-existing violations (baselined, do not block):** {{file:line — why acceptable}}
- **See also:** AGENTS.md Rule {{N}}

<!-- copy the block above for each automated check -->

---

## Not Yet Automated (requires human review)

Real bugs that static analysis can't reliably catch (need runtime data or judgment).

| Pattern | Why not automatable | Manual check | Rule |
|---------|---------------------|--------------|------|
| {{pattern}} | {{reason}} | {{command/query}} | {{N}} |

---

## Feedback Loop

If a "Not Yet Automated" pattern is triggered in production:
1. Document the incident here under the relevant section.
2. Assess whether automation is now feasible.
3. If automatable, add to `verify.sh` and move it to **Automated Checks**.
