"""Tests for src.services.currency — shared FX / cost-basis helpers."""
from unittest.mock import MagicMock, patch

import pytest


def test_get_today_usd_cny_rate_returns_float():
    """get_today_usd_cny_rate() must return a float."""
    mock_service = MagicMock()
    mock_service.get_latest_rate.return_value = 7.25

    with patch("src.services.currency.get_currency_service", return_value=mock_service):
        from src.services.currency import get_today_usd_cny_rate

        rate = get_today_usd_cny_rate()

    assert isinstance(rate, float)
    assert rate == 7.25


def test_get_today_usd_cny_rate_fallback_to_7():
    """When get_latest_rate returns None, the fallback must be 7.0."""
    mock_service = MagicMock()
    mock_service.get_latest_rate.return_value = None

    with patch("src.services.currency.get_currency_service", return_value=mock_service):
        from src.services.currency import get_today_usd_cny_rate

        rate = get_today_usd_cny_rate()

    assert rate == 7.0


def test_calculate_cost_basis_cny_usd_asset():
    """USD asset: cost_basis = cost_price_unit * quantity * today_fx."""
    from src.services.currency import calculate_cost_basis_cny

    result = calculate_cost_basis_cny(
        market_value=770.0,
        quantity=1.0,
        cost_price_unit=100.0,
        currency="USD",
        top_class="Equity",
        sub_class="US Equity",
        today_fx=7.0,
    )
    assert result == pytest.approx(700.0)


def test_calculate_cost_basis_cny_cny_asset():
    """CNY asset: cost_basis = cost_price_unit * quantity (no FX)."""
    from src.services.currency import calculate_cost_basis_cny

    result = calculate_cost_basis_cny(
        market_value=600.0,
        quantity=10.0,
        cost_price_unit=50.0,
        currency="CNY",
        top_class="Equity",
        sub_class="CN Equity",
        today_fx=7.0,
    )
    assert result == pytest.approx(500.0)


def test_calculate_cost_basis_cny_cash_equivalent():
    """Cash-equivalent top_class must return market_value directly (zero P&L)."""
    from src.services.currency import calculate_cost_basis_cny

    result = calculate_cost_basis_cny(
        market_value=500.0,
        quantity=500.0,
        cost_price_unit=1.0,
        currency="CNY",
        top_class="Cash (现金)",
        sub_class="Cash",
        today_fx=7.0,
    )
    assert result == pytest.approx(500.0)


def test_is_cash_equivalent_asset():
    """Spot-check is_cash_equivalent_asset for in- and out-of-set values."""
    from src.services.currency import is_cash_equivalent_asset

    # Members of CASH_CLASS_DISPLAY_VALUES
    assert is_cash_equivalent_asset("Cash (现金)", "anything") is True
    assert is_cash_equivalent_asset("Cash Checking", "anything") is True
    assert is_cash_equivalent_asset("anything", "Bank Wealth") is True

    # Non-members
    assert is_cash_equivalent_asset("Equity", "US Equity") is False
    assert is_cash_equivalent_asset("Fixed Income", "CN Bond") is False


def test_is_cash_equivalent_asset_money_market():
    """Money market funds (issue #18) must be cash-equivalent → zero unrealized P&L.

    The sub_class display name for money market funds resolves to
    'Money Market (货基)'; the Performance report previously omitted this from
    CASH_CLASS_DISPLAY_VALUES, so funds like 示例流动货币B showed spurious profits.
    """
    from src.services.currency import is_cash_equivalent_asset

    assert is_cash_equivalent_asset("anything", "Money Market (货基)") is True
    assert is_cash_equivalent_asset("Money Market", "anything") is True
    assert is_cash_equivalent_asset("anything", "货币市场") is True


def test_calculate_cost_basis_cny_money_market_zero_pnl():
    """Money market cost basis must equal market value (zero unrealized P&L)."""
    from src.services.currency import calculate_cost_basis_cny

    result = calculate_cost_basis_cny(
        market_value=10000.0,
        quantity=10000.0,
        cost_price_unit=0.95,  # would otherwise produce a spurious 500 CNY gain
        currency="CNY",
        top_class="Cash (现金)",
        sub_class="Money Market (货基)",
        today_fx=7.0,
    )
    assert result == pytest.approx(10000.0)
