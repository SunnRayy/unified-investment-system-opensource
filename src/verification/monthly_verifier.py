"""Monthly verification — compute KPIs from Phase 3 data and write to verification_logs."""
import json
import logging
from datetime import date
from typing import Optional

from src.financial_analysis.regime import get_benchmark_proxy_codes
from src.financial_analysis.snapshot_provider import get_portfolio_value_series

logger = logging.getLogger(__name__)


def _sum_latest_holdings_value_as_of(connector, as_of_date: date) -> Optional[float]:
    """Return authoritative holdings value using each asset's latest non-shadow row up to a date."""
    row = connector.execute(
        """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS max_date
            FROM holdings
            WHERE is_shadow = FALSE
              AND snapshot_date <= ?
            GROUP BY asset_id
        )
        SELECT SUM(h.market_value)
        FROM holdings h
        JOIN latest_per_asset l
          ON h.asset_id = l.asset_id
         AND h.snapshot_date = l.max_date
        WHERE h.is_shadow = FALSE
        """,
        (str(as_of_date),),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0])


def calculate_adoption_rate(connector, period_start: date, period_end: date) -> float:
    """Calculate recommendation adoption rate for the period.

    Formula: adopted_count / decided_count * 100
    Excludes insights where adopted IS NULL (pending).
    """
    row = connector.execute("""
        SELECT
            COUNT(*) FILTER (WHERE adopted = TRUE) as adopted,
            COUNT(*) FILTER (WHERE adopted IS NOT NULL) as decided
        FROM insights
        WHERE category = 'recommendation'
            AND insight_date BETWEEN ? AND ?
    """, (str(period_start), str(period_end))).fetchone()

    adopted, decided = row[0], row[1]
    if decided == 0:
        return 0.0
    return round(adopted / decided * 100, 1)


def calculate_adoption_rate_by_model(connector, period_start: date, period_end: date) -> dict:
    """Adoption rate broken down by AI model."""
    rows = connector.execute("""
        SELECT
            ai_model,
            COUNT(*) FILTER (WHERE adopted = TRUE) as adopted,
            COUNT(*) FILTER (WHERE adopted IS NOT NULL) as decided
        FROM insights
        WHERE category = 'recommendation'
            AND insight_date BETWEEN ? AND ?
            AND ai_model IS NOT NULL
        GROUP BY ai_model
    """, (str(period_start), str(period_end))).fetchall()

    rates = {}
    for model, adopted, decided in rows:
        rates[model] = round(adopted / decided * 100, 1) if decided > 0 else 0.0
    return rates


def calculate_max_drift(connector, period_start: date, period_end: date) -> tuple[float, list]:
    """Calculate max allocation drift and per-class details from the period.

    Returns (max_drift_abs, details_list).
    """
    rows = connector.execute("""
        SELECT asset_class, current_pct, target_pct, deviation_pct, tolerance_pct, is_within_tolerance
        FROM deviation_actions
        WHERE detected_date BETWEEN ? AND ?
            AND asset_class IS NOT NULL
        ORDER BY ABS(deviation_pct) DESC
    """, (str(period_start), str(period_end))).fetchall()

    if not rows:
        return 0.0, []

    details = []
    for r in rows:
        details.append({
            "asset_class": r[0],
            "current_pct": float(r[1]) if r[1] else None,
            "target_pct": float(r[2]) if r[2] else None,
            "deviation_pct": float(r[3]) if r[3] else None,
            "tolerance_pct": float(r[4]) if r[4] else None,
            "is_within_tolerance": r[5],
        })

    max_drift = max(abs(float(r[3])) for r in rows if r[3] is not None) if rows else 0.0
    return max_drift, details


def count_insights(connector, period_start: date, period_end: date) -> int:
    """Count total insights recorded in the period."""
    row = connector.execute("""
        SELECT COUNT(*) FROM insights
        WHERE insight_date BETWEEN ? AND ?
    """, (str(period_start), str(period_end))).fetchone()
    return row[0]


