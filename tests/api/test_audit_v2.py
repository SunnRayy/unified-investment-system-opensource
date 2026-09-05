import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch


# Add project root to sys.path if not there
from src.api.dependencies import get_db
from src.api.main import app
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.validation.sync_audit import OnDemandAuditReport, SourceDiscrepancy

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    """Point this module's routes at a throwaway database.

    These tests mock the audit functions they actually assert on, so the
    connection is incidental — but every route in `audit_v2.py` calls
    `DatabaseConnector(read_only=True)` **with no path**, bypassing the `get_db`
    dependency entirely, which means it binds to whatever the configured
    database is. On a developer machine that is the real portfolio; in CI it is
    the file the schema-init step creates, and under `pytest -n auto` the
    parallel workers then race each other for DuckDB's file lock. That is what
    took the public repository's CI red on 2026-08-30:

        IOException: Could not set lock on file ".../data/unified.duckdb"

    It is a flake by nature — the private run passed the same afternoon — which
    is why it survived this long: rare enough to read as noise rather than as
    the unisolated-database pattern it is.

    Overriding `get_db` is not enough here, and looked sufficient until the
    export tree was actually run. Each route opens *and closes* its own
    connector, so the class itself is patched to hand back a fresh connection to
    the temp file every time rather than one shared object that the first
    request would close underneath the second.

    The routes' own use of the default path is the real defect; this contains
    it for the tests without changing production behaviour.
    """
    db_path = tmp_path / "audit_v2.duckdb"
    seed = DatabaseConnector(str(db_path))
    initialize_schema(seed)
    seed.close()

    def _temp_connector(*args, **kwargs):
        kwargs.pop("db_path", None)
        return DatabaseConnector(str(db_path), read_only=kwargs.get("read_only", False))

    with patch("src.api.routes.audit_v2.DatabaseConnector", side_effect=_temp_connector):
        app.dependency_overrides[get_db] = lambda: DatabaseConnector(
            str(db_path), read_only=True
        )
        yield db_path
        app.dependency_overrides.clear()

@pytest.fixture
def mock_sync_reports():
    return [
        {
            "id": "1234-5678",
            "created_at": "2026-03-10T12:00:00",
            "report_type": "sync",
            "net_worth_before": 1000.0,
            "net_worth_after": 1050.0,
            "net_worth_change_pct": 0.05,
            "integrity_passed": 18,
            "integrity_total": 18,
            "alert": False
        }
    ]

@pytest.fixture
def mock_sync_detail():
    return {
        "id": "1234-5678",
        "created_at": "2026-03-10T12:00:00",
        "report_type": "sync",
        "net_worth_before": 1000.0,
        "net_worth_after": 1050.0,
        "net_worth_change_pct": 0.05,
        "asset_count_before": 20,
        "asset_count_after": 21,
        "by_source_before": {"Schwab_CSV": {"count": 5, "value": 500}},
        "by_source_after": {"Schwab_CSV": {"count": 6, "value": 550}},
        "integrity_passed": 18,
        "integrity_total": 18,
        "integrity_checks": [],
        "reader_counts": {},
        "warnings": [],
        "alert": False
    }

@pytest.fixture
def mock_on_demand_report():
    return OnDemandAuditReport(
        report_id="on-demand-123",
        source_discrepancies=[
            SourceDiscrepancy(
                source_system="Schwab_CSV",
                status="match",
                reader_asset_count=5,
                db_asset_count=5,
                reader_total_value=500.0,
                db_total_value=500.0,
                value_diff_pct=0.0,
                missing_in_db=[],
                missing_in_reader=[],
                value_mismatches=[],
                assets=[]
            )
        ],
        integrity={"all_passed": True, "passed_count": 18, "total_count": 18, "checks": []},
        overall_status="healthy"
    )

def test_get_audit_history(mock_sync_reports, isolated_db):
    # `total` comes from a real COUNT against sync_audit_reports, so seed a real
    # row rather than mocking DatabaseConnector.execute. The old version mocked
    # the whole execute method, which meant the count query was never actually
    # run — the test asserted a number it had itself supplied.
    seed = DatabaseConnector(str(isolated_db))
    seed.execute(
        "INSERT INTO sync_audit_reports (id, report_type) VALUES ('1234-5678', 'sync')"
    )
    seed.close()

    with patch("src.api.routes.audit_v2.get_latest_sync_audits", return_value=mock_sync_reports):
        response = client.get("/audit/v2/reports")
        assert response.status_code == 200
        data = response.json()
        assert "reports" in data
        assert len(data["reports"]) == 1
        assert data["total"] == 1
        assert data["reports"][0]["id"] == "1234-5678"

def test_get_latest_audit(mock_sync_reports):
    with patch("src.api.routes.audit_v2.get_latest_sync_audits", return_value=mock_sync_reports):
        response = client.get("/audit/v2/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "1234-5678"

def test_get_latest_audit_none():
    with patch("src.api.routes.audit_v2.get_latest_sync_audits", return_value=[]):
        response = client.get("/audit/v2/latest")
        assert response.status_code == 200
        assert response.json() is None

def test_get_integrity():
    from src.validation.data_integrity_gate import IntegrityReport, CheckResult
    mock_report = IntegrityReport(checks=[
        CheckResult(name="check1", passed=True, actual_value="ok", threshold="", details="")
    ])
    with patch("src.api.routes.audit_v2.run_integrity_checks", return_value=mock_report):
        response = client.get("/audit/v2/integrity")
        assert response.status_code == 200
        data = response.json()
        assert data["all_passed"] is True
        assert data["passed_count"] == 1
        assert data["total_count"] == 1
        assert len(data["checks"]) == 1
        assert data["checks"][0]["name"] == "check1"

def test_get_audit_report_detail(mock_sync_detail):
    with patch("src.api.routes.audit_v2.get_sync_audit_detail", return_value=mock_sync_detail):
        response = client.get("/audit/v2/reports/1234-5678")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "1234-5678"
        assert "by_source_before" in data

def test_get_audit_report_detail_not_found():
    with patch("src.api.routes.audit_v2.get_sync_audit_detail", return_value=None):
        response = client.get("/audit/v2/reports/not-found")
        assert response.status_code == 404

def test_run_on_demand_audit(mock_on_demand_report):
    with patch("src.api.routes.audit_v2.run_on_demand_audit", return_value=mock_on_demand_report):
        response = client.post("/audit/v2/on-demand")
        assert response.status_code == 200
        data = response.json()
        assert data["report_id"] == "on-demand-123"
        assert data["overall_status"] == "healthy"
        assert len(data["source_discrepancies"]) == 1
        assert data["source_discrepancies"][0]["source_system"] == "Schwab_CSV"
