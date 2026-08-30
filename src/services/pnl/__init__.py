"""The single P&L engine (plan 2026-08-02 §B) — read-only.

One current active-holdings query (``snapshot``), one set of pure leaf helpers
(``pnl_math``), one orchestration entry point (``engine.compute_portfolio_pnl``)
returning the canonical model (``models``). Reporting surfaces are thin
formatters over ``compute_portfolio_pnl``.
"""
from src.services.pnl.aggregate import summary_totals
from src.services.pnl.engine import compute_portfolio_pnl
from src.services.pnl.models import (
    AssetPnL,
    ManualPnL,
    PortfolioPnL,
    Scope,
    Treatment,
)
from src.services.pnl.pnl_math import calculate_unrealized_pl_values

__all__ = [
    "compute_portfolio_pnl",
    "summary_totals",
    "AssetPnL",
    "ManualPnL",
    "PortfolioPnL",
    "Scope",
    "Treatment",
    "calculate_unrealized_pl_values",
]
