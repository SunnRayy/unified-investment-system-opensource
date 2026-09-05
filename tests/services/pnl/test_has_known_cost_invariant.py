"""Guard: "may this asset enter a cost/return denominator?" is keyed on
``AssetPnL.has_known_cost``, never on ``treatment is Treatment.balance_only``.

Release 2 (#7) overlays owner-entered figures and sets ``treatment = manual``.
A surface still keyed on the enum would then read a manual-realized-only asset
(profit logged, cost still unknown) as "not balance_only", charge it in at cost
0, and book its whole market value as profit — the V7.8.3 ¥386K Fixed-Income
phantom, re-opened through the front door.

These tests pin the two halves:
  1. equivalence — on base treatments the swap moves NO number (Phase 1 is a
     pure refactor);
  2. divergence — once ``treatment`` is ``manual`` with an unknown cost, the two
     predicates disagree, and only ``has_known_cost`` gives the safe answer.

Test (2) is the anti-vacuity half: it fails against the pre-Phase-1 code.
"""
from __future__ import annotations

import pytest

from src.services.pnl.engine import summary_totals
from src.services.pnl.models import AssetPnL, Treatment

pytestmark = pytest.mark.critical


def _asset(
    asset_id: str,
    *,
    treatment: Treatment,
    market_value: float,
    cost_basis,
    realized: float = 0.0,
) -> AssetPnL:
    return AssetPnL(
        asset_id=asset_id,
        name=asset_id,
        top_class="Fixed Income",
        sub_class="Bonds",
        source_system="Financial_Summary_Excel",
        currency="CNY",
        market_value_cny=market_value,
        treatment=treatment,
        cost_basis_cny=cost_basis,
        unrealized_cny=None if cost_basis is None else market_value - cost_basis,
        realized_cny=realized,
        lifetime_cny=None,
        return_pct=None,
        unrealized_current_lots_pct=None,
        first_acquired=None,
        has_manual_data=False,
        is_current=True,
    )


def test_has_known_cost_matches_balance_only_for_base_treatments():
    """Phase 1 equivalence: for the three BASE treatments the new predicate is
    exactly the negation of the old one, so the refactor moves no number."""
    cash = _asset("CASH_X", treatment=Treatment.cash, market_value=1000.0, cost_basis=1000.0)
    traded = _asset("US_STK_VOO", treatment=Treatment.traded, market_value=1000.0, cost_basis=800.0)
    bond = _asset("Bond_CMB_CNY", treatment=Treatment.balance_only, market_value=1000.0, cost_basis=None)

    for a in (cash, traded, bond):
        assert a.has_known_cost is (a.treatment is not Treatment.balance_only), a.asset_id


def test_manual_realized_only_stays_out_of_the_cost_denominators():
    """Anti-vacuity: a manual-realized-only asset has treatment=manual (so the
    OLD enum check would let it in) but an unknown cost (so the NEW check keeps
    it out). Its value must not become phantom profit."""
    bond = _asset(
        "Bond_CMB_CNY",
        treatment=Treatment.manual,     # owner logged a profit -> classification flips
        market_value=190_353.00,
        cost_basis=None,                # ...but the COST is still unknown
        realized=4_200.00,              # the logged coupon
    )

    # The old predicate would have admitted it — that is precisely the bug.
    assert (bond.treatment is not Treatment.balance_only) is True
    assert bond.has_known_cost is False

    totals = summary_totals([bond], excluded_ids=frozenset(), apply_name_filter=False)

    # Value counts in net worth (it is real money) ...
    assert totals["net_worth"] == pytest.approx(190_353.00)
    # ... but contributes nothing to the cost/return denominators ...
    assert totals["total_cost_basis"] == pytest.approx(0.0)
    assert totals["measurable_value"] == pytest.approx(0.0)
    assert totals["total_unrealized"] == pytest.approx(0.0)
    # ... while the logged profit still flows through the realized channel.
    assert totals["total_realized"] == pytest.approx(4_200.00)
    assert totals["total_lifetime"] == pytest.approx(4_200.00)


def test_manual_cost_logged_does_enter_the_denominators():
    """The other half of the rule: once the owner logs a COST, the asset becomes
    measurable and joins the denominators at the owner's figure."""
    bond = _asset(
        "Bond_CMB_USD",
        treatment=Treatment.manual,
        market_value=190_353.00,
        cost_basis=185_000.00,          # owner-entered
        realized=0.0,
    )

    assert bond.has_known_cost is True
    totals = summary_totals([bond], excluded_ids=frozenset(), apply_name_filter=False)

    assert totals["total_cost_basis"] == pytest.approx(185_000.00)
    assert totals["measurable_value"] == pytest.approx(190_353.00)
    assert totals["total_unrealized"] == pytest.approx(5_353.00)


def test_engine_invariant_holds_on_base_classification():
    """The equivalence the refactor rests on, stated as an invariant so a future
    change that gives a traded asset a None cost has to face it explicitly."""
    assets = [
        _asset("CASH_X", treatment=Treatment.cash, market_value=10.0, cost_basis=10.0),
        _asset("US_STK_VOO", treatment=Treatment.traded, market_value=10.0, cost_basis=8.0),
        _asset("Bond_CMB_CNY", treatment=Treatment.balance_only, market_value=10.0, cost_basis=None),
    ]
    for a in assets:
        if a.treatment is Treatment.balance_only:
            assert a.cost_basis_cny is None
        else:
            assert a.cost_basis_cny is not None
