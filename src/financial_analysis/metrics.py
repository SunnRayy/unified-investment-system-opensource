"""Historical portfolio risk metrics from actual data.

Unlike risk_calculator.py (model-based assumptions), this module computes
metrics from observed portfolio value time series.
"""
import math
import logging
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


def calculate_returns(values: List[float]) -> List[float]:
    """Calculate simple period returns from a value series.

    Args:
        values: Portfolio values (chronological order)

    Returns:
        List of period returns (length = len(values) - 1)
    """
    if len(values) < 2:
        return []
    return [(values[i] / values[i - 1]) - 1.0 for i in range(1, len(values))]


def max_drawdown(values: List[float]) -> float:
    """Calculate maximum drawdown (peak-to-trough decline).

    Args:
        values: Portfolio values (chronological order)

    Returns:
        Maximum drawdown as a positive decimal (e.g., 0.25 = 25% decline)
    """
    if len(values) < 2:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def sharpe_ratio(
    returns: List[float],
    risk_free_rate: float = 0.03,
    periods_per_year: int = 12,
) -> Optional[float]:
    """Calculate annualized Sharpe ratio.

    Args:
        returns: Period returns
        risk_free_rate: Annual risk-free rate (default 3%)
        periods_per_year: Number of periods per year (12=monthly, 252=daily)

    Returns:
        Annualized Sharpe ratio, or None if insufficient data
    """
    if len(returns) < 2:
        return None
    mean_r = sum(returns) / len(returns)
    std_r = (sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5
    if std_r == 0:
        return None
    rfr_per_period = risk_free_rate / periods_per_year
    return (mean_r - rfr_per_period) / std_r * math.sqrt(periods_per_year)


def sortino_ratio(
    returns: List[float],
    risk_free_rate: float = 0.03,
    periods_per_year: int = 12,
) -> Optional[float]:
    """Calculate annualized Sortino ratio (downside deviation only).

    Args:
        returns: Period returns
        risk_free_rate: Annual risk-free rate
        periods_per_year: Periods per year

    Returns:
        Annualized Sortino ratio, or None if insufficient data
    """
    if len(returns) < 2:
        return None
    rfr_per_period = risk_free_rate / periods_per_year
    downside = [min(r - rfr_per_period, 0) ** 2 for r in returns]
    downside_dev = (sum(downside) / len(downside)) ** 0.5
    if downside_dev == 0:
        return None
    mean_r = sum(returns) / len(returns)
    return (mean_r - rfr_per_period) / downside_dev * math.sqrt(periods_per_year)


def calmar_ratio(
    values: List[float],
    periods_per_year: int = 12,
) -> Optional[float]:
    """Calculate Calmar ratio = annualized return / max drawdown.

    Args:
        values: Portfolio values (chronological)
        periods_per_year: Periods per year

    Returns:
        Calmar ratio, or None if no drawdown
    """
    if len(values) < 2:
        return None
    dd = max_drawdown(values)
    if dd == 0:
        return None
    total_return = values[-1] / values[0] - 1.0
    n_periods = len(values) - 1
    annual_return = (1 + total_return) ** (periods_per_year / n_periods) - 1
    return annual_return / dd


from src.financial_analysis.snapshot_provider import get_portfolio_value_series

def calculate_portfolio_metrics(
    db: Any,
    periods_per_year: int = 12,
    include_asset_ids: Optional[list] = None,
    start_date: Optional[str] = None,
    exclude_non_balanceable: bool = False,
) -> Dict[str, Any]:
    """Calculate all risk metrics from historical snapshots.

    Args:
        db: DatabaseConnector or mock
        periods_per_year: Periods per year (e.g. 12 for monthly, 252 for daily)
        include_asset_ids: If provided, only include these asset IDs
        start_date: Optional start date filter (YYYY-MM-DD)
        exclude_non_balanceable: If True, subtract non-rebalanceable totals from BS.

    Returns:
        Dict with max_drawdown, sharpe_ratio, sortino_ratio, calmar_ratio,
        volatility_annual, total_return
    """
    try:
        # 1. Fetch portfolio value series from shared provider
        # This combines balance_sheet_monthly (historical) and holdings (current)
        snapshots = get_portfolio_value_series(
            db,
            start_date=start_date,
            include_asset_ids=include_asset_ids,
            exclude_non_balanceable=exclude_non_balanceable
        )

        if len(snapshots) < 2:
            return {
                "max_drawdown": None,
                "sharpe_ratio": None,
                "sortino_ratio": None,
                "calmar_ratio": None,
                "volatility_annual": None,
                "total_return": None,
                "data_points": len(snapshots),
            }

        # Filter partial-data snapshots: early balance_sheet_monthly records captured
        # only a subset of holdings, producing artificially low values and extreme returns.
        # A snapshot below 10% of the series peak is a data-completeness artifact.
        MIN_COMPLETENESS_FRACTION = 0.10
        peak_value = max(s["value"] for s in snapshots)
        if peak_value > 0:
            threshold = peak_value * MIN_COMPLETENESS_FRACTION
            filtered = [s for s in snapshots if s["value"] >= threshold]
            original_count = len(snapshots)
            if len(filtered) >= 2:
                snapshots = filtered
                removed = original_count - len(filtered)
                if removed > 0:
                    logger.debug(
                        "Filtered %d partial-data snapshots (below %.0f%% of peak %.0f)",
                        removed,
                        MIN_COMPLETENESS_FRACTION * 100,
                        peak_value,
                    )
            else:
                return {
                    "max_drawdown": None,
                    "sharpe_ratio": None,
                    "sortino_ratio": None,
                    "calmar_ratio": None,
                    "volatility_annual": None,
                    "total_return": None,
                    "data_points": len(filtered),
                }

        values = [s["value"] for s in snapshots]
        rets = calculate_returns(values)

        if not rets:
             return {
                "max_drawdown": round(max_drawdown(values) * 100, 2),
                "sharpe_ratio": None,
                "sortino_ratio": None,
                "calmar_ratio": None,
                "volatility_annual": None,
                "total_return": round((values[-1]/values[0]-1)*100, 2) if values[0] != 0 else 0,
                "data_points": len(values),
            }

        mean_ret = sum(rets) / len(rets)
        std_r = (sum((r - mean_ret) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 if len(rets) > 1 else 0.0
        vol_annual = std_r * math.sqrt(periods_per_year)
        total_ret = values[-1] / values[0] - 1.0 if values[0] != 0 else 0.0
        dd_frac = max_drawdown(values)

        # Calmar = annualized return / |max drawdown|. The numerator MUST be the
        # cash-flow-neutral annualized TWR (the exact value rendered as "TWR
        # (Annualized)"), NOT the deposit-inflated simple total return — external
        # deposits make values[-1]/values[0] balloon, which is why the old
        # calmar_ratio(values) numerator (~46% annualized) gave Calmar ≈ 4-5
        # instead of TWR/MaxDD ≈ 1.2. Compute TWR with the SAME scope params so
        # Calmar reconciles with the displayed TWR and Max Drawdown by construction.
        calmar = None
        if dd_frac and dd_frac > 0:
            try:
                from src.financial_analysis.twr import calculate_portfolio_twr
                _twr = calculate_portfolio_twr(
                    db,
                    start_date=start_date,
                    include_asset_ids=include_asset_ids,
                    exclude_non_balanceable=exclude_non_balanceable,
                )
                _ann = (_twr or {}).get("annualized")
                if _ann is not None:
                    calmar = _ann / dd_frac
            except Exception as _twr_exc:
                logger.debug("Calmar TWR numerator unavailable: %s", _twr_exc)

        return {
            "max_drawdown": round(dd_frac * 100, 2),
            "sharpe_ratio": _safe_round(sharpe_ratio(rets, periods_per_year=periods_per_year)),
            "sortino_ratio": _safe_round(sortino_ratio(rets, periods_per_year=periods_per_year)),
            "calmar_ratio": _safe_round(calmar),
            "volatility_annual": round(vol_annual * 100, 2),
            "total_return": round(total_ret * 100, 2),
            "data_points": len(values),
        }
    except Exception as e:
        logger.error(f"Error calculating portfolio metrics: {e}")
        return {
            "max_drawdown": None, "sharpe_ratio": None, "sortino_ratio": None,
            "calmar_ratio": None, "volatility_annual": None, "total_return": None,
            "data_points": 0,
        }


def _safe_round(val: Optional[float], digits: int = 2) -> Optional[float]:
    return round(val, digits) if val is not None else None
