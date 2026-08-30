"""Time-Weighted Return (TWR) calculation.

TWR measures portfolio performance independent of cash flow timing.
Uses chain-linking of sub-period returns between snapshots.

Formula: TWR = Π(1 + r_i) - 1
Where r_i = (V_end - V_start - CF_i) / (V_start + CF_i_weighted)
"""
import logging
from datetime import date
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

OUTFLOW_TRANSACTION_TYPES = frozenset(
    {
        "buy",
        "premium_payment",
        "vest",
        "rsu_vest",
        "transfer_in",
        "dividend_reinvest",
        "tax_adjustment",
    }
)
INFLOW_TRANSACTION_TYPES = frozenset(
    {
        "sell",
        "transfer_out",
        "dividend",
        "dividend_cash",
        "interest",
    }
)


def calculate_twr_from_snapshots(
    snapshots: List[dict],
    cashflows: List[dict],
) -> Optional[float]:
    """Calculate TWR from a series of portfolio value snapshots and cash flows.

    Args:
        snapshots: List of {"date": date, "value": float} sorted by date ascending.
        cashflows: List of {"date": date, "amount": float} — positive = deposit, negative = withdrawal.

    Returns:
        Cumulative TWR as a decimal (e.g. 0.10 = 10%), or None if insufficient data.
    """
    if len(snapshots) < 2:
        return None

    # Sort
    snapshots = sorted(snapshots, key=lambda s: s["date"])
    cashflows = sorted(cashflows, key=lambda c: c["date"])

    cumulative = 1.0

    for i in range(1, len(snapshots)):
        v_start = snapshots[i - 1]["value"]
        v_end = snapshots[i]["value"]
        period_start = snapshots[i - 1]["date"]
        period_end = snapshots[i]["date"]

        if v_start == 0:
            continue

        # Sum cash flows in this period
        period_cf = sum(
            cf["amount"]
            for cf in cashflows
            if period_start < cf["date"] <= period_end
        )

        # Modified Dietz: weight cash flows by time in period
        period_days = (period_end - period_start).days
        if period_days <= 0:
            continue

        weighted_cf = 0.0
        for cf in cashflows:
            if period_start < cf["date"] <= period_end:
                days_remaining = (period_end - cf["date"]).days
                weight = days_remaining / period_days
                weighted_cf += cf["amount"] * weight

        denominator = v_start + weighted_cf
        if denominator <= 0:
            continue

        r_i = (v_end - v_start - period_cf) / denominator
        cumulative *= (1 + r_i)

    return cumulative - 1.0


from src.financial_analysis.snapshot_provider import get_portfolio_value_series

def calculate_portfolio_twr(
    db: Any,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    include_asset_ids: Optional[List[str]] = None,
    exclude_non_balanceable: bool = False,
) -> Optional[dict]:
    """Calculate TWR for the portfolio using historical snapshots.

    Args:
        db: DatabaseConnector
        start_date: Optional start date filter (YYYY-MM-DD)
        end_date: Optional end date filter (YYYY-MM-DD)
        include_asset_ids: Optional list of asset IDs to filter.
        exclude_non_balanceable: If True, subtract non-rebalanceable totals from BS.

    Returns:
        Dict with keys:
          - "cumulative": float — raw cumulative TWR (e.g. 0.25 = 25%)
          - "annualized": float|None — annualized TWR if >= 365 days of data, else None
        Returns None on failure or insufficient data.
    """
    try:
        # 1. Fetch portfolio value series from shared provider
        # This combines balance_sheet_monthly (historical) and holdings (current)
        snapshots = get_portfolio_value_series(
            db,
            start_date=start_date,
            end_date=end_date,
            include_asset_ids=include_asset_ids,
            exclude_non_balanceable=exclude_non_balanceable
        )

        if len(snapshots) < 2:
            return None

        # 2. Get cash flows (buy = deposit, sell = withdrawal)
        # We query transactions occurring between the first and last snapshot.
        from src.services.transaction_source_selector import build_source_filter_clauses
        
        actual_start_date = snapshots[0]["date"].isoformat()
        actual_end_date = snapshots[-1]["date"].isoformat()

        # Build deduplication filter
        assets_to_filter = include_asset_ids
        if assets_to_filter is None:
            tx_assets = db.execute("SELECT DISTINCT asset_id FROM transactions WHERE is_provisional = FALSE").fetchall()
            assets_to_filter = sorted({r[0] for r in tx_assets if r and r[0]})
            
        dedup_clause, dedup_params = build_source_filter_clauses(db, assets_to_filter)
        
        cf_params: list = list(dedup_params)
        cf_filter = f" AND {dedup_clause}"

        cf_filter += " AND transaction_date >= ? AND transaction_date <= ?"
        cf_params.extend([actual_start_date, actual_end_date])

        cf_rows = db.execute(
            f"""
            SELECT transaction_date, transaction_type, amount_net
            FROM transactions
            WHERE is_provisional = FALSE {cf_filter}
            ORDER BY transaction_date ASC
            """,
            cf_params or None,
        ).fetchall()

        cashflows = []
        for row in cf_rows:
            dt = _to_date(row[0])
            typ = str(row[1]).lower()
            amt = float(row[2] or 0)
            if amt == 0:
                continue
            if typ in OUTFLOW_TRANSACTION_TYPES:
                cashflows.append({"date": dt, "amount": abs(amt)})  # deposit
            elif typ in INFLOW_TRANSACTION_TYPES:
                cashflows.append({"date": dt, "amount": -abs(amt)})  # withdrawal

        # 3. Calculate TWR
        cumulative = calculate_twr_from_snapshots(snapshots, cashflows)
        if cumulative is None:
            return None

        # 4. Annualize if data spans >= 365 days
        total_days = (snapshots[-1]["date"] - snapshots[0]["date"]).days if len(snapshots) >= 2 else 0
        if total_days >= 365:
            annualized = (1 + cumulative) ** (365 / total_days) - 1
        else:
            annualized = None

        return {"cumulative": cumulative, "annualized": annualized}

    except Exception as e:
        logger.error(f"Error calculating portfolio TWR: {e}")
        return None


def _to_date(val) -> date:
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val)
    if hasattr(val, "date"):
        return val.date()
    return date.fromisoformat(str(val))
