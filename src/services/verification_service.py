"""
Verification service — computes fresh verification metrics from existing data sources.

Replaces the --verify-monthly CLI dependency by computing on-demand from:
  - insights table (adoption stats, monthly history)
  - trade_logs table (verdict/outcome counts)
  - strategy_reviewer (allocation drift)

Stores a summary row in verification_logs for historical trending.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from src.config import load_config
from src.services.decision_scorer import compute_insight_adoption_metrics

logger = logging.getLogger(__name__)


def compute_verification_report(db: Any) -> dict:
    """
    Aggregate fresh verification metrics from existing data and store in verification_logs.

    Returns a dict with KPI values, adoption history, and verdict breakdown.
    Safe to call even if trade_logs has no scored rows or insights is empty.
    """
    today = date.today()
    month_start = today.replace(day=1)

    # --- Overall adoption stats — shared with /decisions/stats so both surfaces agree ---
    _adoption = compute_insight_adoption_metrics(db)
    total_insights = _adoption["total_insights"]
    adoption_rate = _adoption["adoption_rate"]

    # --- Monthly adoption history ---
    monthly_rows = db.execute(
        """
        SELECT
            DATE_TRUNC('month', created_at)::DATE AS month,
            COUNT(*)                              AS total,
            SUM(CASE WHEN adopted = 1 THEN 1 ELSE 0 END) AS adopted
        FROM insights
        WHERE created_at IS NOT NULL
          AND COALESCE(category, '') != 'lesson'
        GROUP BY DATE_TRUNC('month', created_at)::DATE
        ORDER BY month ASC
        """
    ).fetchall()
    adoption_history = [
        {
            "period_start": str(r[0]) if r[0] else None,
            "adoption_rate": round(int(r[2] or 0) / int(r[1]) * 100, 1) if r[1] else 0.0,
            "total": int(r[1]),
            "adopted": int(r[2] or 0),
        }
        for r in monthly_rows
    ]

    # --- Monthly verdict breakdown ---
    # 'neutrals' is additive (backward-compatible new column).
    # 'total_scored' counts ALL non-NULL verdicts (for total reporting).
    verdict_rows = db.execute(
        """
        SELECT
            DATE_TRUNC('month', log_date)::DATE AS month,
            SUM(CASE WHEN verdict = 'good_call'           THEN 1 ELSE 0 END) AS good_calls,
            SUM(CASE WHEN verdict = 'regret'              THEN 1 ELSE 0 END) AS regrets,
            SUM(CASE WHEN verdict = 'missed_opportunity'  THEN 1 ELSE 0 END) AS missed,
            SUM(CASE WHEN verdict = 'bullet_dodged'       THEN 1 ELSE 0 END) AS bullet_dodged,
            COUNT(*) FILTER (WHERE verdict IS NOT NULL)                       AS total_scored,
            SUM(CASE WHEN verdict = 'neutral'             THEN 1 ELSE 0 END) AS neutrals
        FROM trade_logs
        WHERE log_date IS NOT NULL
        GROUP BY DATE_TRUNC('month', log_date)::DATE
        ORDER BY month DESC
        """
    ).fetchall()
    verdict_breakdown = [
        {
            "period_start": str(r[0]) if r[0] else None,
            "good_calls": int(r[1] or 0),
            "regrets": int(r[2] or 0),
            "missed_opportunity": int(r[3] or 0),
            "bullet_dodged": int(r[4] or 0),
            "total_scored": int(r[5] or 0),
            "neutrals": int(r[6] or 0),
        }
        for r in verdict_rows
    ]

    # --- Overall verdict KPIs ---
    # verdict_hit_rate = good_call / decisive_verdicts × 100.
    # Decisive = good_call + regret + missed_opportunity + bullet_dodged.
    # Neutral is reported separately and must not depress the hit rate: a DCA trade
    # that landed within ±5% "按计划" should not count as a missed good call.
    scored_row = db.execute(
        """
        SELECT
            SUM(CASE WHEN verdict IN ('good_call','regret','missed_opportunity','bullet_dodged')
                     THEN 1 ELSE 0 END) AS decisive,
            SUM(CASE WHEN verdict = 'good_call' THEN 1 ELSE 0 END) AS good_calls
        FROM trade_logs
        WHERE verdict IS NOT NULL
        """
    ).fetchone()
    total_scored = int(scored_row[0] or 0) if scored_row else 0  # decisive count, kept as 'total_scored' for compat
    good_calls = int(scored_row[1] or 0) if scored_row else 0
    verdict_hit_rate = round(good_calls / total_scored * 100, 1) if total_scored > 0 else None

    # --- Portfolio vs benchmark ---
    portfolio_return = None
    benchmark_return = None
    alpha = None
    try:
        from src.verification.monthly_verifier import (
            calculate_benchmark_return,
            calculate_portfolio_return,
        )

        config = load_config()
        benchmark_code = config.get("verification", {}).get("benchmark_code", "000300")
        portfolio_return = calculate_portfolio_return(db, month_start, today)
        benchmark_return = calculate_benchmark_return(db, month_start, today, benchmark_code)
        if portfolio_return is not None and benchmark_return is not None:
            alpha = round(portfolio_return - benchmark_return, 4)
    except Exception as exc:
        logger.warning("Could not compute portfolio vs benchmark returns: %s", exc)

    # --- Max allocation drift vs strategic targets ---
    max_drift = None
    try:
        from src.services.strategy_reviewer import review_allocation_alignment
        alignment = review_allocation_alignment(db)
        scope = alignment.get("target_scope_alignment", {})
        drifts = [
            abs(v["drift_pct"])
            for v in scope.values()
            if v.get("drift_pct") is not None
        ]
        if drifts:
            max_drift = round(max(drifts), 2)
    except Exception as exc:
        logger.warning("Could not compute max allocation drift: %s", exc)

    # --- Persist summary to verification_logs ---
    try:
        db.execute(
            """
            INSERT INTO verification_logs (
                verification_date, verification_type,
                period_start, period_end,
                adoption_rate, max_allocation_drift,
                total_insights, generated_by,
                portfolio_return, benchmark_return, alpha
            ) VALUES (?, 'monthly', ?, ?, ?, ?, ?, 'system', ?, ?, ?)
            """,
            (
                today, month_start, today,
                adoption_rate, max_drift, total_insights,
                portfolio_return, benchmark_return, alpha,
            ),
        )
    except Exception as exc:
        logger.warning("Could not persist verification_log row: %s", exc)

    return {
        "verification_date": str(today),
        "period_start": str(month_start),
        "period_end": str(today),
        "adoption_rate": adoption_rate,
        "max_drift": max_drift,
        "total_insights": total_insights,
        "verdict_hit_rate": verdict_hit_rate,
        "good_calls": good_calls,
        "total_scored": total_scored,
        "portfolio_return": portfolio_return,
        "benchmark_return": benchmark_return,
        "alpha": alpha,
        "adoption_history": adoption_history,
        "verdict_breakdown": verdict_breakdown,
    }
