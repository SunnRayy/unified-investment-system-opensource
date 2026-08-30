
import pytest
import logging

pytestmark = pytest.mark.pipeline

from src.identity.authority_resolver import AuthorityResolver


def test_resolver_initialization():
    resolver = AuthorityResolver()
    assert resolver is not None

def test_resolve_us_stock_default_rules():
    """Test standard US stock pattern resolution."""

    config = {
        'defaults': [
            {'pattern': 'US_STK_%', 'authority': 'AIA', 'priority': 10},
            {'pattern': '%', 'authority': 'PIS', 'priority': 100}
        ]
    }
    resolver = AuthorityResolver(config=config)

    assert resolver.resolve('US_STK_AAPL') == 'AIA'
    assert resolver.resolve('US_STK_MSFT') == 'AIA'

def test_resolve_other_asset_default_rules():
    """Test fallback to PIS."""

    config = {
        'defaults': [
            {'pattern': 'US_STK_%', 'authority': 'AIA', 'priority': 10},
            {'pattern': '%', 'authority': 'PIS', 'priority': 100}
        ]
    }
    resolver = AuthorityResolver(config=config)

    assert resolver.resolve('CN_FUND_900008') == 'PIS'
    assert resolver.resolve('CASH_USD') == 'PIS'

def test_priority_handling():
    """Test that lower priority number wins."""

    # Per ADR-013: lower priority number = higher authority (evaluated first, first match wins).
    # 'special_case' matches both 'special_%' (priority 5) and 'special_case' (priority 20).
    # Because 5 < 20 and rules are sorted ascending, HIGH_PRIO wins.
    config = {
        'defaults': [
            {'pattern': 'special_%', 'authority': 'HIGH_PRIO', 'priority': 5},
            {'pattern': 'special_case', 'authority': 'LOWER_PRIO', 'priority': 20},
            {'pattern': '%', 'authority': 'DEFAULT', 'priority': 100}
        ]
    }
    resolver = AuthorityResolver(config=config)
    assert resolver.resolve('special_case') == 'HIGH_PRIO'


def test_resolve_returns_none_when_no_match(caplog):
    """resolve() returns None and logs a debug diagnostic when no rule matches.

    Regression coverage for ADR-013: with no catch-all rule, an asset whose only
    matching rule's authority is unavailable falls through to None. The resolver
    logs a debug line naming the asset_id and the available_sources considered.
    """

    # No '%' catch-all here, so a non-matching id resolves to None.
    config = {
        'rules': [
            {'pattern': 'US_STK_%', 'authority': 'Schwab_CSV', 'priority': 8},
        ]
    }
    resolver = AuthorityResolver(config=config)

    with caplog.at_level(logging.DEBUG, logger='src.identity.authority_resolver'):
        # Case 1: no pattern matches at all
        assert resolver.resolve('CN_FUND_900008') is None
        # Case 2: pattern matches but authority not in available_sources
        assert resolver.resolve('US_STK_AAPL', available_sources=['PIS']) is None

    # The debug log should name at least one of the failing asset IDs
    assert any('CN_FUND_900008' in rec.message for rec in caplog.records), (
        "Expected a debug log naming the unresolved asset_id"
    )

def test_resolve_multiple_available_sources():
    """Test resolution when we know available sources (optional feature from plan)."""
    # Plan says: get_authority(canonical_id, available_sources)
    # If rule says AIA but AIA is not in available_sources, fallback?
    # Logic in plan:
    # if rule.authoritative_source in available_sources: return rule
    # else: continue to next rule


    config = {
        'defaults': [
            {'pattern': 'US_STK_%', 'authority': 'AIA', 'priority': 10},
            {'pattern': '%', 'authority': 'PIS', 'priority': 100}
        ]
    }
    resolver = AuthorityResolver(config=config)

    # Case 1: AIA is available and authoritative
    assert resolver.resolve('US_STK_AAPL', available_sources=['AIA', 'PIS']) == 'AIA'

    # Case 2: AIA is authoritative but NOT available (e.g. only in PIS)
    # Then it should fall through to next rule (PIS)
    assert resolver.resolve('US_STK_AAPL', available_sources=['PIS']) == 'PIS'


# ---------------------------------------------------------------------------
# C3b: backward-compat for single-authority rules
# ---------------------------------------------------------------------------

