"""Valuation signal classification."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ValuationReference:
    ticker: str
    metric: str
    low_threshold: float
    high_threshold: float
    historical_mean: float | None
    rate_sensitive: bool
    pct_low_threshold: float = field(default=30.0)
    pct_high_threshold: float = field(default=70.0)


# Mirrors MIN_PCT_YEARS_FOR_SIGNAL in collector — kept in sync manually.
# A few weeks of daily data can produce wildly misleading percentile signals.
_MIN_PCT_YEARS_FOR_SIGNAL = 3


def classify_signal(
    metric: str,
    value: float | None,
    ref: ValuationReference,
    adj_factor: float = 1.0,
    percentile: float | None = None,
    pct_years: int = 10,
) -> tuple[str, str]:
    """
    Returns (signal, basis_str).
    signal: 'LOW' | 'FAIR' | 'HIGH' | 'N/A'

    Priority:
    1. percentile provided AND pct_years >= 3 → use ref.pct_low/high_threshold (default 30/70)
    2. absolute low/high thresholds (with adj_factor for rate-sensitive PE)
    3. thresholds degenerate (both 0) and historical_mean available → ±15% fallback
    4. no data at all → N/A
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A", "no_data"

    # ── Primary: percentile-based ──────────────────────────────────────────
    # Guard: silently drop the percentile if we have fewer than 3 years of
    # history — a small sample produces a misleading signal (e.g. 8 daily
    # rows → 6th percentile → "LOW" on a stock at 10-year highs).
    if percentile is not None and pct_years < _MIN_PCT_YEARS_FOR_SIGNAL:
        percentile = None

    if percentile is not None:
        low_pct = ref.pct_low_threshold or 30.0
        high_pct = ref.pct_high_threshold or 70.0
        pct_label = f"历史{pct_years}年" if pct_years > 0 else "历史"

        if percentile < low_pct:
            return "LOW", f"{pct_label} {percentile:.0f}th分位 < {low_pct:.0f}th"
        if percentile > high_pct:
            return "HIGH", f"{pct_label} {percentile:.0f}th分位 > {high_pct:.0f}th"
        return "FAIR", f"{pct_label} {percentile:.0f}th分位，处于合理区间"

    # ── Absolute low/high thresholds ──────────────────────────────────────
    if metric == "pe_forward" and ref.rate_sensitive:
        high_eff = ref.high_threshold * adj_factor
    else:
        high_eff = ref.high_threshold
    low_eff = ref.low_threshold

    if high_eff > 0 or low_eff > 0:
        if high_eff <= low_eff:
            return "N/A", "invalid_reference_config"
        if value <= low_eff:
            return "LOW", f"{metric} {value:.2f} ≤ low {low_eff:.2f}"
        if value >= high_eff:
            return "HIGH", f"{metric} {value:.2f} ≥ high {high_eff:.2f} (adj×{adj_factor:.3f})"
        return "FAIR", f"{metric} {value:.2f} in [{low_eff:.2f}, {high_eff:.2f}]"

    # ── Fallback: historical mean ±15% (when thresholds are both 0) ───────
    if ref.historical_mean:
        ratio = value / ref.historical_mean
        if ratio < 0.85:
            return "LOW", (
                f"当前{value:.1f}x < 历史均值{ref.historical_mean:.1f}x的85%"
                f"（降级判断，无百分位数据）"
            )
        if ratio > 1.15:
            return "HIGH", (
                f"当前{value:.1f}x > 历史均值{ref.historical_mean:.1f}x的115%"
                f"（降级判断，无百分位数据）"
            )
        return "FAIR", (
            f"当前{value:.1f}x，历史均值{ref.historical_mean:.1f}x"
            f"（降级判断，无百分位数据）"
        )

    return "N/A", "no_data"
