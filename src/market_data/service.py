from typing import Dict, Optional
from datetime import date
import pandas as pd
import logging

from src.database.connector import DatabaseConnector
from src.market_data.scrapers.base import BaseScraper
from src.market_data.scrapers.cn_fund_scraper import CNFundMarketDataScraper
from src.market_data.fetchers.base import FetcherManager, DataFetchError, UnsupportedCodeError
from src.market_data.fetchers.types import RealtimeQuote
from src.market_data.fetchers.yfinance_fetcher import YfinanceFetcher, fetch_fx_rates
from src.market_data.fetchers.akshare_fetcher import AkshareFundFetcher
from src.market_data.fetchers.gold_fetcher import GoldPriceFetcher
from src.validation.reader_validator import extract_symbol

logger = logging.getLogger(__name__)

_FIXED_NAV_MONEY_MARKET_CLASSES = {"Money Market", "货币市场"}

class MarketDataService:
    """Service to fetch market data from various sources."""

    def __init__(self):
        self._scrapers: Dict[str, BaseScraper] = {}
        self._register_default_scrapers()
        self._fetchers: Dict[str, FetcherManager] = {}
        self._register_default_fetchers()
        
    def _register_default_scrapers(self):
        """Register default available scrapers."""
        self.register_scraper("CN_FUND_", CNFundMarketDataScraper())

    def _register_default_fetchers(self):
        """Register default fetchers by market type."""
        self._fetchers["us"] = FetcherManager([YfinanceFetcher()])
        self._fetchers["cn_fund"] = FetcherManager([AkshareFundFetcher()])
        self._fetchers["gold"] = FetcherManager([GoldPriceFetcher()])

    def _detect_market(self, code: str) -> str:
        """Route code to market type. Explicit table — raises UnsupportedCodeError for unknowns."""
        if code.startswith("CN_FUND_"):
            return "cn_fund"
        # Gold assets (paper gold from SGE)
        if code == "ALTS_Paper_Gold" or code.startswith("GOLD_"):
            return "gold"
        # US stocks/ETFs/RSUs: canonical prefixes or bare tickers
        us_prefixes = ("US_STK_", "US_ETF_", "RSU_")
        if any(code.startswith(p) for p in us_prefixes):
            return "us"
        # Bare ticker (e.g., "AMZN", "NVDA") — assume US
        if code.isalpha() and code.isupper() and len(code) <= 5:
            return "us"
        raise UnsupportedCodeError(f"Cannot determine market for code: {code!r}")

    def get_ohlcv(self, code: str, days: int = 60) -> pd.DataFrame:
        """Fetch OHLCV history. Returns DataFrame with columns: date, open, high, low, close, volume, pct_chg, source."""
        market = self._detect_market(code)
        manager = self._fetchers.get(market)
        if not manager:
            raise UnsupportedCodeError(f"No fetcher registered for market: {market!r}")
        bars = manager.get_ohlcv(code, days)
        return pd.DataFrame([
            {"date": b.date, "open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "volume": b.volume, "pct_chg": b.pct_chg, "source": b.source}
            for b in bars
        ])

    def get_realtime_quote(self, code: str) -> Optional[RealtimeQuote]:
        """Get latest price.

        Raises UnsupportedCodeError for unrecognized codes (caller must handle skip).
        Returns None on transient fetch failure (DataFetchError, network error).
        """
        market = self._detect_market(code)  # UnsupportedCodeError propagates — caller skips
        manager = self._fetchers.get(market)
        if not manager:
            raise UnsupportedCodeError(f"No fetcher registered for market: {market!r}")
        try:
            return manager.get_realtime_quote(code)
        except DataFetchError as e:
            logger.warning(f"Could not get realtime quote for {code}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Unexpected realtime quote error for {code}: {e}")
            return None

    def _should_skip_live_refresh(
        self,
        asset_id: str,
        market: str,
        asset_class: Optional[str],
        market_price_unit: Optional[float],
        cost_price_unit: Optional[float],
    ) -> bool:
        if market != "cn_fund":
            return False
        if asset_class not in _FIXED_NAV_MONEY_MARKET_CLASSES:
            return False
        if market_price_unit is None:
            return False
        if abs(float(market_price_unit) - 1.0) > 1e-9:
            return False
        if cost_price_unit is not None and abs(float(cost_price_unit) - 1.0) > 1e-9:
            return False
        return True

    def refresh_portfolio_prices(
        self,
        connector: DatabaseConnector,
        fx_rates: Optional[dict] = None,
    ) -> dict:
        if fx_rates is None:
            fx_rates = fetch_fx_rates()

        rows = connector.execute(
            """
            SELECT DISTINCT
                h.asset_id,
                h.market_price_unit,
                h.cost_price_unit,
                ar.asset_class
            FROM holdings h
            LEFT JOIN asset_registry ar ON ar.canonical_id = h.asset_id
            WHERE is_shadow = FALSE
              AND quantity > 0
              AND (
                  asset_id LIKE 'US_STK_%'
                  OR asset_id LIKE 'US_ETF_%'
                  OR asset_id LIKE 'RSU_%'
                  OR asset_id LIKE 'CN_FUND_%'
                  OR asset_id LIKE 'GOLD_%'
                  OR asset_id = 'ALTS_Paper_Gold'
              )
            ORDER BY asset_id
            """
        ).fetchall()

        refreshed = 0
        skipped = 0
        errors = 0
        seen: set[tuple[str, str]] = set()
        refreshed_assets: list[dict] = []
        skipped_assets: list[dict] = []
        error_assets: list[dict] = []

        for asset_id, market_price_unit, cost_price_unit, asset_class in rows:
            try:
                market = self._detect_market(asset_id)  # fast fail before extract
                raw_code = extract_symbol(asset_id)
            except UnsupportedCodeError:
                skipped += 1
                skipped_assets.append(
                    {
                        "asset_id": asset_id,
                        "market": "unknown",
                        "reason": "unsupported",
                    }
                )
                continue

            if self._should_skip_live_refresh(
                asset_id=asset_id,
                market=market,
                asset_class=asset_class,
                market_price_unit=market_price_unit,
                cost_price_unit=cost_price_unit,
            ):
                skipped += 1
                skipped_assets.append(
                    {
                        "asset_id": asset_id,
                        "market": market,
                        "reason": "fixed-nav money market",
                    }
                )
                continue

            dedup_key = (market, raw_code)
            if dedup_key in seen:
                continue
            # Note: seen.add() deferred until after successful upsert so that
            # a transient failure on one alias doesn't silently block other aliases.

            try:
                quote = self.get_realtime_quote(asset_id)
            except UnsupportedCodeError as exc:
                skipped += 1
                skipped_assets.append(
                    {
                        "asset_id": asset_id,
                        "market": market,
                        "reason": str(exc) or "unsupported",
                    }
                )
                continue

            if quote is None:
                errors += 1
                error_assets.append(
                    {
                        "asset_id": asset_id,
                        "market": market,
                        "reason": "quote unavailable",
                    }
                )
                continue

            from datetime import date as _date
            if quote.as_of_date == _date.min:
                logger.warning(f"No trading date available for {asset_id}; skipping upsert")
                errors += 1
                error_assets.append(
                    {
                        "asset_id": asset_id,
                        "market": market,
                        "reason": "missing trading date",
                    }
                )
                continue

            connector.execute(
                """
                INSERT INTO market_daily (code, date, close, data_source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (code, date) DO UPDATE SET
                    close = EXCLUDED.close,
                    data_source = EXCLUDED.data_source
                """,
                (raw_code, quote.as_of_date, quote.price, quote.source),
            )
            seen.add(dedup_key)  # Only after successful upsert
            refreshed += 1
            refreshed_assets.append(
                {
                    "asset_id": asset_id,
                    "code": raw_code,
                    "market": market,
                    "price": float(quote.price),
                    "as_of_date": quote.as_of_date.isoformat(),
                    "source": quote.source,
                }
            )

        # Deferred import: avoids circular dependency (dsa_sync imports MarketDataService)
        from src.sync.dsa_sync import _update_from_dsa

        holdings_updated = _update_from_dsa(connector, fx_rates)
        return {
            "refreshed": refreshed,
            "skipped": skipped,
            "errors": errors,
            "holdings_updated": holdings_updated,
            "fx_rates": fx_rates,
            "refreshed_assets": refreshed_assets,
            "skipped_assets": skipped_assets,
            "error_assets": error_assets,
        }
        
    def refresh_prices_for_asset_ids(
        self,
        connector: DatabaseConnector,
        asset_ids: list[str],
    ) -> int:
        """Refresh market_daily prices for an explicit asset_id list (P9 a0 sub-step).

        Same realtime-quote → market_daily upsert path as refresh_portfolio_prices,
        without the holdings query — keeps pending-verification assets (incl. sold
        assets) priced for the +30d outcome window. Returns the upsert count.
        Skips unfetchable codes without raising; unfetchable assets honestly end
        'verification_blocked' at maturity.
        """
        from datetime import date as _date

        refreshed = 0
        seen: set[tuple[str, str]] = set()

        for asset_id in asset_ids:
            if not asset_id:
                continue
            try:
                market = self._detect_market(asset_id)
                raw_code = extract_symbol(asset_id)
            except UnsupportedCodeError:
                logger.debug("refresh_prices_for_asset_ids: unsupported asset_id=%s", asset_id)
                continue

            # Dedup = attempted once per run (marked BEFORE fetching, so a failed
            # upsert doesn't trigger a duplicate network fetch via an alias id).
            dedup_key = (market, raw_code)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # Mirror refresh_portfolio_prices' fixed-NAV money-market guard (NAV pinned
            # at 1.0 → never live-fetch). Best-effort: sold assets have no holding row.
            try:
                _guard_row = connector.execute(
                    """
                    SELECT h.market_price_unit, h.cost_price_unit, ar.asset_class
                    FROM holdings h
                    LEFT JOIN asset_registry ar ON ar.canonical_id = h.asset_id
                    WHERE h.asset_id = ? AND h.is_shadow = FALSE
                    ORDER BY h.snapshot_date DESC
                    LIMIT 1
                    """,
                    (asset_id,),
                ).fetchone()
            except Exception as exc:
                logger.debug("refresh_prices_for_asset_ids: guard lookup failed for %s: %s", asset_id, exc)
                _guard_row = None
            if _guard_row is not None and self._should_skip_live_refresh(
                asset_id=asset_id,
                market=market,
                asset_class=_guard_row[2],
                market_price_unit=_guard_row[0],
                cost_price_unit=_guard_row[1],
            ):
                logger.debug("refresh_prices_for_asset_ids: fixed-nav money market %s — skipping", asset_id)
                continue

            try:
                quote = self.get_realtime_quote(asset_id)
            except UnsupportedCodeError:
                logger.debug("refresh_prices_for_asset_ids: unsupported (quote) asset_id=%s", asset_id)
                continue
            except Exception as exc:
                logger.warning("refresh_prices_for_asset_ids: unexpected error for %s: %s", asset_id, exc)
                continue
            if quote is None:
                logger.debug("refresh_prices_for_asset_ids: no quote for asset_id=%s", asset_id)
                continue
            if quote.as_of_date == _date.min:
                logger.debug("refresh_prices_for_asset_ids: missing trading date for %s — skipping", asset_id)
                continue

            try:
                connector.execute(
                    """
                    INSERT INTO market_daily (code, date, close, data_source)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (code, date) DO UPDATE SET
                        close = EXCLUDED.close,
                        data_source = EXCLUDED.data_source
                    """,
                    (raw_code, quote.as_of_date, quote.price, quote.source),
                )
                refreshed += 1
            except Exception as exc:
                logger.warning("refresh_prices_for_asset_ids: upsert failed for %s: %s", asset_id, exc)

        return refreshed

    def backfill_trade_window_prices(
        self,
        connector: DatabaseConnector,
        trades: list,
        max_fetches: int = 20,
    ) -> int:
        """Historical price backfill for verification windows.

        For each (asset_id, log_date) pair in *trades* (deduplicating by raw
        market code), checks whether market_daily already holds at least one
        close in BOTH of:
          - baseline window  [log_date−7d, log_date]
          - end window       [min(log_date+30d, today)−7d, min(log_date+30d, today)]

        If both windows are covered the code is skipped.  Otherwise the full
        historical range [log_date−7d, today] is fetched:
          • CN funds   — ``get_market_data`` (akshare scraper, exact date range).
          • US/Gold    — ``get_ohlcv`` (yfinance/gold fetcher) with a days window
                         large enough to reach log_date−7.
        All returned rows are upserted into market_daily (ON CONFLICT DO UPDATE).

        Respects *max_fetches* cap (counted per unique code that triggers a
        fetch, not per trade).  Never raises; errors are logged as WARNINGs.
        Returns the number of codes for which rows were upserted.
        """
        from datetime import timedelta
        from datetime import date as _date

        today = _date.today()
        fetched = 0
        seen_codes: set[str] = set()

        for item in trades:
            if fetched >= max_fetches:
                break
            asset_id = item[0]
            log_date = item[1]
            if not asset_id:
                continue

            # Normalise log_date to a Python date object
            if not isinstance(log_date, _date):
                try:
                    log_date = _date.fromisoformat(str(log_date)[:10])
                except (ValueError, TypeError):
                    logger.warning(
                        "backfill_trade_window_prices: cannot parse log_date=%r for %s — skipping",
                        log_date, asset_id,
                    )
                    continue

            try:
                market = self._detect_market(asset_id)
                raw_code = extract_symbol(asset_id)
            except UnsupportedCodeError:
                logger.debug(
                    "backfill_trade_window_prices: unsupported asset_id=%s — skipping", asset_id
                )
                continue

            if raw_code in seen_codes:
                continue

            # ── Window bounds ────────────────────────────────────────────────
            baseline_start = log_date - timedelta(days=7)
            end_date_cap = min(log_date + timedelta(days=30), today)
            end_window_start = end_date_cap - timedelta(days=7)

            # ── Check if both windows already have price data ─────────────────
            try:
                baseline_count = int(
                    connector.execute(
                        "SELECT COUNT(*) FROM market_daily WHERE code = ? AND date >= ? AND date <= ?",
                        (raw_code, baseline_start, log_date),
                    ).fetchone()[0]
                )
                end_count = int(
                    connector.execute(
                        "SELECT COUNT(*) FROM market_daily WHERE code = ? AND date >= ? AND date <= ?",
                        (raw_code, end_window_start, end_date_cap),
                    ).fetchone()[0]
                )
            except Exception as exc:
                logger.warning(
                    "backfill_trade_window_prices: window check failed for %s: %s", asset_id, exc
                )
                seen_codes.add(raw_code)
                continue

            if baseline_count > 0 and end_count > 0:
                logger.debug(
                    "backfill_trade_window_prices: %s both windows present — skipping", asset_id
                )
                seen_codes.add(raw_code)
                continue

            # Mark seen BEFORE fetch to prevent duplicate network calls
            seen_codes.add(raw_code)

            # ── Fetch historical range [log_date−7d, today] ───────────────────
            fetch_start = baseline_start
            df = None

            try:
                if market == "cn_fund":
                    # Scraper path: accepts explicit start/end, end_date=None → today
                    df = self.get_market_data(asset_id, fetch_start)
                else:
                    # Fetcher path (US, gold): days window sized to reach fetch_start.
                    # yfinance computes start_date = today − int(days × 1.5) days;
                    # we need int(days × 1.5) >= (today − fetch_start).days.
                    days_back = (today - fetch_start).days + 7  # +7 calendar buffer
                    days_param = max(60, int(days_back / 1.5) + 1)
                    df = self.get_ohlcv(asset_id, days_param)
            except UnsupportedCodeError:
                logger.debug(
                    "backfill_trade_window_prices: unsupported (fetch) %s — skipping", asset_id
                )
                continue
            except Exception as exc:
                logger.warning(
                    "backfill_trade_window_prices: fetch failed for %s: %s", asset_id, exc
                )
                continue

            if df is None or df.empty:
                logger.debug(
                    "backfill_trade_window_prices: no data returned for %s", asset_id
                )
                continue

            # ── Upsert all returned rows into market_daily ────────────────────
            upserted = 0
            for _, row in df.iterrows():
                row_date = row["date"]
                row_close = row.get("close")
                if row_close is None or pd.isna(row_close):
                    continue
                # Ensure row_date is a Python date (not pandas Timestamp)
                if hasattr(row_date, "date"):
                    row_date = row_date.date()
                data_source = str(row.get("source", "historical_backfill"))
                try:
                    connector.execute(
                        """
                        INSERT INTO market_daily (code, date, close, data_source)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT (code, date) DO UPDATE SET
                            close = EXCLUDED.close,
                            data_source = EXCLUDED.data_source
                        """,
                        (raw_code, row_date, float(row_close), data_source),
                    )
                    upserted += 1
                except Exception as exc:
                    logger.warning(
                        "backfill_trade_window_prices: upsert failed for %s on %s: %s",
                        asset_id, row_date, exc,
                    )

            if upserted > 0:
                fetched += 1
                logger.info(
                    "backfill_trade_window_prices: %s — %d rows upserted", asset_id, upserted
                )
            else:
                logger.debug(
                    "backfill_trade_window_prices: %s — 0 rows upserted (no new data)", asset_id
                )

        return fetched

    def register_scraper(self, prefix: str, scraper: BaseScraper):
        """Register a scraper for a specific asset ID prefix."""
        self._scrapers[prefix] = scraper
        logger.info(f"Registered scraper {type(scraper).__name__} for prefix '{prefix}'")
        
    def get_scraper(self, asset_id: str) -> Optional[BaseScraper]:
        """Find the appropriate scraper for an asset ID."""
        for prefix, scraper in self._scrapers.items():
            if asset_id.startswith(prefix):
                return scraper
        return None
        
    def get_market_data(self, asset_id: str, start_date: date, end_date: Optional[date] = None) -> pd.DataFrame:
        """
        Fetch market data for an asset using the appropriate scraper.
        
        Args:
            asset_id: The asset identifier
            start_date: Start date for data fetch
            end_date: End date (optional)
            
        Returns:
            DataFrame with columns: date, close, currency
            
        Raises:
            ValueError: If no scraper is found for the asset ID or fetch fails
        """
        scraper = self.get_scraper(asset_id)
        if not scraper:
            logger.error(f"No scraper found for asset ID: {asset_id}")
            raise ValueError(f"No scraper found for asset ID: {asset_id}")
            
        try:
            logger.info(f"Fetching market data for {asset_id} from {start_date} to {end_date}")
            return scraper.fetch_history(asset_id, start_date, end_date)
        except Exception as e:
            logger.error(f"Failed to fetch market data for {asset_id}: {e}")
            raise
