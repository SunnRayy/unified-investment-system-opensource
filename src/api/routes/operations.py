"""Operations investigation API endpoints."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.sync.phases.manifest import PIPELINE_MANIFEST
from src.validation.data_integrity_gate import run_integrity_checks
from src.sources.registry import get_registry
from src.services.taxonomy_display import get_class_name_cn_map

router = APIRouter(prefix="/operations", tags=["operations"])

LEGACY_SOURCES = ("PIS", "PIS_SQLite", "PIS_Excel", "PIS_Historical")
# Derived from registry — same name, same value, single source of truth.
READER_SOURCES: tuple = get_registry().all_source_systems()


def _fetch_rows(db: DatabaseConnector, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor = db.execute(query, params)
    rows = cursor.fetchall()
    cols = [col[0] for col in cursor.description]
    return [dict(zip(cols, row)) for row in rows]


def _fetch_one(db: DatabaseConnector, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = _fetch_rows(db, query, params)
    return rows[0] if rows else None


def _loads_json(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default
    return default


def _to_iso(ts: Any) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.isoformat()
    if isinstance(ts, date):
        return ts.isoformat()
    return str(ts)


def _latest_active_cte() -> str:
    return """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS max_date
            FROM holdings
            WHERE is_shadow = FALSE
            GROUP BY asset_id
        ),
        active AS (
            SELECT
                h.asset_id,
                COALESCE(r.display_name, h.asset_name, h.asset_id) AS display_name,
                COALESCE(r.asset_class, 'Unclassified') AS asset_class,
                h.source_system,
                h.snapshot_date,
                h.market_value,
                h.quantity,
                COALESCE(h.currency, 'CNY') AS currency
            FROM holdings h
            JOIN latest_per_asset l
              ON h.asset_id = l.asset_id
             AND h.snapshot_date = l.max_date
            LEFT JOIN asset_registry r
              ON r.canonical_id = h.asset_id
            WHERE h.is_shadow = FALSE
        )
    """


def _compute_sync_changelog(latest: dict[str, Any], prior: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Diff two sync_audit_reports rows and produce a changelog event list."""
    events: list[dict[str, Any]] = []

    def _fmt_dt(ts: Any) -> tuple[str, str]:
        if ts is None:
            return "", ""
        try:
            dt = datetime.fromisoformat(str(ts))
            return dt.strftime("%b %d"), dt.strftime("%H:%M")
        except ValueError:
            return "", str(ts)

    created_at = latest.get("created_at")
    date_str, ts_str = _fmt_dt(created_at)

    # Surface warnings from the latest report
    for w in _loads_json(latest.get("warnings"), []):
        events.append({"kind": "warning", "title": str(w), "detail": "", "date": date_str, "ts": ts_str})

    # Surface info_messages
    for msg in _loads_json(latest.get("info_messages"), []):
        events.append({"kind": "info", "title": str(msg), "detail": "", "date": date_str, "ts": ts_str})

    if prior is not None:
        # Diff integrity_checks: find PASS→FAIL and FAIL→PASS transitions
        latest_checks = {c["name"]: c for c in _loads_json(latest.get("integrity_checks"), [])}
        prior_checks = {c["name"]: c for c in _loads_json(prior.get("integrity_checks"), [])}
        for name, check in latest_checks.items():
            prior_check = prior_checks.get(name)
            if prior_check is None:
                continue
            was_passed = bool(prior_check.get("passed"))
            now_passed = bool(check.get("passed"))
            if was_passed and not now_passed:
                events.append({
                    "kind": "warning",
                    "title": f"Integrity check failed — {name}",
                    "detail": f"Actual: {check.get('actual_value', '')} · Threshold: {check.get('threshold', '')}",
                    "date": date_str, "ts": ts_str,
                })
            elif not was_passed and now_passed:
                events.append({
                    "kind": "info",
                    "title": f"Integrity check restored — {name}",
                    "detail": f"Now passing. {check.get('details', '')}",
                    "date": date_str, "ts": ts_str,
                })

        # Detect per-source count changes
        latest_by_src = _loads_json(latest.get("by_source_after"), {})
        prior_by_src = _loads_json(prior.get("by_source_after"), {})
        for src, after in latest_by_src.items():
            before = prior_by_src.get(src, 0)
            after_count = int(after.get("count", 0)) if isinstance(after, dict) else int(after or 0)
            before_count = int(before.get("count", 0)) if isinstance(before, dict) else int(before or 0)
            delta = after_count - before_count
            if delta != 0:
                sign = "+" if delta > 0 else ""
                events.append({
                    "kind": "case" if abs(delta) > 2 else "info",
                    "title": f"Source count changed — {src}",
                    "detail": f"{sign}{delta} asset(s) vs prior sync",
                    "date": date_str, "ts": ts_str,
                })

    return events


