# ADR-007: Native-Currency P&L (V5.2.0+)

**Date:** 2026-05-29
**Status:** Accepted
**Deciders:** Ray (Owner), Claude Code (Architect)

---

## Context

Prior to V5.2.0, all cost-basis values for Schwab (US stocks/ETFs) and RSU
(Amazon RSUs) were stored in CNY by multiplying native USD prices by a hardcoded
FX rate (7.0 at transformer time, or historical FX at `CostBasisCalculator` time).

This caused two serious bugs:
1. **SGOV showed −86% P&L**: Historical USD cashflows were converted at their
   historical FX rates (e.g., 6.5–7.0); the current market value was converted
   at the current rate (7.1). The P&L was computing CNY(market) − CNY(cost_historic),
   which mixes FX epochs and artificially inflates/deflates the P&L by the FX change.
2. **Portfolio oscillated ±0.41% on weekends**: `market_price_unit` was stored in
   CNY (×7.0), but the DSA update wrote it in native USD. This caused DSA condition C
   to fire every sync on weekends (no live price available), oscillating `market_value`
   between two FX-multiplied versions.

---

## Decision

**1. `market_value` is always stored in CNY (canonical portfolio currency).**

Every row in the `holdings` table stores `market_value` in CNY. This invariant is
enforced by integrity check #2 (`no_raw_usd_in_schwab_holdings`): any Schwab/RSU
position with `market_value < 50,000` is flagged as a potential raw-USD leak.

**2. Per-unit prices are stored in native currency for Schwab and RSU.**

- `cost_price_unit`: set by the transformer or FIFO backfill in native USD (not CNY).
  `CostBasisCalculator` works in native currency; FX conversion is the caller's
  responsibility.
- `market_price_unit`: set by `_update_from_dsa` in native USD. The Schwab
  transformer also sets it initially as an approximation in native USD.

For CNY-denominated assets (CN funds, gold, insurance, deposits), both fields remain
in CNY as before.

**3. P&L is computed in native USD, converted to CNY once at display time.**

For USD assets:
```
P&L (USD) = (market_price_unit − cost_price_unit) × qty
P&L (CNY) = P&L (USD) × today_fx_rate
```
Never compute `market_value − cost_price_unit × qty` for USD assets — that mixes a
CNY `market_value` with a USD `cost_price_unit`, producing garbage.

The live FX rate is fetched at display time; no historical FX is used for P&L.

**4. `_backfill_fifo_cost_basis` detects and nulls stale CNY cost values.**

After upgrading to native-currency mode, any row with a `cost_price_unit /
market_price_unit` ratio > 4.5 (indicating CNY×7 storage) is automatically nulled
so FIFO can recompute from transactions in native USD.

---

## Consequences

**Positive:**
- SGOV P&L went from −86% (FX artefact) to +0.03% (true economic return).
- Portfolio oscillation ±0.41% eliminated.
- FX impact is isolated to a single live-FX multiply at display time, making it
  easy to attribute separately in future.
- DSA condition C no longer fires spuriously on weekends.

**Negative / Trade-offs:**
- `cost_price_unit` and `market_price_unit` are NOT in the same currency as
  `market_value` for USD assets. Any agent or query that sums or compares
  `market_value` with `cost_price_unit × qty` must apply FX conversion.
- Separate FX-P&L attribution (how much of the P&L is FX vs. underlying return)
  is not yet implemented; it is a deferred feature.

**Neutral / Future work:**
- Separate FX-P&L attribution feature tracked as deferred in the roadmap.
- The `_backfill_fifo_cost_basis` stale-CNY detector (ratio > 4.5) is a one-time
  migration guard; it can be removed after the DB is fully migrated.

---

## Alternatives Considered

| Alternative | Reason Not Chosen |
|-------------|------------------|
| Convert all prices to CNY at insertion time using historical FX | Causes FX-epoch mixing bugs (SGOV −86% case); makes FX-P&L separation impossible |
| Convert all prices to CNY at insertion time using today's FX | Portfolio values oscillate whenever live FX changes even with no underlying asset price change |
| Store separate `cost_price_unit_cny` field | Doubles the storage and update surface; sources of truth become ambiguous |

---

## References

- `src/sources/schwab_transformer.py` — sets `market_price_unit` in native USD
- `src/sources/rsu_transformer.py` — sets `cost_price_unit` in native USD
- `src/services/cost_basis_calculator.py` — FIFO in native currency
- `src/sync/orchestrator.py:_backfill_fifo_cost_basis` — stale-CNY detection
- `src/validation/data_integrity_gate.py` — check #2 (`no_raw_usd_in_schwab_holdings`)
- `docs/architecture/data-pipeline-v4.md` sections 31–45, 93, 122–123
- AGENTS.md Rule 2 (currency convention)
