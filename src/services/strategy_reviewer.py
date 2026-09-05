"""Generate strategy alignment reports from holdings, targets, and trade behavior."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_STRATEGY_REPORT_VERSION = "2026-05-21-scope-v3"

_TARGET_CLASS_MAP: dict[str, str] = {
    "CN Equity": "CN Equity",
    "HK Equity": "HK Equity",
    "HK ETF": "HK Equity",
    "US Equity": "US Equity",
    "固定收益": "Fixed Income",
    "现金": "Cash",
}

_HOLDINGS_CLASS_MAP: dict[str, str] = {
    "CN Equity": "CN Equity",
    "HK Equity": "HK Equity",
    "HK ETF": "HK Equity",
    "US Equity": "US Equity",
    "US Bonds": "Fixed Income",
    "Fixed Income": "Fixed Income",
    "Bank Wealth": "Fixed Income",
    "Cash Checking": "Cash",
    "Money Market": "Cash",
    "Cash": "Cash",
}

_SCOPE_LABEL_NORMALIZATION: dict[str, str] = {
    "Property": "Real Estate",
    "Real Estate": "Real Estate",
    "Insurance Products": "Insurance",
    "Insurance": "Insurance",
    "Gold": "Commodity",
    "Commodity": "Commodity",
    "Crypto": "Alternative",
    "Alternative": "Alternative",
}

_SCOPE_EXCLUDED_CLASSES = ("Alternative", "Commodity", "Insurance", "Real Estate")
_BTC_PROXY_TOKENS = ("IBIT", "FBTC", "BTC")

# Map profile class names (sub-classes) → taxonomy top-level class names.
# Includes Chinese sub-class names that aren't in taxonomy_classes table.
def _has_taxonomy_classes(db: Any) -> bool:
    """Check if the taxonomy_classes table exists (production DB vs test DB)."""
    try:
        db.execute("SELECT 1 FROM taxonomy_classes LIMIT 1")
        return True
    except Exception:
        return False
def _normalize_scope_label(name: str | None) -> str:
    if not name:
        return "Unclassified"
    return _SCOPE_LABEL_NORMALIZATION.get(name, name)


def _is_btc_proxy(asset_id: str | None, display_name: str | None) -> bool:
    haystacks = [asset_id or "", display_name or ""]
    upper_values = [value.upper() for value in haystacks]
    return any(token in value for value in upper_values for token in _BTC_PROXY_TOKENS)


def _compute_status(alignment: dict[str, dict[str, Any]]) -> str:
    drifting_count = sum(1 for item in alignment.values() if item.get("status") == "drifting")
    if drifting_count > 3:
        return "misaligned"
    if drifting_count > 0:
        return "drifting"
    return "aligned"


def _fetch_current_holdings_scope_rows(db: Any, has_tc: bool) -> list[tuple[str, str, str, str | None, float]]:
    if has_tc:
        rows = db.execute(
            """
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) AS max_date
                FROM holdings
                WHERE is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT
                h.asset_id,
                COALESCE(r.asset_class, 'Unclassified') AS raw_class,
                COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified') AS top_class,
                r.display_name,
                SUM(h.market_value) AS total_value
            FROM holdings h
            JOIN latest_per_asset l
              ON h.asset_id = l.asset_id
             AND h.snapshot_date = l.max_date
            LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
            LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
            LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
            WHERE h.is_shadow = FALSE
              AND h.market_value > 0
            GROUP BY h.asset_id, COALESCE(r.asset_class, 'Unclassified'), COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified'), r.display_name
            """
        ).fetchall()
    else:
        rows = db.execute(
            """
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) AS max_date
                FROM holdings
                WHERE is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT
                h.asset_id,
                COALESCE(r.asset_class, 'Unclassified') AS raw_class,
                COALESCE(r.asset_class, 'Unclassified') AS top_class,
                r.display_name,
                SUM(h.market_value) AS total_value
            FROM holdings h
            JOIN latest_per_asset l
              ON h.asset_id = l.asset_id
             AND h.snapshot_date = l.max_date
            LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
            WHERE h.is_shadow = FALSE
              AND h.market_value > 0
            GROUP BY h.asset_id, COALESCE(r.asset_class, 'Unclassified'), r.display_name
            """
        ).fetchall()
    return [(str(r[0]), _normalize_scope_label(r[1]), _normalize_scope_label(r[2]), r[3], float(r[4] or 0.0)) for r in rows]


def _build_target_scope_alignment(
    current_rows: list[tuple[str, str, str, str | None, float]],
    target_rows: list[tuple[str, float]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], str]:
    target_map: dict[str, float] = {}
    for raw_class, pct in target_rows:
        normalized = _TARGET_CLASS_MAP.get(raw_class)
        if normalized:
            target_map[normalized] = target_map.get(normalized, 0.0) + float(pct)

    current_map: dict[str, float] = {key: 0.0 for key in target_map}
    excluded_classes: set[str] = set()
    for asset_id, raw_class, _, display_name, market_value in current_rows:
        normalized = _HOLDINGS_CLASS_MAP.get(raw_class)
        if _is_btc_proxy(asset_id, display_name):
            normalized = "US Equity"
        if normalized and normalized in current_map:
            current_map[normalized] += market_value
        else:
            excluded_classes.add(_normalize_scope_label(raw_class))

    total_value = sum(current_map.values())
    alignment: dict[str, dict[str, Any]] = {}
    for cls, target_pct in target_map.items():
        actual_pct = round(current_map.get(cls, 0.0) / total_value * 100, 2) if total_value > 0 else 0.0
        drift_pct = round(actual_pct - target_pct, 2)
        alignment[cls] = {
            "actual_pct": actual_pct,
            "target_pct": round(target_pct, 2),
            "drift_pct": drift_pct,
            "status": "drifting" if abs(drift_pct) > 5 else "aligned",
        }

    summary = {
        "included_classes": list(target_map.keys()),
        "excluded_classes": sorted(cls for cls in excluded_classes if cls in _SCOPE_EXCLUDED_CLASSES),
        "coverage_note": "Strategic scope excludes gold, alternatives, real estate, and insurance. BTC held via US ETF proxies is included.",
    }
    return alignment, summary, _compute_status(alignment)


def _fetch_uis_target_map(db: Any) -> dict[str, float]:
    rows = db.execute(
        """
        SELECT
            COALESCE(parent_tc.name, tc.name) AS top_class,
            SUM(rpa.target_pct) AS total_target_pct
        FROM risk_profile_allocations rpa
        JOIN risk_profiles rp ON rpa.profile_id = rp.id
        JOIN taxonomy_classes tc ON rpa.class_id = tc.id
        LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
        WHERE rp.is_active = TRUE
        GROUP BY COALESCE(parent_tc.name, tc.name)
        """
    ).fetchall()
    return {_normalize_scope_label(row[0]): float(row[1] or 0.0) for row in rows}


def _build_uis_scope_alignment(
    current_rows: list[tuple[str, str, str, str | None, float]],
    target_map: dict[str, float],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], str]:
    included_classes = set(target_map.keys())
    current_map: dict[str, float] = {key: 0.0 for key in target_map}
    excluded_classes: set[str] = set()

    for _, _, top_class, _, market_value in current_rows:
        normalized = _normalize_scope_label(top_class)
        if normalized in included_classes:
            current_map[normalized] += market_value
        else:
            excluded_classes.add(normalized)

    total_value = sum(current_map.values())
    alignment: dict[str, dict[str, Any]] = {}
    for cls, target_pct in target_map.items():
        actual_pct = round(current_map.get(cls, 0.0) / total_value * 100, 2) if total_value > 0 else 0.0
        drift_pct = round(actual_pct - target_pct, 2)
        alignment[cls] = {
            "actual_pct": actual_pct,
            "target_pct": round(target_pct, 2),
            "drift_pct": drift_pct,
            "status": "drifting" if abs(drift_pct) > 5 else "aligned",
        }

    summary = {
        "included_classes": list(target_map.keys()),
        "coverage_note": "Huinsight scope uses the active risk profile for investable target classes.",
        "excluded_classes": sorted(excluded_classes),
    }
    return alignment, summary, _compute_status(alignment)


def _normalize_profile_scope_class(cls: str) -> str:
    if cls in {"CN Equity", "HK Equity", "US Equity"}:
        return "Equity"
    return cls


def review_allocation_alignment(db: Any) -> dict[str, Any]:
    """Compare current allocations against strategic targets and Huinsight risk profile."""
    has_tc = _has_taxonomy_classes(db)

    current_rows = _fetch_current_holdings_scope_rows(db, has_tc)
    target_rows = db.execute(
        """
        WITH ranked AS (
            SELECT asset_class, target_pct,
                   ROW_NUMBER() OVER (PARTITION BY asset_class ORDER BY effective_date DESC, id DESC) AS rn
            FROM target_allocations
            WHERE source = 'Strategic_Profile'
        )
        SELECT asset_class, target_pct FROM ranked WHERE rn = 1
        """
    ).fetchall()
    target_scope_alignment, target_scope_summary, target_status = _build_target_scope_alignment(current_rows, target_rows)
    uis_target_map = _fetch_uis_target_map(db) if has_tc else {}
    uis_scope_alignment, uis_scope_summary, uis_status = _build_uis_scope_alignment(current_rows, uis_target_map)

    return {
        "target_scope_alignment": target_scope_alignment,
        "uis_scope_alignment": uis_scope_alignment,
        "target_scope_summary": target_scope_summary,
        "uis_scope_summary": uis_scope_summary,
        "target_scope_alignment_status": target_status,
        "uis_scope_alignment_status": uis_status,
    }


def review_trading_frequency(db: Any) -> dict[str, Any]:
    """Compute 30/60/90-day trade counts and frequency assessment."""
    today = date.today()

    def count_trades(days: int) -> int:
        cutoff = today - timedelta(days=days)
        row = db.execute(
            """
            SELECT COUNT(*)
            FROM trade_logs
            WHERE log_date >= ?
              AND suggestion_source IS NOT NULL
            """,
            (cutoff,),
        ).fetchone()
        return int(row[0]) if row else 0

    count_30 = count_trades(30)
    count_60 = count_trades(60)
    count_90 = count_trades(90)

    assessment = "aligned"
    if count_30 > 8:
        assessment = "high_frequency"
    elif count_30 > 4:
        assessment = "moderate"

    return {
        "period_30d": count_30,
        "period_60d": count_60,
        "period_90d": count_90,
        "monthly_rate": count_30,
        "assessment": assessment,
        "philosophy_threshold": 4,
    }


def review_contrarian_consistency(db: Any) -> dict[str, Any]:
    """Estimate contrarian consistency from recent sell timing vs market moves."""
    sells = db.execute(
        """
        SELECT log_date, asset_id
        FROM trade_logs
        WHERE action IN ('Sell', 'sell')
          AND log_date >= CURRENT_DATE - INTERVAL '180 days'
          AND suggestion_source IS NOT NULL
        ORDER BY log_date DESC
        LIMIT 50
        """
    ).fetchall()

    if not sells:
        return {"status": "ok", "contrarian_score": 100.0, "sell_count": 0, "panic_sell_count": 0, "details": []}

    details: list[dict[str, Any]] = []
    panic_sells = 0
    usable_signals = 0
    for sell_date, asset_id in sells:
        market_code = "110020" if asset_id.startswith("CN_") or asset_id.startswith("HK_") else "SPY"
        # Use nearest prior trading day within 3 calendar days
        cutoff = sell_date - timedelta(days=3)
        market_row = db.execute(
            """
            SELECT close, open
            FROM market_daily
            WHERE code = ?
              AND date <= ?
              AND date >= ?
            ORDER BY date DESC
            LIMIT 1
            """,
            (market_code, sell_date, cutoff),
        ).fetchone()

        market_return = None
        was_panic = False
        if market_row and market_row[1] and float(market_row[1]) > 0:
            market_return = (float(market_row[0]) - float(market_row[1])) / float(market_row[1]) * 100
            if abs(market_return) > 0.0001:
                usable_signals += 1
            if market_return < -1.0:
                panic_sells += 1
                was_panic = True

        # Only add to details if market data was found
        if market_return is not None:
            details.append(
                {
                    "date": str(sell_date),
                    "asset_id": asset_id,
                    "market_return_pct": round(market_return, 2),
                    "was_panic_sell": was_panic,
                }
            )

    total = len(sells)
    if usable_signals == 0:
        return {
            "status": "insufficient_market_context",
            "contrarian_score": None,
            "sell_count": total,
            "panic_sell_count": 0,
            "details": details[:10],
        }

    score = round((1 - panic_sells / total) * 100, 1) if total > 0 else 100.0
    return {
        "status": "ok",
        "contrarian_score": score,
        "sell_count": total,
        "panic_sell_count": panic_sells,
        "details": details[:10],
    }


def generate_strategy_report(db: Any) -> dict[str, Any]:
    """Build and store one strategy review report row."""
    allocation = review_allocation_alignment(db)
    frequency = review_trading_frequency(db)
    contrarian = review_contrarian_consistency(db)

    target_top_classes = set(allocation["target_scope_summary"]["included_classes"])
    uis_top_classes = set(allocation["uis_scope_summary"]["included_classes"])
    normalized_target_classes = {_normalize_profile_scope_class(cls) for cls in target_top_classes}
    normalized_uis_classes = {_normalize_profile_scope_class(cls) for cls in uis_top_classes}
    discrepancies = {
        "target_only": sorted(normalized_target_classes - normalized_uis_classes),
        "uis_only": sorted(normalized_uis_classes - normalized_target_classes),
        "both": sorted(normalized_target_classes & normalized_uis_classes),
    }

    report = {
        "review_date": str(date.today()),
        **allocation,
        "trading_frequency": frequency,
        "contrarian_score": contrarian["contrarian_score"],
        "contrarian_details": contrarian,
        "profile_discrepancies": discrepancies,
    }

    db.execute(
        """
        INSERT INTO strategy_review_reports
            (review_date, allocation_alignment, trading_frequency, contrarian_score,
             contrarian_details, profile_discrepancies, overall_alignment)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            date.today(),
            json.dumps({
                "report_version": _STRATEGY_REPORT_VERSION,
                "target_scope_alignment": report["target_scope_alignment"],
                "uis_scope_alignment": report["uis_scope_alignment"],
                "target_scope_summary": report["target_scope_summary"],
                "uis_scope_summary": report["uis_scope_summary"],
                "target_scope_alignment_status": report["target_scope_alignment_status"],
                "uis_scope_alignment_status": report["uis_scope_alignment_status"],
            }, ensure_ascii=False, default=str),
            json.dumps(report["trading_frequency"]),
            report["contrarian_score"],
            json.dumps(report["contrarian_details"], ensure_ascii=False, default=str),
            json.dumps(report["profile_discrepancies"]),
            report["uis_scope_alignment_status"],
        ),
    )

    return report
