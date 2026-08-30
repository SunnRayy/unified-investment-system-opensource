"""Historical percentile computation for valuation metrics."""
from __future__ import annotations

import math
from typing import Sequence

def compute_percentile(
    series: Sequence[float | None],
    value: float,
    date_range_days: int = 0,
) -> tuple[float | None, int]:
    """
    Compute mid-rank percentile of value in series.
    Returns (pct 0-100, years_of_data) or (None, 0) if insufficient data.
    Requires >= 5 finite values.
    """
    finite = [x for x in series if x is not None and math.isfinite(x)]
    if len(finite) < 5:
        return None, 0
    count_below = sum(1 for x in finite if x < value)
    count_equal = sum(1 for x in finite if x == value)
    pct = (count_below + 0.5 * count_equal) / len(finite) * 100.0
    years = round(date_range_days / 365.25, 1) if date_range_days > 0 else 0
    return round(pct, 2), int(years)
