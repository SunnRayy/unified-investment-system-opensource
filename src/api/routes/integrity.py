"""Data Integrity API routes."""
from fastapi import APIRouter, Depends
from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.validation.data_integrity_gate import run_integrity_checks

router = APIRouter(prefix="/integrity", tags=["Integrity"])


@router.get("/status")
async def get_integrity_status(db: DatabaseConnector = Depends(get_db)):
    """Run data integrity checks and return results."""
    report = run_integrity_checks(db)
    return {
        "all_passed": report.all_passed,
        "passed_count": report.passed_count,
        "total_count": len(report.checks),
        "run_at": report.run_at.isoformat(),
        "checks": [
            {
                "name": c.name,
                "passed": c.passed,
                "actual_value": str(c.actual_value),
                "threshold": str(c.threshold),
                "details": c.details,
            }
            for c in report.checks
        ],
    }


@router.get("/audit")
async def get_integrity_audit(db: DatabaseConnector = Depends(get_db)):
    """Run data integrity checks (alias for /status — ground truth deprecated)."""
    report = run_integrity_checks(db)
    return {
        "all_passed": report.all_passed,
        "passed_count": report.passed_count,
        "total_count": len(report.checks),
        "run_at": report.run_at.isoformat(),
        "checks": [
            {
                "name": c.name,
                "passed": c.passed,
                "actual_value": str(c.actual_value),
                "threshold": str(c.threshold),
                "details": c.details,
            }
            for c in report.checks
        ],
    }