def _make_resolver_single_auth():
    """Resolver with only single-authority (string) rules — exercises back-compat path."""
    config = {
        'rules': [
            {'pattern': 'GOLD_*',      'authority': 'Gold_Excel',        'priority': 8},
            {'pattern': 'CN_FUND_*',   'authority': 'CN_Fund_Excel',     'priority': 8},
            {'pattern': 'RSU_*',       'authority': 'RSU_Excel',         'priority': 5},
            {'pattern': '*',           'authority': 'Financial_Summary_Excel', 'priority': 9},
        ]
    }
    return AuthorityResolver(config=config)


def test_resolve_single_auth_gold():
    """resolve() for GOLD_* is byte-identical to pre-C3b behaviour."""
    resolver = _make_resolver_single_auth()
    assert resolver.resolve('GOLD_PAPER_CMB') == 'Gold_Excel'
    assert resolver.resolve('GOLD_PAPER_CMB', available_sources=['Gold_Excel', 'PIS']) == 'Gold_Excel'


def test_resolve_single_auth_cn_fund():
    """resolve() for CN_FUND_* is byte-identical to pre-C3b behaviour."""
    resolver = _make_resolver_single_auth()
    assert resolver.resolve('CN_FUND_900008') == 'CN_Fund_Excel'
    assert resolver.resolve('CN_FUND_900008', available_sources=['CN_Fund_Excel']) == 'CN_Fund_Excel'


def test_resolve_single_auth_rsu():
    """resolve() for RSU_* is byte-identical to pre-C3b behaviour."""
    resolver = _make_resolver_single_auth()
    assert resolver.resolve('RSU_AMZN') == 'RSU_Excel'
    assert resolver.resolve('RSU_AMZN', available_sources=['RSU_Excel']) == 'RSU_Excel'


def test_resolve_single_auth_catchall():
    """resolve() for catch-all '*' is byte-identical to pre-C3b behaviour."""
    resolver = _make_resolver_single_auth()
    assert resolver.resolve('CASH_Deposit_USD') == 'Financial_Summary_Excel'
    assert resolver.resolve('Property_Shanghai') == 'Financial_Summary_Excel'


# ---------------------------------------------------------------------------
# C3b: resolve_authorities() — new method
# ---------------------------------------------------------------------------

def _make_resolver_co_auth():
    """Resolver with co-authority US rules plus single-authority others."""
    config = {
        'rules': [
            {'pattern': 'US_STK_*',  'authorities': ['Schwab_CSV', 'Broker_IBKR'], 'priority': 8},
            {'pattern': 'US_ETF_*',  'authorities': ['Schwab_CSV', 'Broker_IBKR'], 'priority': 8},
            {'pattern': 'CASH_USD',  'authorities': ['Schwab_CSV', 'Broker_IBKR'], 'priority': 8},
            {'pattern': 'GOLD_*',    'authority': 'Gold_Excel',                    'priority': 8},
            {'pattern': '*',         'authority': 'Financial_Summary_Excel',        'priority': 9},
        ]
    }
    return AuthorityResolver(config=config)


def test_resolve_authorities_both_brokers_available():
    """resolve_authorities returns both brokers when both are available."""
    resolver = _make_resolver_co_auth()
    result = resolver.resolve_authorities('US_STK_SGOV', available_sources=['Schwab_CSV', 'Broker_IBKR'])
    assert result == frozenset({'Schwab_CSV', 'Broker_IBKR'})


def test_resolve_authorities_only_schwab_available():
    """resolve_authorities returns only Schwab when IBKR absent."""
    resolver = _make_resolver_co_auth()
    result = resolver.resolve_authorities('US_STK_SGOV', available_sources=['Schwab_CSV'])
    assert result == frozenset({'Schwab_CSV'})


def test_resolve_authorities_single_auth_gold():
    """resolve_authorities returns 1-element frozenset for single-authority rules."""
    resolver = _make_resolver_co_auth()
    result = resolver.resolve_authorities('GOLD_PAPER_CMB', available_sources=['Gold_Excel', 'PIS'])
    assert result == frozenset({'Gold_Excel'})


def test_resolve_authorities_no_available_sources():
    """resolve_authorities with no available_sources filter returns full rule set."""
    resolver = _make_resolver_co_auth()
    # Co-authority rule: both declared authorities returned
    assert resolver.resolve_authorities('US_STK_AAPL') == frozenset({'Schwab_CSV', 'Broker_IBKR'})
    # Single-authority rule: 1-element frozenset
    assert resolver.resolve_authorities('GOLD_PAPER_CMB') == frozenset({'Gold_Excel'})


