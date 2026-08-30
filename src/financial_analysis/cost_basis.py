
"""FIFO Cost Basis Calculator."""

import logging
from dataclasses import dataclass, field
from typing import List, Tuple
import pandas as pd

logger = logging.getLogger(__name__)

@dataclass
class PurchaseLot:
    """Represents a single purchase lot of an asset."""
    lot_date: pd.Timestamp
    quantity: float
    price_per_unit: float
    # Total cost basis for the ORIGINAL quantity. 
    # Or typically we track cost basis for REMAINING quantity?
    # Logic: cost_basis = quantity * price (roughly).
    # But usually we store the ACTUAL amount paid (including fees maybe).
    # Here let's store unit cost and multiply by quantity for remaining cost basis to avoid floating point drift on split?
    # Or store `cost_basis` as the cost of the *remaining* shares.
    cost_basis: float 
    
    # Store initial mostly for reference if needed
    initial_quantity: float = field(init=False)
    
    def __post_init__(self):
        self.initial_quantity = self.quantity
        # Normalize quantity to be positive for the lot
        if self.quantity < 0:
            logger.warning(f"PurchaseLot created with negative quantity {self.quantity}. Abs taken.")
            self.quantity = abs(self.quantity)
        
        # Ensure cost basis is positive for the lot value
        if self.cost_basis < 0:
             self.cost_basis = abs(self.cost_basis)
             
    @property
    def remaining_quantity(self) -> float:
        return self.quantity

    def is_empty(self) -> bool:
        return self.quantity <= 1e-9

    def sell_shares(self, sell_quantity: float) -> Tuple[float, float]:
        """
        Sell shares from this lot.
        Returns (quantity_sold, cost_basis_of_sold_shares)
        """
        if self.is_empty():
            return 0.0, 0.0
            
        shares_to_sell = min(sell_quantity, self.quantity)
        fraction = shares_to_sell / self.quantity if self.quantity > 0 else 0
        
        cost_of_sold = self.cost_basis * fraction
        
        # Update lot state
        self.quantity -= shares_to_sell
        self.cost_basis -= cost_of_sold
        
        return shares_to_sell, cost_of_sold


