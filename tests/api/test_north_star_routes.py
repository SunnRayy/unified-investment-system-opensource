"""Tests for src/api/routes/north_star.py (PRD 2026-07-07 F3, Batch B6).

In-memory DuckDB via initialize_schema (never a bare, schema-less connector).
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.database.seed_loader import seed_demo_content
from src.services.north_star_flows import compose_natural_key


@pytest.fixture
def client():
    test_conn = DatabaseConnector(":memory:")
    initialize_schema(test_conn)
    # Program OSR WS-3c: unforced_errors seed moved out of schema.sql into
    # the seed-pack system — test session runs under $UIS_SEED_PROFILE=example
    # (tests/conftest.py), so this populates the persona's example entry.
    seed_demo_content(test_conn)

    def override_get_db():
        return test_conn

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), test_conn
    app.dependency_overrides.clear()
    test_conn.close()


def _insert_tx(conn, tx_date: str, asset_id: str, tx_type: str, amount_net: float) -> int:
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, amount_net, amount_gross,
             currency, source_system, is_provisional)
        VALUES (?, ?, ?, ?, ?, ?, 'CNY', 'test', FALSE)
        """,
        [tx_date, asset_id, asset_id, tx_type, amount_net, amount_net],
    )
    return conn.execute(
        "SELECT id FROM transactions WHERE asset_id = ? AND transaction_date = ? AND transaction_type = ? ORDER BY id DESC LIMIT 1",
        [asset_id, tx_date, tx_type],
    ).fetchone()[0]


def _nk_for(conn, tx_id: int) -> str:
    """V81 natural key a given transactions.id row currently composes to."""
    row = conn.execute(
        "SELECT source_system, transaction_date, asset_id, transaction_type, amount_gross "
        "FROM transactions WHERE id = ?",
        [tx_id],
    ).fetchone()
    return compose_natural_key(*row)


def test_panel_returns_all_four_sections(client):
    test_client, _conn = client
    resp = test_client.get("/north-star/panel")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"contributions", "time_in_market", "unforced_errors", "glide_path"}


def test_flow_tag_invalid_classification_returns_422(client):
    test_client, conn = client
    tx_id = _insert_tx(conn, "2026-03-01", "US_STK_SGOV", "sell", 10000.0)
    resp = test_client.put(
        "/north-star/flows/tag",
        json={"source_table": "transactions", "source_row_key": str(tx_id), "classification": "bogus"},
    )
    assert resp.status_code == 422


def test_flow_tag_invalid_source_table_returns_422(client):
    test_client, conn = client
    resp = test_client.put(
        "/north-star/flows/tag",
        json={"source_table": "not_a_table", "source_row_key": "1", "classification": "external_contribution"},
    )
    assert resp.status_code == 422


def test_flow_tag_accepts_fs_cash_delta_source_table(client):
    """Regression: 'fs_cash_delta' must be in the route's _VALID_SOURCE_TABLES.

    north_star_flows.tag_flow_manual has handled fs_cash_delta since the
    FS-cash feature shipped (V7.6.0), but the route allowlist omitted it, so
    PUT /flows/tag returned 422 before ever reaching that working code — which
    broke the classification UI's per-row Tag action on exactly the FS-cash
    rows it exists to classify (only the bulk path worked, because
    /flows/tag/bulk validates no source_table at all).

    A well-formed fscash: key that has no live candidate must fall through to
    the LookupError -> 404 path, NOT be rejected as an invalid source_table.
    Asserting "not 422" is the point: 404 here proves validation passed and
    the service layer was actually reached.
    """
    test_client, _conn = client
    resp = test_client.put(
        "/north-star/flows/tag",
        json={
            "source_table": "fs_cash_delta",
            "source_row_key": "fscash:CASH_Deposit_BOC_CNY|2026-06",
            "classification": "internal_transfer",
        },
    )
    assert resp.status_code != 422, (
        "fs_cash_delta was rejected by the route allowlist — the classification "
        "UI's per-row Tag action is broken for every FS-cash row."
    )
    assert resp.status_code == 404


