"""Tests for ADR-004: dynamic injection of import adapter authority rules."""

import json

import duckdb
import pytest

from src.identity.authority_resolver import AuthorityResolver


@pytest.fixture
def db(tmp_path):
    """In-memory DuckDB with import_adapter_approvals table."""
    conn = duckdb.connect(str(tmp_path / "test.duckdb"))
    conn.execute("""
        CREATE TABLE import_adapter_approvals (
            adapter_key VARCHAR PRIMARY KEY,
            approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            approved_by VARCHAR,
            source_system VARCHAR NOT NULL,
            asset_prefixes_json JSON NOT NULL,
            authority_priority INTEGER NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            generated_reader_key VARCHAR
        )
    """)
    return conn


class _FakeConnector:
    """Minimal wrapper so _load_adapter_authority_rules can call .execute()."""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        if params:
            return self._conn.execute(sql, list(params))
        return self._conn.execute(sql)


def _insert_approval(db, adapter_key, source_system, prefixes, priority, enabled=True):
    """Insert an adapter approval row."""
    db.execute(
        "INSERT INTO import_adapter_approvals "
        "(adapter_key, source_system, asset_prefixes_json, authority_priority, enabled) "
        "VALUES (?, ?, ?, ?, ?)",
        [adapter_key, source_system, json.dumps(prefixes), priority, enabled],
    )


def test_adapter_rules_injected_into_resolver(db):
    """Adapter rules are loaded and merged with static YAML rules."""
    from src.sync.orchestrator import _load_adapter_authority_rules

    _insert_approval(db, "ibkr_csv", "Broker_IBKR", ["US_STK_", "US_ETF_"], 7)

    connector = _FakeConnector(db)
    rules = _load_adapter_authority_rules(connector)

    assert len(rules) == 2
    assert rules[0]["pattern"] == "US_STK_*"
    assert rules[0]["authority"] == "Broker_IBKR"
    assert rules[0]["priority"] == 7
    assert rules[1]["pattern"] == "US_ETF_*"
    assert rules[1]["authority"] == "Broker_IBKR"


def test_adapter_rules_override_lower_priority(db):
    """Adapter at priority 7 wins over built-in Schwab_CSV at priority 8."""
    from src.sync.orchestrator import _load_adapter_authority_rules

    _insert_approval(db, "ibkr_csv", "Broker_IBKR", ["US_STK_"], 7)

    # Simulate: build resolver from static rules, then inject adapter rules
    resolver = AuthorityResolver(config={
        "rules": [
            {"pattern": "US_STK_*", "authority": "Schwab_CSV", "priority": 8},
            {"pattern": "*", "authority": "AIA", "priority": 10},
        ]
    })

    adapter_rules = _load_adapter_authority_rules(_FakeConnector(db))
    resolver.rules.extend(adapter_rules)
    resolver.rules.sort(key=lambda x: x.get("priority", 100))

    # Broker_IBKR (priority 7) should win over Schwab_CSV (priority 8)
    result = resolver.resolve("US_STK_AAPL", available_sources=["Schwab_CSV", "Broker_IBKR", "AIA"])
    assert result == "Broker_IBKR"


def test_disabled_adapter_not_injected(db):
    """Disabled adapters are excluded from rule injection."""
    from src.sync.orchestrator import _load_adapter_authority_rules

    _insert_approval(db, "ibkr_csv", "Broker_IBKR", ["US_STK_"], 7, enabled=False)

    rules = _load_adapter_authority_rules(_FakeConnector(db))
    assert len(rules) == 0


def test_no_approvals_returns_empty(db):
    """Empty approvals table produces no rules."""
    from src.sync.orchestrator import _load_adapter_authority_rules

    rules = _load_adapter_authority_rules(_FakeConnector(db))
    assert rules == []


def test_missing_table_returns_empty(tmp_path):
    """Missing approvals table (old DB) is handled gracefully."""
    from src.sync.orchestrator import _load_adapter_authority_rules

    conn = duckdb.connect(str(tmp_path / "empty.duckdb"))
    rules = _load_adapter_authority_rules(_FakeConnector(conn))
    assert rules == []


def test_prefix_already_has_glob(db):
    """Prefixes that already end with * are not double-starred."""
    from src.sync.orchestrator import _load_adapter_authority_rules

    _insert_approval(db, "custom_src", "Custom_Source", ["CN_FUND_*"], 8)

    rules = _load_adapter_authority_rules(_FakeConnector(db))
    assert len(rules) == 1
    assert rules[0]["pattern"] == "CN_FUND_*"  # not "CN_FUND_**"