def _compute_source_reconciliation(
    latest: dict[str, Any],
    prior: dict[str, Any] | None,
    integrity_check_names: set[str],
) -> list[dict[str, Any]]:
    """Build per-source reconciliation rows from the latest sync_audit_report.

    Compares by_source_before vs by_source_after so the UI can show count/value
    deltas from the most recent sync — e.g. "CN_Fund_Excel: 42 → 41 (−1)".
    """
    by_source_after = _loads_json(latest.get("by_source_after"), {})
    by_source_before = _loads_json(latest.get("by_source_before"), {})
    last_sync = _to_iso(latest.get("created_at")) or ""

    # Derive failing source names from integrity_checks
    failing_sources: set[str] = set()
    for check in _loads_json(latest.get("integrity_checks"), []):
        if "source_reconciliation" in check.get("name", "") and not check.get("passed"):
            failing_sources.add(check["name"])

    rows: list[dict[str, Any]] = []

    def _extract_count_value(entry: Any) -> tuple[int, float]:
        """Handle both legacy flat-int {src: N} and current nested {src: {count, value}} formats."""
        if isinstance(entry, (int, float)):
            return int(entry), 0.0
        if isinstance(entry, dict):
            return int(entry.get("count", 0)), float(entry.get("value", 0.0))
        return 0, 0.0

    for src in READER_SOURCES:
        after = by_source_after.get(src)
        before = by_source_before.get(src)

        db_count, db_value = _extract_count_value(after) if after is not None else (0, 0.0)
        prior_count, prior_value = (_extract_count_value(before) if before is not None else (None, None))

        count_delta = (db_count - prior_count) if prior_count is not None else None
        value_delta_pct: float | None = None
        if prior_value is not None and prior_value != 0:
            value_delta_pct = round((db_value - prior_value) / abs(prior_value) * 100, 2)

        src_lower = src.lower().replace("_", "")
        failed = any(src_lower in f.replace("_", "") for f in failing_sources)
        # Flag as warning if integrity failed, or if a large count drop happened (>5 assets lost)
        large_drop = count_delta is not None and count_delta < -5
        status = "warning" if (failed or large_drop) else ("ok" if db_count > 0 else "missing")

        rows.append({
            "source": src,
            "db_count": db_count,
            "db_value": db_value,
            "prior_count": prior_count,
            "prior_value": prior_value,
            "count_delta": count_delta,
            "value_delta_pct": value_delta_pct,
            "status": status,
            "last_sync": last_sync,
        })

    return rows


