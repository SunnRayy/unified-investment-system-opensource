"""Valuation dashboard API endpoints."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator

from src.api.dependencies import get_db
from src.data_manager.currency_converter import get_currency_service
from src.database.connector import DatabaseConnector
from src.services.valuation.collector import ValuationCollector, _refresh_lock
from src.services.valuation.fetchers.akshare_index_pe import (
    fetch_cn_index_history,
)
from src.services.valuation.percentile import compute_percentile
from src.services.valuation.rate_adjust import adjusted_factor
from src.services.valuation.reference import get_all_references, upsert_reference
from src.services.valuation.canonical_underlyings import apply_canonical_signal_dedup
from src.services.valuation.bucket_suppression import apply_bucket_signal_suppression
from src.api.routes._errors import api_error_response
from src.storage.gcs_flush import mark_dirty

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/valuation", tags=["Valuation"])


def _open_writable(db: DatabaseConnector) -> DatabaseConnector:
    """Open a writable DuckDB connection (follow market_data.py pattern)."""
    if getattr(db, "read_only", False) is True:
        db_path = db.db_path
        db.close()
        return DatabaseConnector(db_path, read_only=False)
    return db


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/snapshot/latest")
async def get_latest_snapshots(db: DatabaseConnector = Depends(get_db)) -> list[dict]:
    """Per-ticker latest valuation snapshot."""
    try:
        rows = db.execute("""
            WITH latest AS (
                SELECT ticker, row_kind, MAX(snapshot_date) AS max_date
                FROM valuation_snapshots
                GROUP BY ticker, row_kind
            )
            SELECT vs.id, vs.snapshot_date, vs.ticker, vs.display_name, vs.row_kind,
                   vs.linked_ticker, vs.asset_id, vs.asset_class,
                   vs.pe_ttm, vs.pe_forward, vs.pb_ratio, vs.peg_ratio,
                   vs.fcf_yield, vs.dividend_yield, vs.ev_ebitda, vs.sec_yield,
                   COALESCE(vs.pe_ttm_pct, vs.pe_fwd_pct, vs.pb_pct) AS percentile_value,
                   CASE
                       WHEN vs.pe_ttm_pct IS NOT NULL THEN 'pe_ttm'
                       WHEN vs.pe_fwd_pct IS NOT NULL THEN 'pe_forward'
                       WHEN vs.pb_pct IS NOT NULL THEN 'pb_ratio'
                       ELSE NULL
                   END AS percentile_metric,
                   vs.pct_years,
                   vs.valuation_signal, vs.signal_basis, vs.rate_adjustment_factor,
                   vs.data_source, vs.is_estimable, vs.notes, vs.created_at
            FROM valuation_snapshots vs
            JOIN latest l ON vs.ticker = l.ticker AND vs.row_kind = l.row_kind AND vs.snapshot_date = l.max_date
            ORDER BY vs.row_kind, vs.asset_class, vs.ticker
        """).df()
        records = rows.to_dict(orient="records")
        # PRD 2026-07-07 F4.2: dedup conflicting signals for instruments that
        # map to the same canonical underlying (e.g. VOO -> SP500) — one
        # underlying, one signal. Read/serving-path only; does not touch how
        # either raw series is ingested or stored.
        records = apply_canonical_signal_dedup(records)
        # F4.5: compliance-bucket assets never show a valuation signal
        # (execution progress instead); ratio-bucket assets show band
        # position only (current % vs target band, no valuation/P&L signal).
        return apply_bucket_signal_suppression(db, records)
    except Exception as exc:
        logger.error("get_latest_snapshots error: %s", exc)
        return api_error_response(exc, context="valuation-snapshots")


@router.get("/snapshot/history")
async def get_snapshot_history(
    ticker: str,
    days: int = 365,
    db: DatabaseConnector = Depends(get_db),
) -> list[dict]:
    try:
        rows = db.execute(
            "SELECT * FROM valuation_snapshots "
            "WHERE ticker = ? AND snapshot_date >= CURRENT_DATE - INTERVAL ? DAY "
            "ORDER BY snapshot_date DESC",
            (ticker, days)
        ).df()
        return rows.to_dict(orient="records")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/refresh")
async def trigger_refresh(db: DatabaseConnector = Depends(get_db)) -> dict[str, Any]:
    if _refresh_lock.locked():
        raise HTTPException(status_code=409, detail="refresh already in progress")
    writable_db = _open_writable(db)
    collector = ValuationCollector(writable_db)
    result = await collector.refresh_all()
    if result.status == "rate_limited":
        raise HTTPException(status_code=429, detail="rate limit: 3 refreshes per day")
    mark_dirty()
    return {
        "status": result.status,
        "refreshed_count": result.refreshed_count,
        "failed": result.failed,
    }


@router.get("/reference")
async def get_reference(db: DatabaseConnector = Depends(get_db)) -> list[dict]:
    refs = get_all_references(db)
    return [
        {"ticker": r.ticker, "metric": r.metric, "low_threshold": r.low_threshold,
         "high_threshold": r.high_threshold, "historical_mean": r.historical_mean,
         "rate_sensitive": r.rate_sensitive,
         "pct_low_threshold": r.pct_low_threshold,
         "pct_high_threshold": r.pct_high_threshold}
        for r in refs
    ]


class UpdateReferenceRequest(BaseModel):
    low_threshold: float
    high_threshold: float
    historical_mean: Optional[float] = None
    rate_sensitive: bool = False
    notes: Optional[str] = None
    pct_low_threshold: float = 30.0
    pct_high_threshold: float = 70.0

    @model_validator(mode="after")
    def check_thresholds(self):
        if self.high_threshold <= self.low_threshold:
            raise ValueError("high_threshold must be greater than low_threshold")
        return self


class CreateWatchlistRequest(BaseModel):
    ticker: str
    display_name: str
    asset_type: str
    note: Optional[str] = None


@router.put("/reference/{ticker}/{metric}")
async def update_reference(
    ticker: str,
    metric: str,
    body: UpdateReferenceRequest,
    db: DatabaseConnector = Depends(get_db),
) -> dict[str, Any]:
    writable_db = _open_writable(db)
    try:
        upsert_reference(writable_db, ticker, metric, body.low_threshold, body.high_threshold,
                         body.historical_mean, body.rate_sensitive, body.notes,
                         pct_low_threshold=body.pct_low_threshold,
                         pct_high_threshold=body.pct_high_threshold)
        mark_dirty()
        return {"ticker": ticker, "metric": metric, "status": "updated"}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/macro")
async def get_macro(db: DatabaseConnector = Depends(get_db)) -> dict[str, Any]:
    FALLBACK = 4.26
    us10y = FALLBACK
    fallback_used = True
    source = "fallback"
    try:
        row = db.execute(
            "SELECT value, updated_at FROM market_sentiment_cache "
            "WHERE indicator_key = 'us10y' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row and row[0] is not None:
            val = float(row[0])
            if 0.1 < val < 20:
                us10y = val
                fallback_used = False
                source = f"market_sentiment_cache (as of {row[1]})"
    except Exception as exc:
        logger.warning("macro endpoint: %s", exc)

    try:
        adj = adjusted_factor(us10y)
    except ValueError:
        adj = 1.0

    usd_cny: float | None = None
    try:
        rate = get_currency_service().get_latest_rate("USD", "CNY")
        if rate and 5.0 < rate < 10.0:
            usd_cny = rate
    except Exception as exc:
        logger.warning("macro endpoint: usd_cny fetch failed: %s", exc)

    return {
        "us10y": us10y,
        "rate_adjustment_factor": adj,
        "source": source,
        "fallback_used": fallback_used,
        "usd_cny": usd_cny,
    }


@router.get("/percentile/{ticker}/{metric}")
async def get_percentile_detail(
    ticker: str,
    metric: str,
    years: int = Query(default=10, ge=0, description="Lookback window in years; 0 = full history"),
    db: DatabaseConnector = Depends(get_db),
) -> dict[str, Any]:
    cutoff = (date.today() - timedelta(days=years * 365)).isoformat() if years > 0 else None
    history_query = (
        "SELECT observed_date, value, source FROM valuation_history "
        "WHERE ticker = ? AND metric = ?"
        + (" AND observed_date >= ?" if cutoff else "")
        + " ORDER BY observed_date ASC"
    )
    history_params = (ticker, metric, cutoff) if cutoff else (ticker, metric)
    rows = db.execute(history_query, history_params).fetchall()

    latest_row = db.execute(
        "SELECT value, source FROM valuation_history "
        "WHERE ticker = ? AND metric = ? ORDER BY observed_date DESC LIMIT 1",
        (ticker, metric),
    ).fetchone()

    latest_value = float(latest_row[0]) if latest_row and latest_row[0] is not None else None
    source = latest_row[1] if latest_row else None
    sample_size = 0
    percentile = None
    years_actual = 0

    if rows:
        values = [float(r[1]) for r in rows if r[1] is not None]
        sample_size = len(values)
        if latest_value is not None and sample_size > 0:
            dates = [r[0] for r in rows if r[0] is not None]
            date_range_days = (dates[-1] - dates[0]).days if len(dates) >= 2 else 0
            percentile, years_actual = compute_percentile(values, latest_value, date_range_days=date_range_days)

    return {
        "ticker": ticker,
        "metric": metric,
        "latest_value": latest_value,
        "percentile": percentile,
        "years": years_actual,
        "window_years": years,
        "sample_size": sample_size,
        "source": source,
        "has_seed_history": sample_size > 0,
        "note": None if sample_size > 0 else "history_accumulating",
    }


@router.get("/watchlist")
async def get_watchlist(db: DatabaseConnector = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        "SELECT ticker, display_name, asset_type, note, added_at "
        "FROM valuation_watchlist ORDER BY added_at DESC, ticker"
    ).df()
    return rows.to_dict(orient="records")


def _backfill_cn_index_history_task(ticker: str, db: DatabaseConnector) -> None:
    """Background: seed valuation_history from akshare full history for a CN_INDEX watchlist item."""
    try:
        collector = ValuationCollector(db)
        if collector._needs_history_backfill(ticker, "pe_ttm"):
            history = fetch_cn_index_history(ticker)
            if history:
                collector._bulk_insert_history(ticker, "pe_ttm", history, "akshare_index_pe")
                logger.info("Backfilled %d history rows for watchlist ticker %s", len(history), ticker)
    except Exception as exc:
        logger.warning("History backfill failed for %s: %s", ticker, exc)


@router.post("/watchlist")
async def create_watchlist(
    body: CreateWatchlistRequest,
    background_tasks: BackgroundTasks,
    db: DatabaseConnector = Depends(get_db),
) -> dict[str, Any]:
    writable_db = _open_writable(db)

    existing = writable_db.execute(
        "SELECT COUNT(*) FROM valuation_watchlist WHERE ticker = ?", (body.ticker,)
    ).fetchone()
    already_exists = existing and int(existing[0]) > 0

    writable_db.execute(
        "INSERT INTO valuation_watchlist (ticker, display_name, asset_type, note, added_at) "
        "VALUES (?, ?, ?, ?, NOW()) ON CONFLICT (ticker) DO NOTHING",
        (body.ticker, body.display_name, body.asset_type, body.note),
    )

    if body.asset_type == "CN_INDEX":
        background_tasks.add_task(_backfill_cn_index_history_task, body.ticker, writable_db)
        backfill_status = "seeded"
    elif body.asset_type in ("US_INDEX", "HK_INDEX", "CN_MARKET", "US_STOCK"):
        backfill_status = "deferred"
    else:
        backfill_status = "unsupported"

    mark_dirty()
    return {
        "ticker": body.ticker,
        "status": "exists" if already_exists else "created",
        "backfill_status": backfill_status,
    }


@router.delete("/watchlist/{ticker}")
async def delete_watchlist(
    ticker: str,
    db: DatabaseConnector = Depends(get_db),
) -> dict[str, Any]:
    writable_db = _open_writable(db)
    writable_db.execute("DELETE FROM valuation_watchlist WHERE ticker = ?", (ticker,))
    mark_dirty()
    return {"ticker": ticker, "status": "deleted"}
