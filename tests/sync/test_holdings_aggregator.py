
import pytest

pytestmark = pytest.mark.pipeline

from datetime import date
from unittest.mock import MagicMock

from src.sync.holdings_aggregator import HoldingsAggregator
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema


def _make_mock_resolver(resolve_map: dict, resolve_authorities_map: dict) -> MagicMock:
    """Build a mock resolver with both resolve() and resolve_authorities() wired up."""
    resolver = MagicMock()

    def _resolve(canonical_id, available_sources=None):
        return resolve_map.get(canonical_id)

    def _resolve_authorities(canonical_id, available_sources=None):
        return resolve_authorities_map.get(canonical_id, frozenset())

    resolver.resolve.side_effect = _resolve
    resolver.resolve_authorities.side_effect = _resolve_authorities
    return resolver


@pytest.fixture
def mock_resolver():
    """Single-authority resolver (pre-C3b equivalent).

    resolve('US_STK_AAPL', ...) -> 'AIA'        authority_source
    resolve_authorities(...)     -> frozenset({'AIA'})  shadow set
    resolve('CN_FUND_ONLY', ...) -> 'PIS'
    resolve_authorities(...)     -> frozenset({'PIS'})
    """
    return _make_mock_resolver(
        resolve_map={
            'US_STK_AAPL': 'AIA',
            'CN_FUND_ONLY': 'PIS',
        },
        resolve_authorities_map={
            'US_STK_AAPL': frozenset({'AIA'}),
            'CN_FUND_ONLY': frozenset({'PIS'}),
        },
    )


@pytest.fixture
def db_connector():
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    return connector


def test_apply_authority_rules(db_connector, mock_resolver):
    """Single-authority case: behaviour is identical to pre-C3b (NOT IN {sole_source} == != sole_source)."""
    today = date.today()

    def insert(asset_id, source, val=100):
        db_connector.execute("""
            INSERT INTO holdings (snapshot_date, asset_id, source_system, market_value, is_shadow)
            VALUES (?, ?, ?, ?, ?)
        """, (today, asset_id, source, val, False))

    # Case 1: Overlap - US Stock in both (one should become shadow)
    insert('US_STK_AAPL', 'AIA')
    insert('US_STK_AAPL', 'PIS')

    # Case 2: No Overlap - CN Fund (PIS only)
    insert('CN_FUND_ONLY', 'PIS')

    aggregator = HoldingsAggregator(mock_resolver)
    aggregator.apply_authority_rules(db_connector, today)

    # 1. US_STK_AAPL — AIA is authority, PIS shadowed
    res = db_connector.execute("""
        SELECT source_system, is_shadow, authority_source
        FROM holdings WHERE asset_id = 'US_STK_AAPL'
    """).fetchall()

    res_map = {row[0]: (row[1], row[2]) for row in res}

    assert res_map['AIA'][0] is False, "AIA should NOT be shadow"
    assert res_map['AIA'][1] == 'AIA', "AIA authority_source mismatch"

    assert res_map['PIS'][0] is True, "PIS SHOULD be shadow"
    assert res_map['PIS'][1] == 'AIA', "PIS authority_source mismatch"

    # 2. CN_FUND_ONLY — sole source, not shadowed
    res2 = db_connector.execute("""
        SELECT source_system, is_shadow, authority_source
        FROM holdings WHERE asset_id = 'CN_FUND_ONLY'
    """).fetchall()

    assert res2[0][1] is False, "PIS only should NOT be shadow"
    assert res2[0][2] == 'PIS'

    db_connector.close()


def test_apply_authority_rules_co_authority_same_date(db_connector):
    """C3b co-authority: two sources both in the authority set on the SAME date → BOTH non-shadow."""
    today = date.today()

    # Co-authority resolver: both Schwab_CSV and Broker_IBKR are authoritative for US_STK_*
    resolver = _make_mock_resolver(
        resolve_map={
            'US_STK_SGOV': 'Schwab_CSV',   # primary = first declared
        },
        resolve_authorities_map={
            'US_STK_SGOV': frozenset({'Schwab_CSV', 'Broker_IBKR'}),
        },
    )

    def insert(asset_id, source, val=100, shadow=False):
        db_connector.execute("""
            INSERT INTO holdings (snapshot_date, asset_id, source_system, market_value, is_shadow)
            VALUES (?, ?, ?, ?, ?)
        """, (today, asset_id, source, val, shadow))

    # Both Schwab and IBKR hold SGOV on the same date
    insert('US_STK_SGOV', 'Schwab_CSV')
    insert('US_STK_SGOV', 'Broker_IBKR')
    # PIS also has it — should be shadowed
    insert('US_STK_SGOV', 'PIS')

    aggregator = HoldingsAggregator(resolver)
    aggregator.apply_authority_rules(db_connector, today)

    res = db_connector.execute("""
        SELECT source_system, is_shadow, authority_source
        FROM holdings WHERE asset_id = 'US_STK_SGOV'
        ORDER BY source_system
    """).fetchall()

    res_map = {row[0]: (row[1], row[2]) for row in res}

    # Both brokers are in the authority set → NOT shadowed
    assert res_map['Schwab_CSV'][0] is False, "Schwab_CSV must NOT be shadow (in authority set)"
    assert res_map['Broker_IBKR'][0] is False, "Broker_IBKR must NOT be shadow (in authority set)"

    # PIS is not in the authority set → shadowed
    assert res_map['PIS'][0] is True, "PIS must be shadow (not in authority set)"

    # authority_source stamped with the primary (Schwab_CSV) on all rows
    assert res_map['Schwab_CSV'][1] == 'Schwab_CSV'
    assert res_map['Broker_IBKR'][1] == 'Schwab_CSV'
    assert res_map['PIS'][1] == 'Schwab_CSV'

    db_connector.close()


def test_apply_authority_rules_already_shadow_stays_shadow(db_connector):
    """Rows already is_shadow=TRUE must remain shadow regardless of set membership."""
    today = date.today()

    resolver = _make_mock_resolver(
        resolve_map={'US_STK_AAPL': 'Schwab_CSV'},
        resolve_authorities_map={'US_STK_AAPL': frozenset({'Schwab_CSV'})},
    )

    # Insert a Schwab row that is already shadow (e.g. stale older snapshot)
    db_connector.execute("""
        INSERT INTO holdings (snapshot_date, asset_id, source_system, market_value, is_shadow)
        VALUES (?, 'US_STK_AAPL', 'Schwab_CSV', 100, TRUE)
    """, (today,))

    aggregator = HoldingsAggregator(resolver)
    aggregator.apply_authority_rules(db_connector, today)

    res = db_connector.execute("""
        SELECT is_shadow FROM holdings WHERE asset_id = 'US_STK_AAPL'
    """).fetchone()
    assert res[0] is True, "Pre-existing shadow must remain shadow"

    db_connector.close()