class CostBasisCalculator:
    """Calculates FIFO cost basis in the asset's native currency.

    Works in the native currency of the asset (USD for Schwab/RSU, CNY for
    others). FX conversion is the caller's responsibility at display time
    using today's rate, not historical rates. See performance.py header
    comment for the full method.
    """
    
    def __init__(self, asset_id: str):
        self.asset_id = asset_id
        self.lots: List[PurchaseLot] = []
        self.realized_pnl: float = 0.0
        self.total_sold_amount: float = 0.0
        self.native_currency: str = 'CNY'
        
    def process_transactions(self, transactions: pd.DataFrame):
        """
        Process a DataFrame of transactions.
        Expected columns: transaction_type, quantity, price_unit, amount_net, (currency - optional)
        Index: DatetimeIndex (transaction date)
        """
        # Ensure sorted by date
        sorted_tx = transactions.sort_index()
        
        for tx_date, tx in sorted_tx.iterrows():
            self._process_single_transaction(tx, tx_date)

    def _process_single_transaction(self, tx: pd.Series, tx_date: pd.Timestamp):
        tx_type = tx.get('transaction_type', '').lower()
        
        # Safely get float values, handling None/NaN
        qty = tx.get('quantity')
        qty = float(qty) if qty is not None else 0.0
        
        amount = tx.get('amount_net')
        amount = float(amount) if amount is not None else 0.0

        price = tx.get('price_unit')
        price = float(price) if price is not None else 0.0
        
        currency = tx.get('currency', 'CNY')
        if pd.isna(currency):
            currency = 'CNY'
        else:
            currency = str(currency)

        if currency != 'CNY' and self.native_currency == 'CNY':
            self.native_currency = currency
        
        # Normalize signs if needed. 
        # By convention here:
        # Buy: qty usually positive
        # Sell: qty usually negative (in some systems) or positive (with type='Sell')
        # We look at TYPE.
        
        if 'dividend' in tx_type and 'reinvest' in tx_type:
            # Treated as Reinvestment -> Cost Basis = Market Value (Taxed Income)
            # CN/US Tax Rule: The dividend is income, reinvestment is a purchase at FMV.
            cost_basis = abs(amount) if abs(amount) > 0 else (abs(qty) * abs(price))
            self._add_lot(tx_date, abs(qty), abs(price), cost_basis)

        elif 'buy' in tx_type or 'vest' in tx_type:
             # Standard Purchase or RSU Vest (Taxable Event) -> Cost Basis = Market Value
            self._add_lot(tx_date, abs(qty), abs(price), abs(amount))
            
        elif 'dividend' in tx_type:
            # Cash Dividend -> Realized Profit
            self.realized_pnl += abs(amount)
            
        elif 'sell' in tx_type or 'redemption' in tx_type:
            # Handle Sale
            self._sell_shares(abs(qty), abs(amount))

        elif 'transfer' in tx_type:
            # ACAT/security transfer (transfer_in / transfer_out) is NON-REALIZING:
            # lots persist (cost basis carries across brokers). (C3.3)
            # NOTE (Attribution & Flows WS-3.1, V79): the Schwab 'Security Transfer'
            # action now maps to the pseudo-type 'transfer', resolved by quantity
            # sign at the reader hook into 'transfer_out'/'transfer_in' — so both
            # legs DO enter this branch now (previously they fell through to
            # 'other', a no-op via the same fall-off-the-end path). Behavior is
            # unchanged either way: both branches are no-ops, so both legs remain
            # non-realizing before and after V79.
            pass

        elif tx_type == 'tax_adjustment':
            # Tax Adjustment (e.g., NRA withholding on dividends) -> Reduces Realized PnL
            # Amount is negative in source (money taken), so abs() makes it positive,
            # and we subtract it from realized P&L.
            self.realized_pnl -= abs(amount)

        # TODO: Handle Splits, Mergers if needed (out of scope for now)

    def _add_lot(self, date: pd.Timestamp, qty: float, price: float, cost: float):
        if qty > 0:
            lot = PurchaseLot(date, qty, price, cost)
            self.lots.append(lot)
            
    def _sell_shares(self, qty_to_sell: float, proceeds: float):
        remaining_to_sell = qty_to_sell
        total_cost_basis_sold = 0.0
        
        # FIFO: Consume from oldest lots (front of list)
        # We iterate and remove empty lots
        
        while remaining_to_sell > 1e-9 and self.lots:
            current_lot = self.lots[0]
            sold_qty, sold_cost = current_lot.sell_shares(remaining_to_sell)
            
            remaining_to_sell -= sold_qty
            total_cost_basis_sold += sold_cost
            
            if current_lot.is_empty():
                self.lots.pop(0)
                
        # Calculate Realized PnL for this transaction
        # PnL = Proceeds - Cost Basis
        # Note: If we sell MORE than we have (short?), remaining_to_sell > 0.
        # This simple calculator assumes long-only. Shorting requires negative lots or different logic.
        if remaining_to_sell > 1e-9:
             logger.warning(f"Sold more shares than held for {self.asset_id}. Shorting not fully supported.")
        
        # We attribute PnL proportionally to the actual sold amount? 
        # Or simple: PnL += (Proceeds * (actual_sold / asked_sell)) - Cost_Basis
        # Here we assume proceeds covers the whole qty_to_sell.
        # If we couldn't sell everything, we should scale proceeds?
        # Let's assume valid data for now.
        
        self.realized_pnl += (proceeds - total_cost_basis_sold)
        self.total_sold_amount += proceeds

    def get_current_position(self) -> float:
        return sum(lot.quantity for lot in self.lots)

    def get_total_cost_basis(self) -> float:
        return sum(lot.cost_basis for lot in self.lots)

    def get_average_cost(self) -> float:
        total_qty = self.get_current_position()
        if total_qty == 0:
            return 0.0
        return self.get_total_cost_basis() / total_qty
