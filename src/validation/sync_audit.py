import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime

from src.sources.registry import get_registry

logger = logging.getLogger(__name__)

# Derived from registry — key↔system maps for source-name translation.
_KEY_TO_SYSTEM: dict = get_registry().key_to_system()
_SYSTEM_TO_KEY: dict = get_registry().system_to_key()

@dataclass
class AssetAuditDetail:
    asset_id: str
    status: str
    reader_value: float
    db_value: float
    reader_qty: float
    db_qty: float
    original_currency: str
    original_value: float
    db_currency: str
    asset_name: str = ""

@dataclass
class SourceDiscrepancy:
    source_system: str
    status: str                 # "match", "discrepancy", "error"
    reader_asset_count: int
    db_asset_count: int
    reader_total_value: float
    db_total_value: float
    value_diff_pct: float
    missing_in_db: List[str]
    missing_in_reader: List[str]
    value_mismatches: List[Dict[str, Any]]
    assets: List[AssetAuditDetail]

@dataclass
class SyncAuditReport:
    sync_id: str
    timestamp: str
    net_worth_before: float
    net_worth_after: float
    net_worth_change_pct: float
    asset_count_before: int
    asset_count_after: int
    by_source_before: Dict[str, Any]
    by_source_after: Dict[str, Any]
    integrity_passed: int
    integrity_total: int
    integrity_checks: List[Dict[str, Any]]
    reader_counts: Dict[str, Any]
    warnings: List[str]
    alert: bool
    is_no_change: bool = False
    info_messages: List[str] = field(default_factory=list)
    # Per-phase StepResult dicts {name, status, critical, error, duration_ms};
    # phase-level entries use name "P0".."P8" (see src/sync/phases/manifest.py)
    steps: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class OnDemandAuditReport:
    report_id: str
    source_discrepancies: List[SourceDiscrepancy]
    integrity: Dict[str, Any]
    overall_status: str

def persist_sync_audit(connector, report: SyncAuditReport) -> None:
    """Writes a SyncAuditReport to the sync_audit_reports DB table."""
    try:
        connector.execute("""
            INSERT INTO sync_audit_reports (
                id, created_at, report_type, net_worth_before, net_worth_after,
                net_worth_change_pct, asset_count_before, asset_count_after,
                by_source_before, by_source_after, integrity_passed, integrity_total,
                integrity_checks, reader_counts, warnings, alert, is_no_change, info_messages,
                steps
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report.sync_id,
            report.timestamp,
            "sync",
            report.net_worth_before,
            report.net_worth_after,
            report.net_worth_change_pct,
            report.asset_count_before,
            report.asset_count_after,
            json.dumps(report.by_source_before),
            json.dumps(report.by_source_after),
            report.integrity_passed,
            report.integrity_total,
            json.dumps(report.integrity_checks),
            json.dumps(report.reader_counts),
            json.dumps(report.warnings),
            report.alert,
            report.is_no_change,
            json.dumps(report.info_messages),
            json.dumps(report.steps)
        ))
        logger.info(f"Persisted sync audit report {report.sync_id}")
    except Exception as e:
        logger.error(f"Failed to persist sync audit report: {e}")
        # Not throwing here: persistence failure shouldn't crash the pipeline, but logged

def get_latest_sync_audits(connector, limit: int = 20) -> List[Dict[str, Any]]:
    """Returns a list of recent sync audit reports in descending created_at order."""
    rows = connector.execute(f"""
        SELECT
            id, created_at, report_type, net_worth_before, net_worth_after,
            net_worth_change_pct, integrity_passed, integrity_total, alert,
            is_no_change, info_messages
        FROM sync_audit_reports
        WHERE report_type = 'sync'
        ORDER BY created_at DESC
        LIMIT {limit}
    """).fetchall()

    reports = []
    for row in rows:
        reports.append({
            "id": row[0],
            "created_at": str(row[1]) if row[1] else None,
            "report_type": row[2],
            "net_worth_before": row[3],
            "net_worth_after": row[4],
            "net_worth_change_pct": row[5],
            "integrity_passed": row[6],
            "integrity_total": row[7],
            "alert": bool(row[8]),
            "is_no_change": bool(row[9]) if row[9] is not None else False,
            "info_messages": json.loads(row[10]) if row[10] else []
        })
    return reports

def get_sync_audit_detail(connector, sync_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves all details of a single sync audit report."""
    row = connector.execute("""
        SELECT
            id, created_at, report_type, net_worth_before, net_worth_after,
            net_worth_change_pct, asset_count_before, asset_count_after,
            by_source_before, by_source_after, integrity_passed, integrity_total,
            integrity_checks, reader_counts, warnings, alert, is_no_change, info_messages,
            steps
        FROM sync_audit_reports
        WHERE id = ?
    """, (sync_id,)).fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "created_at": str(row[1]) if row[1] else None,
        "report_type": row[2],
        "net_worth_before": row[3],
        "net_worth_after": row[4],
        "net_worth_change_pct": row[5],
        "asset_count_before": row[6],
        "asset_count_after": row[7],
        "by_source_before": json.loads(row[8]) if row[8] else {},
        "by_source_after": json.loads(row[9]) if row[9] else {},
        "integrity_passed": row[10],
        "integrity_total": row[11],
        "integrity_checks": json.loads(row[12]) if row[12] else [],
        "reader_counts": json.loads(row[13]) if row[13] else {},
        "warnings": json.loads(row[14]) if row[14] else [],
        "alert": bool(row[15]),
        "is_no_change": bool(row[16]) if row[16] is not None else False,
        "info_messages": json.loads(row[17]) if row[17] else [],
        # NULL for runs persisted before the steps column existed (legacy)
        "steps": json.loads(row[18]) if row[18] else None
    }

