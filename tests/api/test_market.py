"""Tests for Market API endpoints."""
import asyncio
from unittest.mock import MagicMock
import numpy as np


def test_market_regime_endpoint():
    from src.api.routes.market import get_market_regime

    db = MagicMock()
    # Return 100 days of price data for SPY
    np.random.seed(42)
    prices = 100 * np.cumprod(1 + np.random.normal(0.0005, 0.01, 100))
    rows = [(f"2025-{(i//30)+1:02d}-{(i%28)+1:02d}", float(p)) for i, p in enumerate(prices)]
    db.execute.return_value = MagicMock(fetchall=MagicMock(return_value=rows))

    result = asyncio.run(get_market_regime(db=db))
    assert "trend" in result
    assert result["trend"] in ("Bull", "Neutral", "Bear", "Unknown")