def test_flow_tag_manual_upsert_succeeds(client):
    test_client, conn = client
    tx_id = _insert_tx(conn, "2026-03-01", "CN_FUND_000001", "transfer_in", 8000.0)
    resp = test_client.put(
        "/north-star/flows/tag",
        json={
            "source_table": "transactions",
            "source_row_key": str(tx_id),
            "classification": "external_contribution",
            "note": "salary savings",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["classification"] == "external_contribution"
    assert body["tagged_by"] == "manual"
    assert body["amount_cny"] == 8000.0


def test_flow_tag_unknown_row_returns_404(client):
    test_client, _conn = client
    resp = test_client.put(
        "/north-star/flows/tag",
        json={"source_table": "transactions", "source_row_key": "999999", "classification": "external_contribution"},
    )
    assert resp.status_code == 404


def test_classify_endpoint_runs_heuristics(client):
    test_client, conn = client
    _insert_tx(conn, "2026-03-01", "US_STK_SGOV", "sell", 10000.0)
    _insert_tx(conn, "2026-03-01", "US_STK_BRKB", "buy", 10000.0)

    resp = test_client.post("/north-star/flows/classify")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tagged"] == 2


def test_unclassified_flows_endpoint(client):
    test_client, conn = client
    tx_id = _insert_tx(conn, "2026-04-01", "CN_FUND_000001", "transfer_in", 5000.0)

    resp = test_client.get("/north-star/flows/unclassified")
    assert resp.status_code == 200
    rows = resp.json()
    assert any(r["source_row_key"] == str(tx_id) for r in rows)


def test_unforced_errors_list_includes_seed(client):
    test_client, _conn = client
    resp = test_client.get("/north-star/unforced-errors")
    assert resp.status_code == 200
    rows = resp.json()
    assert any("deadline-adjacent liquidation quota" in r["description"] for r in rows)


def test_unforced_errors_create(client):
    test_client, _conn = client
    resp = test_client.post(
        "/north-star/unforced-errors",
        json={"error_date": "2026-05-01", "description": "Missed rebalance window", "est_cost_cny": 500.0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["description"] == "Missed rebalance window"

    listed = test_client.get("/north-star/unforced-errors").json()
    assert any(r["description"] == "Missed rebalance window" for r in listed)


def test_unforced_errors_create_empty_description_422(client):
    test_client, _conn = client
    resp = test_client.post(
        "/north-star/unforced-errors",
        json={"error_date": "2026-05-01", "description": "   "},
    )
    assert resp.status_code == 422


def test_classify_dry_run_does_not_write(client):
    test_client, conn = client
    _insert_tx(conn, "2026-03-01", "US_STK_SGOV", "sell", 10000.0)
    _insert_tx(conn, "2026-03-01", "US_STK_BRKB", "buy", 10000.0)

    resp = test_client.post("/north-star/flows/classify?dry_run=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True
    assert body["would_tag"] == 2
    # Nothing written
    count = conn.execute("SELECT COUNT(*) FROM cash_flow_tags").fetchone()[0]
    assert count == 0


def test_classify_returns_tagged_ids(client):
    test_client, conn = client
    _insert_tx(conn, "2026-03-01", "US_STK_SGOV", "sell", 10000.0)
    _insert_tx(conn, "2026-03-01", "US_STK_BRKB", "buy", 10000.0)

    resp = test_client.post("/north-star/flows/classify")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body.get("tagged_ids"), list)
    assert len(body["tagged_ids"]) == 2


def test_revert_classify_removes_heuristic_rows(client):
    test_client, conn = client
    _insert_tx(conn, "2026-03-01", "US_STK_SGOV", "sell", 10000.0)
    _insert_tx(conn, "2026-03-01", "US_STK_BRKB", "buy", 10000.0)

    classify_resp = test_client.post("/north-star/flows/classify").json()
    tagged_ids = classify_resp["tagged_ids"]
    assert len(tagged_ids) == 2

    revert_resp = test_client.post("/north-star/flows/classify/revert", json={"ids": tagged_ids})
    assert revert_resp.status_code == 200
    assert revert_resp.json()["deleted"] == 2
    count = conn.execute("SELECT COUNT(*) FROM cash_flow_tags WHERE tagged_by = 'heuristic'").fetchone()[0]
    assert count == 0


def test_patch_unforced_error_cost_updates_and_records_history(client):
    test_client, _ = client
    create_resp = test_client.post(
        "/north-star/unforced-errors",
        json={"error_date": "2026-05-01", "description": "Cost edit test"},
    )
    assert create_resp.status_code == 200
    error_id = create_resp.json()["id"]

    patch_resp = test_client.patch(
        f"/north-star/unforced-errors/{error_id}",
        json={"est_cost_cny": 1500.0},
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert body["est_cost_cny"] == 1500.0
    assert isinstance(body.get("cost_edit_history"), list)
    assert len(body["cost_edit_history"]) == 1
    assert body["cost_edit_history"][0]["new"] == 1500.0
    assert body["cost_edit_history"][0]["old"] is None


def test_patch_unforced_error_not_found_returns_404(client):
    test_client, _ = client
    resp = test_client.patch(
        "/north-star/unforced-errors/99999",
        json={"est_cost_cny": 100.0},
    )
    assert resp.status_code == 404


# ── WS-A: GET /flows/classified ───────────────────────────────────────────────

def test_classified_flows_empty_when_none_tagged(client):
    test_client, _conn = client
    resp = test_client.get("/north-star/flows/classified")
    assert resp.status_code == 200
    assert resp.json() == []


def test_classified_flows_returns_tagged_row(client):
    from datetime import date
    test_client, conn = client
    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "CN_FUND_000001", "transfer_in", 5000.0)
    # Tag it manually
    test_client.put(
        "/north-star/flows/tag",
        json={
            "source_table": "transactions",
            "source_row_key": str(tx_id),
            "classification": "external_contribution",
        },
    )
    resp = test_client.get("/north-star/flows/classified")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["source_table"] == "transactions"
    assert row["source_row_key"] == _nk_for(conn, tx_id)
    assert row["classification"] == "external_contribution"
    assert row["tagged_by"] == "manual"
    assert row["asset_id"] == "CN_FUND_000001"
    assert row["orphaned"] is False


def test_classified_flows_filter_by_classification(client):
    from datetime import date
    test_client, conn = client
    today = date.today()
    tx_id_a = _insert_tx(conn, today.isoformat(), "ASSET_A", "transfer_in", 1000.0)
    tx_id_b = _insert_tx(conn, today.isoformat(), "ASSET_B", "transfer_in", 2000.0)
    test_client.put(
        "/north-star/flows/tag",
        json={"source_table": "transactions", "source_row_key": str(tx_id_a), "classification": "external_contribution"},
    )
    test_client.put(
        "/north-star/flows/tag",
        json={"source_table": "transactions", "source_row_key": str(tx_id_b), "classification": "income_reinvested"},
    )
    resp = test_client.get("/north-star/flows/classified?classification=external_contribution")
    assert resp.status_code == 200
    rows = resp.json()
    assert all(r["classification"] == "external_contribution" for r in rows)
    assert any(r["source_row_key"] == _nk_for(conn, tx_id_a) for r in rows)
    assert not any(r["source_row_key"] == _nk_for(conn, tx_id_b) for r in rows)


def test_classified_flows_invalid_classification_returns_422(client):
    test_client, _conn = client
    resp = test_client.get("/north-star/flows/classified?classification=bogus_value")
    assert resp.status_code == 422


# ── WS-A: PUT /flows/tag/bulk ─────────────────────────────────────────────────

def test_bulk_tag_happy_path(client):
    from datetime import date
    test_client, conn = client
    today = date.today()
    tx_id_a = _insert_tx(conn, today.isoformat(), "BULK_A", "transfer_in", 3000.0)
    tx_id_b = _insert_tx(conn, today.isoformat(), "BULK_B", "transfer_in", 4000.0)

    resp = test_client.put(
        "/north-star/flows/tag/bulk",
        json={
            "items": [
                {"source_table": "transactions", "source_row_key": str(tx_id_a)},
                {"source_table": "transactions", "source_row_key": str(tx_id_b)},
            ],
            "classification": "external_contribution",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tagged"] == 2
    assert body["not_found"] == 0

    # Verify rows are in cash_flow_tags with tagged_by='manual'
    count = conn.execute(
        "SELECT COUNT(*) FROM cash_flow_tags WHERE tagged_by = 'manual' AND classification = 'external_contribution'"
    ).fetchone()[0]
    assert count == 2


def test_bulk_tag_invalid_classification_returns_422(client):
    test_client, _conn = client
    resp = test_client.put(
        "/north-star/flows/tag/bulk",
        json={
            "items": [{"source_table": "transactions", "source_row_key": "1"}],
            "classification": "not_valid",
        },
    )
    assert resp.status_code == 422


def test_bulk_tag_missing_row_counted_in_not_found(client):
    from datetime import date
    test_client, conn = client
    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "REAL_ASSET", "transfer_in", 1000.0)
    resp = test_client.put(
        "/north-star/flows/tag/bulk",
        json={
            "items": [
                {"source_table": "transactions", "source_row_key": str(tx_id)},
                {"source_table": "transactions", "source_row_key": "999999"},  # nonexistent
            ],
            "classification": "external_contribution",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tagged"] == 1
    assert body["not_found"] == 1


def test_bulk_tag_empty_items_returns_zero(client):
    test_client, _conn = client
    resp = test_client.put(
        "/north-star/flows/tag/bulk",
        json={"items": [], "classification": "external_contribution"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"tagged": 0, "not_found": 0}


# ── WS-A: DELETE /flows/tag ───────────────────────────────────────────────────

def test_untag_removes_tagged_rows(client):
    import json as _json
    from datetime import date
    test_client, conn = client
    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "UNTAG_ME", "transfer_in", 2000.0)

    # Tag it first
    test_client.put(
        "/north-star/flows/tag",
        json={"source_table": "transactions", "source_row_key": str(tx_id), "classification": "external_contribution"},
    )
    assert conn.execute("SELECT COUNT(*) FROM cash_flow_tags").fetchone()[0] >= 1

    # Untag it — DELETE with body requires request() as TestClient.delete() has no json kwarg
    resp = test_client.request(
        "DELETE",
        "/north-star/flows/tag",
        content=_json.dumps({"items": [{"source_table": "transactions", "source_row_key": str(tx_id)}]}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 1

    # Should no longer be in cash_flow_tags
    count = conn.execute(
        "SELECT COUNT(*) FROM cash_flow_tags WHERE source_row_key = ?", [str(tx_id)]
    ).fetchone()[0]
    assert count == 0


def test_untag_empty_items_returns_zero(client):
    import json as _json
    test_client, _conn = client
    resp = test_client.request(
        "DELETE",
        "/north-star/flows/tag",
        content=_json.dumps({"items": []}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 0}


def test_untag_nonexistent_row_returns_zero(client):
    import json as _json
    test_client, _conn = client
    resp = test_client.request(
        "DELETE",
        "/north-star/flows/tag",
        content=_json.dumps({"items": [{"source_table": "transactions", "source_row_key": "999999"}]}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 0


# ── WS-A: GET /contributions ──────────────────────────────────────────────────

def test_contributions_shape_on_empty_db(client):
    test_client, _conn = client
    resp = test_client.get("/north-star/contributions")
    assert resp.status_code == 200
    body = resp.json()
    assert "ytd_sum" in body
    assert "trailing_12m_sum" in body
    assert "unclassified_count" in body
    assert "by_classification" in body
    bc = body["by_classification"]
    assert set(bc.keys()) == {"external_contribution", "internal_transfer", "income_reinvested"}


def test_contributions_reflects_tagged_external(client):
    from datetime import date
    test_client, conn = client
    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "CONTRIB_TEST", "transfer_in", 10000.0)
    test_client.put(
        "/north-star/flows/tag",
        json={"source_table": "transactions", "source_row_key": str(tx_id), "classification": "external_contribution"},
    )
    resp = test_client.get("/north-star/contributions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ytd_sum"] == 10000.0
    assert body["by_classification"]["external_contribution"] == 10000.0
    assert body["unclassified_count"] == 0


# ── Task B: window_months query param (Cash Flow tab Last 12m/36m/All Time) ──

def _insert_income_expense_month(conn, record_key: str, month: str, payload: dict) -> None:
    import json
    conn.execute(
        "INSERT INTO income_expense_monthly (record_key, transaction_date, payload) VALUES (?, ?, ?)",
        [record_key, month, json.dumps(payload)],
    )


def _seed_40_months(conn) -> list[str]:
    """40 distinct months (2022-01 .. 2025-04) with strictly increasing
    gross_invested, so 12m/36m/all windows are each independently
    verifiable by their summed value."""
    months = []
    for i in range(40):
        year = 2022 + (i // 12)
        month_num = (i % 12) + 1
        month_str = f"{year}-{month_num:02d}-01"
        months.append(month_str[:7])
        _insert_income_expense_month(conn, f"m{i}", month_str, {
            "投资理财_股票基金_天天基金": 1000.0 * (i + 1),
            "收入_主动收入_工资": 10000,
        })
    return months


def test_contributions_default_window_is_12_months(client):
    """No window_months param -> today's behaviour (12-data-month window)."""
    test_client, conn = client
    months = _seed_40_months(conn)
    resp = test_client.get("/north-star/contributions")
    assert resp.status_code == 200
    inv = resp.json()["investment"]
    assert inv["window_start_month"] == months[-12]
    assert inv["window_end_month"] == months[-1]
    expected_gross = sum(1000.0 * (i + 1) for i in range(28, 40))
    assert inv["gross_invested_ttm"] == expected_gross


def test_contributions_window_months_36(client):
    test_client, conn = client
    months = _seed_40_months(conn)
    resp = test_client.get("/north-star/contributions", params={"window_months": "36"})
    assert resp.status_code == 200
    inv = resp.json()["investment"]
    assert inv["window_start_month"] == months[-36]
    assert inv["window_end_month"] == months[-1]
    expected_gross = sum(1000.0 * (i + 1) for i in range(4, 40))
    assert inv["gross_invested_ttm"] == expected_gross


def test_contributions_window_months_all(client):
    """'all' must cover the FULL series and report the true first/last data
    month — never a hardcoded '12' or '36' — honesty requirement."""
    test_client, conn = client
    months = _seed_40_months(conn)
    resp = test_client.get("/north-star/contributions", params={"window_months": "all"})
    assert resp.status_code == 200
    inv = resp.json()["investment"]
    assert inv["window_start_month"] == months[0]
    assert inv["window_end_month"] == months[-1]
    expected_gross = sum(1000.0 * (i + 1) for i in range(40))
    assert inv["gross_invested_ttm"] == expected_gross
    # 'all' strictly covers more than '36', proving it isn't silently
    # clamped to 36 by accident.
    resp_36 = test_client.get("/north-star/contributions", params={"window_months": "36"})
    assert inv["gross_invested_ttm"] > resp_36.json()["investment"]["gross_invested_ttm"]


def test_contributions_window_months_all_is_case_insensitive(client):
    test_client, conn = client
    _seed_40_months(conn)
    resp = test_client.get("/north-star/contributions", params={"window_months": "ALL"})
    assert resp.status_code == 200


def test_contributions_window_months_invalid_returns_400(client):
    test_client, _conn = client
    resp = test_client.get("/north-star/contributions", params={"window_months": "24"})
    assert resp.status_code == 400


def test_contributions_rsu_window_matches_investment_window(client):
    """rsu.* must read the SAME window investment.* was computed over — the
    coupling ADR-025 §5.2/§3.3 relies on (never let them diverge), even when
    window_months=36 is requested."""
    test_client, conn = client
    months = _seed_40_months(conn)
    resp = test_client.get("/north-star/contributions", params={"window_months": "36"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["rsu"]["window_start_month"] == months[-36]
    assert body["rsu"]["window_end_month"] == months[-1]
    assert body["rsu"]["window_start_month"] == body["investment"]["window_start_month"]
    assert body["rsu"]["window_end_month"] == body["investment"]["window_end_month"]


def test_contributions_ytd_and_trailing_12m_sum_unaffected_by_window_months(client):
    """The legacy tag-based ytd_sum/trailing_12m_sum (ADR-025 §4a, retired
    from display but still in the payload) must stay fixed trailing-12M/YTD
    regardless of window_months — only investment.*/rsu.* follow the toggle."""
    test_client, conn = client
    _seed_40_months(conn)
    today = date.today()
    tx_id = _insert_tx(conn, today.isoformat(), "WINDOW_INDEP", "transfer_in", 5000.0)
    test_client.put(
        "/north-star/flows/tag",
        json={"source_table": "transactions", "source_row_key": str(tx_id), "classification": "external_contribution"},
    )
    resp_12 = test_client.get("/north-star/contributions", params={"window_months": "12"})
    resp_all = test_client.get("/north-star/contributions", params={"window_months": "all"})
    assert resp_12.json()["ytd_sum"] == resp_all.json()["ytd_sum"] == 5000.0
    assert resp_12.json()["trailing_12m_sum"] == resp_all.json()["trailing_12m_sum"] == 5000.0


# ── WS-A: GET /unforced-errors (already exists — verify it still works) ───────

def test_get_unforced_errors_returns_list(client):
    """GET /north-star/unforced-errors is already implemented; this test
    confirms it is present and returns the seeded row (not a regression)."""
    test_client, _conn = client
    resp = test_client.get("/north-star/unforced-errors")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert any("deadline-adjacent liquidation quota" in r["description"] for r in rows)


def test_classified_flows_response_includes_rule_id(client):
    """GET /north-star/flows/classified must include rule_id in each row."""
    test_client, conn = client
    today = date.today()
    # Insert a vest row and run heuristic via the route
    conn.execute(
        """
        INSERT INTO transactions
            (transaction_date, asset_id, asset_name, transaction_type, amount_net, currency, source_system, is_provisional)
        VALUES (?, 'RSU_TEST', 'RSU Test', 'vest', 75000.0, 'CNY', 'test', FALSE)
        """,
        [today.isoformat()],
    )
    # Run the heuristic classify route
    classify_resp = test_client.post("/north-star/flows/classify")
    assert classify_resp.status_code == 200

    # Fetch classified flows
    resp = test_client.get("/north-star/flows/classified")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    vest_rows = [r for r in rows if r.get("transaction_type") == "vest"]
    assert len(vest_rows) >= 1
    assert "rule_id" in vest_rows[0]
    assert vest_rows[0]["rule_id"] == "rsu_vest"
