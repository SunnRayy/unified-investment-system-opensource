"""Market sentiment indicator fetcher and classifier."""

from __future__ import annotations

import json
import logging
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Optional

from src.utils.http_client import http_get

logger = logging.getLogger(__name__)
yf = None


class MacroAnalyzer:
    """Fetches and classifies Wave 1 market sentiment indicators."""

    FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
    FEAR_GREED_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    CRYPTO_FNG_URL = "https://api.alternative.me/fng/?limit=1"
    COINGECKO_BASE = "https://api.coingecko.com/api/v3"
    SHILLER_CAPE_URL = "https://www.multpl.com/shiller-pe/table/by-month"
    # Increased from 10s → 15s to survive 2 automatic retries within the
    # upstream proxy window.  Browser UA is now set globally in http_client.
    REQUEST_TIMEOUT = 15

    def __init__(self, fred_api_key: Optional[str] = None):
        self.fred_api_key = fred_api_key or os.environ.get("FRED_API_KEY")

    def fetch_all(self) -> list[dict]:
        """Fetch all indicators concurrently (parallel reduces wall-time from
        ~sum to ~max, preventing upstream proxy timeouts on slow FRED calls)."""
        fetchers: list[Callable[[], dict]] = [
            self._fetch_fear_greed,
            # VIX: yfinance primary (real-time last-trade), FRED fallback (1-day lag)
            self._fetch_vix,
            # US: computed near-current (Corp equities / GDP); World Bank series is frozen at 2020.
            self._fetch_buffett_us_computed,
            lambda: self._fetch_fred_indicator("buffett_cn", "Buffett Indicator (China)", "DDDM01CNA156NWDB"),
            lambda: self._fetch_fred_indicator("buffett_jp", "Buffett Indicator (Japan)", "DDDM01JPA156NWDB"),
            lambda: self._fetch_fred_indicator("buffett_eu", "Buffett Indicator (Europe)", "DDDM01GBA156NWDB"),
            self._fetch_shiller_cape,
            self._fetch_brent_crude,
            # US10Y: yfinance primary (^TNX, real-time), FRED fallback
            self._fetch_us10y,
            self._fetch_gold_silver_ratio,
            self._fetch_crypto_fng,
            lambda: self._fetch_coingecko_vol_indicator("bitcoin", "btc", "BTC Price + 30d Vol", "btc"),
            lambda: self._fetch_coingecko_vol_indicator("ethereum", "eth", "ETH Price + 30d Vol", "eth"),
            self._fetch_btc_dominance,
        ]

        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(fn): fn for fn in fetchers}
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as exc:
                    logger.warning("Unexpected error in sentiment fetcher: %s", exc)
        return results

    def _fetch_fear_greed(self) -> dict:
        try:
            payload = http_get(
                self.FEAR_GREED_URL, timeout=self.REQUEST_TIMEOUT
            ).json()
            score = payload.get("fear_and_greed", {}).get("score")
            if score is None:
                score = payload.get("fear_and_greed", {}).get("now", {}).get("value")
            value = float(score)
            zone, zone_color = self._classify_fear_greed(value)
            return self._build_indicator(
                indicator_key="fear_greed",
                section="equity_macro",
                indicator_name="CNN Fear & Greed Index",
                value=value,
                display_value=f"{value:.1f}",
                zone=zone,
                zone_color=zone_color,
                description=f"Equity sentiment: {zone}.",
                raw_data=payload,
            )
        except Exception as exc:
            logger.warning("Failed to fetch fear & greed: %s", exc)
            return self._unavailable(
                indicator_key="fear_greed",
                section="equity_macro",
                indicator_name="CNN Fear & Greed Index",
                description="Equity sentiment unavailable.",
                error=exc,
            )

    def _staleness_note(self, obs_date: str) -> str:
        """Return a ⚠️ warning suffix when a FRED observation is several years old."""
        try:
            obs_year = int(str(obs_date)[:4])
        except (ValueError, TypeError):
            return ""
        lag = datetime.utcnow().year - obs_year
        if lag >= 3:
            return (
                f" ⚠️ STALE: data is from {obs_year} (~{lag}-year lag) — does NOT "
                f"reflect current market levels"
            )
        return ""

    def _fred_latest(self, series_id: str) -> tuple[float, str]:
        """Return (latest non-null value, obs_date) for a FRED series. Raises on failure."""
        if not self.fred_api_key:
            raise ValueError("FRED API key is not configured")
        response = http_get(
            self.FRED_BASE,
            params={
                "series_id": series_id,
                "api_key": self.fred_api_key,
                "sort_order": "desc",
                "limit": 5,
                "file_type": "json",
            },
            timeout=self.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        for obs in response.json().get("observations", []):
            raw = obs.get("value")
            if raw in (None, "", "."):
                continue
            return float(raw), obs.get("date", "")
        raise ValueError(f"No valid FRED observation for {series_id}")

    def _fetch_buffett_us_computed(self) -> dict:
        """US Buffett Indicator computed near-real-time as Corporate Equities / GDP.

        Replaces FRED ``DDDM01USA156NWDB`` (World Bank stock-market-cap/GDP), which
        stopped updating in 2020 and therefore reported ~40pp below the current
        classic indicator. Uses the Fed Z.1 series ``NCBEILQ027S`` (Nonfinancial
        Corporate Business; Corporate Equities; Liability, $millions, quarterly)
        over nominal ``GDP`` ($billions, quarterly). This is a recognized
        Buffett-indicator methodology that updates every quarter; it runs a few
        points under the Wilshire-5000 TMC/GDP figure (which also includes
        financials), but is current rather than 6 years stale.

        PRD 2026-07-07 F4.3/defect(c): this Fed-Z.1-derived variant previously
        served under the same generic "Buffett Indicator (US)" label as the
        classic World Bank stock-market-cap/GDP fallback below, with no
        methodology tag distinguishing them — reporting ~194.9% here while the
        classic ratio reads ~235%, materially understating US overvaluation to
        anyone comparing the two without knowing they differ. Tagged
        ``methodology="buffett_fed_z1_corp_equities_gdp"`` (narrower: nonfinancial
        corporate equities only, excludes financials/funds) vs the classic
        fallback's ``"buffett_classic_tmc_gdp"``.
        """
        indicator_key, indicator_name = "buffett_us", "Buffett Indicator (US, Fed Z.1 corp equities/GDP)"
        try:
            equities_m, eq_date = self._fred_latest("NCBEILQ027S")  # $ millions
            gdp_b, gdp_date = self._fred_latest("GDP")              # $ billions
            if not gdp_b:
                raise ValueError("GDP observation is zero/empty")
            value = (equities_m / 1000.0) / gdp_b * 100.0          # both -> $billions
            zone, zone_color = self._classify_buffett(value)
            description = (
                f"Buffett valuation zone: {zone}. Corp equities (Fed Z.1 NCBEILQ027S, "
                f"{eq_date}) ÷ GDP ({gdp_date}). Near-current basis; runs a few points "
                f"under the Wilshire-5000 TMC/GDP figure (excludes financials)."
            )
            return self._build_indicator(
                indicator_key=indicator_key,
                section="equity_macro",
                indicator_name=indicator_name,
                value=value,
                display_value=f"{value:.1f}%",
                zone=zone,
                zone_color=zone_color,
                description=description,
                raw_data={
                    "equities_millions": equities_m,
                    "equities_date": eq_date,
                    "gdp_billions": gdp_b,
                    "gdp_date": gdp_date,
                    "method": "NCBEILQ027S/GDP",
                },
                methodology="buffett_fed_z1_corp_equities_gdp",
                data_source="FRED (Fed Z.1 NCBEILQ027S nonfinancial corp equities / BEA GDP)",
            )
        except Exception as exc:
            logger.warning(
                "Computed US Buffett indicator failed (%s); falling back to World Bank series",
                exc,
            )
            # Fallback path appends a staleness warning (the World Bank series is frozen at 2020).
            return self._fetch_fred_indicator(indicator_key, indicator_name, "DDDM01USA156NWDB")

    def _fetch_fred_indicator(self, indicator_key: str, indicator_name: str, series_id: str) -> dict:
        try:
            if not self.fred_api_key:
                raise ValueError("FRED API key is not configured")

            response = http_get(
                self.FRED_BASE,
                params={
                    "series_id": series_id,
                    "api_key": self.fred_api_key,
                    "sort_order": "desc",
                    "limit": 5,
                    "file_type": "json",
                },
                timeout=self.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()

            value = None
            obs_date = ""
            for obs in payload.get("observations", []):
                raw = obs.get("value")
                if raw in (None, "", "."):
                    continue
                value = float(raw)
                obs_date = obs.get("date", "")
                break
            if value is None:
                raise ValueError("No valid FRED observation found")

            date_suffix = f" (FRED data from {obs_date})" if obs_date else ""
            # World Bank Buffett series (DDDM01*) lag several years and stopped
            # updating (US is frozen at 2020). Flag stale data so it isn't read
            # as current — a 2020 value sits ~40pp below today's market level.
            if obs_date and indicator_key.startswith("buffett"):
                date_suffix += self._staleness_note(obs_date)

            methodology = None
            data_source = None
            if indicator_key == "vix":
                zone, zone_color = self._classify_vix(value)
                description = f"VIX zone: {zone}.{date_suffix}"
                display_value = f"{value:.1f}"
            elif indicator_key == "us10y":
                zone, zone_color = self._classify_us10y(value)
                display_value = f"{value:.2f}%"
                description = f"US 10Y yield: {zone}. Tech stock gravity indicator.{date_suffix}"
            else:
                zone, zone_color = self._classify_buffett(value)
                description = f"Buffett valuation zone: {zone}.{date_suffix}"
                display_value = f"{value:.1f}%"
                if indicator_key.startswith("buffett"):
                    # World Bank DDDM01* is stock-market-capitalization-to-GDP —
                    # the classic Buffett-ratio formula (distinct from the
                    # Fed-Z.1-derived "buffett_fed_z1_corp_equities_gdp" variant above).
                    methodology = "buffett_classic_tmc_gdp"
                    data_source = f"World Bank ({series_id})"

            return self._build_indicator(
                indicator_key=indicator_key,
                section="equity_macro",
                indicator_name=indicator_name,
                value=value,
                display_value=display_value,
                zone=zone,
                zone_color=zone_color,
                description=description,
                raw_data=payload,
                methodology=methodology,
                data_source=data_source,
            )
        except Exception as exc:
            logger.warning("Failed to fetch %s (%s) from FRED: %s", indicator_key, series_id, exc)
            # Fallback: VIX and US10Y are available directly via yfinance.
            # Buffett indicators are annual World Bank data — no fast yfinance equivalent.
            if indicator_key == "vix":
                return self._fetch_vix_yfinance_fallback(indicator_name)
            if indicator_key == "us10y":
                return self._fetch_us10y_yfinance_fallback(indicator_name)
            return self._unavailable(
                indicator_key=indicator_key,
                section="equity_macro",
                indicator_name=indicator_name,
                description=f"{indicator_name} unavailable.",
                error=exc,
            )

    def _yf_last_price(self, ticker_symbol: str) -> float:
        """Return the most current available price from yfinance.

        Tries fast_info.lastPrice first (real-time last-trade price),
        then falls back to 5-day daily history close as a safety net.
        """
        global yf
        if yf is None:
            import yfinance as yf_module
            yf = yf_module

        ticker = yf.Ticker(ticker_symbol)
        # fast_info.lastPrice = current market price during hours, or last close after hours
        try:
            price = float(ticker.fast_info["lastPrice"])
            if price and price > 0:
                return price
        except (KeyError, TypeError, ValueError):
            pass

        # Fallback: daily history (5-day window, take last valid close)
        hist = ticker.history(period="5d")
        if hist.empty:
            raise ValueError(f"No price data for {ticker_symbol}")
        return float(hist["Close"].dropna().iloc[-1])

    def _fetch_vix(self) -> dict:
        """Fetch VIX with yfinance as primary (real-time) and FRED as fallback.

        FRED VIXCLS has a 1-business-day publication lag — at 01:46 UTC it shows
        yesterday's closing VIX, not today's intraday value. yfinance fast_info
        gives the current last-trade price during and after market hours.
        """
        indicator_name = "VIX (Volatility Index)"
        try:
            value = self._yf_last_price("^VIX")
            zone, zone_color = self._classify_vix(value)
            return self._build_indicator(
                indicator_key="vix",
                section="equity_macro",
                indicator_name=indicator_name,
                value=value,
                display_value=f"{value:.1f}",
                zone=zone,
                zone_color=zone_color,
                description=f"VIX zone: {zone}.",
                raw_data={"value": value, "source": "yfinance"},
            )
        except Exception as exc:
            logger.warning("VIX yfinance fetch failed, trying FRED: %s", exc)

        # FRED fallback
        try:
            return self._fetch_fred_indicator("vix", indicator_name, "VIXCLS")
        except Exception as exc2:
            logger.warning("VIX FRED fallback also failed: %s", exc2)
            return self._unavailable(
                indicator_key="vix",
                section="equity_macro",
                indicator_name=indicator_name,
                description="VIX unavailable (yfinance + FRED both failed).",
                error=exc2,
            )

    def _fetch_us10y(self) -> dict:
        """Fetch US 10Y yield with yfinance as primary (real-time) and FRED as fallback.

        FRED DGS10 has a 1-business-day lag. yfinance ^TNX gives real-time yield.
        yfinance already returns the percentage directly (e.g. 4.455 = 4.455%).
        """
        indicator_name = "US 10Y Treasury Yield"
        try:
            value = self._yf_last_price("^TNX")
            zone, zone_color = self._classify_us10y(value)
            return self._build_indicator(
                indicator_key="us10y",
                section="equity_macro",
                indicator_name=indicator_name,
                value=value,
                display_value=f"{value:.2f}%",
                zone=zone,
                zone_color=zone_color,
                description=f"US 10Y yield: {zone}. Tech stock gravity indicator.",
                raw_data={"value": value, "source": "yfinance"},
            )
        except Exception as exc:
            logger.warning("US10Y yfinance fetch failed, trying FRED: %s", exc)

        # FRED fallback
        try:
            return self._fetch_fred_indicator("us10y", indicator_name, "DGS10")
        except Exception as exc2:
            logger.warning("US10Y FRED fallback also failed: %s", exc2)
            return self._unavailable(
                indicator_key="us10y",
                section="equity_macro",
                indicator_name=indicator_name,
                description="US 10Y yield unavailable (yfinance + FRED both failed).",
                error=exc2,
            )

    def _fetch_vix_yfinance_fallback(self, indicator_name: str) -> dict:
        """Legacy fallback — kept for backwards-compat. New code calls _fetch_vix()."""
        try:
            value = self._yf_last_price("^VIX")
            zone, zone_color = self._classify_vix(value)
            return self._build_indicator(
                indicator_key="vix",
                section="equity_macro",
                indicator_name=indicator_name,
                value=value,
                display_value=f"{value:.1f}",
                zone=zone,
                zone_color=zone_color,
                description=f"VIX zone: {zone}.",
                raw_data={"value": value, "source": "yfinance"},
            )
        except Exception as exc2:
            logger.warning("VIX yfinance fallback also failed: %s", exc2)
            return self._unavailable(
                indicator_key="vix",
                section="equity_macro",
                indicator_name=indicator_name,
                description="VIX unavailable (FRED + yfinance both failed).",
                error=exc2,
            )

    def _fetch_us10y_yfinance_fallback(self, indicator_name: str) -> dict:
        """Legacy fallback — kept for backwards-compat. New code calls _fetch_us10y()."""
        try:
            value = self._yf_last_price("^TNX")
            zone, zone_color = self._classify_us10y(value)
            return self._build_indicator(
                indicator_key="us10y",
                section="equity_macro",
                indicator_name=indicator_name,
                value=value,
                display_value=f"{value:.2f}%",
                zone=zone,
                zone_color=zone_color,
                description=f"US 10Y yield: {zone}. Tech stock gravity indicator.",
                raw_data={"value": value, "source": "yfinance"},
            )
        except Exception as exc2:
            logger.warning("US10Y yfinance fallback also failed: %s", exc2)
            return self._unavailable(
                indicator_key="us10y",
                section="equity_macro",
                indicator_name=indicator_name,
                description="US 10Y yield unavailable (FRED + yfinance both failed).",
                error=exc2,
            )

    def _fetch_shiller_cape(self) -> dict:
        """Fetch Shiller CAPE (Cyclically Adjusted P/E ratio) from multpl.com.

        CAPE is the S&P 500 price divided by the 10-year inflation-adjusted average
        earnings. It signals long-term equity market over/undervaluation relative to
        the historical average (~16-17). Source: multpl.com (Robert Shiller data).
        """
        try:
            response = http_get(self.SHILLER_CAPE_URL, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            html = response.text

            value = self._parse_cape_from_html(html)
            if value is None:
                raise ValueError("Could not parse Shiller CAPE from multpl.com response")

            zone, zone_color = self._classify_cape(value)
            description = (
                f"Shiller CAPE zone: {zone}. "
                f"Historical avg ~16.5; >25 signals elevated long-term valuation."
            )
            return self._build_indicator(
                indicator_key="shiller_cape",
                section="equity_macro",
                indicator_name="Shiller CAPE (P/E10)",
                value=value,
                display_value=f"{value:.1f}",
                zone=zone,
                zone_color=zone_color,
                description=description,
                raw_data={"value": value, "source": "multpl.com"},
            )
        except Exception as exc:
            logger.warning("Failed to fetch Shiller CAPE from multpl.com: %s", exc)
            return self._unavailable(
                indicator_key="shiller_cape",
                section="equity_macro",
                indicator_name="Shiller CAPE (P/E10)",
                description="Shiller CAPE unavailable.",
                error=exc,
            )

    def _parse_cape_from_html(self, html: str) -> Optional[float]:
        """Extract CAPE value from multpl.com HTML using multiple strategies.

        Strategy 1 (most reliable): parse the <meta name="description"> tag which
          always contains the current value as plain text:
          "Current Shiller PE Ratio is 41.57, a change of..."

        Strategy 2 (table parse): the actual table structure is multi-line:
          <td>Jun 5, 2026</td>
          <td>
          &#x2002;          ← en-space HTML entity
          41.57
          </td>
          Uses DOTALL to match across lines and strips HTML entities.
        """
        # Strategy 1: meta description — most reliable, plain text
        m = re.search(
            r"Current Shiller PE Ratio is ([\d]+\.[\d]+)",
            html,
            re.IGNORECASE,
        )
        if m:
            return float(m.group(1))

        # Strategy 2: multi-line table cell after a date cell
        m = re.search(
            r"<td[^>]*>\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
            r"[^<]*</td>\s*<td[^>]*>[\s\S]*?([\d]+\.[\d]+)\s*</td>",
            html,
            re.IGNORECASE,
        )
        if m:
            return float(m.group(1))

        return None

    def _fetch_brent_crude(self) -> dict:
        try:
            global yf
            if yf is None:
                import yfinance as yf_module

                yf = yf_module

            payload = yf.Ticker("BZ=F").history(period="5d")
            if payload.empty:
                raise ValueError("No Brent data found")

            close_series = payload.get("Close")
            if close_series is None:
                raise ValueError("Brent close price missing")

            valid_close = close_series.dropna()
            if valid_close.empty:
                raise ValueError("No valid Brent close price")

            value = float(valid_close.iloc[-1])
            zone, zone_color = self._classify_brent(value)
            return self._build_indicator(
                indicator_key="brent_crude",
                section="equity_macro",
                indicator_name="Brent Crude Oil",
                value=value,
                display_value=f"${value:.2f}",
                zone=zone,
                zone_color=zone_color,
                description=f"Brent crude zone: {zone}.",
                raw_data={"history_rows": len(payload), "latest_close": value},
            )
        except Exception as exc:
            logger.warning("Failed to fetch Brent crude: %s", exc)
            return self._unavailable(
                indicator_key="brent_crude",
                section="equity_macro",
                indicator_name="Brent Crude Oil",
                description="Brent crude unavailable.",
                error=exc,
            )

    def _fetch_gold_silver_ratio(self) -> dict:
        """Fetch gold/silver ratio via yfinance (GC=F / SI=F).

        The former source (goldprice.org) now returns 403.  yfinance is the
        same provider used for Brent Crude and already works reliably.
        """
        try:
            global yf
            if yf is None:
                import yfinance as yf_module
                yf = yf_module

            gold_hist = yf.Ticker("GC=F").history(period="5d")
            silver_hist = yf.Ticker("SI=F").history(period="5d")

            if gold_hist.empty or silver_hist.empty:
                raise ValueError("No gold or silver price data from yfinance")

            gold_close = gold_hist["Close"].dropna()
            silver_close = silver_hist["Close"].dropna()

            if gold_close.empty or silver_close.empty:
                raise ValueError("Missing close prices for gold or silver")

            gold_price = float(gold_close.iloc[-1])
            silver_price = float(silver_close.iloc[-1])

            if silver_price <= 0:
                raise ValueError(f"Invalid silver price: {silver_price}")

            value = gold_price / silver_price
            zone, zone_color = self._classify_gold_silver(value)
            return self._build_indicator(
                indicator_key="gold_silver_ratio",
                section="gold",
                indicator_name="Gold/Silver Ratio",
                value=value,
                display_value=f"{value:.1f}",
                zone=zone,
                zone_color=zone_color,
                description=f"Gold/Silver ratio zone: {zone}.",
                raw_data={"gold_usd": gold_price, "silver_usd": silver_price, "ratio": value},
            )
        except Exception as exc:
            logger.warning("Failed to fetch gold/silver ratio: %s", exc)
            return self._unavailable(
                indicator_key="gold_silver_ratio",
                section="gold",
                indicator_name="Gold/Silver Ratio",
                description="Gold/Silver ratio unavailable.",
                error=exc,
            )

    def _fetch_crypto_fng(self) -> dict:
        try:
            response = http_get(self.CRYPTO_FNG_URL, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()

            entries = payload.get("data", [])
            if not entries:
                raise ValueError("Missing crypto fear/greed data")
            value = float(entries[0].get("value"))
            zone, zone_color = self._classify_crypto_fng(value)
            return self._build_indicator(
                indicator_key="crypto_fear_greed",
                section="crypto",
                indicator_name="Crypto Fear & Greed Index",
                value=value,
                display_value=f"{value:.0f}",
                zone=zone,
                zone_color=zone_color,
                description=f"Crypto sentiment: {zone}.",
                raw_data=payload,
            )
        except Exception as exc:
            logger.warning("Failed to fetch crypto fear/greed: %s", exc)
            return self._unavailable(
                indicator_key="crypto_fear_greed",
                section="crypto",
                indicator_name="Crypto Fear & Greed Index",
                description="Crypto sentiment unavailable.",
                error=exc,
            )

    def _fetch_coingecko_vol_indicator(
        self, coin_id: str, indicator_key: str, indicator_name: str, asset: str
    ) -> dict:
        try:
            response = http_get(
                f"{self.COINGECKO_BASE}/coins/{coin_id}/market_chart",
                params={"vs_currency": "usd", "days": 30, "interval": "daily"},
                timeout=self.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            prices = [float(p[1]) for p in payload.get("prices", []) if len(p) >= 2]
            if len(prices) < 2:
                raise ValueError("Insufficient price history")

            current_price = prices[-1]
            vol = self._calc_annualized_vol(prices)
            zone, zone_color = self._classify_crypto_vol(vol, asset)
            return self._build_indicator(
                indicator_key=indicator_key,
                section="crypto",
                indicator_name=indicator_name,
                value=vol,
                display_value=f"${current_price:,.2f} | {vol:.1f}% vol",
                zone=zone,
                zone_color=zone_color,
                description=f"{asset.upper()} 30d annualized volatility: {zone}.",
                raw_data=payload,
            )
        except Exception as exc:
            logger.warning("Failed to fetch %s volatility: %s", indicator_key, exc)
            return self._unavailable(
                indicator_key=indicator_key,
                section="crypto",
                indicator_name=indicator_name,
                description=f"{indicator_name} unavailable.",
                error=exc,
            )

    def _fetch_btc_dominance(self) -> dict:
        try:
            response = http_get(
                f"{self.COINGECKO_BASE}/global",
                timeout=self.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            value = float(payload["data"]["market_cap_percentage"]["btc"])
            zone, zone_color = self._classify_btc_dominance(value)
            return self._build_indicator(
                indicator_key="btc_dominance",
                section="crypto",
                indicator_name="BTC Dominance",
                value=value,
                display_value=f"{value:.1f}%",
                zone=zone,
                zone_color=zone_color,
                description=f"BTC dominance zone: {zone}.",
                raw_data=payload,
            )
        except Exception as exc:
            logger.warning("Failed to fetch BTC dominance: %s", exc)
            return self._unavailable(
                indicator_key="btc_dominance",
                section="crypto",
                indicator_name="BTC Dominance",
                description="BTC dominance unavailable.",
                error=exc,
            )

    def _build_indicator(
        self,
        indicator_key: str,
        section: str,
        indicator_name: str,
        value: Optional[float],
        display_value: str,
        zone: str,
        zone_color: str,
        description: str,
        raw_data: Any,
        methodology: Optional[str] = None,
        data_source: Optional[str] = None,
    ) -> dict:
        # PRD 2026-07-07 F4.3: every dashboard metric carries source/methodology/
        # as_of. as_of mirrors updated_at (this fetch's timestamp); methodology/
        # data_source are None for indicators that aren't methodology-sensitive
        # (only Buffett variants set them today — see _fetch_buffett_us_computed
        # and the buffett branch of _fetch_fred_indicator).
        updated_at = datetime.utcnow().replace(microsecond=0).isoformat()
        return {
            "indicator_key": indicator_key,
            "section": section,
            "indicator_name": indicator_name,
            "value": value,
            "display_value": display_value,
            "zone": zone,
            "zone_color": zone_color,
            "description": description,
            "raw_json": json.dumps(raw_data, ensure_ascii=False),
            "updated_at": updated_at,
            "methodology": methodology,
            "data_source": data_source,
            "as_of": updated_at,
        }

    def _unavailable(
        self,
        indicator_key: str,
        section: str,
        indicator_name: str,
        description: str,
        error: Optional[Exception] = None,
    ) -> dict:
        detail = str(error) if error else "Unknown error"
        return self._build_indicator(
            indicator_key=indicator_key,
            section=section,
            indicator_name=indicator_name,
            value=None,
            display_value="Unavailable",
            zone="Unavailable",
            zone_color="grey",
            description=f"{description} ({detail})",
            raw_data={"error": detail},
        )

    def _classify_fear_greed(self, value: float) -> tuple[str, str]:
        if value < 25:
            return "Extreme Fear", "red"
        if value < 45:
            return "Fear", "orange"
        if value <= 55:
            return "Neutral", "yellow"
        if value <= 75:
            return "Greed", "light-green"
        return "Extreme Greed", "green"

    def _classify_vix(self, value: float) -> tuple[str, str]:
        if value < 15:
            return "Low", "green"
        if value < 20:
            return "Normal", "yellow"
        if value <= 30:
            return "Elevated", "orange"
        return "High", "red"

    def _classify_brent(self, value: float) -> tuple[str, str]:
        if value < 70:
            return "Safe", "green"
        if value < 90:
            return "Normal", "yellow"
        if value < 100:
            return "Elevated", "orange"
        return "Danger", "red"

    def _classify_us10y(self, value: float) -> tuple[str, str]:
        if value < 3.5:
            return "Accommodative", "green"
        if value < 4.0:
            return "Normal", "yellow"
        if value < 4.5:
            return "Elevated", "orange"
        return "Restrictive", "red"

    def _classify_buffett(self, value: float) -> tuple[str, str]:
        if value < 75:
            return "Significantly Undervalued", "green"
        if value < 90:
            return "Modestly Undervalued", "light-green"
        if value <= 115:
            return "Fair Value", "yellow"
        if value <= 135:
            return "Modestly Overvalued", "orange"
        return "Significantly Overvalued", "red"

    def _classify_cape(self, value: float) -> tuple[str, str]:
        """Classify Shiller CAPE zone. Historical avg ~16.5; post-1990 avg ~24."""
        if value < 15:
            return "Deeply Undervalued", "green"
        if value < 20:
            return "Fair Value", "light-green"
        if value < 25:
            return "Slightly Elevated", "yellow"
        if value < 32:
            return "Elevated", "orange"
        return "Significantly Overvalued", "red"

    def _classify_gold_silver(self, value: float) -> tuple[str, str]:
        if value < 55:
            return "Strong Buy", "green"
        if value < 70:
            return "Buy", "light-green"
        if value < 85:
            return "Hold", "yellow"
        if value <= 95:
            return "Sell", "orange"
        return "Strong Sell", "red"

    def _classify_crypto_fng(self, value: float) -> tuple[str, str]:
        return self._classify_fear_greed(value)

    def _classify_btc_dominance(self, value: float) -> tuple[str, str]:
        if value < 35:
            return "Altcoin Season", "red"
        if value < 42:
            return "Leaning Alts", "orange"
        if value < 55:
            return "Normal", "yellow"
        if value <= 60:
            return "BTC Strong", "orange"
        return "BTC Dominant", "red"

    def _calc_annualized_vol(self, prices: list[float]) -> float:
        if len(prices) < 2:
            return 0.0
        daily_returns: list[float] = []
        for prev, curr in zip(prices, prices[1:]):
            if prev <= 0:
                continue
            daily_returns.append((curr - prev) / prev)
        if len(daily_returns) < 2:
            return 0.0
        mean_ret = sum(daily_returns) / len(daily_returns)
        variance = sum((ret - mean_ret) ** 2 for ret in daily_returns) / (len(daily_returns) - 1)
        return math.sqrt(variance) * math.sqrt(365) * 100

    def _classify_crypto_vol(self, vol: float, asset: str) -> tuple[str, str]:
        key = asset.lower()
        if key == "eth":
            if vol > 100:
                return "Extreme", "red"
            if vol >= 75:
                return "High", "orange"
            if vol >= 30:
                return "Normal", "yellow"
            if vol >= 25:
                return "Low", "orange"
            return "Extreme Low", "green"

        if vol > 80:
            return "Extreme", "red"
        if vol >= 60:
            return "High", "orange"
        if vol >= 25:
            return "Normal", "yellow"
        if vol >= 20:
            return "Low", "orange"
        return "Extreme Low", "green"
