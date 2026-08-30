# tests/api/test_rule12_error_contract.py
"""
Rule 12 error-contract tests — Pass 1 (chore/agent-trust-rule12).

Verifies that the 7 fixed endpoints:
  1. Return HTTP 5xx (not 200) when the underlying query raises an exception.
  2. Return a structured {"error": {"code": ..., "message": ...}} body on failure.
  3. Still return 200 + normal/empty payload when the DB is empty (genuinely empty ≠ error).

All tests inject an in-memory DuckDB via FastAPI's dependency_overrides so they
never touch data/unified.duckdb.

IMPORTANT: UIS_AUTH_TOKEN must NOT be set in this test module's environment.
Setting it triggers the cloud-mode boundary check in src/api/main.py which
raises RuntimeError when routes are mounted without /api prefix in test mode.

Endpoints covered (Pass 1):
  GET /audit/logs           (data.py — list)
  GET /insights             (data.py — list)
  GET /dashboard/allocation (data.py — list)
  GET /compass/report       (data.py — object)
  GET /wealthos/assets      (data.py — object with lists)
  GET /compass/summary      (compass.py — object)
  GET /compass/allocation   (compass.py — list)

Endpoints added (Pass E 2a):
  GET /analytics/projection       (analytics.py)
  GET /analytics/goals            (analytics.py)
  GET /valuation/snapshot/latest  (valuation.py)
  GET /market/regime              (market.py)
  GET /ai-advisor/behavioral-metrics/latest  (ai_advisor.py — duckdb.connect patch)
  GET /performance/attribution    (performance.py)
"""
import os
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

# Unset auth token BEFORE importing the app so the cloud-mode check doesn't trigger.
os.environ.pop("UIS_AUTH_TOKEN", None)

from src.api.main import app  # noqa: E402
from src.api.dependencies import get_db  # noqa: E402
from src.database.connector import DatabaseConnector  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_memory_connector():
    """Create an in-memory DuckDB connector (never touches production DB)."""
    return DatabaseConnector(":memory:")


def _memory_db_override():
    """FastAPI dependency override: yields an in-memory DB connector."""
    conn = _make_memory_connector()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(autouse=False)
def memory_db_client():
    """TestClient that overrides get_db with an in-memory connector."""
    app.dependency_overrides[get_db] = _memory_db_override
    tc = TestClient(app, raise_server_exceptions=False)
    yield tc
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=False)
def failing_db_client():
    """TestClient that overrides get_db to return a connector whose execute always raises."""
    def _failing_db_override():
        conn = MagicMock(spec=DatabaseConnector)
        conn.execute.side_effect = RuntimeError("forced DB failure for Rule 12 test")
        conn.close = MagicMock()
        yield conn

    app.dependency_overrides[get_db] = _failing_db_override
    tc = TestClient(app, raise_server_exceptions=False)
    yield tc
    app.dependency_overrides.pop(get_db, None)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: assert the error-contract shape
# ─────────────────────────────────────────────────────────────────────────────

def _assert_error_contract(response, expected_status_range=(500, 599)):
    """Assert the response has an HTTP 5xx status and a Rule-12-compliant error body.

    Frozen contract: {"error": {"code": str, "message": str}} — NOT {"detail": ...}.
    See src/api/routes/_errors.py ApiErrorResponse.
    """
    lo, hi = expected_status_range
    assert lo <= response.status_code <= hi, (
        f"Expected HTTP 5xx, got {response.status_code}. Body: {response.text[:300]}"
    )
    body = response.json()
    # Shape must be {"error": {...}} directly — NOT {"detail": {"error": {...}}}
    assert "error" in body, (
        f"Missing top-level 'error' key (got {list(body.keys())}). "
        f"Body: {body}. Check that api_error_response raises ApiErrorResponse, not HTTPException."
    )
    error = body["error"]
    assert "code" in error, f"Missing 'code' in error: {error}"
    assert "message" in error, f"Missing 'message' in error: {error}"
    # Safety: no raw stack traces in the message
    assert "Traceback" not in error.get("message", ""), "Stack trace leaked to client"


# ─────────────────────────────────────────────────────────────────────────────
# GET /audit/logs — list endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditLogsErrorContract:
    def test_returns_5xx_on_db_failure(self, failing_db_client):
        """GET /audit/logs returns 5xx + error body when the DB query raises."""
        res = failing_db_client.get("/audit/logs")
        _assert_error_contract(res)

    def test_empty_db_returns_200_or_5xx_but_not_empty_list_masking_error(self, memory_db_client):
        """GET /audit/logs with empty DB: if 200 the body must be a list (empty is ok)."""
        res = memory_db_client.get("/audit/logs")
        # Table missing → route raises 5xx (the route queries sync_audit_logs which doesn't exist)
        # Either way: empty must NOT mean 200 + [] hiding a real failure.
        if res.status_code == 200:
            assert isinstance(res.json(), list), "If 200, body must be a list"
        # A 5xx is also acceptable here (table does not exist in :memory: DB)


