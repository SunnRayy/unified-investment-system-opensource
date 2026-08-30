# ADR-010: AI-Advisor / LLM Integration (LLMClient, Gemini, DeepSeek Routing)

**Date:** 2026-05-29 (drafted 2026-06-20, accepted 2026-06-20)
**Status:** Accepted

---

## Context

Huinsight has an AI Advisor feature that uses `LLMClient` (`src/services/llm_client.py`)
to route prompts through a primary → fallback → fallback chain (configured in
`config/settings.yaml`). Currently supported: Gemini (primary), DeepSeek (fallback).
The integration is centralized via `LLMClient` per AGENTS.md Rule 21.

An architecture decision is needed to document: (1) the model routing policy, (2)
how to safely add or remove providers, (3) the prompt-caching and usage-logging
strategy, and (4) the safety boundary between structured outputs and raw generation.

## Decision (proposed)

Ratify `LLMClient` as the **single LLM boundary** (Rule 21) and formalize its
contract along four axes:

1. **Routing policy** — an ordered provider list in `config/settings.yaml`;
   `LLMClient` tries primary, then each fallback on error or timeout, and records
   which provider answered. The order is owner-configurable via Settings → AI Models
   (no code change to reorder or swap). Recommend adding a frontier **Claude** model
   (e.g. latest Claude Sonnet/Opus) as a selectable provider alongside Gemini and
   DeepSeek, given its strength on the structured brief/review tasks.
2. **Add/remove providers** — config-only. A provider is `{name, api_key_env,
   model, enabled}`; adding one is a settings edit + an env-var key. No provider
   SDK is imported outside `LLMClient`.
3. **Prompt-caching + usage logging** — persist per-call `{provider, model,
   prompt_tokens, completion_tokens, latency_ms, ok}` to a `llm_usage` table for
   cost/latency visibility; enable provider-side prompt caching where supported for
   the large shared-context prompts (brief/review) to cut token cost.
4. **Structured vs raw boundary** — brief/review responses MUST be JSON-schema
   validated (the existing `_normalize_*_keys` + schema path); only validated
   structured output feeds downstream tables. Raw free-text generation is allowed
   only behind `LLMClient` with an enforced timeout, never written to a typed table
   unparsed.

## Options considered

1. **Status quo (implicit contract)** — already centralized, but routing/caching/
   logging behaviour is undocumented and untracked. Rejected as the baseline.
2. **Documented contract + usage logging (chosen)** — keeps the clean single-client
   design, adds cost/latency observability and an explicit output-safety boundary.
3. **Adopt an LLM framework (e.g. LangChain)** — rejected: the centralized
   `LLMClient` is already simpler than a framework and Rule 21 keeps the surface
   minimal; a framework adds dependency weight and obscures the routing path.

## Consequences

- Operability: cost and latency become visible per provider; failovers are auditable.
- Adding Claude (or any provider) is a config + env change, exercised by the existing
  Settings → AI Models console.
- Small build: a `llm_usage` table + writes inside `LLMClient`; optional prompt-cache
  wiring per provider.
- The structured-output boundary hardens against malformed LLM responses reaching
  typed storage.

---

## References

- `src/services/llm_client.py` — centralized LLM client
- `config/settings.yaml` — model config
- AGENTS.md Rule 21 (All LLM Calls Must Go Through LLMClient)
- Deferred architecture items are tracked internally
