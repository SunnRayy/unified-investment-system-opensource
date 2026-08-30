from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass
class OHLCVBar:
    code: str
    date: date
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: float
    volume: Optional[float]  # None for CN funds
    pct_chg: Optional[float]
    source: str


@dataclass
class RealtimeQuote:
    code: str
    price: float
    change_pct: Optional[float]
    volume: Optional[float]
    timestamp: datetime
    source: str
    as_of_date: date = date.min