def test_resolve_authorities_empty_when_no_match(caplog):
    """resolve_authorities returns frozenset() and logs debug when no rule yields usable set."""
    config = {
        'rules': [
            {'pattern': 'US_STK_%', 'authorities': ['Schwab_CSV', 'Broker_IBKR'], 'priority': 8},
        ]
    }
    resolver = AuthorityResolver(config=config)
    with caplog.at_level(logging.DEBUG, logger='src.identity.authority_resolver'):
        result = resolver.resolve_authorities('CN_FUND_900008', available_sources=['CN_Fund_Excel'])
    assert result == frozenset()
    assert any('CN_FUND_900008' in rec.message for rec in caplog.records)


def test_resolve_primary_for_co_authority_is_first_declared():
    """resolve() for co-authority rule returns the first declared authority (Schwab_CSV)."""
    resolver = _make_resolver_co_auth()
    # Both available: primary is Schwab_CSV (first declared)
    assert resolver.resolve('US_STK_AAPL', available_sources=['Schwab_CSV', 'Broker_IBKR']) == 'Schwab_CSV'
    # Only IBKR available: falls back to IBKR
    assert resolver.resolve('US_STK_AAPL', available_sources=['Broker_IBKR']) == 'Broker_IBKR'


def test_rule_authorities_helper_both_forms():
    """_rule_authorities tolerates both list and string rule forms."""
    resolver = AuthorityResolver(config={'rules': []})

    list_rule = {'pattern': '*', 'authorities': ['A', 'B'], 'priority': 1}
    str_rule  = {'pattern': '*', 'authority': 'C',          'priority': 1}
    empty_rule = {'pattern': '*', 'priority': 1}

    assert resolver._rule_authorities(list_rule) == ['A', 'B']
    assert resolver._rule_authorities(str_rule)  == ['C']
    assert resolver._rule_authorities(empty_rule) == []


# ---------------------------------------------------------------------------
# FIX 2: coauthority_sources() public method
# ---------------------------------------------------------------------------

def test_coauthority_sources():
    """coauthority_sources() returns sources from rules with >=2 declared authorities
    and excludes single-authority sources like CN_Fund_Excel, Gold_Excel, etc.

    Uses the real config (config/source_authority.yaml) so this is an integration
    test against the actual deployment configuration.
    """

    resolver = AuthorityResolver()
    result = resolver.coauthority_sources()

    # Must include both co-authority broker sources
    assert 'Schwab_CSV' in result, f"Schwab_CSV should be in coauthority_sources, got {result}"
    assert 'Broker_IBKR' in result, f"Broker_IBKR should be in coauthority_sources, got {result}"

    # Single-authority sources must NOT be included
    single_auth_sources = {
        'CN_Fund_Excel', 'Gold_Excel', 'Insurance_Excel',
        'RSU_Excel', 'Financial_Summary_Excel',
    }
    overlap = result & single_auth_sources
    assert not overlap, (
        f"coauthority_sources() must not include single-authority sources, "
        f"but found {overlap}"
    )


def test_coauthority_sources_unit_with_inline_config():
    """coauthority_sources() with inline config — pure unit test, no file I/O."""

    config = {
        'rules': [
            {'pattern': 'US_STK_%', 'authorities': ['Schwab_CSV', 'Broker_IBKR'], 'priority': 8},
            {'pattern': 'US_ETF_%', 'authorities': ['Schwab_CSV', 'Broker_IBKR'], 'priority': 8},
            {'pattern': 'GOLD_%',   'authority': 'Gold_Excel',                    'priority': 8},
            {'pattern': '%',        'authority': 'Financial_Summary_Excel',        'priority': 9},
        ]
    }
    resolver = AuthorityResolver(config=config)
    result = resolver.coauthority_sources()

    assert result == frozenset({'Schwab_CSV', 'Broker_IBKR'})
    assert 'Gold_Excel' not in result
    assert 'Financial_Summary_Excel' not in result


def test_coauthority_sources_empty_when_no_coauth_rules():
    """coauthority_sources() returns frozenset() when there are no co-authority rules."""

    config = {
        'rules': [
            {'pattern': 'GOLD_%', 'authority': 'Gold_Excel',    'priority': 8},
            {'pattern': '%',      'authority': 'PIS',            'priority': 9},
        ]
    }
    resolver = AuthorityResolver(config=config)
    result = resolver.coauthority_sources()
    assert result == frozenset(), f"Expected empty frozenset, got {result}"
