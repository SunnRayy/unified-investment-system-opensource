
from unittest.mock import MagicMock, patch
from src.sync.orchestrator import run_full_sync_v3
from src.validation.data_integrity_gate import IntegrityReport, CheckResult


def _clean_integrity_report() -> IntegrityReport:
    """Return a fully-passing IntegrityReport for use in orchestration-mechanics tests.

    This test uses a MagicMock connector (not a real DB) to isolate authority-resolution
    call order.  The integrity gate runs SQL against the connector and, with MagicMock,
    int(MagicMock()) == 1 — so blocking checks like active_holdings_have_positive_value
    return passed=False, forcing success=False and hiding the mechanics under test.
    Mocking the gate here is appropriate: this test is purely about authority-resolution
    wiring, not about data-integrity semantics.
    """
    from src.validation.data_integrity_gate import INTEGRITY_CHECKS
    return IntegrityReport(checks=[
        CheckResult(name=name, passed=True, actual_value="mocked", threshold="n/a", details="mocked")
        for name, _ in INTEGRITY_CHECKS
    ])


@patch('src.sync.orchestrator.sync_asset_registry')
@patch('src.sync.orchestrator.sync_current_allocations')
@patch('src.sync.orchestrator.validate_cost_basis')
@patch('src.sync.orchestrator.validate_allocations')
@patch('src.identity.authority_resolver.AuthorityResolver')
@patch('src.sync.holdings_aggregator.HoldingsAggregator')
@patch('src.sync.orchestrator.run_integrity_checks', return_value=None)  # replaced below via side_effect
def test_full_sync_includes_authority_resolution(
    mock_run_integrity,
    MockAggregator, MockResolver,
    mock_val_alloc, mock_val_cost,
    mock_sync_alloc,
    mock_sync_registry
):
    # This test is purely about authority-resolution call-order mechanics.
    # We mock the integrity gate to return a clean all-passing report so that
    # MagicMock connector interactions don't cause blocking-check failures that
    # obscure the assertion on MockResolver/MockAggregator.
    mock_run_integrity.side_effect = lambda connector: _clean_integrity_report()

    # Setup
    connector = MagicMock()
    # Config to skip freshness (though mocked) and disable taxonomy loading if needed
    config = {
        'sources': {
            'pis': {'excel_path': 'dummy.xlsx', 'sqlite_path': 'dummy.db'}
        },
        'validation': {
            'freshness': {'enabled': False}, # Skip freshness logic
            'taxonomy': {'enabled': False}   # Skip taxonomy logic
        }
    }

    # Configure Aggregator Mock
    mock_aggregator_instance = MockAggregator.return_value

    # Execute
    result = run_full_sync_v3(connector, config)

    # Verify Success
    assert result.success is True, f"Sync failed with error: {result.error_message}"

    # Verify Authority Resolution was called (may be called multiple times — once per phase
    # that uses it, e.g. P5 authority resolution + C3.4 consolidation).
    MockResolver.assert_called()
    MockAggregator.assert_called_once()
    mock_aggregator_instance.apply_authority_rules.assert_called_once()

    # Verify execution order (implicitly by structure, but can check calls)
    # Ensure it happens AFTER holdings sync and BEFORE allocations sync

    # Get all calls to the mocks in a shared parent?
    # Hard to check strict order across patches without a manager.
    # But confirming it is called is verifying the line is executed.


# ---------------------------------------------------------------------------
# C3b integration: resolve_authorities set semantics in the real resolver
# ---------------------------------------------------------------------------

def test_resolve_authorities_co_authority_rule_from_yaml():
    """Integration: real AuthorityResolver loaded from source_authority.yaml returns a set
    for US_STK_* and single element for single-authority rules.

    This test uses the live YAML on disk (config/source_authority.yaml) which now
    declares [Schwab_CSV, Broker_IBKR] for US_STK_*, US_ETF_*, and CASH_USD.
    """
    from src.identity.authority_resolver import AuthorityResolver

    resolver = AuthorityResolver()  # loads config/source_authority.yaml

    # Co-authority rules
    both_avail = ['Schwab_CSV', 'Broker_IBKR', 'CN_Fund_Excel']
    assert resolver.resolve_authorities('US_STK_SGOV', available_sources=both_avail) == frozenset({'Schwab_CSV', 'Broker_IBKR'})
    assert resolver.resolve_authorities('US_ETF_VOO',  available_sources=both_avail) == frozenset({'Schwab_CSV', 'Broker_IBKR'})
    assert resolver.resolve_authorities('CASH_USD',    available_sources=both_avail) == frozenset({'Schwab_CSV', 'Broker_IBKR'})

    # Only Schwab available
    assert resolver.resolve_authorities('US_STK_AAPL', available_sources=['Schwab_CSV']) == frozenset({'Schwab_CSV'})

    # Single-authority rules — 1-element frozenset
    assert resolver.resolve_authorities('CN_FUND_900008', available_sources=['CN_Fund_Excel', 'PIS']) == frozenset({'CN_Fund_Excel'})
    assert resolver.resolve_authorities('GOLD_PAPER_CMB', available_sources=['Gold_Excel'])            == frozenset({'Gold_Excel'})
    assert resolver.resolve_authorities('RSU_AMZN',       available_sources=['RSU_Excel'])             == frozenset({'RSU_Excel'})

    # resolve() still returns the primary (Schwab_CSV = first declared) unchanged
    assert resolver.resolve('US_STK_AAPL', available_sources=['Schwab_CSV', 'Broker_IBKR']) == 'Schwab_CSV'
