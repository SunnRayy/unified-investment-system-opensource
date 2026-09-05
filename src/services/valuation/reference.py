"""CRUD for valuation_reference table."""
from __future__ import annotations

from src.services.valuation.signal import ValuationReference

# Default reference thresholds for tracked indexes (keyed by index name / proxy ticker).
# Seeded once; upsert-safe so re-running is idempotent.
_INDEX_REFERENCE_DEFAULTS: list[tuple] = [
    # (ticker, metric, low, high, mean, rate_sensitive, notes)
    ("沪深300", "pe_ttm", 10.0, 15.0, 12.5, False,
     "沪深300 10yr: min~8.5x(2018底), median~12.5x, max~17.5x(2021)"),
    ("中证500", "pe_ttm", 16.0, 35.0, 22.0, False,
     "中证500 10yr: min~14x(2018底), median~22x, max~42x(2015牛市); high=35 ~75th pct"),
    ("上证50",  "pe_ttm",  8.0, 13.0, 10.0, False,
     "上证50 10yr: min~7x, median~10x, max~16x"),
    ("3033.HK", "pe_ttm", 15.0, 35.0, 28.0, False,
     "恒生科技 since 2020.7 via 3033.HK ETF proxy (spot-only daily accumulation)"),
    ("科创50",  "pe_ttm", 25.0, 70.0, 45.0, False,
     "科创50 since 2019.7: PE高波动(成长/亏损股)。历史区间~25x-200x"),
    ("创业板",  "pe_ttm", 20.0, 45.0, 35.0, False,
     "创业板 10yr: min~20x(2018底), median~35x, max~140x(2015牛市)"),
    ("S&P500",   "pe_ttm", 15.0, 25.0, 20.0, False,
     "S&P500 10yr TTM: min~13(2020 COVID), median~20, max~35(2021); percentile-based signal primary"),
    ("Nasdaq100", "pe_ttm", 20.0, 40.0, 30.0, False,
     "Nasdaq100 10yr TTM: tech-heavy, higher structural PE; percentile-based signal primary"),
]

_SELECT_COLS = (
    "ticker, metric, low_threshold, high_threshold, historical_mean, rate_sensitive, "
    "COALESCE(pct_low_threshold, 30.0), COALESCE(pct_high_threshold, 70.0)"
)


def _row_to_ref(r) -> ValuationReference:
    return ValuationReference(
        ticker=r[0], metric=r[1],
        low_threshold=float(r[2]), high_threshold=float(r[3]),
        historical_mean=float(r[4]) if r[4] is not None else None,
        rate_sensitive=bool(r[5]),
        pct_low_threshold=float(r[6]) if r[6] is not None else 30.0,
        pct_high_threshold=float(r[7]) if r[7] is not None else 70.0,
    )


def get_all_references(db) -> list[ValuationReference]:
    rows = db.execute(
        f"SELECT {_SELECT_COLS} FROM valuation_reference ORDER BY ticker, metric"
    ).fetchall()
    return [_row_to_ref(r) for r in rows]


def get_reference(db, ticker: str, metric: str) -> ValuationReference | None:
    row = db.execute(
        f"SELECT {_SELECT_COLS} FROM valuation_reference WHERE ticker = ? AND metric = ?",
        (ticker, metric)
    ).fetchone()
    return _row_to_ref(row) if row else None


def upsert_reference(
    db, ticker: str, metric: str,
    low_threshold: float, high_threshold: float,
    historical_mean: float | None,
    rate_sensitive: bool, notes: str | None,
    pct_low_threshold: float = 30.0,
    pct_high_threshold: float = 70.0,
) -> None:
    if high_threshold <= low_threshold:
        raise ValueError(f"high_threshold ({high_threshold}) must be > low_threshold ({low_threshold})")
    db.execute("""
        INSERT INTO valuation_reference (
            ticker, metric, low_threshold, high_threshold,
            historical_mean, rate_sensitive, notes,
            pct_low_threshold, pct_high_threshold, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())
        ON CONFLICT (ticker, metric) DO UPDATE SET
          low_threshold = EXCLUDED.low_threshold,
          high_threshold = EXCLUDED.high_threshold,
          historical_mean = EXCLUDED.historical_mean,
          rate_sensitive = EXCLUDED.rate_sensitive,
          notes = EXCLUDED.notes,
          pct_low_threshold = EXCLUDED.pct_low_threshold,
          pct_high_threshold = EXCLUDED.pct_high_threshold,
          updated_at = NOW()
    """, (ticker, metric, low_threshold, high_threshold, historical_mean,
          rate_sensitive, notes, pct_low_threshold, pct_high_threshold))


def seed_index_references(db) -> int:
    """Upsert default index-name reference rows.

    Always syncs defaults from code — use the PUT /reference endpoint to customise per-ticker.
    Returns number of rows written.
    """
    for ticker, metric, low, high, mean, rate_sensitive, notes in _INDEX_REFERENCE_DEFAULTS:
        upsert_reference(db, ticker, metric, low, high, mean, rate_sensitive, notes)
    return len(_INDEX_REFERENCE_DEFAULTS)
