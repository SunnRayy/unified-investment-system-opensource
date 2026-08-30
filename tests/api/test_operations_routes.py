"""Tests for operations investigation endpoints."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema


def _execute_migration(connector: DatabaseConnector, migration_path: Path) -> None:
    sql = migration_path.read_text()
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    for stmt in "\n".join(lines).split(";"):
        clean = stmt.strip()
        if clean:
            connector.execute(clean)


@pytest.fixture
def client_with_ops_data():
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    for migration in sorted(Path("src/database/migrations").glob("*.sql")):
        _execute_migration(connector, migration)

    connector.execute(
        """
        INSERT INTO asset_registry (canonical_id, display_name, asset_class, is_rebalanceable)
        VALUES
          ('US_STK_SGOV', 'SGOV', 'US Equity', TRUE),
          ('US_STK_STALE_SHADOW', 'STALE', 'US Equity', TRUE),
          ('CN_FUND_000001', 'Fund-A', 'CN Fund', TRUE),
          ('CN_FUND_900017', 'A50 ETF', 'CN Equity', TRUE),
          ('CASH_Deposit_BOC_CNY', 'BOC Cash', 'Cash', FALSE),
          ('Pension_Personal', 'Personal Pension', 'Pension', FALSE),
          ('Property_阳光花园', 'Blue County', 'Property', FALSE)
        """
    )
    connector.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, quantity, cost_price_unit, market_price_unit,
            market_value, currency, source_system, is_shadow
        ) VALUES
          ('2026-02-01', 'US_STK_SGOV', 'SGOV', 100, 100, 100, 100000, 'CNY', 'PIS_SQLite', FALSE),
          ('2026-03-08', 'US_STK_SGOV', 'SGOV', 446, 100, 101, 231400, 'CNY', 'Schwab_CSV', FALSE),
          ('2026-03-08', 'US_STK_SGOV', 'SGOV', 446, 100, 100, 229000, 'CNY', 'PIS_SQLite', TRUE),
          ('2026-03-01', 'US_STK_STALE_SHADOW', 'STALE', 10, 10, 11, 11000, 'CNY', 'Schwab_CSV', TRUE),
          ('2026-03-08', 'US_STK_STALE_SHADOW', 'STALE', 10, 10, 12, 12000, 'CNY', 'Schwab_CSV', FALSE),
          ('2026-03-07', 'CN_FUND_000001', 'Fund-A', 1000, 1.0, 1.1, 110000, 'CNY', 'CN_Fund_Excel', FALSE),
          ('2026-02-27', 'CN_FUND_900017', 'A50 ETF', 1000, 1.0, 1.1, 110000, 'CNY', 'CN_Fund_Excel', FALSE),
          ('2026-02-28', 'CN_FUND_900017', 'A50 ETF', 1000, 1.0, 1.1, 110000, 'CNY', 'PIS_SQLite', TRUE),
          ('2026-03-09', 'CN_FUND_900017', 'A50 ETF', 1000, 1.0, 1.1, 110000, 'CNY', 'PIS_SQLite', TRUE),
          ('2026-03-08', 'CASH_Deposit_BOC_CNY', 'BOC Cash', 1, NULL, NULL, 0, 'CNY', 'Financial_Summary_Excel', FALSE),
          ('2026-03-08', 'Pension_Personal', 'Personal Pension', 1, NULL, NULL, 0, 'CNY', 'Financial_Summary_Excel', FALSE),
          ('2026-03-08', 'Property_阳光花园', 'Blue County', 1, NULL, NULL, 0, 'CNY', 'Financial_Summary_Excel', FALSE)
        """
    )
    connector.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type, quantity, price_unit,
            amount_gross, amount_net, commission_fee, currency, account, memo, source_system, verified
        ) VALUES
          ('2026-03-08', 'US_STK_SGOV', 'SGOV', 'buy', 10, 100, 1000, 999, 1, 'USD', 'SCHWAB', 'open', 'Schwab_CSV', TRUE),
          ('2026-03-05', 'US_STK_SGOV', 'SGOV', 'dividend', 0, 0, 10, 10, 0, 'USD', 'SCHWAB', 'div', 'Schwab_CSV', TRUE),
          ('2026-02-27', 'CN_FUND_900017', 'A50 ETF', 'buy', 1000, 1.1, 110000, 110000, 0, 'CNY', 'FUND', 'reader buy', 'CN_Fund_Excel', TRUE)
        """
    )
    connector.execute(
        """
        INSERT INTO sync_audit_reports (
            id, created_at, report_type, net_worth_before, net_worth_after,
            net_worth_change_pct, asset_count_before, asset_count_after,
            by_source_before, by_source_after, integrity_passed, integrity_total,
            integrity_checks, reader_counts, warnings, alert
        ) VALUES
          (
            'run-1', '2026-03-11 08:13:00', 'sync', 5300000, 5310000,
            0.0019, 20, 20, '{}', '{"Gold_Excel": 1}', 18, 18, '[]', '{}', '["WARN: sample"]', FALSE
          ),
          (
            'run-2', '2026-03-12 08:13:00', 'sync', 5310000, 5320000,
            0.0018, 20, 20, '{}', '{"Schwab_CSV": 2}', 18, 18, '[]', '{}', '[]', FALSE
          ),
          (
            'run-3', '2026-03-13 08:13:00', 'sync', 5320000, 5330000,
            0.0017, 20, 20, '{}', '{"Insurance_Excel": 3}', 18, 18, '[]', '{}', '[]', FALSE
          ),
          (
            'run-4', '2026-03-14 08:13:00', 'sync', 5330000, 5340000,
            0.0016, 20, 20, '{}', '{"Schwab_CSV": 4}', 18, 18, '[]', '{}', '[]', FALSE
          )
        """
    )

    def _override_get_db():
        return connector

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        connector.close()


def test_portfolio_audit_contract(client_with_ops_data):
    response = client_with_ops_data.get("/operations/portfolio-audit")
    assert response.status_code == 200
    payload = response.json()
    assert "integrity" in payload
    assert "asset_classes" in payload
    assert "source_strip" in payload


def test_asset_class_audit_contract(client_with_ops_data):
    response = client_with_ops_data.get("/operations/asset-class-audit", params={"class": "US Equity"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["class_name"] == "US Equity"
    assert "groups" in payload
    assert all(group["group_type"] != "derived_secondary" for group in payload["groups"])
    all_asset_ids = [asset.get("asset_id") for group in payload["groups"] for asset in group.get("assets", [])]
    assert "AIA" not in all_asset_ids
    assert "trade_logs" not in all_asset_ids


def test_asset_case_file_contract(client_with_ops_data):
    response = client_with_ops_data.get("/operations/asset-case-file", params={"asset_id": "US_STK_SGOV"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["asset_id"] == "US_STK_SGOV"
    assert "source_trace" in payload
    assert "signals" in payload


def test_sync_history_contract(client_with_ops_data):
    response = client_with_ops_data.get("/operations/sync-history")
    assert response.status_code == 200
    payload = response.json()
    assert "runs" in payload
    assert payload["runs"][0]["id"] == "run-4"


def test_sync_history_filter_param_supports_all_and_no_change(client_with_ops_data):
    db = app.dependency_overrides[get_db]()
    db.execute(
        """
        INSERT INTO sync_audit_reports (
            id, created_at, report_type, net_worth_before, net_worth_after,
            net_worth_change_pct, asset_count_before, asset_count_after,
            by_source_before, by_source_after, integrity_passed, integrity_total,
            integrity_checks, reader_counts, warnings, alert, is_no_change
        ) VALUES (
            'run-no-change', '2026-03-15 08:13:00', 'sync', 5340000, 5340000,
            0.0, 20, 20, '{}', '{}', 18, 18, '[]',
            '{"holdings_synced": 0, "transactions_synced": 0}', '[]', FALSE, TRUE
        )
        """
    )

    all_response = client_with_ops_data.get("/operations/sync-history", params={"filter": "all"})
    assert all_response.status_code == 200
    all_runs = all_response.json()["runs"]
    assert any(run["is_no_change"] is True for run in all_runs)
    assert any(run["is_no_change"] is False for run in all_runs)

    no_change_response = client_with_ops_data.get("/operations/sync-history", params={"filter": "no_change"})
    assert no_change_response.status_code == 200
    no_change_runs = no_change_response.json()["runs"]
    assert len(no_change_runs) > 0
    assert all(run["is_no_change"] is True for run in no_change_runs)


def test_sync_history_detail_returns_persisted_is_no_change_for_zero_delta_run(client_with_ops_data):
    db = app.dependency_overrides[get_db]()
    db.execute(
        """
        INSERT INTO sync_audit_reports (
            id, created_at, report_type, net_worth_before, net_worth_after,
            net_worth_change_pct, asset_count_before, asset_count_after,
            by_source_before, by_source_after, integrity_passed, integrity_total,
            integrity_checks, reader_counts, warnings, alert, is_no_change
        ) VALUES (
            'run-zero-change', '2026-03-16 08:13:00', 'sync', 5340000, 5340000,
            0.0, 20, 20, '{}', '{}', 18, 18, '[]',
            '{"holdings_synced": 0, "transactions_synced": 0}', '[]', FALSE, TRUE
        )
        """
    )

    response = client_with_ops_data.get("/operations/sync-history/run-zero-change")
    assert response.status_code == 200
    payload = response.json()
    assert payload["net_worth_delta"] == 0.0
    assert payload["warnings"] == []
    assert payload["reader_counts"]["holdings_synced"] == 0
    assert payload["reader_counts"]["transactions_synced"] == 0
    assert payload["is_no_change"] is True


def test_portfolio_audit_legacy_influence_ignores_old_shadowed_legacy_rows(client_with_ops_data):
    response = client_with_ops_data.get("/operations/portfolio-audit")
    assert response.status_code == 200
    payload = response.json()
    assert payload["legacy_influence_cases"] == 0


def test_asset_class_audit_excludes_nontradeable_zero_value_from_open_cases(client_with_ops_data):
    response = client_with_ops_data.get("/operations/asset-class-audit", params={"class": "Cash"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["open_cases"] == 0


def test_asset_case_file_reader_shadow_conflict_requires_latest_reader_shadow(client_with_ops_data):
    response = client_with_ops_data.get("/operations/asset-case-file", params={"asset_id": "US_STK_STALE_SHADOW"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["severity"] == "healthy"
    assert payload["authority_context"]["shadow_conflict_flag"] is False


def test_asset_case_file_filters_sync_runs_to_active_source(client_with_ops_data):
    response = client_with_ops_data.get("/operations/asset-case-file", params={"asset_id": "US_STK_SGOV"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence_counts"]["sync_runs"] == 2

    run_descriptions = [
        event["description"]
        for event in payload["source_trace"]
        if event["source_system"] == "Sync" and event["description"].startswith("Run ")
    ]
    assert any("Run run-4" in desc for desc in run_descriptions)
    assert any("Run run-2" in desc for desc in run_descriptions)
    assert all("run-1" not in desc and "run-3" not in desc for desc in run_descriptions)


def test_asset_case_file_ignores_shadow_only_legacy_rows_for_severity(client_with_ops_data):
    response = client_with_ops_data.get("/operations/asset-case-file", params={"asset_id": "CN_FUND_900017"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["severity"] == "healthy"
    assert payload["authority_context"]["legacy_influence_flag"] is False