@router.get("/portfolio-audit")
async def get_portfolio_audit(db: DatabaseConnector = Depends(get_db)):
    audit_reports = _fetch_rows(
        db,
        """
        SELECT id, created_at, integrity_passed, integrity_total, warnings,
               info_messages, integrity_checks, by_source_after, by_source_before
        FROM sync_audit_reports
        WHERE report_type = 'sync'
        ORDER BY created_at DESC
        LIMIT 3
        """,
    )
    latest_report = audit_reports[0] if audit_reports else None
    prior_report = audit_reports[1] if len(audit_reports) > 1 else None
    report_warnings = _loads_json(latest_report["warnings"], []) if latest_report else []
    reader_warnings = len([w for w in report_warnings if "WARN:" in str(w)])

    sync_changelog = _compute_sync_changelog(latest_report, prior_report) if latest_report else []

    try:
        integrity_report = run_integrity_checks(db)
        checks = {c.name: bool(c.passed) for c in integrity_report.checks}
        integrity_check_names = {c.name for c in integrity_report.checks}

        # Group integrity checks by category for the UI
        CATEGORY_MAP = {
            "net_worth": "Financial Bounds",
            "shadow": "Shadow & Structure",
            "source_reconciliation": "Source Reconciliation",
            "twr": "Return Metrics",
            "xirr": "Return Metrics",
            "cost_basis": "Financial Bounds",
            "endpoint": "Cross-Endpoint Consistency",
        }
        cat_groups: dict[str, dict[str, Any]] = {}
        for c in integrity_report.checks:
            cat = next((v for k, v in CATEGORY_MAP.items() if k in c.name), "Other")
            if cat not in cat_groups:
                cat_groups[cat] = {"cat": cat, "pass": 0, "total": 0, "fails": []}
            cat_groups[cat]["total"] += 1
            if c.passed:
                cat_groups[cat]["pass"] += 1
            else:
                cat_groups[cat]["fails"].append({
                    "name": c.name,
                    "actual": str(c.actual_value) if c.actual_value is not None else "",
                    "thr": str(c.threshold) if c.threshold else "",
                    "details": c.details or "",
                })
        integrity_grouped = sorted(cat_groups.values(), key=lambda r: r["cat"])

        integrity_summary = {
            "passed": integrity_report.passed_count,     # verified only
            "skipped": integrity_report.skipped_count,   # evaluated nothing
            "total": len(integrity_report.checks),
            "all_passed": integrity_report.all_passed,
        }
    except Exception:
        checks = {}
        integrity_check_names = set()
        integrity_grouped = []
        integrity_summary = {"passed": 0, "total": 0, "all_passed": False}

    source_reconciliation = (
        _compute_source_reconciliation(latest_report, prior_report, integrity_check_names)
        if latest_report else []
    )

    class_rows = _fetch_rows(
        db,
        f"""
        {_latest_active_cte()}
        SELECT
            asset_class,
            source_system,
            COUNT(*) AS asset_count,
            SUM(COALESCE(market_value, 0)) AS total_value
        FROM active
        GROUP BY asset_class, source_system
        ORDER BY asset_class, source_system
        """,
    )

    asset_flags = _fetch_rows(
        db,
        f"""
        {_latest_active_cte()}
        SELECT
            a.asset_id,
            a.asset_class,
            a.source_system,
            a.snapshot_date,
            CASE
                WHEN a.asset_id LIKE 'Pension_%' OR a.asset_id LIKE 'Property_%'
                     OR a.asset_id LIKE 'CASH_%' THEN FALSE
                WHEN a.market_value IS NULL THEN TRUE
                WHEN a.market_value < 0 THEN TRUE
                ELSE FALSE
            END AS value_issue,
            EXISTS (
                SELECT 1
                FROM holdings h2
                WHERE h2.asset_id = a.asset_id
                  AND h2.source_system IN {LEGACY_SOURCES}
                  AND h2.is_shadow = FALSE
                  AND h2.snapshot_date = (
                      SELECT MAX(h3.snapshot_date)
                      FROM holdings h3
                      WHERE h3.asset_id = h2.asset_id
                        AND h3.is_shadow = FALSE
                  )
            ) AS legacy_influence
        FROM active a
        """,
    )

    source_counts: dict[str, int] = {}
    class_map: dict[str, dict[str, Any]] = {}
    legacy_influence_assets: set[str] = set()
    # Additive _cn companion (Program BIL / WS-9) — class_name here is a raw
    # asset_registry.asset_class value, i.e. a taxonomy_classes.name.
    name_cn_map = get_class_name_cn_map(db)

    for row in class_rows:
        class_name = row["asset_class"]
        class_map.setdefault(
            class_name,
            {
                "class_name": class_name,
                "class_name_cn": name_cn_map.get(class_name),
                "current_value": 0.0,
                "status": "healthy",
                "source_signal_summary": [],
                "open_case_count": 0,
            },
        )
        class_map[class_name]["current_value"] += float(row["total_value"] or 0.0)
        class_map[class_name]["source_signal_summary"].append(
            {
                "source_system": row["source_system"],
                "asset_count": int(row["asset_count"] or 0),
            }
        )

    for row in asset_flags:
        has_issue = bool(row["legacy_influence"]) or bool(row["value_issue"])
        if not has_issue:
            continue
        class_name = row["asset_class"]
        source_name = row["source_system"]
        class_map.setdefault(
            class_name,
            {
                "class_name": class_name,
                "class_name_cn": name_cn_map.get(class_name),
                "current_value": 0.0,
                "status": "healthy",
                "source_signal_summary": [],
                "open_case_count": 0,
            },
        )
        class_map[class_name]["open_case_count"] += 1
        class_map[class_name]["status"] = "warning"
        source_counts[source_name] = source_counts.get(source_name, 0) + 1
        if row["legacy_influence"]:
            legacy_influence_assets.add(row["asset_id"])

    if source_counts:
        source_strip = [{"source_system": key, "flagged_asset_count": value} for key, value in sorted(source_counts.items())]
    else:
        source_strip = [{"source_system": src, "flagged_asset_count": 0} for src in READER_SOURCES]

    check_source_recon = [value for key, value in checks.items() if "source_reconciliation" in key]
    global_health = [
        {
            "key": "net_worth_plausible",
            "label": "Net worth plausibility",
            "status": "ok" if checks.get("net_worth_plausible", True) else "warning",
        },
        {
            "key": "shadow_mutual_exclusion",
            "label": "Shadow rule health",
            "status": "ok" if checks.get("shadow_mutual_exclusion", True) else "warning",
        },
        {
            "key": "source_reconciliation",
            "label": "Source reconciliation presence",
            "status": "ok" if all(check_source_recon) or not check_source_recon else "warning",
        },
        {
            "key": "cost_basis_ratio_under_10x",
            "label": "Value / cost outlier presence",
            "status": "ok" if checks.get("cost_basis_ratio_under_10x", True) else "warning",
        },
    ]

    open_anomalies = (
        max(0, integrity_summary["total"] - integrity_summary["passed"])
        + reader_warnings
        + len(legacy_influence_assets)
    )

    return {
        "last_sync_timestamp": _to_iso(latest_report["created_at"]) if latest_report else None,
        "integrity": integrity_summary,
        "integrity_grouped": integrity_grouped,
        "open_anomalies": open_anomalies,
        "reader_warnings": reader_warnings,
        "legacy_influence_cases": len(legacy_influence_assets),
        "global_health": global_health,
        "asset_classes": sorted(class_map.values(), key=lambda row: row["current_value"], reverse=True),
        "source_strip": source_strip,
        "sync_changelog": sync_changelog,
        "source_reconciliation": source_reconciliation,
    }


