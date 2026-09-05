import pytest
import uuid
from datetime import datetime
from unittest.mock import MagicMock

# Tests will fail because src.validation.sync_audit does not exist yet.
from src.validation.sync_audit import (
    SyncAuditReport,
    OnDemandAuditReport,
    persist_sync_audit,
    get_latest_sync_audits,
    get_sync_audit_detail,
    run_on_demand_audit
)

@pytest.fixture
def audit_db(clean_db):
    """Extend the clean_db fixture with the sync_audit_reports table."""
    clean_db.execute("""
        CREATE TABLE IF NOT EXISTS sync_audit_reports (
            id VARCHAR(36) PRIMARY KEY,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            report_type VARCHAR(20) NOT NULL DEFAULT 'sync',
            net_worth_before DOUBLE,
            net_worth_after DOUBLE,
            net_worth_change_pct DOUBLE,
            asset_count_before INTEGER,
            asset_count_after INTEGER,
            by_source_before JSON,
            by_source_after JSON,
            integrity_passed INTEGER,
            integrity_total INTEGER,
            integrity_checks JSON,
            source_discrepancies JSON,
            reader_counts JSON,
            warnings JSON,
            alert BOOLEAN DEFAULT FALSE,
            is_no_change BOOLEAN DEFAULT FALSE,
            info_messages JSON,
            steps JSON
        )
    """)
    return clean_db

def test_sync_audit_report_creation():
    report = SyncAuditReport(
        sync_id=str(uuid.uuid4()),
        timestamp=datetime.now().isoformat(),
        net_worth_before=100.0,
        net_worth_after=110.0,
        net_worth_change_pct=0.1,
        asset_count_before=5,
        asset_count_after=6,
        by_source_before={"Schwab": {"count": 2, "value": 50}},
        by_source_after={"Schwab": {"count": 3, "value": 60}},
        integrity_passed=18,
        integrity_total=18,
        integrity_checks=[],
        reader_counts={"Schwab": {"read": 3, "inserted": 3}},
        warnings=["Warning 1"],
        alert=False
    )
    assert report.net_worth_after == 110.0
    assert report.alert is False

def test_persist_and_retrieve_sync_audit(audit_db):
    sync_id1 = str(uuid.uuid4())
    report1 = SyncAuditReport(
        sync_id=sync_id1,
        timestamp=datetime.now().isoformat(),
        net_worth_before=100.0,
        net_worth_after=110.0,
        net_worth_change_pct=0.1,
        asset_count_before=5,
        asset_count_after=6,
        by_source_before={"Schwab": {"count": 2, "value": 50}},
        by_source_after={"Schwab": {"count": 3, "value": 60}},
        integrity_passed=18,
        integrity_total=18,
        integrity_checks=[{"name": "check1", "passed": True, "details": "ok"}],
        reader_counts={"Schwab": {"read": 3, "inserted": 3}},
        warnings=[],
        alert=False
    )
    
    persist_sync_audit(audit_db, report1)
    
    latest = get_latest_sync_audits(audit_db, limit=10)
    assert len(latest) == 1
    assert latest[0]["id"] == sync_id1
    assert latest[0]["net_worth_after"] == 110.0
    
    detail = get_sync_audit_detail(audit_db, sync_id1)
    assert detail["id"] == sync_id1
    assert "by_source_before" in detail
    assert "integrity_checks" in detail
    assert len(detail["integrity_checks"]) == 1

def test_get_latest_sync_audits_order(audit_db):
    report1 = SyncAuditReport(
        sync_id=str(uuid.uuid4()), timestamp="2026-03-01T10:00:00", net_worth_before=0, net_worth_after=0,
        net_worth_change_pct=0, asset_count_before=0, asset_count_after=0, by_source_before={}, by_source_after={},
        integrity_passed=18, integrity_total=18, integrity_checks=[], reader_counts={}, warnings=[], alert=False
    )
    report2 = SyncAuditReport(
        sync_id=str(uuid.uuid4()), timestamp="2026-03-02T10:00:00", net_worth_before=0, net_worth_after=100,
        net_worth_change_pct=1.0, asset_count_before=0, asset_count_after=0, by_source_before={}, by_source_after={},
        integrity_passed=18, integrity_total=18, integrity_checks=[], reader_counts={}, warnings=[], alert=True
    )
    
    persist_sync_audit(audit_db, report1)
    persist_sync_audit(audit_db, report2)
    
    latest = get_latest_sync_audits(audit_db, limit=2)
    assert len(latest) == 2
    # Should be descending by timestamp
    assert latest[0]["id"] == report2.sync_id
    assert latest[1]["id"] == report1.sync_id

def test_run_on_demand_audit_graceful_missing_files(audit_db):
    from unittest.mock import patch

    with patch("src.sync.schwab_sync.sync_schwab", side_effect=Exception("File read error")), \
         patch("src.validation.data_integrity_gate.run_integrity_checks", return_value=MagicMock(all_passed=True, checks=[])):
        
        report = run_on_demand_audit(audit_db, {})
        assert isinstance(report, OnDemandAuditReport)
        # We test that all 5 sources handled errors gracefully
        assert len(report.source_discrepancies) == 5
        schwab = next(d for d in report.source_discrepancies if d.source_system == "Schwab_CSV")
        assert schwab.status == "error"
