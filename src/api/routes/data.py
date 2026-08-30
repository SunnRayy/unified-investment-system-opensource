from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query
from src.api.dependencies import get_db
from src.api.routes._errors import api_error_response
from src.database.connector import DatabaseConnector
from src.config import load_config
from src.financial_analysis.risk_calculator import calculate_portfolio_risk
from src.financial_analysis.metrics import calculate_portfolio_metrics
from src.financial_analysis.xirr import calculate_portfolio_xirr
from src.sources.registry import get_registry
import logging
import numpy as np
import pandas as pd
import sqlite3
import os
import math

# Derived from registry — reader sources eligible for sold-position close.
# Excludes Financial_Summary_Excel (historical, category != "reader").
_SOLD_CLOSE_CANDIDATE_SOURCES: frozenset = frozenset(
    get_registry().holding_source_systems()
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Data"])

CORR_WINDOW_DAYS = 180
CORR_MIN_OVERLAP_PERIODS = 8
CORR_JUMP_K = 6.0
CORR_JUMP_ABS_FLOOR = 0.30
CORR_JUMP_ABS_HARD_CAP = 2.0
CORR_WINSOR_P_LOW = 0.05
CORR_WINSOR_P_HIGH = 0.95
CORR_LOW_CONFIDENCE_MARGIN = 3

def _db_exists(path: str) -> bool:
    return os.path.exists(path)


def _normalize_period(period: str) -> str:
    period_key = (period or "all_time").strip().lower()
    aliases = {
        "all": "all_time",
        "all_time": "all_time",
        "36m": "last_36m",
        "last_36m": "last_36m",
        "12m": "last_12m",
        "last_12m": "last_12m",
    }
    return aliases.get(period_key, "all_time")


def _period_start_date(period: str):
    normalized = _normalize_period(period)
    today = date.today()
    if normalized == "last_36m":
        return (today - timedelta(days=365 * 3)).isoformat()
    if normalized == "last_12m":
        return (today - timedelta(days=365)).isoformat()
    return None


from src.services.rebalanceable_filter import adjust_balance_sheet_payload

def _balance_sheet_non_balanceable_adjustment(payload: dict) -> float:
    return adjust_balance_sheet_payload(payload)

@router.get("/dashboard/kpi")
async def get_dashboard_kpi(
    include_non_rebalanceable: bool = Query(default=False),
    db: DatabaseConnector = Depends(get_db)
):
    """Get Key Performance Indicators for the dashboard."""
    try:
        from src.services.rebalanceable_filter import fetch_non_rebalanceable_asset_ids
        excluded_ids = set()
        if not include_non_rebalanceable:
            excluded_ids = fetch_non_rebalanceable_asset_ids(db)

        # Net Worth via latest-per-asset CTE (handles mixed snapshot_dates across sources)
        # pnl_24h set to None — "previous" is ambiguous with mixed-date snapshots
        nw_query = """
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) as latest_date
                FROM holdings WHERE is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT 
                SUM(h.market_value) as total_val,
                SUM(CASE 
                    WHEN r.asset_class LIKE '%Cash%' 
                      OR r.asset_class LIKE '%现金%' 
                      OR r.asset_class LIKE '%货币%' 
                      OR r.asset_class LIKE '%Money Market%' 
                      OR r.asset_class LIKE '%Deposit%' 
                      OR r.asset_class LIKE '%活期%'
                      OR r.asset_class LIKE '%货基%'
                    THEN h.market_value 
                    ELSE 0 
                END) as cash_val
            FROM holdings h
            JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
            WHERE h.is_shadow = FALSE
        """
        if excluded_ids:
            placeholders = ", ".join(["?"] * len(excluded_ids))
            nw_query += f" AND h.asset_id NOT IN ({placeholders})"
            nw_row = db.execute(nw_query, list(excluded_ids)).fetchone()
        else:
            nw_row = db.execute(nw_query).fetchone()

        net_worth = float(nw_row[0] or 0.0) if nw_row else 0.0
        cash_available = float(nw_row[1] or 0.0) if nw_row and len(nw_row) > 1 else 0.0
        pnl_24h = None  # Ambiguous with mixed snapshot dates — known limitation

        # Market Pulse from DSA
        market_pulse = None
        market_pulse_sentiment = None
        market_pulse_source = "unavailable"
        
        try:
            config = load_config()
            dsa_path = config.get('subsystems', {}).get('daily_stock_analysis', {}).get('path')
            market_db = config.get('subsystems', {}).get('daily_stock_analysis', {}).get('data_sources', {}).get('market_db')
            
            if dsa_path and market_db:
                full_path = f"{dsa_path}/{market_db}"
                if _db_exists(full_path):
                    conn = sqlite3.connect(full_path)
                    try:
                        # Query for benchmark (000300 CSI 300)
                        cursor = conn.execute("""
                            SELECT date, close FROM stock_daily
                            WHERE code = '000300'
                            ORDER BY date DESC LIMIT 2
                        """)
                        rows = cursor.fetchall()
                        if len(rows) >= 2:
                            latest_close = rows[0][1]
                            prev_close = rows[1][1]
                            change_pct = (latest_close - prev_close) / prev_close * 100
                            
                            market_pulse = round(change_pct, 2)
                            market_pulse_source = "DSA (CSI 300)"
                            
                            if change_pct > 1.0:
                                market_pulse_sentiment = "Bullish"
                            elif change_pct < -1.0:
                                market_pulse_sentiment = "Bearish"
                            else:
                                market_pulse_sentiment = "Neutral"
                    finally:
                        conn.close()
        except Exception as e:
            print(f"DSA Pulse Error: {e}")

        # Fallback: use Fear & Greed from market_sentiment_cache if DSA unavailable
        if market_pulse is None:
            try:
                fg_result = db.execute("""
                    SELECT value, zone FROM market_sentiment_cache
                    WHERE indicator_key = 'fear_greed'
                """).fetchone()
                if fg_result and fg_result[0] is not None:
                    market_pulse = round(float(fg_result[0]), 1)
                    market_pulse_source = "Fear & Greed"
                    zone = fg_result[1] or ''
                    market_pulse_sentiment = zone if zone else "N/A"
            except Exception:
                pass

        # Macro indicators from sentiment cache (for Dashboard cards)
        macro_indicators = {}
        for key in ("vix", "brent_crude", "us10y"):
            try:
                row = db.execute(
                    """
                    SELECT value, display_value, zone, zone_color
                    FROM market_sentiment_cache
                    WHERE indicator_key = ?
                    """,
                    [key]
                ).fetchone()
                if row:
                    macro_indicators[key] = {
                        "value": float(row[0]) if row[0] is not None else None,
                        "display_value": row[1],
                        "zone": row[2],
                        "zone_color": row[3]
                    }
                else:
                    macro_indicators[key] = None
            except Exception:
                macro_indicators[key] = None

        return {
            "net_worth": net_worth,
            "cash_available": cash_available,
            "pnl_24h": pnl_24h,
            "market_pulse": market_pulse,
            "market_pulse_source": market_pulse_source,
            "market_pulse_sentiment": market_pulse_sentiment,
            "vix": macro_indicators.get("vix"),
            "brent_crude": macro_indicators.get("brent_crude"),
            "us10y": macro_indicators.get("us10y")
        }
    except Exception:
        return {
            "net_worth": 0,
            "cash_available": 0,
            "pnl_24h": None,
            "market_pulse": None,
            "market_pulse_source": "unavailable",
            "market_pulse_sentiment": None,
            "vix": None,
            "brent_crude": None,
            "us10y": None
        }


@router.get("/audit/logs")
async def get_audit_logs(limit: int = 50, db: DatabaseConnector = Depends(get_db)):
    """Get recent audit logs."""
    try:
        # .df() rather than fetchall(): it carries column names, so no separate
        # cursor-description pass is needed to build the dicts below.
        df = db.execute(f"SELECT * FROM sync_audit_logs ORDER BY sync_timestamp DESC LIMIT {limit}").df()
        # Replace NaN with None so JSON serialization works
        # DataFrames containing missing values in int columns convert to float with NaN
        import numpy as np
        return df.replace({np.nan: None}).to_dict(orient='records')
    except Exception as e:
        logger.exception("get_audit_logs failed")
        return api_error_response(e, context="get_audit_logs")


@router.get("/audit/summary")
async def get_audit_summary(db: DatabaseConnector = Depends(get_db)):
    """Get audit summary statistics."""
    try:
        # Total logs
        total_result = db.execute("SELECT COUNT(*) FROM sync_audit_logs").fetchone()
        total_logs = total_result[0] if total_result else 0
        
        # Last sync timestamp
        last_sync_result = db.execute(
            "SELECT MAX(sync_timestamp) FROM sync_audit_logs"
        ).fetchone()
        ts = last_sync_result[0] if last_sync_result else None
        last_sync_timestamp = ts.isoformat() if hasattr(ts, 'isoformat') else str(ts) if ts else None
        
        # Unresolved conflicts
        unresolved_result = db.execute(
            "SELECT COUNT(*) FROM sync_audit_logs WHERE is_resolved = FALSE"
        ).fetchone()
        unresolved_conflicts = unresolved_result[0] if unresolved_result else 0

        return {
            "total_logs": total_logs,
            "last_sync_timestamp": last_sync_timestamp,
            "unresolved_conflicts": unresolved_conflicts,
        }
    except Exception:
        return {
            "total_logs": 0,
            "last_sync_timestamp": None,
            "unresolved_conflicts": 0,
        }

@router.get("/insights")
async def get_insights(db: DatabaseConnector = Depends(get_db)):
    """Get latest insights."""
    try:
        insights = db.execute("""
            SELECT i.title, i.content, i.created_at as generated_at, i.insight_type as type 
            FROM insights i
            ORDER BY i.created_at DESC 
            LIMIT 5
        """).df()
        
        # Fix for timestamps not being JSON serializable
        # Convert timestamp columns to ISO format string
        if 'generated_at' in insights.columns:
            insights['generated_at'] = insights['generated_at'].apply(lambda x: x.isoformat() if hasattr(x, 'isoformat') else str(x))
            
        return insights.to_dict(orient='records')
    except Exception as e:
        logger.exception("get_insights failed")
        return api_error_response(e, context="get_insights")

def map_class(cls_name):
    """Map asset class names (English or Chinese) to Chinese category names."""
    if not cls_name: return "另类投资"
    c = cls_name.upper()
    
    # English mappings
    if "EQUITY" in c or "STOCK" in c or "ETF" in c: return "股票"
    if "BOND" in c or "FIXED" in c or "DEBT" in c: return "固定收益"
    if "CASH" in c or "DEPOSIT" in c or "MONEY" in c: return "现金"
    if "GOLD" in c or "COMMODITY" in c: return "商品"
    if "REIT" in c or "ESTATE" in c or "PROPERTY" in c: return "房地产"
    
    # Chinese mappings (exact keyword matches from asset_registry)
    if "股票" in cls_name or "权益" in cls_name: return "股票"
    if "债券" in cls_name or "固定收益" in cls_name: return "固定收益"
    if "现金" in cls_name or "货币" in cls_name: return "现金"
    if "商品" in cls_name or "黄金" in cls_name: return "商品"
    if "地产" in cls_name or "房产" in cls_name: return "房地产"
    
    return "另类投资"

@router.get("/dashboard/allocation")
async def get_dashboard_allocation(
    include_non_rebalanceable: bool = Query(default=False),
    db: DatabaseConnector = Depends(get_db)
):
    """Get current asset allocation breakdown."""
    try:
        # Prefer current_allocations if populated and valid
        query_alloc = """
            SELECT asset_class, sum(market_value) as value 
            FROM current_allocations 
            WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM current_allocations)
            GROUP BY asset_class
        """
        result = db.execute(query_alloc).fetchall()
        
        # If current_allocations is empty or has very few rows (e.g. just 1 row), fallback to holdings aggregation
        if not result or len(result) < 2:
            from src.services.rebalanceable_filter import fetch_non_rebalanceable_asset_ids
            excluded_ids = set()
            if not include_non_rebalanceable:
                excluded_ids = fetch_non_rebalanceable_asset_ids(db)
            
            query_holdings = """
                WITH latest_per_asset AS (
                    SELECT asset_id, MAX(snapshot_date) as latest_date
                    FROM holdings WHERE is_shadow = FALSE
                    GROUP BY asset_id
                )
                SELECT
                    r.asset_class,
                    SUM(h.market_value) as value
                FROM holdings h
                JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
                LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
                WHERE h.is_shadow = FALSE
            """
            if excluded_ids:
                placeholders = ", ".join(["?"] * len(excluded_ids))
                query_holdings += f" AND h.asset_id NOT IN ({placeholders})"
                query_holdings += " GROUP BY 1"
                raw_data = db.execute(query_holdings, list(excluded_ids)).fetchall()
            else:
                query_holdings += " GROUP BY 1"
                raw_data = db.execute(query_holdings).fetchall()
            
            # Aggregate by mapped class
            mapped_data = {}
            for row in raw_data:
                # row[0] is english class from registry, row[1] is value
                cat = map_class(row[0])
                if not include_non_rebalanceable and cat in ["房地产", "保险", "Real Estate", "Insurance"]:
                    continue
                mapped_data[cat] = mapped_data.get(cat, 0.0) + (row[1] if row[1] else 0.0)
            
            return [{"name": k, "value": v} for k, v in mapped_data.items()]
            
        final_allocs = []
        for row in result:
            cat = map_class(row[0])
            if not include_non_rebalanceable and cat in ["房地产", "保险", "Real Estate", "Insurance"]:
                continue
            final_allocs.append({"name": row[0], "value": float(row[1])})
        return final_allocs
    except Exception as e:
        logger.exception("get_dashboard_allocation failed")
        return api_error_response(e, context="get_dashboard_allocation")

@router.get("/compass/report")
async def get_compass_report(db: DatabaseConnector = Depends(get_db)):
    """Get Compass report data: Allocation vs Target, Drift."""
    try:
        # 1. Get Targets (Chinese classes)
        targets_df = db.execute("SELECT asset_class, target_pct, tolerance_pct FROM target_allocations WHERE effective_date <= CURRENT_DATE ORDER BY effective_date DESC").df()
        targets = {}
        if not targets_df.empty:
            for _, row in targets_df.iterrows():
                if row['asset_class'] not in targets:
                    targets[row['asset_class']] = {"target": float(row['target_pct']), "tolerance": float(row['tolerance_pct'])}
        
        # 2. Get Current Market Value per Class (Map to Chinese)
        current_data = db.execute("""
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) as latest_date
                FROM holdings WHERE is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT
                r.asset_class,
                SUM(h.market_value) as val
            FROM holdings h
            JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
            WHERE h.is_shadow = FALSE
            GROUP BY 1
        """).fetchall()
        
        mapped_current = {}
        total_mv = 0.0
        
        for row in current_data:
            cat = map_class(row[0])
            val = float(row[1]) if row[1] else 0.0
            mapped_current[cat] = mapped_current.get(cat, 0.0) + val
            total_mv += val
            
        report = []
        all_classes = set(targets.keys()) | set(mapped_current.keys())
        
        for cls in all_classes:
            target_info = targets.get(cls, {"target": 0.0, "tolerance": 5.0})
            current_val = mapped_current.get(cls, 0.0)
            
            current_pct = (current_val / total_mv * 100) if total_mv > 0 else 0.0
            drift_pct = current_pct - target_info['target']
            
            status = "Within Range"
            if abs(drift_pct) > target_info['tolerance']:
                status = f"Drift > {target_info['tolerance']}%"
                
            report.append({
                "asset_class": cls,
                "current_value": current_val,
                "current_pct": round(current_pct, 2),
                "target_pct": float(target_info['target']),
                "drift_pct": round(drift_pct, 2),
                "status": status,
                "tolerance": float(target_info['tolerance'])
            })
            
        return report
    except Exception as e:
        logger.exception("get_compass_report failed")
        return api_error_response(e, context="get_compass_report")

@router.get("/wealthos/summary")
async def get_wealthos_summary(
    include_non_rebalanceable: bool = Query(default=False),
    db: DatabaseConnector = Depends(get_db)
):
    """Get WealthOS summary statistics."""
    try:
        from src.api.routes.performance import get_performance_summary, PERIOD_ALL_TIME

        # Keep WealthOS lifetime metrics aligned with Performance report semantics.
        # Must pass period/exclude_non_balanceable explicitly — Query() defaults are FieldInfo
        # objects (FastAPI injection only), not actual values when called directly from Python.
        perf_summary = await get_performance_summary(
            period=PERIOD_ALL_TIME,
            exclude_non_balanceable=False,
            include_non_rebalanceable=include_non_rebalanceable,
            db=db
        )
        total_cost_basis = float(perf_summary.get("total_cost_basis") or 0.0)
        total_lifetime_gain = float(perf_summary.get("total_lifetime_pl") or 0.0)
        lifetime_gain_pct = (
            (total_lifetime_gain / total_cost_basis) * 100
            if total_cost_basis != 0
            else 0.0
        )
        active_asset_count = int(perf_summary.get("asset_count") or 0)
        
        # Total asset count (all time)
        total_query = "SELECT COUNT(DISTINCT asset_id) FROM transactions"
        total_result = db.execute(total_query).fetchone()
        total_asset_count = total_result[0] if total_result else 0
        
        # Annualized return (XIRR)
        from src.api.routes.performance import fetch_included_asset_ids
        
        exclude = False
        if isinstance(include_non_rebalanceable, bool):
            exclude = not include_non_rebalanceable
        include_asset_ids = (
            fetch_included_asset_ids(db) if exclude else None
        )
        annualized_return = calculate_portfolio_xirr(db, include_asset_ids=include_asset_ids)
        if annualized_return is not None:
            annualized_return = round(annualized_return * 100, 2)
        
        return {
            "total_lifetime_gain": round(total_lifetime_gain, 2),
            "lifetime_gain_pct": round(lifetime_gain_pct, 2),
            "annualized_return": annualized_return,
            "active_asset_count": active_asset_count,
            "total_asset_count": total_asset_count
        }
    except Exception:
        return {
            "total_lifetime_gain": 0,
            "lifetime_gain_pct": 0,
            "annualized_return": None,
            "active_asset_count": 0,
            "total_asset_count": 0
        }


@router.get("/wealthos/assets")
async def get_wealthos_assets(
    include_non_rebalanceable: bool = Query(default=False),
    db: DatabaseConnector = Depends(get_db)
):
    """Get all assets ever held: active (current holdings) + closed (fully sold).
    Lifetime P&L = unrealized + realized (FIFO), same semantics as Performance ALL TIME.
    """
    try:
        # Thin formatter over the single P&L engine. The engine owns the active
        # snapshot (positive-position filter + sold-after-snapshot removal), the
        # closed/transaction-only union, the co-authority-safe realized-P&L map,
        # and the transaction-ledger provenance; src/services/pnl/wealthos.py maps
        # those records into this endpoint's response shape, byte-for-byte with
        # the V7.8.3 loop (parity-gated by
        # tests/api/test_wealthos_assets_engine_parity.py).
        from src.services.pnl.wealthos import build_wealthos_assets

        return build_wealthos_assets(
            db, include_non_rebalanceable=include_non_rebalanceable
        )

    except Exception as e:
        logger.exception("get_wealthos_assets failed")
        return api_error_response(e, context="get_wealthos_assets")

from typing import Optional

@router.get("/performance/history")
async def get_performance_history(
    period: str = Query(default="all_time"),
    exclude_non_balanceable: bool = Query(default=False),
    include_non_rebalanceable: Optional[bool] = Query(default=None),
    db: DatabaseConnector = Depends(get_db),
):
    """Get historical net worth for performance chart.

    Combines balance_sheet_monthly (historical monthly, 合计总资产) with
    holdings snapshots (recent daily) for full coverage.
    """
    if isinstance(include_non_rebalanceable, bool):
        exclude_non_balanceable = not include_non_rebalanceable
    import json as _json
    start_date = _period_start_date(period)
    included_asset_ids = None
    if exclude_non_balanceable:
        try:
            from src.api.routes.performance import fetch_included_asset_ids
            included_asset_ids = fetch_included_asset_ids(db, start_date=start_date)
        except Exception as e:
            logger.error(f"Performance history: failed to fetch non-rebalanceable IDs: {e}")

    history = []

    # Step A: Balance-sheet history (independent — failures here don't affect current point)
    # No longer capped at first_holdings — balance_sheet covers pre-Huinsight months cleanly.
    try:
        bs_clauses = []
        bs_params = []
        if start_date:
            bs_clauses.append("snapshot_date >= ?")
            bs_params.append(start_date)
        bs_filter = f"WHERE {' AND '.join(bs_clauses)}" if bs_clauses else ""
        bs_rows = db.execute(
            f"SELECT snapshot_date, payload FROM balance_sheet_monthly {bs_filter} ORDER BY snapshot_date ASC",
            bs_params or None,
        ).fetchall()

        for row in bs_rows:
            date_str = str(row[0])[:10]
            try:
                payload = _json.loads(row[1]) if isinstance(row[1], str) else row[1]
                # Prefer 合计总资产 (total gross assets), fall back to 合计净资产
                value = payload.get("合计总资产") or payload.get("合计净资产")
                if exclude_non_balanceable and value is not None:
                    adjustment = _balance_sheet_non_balanceable_adjustment(payload or {})
                    value = max(float(value) - adjustment, 0.0)
                if value and float(value) > 0:
                    history.append({"name": date_str, "value": float(value)})
            except Exception as e:
                logger.warning(f"Skipping balance sheet row {row[0]}: {e}")
                continue
    except Exception as e:
        logger.error(f"Performance history: balance sheet query failed: {e}")

    # Step B: Current point from holdings (independent — failure here still returns BS history)
    # Latest-per-asset CTE gives one accurate current total. With mixed snapshot_dates
    # across readers, GROUP BY snapshot_date produces partial-portfolio rows.
    try:
        current_clauses = ["h.is_shadow = FALSE"]
        current_params: list = []
        if start_date:
            current_clauses.append("lpa.latest_date >= ?")
            current_params.append(start_date)
        if included_asset_ids is not None:
            if included_asset_ids:
                placeholders = ", ".join(["?"] * len(included_asset_ids))
                current_clauses.append(f"h.asset_id IN ({placeholders})")
                current_params.extend(included_asset_ids)
            else:
                current_clauses.append("1=0")

        current_row = db.execute(f"""
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) as latest_date
                FROM holdings WHERE is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT
                strftime(MAX(lpa.latest_date), '%Y-%m-%d') as name,
                SUM(h.market_value) as value
            FROM holdings h
            JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            WHERE {' AND '.join(current_clauses)}
        """, current_params or None).fetchone()

        if current_row and current_row[0] and current_row[1]:
            history.append({"name": str(current_row[0]), "value": float(current_row[1])})
    except Exception as e:
        logger.error(f"Performance history: current holdings point failed: {e}")

    return history
@router.get("/risk/metrics")
async def get_risk_metrics(
    include_non_rebalanceable: bool = Query(default=False),
    db: DatabaseConnector = Depends(get_db)
):
    """Get portfolio risk metrics based on current allocation."""
    try:
        from src.api.routes.performance import fetch_included_asset_ids
        exclude = not include_non_rebalanceable
        include_asset_ids = fetch_included_asset_ids(db) if exclude else None

        if include_asset_ids is not None and not include_asset_ids:
            return calculate_portfolio_risk({})
        
        # 1. Get Current Allocation (latest snapshot only)
        # Also get asset IDs to fetch price history
        
        tx_filter = ""
        if include_asset_ids is not None:
            placeholders = ", ".join(["?" for _ in include_asset_ids])
            tx_filter = f" AND h.asset_id IN ({placeholders})"
            
        query_holdings = f"""
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) as latest_date
                FROM holdings WHERE is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT
                r.asset_class,
                SUM(h.market_value) as val,
                h.asset_id
            FROM holdings h
            JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
            WHERE h.is_shadow = FALSE{tx_filter}
            GROUP BY 1, 3
        """
        raw_rows = db.execute(query_holdings, list(include_asset_ids) if include_asset_ids is not None else None).fetchall()
        
        alloc = {"股票": 0.0, "固定收益": 0.0, "现金": 0.0, "另类投资": 0.0, "商品": 0.0, "房地产": 0.0}
        total_val = 0.0
        asset_class_holdings = {} # class -> list of (asset_id, value)
        
        for row in raw_rows:
            cat = map_class(row[0])
            val = float(row[1]) if row[1] else 0.0
            asset_id = row[2]
            
            alloc[cat] = alloc.get(cat, 0.0) + val
            total_val += val
            
            if cat not in asset_class_holdings:
                asset_class_holdings[cat] = []
            asset_class_holdings[cat].append((asset_id, val))
            
        if total_val == 0:
            return calculate_portfolio_risk({})
            
        # 2. Calculate Class Weights
        weights = {k: v / total_val for k, v in alloc.items()}
        
        # 3. Calculate Actual Volatilities from DSA (Hybrid Model)
        custom_vols = {}
        conn_dsa = None
        try:
            config = load_config()
            dsa_path = config.get('subsystems', {}).get('daily_stock_analysis', {}).get('path')
            market_db = config.get('subsystems', {}).get('daily_stock_analysis', {}).get('data_sources', {}).get('market_db')
            
            if dsa_path and market_db:
                full_path = f"{dsa_path}/{market_db}"
                if _db_exists(full_path):
                    conn_dsa = sqlite3.connect(full_path)
                    
                    # Calculate volatility for each class
                    import pandas as pd
                    import numpy as np
                    
                    for cat, assets in asset_class_holdings.items():
                        class_accumulated_vol = 0.0
                        class_total_val = sum(a[1] for a in assets)
                        
                        if class_total_val == 0:
                            continue
                        
                        has_data = False
                        
                        for asset_id, val in assets:
                            # Extract code (simple heuristic for CN funds/stocks)
                            code = None
                            if "CN_FUND_" in asset_id or "CN_STOCK_" in asset_id:
                                parts = asset_id.split('_')
                                if len(parts) >= 3:
                                    code = parts[2]
                            elif "00" in asset_id and len(asset_id) == 6:
                                code = asset_id
                                
                            asset_vol = 0.1 # Default fallback if no code
                            
                            if code:
                                try:
                                    # Get last 90 days price
                                    # Note: market_daily table structure depends on DSA
                                    df = pd.read_sql_query(f"SELECT close FROM stock_daily WHERE code='{code}' ORDER BY date DESC LIMIT 60", conn_dsa)
                                    if len(df) > 30:
                                        # Calculate 60-day annualized vol
                                        df['ret'] = df['close'].pct_change()
                                        std = df['ret'].std()
                                        if not np.isnan(std):
                                            asset_vol = std * np.sqrt(252)
                                            has_data = True
                                        else:
                                            asset_vol = 0.1 # Fallback
                                except Exception:
                                    pass
                            
                            # Weighted sum of volatilities (Assuming perfect correlation within class for conservatism)
                            # Or simplified: sum(w * vol) 
                            weight_in_class = val / class_total_val
                            class_accumulated_vol += weight_in_class * asset_vol
                            
                        # If we found actual data for this class, use it
                        # If we didn't search or find any (e.g. Cash), custom_vols won't be set, allowing model fallback
                        if has_data:
                            # Sanity check limits
                            class_accumulated_vol = max(0.01, min(1.5, class_accumulated_vol))
                            custom_vols[cat] = class_accumulated_vol
                            
        except Exception as e:
            print(f"DSA Volatility Error: {e}")
        finally:
            if conn_dsa:
                conn_dsa.close()

        # 4. Base model metrics (also used as fallback when history is insufficient)
        model_metrics = calculate_portfolio_risk(weights, custom_volatilities=custom_vols)

        # 5. Overlay historical volatility/sharpe when available to align with
        # /performance/risk-metrics. Keep API field names for frontend contract.
        hist = calculate_portfolio_metrics(
            db,
            include_asset_ids=include_asset_ids,
            exclude_non_balanceable=exclude,
        )
        hist_vol = hist.get("volatility_annual") if isinstance(hist, dict) else None
        hist_sharpe = hist.get("sharpe_ratio") if isinstance(hist, dict) else None
        if hist_vol is None or hist_sharpe is None:
            return model_metrics

        hist_vol = float(hist_vol)
        hist_sharpe = float(hist_sharpe)
        if not math.isfinite(hist_vol) or not math.isfinite(hist_sharpe):
            return model_metrics

        # Daily 95% VaR (%) from annualized volatility (%):
        # VaR_95 = 1.65 * sigma_daily, sigma_daily = sigma_annual / sqrt(252)
        var_95 = 1.65 * (hist_vol / 100.0) / math.sqrt(252) * 100.0
        volatility_status = "LOW" if hist_vol < 10 else "MED" if hist_vol < 20 else "HIGH"
        sharpe_status = (
            "POOR" if hist_sharpe < 0.5
            else "AVG" if hist_sharpe < 1.0
            else "GOOD" if hist_sharpe < 1.5
            else "EXCELLENT"
        )
        var_status = "LOW" if var_95 < 1.5 else "MED" if var_95 < 3 else "HIGH"

        return {
            "volatility": round(hist_vol, 2),
            "volatility_status": volatility_status,
            "sharpe": round(hist_sharpe, 2),
            "sharpe_status": sharpe_status,
            "var_95": round(var_95, 2),
            "var_95_status": var_status,
            # Keep these as model-derived estimates for Risk Matrix only.
            "beta": model_metrics.get("beta", 0),
            "div_score": model_metrics.get("div_score", 0),
        }
        
    except Exception as e:
        print(f"Error calculating risk metrics: {e}")
        return {
            "volatility": 0, "volatility_status": "LOW",
            "sharpe": 0, "sharpe_status": "AVG",
            "var_95": 0, "var_95_status": "LOW",
            "beta": 0, "div_score": 0
        }


@router.get("/risk/correlation")
async def get_risk_correlation(
    level: str = "top",
    include_non_rebalanceable: bool = Query(default=False),
    db: DatabaseConnector = Depends(get_db)
):
    """Get empirical asset class correlation matrix from authoritative holdings history."""
    try:
        from src.api.routes.performance import fetch_included_asset_ids

        level_key = "sub" if (level or "").strip().lower() == "sub" else "top"

        exclude = not include_non_rebalanceable
        include_asset_ids = fetch_included_asset_ids(db) if exclude else None

        def empty_payload(method: str = "empirical_holdings", window_start: str = "", window_end: str = ""):
            return {
                "matrix": [],
                "assets": [],
                "method": method,
                "effective_periods": 0,
                "overlap_min": 0,
                "overlap_median": 0,
                "insufficient_pairs": 0,
                "total_pairs": 0,
                "window_start": window_start,
                "window_end": window_end,
                "min_overlap_periods": CORR_MIN_OVERLAP_PERIODS,
                "winsor_p_low": CORR_WINSOR_P_LOW,
                "winsor_p_high": CORR_WINSOR_P_HIGH,
                "excluded_jump_dates": [],
                "excluded_jump_points_count": 0,
                "excluded_jump_points_by_class": {},
                "clipped_points_by_class": {},
                "clipped_pair_share": 0.0,
            }

        if include_asset_ids is not None and not include_asset_ids:
            return empty_payload()

        class_expr = (
            "COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unknown')"
            if level_key == "top"
            else "COALESCE(tc.name, r.asset_class, 'Unknown')"
        )
        tx_filter = ""
        query_params = []
        if include_asset_ids is not None:
            placeholders = ", ".join(["?" for _ in include_asset_ids])
            tx_filter = f" AND auth.asset_id IN ({placeholders})"
            query_params = list(include_asset_ids)

        query = f"""
            WITH auth AS (
                SELECT
                    h.asset_id,
                    h.snapshot_date,
                    h.market_value,
                    ROW_NUMBER() OVER (
                        PARTITION BY h.asset_id, h.snapshot_date
                        ORDER BY h.is_shadow ASC
                    ) AS rn
                FROM holdings h
            )
            SELECT
                auth.snapshot_date,
                {class_expr} AS asset_class,
                SUM(auth.market_value) AS total_value
            FROM auth
            LEFT JOIN asset_registry r ON auth.asset_id = r.canonical_id
            LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
            LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
            WHERE auth.rn = 1{tx_filter}
            GROUP BY 1, 2
            ORDER BY 1, 2
        """
        rows = db.execute(query, query_params or None).fetchall()
        if not rows:
            return empty_payload()

        pivot_data: dict = {}
        for snapshot_date, asset_class, total_value in rows:
            if snapshot_date is None:
                continue
            label = str(asset_class or "Unknown")
            if label in ("Unknown", "Unclassified"):
                continue
            if snapshot_date not in pivot_data:
                pivot_data[snapshot_date] = {}
            pivot_data[snapshot_date][label] = (
                pivot_data[snapshot_date].get(label, 0.0) + float(total_value or 0.0)
            )

        if not pivot_data:
            return empty_payload()

        pivot = pd.DataFrame(pivot_data).T.sort_index()
        pivot.index = pd.to_datetime(pivot.index)
        if pivot.empty or len(pivot) < 3:
            return empty_payload()

        cutoff = pivot.index.max() - pd.Timedelta(days=CORR_WINDOW_DAYS)
        pivot = pivot[pivot.index >= cutoff]
        if pivot.empty or len(pivot) < 3:
            window_start = cutoff.date().isoformat()
            window_end = cutoff.date().isoformat()
            return empty_payload(window_start=window_start, window_end=window_end)

        window_start = pivot.index.min().date().isoformat()
        window_end = pivot.index.max().date().isoformat()

        # Compute period-over-period returns on raw daily snapshots.
        # fill_method=None prevents forward-filling gaps between irregular dates.
        returns = pivot.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
        returns_masked = returns.copy()

        excluded_jump_dates: set[str] = set()
        excluded_jump_points_by_class: dict[str, int] = {}
        for asset in returns_masked.columns:
            series = returns_masked[asset].dropna()
            if series.empty:
                continue
            values = series.to_numpy(dtype=float)
            median_val = float(np.median(values))
            mad = float(np.median(np.abs(values - median_val)))
            std = float(np.std(values))
            scale = max(mad, std * 0.6745)
            threshold = float(np.median(np.abs(values)) + (CORR_JUMP_K * scale))
            jump_mask = ((series.abs() > threshold) | (series.abs() > CORR_JUMP_ABS_HARD_CAP)) & (
                series.abs() > CORR_JUMP_ABS_FLOOR
            )
            flagged_idx = series.index[jump_mask]
            if len(flagged_idx) == 0:
                continue
            returns_masked.loc[flagged_idx, asset] = np.nan
            excluded_jump_points_by_class[str(asset)] = int(len(flagged_idx))
            excluded_jump_dates.update(ts.date().isoformat() for ts in flagged_idx)

        returns_clean = returns_masked.copy()
        clipped_points_by_class: dict[str, int] = {}
        clipped_total = 0
        valid_total = 0
        for asset in returns_clean.columns:
            series = returns_clean[asset].dropna()
            if series.empty:
                clipped_points_by_class[str(asset)] = 0
                continue
            valid_total += int(series.shape[0])
            low_q = float(series.quantile(CORR_WINSOR_P_LOW))
            high_q = float(series.quantile(CORR_WINSOR_P_HIGH))
            clipped = series.clip(lower=low_q, upper=high_q)
            clipped_count = int((clipped != series).sum())
            clipped_total += clipped_count
            clipped_points_by_class[str(asset)] = clipped_count
            returns_clean.loc[series.index, asset] = clipped

        corr_df = returns_clean.corr(method="pearson", min_periods=CORR_MIN_OVERLAP_PERIODS)

        assets = [str(col) for col in pivot.columns.tolist()]
        matrix = []
        for a1 in assets:
            correlations = {}
            for a2 in assets:
                overlap = (
                    int(returns_clean[a1].notna().sum())
                    if a1 == a2
                    else int(returns_clean[[a1, a2]].dropna().shape[0])
                )
                if a1 == a2:
                    correlations[a2] = {
                        "value": 1.0,
                        "overlap": overlap,
                        "low_confidence": False,
                    }
                else:
                    corr_value = None
                    if a1 in corr_df.index and a2 in corr_df.columns:
                        raw = corr_df.loc[a1, a2]
                        if pd.notna(raw):
                            corr_value = max(-1.0, min(1.0, float(raw)))
                    low_conf = (
                        corr_value is not None
                        and CORR_MIN_OVERLAP_PERIODS <= overlap <= (CORR_MIN_OVERLAP_PERIODS + CORR_LOW_CONFIDENCE_MARGIN)
                    )
                    correlations[a2] = {
                        "value": corr_value,
                        "overlap": overlap,
                        "low_confidence": bool(low_conf),
                    }
            matrix.append({"asset": a1, "correlations": correlations})

        off_diagonal_pairs = [(a1, a2) for i, a1 in enumerate(assets) for a2 in assets[i + 1 :]]
        overlap_counts = [
            int(returns_clean[[a1, a2]].dropna().shape[0])
            for a1, a2 in off_diagonal_pairs
        ]
        total_pairs = len(off_diagonal_pairs)
        insufficient_pairs = sum(1 for c in overlap_counts if c < CORR_MIN_OVERLAP_PERIODS)
        overlap_min = min(overlap_counts) if overlap_counts else 0
        overlap_median = float(np.median(overlap_counts)) if overlap_counts else 0.0

        return {
            "matrix": matrix,
            "assets": assets,
            "method": "empirical_holdings",
            "effective_periods": max(len(pivot) - 1, 0),
            "overlap_min": overlap_min,
            "overlap_median": overlap_median,
            "insufficient_pairs": insufficient_pairs,
            "total_pairs": total_pairs,
            "window_start": window_start,
            "window_end": window_end,
            "min_overlap_periods": CORR_MIN_OVERLAP_PERIODS,
            "winsor_p_low": CORR_WINSOR_P_LOW,
            "winsor_p_high": CORR_WINSOR_P_HIGH,
            "excluded_jump_dates": sorted(excluded_jump_dates),
            "excluded_jump_points_count": int(sum(excluded_jump_points_by_class.values())),
            "excluded_jump_points_by_class": excluded_jump_points_by_class,
            "clipped_points_by_class": clipped_points_by_class,
            "clipped_pair_share": round((clipped_total / valid_total), 6) if valid_total > 0 else 0.0,
        }
    except Exception:
        return {
            "matrix": [],
            "assets": [],
            "method": "error",
            "effective_periods": 0,
            "overlap_min": 0,
            "overlap_median": 0,
            "insufficient_pairs": 0,
            "total_pairs": 0,
            "window_start": "",
            "window_end": "",
            "min_overlap_periods": CORR_MIN_OVERLAP_PERIODS,
            "winsor_p_low": CORR_WINSOR_P_LOW,
            "winsor_p_high": CORR_WINSOR_P_HIGH,
            "excluded_jump_dates": [],
            "excluded_jump_points_count": 0,
            "excluded_jump_points_by_class": {},
            "clipped_points_by_class": {},
            "clipped_pair_share": 0.0,
        }


@router.get("/dashboard/actions")
async def get_dashboard_actions(db: DatabaseConnector = Depends(get_db)):
    """Get actionable items for the dashboard Action Center."""
    actions = []
    
    # 1. Drift Alerts
    try:
        # Check for deviation_actions table existence implicitly by querying
        drifts = db.execute("""
            SELECT asset_class, deviation_pct, tolerance_pct
            FROM deviation_actions
            WHERE status='observing' AND is_within_tolerance=0
            LIMIT 5
        """).fetchall()
        
        for d in drifts:
            actions.append({
                "type": "drift_alert",
                "priority": "high",
                "title": f"{d[0]} allocation drift: {d[1]}%",
                "subtitle": f"Tolerance: {d[2]}%",
                "action_url": "/compass"
            })
    except Exception:
        pass
        
    # 2. Pending Decisions
    try:
        pending_count = db.execute("""
            SELECT COUNT(*) FROM insights 
            WHERE adopted IS NULL AND category='recommendation'
        """).fetchone()[0]
        
        if pending_count > 0:
            actions.append({
                "type": "pending_decision",
                "priority": "medium",
                "title": f"{pending_count} recommendations awaiting decision",
                "subtitle": "Review in Decision Hub",
                "action_url": "/decisions"
            })
    except Exception:
        pass
        
    return {"actions": actions}
