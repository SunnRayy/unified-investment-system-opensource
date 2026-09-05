"""Tests for F1.2/F1.3 process-based trade verification (PRD 2026-07-07).

Covers:
  - src/services/process_scorer.py pure functions (evaluate_process, bucket_display_state,
    outcome_info, set_process_checks, compute_process_aggregates, compute_quarterly_outcome_report)
  - flag-gated behavior of GET /decisions/scorecard, GET /decisions/stats,
    GET /decisions/quarterly-outcome-report
  - PUT /ai-advisor/trades/{id}/process-checks
  - the F1 acceptance-criteria regression fixture: AMZN RSU compliance sells +
    gold ratio buy must NEVER surface an emotive verdict string when the flag is on.

Flag-off regression proof lives in tests/api/test_decisions_endpoints.py and
tests/api/test_ai_advisor_verify.py — this file only turns the flag on via a
monkeypatched VerificationConfig (never by editing config/verification.yaml).
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes import ai_advisor as ai_advisor_routes
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services import process_scorer as ps
from src.services.verification_config import ProcessVerificationSection, VerificationConfig

_TODAY = date.today()
_AGE_100D = (_TODAY - timedelta(days=100)).isoformat()
_AGE_200D = (_TODAY - timedelta(days=200)).isoformat()

_EMOTIVE_STRINGS = ("good_call", "regret", "missed_opportunity", "bullet_dodged")


def execute_migration(conn, migration_path: Path):
    migration_sql = migration_path.read_text()
    lines = [line for line in migration_sql.split("\n") if not line.strip().startswith("--")]
    clean_sql = "\n".join(lines)
    for stmt in clean_sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


@pytest.fixture
def flag_on_cfg():
    """A VerificationConfig with process_verification.enabled=True, default window."""
    return VerificationConfig(
        process_verification=ProcessVerificationSection(enabled=True, outcome_window_days=180)
    )


@pytest.fixture
def process_client(tmp_path, monkeypatch):
    """Full app, file-backed DB with migrations applied, flag forced ON via monkeypatch
    of load_verification_config in both route modules (config/verification.yaml on disk
    is never edited — Constraint: flag stays false in config)."""
    from src.api.dependencies import get_db

    db_path = tmp_path / "process_verification.duckdb"
    bootstrap = DatabaseConnector(str(db_path))
    initialize_schema(bootstrap)
    for mig in sorted(Path("src/database/migrations").glob("*.sql")):
        try:
            execute_migration(bootstrap, mig)
        except Exception:
            pass
    bootstrap.run_migrations()
    bootstrap.close()

    def override_get_db():
        conn = DatabaseConnector(str(db_path))
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(ai_advisor_routes, "_DB_PATH", db_path)
    monkeypatch.setattr(
        "src.database.connector.resolve_db_path",
        lambda path="data/unified.duckdb": str(db_path) if path == "data/unified.duckdb" else path,
    )

    forced_cfg = VerificationConfig(
        process_verification=ProcessVerificationSection(enabled=True, outcome_window_days=180)
    )
    import src.api.routes.decisions as decisions_routes

    monkeypatch.setattr(decisions_routes, "load_verification_config", lambda: forced_cfg)
    monkeypatch.setattr(ai_advisor_routes, "load_verification_config", lambda: forced_cfg)

    yield TestClient(app), db_path

    app.dependency_overrides.clear()


def _insert_trade(conn, **kwargs):
    defaults = dict(
        log_date="2026-01-01",
        asset_id="US_STK_TEST",
        asset_name="Test",
        action="Buy",
        price=100.0,
        quantity=10.0,
        amount=1000.0,
        suggestion_source="manual",
        verification_status="verified",
        verification_result=None,
        verdict=None,
        outcome_pct=None,
        decision_reason=None,
        rule_bucket=None,
        memo_id=None,
        process_authorized=None,
        process_params_ok=None,
        process_data_verified=None,
    )
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    row = conn.execute(
        f"INSERT INTO trade_logs ({cols}) VALUES ({placeholders}) RETURNING id",
        list(defaults.values()),
    ).fetchone()
    return int(row[0])


# ---------------------------------------------------------------------------
# process_scorer pure-function unit tests
# ---------------------------------------------------------------------------


class TestEvaluateProcess:
    def test_all_true_is_pass(self):
        result = ps.evaluate_process(
            {"process_authorized": True, "process_params_ok": True, "process_data_verified": True}
        )
        assert result == {"status": "PASS", "failing_checks": [], "checked": True}

    def test_any_false_is_fail_reproducing_june_2026_amzn_case(self):
        """Regression: params_ok=False (single high limit order, no backstop) -> FAIL
        with failing_checks=['params_ok'] — PRD F1 acceptance criterion."""
        result = ps.evaluate_process(
            {"process_authorized": True, "process_params_ok": False, "process_data_verified": None}
        )
        assert result["status"] == "FAIL"
        assert result["failing_checks"] == ["params_ok"]
        assert result["checked"] is True

    def test_all_null_is_unscored(self):
        result = ps.evaluate_process(
            {"process_authorized": None, "process_params_ok": None, "process_data_verified": None}
        )
        assert result["status"] == "UNSCORED"
        assert result["checked"] is False

    def test_partial_true_no_false_is_unscored(self):
        result = ps.evaluate_process(
            {"process_authorized": True, "process_params_ok": None, "process_data_verified": None}
        )
        assert result["status"] == "UNSCORED"


class TestBucketDisplayState:
    def test_compliance_pass_is_compliant(self):
        process = ps.evaluate_process(
            {"process_authorized": True, "process_params_ok": True, "process_data_verified": True}
        )
        display = ps.bucket_display_state("compliance", process)
        assert display["state"] == "compliant"
        assert display["outcome_eligible"] is False

    def test_ratio_fail_is_violation(self):
        process = ps.evaluate_process(
            {"process_authorized": False, "process_params_ok": True, "process_data_verified": True}
        )
        display = ps.bucket_display_state("ratio", process)
        assert display["state"] == "violation"

    def test_liquidity_unscored_is_unreviewed(self):
        process = ps.evaluate_process(
            {"process_authorized": None, "process_params_ok": None, "process_data_verified": None}
        )
        display = ps.bucket_display_state("liquidity", process)
        assert display["state"] == "unreviewed"

    def test_value_bucket_is_outcome_eligible(self):
        process = ps.evaluate_process(
            {"process_authorized": True, "process_params_ok": True, "process_data_verified": True}
        )
        display = ps.bucket_display_state("value", process)
        assert display["outcome_eligible"] is True
        assert display["state"] == "compliant"

    def test_missing_bucket_defaults_to_value(self):
        process = ps.evaluate_process({})
        display = ps.bucket_display_state(None, process)
        assert display["rule_bucket"] == "value"


class TestOutcomeInfo:
    def test_non_value_bucket_is_n_a_by_rule(self):
        for bucket in ("compliance", "ratio", "liquidity"):
            info = ps.outcome_info(
                {"rule_bucket": bucket, "log_date": _AGE_200D, "outcome_pct": -8.5}, _TODAY, 180
            )
            assert info["status"] == "n_a_by_rule"
            assert "reason" in info
            assert "outcome_pct" not in info

    def test_value_bucket_under_window_is_maturing(self):
        info = ps.outcome_info(
            {"rule_bucket": "value", "log_date": _AGE_100D, "outcome_pct": None}, _TODAY, 180
        )
        assert info["status"] == "maturing"

    def test_value_bucket_over_window_with_outcome_is_evaluated_no_verdict_key(self):
        info = ps.outcome_info(
            {"rule_bucket": "value", "log_date": _AGE_200D, "outcome_pct": 12.345}, _TODAY, 180
        )
        assert info["status"] == "evaluated"
        assert info["outcome_pct"] == 12.345
        assert "verdict" not in info
        assert "good_call" not in json.dumps(info)

    def test_value_bucket_over_window_no_price_is_insufficient_data(self):
        info = ps.outcome_info(
            {"rule_bucket": "value", "log_date": _AGE_200D, "outcome_pct": None}, _TODAY, 180
        )
        assert info["status"] == "insufficient_data"


class TestSetProcessChecksAndDefaults:
    def test_set_process_checks_partial_update(self, tmp_path):
        db_path = tmp_path / "spc.duckdb"
        conn = DatabaseConnector(str(db_path))
        initialize_schema(conn)
        trade_id = _insert_trade(conn, process_authorized=True)

        ps.set_process_checks(conn, trade_id, params_ok=True)
        row = conn.execute(
            "SELECT process_authorized, process_params_ok, process_data_verified, process_checked_at"
            " FROM trade_logs WHERE id = ?",
            [trade_id],
        ).fetchone()
        assert row[0] is True  # untouched by this call
        assert row[1] is True
        assert row[2] is None
        assert row[3] is not None
        conn.close()

    def test_suggest_process_defaults_memo_id_present(self):
        defaults = ps.suggest_process_defaults({"memo_id": "2026-Q2-010-v2", "suggestion_source": None})
        assert defaults["authorized"] is True
        assert defaults["params_ok"] is None
        assert defaults["data_verified"] is None

    def test_suggest_process_defaults_dca_source(self):
        defaults = ps.suggest_process_defaults({"memo_id": None, "suggestion_source": "dca"})
        assert defaults["authorized"] is True

    def test_suggest_process_defaults_no_signal_is_none(self):
        defaults = ps.suggest_process_defaults({"memo_id": None, "suggestion_source": "manual"})
        assert defaults["authorized"] is None


class TestQuarterlyOutcomeReport:
    def test_hit_rate_math_and_grouping(self, tmp_path):
        db_path = tmp_path / "quarterly.duckdb"
        conn = DatabaseConnector(str(db_path))
        initialize_schema(conn)

        # Two trades under memo A, both matured (200d+ old): one positive, one negative.
        _insert_trade(
            conn, log_date=_AGE_200D, memo_id="MEMO_A", rule_bucket="value", outcome_pct=10.0,
            asset_id="US_STK_A",
        )
        _insert_trade(
            conn, log_date=_AGE_200D, memo_id="MEMO_A", rule_bucket="value", outcome_pct=-5.0,
            asset_id="US_STK_B",
        )
        # One trade under memo B, too young to evaluate.
        _insert_trade(
            conn, log_date=_AGE_100D, memo_id="MEMO_B", rule_bucket="value", outcome_pct=None,
            asset_id="US_STK_C",
        )
        conn.close()

        conn = DatabaseConnector(str(db_path))
        # Both fixtures fall in the same quarter as _AGE_200D/_AGE_100D — resolve dynamically.
        q_date = date.fromisoformat(_AGE_200D)
        quarter = (q_date.month - 1) // 3 + 1
        report = ps.compute_quarterly_outcome_report(conn, q_date.year, quarter, today=_TODAY)
        conn.close()

        memo_a = next(m for m in report["memos"] if m["memo_id"] == "MEMO_A")
        assert memo_a["trades"] == 2
        assert memo_a["evaluated"] == 2
        assert memo_a["avg_outcome_pct"] == 2.5
        assert memo_a["hit_rate"] == 50.0

    def test_insufficient_data_when_nothing_evaluated(self, tmp_path):
        db_path = tmp_path / "quarterly_empty.duckdb"
        conn = DatabaseConnector(str(db_path))
        initialize_schema(conn)
        _insert_trade(
            conn, log_date=_AGE_100D, memo_id="MEMO_X", rule_bucket="value", outcome_pct=None,
        )
        q_date = date.fromisoformat(_AGE_100D)
        quarter = (q_date.month - 1) // 3 + 1
        report = ps.compute_quarterly_outcome_report(conn, q_date.year, quarter, today=_TODAY)
        conn.close()

        assert report.get("insufficient_data") is True
        assert report["total_evaluated"] == 0


# ---------------------------------------------------------------------------
# API — flag ON behavior
# ---------------------------------------------------------------------------


class TestScorecardFlagOn:
    def test_scorecard_never_serializes_emotive_verdict_strings(self, process_client):
        """PRD F1 acceptance: zero occurrences of good_call/regret/missed_opportunity/
        bullet_dodged anywhere in the scorecard response when the flag is on — reproducing
        AMZN RSU compliance sells (missed_opportunity, archived) and a gold ratio buy
        (regret, archived)."""
        client, db_path = process_client
        conn = DatabaseConnector(str(db_path))
        # AMZN RSU forced sells under Project Exodus — compliance bucket, legacy verdict
        # already archived to verdict_archived (simulating migration 010's backfill).
        for d in ("2026-03-16", "2026-04-08", "2026-04-09"):
            _insert_trade(
                conn, log_date=d, asset_id="RSU_AMZN", action="Sell",
                rule_bucket="compliance", verdict=None,
                verification_result="RSU 强制变现", verification_status="verified",
            )
        # Gold ratio buy — legacy verdict archived as 'regret'.
        _insert_trade(
            conn, log_date="2026-06-05", asset_id="ALTS_Paper_Gold", action="Buy",
            rule_bucket="ratio", verdict=None,
            verification_result="按比例买入黄金", verification_status="verified",
        )
        conn.close()

        response = client.get("/decisions/scorecard?limit=10")
        assert response.status_code == 200
        body_text = response.text
        for word in _EMOTIVE_STRINGS:
            assert word not in body_text, f"emotive verdict string '{word}' leaked into scorecard response"

        items = response.json()["items"]
        assert len(items) == 4
        for item in items:
            assert item["verdict"] is None
            assert item["rule_bucket"] in ("compliance", "ratio")
            assert item["process"]["state"] in ("compliant", "violation", "unreviewed")
            assert item["outcome_info"]["status"] == "n_a_by_rule"

    def test_value_bucket_maturing_vs_evaluated(self, process_client):
        client, db_path = process_client
        conn = DatabaseConnector(str(db_path))
        _insert_trade(
            conn, log_date=_AGE_100D, asset_id="US_STK_MSFT", action="Buy",
            rule_bucket="value", verification_result="持有观察", verification_status="verified",
        )
        _insert_trade(
            conn, log_date=_AGE_200D, asset_id="US_STK_BRKB", action="Buy",
            rule_bucket="value", outcome_pct=7.25,
            verification_result="持有观察", verification_status="verified",
        )
        conn.close()

        response = client.get("/decisions/scorecard?limit=10")
        assert response.status_code == 200
        items = response.json()["items"]
        by_asset = {item["asset_id"]: item for item in items}

        assert by_asset["US_STK_MSFT"]["outcome_info"]["status"] == "maturing"
        assert "outcome_pct" not in by_asset["US_STK_MSFT"]["outcome_info"]

        evaluated = by_asset["US_STK_BRKB"]["outcome_info"]
        assert evaluated["status"] == "evaluated"
        assert evaluated["outcome_pct"] == 7.25
        assert by_asset["US_STK_BRKB"]["verdict"] is None

    def test_process_fail_fixture_params_ok_false(self, process_client):
        """June 2026 AMZN case reproduction at the API level: params_ok=False -> FAIL,
        bucket_display_state -> 'violation'."""
        client, db_path = process_client
        conn = DatabaseConnector(str(db_path))
        _insert_trade(
            conn, log_date="2026-06-01", asset_id="RSU_AMZN", action="Sell",
            rule_bucket="compliance", process_authorized=True, process_params_ok=False,
            verification_result="单一高限价单，无保底单", verification_status="verified",
        )
        conn.close()

        response = client.get("/decisions/scorecard?limit=10")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["process"]["status"] == "FAIL"
        assert item["process"]["failing_checks"] == ["params_ok"]
        assert item["process"]["state"] == "violation"


class TestStatsFlagOn:
    def test_process_verification_aggregates_present(self, process_client):
        client, db_path = process_client
        conn = DatabaseConnector(str(db_path))
        _insert_trade(
            conn, log_date="2026-01-01", asset_id="RSU_AMZN", action="Sell",
            rule_bucket="compliance", process_authorized=True, process_params_ok=True,
            process_data_verified=True, verification_result="按计划",
        )
        _insert_trade(
            conn, log_date="2026-01-02", asset_id="ALTS_Paper_Gold", action="Buy",
            rule_bucket="ratio", process_authorized=False, verification_result="临时决定",
        )
        conn.close()

        response = client.get("/decisions/stats")
        assert response.status_code == 200
        data = response.json()
        assert "process_verification" in data
        pv = data["process_verification"]
        assert pv["by_bucket"]["compliance"]["compliant"] == 1
        assert pv["by_bucket"]["ratio"]["violation"] == 1
        assert pv["overall"]["process_pass"] == 1
        assert pv["overall"]["process_fail"] == 1
        assert "value_outcome_coverage" in pv


class TestQuarterlyOutcomeReportEndpoint:
    def test_endpoint_returns_report(self, process_client):
        client, db_path = process_client
        conn = DatabaseConnector(str(db_path))
        q_date = date.fromisoformat(_AGE_200D)
        _insert_trade(
            conn, log_date=_AGE_200D, memo_id="MEMO_Q", rule_bucket="value", outcome_pct=4.0,
            verification_result="按计划",
        )
        conn.close()
        quarter = (q_date.month - 1) // 3 + 1

        response = client.get(f"/decisions/quarterly-outcome-report?year={q_date.year}&quarter={quarter}")
        assert response.status_code == 200
        data = response.json()
        assert data["total_trades"] == 1
        assert any(m["memo_id"] == "MEMO_Q" for m in data["memos"])

    def test_endpoint_400_on_bad_quarter(self, process_client):
        client, _ = process_client
        response = client.get("/decisions/quarterly-outcome-report?year=2026&quarter=5")
        assert response.status_code == 400

    def test_endpoint_works_with_flag_off(self, tmp_path, monkeypatch):
        """Quarterly report exposes no emotive verdicts by construction, so it works
        regardless of the flag (task spec requirement)."""
        from src.api.dependencies import get_db

        db_path = tmp_path / "quarterly_flag_off.duckdb"
        bootstrap = DatabaseConnector(str(db_path))
        initialize_schema(bootstrap)
        bootstrap.close()

        def override_get_db():
            conn = DatabaseConnector(str(db_path))
            try:
                yield conn
            finally:
                conn.close()

        app.dependency_overrides[get_db] = override_get_db
        try:
            client = TestClient(app)
            # Flag is whatever config/verification.yaml says (false) — no monkeypatch here.
            response = client.get("/decisions/quarterly-outcome-report?year=2026&quarter=1")
            assert response.status_code == 200
            assert response.json().get("insufficient_data") is True
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# PUT /ai-advisor/trades/{id}/process-checks
# ---------------------------------------------------------------------------


class TestProcessChecksEndpoint:
    def test_put_process_checks_sets_fields(self, process_client):
        client, db_path = process_client
        conn = DatabaseConnector(str(db_path))
        trade_id = _insert_trade(conn, rule_bucket="value")
        conn.close()

        response = client.put(
            f"/ai-advisor/trades/{trade_id}/process-checks",
            json={"authorized": True, "params_ok": True, "data_verified": False, "notes": "checked"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["process_authorized"] is True
        assert data["process_params_ok"] is True
        assert data["process_data_verified"] is False
        assert data["process_notes"] == "checked"
        assert data["process_checked_at"] is not None

    def test_put_process_checks_partial_update_preserves_other_fields(self, process_client):
        client, db_path = process_client
        conn = DatabaseConnector(str(db_path))
        trade_id = _insert_trade(conn, rule_bucket="value", process_authorized=True)
        conn.close()

        response = client.put(
            f"/ai-advisor/trades/{trade_id}/process-checks",
            json={"params_ok": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["process_authorized"] is True  # untouched
        assert data["process_params_ok"] is True

    def test_put_process_checks_404_on_missing_trade(self, process_client):
        client, _ = process_client
        response = client.put(
            "/ai-advisor/trades/999999/process-checks",
            json={"authorized": True},
        )
        assert response.status_code == 404

    def test_verify_accepts_process_fields(self, process_client):
        """POST /verify with authorized/params_ok/data_verified/notes writes process
        checks via set_process_checks, without writing an emotive verdict (flag on)."""
        client, db_path = process_client
        conn = DatabaseConnector(str(db_path))
        trade_id = _insert_trade(
            conn, log_date=(_TODAY - timedelta(days=5)).isoformat(),
            rule_bucket="compliance", verification_status="pending",
        )
        conn.close()

        response = client.post(
            f"/ai-advisor/trades/{trade_id}/verify",
            json={
                "verification_result": "RSU 按计划变现",
                "authorized": True,
                "params_ok": True,
                "data_verified": True,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["verdict"] is None

        conn = DatabaseConnector(str(db_path))
        row = conn.execute(
            "SELECT process_authorized, process_params_ok, process_data_verified"
            " FROM trade_logs WHERE id = ?",
            [trade_id],
        ).fetchone()
        conn.close()
        assert row == (True, True, True)
