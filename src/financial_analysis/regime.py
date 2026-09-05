"""Market regime assessment — rule-based scoring using price data.

Signals:
1. MA crossover: 50-day vs 200-day moving average (golden/death cross)
2. Realized volatility: 30-day annualized standard deviation of returns
3. Drawdown: current price vs rolling 52-week high
4. Composite score: weighted sum → Bull / Neutral / Bear

No ML dependencies. Uses only numpy (already in deps).
"""
import logging
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Historical hardcoded default — used only if config/settings.yaml is
# unavailable. Kept here as the single fallback value so a config failure
# can never leave any of the three callers below with an empty list.
_DEFAULT_BENCHMARK_PROXY_CODES = ("000300", "CSI300", "000300", "CSI300", "SPY", "^GSPC")


def get_benchmark_proxy_codes() -> "tuple[str, ...]":
    """Market-regime / benchmark proxy codes, tried in order (Program OSR
    WS-2 step 5 — was 3 independent hardcoded copies: this module,
    src.verification.monthly_verifier.BENCHMARK_PROXY_CODES, and an inline
    SQL IN-list in src.services.ai_advisor.behavioral_metrics. The third copy
    had already silently drifted — missing 'CSI300' — exactly the kind of
    divergence a single source of truth prevents.

    Reads config/settings.yaml's verification.benchmark_proxy_codes; falls
    back to the historical 6-code default if config is unavailable, so this
    always returns a usable, non-empty tuple.

    market_daily stores codes like "000300"/"CSI300" (not "000300"/"SPY"
    directly) — see the callers for how each code is actually queried.
    """
    try:
        from src.config import load_config
        config = load_config()
        codes = (config or {}).get("verification", {}).get("benchmark_proxy_codes")
        if codes:
            return tuple(codes)
    except Exception as e:
        logger.warning(
            "get_benchmark_proxy_codes: config unavailable, using historical "
            "default: %s", e,
        )
    return _DEFAULT_BENCHMARK_PROXY_CODES


# Thresholds
VOL_LOW = 0.12       # annualized vol < 12% = low
VOL_HIGH = 0.25      # annualized vol > 25% = high
DRAWDOWN_MILD = -0.05
DRAWDOWN_SEVERE = -0.15


def assess_regime(
    prices: np.ndarray,
    dates: np.ndarray,
) -> Dict[str, Any]:
    """Assess market regime from a price series.

    Args:
        prices: Array of closing prices (oldest first).
        dates: Array of dates (same length as prices).

    Returns:
        Dict with trend, volatility_level, drawdown, MA signals, composite score.
    """
    n = len(prices)
    if n < 50:
        return {
            "trend": "Unknown",
            "volatility_level": "Unknown",
            "volatility_30d": None,
            "drawdown_pct": None,
            "ma50": None,
            "ma200": None,
            "ma_signal": "Insufficient data",
            "score": 0,
            "data_points": n,
        }

    prices = np.array(prices, dtype=float)

    # 1. Moving averages
    ma50 = float(np.mean(prices[-50:]))
    ma200 = float(np.mean(prices[-min(200, n):])) if n >= 200 else float(np.mean(prices))
    current = float(prices[-1])

    if ma50 > ma200 * 1.02:
        ma_signal = "Golden Cross"
        ma_score = 1
    elif ma50 < ma200 * 0.98:
        ma_signal = "Death Cross"
        ma_score = -1
    else:
        ma_signal = "Neutral"
        ma_score = 0

    # 2. Realized volatility (30-day annualized)
    if n >= 30:
        returns_30d = np.diff(prices[-31:]) / prices[-31:-1]
        vol_30d = float(np.std(returns_30d) * np.sqrt(252))
    else:
        returns_all = np.diff(prices) / prices[:-1]
        vol_30d = float(np.std(returns_all) * np.sqrt(252))

    if vol_30d < VOL_LOW:
        vol_level = "Low"
        vol_score = 1
    elif vol_30d > VOL_HIGH:
        vol_level = "High"
        vol_score = -1
    else:
        vol_level = "Normal"
        vol_score = 0

    # 3. Drawdown from peak
    peak = float(np.max(prices[-min(252, n):]))
    drawdown = (current - peak) / peak if peak > 0 else 0.0

    if drawdown > DRAWDOWN_MILD:
        dd_score = 1
    elif drawdown < DRAWDOWN_SEVERE:
        dd_score = -1
    else:
        dd_score = 0

    # 4. Price momentum (3-month return)
    lookback = min(63, n - 1)
    momentum = (current - float(prices[-lookback - 1])) / float(prices[-lookback - 1])
    mom_score = 1 if momentum > 0.05 else (-1 if momentum < -0.05 else 0)

    # 5. Composite score (weighted)
    composite = ma_score * 0.3 + vol_score * 0.2 + dd_score * 0.25 + mom_score * 0.25

    if composite > 0.2:
        trend = "Bull"
    elif composite < -0.2:
        trend = "Bear"
    else:
        trend = "Neutral"

    return {
        "trend": trend,
        "volatility_level": vol_level,
        "volatility_30d": round(vol_30d * 100, 2),
        "drawdown_pct": round(drawdown * 100, 2),
        "ma50": round(ma50, 2),
        "ma200": round(ma200, 2),
        "ma_signal": ma_signal,
        "momentum_3m_pct": round(momentum * 100, 2),
        "score": round(composite, 3),
        "data_points": n,
    }


def assess_portfolio_regime(db: Any) -> Optional[Dict[str, Any]]:
    """Assess market regime using data from stock_daily table.

    Tries to use CSI 300 (000300) or SPY as the market proxy.

    Args:
        db: DatabaseConnector

    Returns:
        Regime assessment dict, or None.
    """
    try:
        # Try CSI 300 proxies first (most relevant for this CN-heavy portfolio),
        # then US proxies, then whatever has the most data.
        # Codes reflect what the DSA reader actually stores in market_daily.
        for symbol in get_benchmark_proxy_codes():
            rows = db.execute(
                """
                SELECT date, close
                FROM market_daily
                WHERE code = ?
                ORDER BY date ASC
                """,
                (symbol,),
            ).fetchall()
            if len(rows) >= 50:
                dates = np.array([r[0] for r in rows])
                prices = np.array([float(r[1]) for r in rows])
                result = assess_regime(prices, dates)
                result["benchmark_symbol"] = symbol
                return result

        # Fallback: use portfolio net worth history as proxy
        rows = db.execute(
            """
            SELECT snapshot_date, SUM(market_value) as total
            FROM holdings WHERE is_shadow = FALSE
            GROUP BY snapshot_date
            ORDER BY snapshot_date ASC
            """
        ).fetchall()
        if len(rows) >= 12:
            dates = np.array([r[0] for r in rows])
            prices = np.array([float(r[1]) for r in rows])
            result = assess_regime(prices, dates)
            result["benchmark_symbol"] = "portfolio_net_worth"
            return result

        return None

    except Exception as e:
        logger.error(f"Error assessing market regime: {e}")
        return None