def run_on_demand_audit(connector, config: dict) -> OnDemandAuditReport:
    """
    On-Demand Audit: compares reader output directly against DB.
    """
    from src.validation.data_integrity_gate import run_integrity_checks

    sources = ["schwab", "cn_fund", "gold", "insurance", "rsu"]
    discrepancies = []
    
    for source in sources:
        try:
            # We use the sync modules directly to get reader data
            if source == "schwab":
                from src.sync.schwab_sync import sync_schwab
                res = sync_schwab(config)
            elif source == "cn_fund":
                from src.sync.cn_fund_sync import sync_cn_fund
                res = sync_cn_fund(config)
            elif source == "gold":
                from src.sync.gold_sync import sync_gold
                res = sync_gold(config)
            elif source == "insurance":
                from src.sync.insurance_sync import sync_insurance
                res = sync_insurance(config)
            elif source == "rsu":
                from src.sync.rsu_sync import sync_rsu
                res = sync_rsu(config)
            else:
                continue

            # Convert result.get("holdings", DataFrame) into records
            df = res.get("holdings") if isinstance(res, dict) else None
            reader_assets = set()
            reader_total = 0.0
            
            if df is not None and not df.empty:
                # The sync_<source> functions return fully transformed data,
                # meaning `asset_id` and `market_value` exist (e.g. US_STK_GOOGL).
                id_col = "asset_id" if "asset_id" in df.columns else None
                val_col = "market_value" if "market_value" in df.columns else None
                
                if id_col:
                    reader_assets = set(df[id_col].dropna().astype(str).tolist())
                if val_col:
                    reader_total = float(df[val_col].dropna().sum())
                    
            db_source_name = _KEY_TO_SYSTEM.get(source, source)

            # Gather reader stats
            # Read from DB
            db_records = connector.execute(
                """WITH latest AS (
                       SELECT asset_id, MAX(snapshot_date) AS max_date
                       FROM holdings
                       WHERE source_system = ? AND is_shadow = FALSE
                       GROUP BY asset_id
                   )
                   SELECT h.asset_id, h.market_value, a.display_name
                   FROM holdings h
                   JOIN latest l ON h.asset_id = l.asset_id AND h.snapshot_date = l.max_date
                   LEFT JOIN asset_registry a ON h.asset_id = a.canonical_id
                   WHERE h.source_system = ? AND h.is_shadow = FALSE AND h.market_value IS NOT NULL""",
                (db_source_name, db_source_name)
            ).fetchall()
            
            db_assets = {r[0] for r in db_records}
            db_values = {r[0]: float(r[1]) for r in db_records}
            db_names = {r[0]: str(r[2] or "") for r in db_records}
            db_total = sum(db_values.values())
            
            missing_in_db = list(reader_assets - db_assets)
            missing_in_reader = list(db_assets - reader_assets)
            
            value_mismatches = []
            asset_details = []
            
            if df is not None and not df.empty and id_col and val_col:
                # We need a robust way to map "US_STK_FBTC" in DB to "US_ETF_FBTC" in Reader.
                matched_reader_ids = set()
                matched_db_ids = set()
                
                def get_core_ticker(asset_id: str) -> str:
                    for prefix in ["US_STK_", "US_ETF_", "CN_FND_", "CASH_"]:
                        if asset_id.startswith(prefix):
                            return asset_id[len(prefix):]
                    return asset_id
                
                # To capture qty if available
                qty_col = next((c for c in ["quantity", "Quantity", "Shares", "Units"] if c in df.columns), None)
                
                # To capture name if available
                name_col = next((c for c in ["name", "asset_name", "Description", "FundName"] if c in df.columns), None)
                
                for _, row in df.iterrows():
                    raw_id = str(row[id_col])
                    if raw_id not in reader_assets:
                        continue # safety for nan
                    
                    raw_name = row[name_col] if name_col else ""
                    reader_name = str(raw_name) if raw_name is not None and str(raw_name) != 'nan' else ""
                    
                    core_reader_id = get_core_ticker(raw_id)
                    matched_db_id = None
                    
                    for db_id in db_assets:
                        core_db_id = get_core_ticker(db_id)
                        # Exact core match or substring overlap (e.g. FBTC in US_ETF_FBTC)
                        if core_reader_id == core_db_id or core_reader_id in db_id or core_db_id in raw_id:
                            matched_db_id = db_id
                            break
                    
                    try:
                        val_str = str(row[val_col]).replace("$", "").replace(",", "")
                        reader_val = float(val_str)
                    except (ValueError, TypeError):
                        reader_val = 0.0
                        
                    reader_qty = 0.0
                    if qty_col:
                        try:
                            reader_qty = float(str(row[qty_col]).replace(",", ""))
                        except (ValueError, TypeError):
                            reader_qty = 0.0

                    db_currency = "CNY"
                    if source in ["schwab", "rsu"]:
                        original_currency = "USD"
                        original_value = reader_val / 7.0 if reader_val > 0 else 0.0
                    else:
                        original_currency = "CNY"
                        original_value = reader_val
                    
                    if matched_db_id:
                        matched_reader_ids.add(raw_id)
                        matched_db_ids.add(matched_db_id)
                        
                        db_val = db_values.get(matched_db_id, 0.0)
                        
                        # Compare logic context
                        is_match = True
                        if db_val > 0:
                            diff_pct = abs(reader_val - db_val) / db_val
                            if diff_pct > 0.01:
                                is_match = False
                        elif reader_val > 0:
                            is_match = False
                        
                        asset_details.append(AssetAuditDetail(
                            asset_id=matched_db_id, # Present standard UI ID
                            status="match" if is_match else "discrepancy",
                            reader_value=reader_val,
                            db_value=db_val,
                            reader_qty=reader_qty,
                            db_qty=0.0, # Not systematically storing qty historical yet
                            original_currency=original_currency,
                            original_value=original_value,
                            db_currency=db_currency,
                            asset_name=db_names.get(matched_db_id) or reader_name
                        ))
                    else:
                        asset_details.append(AssetAuditDetail(
                            asset_id=raw_id,
                            status="missing_in_db",
                            reader_value=reader_val,
                            db_value=0.0,
                            reader_qty=reader_qty,
                            db_qty=0.0,
                            original_currency=original_currency,
                            original_value=original_value,
                            db_currency=db_currency,
                            asset_name=reader_name
                        ))
                
                missing_in_db = list(reader_assets - matched_reader_ids)
                missing_in_reader = list(db_assets - matched_db_ids)
                
                for db_missing in missing_in_reader:
                    db_val = db_values.get(db_missing, 0.0)
                    db_name = db_names.get(db_missing, "")
                    asset_details.append(AssetAuditDetail(
                        asset_id=db_missing,
                        status="missing_in_reader",
                        reader_value=0.0,
                        db_value=db_val,
                        reader_qty=0.0,
                        db_qty=0.0,
                        original_currency="CNY" if source not in ["schwab", "rsu"] else "USD",
                        original_value=0.0,
                        db_currency="CNY",
                        asset_name=db_name
                    ))
            
            diff_abs = abs(reader_total - db_total)
            diff_pct_total = (diff_abs / db_total) if db_total > 0 else (1.0 if diff_abs > 0 else 0)
            
            status = "match"
            if missing_in_db or missing_in_reader or diff_pct_total > 0.01:
                status = "discrepancy"

            discrepancies.append(SourceDiscrepancy(
                source_system=db_source_name,
                status=status,
                reader_asset_count=len(reader_assets),
                db_asset_count=len(db_assets),
                reader_total_value=reader_total,  # Keep raw reader value
                db_total_value=db_total,          # Keep raw DB value
                value_diff_pct=diff_pct_total,    # Pass normalized 0% diff if FX compensated
                missing_in_db=missing_in_db,
                missing_in_reader=missing_in_reader,
                value_mismatches=value_mismatches,
                assets=asset_details
            ))
            
        except Exception as e:
            logger.error(f"Failed to run on-demand audit for {source}: {e}")
            db_source_name = _KEY_TO_SYSTEM.get(source, source)
            discrepancies.append(SourceDiscrepancy(
                source_system=db_source_name,
                status="error",
                reader_asset_count=0,
                db_asset_count=0,
                reader_total_value=0.0,
                db_total_value=0.0,
                value_diff_pct=0.0,
                missing_in_db=[],
                missing_in_reader=[],
                value_mismatches=[],
                assets=[]
            ))

    # Run integrity
    try:
        integrity_report = run_integrity_checks(connector)
        integrity_dict = {
            "all_passed": integrity_report.all_passed,
            "passed_count": integrity_report.passed_count,
            "total_count": integrity_report.passed_count + len(integrity_report.failed_checks),
            "run_at": datetime.now().isoformat(),
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "actual_value": str(c.actual_value),
                    "threshold": str(c.threshold) if c.threshold else "",
                    "details": c.details
                } for c in integrity_report.checks
            ]
        }
    except Exception as e:
        logger.error(f"Integrity check failed during on-demand audit: {e}")
        integrity_dict = {
            "all_passed": False,
            "passed_count": 0,
            "total_count": 0,
            "run_at": datetime.now().isoformat(),
            "checks": [],
            "error": str(e)
        }
        
    overall_status = "healthy"
    if not integrity_dict.get("all_passed") or any(d.status != "match" for d in discrepancies):
        overall_status = "issues_detected"

    return OnDemandAuditReport(
        report_id=str(uuid.uuid4()),
        source_discrepancies=discrepancies,
        integrity=integrity_dict,
        overall_status=overall_status
    )