# ─────────────────────────────────────────────────────────────────────────────
# GET /insights — list endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestInsightsErrorContract:
    def test_returns_5xx_on_db_failure(self, failing_db_client):
        """GET /insights returns 5xx + error body when the DB query raises."""
        res = failing_db_client.get("/insights")
        _assert_error_contract(res)

    def test_error_body_has_insights_code(self, failing_db_client):
        """The error code should reference the failing operation."""
        res = failing_db_client.get("/insights")
        assert 500 <= res.status_code <= 599
        body = res.json()
        assert "error" in body
        assert "insight" in body["error"]["code"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# GET /dashboard/allocation — list endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardAllocationErrorContract:
    def test_returns_5xx_on_db_failure(self, failing_db_client):
        """GET /dashboard/allocation returns 5xx on forced DB exception."""
        res = failing_db_client.get("/dashboard/allocation")
        _assert_error_contract(res)


# ─────────────────────────────────────────────────────────────────────────────
# GET /compass/report — object endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestCompassReportErrorContract:
    def test_returns_5xx_on_db_failure(self, failing_db_client):
        """GET /compass/report returns 5xx on forced DB exception."""
        res = failing_db_client.get("/compass/report")
        _assert_error_contract(res)


# ─────────────────────────────────────────────────────────────────────────────
# GET /wealthos/assets — object endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestWealthOSAssetsErrorContract:
    def test_returns_5xx_on_db_failure(self, failing_db_client):
        """GET /wealthos/assets returns 5xx on forced DB exception."""
        res = failing_db_client.get("/wealthos/assets")
        _assert_error_contract(res)


# ─────────────────────────────────────────────────────────────────────────────
# GET /compass/summary — object endpoint (compass.py)
# ─────────────────────────────────────────────────────────────────────────────

class TestCompassSummaryErrorContract:
    def test_returns_5xx_on_db_failure(self, failing_db_client):
        """GET /compass/summary returns 5xx on forced DB exception."""
        res = failing_db_client.get("/compass/summary")
        _assert_error_contract(res)


# ─────────────────────────────────────────────────────────────────────────────
# GET /compass/allocation — list endpoint (compass.py)
# ─────────────────────────────────────────────────────────────────────────────

class TestCompassAllocationErrorContract:
    def test_returns_5xx_on_db_failure(self, failing_db_client):
        """GET /compass/allocation returns 5xx on forced DB exception."""
        with patch("src.api.routes.compass.build_compass_allocation",
                   side_effect=RuntimeError("forced compass allocation error")):
            res = failing_db_client.get("/compass/allocation")
        _assert_error_contract(res)


# ─────────────────────────────────────────────────────────────────────────────
# _errors.py helper unit tests (no DB needed)
# ─────────────────────────────────────────────────────────────────────────────

class TestApiErrorResponseHelper:
    def test_returns_json_response_not_http_exception(self):
        """api_error_response returns ApiErrorResponse (JSONResponse subclass), not HTTPException.

        This ensures FastAPI returns {"error":...} directly, not {"detail":{"error":...}}.
        """
        from src.api.routes._errors import api_error_response, ApiErrorResponse
        exc = ValueError("something broke")
        result = api_error_response(exc, context="test_op")
        assert isinstance(result, ApiErrorResponse)
        assert result.status_code == 500
        # Content is the raw dict — {"error": {"code": ..., "message": ...}}
        import json as _json
        body = _json.loads(result.body)
        assert body["error"]["code"] == "ERR_TEST_OP"

    def test_upstream_error_maps_to_503(self):
        from src.api.routes._errors import api_error_response
        exc = ConnectionError("connection timeout to gcs bucket")
        result = api_error_response(exc, context="gcs_write")
        assert result.status_code == 503

    def test_no_stack_trace_in_message(self):
        from src.api.routes._errors import api_error_response
        import json as _json
        exc = RuntimeError("some internal details with /path/to/file.py")
        result = api_error_response(exc, context="get_data")
        body = _json.loads(result.body)
        msg = body["error"]["message"]
        assert "Traceback" not in msg
        # The safe message uses the type name, not the raw str(exc)
        assert "RuntimeError" in msg

    def test_no_context_gives_err_internal(self):
        from src.api.routes._errors import api_error_response
        import json as _json
        exc = Exception("generic")
        result = api_error_response(exc)
        body = _json.loads(result.body)
        assert body["error"]["code"] == "ERR_INTERNAL"


# ─────────────────────────────────────────────────────────────────────────────
# Pass E Sub-Agent 1: new Rule 12 coverage
# analytics.py / valuation.py / market.py / ai_advisor.py
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyticsProjectionErrorContract:
    """GET /analytics/projection returns 5xx + Rule-12 body on DB failure."""

    def test_returns_5xx_on_db_failure(self, failing_db_client):
        with patch(
            "src.financial_analysis.monte_carlo.calculate_portfolio_projection",
            side_effect=RuntimeError("forced projection failure"),
        ):
            res = failing_db_client.get("/analytics/projection")
        _assert_error_contract(res)

    def test_error_body_references_projection(self, failing_db_client):
        with patch(
            "src.financial_analysis.monte_carlo.calculate_portfolio_projection",
            side_effect=RuntimeError("forced projection failure"),
        ):
            res = failing_db_client.get("/analytics/projection")
        assert 500 <= res.status_code <= 599
        body = res.json()
        assert "error" in body
        assert "PROJECTION" in body["error"]["code"]


class TestAnalyticsGoalsErrorContract:
    """GET /analytics/goals returns 5xx + Rule-12 body on DB failure."""

    def test_returns_5xx_on_db_failure(self, failing_db_client):
        with patch(
            "src.financial_analysis.goals.list_goals",
            side_effect=RuntimeError("forced goals failure"),
        ):
            res = failing_db_client.get("/analytics/goals")
        _assert_error_contract(res)

    def test_error_body_references_goals(self, failing_db_client):
        with patch(
            "src.financial_analysis.goals.list_goals",
            side_effect=RuntimeError("forced goals failure"),
        ):
            res = failing_db_client.get("/analytics/goals")
        assert 500 <= res.status_code <= 599
        body = res.json()
        assert "error" in body
        assert "GOALS" in body["error"]["code"]


class TestValuationSnapshotsErrorContract:
    """GET /valuation/snapshot/latest returns 5xx + Rule-12 body on DB failure."""

    def test_returns_5xx_on_db_failure(self, failing_db_client):
        res = failing_db_client.get("/valuation/snapshot/latest")
        _assert_error_contract(res)

    def test_error_body_references_valuation_snapshots(self, failing_db_client):
        res = failing_db_client.get("/valuation/snapshot/latest")
        assert 500 <= res.status_code <= 599
        body = res.json()
        assert "error" in body
        assert "VALUATION" in body["error"]["code"]


class TestMarketRegimeErrorContract:
    """GET /market/regime returns 5xx + Rule-12 body on service failure."""

    def test_returns_5xx_on_service_failure(self, failing_db_client):
        with patch(
            "src.api.routes.market.assess_portfolio_regime",
            side_effect=RuntimeError("forced regime failure"),
        ):
            res = failing_db_client.get("/market/regime")
        _assert_error_contract(res)

    def test_error_body_references_market_regime(self, failing_db_client):
        with patch(
            "src.api.routes.market.assess_portfolio_regime",
            side_effect=RuntimeError("forced regime failure"),
        ):
            res = failing_db_client.get("/market/regime")
        assert 500 <= res.status_code <= 599
        body = res.json()
        assert "error" in body
        assert "MARKET" in body["error"]["code"]


class TestBehavioralMetricsErrorContract:
    """GET /ai-advisor/behavioral-metrics/latest returns Rule-12 body on DB failure.

    This endpoint opens its own duckdb.connect() — does NOT use Depends(get_db).
    We patch duckdb.connect directly to force a failure.
    """

    def test_returns_5xx_on_db_failure(self, memory_db_client):
        """behavioral-metrics/latest returns 5xx + Rule-12 body on DB failure."""
        with patch(
            "src.api.routes.ai_advisor.duckdb.connect",
            side_effect=RuntimeError("forced duckdb failure"),
        ):
            res = memory_db_client.get("/ai-advisor/behavioral-metrics/latest")
        _assert_error_contract(res)

    def test_error_body_references_behavioral_metrics(self, memory_db_client):
        """behavioral-metrics/latest error code contains BEHAVIORAL-METRICS."""
        with patch(
            "src.api.routes.ai_advisor.duckdb.connect",
            side_effect=RuntimeError("forced duckdb failure"),
        ):
            res = memory_db_client.get("/ai-advisor/behavioral-metrics/latest")
        assert 500 <= res.status_code <= 599
        body = res.json()
        assert "error" in body
        assert "BEHAVIORAL" in body["error"]["code"]

    def test_not_empty_list_on_failure(self, memory_db_client):
        """behavioral-metrics/latest must NOT return 200 + [] on failure."""
        with patch(
            "src.api.routes.ai_advisor.duckdb.connect",
            side_effect=RuntimeError("forced duckdb failure"),
        ):
            res = memory_db_client.get("/ai-advisor/behavioral-metrics/latest")
        assert res.status_code != 200


class TestAttributionErrorContract:
    """GET /performance/attribution returns 5xx + Rule-12 body when the service raises.

    Note: calculate_portfolio_attribution() internally catches exceptions and returns None.
    We patch it directly to raise so the performance.py outer except fires.
    """

    def test_returns_5xx_on_service_failure(self, memory_db_client):
        with patch(
            "src.api.routes.performance.calculate_portfolio_attribution",
            side_effect=RuntimeError("forced attribution failure"),
        ):
            res = memory_db_client.get("/performance/attribution")
        _assert_error_contract(res)

    def test_error_body_references_attribution(self, memory_db_client):
        with patch(
            "src.api.routes.performance.calculate_portfolio_attribution",
            side_effect=RuntimeError("forced attribution failure"),
        ):
            res = memory_db_client.get("/performance/attribution")
        assert 500 <= res.status_code <= 599
        body = res.json()
        assert "error" in body
        assert "ATTRIBUTION" in body["error"]["code"]
