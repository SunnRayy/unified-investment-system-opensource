
from scipy.optimize import brentq
from datetime import date
from typing import List, Tuple, Optional, Any
import logging
from src.data_manager.currency_converter import get_currency_service

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

def xnpv(rate: float, cashflows: List[Tuple[date, float]]) -> float:
    """
    Calculate Net Present Value for irregular intervals.
    
    Args:
        rate: Annual discount rate
        cashflows: List of (date, amount)
        
    Returns:
        NPV
    """
    if rate <= -1.0:
        return float('inf')
        
    t0 = cashflows[0][0]
    return sum([cf / ((1 + rate) ** ((d - t0).days / 365.0)) for d, cf in cashflows])

def calculate_xirr(
    cashflows: List[Tuple[date, float]],
    guess: float = 0.1
) -> Optional[float]:
    """
    Calculate XIRR (Extended Internal Rate of Return).

    Args:
        cashflows: List of (date, amount) tuples.
                   Negative = outflow (investment), Positive = inflow (return).
        guess: Initial guess for rate

    Returns:
        Annualized return rate, or None if doesn't converge
    """
    if not cashflows or len(cashflows) < 2:
        return None
        
    # Verify signs
    amounts = [cf[1] for cf in cashflows]
    if all(a >= 0 for a in amounts) or all(a <= 0 for a in amounts):
        return None
        
    # Sort by date
    cashflows.sort(key=lambda x: x[0])
    
    try:
        # Solve for rate where XNPV = 0
        # Search range [-0.99, 1000.0] (from -99% to 100,000%)
        result = brentq(lambda r: xnpv(r, cashflows), -0.99, 1000.0, maxiter=100)
        if result > 5.0:
            logger.warning(f"XIRR solution {result:.1%} exceeds 500% — verify cash flows are correct")
        return result
    except Exception as e:
        logger.warning(f"XIRR calculation failed: {e}")
        return None

def calculate_portfolio_xirr(
    db: Any,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    include_asset_ids: Optional[List[str]] = None,
) -> Optional[float]:
    """
    Calculate XIRR for the entire portfolio.
    
    Args:
        db: DatabaseConnector or mock
        
    Returns:
        Annualized XIRR
    """
    try:
        # 1. Fetch all transactions (Cashflows)
        # Buy: Outflow (Negative)
        # Sell: Inflow (Positive)
        # Dividend: Inflow (Positive)
        # Deposit/Withdrawal: Adjust accordingly if tracked, but usually covered by Buy/Sell of assets?
        # Actually, XIRR is usually calculating return on *investment*.
        # Cash In = Buy (Amount we put IN to buy assets) -> OUTFLOW from pocket
        # Cash Out = Sell (Amount we get OUT) -> INFLOW to pocket
        # Dividend = INFLOW
        
        # Query
        normalized_asset_ids = None
        if include_asset_ids is not None:
            normalized_asset_ids = sorted({aid for aid in include_asset_ids if aid})
            if not normalized_asset_ids:
                return None

        # Find which assets we are looking at to build the correct deduplication clause
        from src.services.transaction_source_selector import build_source_filter_clauses
        
        assets_to_filter = normalized_asset_ids
        if assets_to_filter is None:
            tx_assets = db.execute("SELECT DISTINCT asset_id FROM transactions WHERE is_provisional = FALSE").fetchall()
            assets_to_filter = sorted({r[0] for r in tx_assets if r and r[0]})
            
        dedup_clause, dedup_params = build_source_filter_clauses(db, assets_to_filter)
        
        tx_filter = f" AND {dedup_clause}"
        tx_params = list(dedup_params)
        
        if start_date:
            tx_filter += " AND transaction_date >= ?"
            tx_params.append(start_date)
        if end_date:
            tx_filter += " AND transaction_date <= ?"
            tx_params.append(end_date)

        tx_query = f"""
            SELECT asset_id, transaction_date, transaction_type, amount_net, currency
            FROM transactions
            WHERE is_provisional = FALSE
            {tx_filter}
        """
        rows = db.execute(tx_query, tx_params or None).fetchall()
        
        cashflows = []
        asset_currency_map: dict[str, str] = {}
        today_usd_cny_rate = get_currency_service().get_latest_rate('USD', 'CNY') or 7.0

        for row in rows:
            asset_id = row[0]
            dt = row[1]
            # Ensure date object
            if isinstance(dt, str):
                dt = date.fromisoformat(dt)
            elif hasattr(dt, 'date'):
                dt = dt.date()
                
            typ = row[2].lower()
            amt = float(row[3] or 0.0)
            currency = str(row[4] or 'CNY')
            if asset_id and asset_id not in asset_currency_map:
                asset_currency_map[asset_id] = currency
            
            if amt == 0:
                continue
                
            if typ in OUTFLOW_TRANSACTION_TYPES:
                # We spent money -> Negative
                signed_amount = -abs(amt)
            elif typ in INFLOW_TRANSACTION_TYPES:
                # We received money -> Positive
                signed_amount = abs(amt)
            else:
                continue

            if currency == 'USD':
                signed_amount *= today_usd_cny_rate
            cashflows.append((dt, signed_amount))
                
        # 2. Add Terminal Value (Current Portfolio Value)
        # Treated as if we sold everything today -> Positive Inflow
        snapshot_filter = ""
        snapshot_params = []
        if normalized_asset_ids is not None:
            placeholders = ", ".join(["?"] * len(normalized_asset_ids))
            snapshot_filter += f" AND asset_id IN ({placeholders})"
            snapshot_params.extend(normalized_asset_ids)
        if start_date:
            snapshot_filter += " AND snapshot_date >= ?"
            snapshot_params.append(start_date)
        if end_date:
            snapshot_filter += " AND snapshot_date <= ?"
            snapshot_params.append(end_date)

        val_query = f"""
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) as latest_date
                FROM holdings
                WHERE is_shadow = FALSE {snapshot_filter}
                GROUP BY asset_id
            )
            SELECT
                h.asset_id,
                MAX(h.currency) AS currency,
                SUM(COALESCE(h.quantity, 0) * COALESCE(h.market_price_unit, 0)) AS native_terminal_value,
                SUM(COALESCE(h.market_value, 0)) AS cny_terminal_value
            FROM holdings h
            JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            WHERE h.is_shadow = FALSE 
            GROUP BY h.asset_id
        """
        terminal_val = 0.0
        for asset_id, holding_currency, native_terminal_value, cny_terminal_value in db.execute(
            val_query, snapshot_params or None
        ).fetchall():
            asset_currency = asset_currency_map.get(asset_id, holding_currency or 'CNY')
            if asset_currency == 'USD':
                terminal_val += float(native_terminal_value or 0.0) * today_usd_cny_rate
            else:
                terminal_val += float(cny_terminal_value or 0.0)
            
        today = date.today()
        # Ensure terminal value date is >= last cashflow
        if cashflows:
            last_date = max(c[0] for c in cashflows)
            if today < last_date:
                today = last_date
                
        cashflows.append((today, terminal_val))
        logger.debug(
            'XIRR constant-FX method: USD cashflows converted at today rate=%.4f. '
            'Terminal value for USD assets = quantity * market_price_unit * rate. '
            'CNY assets unchanged. FX timing effects stripped.',
            today_usd_cny_rate
        )
        
        # 3. Calculate
        return calculate_xirr(cashflows)
        
    except Exception as e:
        logger.error(f"Error calculating portfolio XIRR: {e}")
        raise e