@router.get("/asset-class-audit")
async def get_asset_class_audit(
    class_name: str = Query(..., alias="class"),
    db: DatabaseConnector = Depends(get_db),
):
    rows = _fetch_rows(
        db,
        f"""
        {_latest_active_cte()}
        SELECT
            a.asset_id,
            a.display_name,
            a.asset_class,
            a.source_system,
            a.snapshot_date,
            a.market_value,
            a.quantity,
            a.currency,
            EXISTS (
                SELECT 1
                FROM holdings h2
                WHERE h2.asset_id = a.asset_id
                  AND h2.source_system IN {LEGACY_SOURCES}
                  AND h2.is_shadow = FALSE
                  AND h2.snapshot_date = (
                      SELECT MAX(h3.snapshot_date)
                      FROM holdings h3
                      WHERE h3.asset_id = h2.asset_id
                        AND h3.is_shadow = FALSE
                  )
            ) AS legacy_influence,
            CASE
                WHEN a.asset_id LIKE 'Pension_%' OR a.asset_id LIKE 'Property_%'
                     OR a.asset_id LIKE 'CASH_%' THEN FALSE
                WHEN a.market_value IS NULL THEN TRUE
                WHEN a.market_value < 0 THEN TRUE
                ELSE FALSE
            END AS value_issue
        FROM active a
        WHERE a.asset_class = ?
        """,
        (class_name,),
    )

    # Additive _cn companion (Program BIL / WS-9).
    class_name_cn = get_class_name_cn_map(db).get(class_name)

    if not rows:
        return {
            "class_name": class_name,
            "class_name_cn": class_name_cn,
            "total_value": 0.0,
            "active_assets": 0,
            "open_cases": 0,
            "groups": [],
        }

    by_source: dict[str, list[dict[str, Any]]] = {}
    legacy_assets: dict[str, dict[str, Any]] = {}
    total_value = 0.0
    open_cases = 0

    for row in rows:
        total_value += float(row["market_value"] or 0.0)
        signal_parts = []
        if row["legacy_influence"]:
            signal_parts.append("legacy influence detected")
            legacy_assets[row["asset_id"]] = {
                "asset_id": row["asset_id"],
                "display_name": row["display_name"],
                "status": "review",
                "last_activity": _to_iso(row["snapshot_date"]),
                "primary_signal": "legacy interference",
                "market_value": float(row["market_value"] or 0.0),
                "quantity": float(row["quantity"] or 0.0) if row.get("quantity") is not None else None,
                "currency": str(row.get("currency") or "CNY"),
                "legacy_influence": True,
                "value_issue": bool(row["value_issue"]),
                "open_case_url": f"/asset-case-file?asset_id={row['asset_id']}",
            }
        if row["value_issue"]:
            signal_parts.append("value outlier")

        status = "warning" if signal_parts else "healthy"
        if status != "healthy":
            open_cases += 1

        by_source.setdefault(row["source_system"], []).append(
            {
                "asset_id": row["asset_id"],
                "display_name": row["display_name"],
                "status": status,
                "last_activity": _to_iso(row["snapshot_date"]),
                "primary_signal": ", ".join(signal_parts) if signal_parts else "no anomalies",
                "market_value": float(row["market_value"] or 0.0),
                "quantity": float(row["quantity"] or 0.0) if row.get("quantity") is not None else None,
                "currency": str(row.get("currency") or "CNY"),
                "legacy_influence": bool(row["legacy_influence"]),
                "value_issue": bool(row["value_issue"]),
                "open_case_url": f"/asset-case-file?asset_id={row['asset_id']}",
                "open_tx_url": f"/transactions?asset_id={row['asset_id']}",
                "open_run_url": "/import",
            }
        )

    groups = []
    for source_system, assets in sorted(by_source.items(), key=lambda item: item[0]):
        assets.sort(key=lambda item: (item["last_activity"] or "", item["status"] != "warning", item["asset_id"]), reverse=True)
        flagged = len([a for a in assets if a["status"] != "healthy"])
        groups.append(
            {
                "group_type": "reader_sources",
                "source_system": source_system,
                "status": "warning" if flagged else "healthy",
                "asset_count": len(assets),
                "flagged_asset_count": flagged,
                "latest_activity": assets[0]["last_activity"] if assets else None,
                "assets": assets,
            }
        )

    if legacy_assets:
        groups.append(
            {
                "group_type": "legacy_influence",
                "source_system": "Legacy(PIS)",
                "status": "review",
                "asset_count": len(legacy_assets),
                "flagged_asset_count": len(legacy_assets),
                "latest_activity": max(item["last_activity"] for item in legacy_assets.values()),
                "assets": list(sorted(legacy_assets.values(), key=lambda item: item["asset_id"])),
            }
        )

    return {
        "class_name": class_name,
        "class_name_cn": class_name_cn,
        "total_value": total_value,
        "active_assets": len(rows),
        "open_cases": open_cases,
        "groups": groups,
    }


