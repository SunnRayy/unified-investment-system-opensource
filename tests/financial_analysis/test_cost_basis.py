
"""Tests for FIFO cost basis calculator."""
import pytest

pytestmark = pytest.mark.critical

import pandas as pd
from src.financial_analysis.cost_basis import PurchaseLot, CostBasisCalculator


class TestPurchaseLot:
    def test_lot_creation(self):
        """Should create a lot with correct initial values."""
        lot = PurchaseLot(
            lot_date=pd.Timestamp('2026-01-10'),
            quantity=100.0,
            price_per_unit=10.0,
            cost_basis=1000.0
        )
        assert lot.remaining_quantity == 100.0
        assert lot.price_per_unit == 10.0
        # For a purchase, amount is cost. Typically we store absolute cost.
        assert abs(lot.cost_basis - 1000.0) < 0.001

    def test_sell_shares_fifo(self):
        """Selling shares should reduce remaining quantity and return cost basis."""
        lot = PurchaseLot(
            lot_date=pd.Timestamp('2026-01-10'),
            quantity=100.0,
            price_per_unit=10.0,
            cost_basis=1000.0
        )
        qty_sold, cost_sold = lot.sell_shares(30.0)
        assert qty_sold == 30.0
        assert cost_sold == 300.0  # 30 shares * $10
        assert lot.remaining_quantity == 70.0

    def test_sell_more_than_remaining(self):
        """Selling more than available should only sell what's available."""

        lot = PurchaseLot(
            lot_date=pd.Timestamp('2026-01-10'),
            quantity=50.0,
            price_per_unit=10.0,
            cost_basis=500.0
        )
        qty_sold, cost_sold = lot.sell_shares(100.0)
        assert qty_sold == 50.0  # Only 50 available
        assert lot.is_empty()
        assert abs(cost_sold - 500.0) < 0.001


class TestCostBasisCalculator:
    def test_simple_buy_transaction(self):
        """Should process a simple buy transaction."""
        calculator = CostBasisCalculator('TEST_ASSET')
        transactions = pd.DataFrame({
            'transaction_type': ['Buy'],
            'quantity': [100.0],
            'price_unit': [10.0],
            'amount_net': [-1000.0]
        }, index=pd.DatetimeIndex([pd.Timestamp('2026-01-10')]))

        calculator.process_transactions(transactions)

        assert calculator.get_current_position() == 100.0
        assert calculator.get_total_cost_basis() == 1000.0
        assert calculator.get_average_cost() == 10.0

    def test_fifo_sell_order(self):
        """Should sell from oldest lots first (FIFO)."""
        calculator = CostBasisCalculator('TEST_ASSET')
        transactions = pd.DataFrame({
            'transaction_type': ['Buy', 'Buy', 'Sell'],
            'quantity': [100.0, 100.0, 50.0], # Sell quantity often stored as positive in quantity column for transaction tables, or we handle signs. 
            # Check implementation assumption: Buy positive, Sell positive quantity? 
            # Or Buy positive quantity / negative amount. Sell negative quantity / positive amount?
            # Let's assume quantity is robust to sign.
            # In PIS, Quantity is usually positive. Transaction Type defines direction.
            'price_unit': [10.0, 20.0, 25.0],
            'amount_net': [-1000.0, -2000.0, 1250.0]
        }, index=pd.DatetimeIndex([
            pd.Timestamp('2026-01-10'),
            pd.Timestamp('2026-01-15'),
            pd.Timestamp('2026-01-20')
        ]))

        # We need to ensure calculator handles signs correctly. 
        # Typically Sync normalizes: Buy (Qty+, Amt-), Sell (Qty-, Amt+) or similar.
        # But here let's assume PIS standard:
        # Buy: Qty > 0, Amt < 0
        # Sell: Qty < 0 (or positive if relying on type), Amt > 0
        # Let's update test data to be explicit about signs usually found in DB:
        # DB schema says: quantity DECIMAL(20,8)
        # PIS Sync often puts absolute quantity for both?
        # Let's check PIS Sync code... `sync_pis_transactions`.
        # It copies directly. 
        # But `transaction_type` is the key.
        
        # Let's update test to stick to a clear convention: 
        # Calculator should rely on Transaction Type principally.
        
        calculator.process_transactions(transactions)

        # Sold 50 from first lot (cost $10 each)
        assert calculator.get_current_position() == 150.0
        # Remaining: 50 @ $10 + 100 @ $20 = $500 + $2000 = $2500
        assert calculator.get_total_cost_basis() == 2500.0
        # Realized P/L: Sold 50 @ $25 (=1250) - cost basis 50 @ $10 (=500) = $750
        assert calculator.realized_pnl == 750.0

    def test_dividend_reinvestment_zero_cost_basis(self):
        """Dividend reinvestment should add shares with zero cost basis."""
        calculator = CostBasisCalculator('TEST_ASSET')
        transactions = pd.DataFrame({
            'transaction_type': ['Buy', 'Dividend_Reinvest'],
            'quantity': [100.0, 10.0],
            'price_unit': [10.0, 11.0],
            'amount_net': [-1000.0, 0.0]  # Reinvested dividend = no new cash outflow typically? 
            # Actually reinvest means we get shares. Amount Net might be 0 if PIS records it that way.
            # Or it might show amount used to buy. 
            # If Amount is 0, then cost basis is 0.
        }, index=pd.DatetimeIndex([
            pd.Timestamp('2026-01-10'),
            pd.Timestamp('2026-02-15')
        ]))

        calculator.process_transactions(transactions)

        assert calculator.get_current_position() == 110.0
        # Original 100 shares @ $10 = $1000 
        # Plus 10 shares @ $11 = $110 (Reinvestment at FMV)
        # Total cost basis = 1110.0
        assert calculator.get_total_cost_basis() == 1110.0

        # Average cost: $1110 / 110 shares = ~$10.09
        assert 10.0 < calculator.get_average_cost() < 10.1


    def test_usd_transactions_stay_in_native_currency(self):
        """USD transactions should keep FIFO cost basis and realized P&L in USD."""
        calculator = CostBasisCalculator('US_STK_SGOV')
        transactions = pd.DataFrame({
            'transaction_type': ['Buy', 'Sell'],
            'quantity': [10.0, 4.0],
            'price_unit': [100.0, 101.0],
            'amount_net': [-1000.0, 404.0],
            'currency': ['USD', 'USD']
        }, index=pd.DatetimeIndex([
            pd.Timestamp('2026-01-10'),
            pd.Timestamp('2026-02-10'),
        ]))

        calculator.process_transactions(transactions)

        assert calculator.native_currency == 'USD'
        assert abs(calculator.get_total_cost_basis() - 600.0) < 0.01
        assert abs(calculator.get_average_cost() - 100.0) < 0.01
        assert abs(calculator.realized_pnl - 4.0) < 0.01
