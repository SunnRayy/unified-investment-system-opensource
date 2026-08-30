"""North Star panel service (PRD 2026-07-07 F3, Batch B6).

Measures the two variables the trading-precision dashboards are silent on:
contribution rate (F3.1) and time-in-market (F3.2), plus an unforced-error
log (F3.3) and a deterministic glide-path projection to the target net worth
(F3.4). See docs/prd-2026-07-07-process-verification-improvements.md §F3 and
docs/plans/2026-07-07-process-verification-program.md design decision D6.

Implementation is split across two sibling modules to keep each file under
the repo's ~400-line guideline:
  - src/services/north_star_flows.py  — F3.1 cash-flow classification
  - src/services/north_star_glide.py  — F3.2/F3.3/F3.4 (TIM, errors, glide)
This module is the intended import surface: routes and tests should import
from ``src.services.north_star``, not the sibling modules directly.
"""
from __future__ import annotations

from src.services.north_star_flows import (  # noqa: F401 — re-export surface
    classify_flows_heuristic,
    contribution_metrics,
    contributions_summary,
    fs_cash_flow_candidates,
    list_classified_flows,
    list_unclassified_flows,
    tag_flow_manual,
    tag_flows_bulk,
    untag_flows,
)
from src.services.north_star_glide import (  # noqa: F401 — re-export surface
    create_unforced_error,
    glide_path,
    list_unforced_errors,
    time_in_market,
    update_unforced_error_cost,
)


def north_star_panel(db, monthly_contribution: float = 0.0) -> dict:
    """Composes contributions + time_in_market + unforced_errors + glide_path
    — the quarterly-report North Star block (F3.5)."""
    return {
        "contributions": contribution_metrics(db),
        "time_in_market": time_in_market(db),
        "unforced_errors": list_unforced_errors(db),
        "glide_path": glide_path(db, monthly_contribution=monthly_contribution),
    }