@router.get("/asset-case-file")
async def get_asset_case_file(
    asset_id: str = Query(...),
    db: DatabaseConnector = Depends(get_db),
):
    holding_rows = _fetch_rows(
        db,
        """
        SELECT
            h.asset_id,
            COALESCE(r.display_name, h.asset_name, h.asset_id) AS display_name,
            COALESCE(r.asset_class, 'Unclassified') AS asset_class,
            h.source_system,
            h.snapshot_date,
            h.quantity,
            h.market_value,
            h.is_shadow
        FROM holdings h
        LEFT JOIN asset_registry r
          ON r.canonical_id = h.asset_id
        WHERE h.asset_id = ?
        ORDER BY h.snapshot_date DESC
        """,
        (asset_id,),
    )
    if not holding_rows:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")

    active_row = next((row for row in holding_rows if not row["is_shadow"]), holding_rows[0])
    competing_sources = sorted({row["source_system"] for row in holding_rows if row["source_system"] != active_row["source_system"]})
    latest_non_shadow_date = max(
        (row["snapshot_date"] for row in holding_rows if not row["is_shadow"]),
        default=None,
    )
    legacy_rows = [
        row
        for row in holding_rows
        if row["source_system"] in LEGACY_SOURCES
        and not row["is_shadow"]
        and row["snapshot_date"] == latest_non_shadow_date
    ]
    reader_latest: dict[str, Any] = {}
    for row in holding_rows:
        if row["source_system"] in READER_SOURCES:
            source_name = row["source_system"]
            snapshot_date = row["snapshot_date"]
            if source_name not in reader_latest or (snapshot_date and snapshot_date > reader_latest[source_name]):
                reader_latest[source_name] = snapshot_date

    reader_shadow_conflict = any(
        row["source_system"] in READER_SOURCES
        and bool(row["is_shadow"])
        and row["snapshot_date"] == reader_latest.get(row["source_system"])
        for row in holding_rows
    )

    signals: list[str] = []
    if reader_shadow_conflict:
        signals.append("Reader row incorrectly marked as shadow — source authority may be wrong")
    if legacy_rows:
        signals.append("Legacy transaction post-dates or overlaps reader snapshot — possible carry-over contamination")
    if not signals:
        signals.append("No anomalies detected — asset appears healthy")

    tx_rows = _fetch_rows(
        db,
        """
        SELECT transaction_date, transaction_type, source_system, amount_net, account, memo
        FROM transactions
        WHERE asset_id = ?
        ORDER BY transaction_date DESC, id DESC
        """,
        (asset_id,),
    )

    all_run_rows = _fetch_rows(
        db,
        """
        SELECT id, created_at, warnings, by_source_after
        FROM sync_audit_reports
        WHERE report_type = 'sync'
        ORDER BY created_at DESC
        LIMIT 20
        """,
    )
    active_source = active_row["source_system"]
    run_rows = [
        row
        for row in all_run_rows
        if active_source in (_loads_json(row.get("by_source_after"), {}) or {})
    ][:5] or all_run_rows[:3]

    trace_events: list[dict[str, Any]] = []
    for row in tx_rows:
        trace_events.append(
            {
                "timestamp": _to_iso(row["transaction_date"]),
                "evidence_type": "transaction",
                "source_system": row["source_system"],
                "description": f"{row['transaction_type']} ({row['amount_net']})",
            }
        )
    for row in holding_rows:
        trace_events.append(
            {
                "timestamp": _to_iso(row["snapshot_date"]),
                "evidence_type": "legacy snapshot" if row["source_system"] in LEGACY_SOURCES else "reader snapshot",
                "source_system": row["source_system"],
                "description": f"Snapshot qty={row['quantity']} mv={row['market_value']}",
            }
        )
    for row in run_rows:
        warnings = _loads_json(row["warnings"], [])
        trace_events.append(
            {
                "timestamp": _to_iso(row["created_at"]),
                "evidence_type": "sync warning" if warnings else "audit run",
                "source_system": "Sync",
                "description": f"Run {row['id']} with {len(warnings)} warning(s)",
            }
        )

    trace_events.sort(key=lambda item: item["timestamp"] or "", reverse=True)

    return {
        "asset_id": asset_id,
        "display_name": active_row["display_name"],
        "breadcrumb": {
            "portfolio": "Portfolio",
            "asset_class": active_row["asset_class"],
            "asset": asset_id,
        },
        "severity": "high" if reader_shadow_conflict else ("review" if legacy_rows else "healthy"),
        "current_state": {
            "active_source": active_row["source_system"],
            "active_shadow_status": bool(active_row["is_shadow"]),
            "current_quantity": float(active_row["quantity"] or 0),
            "current_market_value": float(active_row["market_value"] or 0),
            "last_snapshot_date": _to_iso(active_row["snapshot_date"]),
        },
        "authority_context": {
            "expected_authority_source": active_row["source_system"],
            "competing_sources": competing_sources,
            "legacy_influence_flag": bool(legacy_rows),
            "shadow_conflict_flag": reader_shadow_conflict,
        },
        "signals": signals,
        "source_trace": trace_events,
        "evidence_counts": {
            "transactions": len(tx_rows),
            "snapshots": len(holding_rows),
            "sync_runs": len(run_rows),
        },
        "quick_actions": {
            "transactions": f"/transactions?asset_id={asset_id}",
            "sync_history": "/import",
        },
    }


