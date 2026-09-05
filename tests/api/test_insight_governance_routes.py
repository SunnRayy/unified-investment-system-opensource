"""Tests for F6 Insight Library governance (PRD 2026-07-07 §F6, Batch B7):
promote gate, validated-cases, rule-layer, citations, governance-report,
checklist-export. Endpoints live in src/api/routes/ai_advisor.py under
/ai-advisor/insights/... (see promote_insight / add_validated_case /
set_rule_layer / add_citation / insights_governance_report / checklist_export).

These routes read/write via ai_advisor.py's _get_db_path() (a module-level
file path, NOT the get_db FastAPI dependency), so tests use a temp-file-backed
DuckDB and monkeypatch _DB_PATH, mirroring tests/api/test_decisions_endpoints.py
::file_backed_client.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes import ai_advisor as ai_advisor_routes
from src.database.connector import DatabaseConnector
from src.database.schema import bootstrap_database


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "governance.duckdb"
    bootstrap = DatabaseConnector(str(db_path))
    bootstrap_database(bootstrap)
    bootstrap.close()

    monkeypatch.setattr(ai_advisor_routes, "_DB_PATH", db_path)
    monkeypatch.setattr(
        "src.database.connector.resolve_db_path",
        lambda path="data/unified.duckdb": str(db_path) if path == "data/unified.duckdb" else path,
    )
    yield TestClient(app), db_path


def _insert_insight(db_path, **overrides) -> int:
    fields = {
        "category": "process",
        "title": "Test insight",
        "body": "Test insight body",
        "confidence": 0.3,
        "status": "raw",
        "recurrence_count": 1,
    }
    fields.update(overrides)
    conn = DatabaseConnector(str(db_path))
    try:
        conn.execute(
            """INSERT INTO ai_insights (category, title, body, confidence, status, recurrence_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [fields["category"], fields["title"], fields["body"],
             fields["confidence"], fields["status"], fields["recurrence_count"]],
        )
        insight_id = conn.execute(
            "SELECT id FROM ai_insights WHERE title = ? ORDER BY id DESC LIMIT 1", [fields["title"]]
        ).fetchone()[0]
    finally:
        conn.close()
    return insight_id


# ---------------------------------------------------------------------------
# Promote gate
# ---------------------------------------------------------------------------

def test_promote_denied_at_low_confidence_zero_validated_cases(client):
    test_client, db_path = client
    insight_id = _insert_insight(db_path, title="Weak insight", confidence=0.30)

    resp = test_client.post(f"/ai-advisor/insights/{insight_id}/promote")

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "confidence 30%" in detail
    assert "validated_cases 0" in detail


def test_promote_allowed_at_70_percent_confidence(client):
    test_client, db_path = client
    insight_id = _insert_insight(db_path, title="Strong confidence", confidence=0.70)

    resp = test_client.post(f"/ai-advisor/insights/{insight_id}/promote")

    assert resp.status_code == 200
    assert resp.json()["status"] == "recurring"


def test_promote_allowed_at_3_validated_cases_despite_low_confidence(client):
    test_client, db_path = client
    insight_id = _insert_insight(db_path, title="Evidence-backed insight", confidence=0.30)

    for i in range(3):
        r = test_client.post(
            f"/ai-advisor/insights/{insight_id}/validated-cases",
            json={"link": f"https://example.com/case{i}", "note": f"case {i}"},
        )
        assert r.status_code == 200

    resp = test_client.post(f"/ai-advisor/insights/{insight_id}/promote")
    assert resp.status_code == 200
    assert resp.json()["status"] == "recurring"


def test_promote_gate_applies_at_every_ladder_step_not_just_final(client):
    """A low-confidence, 0-validated-cases insight is blocked at raw->recurring
    already — not just at the final validated->principle hop."""
    test_client, db_path = client
    insight_id = _insert_insight(db_path, title="Blocked early", confidence=0.10, status="raw")

    resp = test_client.post(f"/ai-advisor/insights/{insight_id}/promote")
    assert resp.status_code == 422


def test_promote_unknown_id_404(client):
    test_client, _db_path = client
    resp = test_client.post("/ai-advisor/insights/999999/promote")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Validated cases
# ---------------------------------------------------------------------------

