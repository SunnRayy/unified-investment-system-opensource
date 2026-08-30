from typing import Dict, Any, Optional
import math

def calculate_portfolio_risk(
    weights: Dict[str, float], 
    custom_volatilities: Optional[Dict[str, float]] = None,
    correlation_matrix: Optional[Dict[tuple, float]] = None
) -> Dict[str, Any]:
    """
    Calculate portfolio risk metrics based on asset class weights using hybrid model.
    
    Args:
        weights: Dictionary mapping asset class names (Chinese) to weight (0.0-1.0)
        custom_volatilities: Optional overrides for class volatilities
        correlation_matrix: Optional (class_a, class_b) -> correlation dict
        
    Returns:
        Dictionary containing volatility, sharpe, var_95, etc.
    """
    # 1. Model Assumptions (Annualized Volatility)
    vols = {
        "股票": 0.18,      # 18% vol for Equity
        "固定收益": 0.06,   # 6% vol for Bonds
        "现金": 0.005,     # 0.5% vol for Cash
        "另类投资": 0.25,   # 25% vol for Crypto/VC
        "商品": 0.15,      # 15% vol for Commodities
        "房地产": 0.12      # 12% vol for Real Estate
    }
    
    if custom_volatilities:
        vols.update(custom_volatilities)
    
    expected_returns = {
        "股票": 0.10, "固定收益": 0.04, "现金": 0.02, 
        "另类投资": 0.15, "商品": 0.05, "房地产": 0.07
    }
    
    # 2. Calculate Weighted Volatility
    # If correlation matrix provided, use Matrix calculation: sqrt(w'Sw)
    if correlation_matrix:
        var_p = 0.0
        classes = list(weights.keys())
        for i in classes:
            for j in classes:
                w_i = weights.get(i, 0)
                w_j = weights.get(j, 0)
                sigma_i = vols.get(i, 0.1)
                sigma_j = vols.get(j, 0.1)
                
                # Get correlation (check both orders)
                rho = 0.0
                if i == j:
                    rho = 1.0
                else:
                    rho = correlation_matrix.get((i, j), correlation_matrix.get((j, i), 0.0))
                
                var_p += w_i * w_j * sigma_i * sigma_j * rho
        port_vol = math.sqrt(var_p) if var_p > 0 else 0.0
        
    else:
        # Fallback to linear sum (conservative, assumes corr=1.0)
        port_vol = sum(weights.get(k, 0) * vols.get(k, 0.10) for k in weights)

    # Return is simple weighted average
    port_ret = sum(weights.get(k, 0) * expected_returns.get(k, 0.05) for k in weights)
    
    # 3. Derived Metrics
    # Daily vol approx
    daily_vol = port_vol / 16.0 
    var_95_pct = 1.65 * daily_vol * 100 
    sharpe = (port_ret - 0.03) / port_vol if port_vol > 0 else 0.0
    
    meaningful_allocs = sum(1 for w in weights.values() if w > 0.05)
    div_score = min(10, meaningful_allocs * 2 + 2)
    
    # Beta calculation
    # If we had market covariance we could calculate real beta. 
    # For now, keep the heuristic but maybe adjust if we have high volatility?
    equity_weight = weights.get("股票", 0)
    beta = 0.8 + (equity_weight * 0.4)
    
    # Status determination
    volatility_status = "LOW" if port_vol * 100 < 10 else "MED" if port_vol * 100 < 20 else "HIGH"
    sharpe_status = "POOR" if sharpe < 0.5 else "AVG" if sharpe < 1.0 else "GOOD" if sharpe < 1.5 else "EXCELLENT"
    var_status = "LOW" if var_95_pct < 1.5 else "MED" if var_95_pct < 3 else "HIGH"
    
    return {
        "volatility": round(port_vol * 100, 2),
        "volatility_status": volatility_status,
        "sharpe": round(sharpe, 2),
        "sharpe_status": sharpe_status,
        "var_95": round(var_95_pct, 2),
        "var_95_status": var_status,
        "beta": round(beta, 2), 
        "div_score": div_score
    }
