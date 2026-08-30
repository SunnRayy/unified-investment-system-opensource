"""Single resolver for the North Star / forecast target net worth.

Plan: docs/plans/2026-07-26-your-path-design-implementation.md §3.

`forecast_levers.compute_levers` and `north_star_glide.glide_path` used to
each read `load_verification_config().north_star.target_net_worth_cny`
directly — a SECOND, independent source of the forecast target that happens
to coincidentally agree with the Goals page's live FIRE goal (e.g. a round
¥15,000,000 figure) today. Editing that goal in the UI would silently NOT move the forecast
headline. Same failure class as `_Schawab_USD` (ADR-025 §3) and the
two-goal-dates defect (D-1 in the forecast page design brief).

This module is the ONLY other permitted reader of
`target_net_worth_cny` besides `src/services/verification_config.py` itself
(enforced by tests/services/test_goal_resolver_structural_guard.py) — every
consumer must go through `resolve_north_star_goal`.

Resolution rule (owner-locked, 2026-07-26):
  SELECT id, name, target_amount, target_date
  FROM goals
  WHERE status = 'active' AND LOWER(goal_type) = 'retirement'
  ORDER BY target_date DESC, id DESC
  LIMIT 1

- `LOWER(goal_type)` because `goal_type` is free text from the Goals form.
- Furthest `target_date` wins a tie (the retirement horizon, not an interim
  milestone); `id DESC` is the final tiebreak.
- No matching row, or the query itself fails -> fall back to
  `load_verification_config().north_star.target_net_worth_cny`. This is a
  documented, intentional fallback (owner decision) — do NOT delete the
  config value. The fallback is always a labelled state
  (`source="config_fallback"` + a `fallback_reason`), never a silent
  substitution (AGENTS.md Rule 12): never return None, never blank the page,
  never raise out of this function.

READ-ONLY — no writes to any table anywhere in this module.
"""
from __future__ import annotations

import logging

from src.services.verification_config import load_verification_config

logger = logging.getLogger(__name__)


def resolve_north_star_goal(db) -> dict:
    """Resolve the single authoritative North Star target.

    Returns a dict with keys:
      target_amount   float  — always a real number, never None
      source          str    — "goals" or "config_fallback"
      goal_id         int | None
      name            str | None
      target_date     str | None  (ISO YYYY-MM-DD)
      fallback_reason str | None  — None when source == "goals"
    """
    try:
        row = db.execute(
            """
            SELECT id, name, target_amount, target_date
            FROM goals
            WHERE status = 'active' AND LOWER(goal_type) = 'retirement'
            ORDER BY target_date DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    except Exception:
        logger.exception("goal_resolver: goals query failed, falling back to config")
        return _config_fallback(reason="goals query failed")

    if row is None:
        return _config_fallback(reason="no active retirement goal")

    goal_id, name, target_amount, target_date = row
    return {
        "target_amount": float(target_amount),
        "source": "goals",
        "goal_id": int(goal_id),
        "name": name,
        "target_date": target_date.isoformat() if target_date is not None else None,
        "fallback_reason": None,
    }


def _config_fallback(*, reason: str) -> dict:
    cfg = load_verification_config().north_star
    return {
        "target_amount": float(cfg.target_net_worth_cny),
        "source": "config_fallback",
        "goal_id": None,
        "name": None,
        "target_date": None,
        "fallback_reason": reason,
    }