def _compute_integrity_status(
    integrity_checks_raw: Any,
    integrity_passed: Any,
    integrity_total: Any,
) -> tuple[str, int]:
    """Derive (integrity_status, blocking_failed) from a row's integrity data.

    integrity_status values:
      "ok"       — all checks passed
      "degraded" — only advisory failures (sync is usable)
      "failed"   — at least one blocking failure (sync output cannot be trusted)

    Falls back to conservative (blocking=True) for rows persisted before the
    blocking flag was added to the integrity_checks JSON.
    """
    checks = _loads_json(integrity_checks_raw, [])
    if checks:
        blocking_failed = sum(
            1 for c in checks
            if not c.get("passed", True) and c.get("blocking", True)
        )
        advisory_failed = sum(
            1 for c in checks
            if not c.get("passed", True) and not c.get("blocking", False)
        )
        if blocking_failed > 0:
            return "failed", blocking_failed
        if advisory_failed > 0:
            return "degraded", 0
        return "ok", 0
    # Legacy row: no integrity_checks JSON — fall back to passed<total as blocking.
    passed = int(integrity_passed or 0)
    total = int(integrity_total or 0)
    if total > 0 and passed < total:
        return "failed", total - passed
    return "ok", 0


_PHASE_NAME_BY_ID = {spec.phase_id: spec.name for spec in PIPELINE_MANIFEST}
_PHASE_STEP_RE = re.compile(r"^P\d$")  # single digit per contract (P0..P8)

# Source display names for the freshness panel (spec: operations-pipeline.md)
# Derived from registry — same name, same value, single source of truth.
_SOURCE_DISPLAY_NAMES: dict = get_registry().source_display_names()


