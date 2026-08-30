"""Sliding-scale PE compression based on US 10Y yield."""

def adjusted_factor(us10y_pct: float) -> float:
    """
    Returns valuation compression factor in [0.75, 1.0].
    Input must be in percent points (e.g. 4.5, not 0.045).
    Applied to high_threshold and historical_mean for rate-sensitive US equity metrics.
    """
    if not (0.1 < us10y_pct < 20):
        raise ValueError(f"us10y_pct={us10y_pct!r} appears to be decimal (expected percent points 0.1–20)")
    compression = min(max(0.0, us10y_pct - 2.5) * 0.06, 0.25)
    return round(1.0 - compression, 6)
