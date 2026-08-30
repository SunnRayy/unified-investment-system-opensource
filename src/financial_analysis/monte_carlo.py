"""Monte Carlo simulation engine for portfolio projection.

Generates random walk paths using geometric Brownian motion to estimate
future portfolio values and goal achievement probability.
"""
import logging
import math
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)


def run_monte_carlo(
    initial_value: float,
    annual_return: float = 0.07,
    annual_volatility: float = 0.15,
    years: int = 10,
    num_simulations: int = 1000,
    annual_contribution: float = 0.0,
    seed: Optional[int] = None,
    goal_target: Optional[float] = None,
    return_paths: bool = False,
) -> Dict[str, Any]:
    """Run Monte Carlo simulation for portfolio growth.

    Uses geometric Brownian motion stepped MONTHLY: each month's return is
    drawn from a lognormal distribution calibrated so that the EXPECTED
    annual growth factor equals ``1 + annual_return``, and 1/12 of
    ``annual_contribution`` is added at the end of every month.

    This monthly-step, monthly-contribution convention is deliberately the
    same one used by the deterministic glide path
    (``src.services.north_star_glide._monthly_rate`` /
    ``future_value``) — the two engines must agree on 20M/year-count
    projections for the same inputs. The previous implementation stepped
    annually and added the whole year's contribution at year-end, which
    let that year's contribution earn zero return in the year it was
    made; that produced a systematic gap vs. the glide path (~2.4% at
    10y for typical inputs). See docs/decisions for the write-up if this
    diverges again.

    Args:
        initial_value: Starting portfolio value
        annual_return: Expected annual return (decimal, e.g. 0.07 = 7%)
        annual_volatility: Annual volatility (decimal, e.g. 0.15 = 15%)
        years: Projection horizon in years
        num_simulations: Number of simulation paths
        annual_contribution: Fixed annual contribution total, added as
            annual_contribution/12 at the end of each month
        seed: Random seed for reproducibility
        goal_target: Optional target value for goal probability calculation
        return_paths: When True, include the raw year-sampled simulation
            matrix (shape num_simulations x years+1) under result["paths"]
            as a plain list of lists. Default False — no production caller
            sets this; it exists for tests that need the EMPIRICAL fraction
            of individual paths with value_t >= target at each year (e.g.
            tests/financial_analysis/test_crossing_time_percentiles.py,
            which pins src.financial_analysis.projection_defaults.
            crossing_time_percentiles against this same "value at t"
            definition — NOT first-passage — see that function's
            docstring). The percentiles dict alone cannot answer that
            question because it reports the VALUE at each fixed quantile,
            not the fraction of paths crossing a fixed target.

    Returns:
        Dict with keys: years, percentiles (p10/p25/p50/p75/p90),
        goal_probability (if goal_target set), final_value_stats, and
        "paths" (only when return_paths=True).

        NOTE: ``years`` and each ``percentiles[pX]`` list stay annual —
        length years+1, indices 0..years — even though the simulation
        itself steps monthly internally. Callers/frontend depend on this
        shape; monthly paths are sampled at every 12th step to produce it.
    """
    rng = np.random.default_rng(seed)

    months = years * 12
    monthly_contribution = annual_contribution / 12.0

    # Monthly drift/vol calibrated so the EXPECTED annual growth factor
    # equals (1 + annual_return), matching the annual-step convention's
    # log-normal correction: E[exp(mu_m + 0.5*sigma_m^2)] * 12 compounds to
    # exp(log(1+annual_return)) = 1+annual_return.
    sigma_m = annual_volatility / math.sqrt(12)
    mu_m = math.log(1 + annual_return) / 12.0 - 0.5 * sigma_m ** 2

    # Simulate: shape (num_simulations, months)
    random_returns = rng.normal(mu_m, sigma_m, size=(num_simulations, months))

    # Build monthly paths, contribution applied at each month end
    monthly_paths = np.zeros((num_simulations, months + 1))
    monthly_paths[:, 0] = initial_value

    for t in range(1, months + 1):
        monthly_paths[:, t] = (
            monthly_paths[:, t - 1] * np.exp(random_returns[:, t - 1]) + monthly_contribution
        )

    # Sample at year boundaries (every 12th month) to preserve the public
    # annual-length return shape (years+1 entries, indices 0..years).
    year_sample_indices = [y * 12 for y in range(0, years + 1)]
    paths = monthly_paths[:, year_sample_indices]

    # Percentiles
    year_list = list(range(0, years + 1))
    percentiles = {}
    for label, pct in [("p10", 10), ("p25", 25), ("p50", 50), ("p75", 75), ("p90", 90)]:
        vals = np.percentile(paths, pct, axis=0)
        percentiles[label] = [round(float(v), 2) for v in vals]

    # Final value stats
    final_values = paths[:, -1]
    result: Dict[str, Any] = {
        "years": year_list,
        "initial_value": round(initial_value, 2),
        "percentiles": percentiles,
        "final_value_stats": {
            "mean": round(float(np.mean(final_values)), 2),
            "median": round(float(np.median(final_values)), 2),
            "std": round(float(np.std(final_values)), 2),
            "min": round(float(np.min(final_values)), 2),
            "max": round(float(np.max(final_values)), 2),
        },
        "assumptions": {
            "annual_return": annual_return,
            "annual_volatility": annual_volatility,
            "annual_contribution": annual_contribution,
            "num_simulations": num_simulations,
        },
    }

    if goal_target is not None:
        successes = int(np.sum(final_values >= goal_target))
        result["goal_probability"] = round(successes / num_simulations, 4)
        result["goal_target"] = goal_target

    if return_paths:
        result["paths"] = paths.tolist()

    return result


def calculate_portfolio_projection(
    db: Any,
    years: int = 10,
    num_simulations: int = 1000,
    annual_return: float = 0.07,
    annual_volatility: float = 0.15,
    annual_contribution: float = 0.0,
    goal_target: Optional[float] = None,
    seed: Optional[int] = None,
    include_non_rebalanceable: bool = False,
) -> Dict[str, Any]:
    """Run Monte Carlo projection using current portfolio value from DB.

    Args:
        db: DatabaseConnector or mock
        years: Projection horizon
        num_simulations: Number of paths
        annual_return: Expected return
        annual_volatility: Expected volatility
        annual_contribution: Annual contribution
        goal_target: Optional goal target
        seed: Random seed
        include_non_rebalanceable: Whether to include non-rebalanceable assets

    Returns:
        Monte Carlo result dict
    """
    try:
        from src.services.rebalanceable_filter import fetch_non_rebalanceable_asset_ids
        excluded_ids = set()
        if not include_non_rebalanceable:
            excluded_ids = fetch_non_rebalanceable_asset_ids(db)
            
        query = """
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) as latest_date
                FROM holdings WHERE is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT SUM(h.market_value)
            FROM holdings h
            JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            WHERE h.is_shadow = FALSE
        """
        if excluded_ids:
            placeholders = ", ".join(["?"] * len(excluded_ids))
            query += f" AND h.asset_id NOT IN ({placeholders})"
            row = db.execute(query, list(excluded_ids)).fetchone()
        else:
            row = db.execute(query).fetchone()
            
        initial_value = float(row[0]) if row and row[0] else 0.0
    except Exception as e:
        logger.error(f"Error fetching portfolio value: {e}")
        initial_value = 0.0

    return run_monte_carlo(
        initial_value=initial_value,
        annual_return=annual_return,
        annual_volatility=annual_volatility,
        years=years,
        num_simulations=num_simulations,
        annual_contribution=annual_contribution,
        seed=seed,
        goal_target=goal_target,
    )