def _map_pipeline_steps(steps_raw: Any) -> List[Dict[str, Any]] | None:
    """Map persisted StepResult dicts to the spec's PipelineStepResult shape.

    Keeps only phase-level entries (name "P0".."P8"); finer-grained steps
    recorded inside phases (e.g. live_price_refresh) are excluded here.
    Returns None for legacy runs persisted before the steps column existed.
    """
    steps = _loads_json(steps_raw, None)
    if steps is None:
        return None
    mapped = []
    for s in steps:
        name = str(s.get("name", ""))
        if not _PHASE_STEP_RE.match(name):
            continue
        mapped.append({
            "phase_id": name,
            "name": _PHASE_NAME_BY_ID.get(name, name),
            "status": "ok" if s.get("status") == "ok" else "failed",
            "duration_ms": int(s.get("duration_ms") or 0),
            "error": s.get("error"),
        })
    return mapped


def _staleness_bucket(age_days: int) -> str:
    if age_days <= 14:
        return "fresh"
    if age_days <= 45:
        return "aging"
    return "stale"


@router.get("/pipeline")
async def get_pipeline_status(db: DatabaseConnector = Depends(get_db)):
    """Pipeline topology + last sync run + per-source freshness.

    Contract: docs/api-specs/operations-pipeline.md (PipelineStatusResponse).
    """
    phases = [
        {
            "phase_id": spec.phase_id,
            "name": spec.name,
            "description": spec.description,
            "tables_read": list(spec.tables_read),
            "tables_written": list(spec.tables_written),
        }
        for spec in PIPELINE_MANIFEST
    ]

    last_run = None
    row = _fetch_one(
        db,
        """
        SELECT id, created_at, net_worth_after, net_worth_change_pct,
               integrity_passed, integrity_total, integrity_checks,
               warnings, alert, is_no_change, steps
        FROM sync_audit_reports
        WHERE report_type = 'sync'
        ORDER BY created_at DESC
        LIMIT 1
        """,
    )
    if row:
        integrity_status, _blocking = _compute_integrity_status(
            row["integrity_checks"], row["integrity_passed"], row["integrity_total"]
        )
        last_run = {
            "id": row["id"],
            "timestamp": _to_iso(row["created_at"]),
            "integrity_result": f"{int(row['integrity_passed'] or 0)}/{int(row['integrity_total'] or 0)}",
            "integrity_status": integrity_status,
            "net_worth_after": float(row["net_worth_after"]) if row["net_worth_after"] is not None else None,
            "net_worth_change_pct": float(row["net_worth_change_pct"]) if row["net_worth_change_pct"] is not None else None,
            "warning_count": len(_loads_json(row["warnings"], [])),
            "alert": bool(row["alert"]),
            "is_no_change": bool(row["is_no_change"]) if row["is_no_change"] is not None else False,
            "steps": _map_pipeline_steps(row["steps"]),
        }

    # Per-source freshness over each asset's latest active snapshot —
    # per-asset MAX within source, never a global MAX (AGENTS.md Rule 3).
    freshness_rows = _fetch_rows(
        db,
        """
        WITH latest AS (
            SELECT asset_id, source_system, MAX(snapshot_date) AS max_date
            FROM holdings
            WHERE is_shadow = FALSE
            GROUP BY asset_id, source_system
        )
        SELECT h.source_system,
               COUNT(*) AS active_assets,
               MAX(h.snapshot_date) AS latest_snapshot,
               SUM(h.market_value) AS total_value_cny,
               MAX(h.price_updated_at) AS last_price_refresh,
               SUM(CASE WHEN h.price_updated_at IS NOT NULL THEN 1 ELSE 0 END) AS price_refreshed_assets
        FROM holdings h
        JOIN latest l
          ON h.asset_id = l.asset_id
         AND h.source_system = l.source_system
         AND h.snapshot_date = l.max_date
        WHERE h.is_shadow = FALSE
        GROUP BY h.source_system
        ORDER BY latest_snapshot DESC
        """,
    )
    today = date.today()
    sources = []
    for r in freshness_rows:
        snap = r["latest_snapshot"]
        snap_d = snap.date() if hasattr(snap, "date") else snap
        age = (today - snap_d).days if snap_d else 0
        sources.append({
            "source_system": r["source_system"],
            "display_name": _SOURCE_DISPLAY_NAMES.get(r["source_system"], r["source_system"]),
            "active_assets": int(r["active_assets"] or 0),
            "latest_snapshot": str(snap_d) if snap_d else "",
            "snapshot_age_days": age,
            "total_value_cny": float(r["total_value_cny"] or 0.0),
            "last_price_refresh": _to_iso(r["last_price_refresh"]),
            "price_refreshed_assets": int(r["price_refreshed_assets"] or 0),
            "staleness": _staleness_bucket(age),
        })

    return {
        "phases": phases,
        "last_run": last_run,
        "sources": sources,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


@router.get("/sync-history")
async def get_sync_history(
    limit: int = Query(default=20, ge=1, le=200),
    filter: str = Query(default="all", pattern="^(all|meaningful|no_change)$"),
    db: DatabaseConnector = Depends(get_db),
):
    # Fetch a larger pool when filtering by "meaningful" so we can apply the
    # blocking-failure filter in Python (avoids complex JSON SQL).
    fetch_limit = limit * 5 if filter == "meaningful" else limit

    rows = _fetch_rows(
        db,
        f"""
        SELECT
            id, created_at, report_type, net_worth_change_pct,
            integrity_passed, integrity_total, integrity_checks,
            by_source_after, warnings, alert, is_no_change
        FROM sync_audit_reports
        WHERE report_type = 'sync'
        {"AND is_no_change = TRUE" if filter == "no_change" else ""}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (fetch_limit,),
    )
    runs = []
    for row in rows:
        integrity_status, blocking_failed = _compute_integrity_status(
            row["integrity_checks"], row["integrity_passed"], row["integrity_total"]
        )
        by_source_after = _loads_json(row["by_source_after"], {})
        warnings = _loads_json(row["warnings"], [])

        if filter == "meaningful":
            # A run is meaningful if it has a real delta (>0.01%), a *blocking*
            # integrity failure, or an alert.  Advisory-only failures (degraded)
            # are informational — they do not qualify as a "failed" meaningful run.
            is_meaningful = (
                abs(float(row["net_worth_change_pct"] or 0.0)) > 0.01
                or integrity_status == "failed"
                or bool(row["alert"])
            )
            if not is_meaningful:
                continue

        runs.append(
            {
                "id": row["id"],
                "timestamp": _to_iso(row["created_at"]),
                "type": row["report_type"],
                "net_worth_delta": float(row["net_worth_change_pct"] or 0.0),
                "integrity_result": f"{int(row['integrity_passed'] or 0)}/{int(row['integrity_total'] or 0)}",
                "integrity_status": integrity_status,
                "blocking_failed": blocking_failed,
                "warning_count": len(warnings),
                "sources_affected": sorted(list(by_source_after.keys())) if isinstance(by_source_after, dict) else [],
                "alert": bool(row["alert"]),
                "is_no_change": bool(row["is_no_change"]) if row["is_no_change"] is not None else False,
            }
        )
        if len(runs) >= limit:
            break
    return {"runs": runs}


@router.get("/sync-history/{run_id}")
async def get_sync_history_detail(run_id: str, db: DatabaseConnector = Depends(get_db)):
    row = _fetch_one(
        db,
        """
        SELECT
            id, created_at, report_type, net_worth_before, net_worth_after, net_worth_change_pct,
            integrity_passed, integrity_total, by_source_before, by_source_after, reader_counts, warnings,
            integrity_checks, alert, is_no_change, info_messages, steps
        FROM sync_audit_reports
        WHERE id = ?
        """,
        (run_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Sync run not found: {run_id}")

    integrity_status, blocking_failed = _compute_integrity_status(
        row["integrity_checks"], row["integrity_passed"], row["integrity_total"]
    )

    return {
        "id": row["id"],
        "timestamp": _to_iso(row["created_at"]),
        "type": row["report_type"],
        "net_worth_before": float(row["net_worth_before"] or 0.0),
        "net_worth_after": float(row["net_worth_after"] or 0.0),
        "net_worth_delta": float(row["net_worth_change_pct"] or 0.0),
        "integrity_result": f"{int(row['integrity_passed'] or 0)}/{int(row['integrity_total'] or 0)}",
        "integrity_status": integrity_status,
        "blocking_failed": blocking_failed,
        "by_source_before": _loads_json(row["by_source_before"], {}),
        "by_source_after": _loads_json(row["by_source_after"], {}),
        "reader_counts": _loads_json(row["reader_counts"], {}),
        "warnings": _loads_json(row["warnings"], []),
        "info_messages": _loads_json(row["info_messages"], []),
        "integrity_checks": _loads_json(row["integrity_checks"], []),
        "alert": bool(row["alert"]),
        "is_no_change": bool(row["is_no_change"]) if row["is_no_change"] is not None else False,
        "steps": _map_pipeline_steps(row["steps"]),
    }
