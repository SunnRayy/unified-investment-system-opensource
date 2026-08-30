from fastapi import APIRouter, HTTPException
from datetime import datetime

from src.database.connector import DatabaseConnector
from src.validation.run_reader_validation import load_config
from src.validation.sync_audit import (
    get_latest_sync_audits,
    get_sync_audit_detail,
    run_on_demand_audit,
)
from src.validation.data_integrity_gate import run_integrity_checks
from dataclasses import asdict

router = APIRouter(prefix="/audit/v2", tags=["audit_v2"])

@router.get("/reports")
async def get_audit_history(limit: int = 20):
    connector = DatabaseConnector(read_only=True)
    try:
        reports = get_latest_sync_audits(connector, limit)
        count_row = connector.execute("SELECT COUNT(*) FROM sync_audit_reports WHERE report_type = 'sync'").fetchone()
        total = count_row[0] if count_row else 0
        return {"reports": reports, "total": total}
    finally:
        connector.close()

@router.get("/latest")
async def get_latest_audit():
    connector = DatabaseConnector(read_only=True)
    try:
        reports = get_latest_sync_audits(connector, 1)
        if reports:
            return reports[0]
        return None
    finally:
        connector.close()

@router.get("/integrity")
async def get_integrity_status():
    connector = DatabaseConnector(read_only=True)
    try:
        integrity_report = run_integrity_checks(connector)
        return {
            "all_passed": integrity_report.all_passed,
            "passed_count": integrity_report.passed_count,   # verified only
            "skipped_count": integrity_report.skipped_count,
            "total_count": len(integrity_report.checks),
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
    finally:
        connector.close()

@router.get("/reports/{report_id}")
async def get_audit_report(report_id: str):
    connector = DatabaseConnector(read_only=True)
    try:
        report = get_sync_audit_detail(connector, report_id)
        if not report:
            raise HTTPException(status_code=404, detail="Audit report not found")
        return report
    finally:
        connector.close()

@router.post("/on-demand")
async def create_on_demand_audit():
    connector = DatabaseConnector(read_only=True)
    try:
        config = load_config()
        report = run_on_demand_audit(connector, config)
        return asdict(report)
    finally:
        connector.close()