def calculate_portfolio_return(connector, period_start: date, period_end: date) -> Optional[float]:
    """Calculate portfolio return for the period.

    Uses the shared portfolio value series so history comes from balance_sheet_monthly
    and the current terminal point comes from latest authoritative holdings.

    Returns None if insufficient data.
    """
    start_val = _sum_latest_holdings_value_as_of(connector, period_start)
    end_val = _sum_latest_holdings_value_as_of(connector, period_end)

    if start_val is None or end_val is None:
        snapshots = get_portfolio_value_series(connector, end_date=str(period_end))
        if not snapshots:
            return None

        start_snapshot = None
        end_snapshot = None
        for snapshot in snapshots:
            snapshot_date = snapshot["date"]
            if snapshot_date <= period_start:
                start_snapshot = snapshot
            if snapshot_date <= period_end:
                end_snapshot = snapshot

        if start_snapshot is None:
            start_snapshot = next((s for s in snapshots if s["date"] >= period_start), None)

        if start_val is None and start_snapshot is not None:
            start_val = float(start_snapshot["value"] or 0.0)
        if end_val is None and end_snapshot is not None:
            end_val = float(end_snapshot["value"] or 0.0)

    if start_val is None or end_val is None:
        return None

    if start_val == 0:
        return None

    return round((end_val - start_val) / start_val * 100, 4)


def calculate_benchmark_return(connector, period_start: date, period_end: date, benchmark_code: str) -> Optional[float]:
    """Calculate benchmark return from market_daily data.

    Returns None if benchmark data not available.
    """
    candidate_codes = []
    if benchmark_code:
        candidate_codes.append(benchmark_code)
    candidate_codes.extend(
        code for code in get_benchmark_proxy_codes() if code != benchmark_code
    )

    for code in candidate_codes:
        row = connector.execute("""
            SELECT
                (SELECT close FROM market_daily WHERE code = ? AND date <= ? ORDER BY date DESC LIMIT 1),
                (SELECT close FROM market_daily WHERE code = ? AND date <= ? ORDER BY date DESC LIMIT 1)
        """, (code, str(period_start), code, str(period_end))).fetchone()

        if not row or not row[0] or not row[1] or float(row[0]) == 0:
            continue
        return round((float(row[1]) - float(row[0])) / float(row[0]) * 100, 4)

    return None


def run_monthly_verification(
    connector,
    period_start: date,
    period_end: date,
    config: dict,
    generated_by: str = "system"
) -> dict:
    """Run full monthly verification and save to verification_logs.

    Computes all KPIs and inserts a single verification_logs record.
    Returns the computed result dict.
    """
    benchmark_code = config.get("verification", {}).get("benchmark_code", "000300")

    adoption_rate = calculate_adoption_rate(connector, period_start, period_end)
    adoption_by_model = calculate_adoption_rate_by_model(connector, period_start, period_end)
    max_drift, drift_details = calculate_max_drift(connector, period_start, period_end)
    total_insights = count_insights(connector, period_start, period_end)
    portfolio_return = calculate_portfolio_return(connector, period_start, period_end)
    benchmark_return = calculate_benchmark_return(connector, period_start, period_end, benchmark_code)

    alpha = None
    if portfolio_return is not None and benchmark_return is not None:
        alpha = round(portfolio_return - benchmark_return, 4)

    result = {
        "verification_type": "monthly",
        "period_start": str(period_start),
        "period_end": str(period_end),
        "ai_hit_rate": None,  # Phase 4.5: requires outcome_accuracy backfill
        "ai_hit_rate_by_model": None,
        "adoption_rate": adoption_rate,
        "adoption_rate_by_model": adoption_by_model,  # Per-model breakdown for CLI display
        "portfolio_return": portfolio_return,
        "benchmark_return": benchmark_return,
        "alpha": alpha,
        "max_allocation_drift": max_drift,
        "drift_details": drift_details,
        "total_insights": total_insights,
        "generated_by": generated_by,
    }

    # Save to verification_logs
    connector.execute("""
        INSERT INTO verification_logs (
            verification_date, verification_type, period_start, period_end,
            ai_hit_rate, ai_hit_rate_by_model, adoption_rate,
            portfolio_return, benchmark_return, alpha,
            max_allocation_drift, drift_details,
            total_insights, generated_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(date.today()),
        result["verification_type"],
        result["period_start"],
        result["period_end"],
        result["ai_hit_rate"],
        json.dumps(adoption_by_model) if adoption_by_model else None,
        result["adoption_rate"],
        result["portfolio_return"],
        result["benchmark_return"],
        result["alpha"],
        result["max_allocation_drift"],
        json.dumps(drift_details) if drift_details else None,
        result["total_insights"],
        result["generated_by"],
    ))

    logger.info(f"Monthly verification saved: adoption={adoption_rate}%, drift={max_drift}%, insights={total_insights}")
    return result
