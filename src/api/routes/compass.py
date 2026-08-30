from fastapi import APIRouter, Depends, Query
from src.api.dependencies import get_db
from src.api.routes._errors import api_error_response
from src.database.connector import DatabaseConnector
from src.services.compass_allocation import build_compass_allocation, get_display_name
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)

# CHANGE 1: Explicit prefix
router = APIRouter(prefix="/compass", tags=["Compass"])

def _extract_last_sync_metadata(db: DatabaseConnector) -> tuple[str, str]:
    """Resolve last sync date/source from sync_audit_reports with safe fallbacks."""
    last_sync_date = None
    last_sync_source = "Unknown"

    try:
        latest_run = db.execute(
            """
            SELECT created_at, by_source_after
            FROM sync_audit_reports
            WHERE report_type = 'sync'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    except Exception:
        latest_run = None

    if latest_run:
        created_at = latest_run[0]
        if hasattr(created_at, "strftime"):
            last_sync_date = created_at.strftime("%Y-%m-%d")
        else:
            last_sync_date = str(created_at)[:10]

        raw_by_source_after = latest_run[1]
        by_source_after = {}
        if isinstance(raw_by_source_after, dict):
            by_source_after = raw_by_source_after
        elif isinstance(raw_by_source_after, str):
            try:
                parsed = json.loads(raw_by_source_after)
                if isinstance(parsed, dict):
                    by_source_after = parsed
            except json.JSONDecodeError:
                by_source_after = {}

        source_names = sorted([str(k) for k in by_source_after.keys() if k])
        if source_names:
            last_sync_source = ", ".join(source_names)

    if not last_sync_date:
        fallback_date_row = db.execute(
            """
            SELECT MAX(snapshot_date)
            FROM holdings
            WHERE is_shadow = FALSE
            """
        ).fetchone()
        fallback_date = fallback_date_row[0] if fallback_date_row else None
        if fallback_date and hasattr(fallback_date, "strftime"):
            last_sync_date = fallback_date.strftime("%Y-%m-%d")
        elif fallback_date:
            last_sync_date = str(fallback_date)
        else:
            last_sync_date = datetime.now().strftime("%Y-%m-%d")

    if last_sync_source == "Unknown":
        source_rows = db.execute(
            """
            SELECT DISTINCT source_system
            FROM holdings
            WHERE is_shadow = FALSE AND source_system IS NOT NULL
            ORDER BY source_system
            """
        ).fetchall()
        fallback_sources = [row[0] for row in source_rows if row[0]]
        if fallback_sources:
            last_sync_source = ", ".join(fallback_sources)

    return last_sync_date, last_sync_source

@router.get("/summary")
async def get_compass_summary(
    include_non_rebalanceable: bool = Query(default=False),
    db: DatabaseConnector = Depends(get_db)
):
    """Get KPI metrics with dynamic drift calculation."""
    try:
        # 1. Fetch Targets from the active risk profile (new taxonomy system)
        targets = {}
        try:
            active_allocs = db.execute("""
                SELECT tc.name, rpa.target_pct
                FROM risk_profile_allocations rpa
                JOIN taxonomy_classes tc ON rpa.class_id = tc.id
                JOIN risk_profiles rp ON rpa.profile_id = rp.id
                WHERE rp.is_active = TRUE
            """).fetchall()
            for row in active_allocs:
                targets[row[0]] = {"target": float(row[1]), "tolerance": 5.0}
        except Exception as e:
            print(f"Warning: could not load active profile targets: {e}")

        # 2. Fetch Current Value using taxonomy_classes hierarchy
        # holdings -> asset_registry -> taxonomy_classes (sub-class) -> taxonomy_classes (parent)
        query_improved = """
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) as latest_date
                FROM holdings WHERE is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT
                h.asset_id,
                COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified') as effective_top_class,
                SUM(h.market_value) as val
            FROM holdings h
            JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
            LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
            LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
            WHERE h.is_shadow = FALSE
            GROUP BY 1, 2
        """

        current_rows = db.execute(query_improved).fetchall()
        
        from src.services.rebalanceable_filter import fetch_non_rebalanceable_asset_ids
        excluded_ids = set()
        non_rebalanceable_classes = set()
        if not include_non_rebalanceable:
            excluded_ids = fetch_non_rebalanceable_asset_ids(db)
            try:
                rows = db.execute("SELECT name FROM taxonomy_classes WHERE is_rebalanceable = FALSE").fetchall()
                non_rebalanceable_classes = {r[0] for r in rows if r[0]}
                non_rebalanceable_classes.update({get_display_name(c) for c in non_rebalanceable_classes})
            except Exception:
                pass
        
        total_net_worth = 0.0
        current_map = {}
        for row in current_rows:
            aid = row[0]
            cls = row[1]
            if not include_non_rebalanceable and aid in excluded_ids: 
                continue
                
            val = float(row[2] if row[2] else 0.0)
            current_map[cls] = current_map.get(cls, 0.0) + val
            total_net_worth += val

        # 3. Aggregate sub-class targets to top-level for drift comparison
        # current_map is at top-class level; targets are at sub-class level — must aggregate
        try:
            tc_rows = db.execute("""
                SELECT tc.name, parent.name
                FROM taxonomy_classes tc
                JOIN taxonomy_classes parent ON tc.parent_id = parent.id
                WHERE tc.level > 0
            """).fetchall()
            sub_to_parent = {r[0]: r[1] for r in tc_rows}
        except Exception:
            sub_to_parent = {}

        parent_targets = {}
        for sub_key, sub_info in targets.items():
            parent = sub_to_parent.get(sub_key, sub_key)
            if parent not in parent_targets:
                parent_targets[parent] = {"target": 0.0, "tolerance": 5.0}
            parent_targets[parent]["target"] += sub_info["target"]
        targets = parent_targets

        # Filter targets to exclude non-rebalanceable classes
        classes_in_drift = 0
        effective_targets = {
            k:v for k,v in targets.items()
            if k not in non_rebalanceable_classes and get_display_name(k) not in non_rebalanceable_classes
        }
        
        weighted_drift_sum = 0.0
        
        all_classes = set(current_map.keys()) | set(effective_targets.keys())
        
        for cls in all_classes:
            target_info = effective_targets.get(cls, {"target": 0.0, "tolerance": 5.0})
            curr_val = current_map.get(cls, 0.0)
            curr_pct = (curr_val / total_net_worth * 100.0) if total_net_worth > 0 else 0.0
            
            drift_pct = curr_pct - target_info['target']
            
            if abs(drift_pct) > target_info['tolerance']:
                classes_in_drift += 1
                
            if target_info['target'] > 0:
                 weighted_drift_sum += abs(drift_pct) * (target_info['target'] / 100.0)
            else:
                 weighted_drift_sum += abs(drift_pct) * 0.1 
        
        last_sync_date, last_sync_source = _extract_last_sync_metadata(db)

        return {
            "total_net_worth": round(total_net_worth, 2),
            "drift_index": round(weighted_drift_sum, 2),
            "classes_in_drift": classes_in_drift,
            "total_classes": len(all_classes),
            "last_sync_date": last_sync_date,
            "last_sync_source": last_sync_source
        }
    except Exception as e:
        logger.exception("get_compass_summary failed")
        return api_error_response(e, context="get_compass_summary")

@router.get("/allocation")
async def get_compass_allocation(
    include_non_rebalanceable: bool = Query(default=False),
    include_pending: bool = Query(default=False, description="Overlay provisional allocation from AI Advisor pending trades"),
    db: DatabaseConnector = Depends(get_db)
):
    """Get hierarchy allocation using Dynamic DB Joins.

    When include_pending=false (default) the response is a plain list of AllocationRow objects
    — byte-for-byte identical to prior behavior.

    When include_pending=true the response is an envelope:
      {"allocation": [...], "meta": {"pending_trade_count": N, "is_provisional": true}}
    Each AllocationRow gains three provisional fields: provisional_value, provisional_pct,
    provisional_delta_cny. Stored data is unchanged (read-only overlay).
    """
    try:
        return build_compass_allocation(
            db,
            include_non_rebalanceable=include_non_rebalanceable,
            include_pending=include_pending,
        )
    except Exception as e:
        logger.exception("get_compass_allocation failed")
        return api_error_response(e, context="get_compass_allocation")

def format_signed_pct(val):
    return f"{'+' if val > 0 else ''}{val:.2f}%"

@router.get("/markdown")
async def get_compass_markdown(db: DatabaseConnector = Depends(get_db)):
    """Generate Markdown tables."""
    allocation = await get_compass_allocation(include_non_rebalanceable=False, include_pending=False, db=db)
    
    top_md = "| Asset Class | Current % | Target % | Drift | Status |\n|-------------|-----------|----------|-------|--------|\n"
    sub_md = "| Sub-Class | Parent | Current % | Target % | Drift |\n|-----------|--------|-----------|----------|-------|\n"
    
    for r in allocation:
        status_icon = "✓ OK"
        if r['status'] == 'over':
            status_icon = "⚠️ Over"
        if r['status'] == 'under':
            status_icon = "⬇️ Under"
        
        drift_str = format_signed_pct(r['drift_pct'])
        
        if r['is_top_level']:
            top_md += f"| {r['asset_class']} | {r['current_pct']:.2f}% | {r['target_pct']:.2f}% | {drift_str} | {status_icon} |\n"
        else:
            parent_short = r['parent_class'].split(' (')[0]
            sub_md += f"| {r['asset_class']} | {parent_short} | {r['current_pct']:.2f}% | {r['target_pct']:.2f}% | {drift_str} |\n"
            
    return {
        "top_level_table": top_md.strip(),
        "sub_class_table": sub_md.strip(),
        "generated_at": datetime.now().isoformat()
    }
