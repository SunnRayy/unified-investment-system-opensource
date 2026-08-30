"""multpl.com fetcher for S&P 500 and Nasdaq100 historical P/E ratios.

Free data source (no API key). Parses the public monthly tables.
Used for one-time history seed; daily snapshot comes from yfinance.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime

logger = logging.getLogger(__name__)

_PE_BOUNDS = (3.0, 200.0)
_SP500_URL = "https://www.multpl.com/s-p-500-pe-ratio/table/by-month"
_NASDAQ100_URL = "https://www.multpl.com/nasdaq100-pe-ratio/table/by-month"


def _in_bounds(value: float, bounds: tuple[float, float]) -> bool:
    return bounds[0] <= value <= bounds[1]


def fetch_multpl_sp500_pe_history() -> list[dict]:
    """Fetch monthly S&P 500 TTM P/E history from multpl.com.

    Returns [{date: YYYY-MM-DD, value: float}] sorted oldest-first.
    Returns [] on any failure.
    """
    try:
        from bs4 import BeautifulSoup

        from src.utils.http_client import http_get

        resp = http_get(
            _SP500_URL,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot)"},
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", {"id": "datatable"})
        if table is None:
            logger.warning("multpl.com: datatable not found in response")
            return []

        result: list[dict] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            date_str = cells[0].get_text(strip=True)
            val_str = cells[1].get_text(strip=True)
            try:
                d = datetime.strptime(date_str, "%b %d, %Y")
                v = float(val_str.replace(",", ""))
                if not math.isfinite(v) or not _in_bounds(v, _PE_BOUNDS):
                    continue
                result.append({"date": d.strftime("%Y-%m-%d"), "value": v})
            except (ValueError, TypeError):
                continue

        result.sort(key=lambda x: x["date"])
        logger.info("multpl.com S&P 500 PE history: %d rows", len(result))
        return result

    except Exception as exc:
        logger.warning("fetch_multpl_sp500_pe_history failed: %s", exc)
        return []


def fetch_multpl_nasdaq100_pe_history() -> list[dict]:
    """Fetch monthly Nasdaq100 TTM P/E history from multpl.com.

    Returns [{date: YYYY-MM-DD, value: float}] sorted oldest-first.
    Returns [] on any failure.
    """
    try:
        from bs4 import BeautifulSoup

        from src.utils.http_client import http_get

        resp = http_get(
            _NASDAQ100_URL,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot)"},
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", {"id": "datatable"})
        if table is None:
            logger.warning("multpl.com: datatable not found for Nasdaq100")
            return []

        result: list[dict] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            date_str = cells[0].get_text(strip=True)
            val_str = cells[1].get_text(strip=True)
            try:
                d = datetime.strptime(date_str, "%b %d, %Y")
                v = float(val_str.replace(",", ""))
                if not math.isfinite(v) or not _in_bounds(v, _PE_BOUNDS):
                    continue
                result.append({"date": d.strftime("%Y-%m-%d"), "value": v})
            except (ValueError, TypeError):
                continue

        result.sort(key=lambda x: x["date"])
        logger.info("multpl.com Nasdaq100 PE history: %d rows", len(result))
        return result

    except Exception as exc:
        logger.warning("fetch_multpl_nasdaq100_pe_history failed: %s", exc)
        return []
