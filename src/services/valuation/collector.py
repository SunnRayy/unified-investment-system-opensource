"""Valuation data collection orchestrator."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from src.database.connector import DatabaseConnector
from src.services.portfolio_semantics import fetch_wealthos_active_holdings
from src.services.valuation.rate_adjust import adjusted_factor
from src.services.valuation.percentile import compute_percentile
from src.services.valuation.signal import classify_signal, ValuationReference
from src.services.valuation.reference import get_all_references, seed_index_references
from src.services.valuation.fetchers.fmp import fetch_fmp_us_stock, fetch_fmp_us_history
from src.services.valuation.fetchers.yfinance_fetcher import (
    fetch_yfinance_us_stock,
    fetch_yfinance_etf_yield,
)
from src.services.valuation.fetchers.akshare_index_pe import (
    fetch_cn_index_snapshot,
    fetch_cn_index_history,
    fetch_cn_index_funddb,
    _CSINDEX_CODE_MAP,
)
from src.services.valuation.fetchers.akshare_market_pe import (
    fetch_cn_market_snapshot,
    fetch_cn_market_history,
)
from src.services.valuation.fetchers.hk_index import fetch_hk_index_snapshot
from src.services.valuation.fetchers.hk_baidu import (
    fetch_hk_index_pe_history,
    fetch_hk_index_pb_history,
)
from src.services.valuation.fetchers.us_index_pe import fetch_us_index_snapshot
from src.services.valuation.fetchers.multpl_fetcher import (
    fetch_multpl_sp500_pe_history,
    fetch_multpl_nasdaq100_pe_history,
)

logger = logging.getLogger(__name__)

BOND_ETF_TICKERS = frozenset({"SGOV", "IEF", "BND", "TLT", "SHY", "GOVT", "VGSH", "VCSH"})
NON_ESTIMABLE_TICKERS = frozenset({"IBIT", "FBTC", "WBTC", "GBTC"})

# Maps CN fund code → index Chinese name (used by stock_index_pe_lg).
# Funds in this map get a tracked_index companion row showing the underlying index PE.
FUND_TO_INDEX_MAP: dict[str, str] = {
    "110020": "沪深300",
    "000198": "中证500",
    "005827": "上证50",
}
# Maps ETF proxy ticker → US broad index name.
# Current PE from yfinance on the proxy; history seeded from multpl.com (S&P500 only).
US_INDEX_MAP: dict[str, str] = {
    "SPY": "S&P500",
    "QQQ": "Nasdaq100",
}
# HSTECH tracked via yfinance ETF proxy instead of akshare
HK_ETF_PROXY_MAP: dict[str, str] = {
    "519674": "3033.HK",   # HSTECH proxy
}
# Maps yfinance HK proxy ticker → Baidu Finance stock code (for history backfill).
# Baidu uses 5-digit numeric HK codes; 06969 = CSOP HSTECH ETF (same index as 3033.HK).
HK_BAIDU_SYMBOL_MAP: dict[str, str] = {
    "3033.HK": "06969",
}
# Market-segment indexes (科创50, 创业板) use stock_market_pe_lg
CN_MARKET_INDEXES = frozenset({"科创50", "创业板"})

_refresh_lock: asyncio.Lock = asyncio.Lock()
_daily_counts: dict[str, int] = {}

# Minimum years of actual price history required before a percentile is
# trusted to drive a valuation signal.  Below this threshold the percentile
# is silently dropped and the signal falls back to the absolute-threshold
# path (or 'N/A' if no thresholds exist).  8 daily data points (< 1 month)
# can produce a wildly misleading "6th percentile → LOW" on a historically
# expensive stock.
MIN_PCT_YEARS_FOR_SIGNAL = 3

# Throttle delay (seconds) between AKShare index PE requests to avoid rate-limiting.
# Extracted as a module-level constant so tests can patch it to 0 without changing
# production behaviour.
AKSHARE_INDEX_THROTTLE_SECONDS: float = 2.0

METRIC_FOR_CLASS = {
    "US_STOCK": "pe_forward",
    "US_ETF": "pe_forward",
    "US_BOND_ETF": "sec_yield",
    "CN_FUND": "pe_ttm",
    "HK_FUND": "pe_ttm",
}

# Maps CN index Chinese name → funddb numeric code (superset of _CSINDEX_CODE_MAP)
_INDEX_TO_FUNDDB_CODE: dict[str, str] = {
    **_CSINDEX_CODE_MAP,
    "科创50": "000688",
    "创业板":  "399006",
}


@dataclass
class RefreshResult:
    status: str
    refreshed_count: int = 0
    failed: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class ValuationCollector:
    def __init__(self, db: DatabaseConnector):
        self.db = db

    # ── Routing helpers ─────────────────────────────────────────────────────

    # Tickers that contain characters invalid for yfinance/FMP API calls.
    # Maps the canonical DB ticker → the API-safe form.
    _TICKER_API_ALIASES: dict[str, str] = {
        "BRK/B": "BRK-B",  # yfinance/FMP require dash, not slash
        "BRK/A": "BRK-A",
    }

    def _extract_raw_ticker(self, canonical_id: str) -> str:
        """Return the raw ticker from a canonical asset ID, normalized for API use.

        Strips common prefixes (US_STK_, US_ETF_, etc.) and applies
        _TICKER_API_ALIASES so that e.g. BRK/B becomes BRK-B for
        yfinance and FMP calls.
        """
        for prefix in ("US_STK_", "US_ETF_", "CN_FUND_", "HK_FUND_", "RSU_"):
            if canonical_id.startswith(prefix):
                raw = canonical_id[len(prefix):]
                return self._TICKER_API_ALIASES.get(raw, raw)
        raw = canonical_id
        return self._TICKER_API_ALIASES.get(raw, raw)

    def _detect_asset_type(self, canonical_id: str) -> str:
        raw = self._extract_raw_ticker(canonical_id)
        if raw in NON_ESTIMABLE_TICKERS or canonical_id in NON_ESTIMABLE_TICKERS:
            return "NON_ESTIMABLE"
        if raw in BOND_ETF_TICKERS:
            return "US_BOND_ETF"
        if canonical_id.startswith(("US_STK_", "RSU_")):
            return "US_STOCK"
        if canonical_id.startswith("US_ETF_"):
            return "US_BOND_ETF" if raw in BOND_ETF_TICKERS else "US_ETF"
        if canonical_id.startswith("CN_FUND_"):
            return "CN_FUND"
        if canonical_id.startswith("HK_FUND_"):
            return "HK_FUND"
        if "GOLD" in canonical_id or "ALTS" in canonical_id:
            return "NON_ESTIMABLE"
        return "UNKNOWN"

    # ── Macro data ───────────────────────────────────────────────────────────

    def _get_us10y(self) -> tuple[float, bool]:
        FALLBACK = 4.26
        try:
            row = self.db.execute(
                "SELECT value FROM market_sentiment_cache "
                "WHERE indicator_key = 'us10y' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            if row and row[0] is not None:
                val = float(row[0])
                if 0.1 < val < 20:
                    return val, False
        except Exception as exc:
            logger.warning("Could not read US10Y: %s", exc)
        return FALLBACK, True

    # ── valuation_history helpers ────────────────────────────────────────────

    def _needs_history_backfill(self, ticker: str, metric: str) -> bool:
        """Return True when backfill is warranted: no rows, too few rows, or history too recent.

        Thresholds:
          - count == 0                  → always backfill (first run)
          - count < 100                 → partial backfill happened; retry
          - MIN(observed_date) < 1 yr   → data depth is shallow; retry
        """
        row = self.db.execute(
            "SELECT COUNT(*), MIN(observed_date) FROM valuation_history WHERE ticker = ? AND metric = ?",
            (ticker, metric),
        ).fetchone()
        if not row or int(row[0]) == 0:
            logger.debug("History backfill needed for %s/%s: no rows", ticker, metric)
            return True
        count = int(row[0])
        if count < 100:
            logger.debug("History backfill needed for %s/%s: only %d rows", ticker, metric, count)
            return True
        if row[1] is not None:
            oldest = row[1] if isinstance(row[1], date) else date.fromisoformat(str(row[1]))
            if (date.today() - oldest).days < 365:
                logger.debug(
                    "History backfill needed for %s/%s: oldest %s is within 1yr",
                    ticker, metric, oldest,
                )
                return True
        return False

    def _bulk_insert_history(
        self, ticker: str, metric: str, history: list[dict], source: str
    ) -> int:
        count = 0
        for point in history:
            try:
                self.db.execute(
                    "INSERT INTO valuation_history (ticker, metric, observed_date, value, source) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT (ticker, metric, observed_date) DO NOTHING",
                    (ticker, metric, point["date"], point["pe_ttm"], source),
                )
                count += 1
            except Exception as exc:
                logger.warning(
                    "History insert failed %s/%s/%s: %s", ticker, metric, point.get("date"), exc
                )
        return count

    def _upsert_history(
        self, ticker: str, metric: str, observed_date: str, value: float, source: str
    ) -> None:
        self.db.execute(
            "INSERT INTO valuation_history (ticker, metric, observed_date, value, source) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (ticker, metric, observed_date) DO NOTHING",
            (ticker, metric, observed_date, value, source),
        )

    def _bulk_upsert_series(
        self, ticker: str, metric: str, series: list[dict], source: str
    ) -> int:
        """Upsert a list of {date, value} points into valuation_history. Returns row count."""
        count = 0
        for point in series:
            try:
                self._upsert_history(ticker, metric, point["date"], point["value"], source)
                count += 1
            except Exception as exc:
                logger.warning(
                    "Series upsert failed %s/%s/%s: %s", ticker, metric, point.get("date"), exc
                )
        return count

    def _needs_fmp_history_backfill(self, ticker: str) -> bool:
        """Return True when FMP quarterly pe_ttm history is absent or very sparse (< 10 rows)."""
        row = self.db.execute(
            "SELECT COUNT(*) FROM valuation_history "
            "WHERE ticker = ? AND metric = 'pe_ttm' AND source = 'fmp_historical'",
            (ticker,),
        ).fetchone()
        return not row or int(row[0]) < 10

    def _get_history_percentile(
        self, ticker: str, metric: str, current_value: float, years: int = 10
    ) -> tuple[float | None, int]:
        cutoff = (date.today() - timedelta(days=years * 365)).isoformat() if years > 0 else None
        query = (
            "SELECT value, observed_date FROM valuation_history "
            "WHERE ticker = ? AND metric = ?"
            + (" AND observed_date >= ?" if cutoff else "")
            + " ORDER BY observed_date ASC"
        )
        params = (ticker, metric, cutoff) if cutoff else (ticker, metric)
        rows = self.db.execute(query, params).fetchall()
        if not rows:
            return None, 0
        values = [float(r[0]) for r in rows if r[0] is not None]
        dates = [r[1] for r in rows if r[1] is not None]
        date_range_days = (dates[-1] - dates[0]).days if len(dates) >= 2 else 0
        return compute_percentile(values, current_value, date_range_days=date_range_days)

    # ── DB write ─────────────────────────────────────────────────────────────

    def _write_snapshot(
        self,
        snapshot_date: date,
        ticker: str,
        asset_id: str,
        asset_class: str,
        m: dict[str, Any],
    ) -> None:
        self.db.execute("""
            INSERT INTO valuation_snapshots (
                snapshot_date, ticker, display_name, row_kind, linked_ticker,
                asset_id, asset_class,
                pe_ttm, pe_forward, pb_ratio, peg_ratio, fcf_yield,
                dividend_yield, ev_ebitda, sec_yield,
                pe_ttm_pct, pe_fwd_pct, pb_pct, pct_years,
                valuation_signal, signal_basis, rate_adjustment_factor,
                data_source, is_estimable, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())
            ON CONFLICT (snapshot_date, ticker) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                row_kind = EXCLUDED.row_kind,
                linked_ticker = EXCLUDED.linked_ticker,
                asset_id = EXCLUDED.asset_id,
                asset_class = EXCLUDED.asset_class,
                pe_ttm = EXCLUDED.pe_ttm,
                pe_forward = EXCLUDED.pe_forward,
                pb_ratio = EXCLUDED.pb_ratio,
                peg_ratio = EXCLUDED.peg_ratio,
                fcf_yield = EXCLUDED.fcf_yield,
                dividend_yield = EXCLUDED.dividend_yield,
                ev_ebitda = EXCLUDED.ev_ebitda,
                sec_yield = EXCLUDED.sec_yield,
                pe_ttm_pct = EXCLUDED.pe_ttm_pct,
                pe_fwd_pct = EXCLUDED.pe_fwd_pct,
                pb_pct = EXCLUDED.pb_pct,
                pct_years = EXCLUDED.pct_years,
                valuation_signal = EXCLUDED.valuation_signal,
                signal_basis = EXCLUDED.signal_basis,
                rate_adjustment_factor = EXCLUDED.rate_adjustment_factor,
                data_source = EXCLUDED.data_source,
                is_estimable = EXCLUDED.is_estimable,
                notes = EXCLUDED.notes,
                created_at = NOW()
        """, (
            snapshot_date,
            ticker,
            m.get("display_name"),
            m.get("row_kind", "holding"),
            m.get("linked_ticker"),
            asset_id,
            asset_class,
            m.get("pe_ttm"), m.get("pe_forward"),
            m.get("pb_ratio"), m.get("peg_ratio"),
            m.get("fcf_yield"), m.get("dividend_yield"),
            m.get("ev_ebitda"), m.get("sec_yield"),
            m.get("pe_ttm_pct"), m.get("pe_fwd_pct"),
            m.get("pb_pct"), m.get("pct_years", 0),
            m.get("valuation_signal", "N/A"),
            m.get("signal_basis", ""),
            m.get("rate_adjustment_factor", 1.0),
            m.get("data_source", "unknown"),
            m.get("is_estimable", True),
            m.get("notes"),
        ))

    # ── Per-holding collection (accounting view) ─────────────────────────────

    def _collect_one(
        self,
        canonical_id: str,
        asset_type: str,
        refs: dict[tuple[str, str], ValuationReference],
        us10y: float,
        adj: float,
    ) -> dict[str, Any]:
        raw = self._extract_raw_ticker(canonical_id)
        metrics: dict[str, Any] = {"row_kind": "holding"}

        if asset_type == "NON_ESTIMABLE":
            return {
                "row_kind": "holding",
                "is_estimable": False,
                "valuation_signal": "N/A",
                "notes": f"non_estimable:{canonical_id}",
                "data_source": "none",
                "rate_adjustment_factor": adj,
            }

        if asset_type in ("HK_FUND", "UNKNOWN"):
            return {
                "row_kind": "holding",
                "is_estimable": False,
                "valuation_signal": "N/A",
                "notes": f"stub:{asset_type}",
                "data_source": "none",
                "rate_adjustment_factor": adj,
            }

        if asset_type == "CN_FUND":
            # Holding row = accounting view only (no PE on fund itself)
            return {
                "row_kind": "holding",
                "is_estimable": False,
                "valuation_signal": "N/A",
                "notes": f"cn_fund_holding_no_direct_pe:{raw}",
                "data_source": "none",
                "rate_adjustment_factor": adj,
                "asset_class": asset_type,
            }

        if asset_type == "US_STOCK":
            metrics.update(fetch_fmp_us_stock(raw))
            if not metrics.get("pe_forward") and not metrics.get("pe_ttm"):
                metrics.update(fetch_yfinance_us_stock(raw))
            if not metrics.get("pe_forward") and not metrics.get("pe_ttm"):
                metrics.update({"notes": "data stale - all sources failed", "data_source": "none"})

        elif asset_type == "US_ETF":
            metrics.update(fetch_fmp_us_stock(raw))
            if not metrics.get("pe_forward"):
                yf = fetch_yfinance_us_stock(raw)
                metrics.update({k: v for k, v in yf.items() if v is not None and k not in metrics})

        elif asset_type == "US_BOND_ETF":
            metrics.update(fetch_yfinance_etf_yield(raw))
            if not metrics.get("sec_yield"):
                metrics.update({"notes": "yield data unavailable", "data_source": "none"})

        metrics["rate_adjustment_factor"] = adj

        # FMP history backfill + history-based percentile for US assets
        if asset_type in ("US_STOCK", "US_ETF") and metrics.get("pe_ttm") is not None:
            today_str = date.today().isoformat()
            if self._needs_fmp_history_backfill(raw):
                try:
                    hist = fetch_fmp_us_history(raw)
                    for metric_name, series in hist.items():
                        if series:
                            n = self._bulk_upsert_series(raw, metric_name, series, "fmp_historical")
                            logger.info("FMP backfill %d rows for %s/%s", n, raw, metric_name)
                except Exception as exc:
                    logger.warning("FMP history backfill failed for %s: %s", raw, exc)
            for m, f in [("pe_ttm", "pe_ttm"), ("pb_ratio", "pb_ratio")]:
                v = metrics.get(f)
                if v is not None:
                    self._upsert_history(raw, m, today_str, v, metrics.get("data_source", "fmp"))
            pe_ttm_val = metrics.get("pe_ttm")
            pct_ttm, yrs = self._get_history_percentile(raw, "pe_ttm", pe_ttm_val)
            metrics["pe_ttm_pct"] = pct_ttm
            metrics["pct_years"] = max(yrs, metrics.get("pct_years") or 0)
            pb_val = metrics.get("pb_ratio")
            if pb_val is not None:
                pct_pb, _ = self._get_history_percentile(raw, "pb_ratio", pb_val)
                metrics["pb_pct"] = pct_pb

        primary_metric = METRIC_FOR_CLASS.get(asset_type, "pe_forward")
        metric_value = metrics.get(primary_metric)
        if metric_value is None and primary_metric == "pe_forward" and metrics.get("pe_ttm") is not None:
            primary_metric = "pe_ttm"
            metric_value = metrics.get("pe_ttm")

        # Compute pe_fwd_pct before signal classification so it can be passed as percentile
        if asset_type in ("US_STOCK", "US_ETF") and metrics.get("pe_ttm_pct") is None:
            series, d_range = self._get_history_from_snapshots(raw, "pe_forward")
            if series and metrics.get("pe_forward") is not None:
                pct, yrs = compute_percentile(series, metrics["pe_forward"], date_range_days=d_range)
                metrics["pe_fwd_pct"] = pct
                metrics["pct_years"] = max(yrs, metrics.get("pct_years") or 0)

        # Pick percentile that matches primary_metric
        if primary_metric == "pe_forward":
            pct_for_signal = metrics.get("pe_fwd_pct") or metrics.get("pe_ttm_pct")
        elif primary_metric == "pe_ttm":
            pct_for_signal = metrics.get("pe_ttm_pct")
        else:
            pct_for_signal = None
        yrs_for_signal = int(metrics.get("pct_years") or 0)

        # Gate: refuse to classify by percentile unless we have enough history.
        # A few weeks of daily data can produce a wildly misleading percentile
        # (e.g. 8 rows → 6th percentile → LOW for a stock at 10-year highs).
        if pct_for_signal is not None and yrs_for_signal < MIN_PCT_YEARS_FOR_SIGNAL:
            logger.debug(
                "Suppressing percentile for %s (%.1f pct, %d yrs < %d yr minimum)",
                raw, pct_for_signal, yrs_for_signal, MIN_PCT_YEARS_FOR_SIGNAL,
            )
            pct_for_signal = None

        ref = refs.get((raw, primary_metric)) or refs.get((raw, METRIC_FOR_CLASS.get(asset_type, "pe_forward")))
        if ref:
            signal, basis = classify_signal(
                primary_metric, metric_value, ref, adj,
                percentile=pct_for_signal, pct_years=yrs_for_signal,
            )
            metrics["valuation_signal"] = signal
            metrics["signal_basis"] = basis
        else:
            metrics["valuation_signal"] = "N/A"
            metrics["signal_basis"] = "no_reference_config"

        metrics["asset_class"] = asset_type
        return metrics

    def _get_history_from_snapshots(self, ticker: str, metric: str) -> tuple[list[float | None], int]:
        """Legacy percentile from valuation_snapshots (US stocks only)."""
        col_map = {
            "pe_ttm": "pe_ttm", "pe_forward": "pe_forward",
            "pb_ratio": "pb_ratio", "sec_yield": "sec_yield",
        }
        col = col_map.get(metric)
        if not col:
            return [], 0
        try:
            rows = self.db.execute(
                f"SELECT {col}, snapshot_date FROM valuation_snapshots "
                f"WHERE ticker = ? AND {col} IS NOT NULL ORDER BY snapshot_date ASC",
                (ticker,),
            ).fetchall()
            if not rows:
                return [], 0
            series = [float(r[0]) for r in rows]
            dates = [r[1] for r in rows]
            d_range = (dates[-1] - dates[0]).days if len(dates) >= 2 else 0
            return series, d_range
        except Exception as exc:
            logger.warning("Snapshot history query error for %s/%s: %s", ticker, metric, exc)
            return [], 0

    # ── Tracked index collection ─────────────────────────────────────────────

    def _collect_tracked_index(
        self,
        index_name: str,
        fund_code: str,
        adj: float,
        refs: dict[tuple[str, str], ValuationReference],
    ) -> dict[str, Any] | None:
        """Fetch and persist data for a CN broad index tracked by a held fund.

        Returns metrics dict with row_kind='tracked_index', or None on failure.
        """
        if index_name in CN_MARKET_INDEXES:
            snapshot = fetch_cn_market_snapshot(index_name)
            history_source = "akshare_market_pe"
        else:
            snapshot = fetch_cn_index_snapshot(index_name)
            history_source = "akshare_index_pe"

        today = date.today().isoformat()

        # PB history backfill runs independently of PE snapshot (broad indexes only)
        _PB_SUPPORTED = {"沪深300", "中证500", "上证50"}
        if index_name in _PB_SUPPORTED and self._needs_history_backfill(index_name, "pb_ratio"):
            try:
                pb_hist = fetch_cn_index_funddb(index_name, "市净率")
                if pb_hist:
                    n = self._bulk_upsert_series(index_name, "pb_ratio", pb_hist, "akshare_funddb")
                    logger.info("Backfilled %d rows for %s/pb_ratio", n, index_name)
            except Exception as exc:
                logger.warning("PB backfill failed for %s: %s", index_name, exc)

        if not snapshot or not snapshot.get("pe_ttm"):
            logger.warning("No PE data for tracked index %s", index_name)
            return None

        pe = snapshot["pe_ttm"]

        # Bulk backfill on first encounter (or when history is thin)
        if self._needs_history_backfill(index_name, "pe_ttm"):
            if history_source == "akshare_index_pe":
                try:
                    history = fetch_cn_index_history(index_name)
                    if history:
                        n = self._bulk_insert_history(index_name, "pe_ttm", history, history_source)
                        logger.info("Backfilled %d rows for %s/pe_ttm", n, index_name)
                    else:
                        logger.warning("fetch_cn_index_history returned empty for %s", index_name)
                except Exception as exc:
                    logger.warning("History backfill failed for %s: %s", index_name, exc)
            elif history_source == "akshare_market_pe":
                try:
                    history = fetch_cn_market_history(index_name)
                    if history:
                        n = self._bulk_insert_history(index_name, "pe_ttm", history, history_source)
                        logger.info("Backfilled %d rows for %s/pe_ttm via market_pe", n, index_name)
                    else:
                        logger.warning("fetch_cn_market_history returned empty for %s", index_name)
                except Exception as exc:
                    logger.warning("Market history backfill failed for %s: %s", index_name, exc)

        # Always upsert today's PE point
        self._upsert_history(index_name, "pe_ttm", today, pe, history_source)

        # Percentile from valuation_history
        pct, yrs = self._get_history_percentile(index_name, "pe_ttm", pe)

        # PB percentile — backfill already ran above; just upsert today + compute pct
        pb_pct: float | None = None
        pb_snapshot = snapshot.get("pb_ratio")
        if index_name in _PB_SUPPORTED and pb_snapshot is not None:
            self._upsert_history(index_name, "pb_ratio", today, pb_snapshot, history_source)
            pb_pct, _ = self._get_history_percentile(index_name, "pb_ratio", pb_snapshot)

        # Signal
        ref = refs.get((index_name, "pe_ttm"))
        if ref:
            signal, basis = classify_signal("pe_ttm", pe, ref, adj, percentile=pct, pct_years=yrs)
        else:
            signal, basis = "N/A", "no_reference_config"

        return {
            "row_kind": "tracked_index",
            "linked_ticker": fund_code,
            "display_name": index_name,
            "pe_ttm": pe,
            "pb_ratio": pb_snapshot,
            "pe_ttm_pct": pct,
            "pb_pct": pb_pct,
            "pct_years": yrs,
            "valuation_signal": signal,
            "signal_basis": basis,
            "rate_adjustment_factor": adj,
            "data_source": snapshot.get("data_source", history_source),
            "is_estimable": True,
            "asset_class": "CN_INDEX",
        }

    def _collect_tracked_hk_index(
        self,
        hk_proxy_ticker: str,
        fund_code: str,
        adj: float,
        refs: dict[tuple[str, str], ValuationReference],
    ) -> dict[str, Any] | None:
        snapshot = fetch_hk_index_snapshot(hk_proxy_ticker)
        if not snapshot or not snapshot.get("pe_ttm"):
            return None

        pe = snapshot["pe_ttm"]
        today = date.today().isoformat()

        baidu_symbol = HK_BAIDU_SYMBOL_MAP.get(hk_proxy_ticker)

        # PB history backfill via Baidu (runs before percentile computation)
        if baidu_symbol and self._needs_history_backfill(hk_proxy_ticker, "pb_ratio"):
            try:
                pb_hist = fetch_hk_index_pb_history(baidu_symbol)
                if pb_hist:
                    n = self._bulk_upsert_series(hk_proxy_ticker, "pb_ratio", pb_hist, "baidu_hk")
                    logger.info("Baidu HK PB backfill: %d rows for %s", n, hk_proxy_ticker)
            except Exception as exc:
                logger.warning("Baidu PB backfill failed for %s: %s", hk_proxy_ticker, exc)

        # PE history backfill via Baidu
        if baidu_symbol and self._needs_history_backfill(hk_proxy_ticker, "pe_ttm"):
            try:
                pe_hist = fetch_hk_index_pe_history(baidu_symbol)
                if pe_hist:
                    n = self._bulk_insert_history(hk_proxy_ticker, "pe_ttm", pe_hist, "baidu_hk")
                    logger.info("Baidu HK PE backfill: %d rows for %s", n, hk_proxy_ticker)
            except Exception as exc:
                logger.warning("Baidu PE backfill failed for %s: %s", hk_proxy_ticker, exc)

        self._upsert_history(hk_proxy_ticker, "pe_ttm", today, pe, "yfinance_hk_proxy")
        pct, yrs = self._get_history_percentile(hk_proxy_ticker, "pe_ttm", pe)

        # PB snapshot + percentile
        pb_pct: float | None = None
        pb_snapshot = snapshot.get("pb_ratio")
        if pb_snapshot is not None:
            self._upsert_history(hk_proxy_ticker, "pb_ratio", today, pb_snapshot, "yfinance_hk_proxy")
            pb_pct, _ = self._get_history_percentile(hk_proxy_ticker, "pb_ratio", pb_snapshot)

        ref = refs.get((hk_proxy_ticker, "pe_ttm"))
        signal, basis = ("N/A", "no_reference_config")
        if ref:
            signal, basis = classify_signal("pe_ttm", pe, ref, adj, percentile=pct, pct_years=yrs)

        return {
            "row_kind": "tracked_index",
            "linked_ticker": fund_code,
            "display_name": hk_proxy_ticker,
            "pe_ttm": pe,
            "pb_ratio": pb_snapshot,
            "pe_ttm_pct": pct,
            "pb_pct": pb_pct,
            "pct_years": yrs,
            "valuation_signal": signal,
            "signal_basis": basis,
            "rate_adjustment_factor": adj,
            "data_source": "yfinance_hk_proxy",
            "is_estimable": True,
            "asset_class": "HK_INDEX",
        }

    # ── US broad index collection ────────────────────────────────────────────

    def _collect_tracked_us_index(
        self,
        index_name: str,
        etf_proxy: str,
        adj: float,
        refs: dict[tuple[str, str], ValuationReference],
    ) -> dict[str, Any] | None:
        snapshot = fetch_yfinance_us_stock(etf_proxy)
        if not snapshot or not snapshot.get("pe_ttm"):
            logger.warning("No PE data for US index %s (proxy %s)", index_name, etf_proxy)
            return None

        pe = snapshot["pe_ttm"]
        today = date.today().isoformat()
        self._upsert_history(index_name, "pe_ttm", today, pe, "yfinance_proxy")

        if self._needs_history_backfill(index_name, "pe_ttm"):
            if index_name == "S&P500":
                history_fetcher = fetch_multpl_sp500_pe_history
            elif index_name == "Nasdaq100":
                history_fetcher = fetch_multpl_nasdaq100_pe_history
            else:
                history_fetcher = None

            if history_fetcher is not None:
                try:
                    history = history_fetcher()
                    if history:
                        n = self._bulk_insert_history(
                            index_name, "pe_ttm",
                            [{"date": r["date"], "pe_ttm": r["value"]} for r in history],
                            "multpl",
                        )
                        logger.info("multpl history backfill: %d rows for %s", n, index_name)
                    else:
                        logger.warning("multpl returned empty history for %s", index_name)
                except Exception as exc:
                    logger.warning("multpl backfill failed for %s: %s", index_name, exc)

        pct, yrs = self._get_history_percentile(index_name, "pe_ttm", pe)

        ref = refs.get((index_name, "pe_ttm"))
        signal, basis = ("N/A", "no_reference_config")
        if ref:
            signal, basis = classify_signal("pe_ttm", pe, ref, adj, percentile=pct, pct_years=yrs)

        return {
            "row_kind": "tracked_index",
            "linked_ticker": etf_proxy,
            "display_name": index_name,
            "pe_ttm": pe,
            "pe_ttm_pct": pct,
            "pct_years": yrs,
            "valuation_signal": signal,
            "signal_basis": basis,
            "rate_adjustment_factor": adj,
            "data_source": "yfinance_proxy",
            "is_estimable": True,
            "asset_class": "US_INDEX",
        }

    # ── Watchlist collection ─────────────────────────────────────────────────

    def _get_watchlist_items(self) -> list[tuple]:
        try:
            return self.db.execute(
                "SELECT ticker, display_name, asset_type, note FROM valuation_watchlist ORDER BY ticker"
            ).fetchall()
        except Exception as exc:
            logger.warning("Could not fetch watchlist: %s", exc)
            return []

    def _collect_watchlist_item(
        self,
        ticker: str,
        display_name: str,
        asset_type: str,
        adj: float,
        refs: dict[tuple[str, str], ValuationReference],
    ) -> dict[str, Any] | None:
        if asset_type == "US_INDEX":
            snapshot = fetch_us_index_snapshot(ticker)
        elif asset_type == "HK_INDEX":
            snapshot = fetch_hk_index_snapshot(ticker)
        elif asset_type == "CN_INDEX":
            snapshot = fetch_cn_index_snapshot(ticker)
        elif asset_type == "CN_MARKET":
            snapshot = fetch_cn_market_snapshot(ticker)
        elif asset_type == "US_STOCK":
            raw_snap: dict[str, Any] = {}
            raw_snap.update(fetch_fmp_us_stock(ticker))
            if not raw_snap.get("pe_forward") and not raw_snap.get("pe_ttm"):
                raw_snap.update(fetch_yfinance_us_stock(ticker))
            snapshot = raw_snap
        else:
            snapshot = {}

        pe_ttm = snapshot.get("pe_ttm")
        pe_fwd = snapshot.get("pe_forward")
        if not snapshot or (pe_ttm is None and pe_fwd is None):
            return {
                "row_kind": "watchlist",
                "display_name": display_name,
                "is_estimable": False,
                "valuation_signal": "N/A",
                "notes": f"no_pe_data:{ticker}",
                "data_source": "none",
                "rate_adjustment_factor": adj,
                "asset_class": asset_type,
            }

        # US stocks use pe_forward as primary; index types use pe_ttm
        use_fwd = asset_type == "US_STOCK" and pe_fwd is not None
        metric_key = "pe_forward" if use_fwd else "pe_ttm"
        pe = pe_fwd if use_fwd else (pe_ttm or pe_fwd)

        today = date.today().isoformat()

        # FMP history backfill for US watchlist stocks (same logic as holdings)
        if asset_type == "US_STOCK" and pe_ttm is not None and self._needs_fmp_history_backfill(ticker):
            try:
                hist = fetch_fmp_us_history(ticker)
                for metric_name, series in hist.items():
                    if series:
                        n = self._bulk_upsert_series(ticker, metric_name, series, "fmp_historical")
                        logger.info("FMP watchlist backfill %d rows for %s/%s", n, ticker, metric_name)
            except Exception as exc:
                logger.warning("FMP watchlist history backfill failed for %s: %s", ticker, exc)

        self._upsert_history(ticker, metric_key, today, pe, snapshot.get("data_source", "unknown"))
        pct, yrs = self._get_history_percentile(ticker, metric_key, pe)

        ref = refs.get((ticker, metric_key)) or refs.get((ticker, "pe_ttm"))
        signal, basis = ("N/A", "no_reference_config")
        if ref:
            signal, basis = classify_signal(metric_key, pe, ref, adj, percentile=pct, pct_years=yrs)

        return {
            "row_kind": "watchlist",
            "display_name": display_name,
            "pe_ttm": pe_ttm,
            "pe_forward": pe_fwd,
            "dividend_yield": snapshot.get("dividend_yield"),
            "pe_ttm_pct": pct if metric_key == "pe_ttm" else None,
            "pe_fwd_pct": pct if metric_key == "pe_forward" else None,
            "pct_years": yrs,
            "valuation_signal": signal,
            "signal_basis": basis,
            "rate_adjustment_factor": adj,
            "data_source": snapshot.get("data_source", "unknown"),
            "is_estimable": True,
            "asset_class": asset_type,
        }

    # ── Main refresh ─────────────────────────────────────────────────────────

    async def refresh_all(self) -> RefreshResult:
        today = date.today().isoformat()

        async with _refresh_lock:
            if _daily_counts.get(today, 0) >= 3:
                return RefreshResult(status="rate_limited")
            _daily_counts[today] = _daily_counts.get(today, 0) + 1

        result = RefreshResult(status="ok")
        try:
            seed_index_references(self.db)
            holdings = await asyncio.to_thread(fetch_wealthos_active_holdings, self.db)
            us10y, _ = self._get_us10y()
            try:
                adj = adjusted_factor(us10y)
            except ValueError:
                adj = 1.0

            refs_list = get_all_references(self.db)
            refs = {(r.ticker, r.metric): r for r in refs_list}
            today_date = date.today()

            # ── Phase A: Holding rows ──────────────────────────────────────
            seen_index_funds: set[str] = set()
            for h in holdings:
                canonical_id = h.get("asset_id") or h.get("canonical_id") or ""
                if not canonical_id:
                    continue
                asset_type = self._detect_asset_type(canonical_id)
                raw = self._extract_raw_ticker(canonical_id)
                try:
                    metrics = await asyncio.to_thread(
                        self._collect_one, canonical_id, asset_type, refs, us10y, adj
                    )
                    self._write_snapshot(today_date, raw, canonical_id, asset_type, metrics)
                    result.refreshed_count += 1
                    if asset_type == "CN_FUND":
                        seen_index_funds.add(raw)
                except Exception as exc:
                    logger.warning("Failed to collect holding %s: %s", canonical_id, exc)
                    result.failed.append({"ticker": raw, "error": str(exc)})

            # ── Phase B: Tracked index rows ────────────────────────────────
            for idx, (fund_code, index_name) in enumerate(FUND_TO_INDEX_MAP.items()):
                if idx > 0:
                    await asyncio.sleep(AKSHARE_INDEX_THROTTLE_SECONDS)  # avoid AKShare rate throttle between indexes
                try:
                    metrics = await asyncio.to_thread(
                        self._collect_tracked_index, index_name, fund_code, adj, refs
                    )
                    if metrics:
                        self._write_snapshot(
                            today_date, index_name, f"INDEX_{index_name}", "CN_INDEX", metrics
                        )
                        result.refreshed_count += 1
                except Exception as exc:
                    logger.warning("Failed to collect tracked index %s: %s", index_name, exc)
                    result.failed.append({"ticker": index_name, "error": str(exc)})

            for fund_code, proxy_ticker in HK_ETF_PROXY_MAP.items():
                try:
                    metrics = await asyncio.to_thread(
                        self._collect_tracked_hk_index, proxy_ticker, fund_code, adj, refs
                    )
                    if metrics:
                        self._write_snapshot(
                            today_date, proxy_ticker, f"HK_INDEX_{proxy_ticker}", "HK_INDEX", metrics
                        )
                        result.refreshed_count += 1
                except Exception as exc:
                    logger.warning("Failed to collect HK proxy %s: %s", proxy_ticker, exc)
                    result.failed.append({"ticker": proxy_ticker, "error": str(exc)})

            # ── Phase C: US broad index rows ──────────────────────────────
            for etf_proxy, index_name in US_INDEX_MAP.items():
                try:
                    metrics = await asyncio.to_thread(
                        self._collect_tracked_us_index, index_name, etf_proxy, adj, refs
                    )
                    if metrics:
                        self._write_snapshot(
                            today_date, index_name, f"US_INDEX_{index_name}", "US_INDEX", metrics
                        )
                        result.refreshed_count += 1
                except Exception as exc:
                    logger.warning("Failed to collect US index %s: %s", index_name, exc)
                    result.failed.append({"ticker": index_name, "error": str(exc)})

            # ── Phase D: Watchlist rows ────────────────────────────────────
            watchlist = await asyncio.to_thread(self._get_watchlist_items)
            for row in watchlist:
                ticker, display_name, asset_type, _note = row[0], row[1], row[2], row[3]
                try:
                    metrics = await asyncio.to_thread(
                        self._collect_watchlist_item, ticker, display_name, asset_type, adj, refs
                    )
                    if metrics:
                        self._write_snapshot(
                            today_date, ticker, f"WL_{ticker}", asset_type, metrics
                        )
                        result.refreshed_count += 1
                except Exception as exc:
                    logger.warning("Failed to collect watchlist %s: %s", ticker, exc)
                    result.failed.append({"ticker": ticker, "error": str(exc)})

        except Exception as exc:
            logger.error("refresh_all failed: %s", exc)
            result.status = "error"
            result.failed.append({"ticker": "all", "error": str(exc)})

        return result