def test_add_validated_case_increments_and_appends_link(client):
    test_client, db_path = client
    insight_id = _insert_insight(db_path, title="Case tracked insight")

    resp = test_client.post(
        f"/ai-advisor/insights/{insight_id}/validated-cases",
        json={"link": "https://example.com/case1", "note": "confirmed in review"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["validated_cases"] == 1
    assert len(body["validated_case_links"]) == 1
    assert body["validated_case_links"][0]["link"] == "https://example.com/case1"
    assert body["validated_case_links"][0]["note"] == "confirmed in review"
    assert "added_at" in body["validated_case_links"][0]


def test_add_validated_case_unknown_id_404(client):
    test_client, _db_path = client
    resp = test_client.post(
        "/ai-advisor/insights/999999/validated-cases", json={"link": "x"}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Rule layer
# ---------------------------------------------------------------------------

def test_set_rule_layer_principle(client):
    test_client, db_path = client
    insight_id = _insert_insight(db_path, title="Principle candidate")

    resp = test_client.put(
        f"/ai-advisor/insights/{insight_id}/rule-layer", json={"rule_layer": "principle"}
    )
    assert resp.status_code == 200
    assert resp.json()["rule_layer"] == "principle"


def test_set_rule_layer_checklist_item(client):
    test_client, db_path = client
    insight_id = _insert_insight(db_path, title="Checklist candidate")

    resp = test_client.put(
        f"/ai-advisor/insights/{insight_id}/rule-layer", json={"rule_layer": "checklist_item"}
    )
    assert resp.status_code == 200
    assert resp.json()["rule_layer"] == "checklist_item"


def test_set_rule_layer_invalid_value_422(client):
    test_client, db_path = client
    insight_id = _insert_insight(db_path, title="Bad rule layer")

    resp = test_client.put(
        f"/ai-advisor/insights/{insight_id}/rule-layer", json={"rule_layer": "bogus"}
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Checklist export
# ---------------------------------------------------------------------------

def test_checklist_export_groups_by_category(client):
    test_client, db_path = client
    id_a = _insert_insight(db_path, title="Order placement rule A", category="order_placement")
    id_b = _insert_insight(db_path, title="FX rule B", category="fx")
    id_c = _insert_insight(db_path, title="Not a checklist item", category="fx")

    test_client.put(f"/ai-advisor/insights/{id_a}/rule-layer", json={"rule_layer": "checklist_item"})
    test_client.put(f"/ai-advisor/insights/{id_b}/rule-layer", json={"rule_layer": "checklist_item"})
    test_client.put(f"/ai-advisor/insights/{id_c}/rule-layer", json={"rule_layer": "principle"})

    resp = test_client.get("/ai-advisor/insights/checklist-export")
    assert resp.status_code == 200
    assert "markdown" in resp.headers.get("content-type", "")
    markdown = resp.text

    assert "## order_placement" in markdown
    assert "## fx" in markdown
    assert "Order placement rule A" in markdown
    assert "FX rule B" in markdown
    assert "Not a checklist item" not in markdown


def test_checklist_export_empty_state(client):
    test_client, _db_path = client
    resp = test_client.get("/ai-advisor/insights/checklist-export")
    assert resp.status_code == 200
    assert "No checklist_item insights found" in resp.text


# ---------------------------------------------------------------------------
# Citations + governance report
# ---------------------------------------------------------------------------

def test_citations_post_and_list(client):
    test_client, db_path = client
    insight_id = _insert_insight(db_path, title="Cited rule", status="principle")

    resp = test_client.post(
        f"/ai-advisor/insights/{insight_id}/citations",
        json={"memo_id": "MEMO-2026-07-01", "note": "applied in Q3 memo"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["memo_id"] == "MEMO-2026-07-01"
    assert body["quarter"].endswith("-Q" + body["quarter"].split("-Q")[1])

    list_resp = test_client.get(f"/ai-advisor/insights/{insight_id}/citations")
    assert list_resp.status_code == 200
    citations = list_resp.json()
    assert len(citations) == 1
    assert citations[0]["memo_id"] == "MEMO-2026-07-01"


def test_governance_report_zero_citation_rule_listed_and_pairing_warning(client):
    test_client, db_path = client
    from datetime import datetime
    now = datetime.now()
    year, quarter = now.year, (now.month - 1) // 3 + 1

    # A principle-status insight promoted (by direct DB write, simulating a
    # same-quarter promotion) with zero citations this quarter.
    conn = DatabaseConnector(str(db_path))
    try:
        conn.execute(
            """INSERT INTO ai_insights (category, title, body, confidence, status, recurrence_count, updated_at)
               VALUES ('process', 'Uncited principle', 'body', 0.9, 'principle', 1, CURRENT_TIMESTAMP)"""
        )
    finally:
        conn.close()

    resp = test_client.get("/ai-advisor/insights/governance-report", params={"year": year, "quarter": quarter})
    assert resp.status_code == 200
    body = resp.json()

    assert body["promoted_this_quarter"] >= 1
    titles = [r["title"] for r in body["zero_citation_rules"]]
    assert "Uncited principle" in titles
    assert body["pairing_warning"] is True
    assert "basis" in body and len(body["basis"]) > 0


def test_governance_report_no_pairing_warning_when_no_promotions(client):
    test_client, _db_path = client
    resp = test_client.get("/ai-advisor/insights/governance-report", params={"year": 2020, "quarter": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["promoted_this_quarter"] == 0
    assert body["pairing_warning"] is False


def test_governance_report_invalid_quarter_422(client):
    test_client, _db_path = client
    resp = test_client.get("/ai-advisor/insights/governance-report", params={"year": 2026, "quarter": 5})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# List endpoint additive fields
# ---------------------------------------------------------------------------

def test_list_insights_includes_promote_eligibility_fields(client):
    test_client, db_path = client
    _insert_insight(db_path, title="List field check", confidence=0.30)

    resp = test_client.get("/ai-advisor/insights")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    item = items[0]
    assert item["validated_cases"] == 0
    assert item["rule_layer"] is None
    assert item["promote_eligible"] is False
    assert "confidence 30%" in item["promote_blocked_reason"]
