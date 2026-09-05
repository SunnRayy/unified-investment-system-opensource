"""Tests for management API routes."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from pathlib import Path

from src.api.main import app
from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema

client = TestClient(app)


@pytest.fixture
def mock_connector():
    with patch("src.api.routes.management.DatabaseConnector") as mock:
        conn = MagicMock()
        mock.return_value = conn
        yield conn


@pytest.fixture
def mock_db():
    """Override get_db dependency with a MagicMock for GET handler tests."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    conn.execute.return_value = cursor
    app.dependency_overrides[get_db] = lambda: conn
    yield conn
    app.dependency_overrides.pop(get_db, None)


class TestTransactionSearch:
    def test_search_transactions_with_filters(self, mock_db):
        """GET /api/management/transactions returns filtered transactions."""
        mock_db.execute.side_effect = [
            [{"cnt": 150}],  # Count query
            # Data query
            [
                {"id": 1, "asset_id": "US_STK_AAPL", "date": "2023-01-01",
                 "transaction_type": "Buy", "quantity": 10, "amount_cny": 1000}
            ]
        ]

        response = client.get("/management/transactions", params={
            "asset_id": "US_STK",
            "txn_type": "Buy",
            "page": 1,
            "limit": 50
        })

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 150
        assert len(data["transactions"]) == 1
        assert data["transactions"][0]["asset_id"] == "US_STK_AAPL"

        # Verify query parameters were used (partially)
        # We can't easily inspect the SQL string without more complex mocking,
        # but we can trust the side_effect sequence.

    def test_search_defaults(self, mock_db):
        """GET /api/management/transactions works without params."""
        mock_db.execute.side_effect = [
            [{"cnt": 10}],
            []
        ]
        response = client.get("/management/transactions")
        assert response.status_code == 200


class TestImportPreview:
    def test_preview_returns_readers(self, mock_connector):
        """GET /api/management/import/preview returns reader validation results."""
        mock_report = MagicMock()
        mock_schwab = MagicMock()
        mock_schwab.holdings_count = 15
        mock_schwab.transactions_count = 42
        mock_schwab.warnings = []
        mock_cn = MagicMock()
        mock_cn.holdings_count = 8
        mock_cn.transactions_count = 0
        mock_cn.warnings = ["Missing column"]
        mock_report.reader_results = {"schwab": mock_schwab, "cn_fund": mock_cn}

        with patch("src.validation.run_reader_validation.run_full_validation", return_value=mock_report):
            response = client.get("/management/import/preview")
            assert response.status_code == 200
            data = response.json()
            assert len(data["readers"]) == 2
            assert data["readers"][0]["reader"] == "schwab"
            assert data["readers"][0]["status"] == "ok"
            assert data["readers"][1]["status"] == "warning"


class TestTransactionSources:
    def test_returns_distinct_sources_and_types(self, mock_db):
        """GET /api/management/transactions/sources returns filter options."""
        mock_db.execute.side_effect = [
            [{"source_system": "schwab"}, {"source_system": "cn_fund"}],
            [{"transaction_type": "Buy"}, {"transaction_type": "Sell"}],
        ]
        response = client.get("/management/transactions/sources")
        assert response.status_code == 200
        data = response.json()
        assert "schwab" in data["sources"]
        assert "Buy" in data["types"]


class TestTransactionFilters:
    def test_returns_normalized_filter_metadata(self, mock_db):
        """GET /management/transactions/filters returns filter metadata."""
        mock_db.execute.side_effect = [
            [{"source_system": "Schwab_CSV"}],
            [{"transaction_type": "Buy"}],
            [{"normalized_type": "buy"}],
            [{"account": "SCHWAB"}],
        ]
        response = client.get("/management/transactions/filters")
        assert response.status_code == 200
        data = response.json()
        assert "normalized_types" in data
        assert "raw_types" in data
        assert "sources" in data


def _execute_migration(connector: DatabaseConnector, migration_path: Path) -> None:
    sql = migration_path.read_text()
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    for stmt in "\n".join(lines).split(";"):
        clean = stmt.strip()
        if clean:
            connector.execute(clean)


@pytest.fixture
def seeded_memory_connector():
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    for migration in sorted(Path("src/database/migrations").glob("*.sql")):
        _execute_migration(connector, migration)

    connector.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type, quantity,
            price_unit, amount_gross, amount_net, commission_fee, currency, account,
            memo, source_system, verified
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-03-08",
            "US_STK_SGOV",
            "SGOV",
            "buy",
            10,
            100.0,
            1000.0,
            999.0,
            1.0,
            "USD",
            "SCHWAB",
            "open position",
            "Schwab_CSV",
            True,
        ),
    )
    yield connector
    connector.close()


def test_search_transactions_uses_live_transactions_schema(seeded_memory_connector):
    """GET /management/transactions works with the live transactions table columns."""
    app.dependency_overrides[get_db] = lambda: seeded_memory_connector
    try:
        response = client.get("/management/transactions", params={"asset_id": "US_STK_SGOV"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        row = payload["transactions"][0]
        assert row["transaction_date"] == "2026-03-08"
        assert row["price_unit"] == 100.0
        assert row["amount_net"] == 999.0
        assert row["commission_fee"] == 1.0
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_transaction_filters_endpoint_works_with_live_connector(seeded_memory_connector):
    """GET /management/transactions/filters works with real DuckDB execute cursors."""
    app.dependency_overrides[get_db] = lambda: seeded_memory_connector
    try:
        filters_response = client.get("/management/transactions/filters")
        assert filters_response.status_code == 200
        filters_payload = filters_response.json()
        assert "Schwab_CSV" in filters_payload["sources"]
        assert "buy" in filters_payload["normalized_types"]
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_transaction_sources_endpoint_works_with_live_connector(seeded_memory_connector):
    """GET /management/transactions/sources works with real DuckDB execute cursors."""
    app.dependency_overrides[get_db] = lambda: seeded_memory_connector
    try:
        sources_response = client.get("/management/transactions/sources")
        assert sources_response.status_code == 200
        sources_payload = sources_response.json()
        assert "Schwab_CSV" in sources_payload["sources"]
        assert "buy" in sources_payload["types"]
    finally:
        app.dependency_overrides.pop(get_db, None)
