"""Tests for multpl.com S&P 500 PE history fetcher."""
from unittest.mock import patch, MagicMock


_SAMPLE_HTML = """
<html><body>
<table id="datatable">
  <thead><tr><th>Date</th><th>Value</th></tr></thead>
  <tbody>
    <tr><td>Apr 1, 2026</td><td>27.24</td></tr>
    <tr><td>Mar 1, 2026</td><td>26.80</td></tr>
    <tr><td>Jan 1, 2016</td><td>22.13</td></tr>
    <tr><td>Jan 1, 2000</td><td>29.54</td></tr>
  </tbody>
</table>
</body></html>
"""

_SAMPLE_HTML_BAD_ROW = """
<html><body>
<table id="datatable">
  <tbody>
    <tr><td>Apr 1, 2026</td><td>27.24</td></tr>
    <tr><td>bad date</td><td>not a number</td></tr>
    <tr><td>Mar 1, 2026</td><td>999.99</td></tr>
    <tr><td>Feb 1, 2026</td><td>25.00</td></tr>
  </tbody>
</table>
</body></html>
"""


def _mock_response(html: str, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.text = html
    resp.raise_for_status = MagicMock()
    return resp


def test_returns_sorted_list():
    from src.services.valuation.fetchers.multpl_fetcher import fetch_multpl_sp500_pe_history
    with patch("src.utils.http_client.http_get", return_value=_mock_response(_SAMPLE_HTML)):
        result = fetch_multpl_sp500_pe_history()
    assert len(result) == 4
    assert result[0]["date"] == "2000-01-01"   # oldest first
    assert result[-1]["date"] == "2026-04-01"  # newest last
    assert result[-1]["value"] == 27.24


def test_skips_bad_rows():
    from src.services.valuation.fetchers.multpl_fetcher import fetch_multpl_sp500_pe_history
    with patch("src.utils.http_client.http_get", return_value=_mock_response(_SAMPLE_HTML_BAD_ROW)):
        result = fetch_multpl_sp500_pe_history()
    dates = [r["date"] for r in result]
    assert "2026-04-01" in dates
    assert "2026-02-01" in dates
    # bad date / bad value rows excluded
    assert len(result) == 2


def test_out_of_bounds_pe_excluded():
    html = """
    <html><body><table id="datatable"><tbody>
    <tr><td>Apr 1, 2026</td><td>999.99</td></tr>
    <tr><td>Mar 1, 2026</td><td>1.50</td></tr>
    <tr><td>Feb 1, 2026</td><td>25.00</td></tr>
    </tbody></table></body></html>
    """
    from src.services.valuation.fetchers.multpl_fetcher import fetch_multpl_sp500_pe_history
    with patch("src.utils.http_client.http_get", return_value=_mock_response(html)):
        result = fetch_multpl_sp500_pe_history()
    assert len(result) == 1
    assert result[0]["value"] == 25.00


def test_returns_empty_on_missing_table():
    from src.services.valuation.fetchers.multpl_fetcher import fetch_multpl_sp500_pe_history
    with patch("src.utils.http_client.http_get", return_value=_mock_response("<html><body>no table</body></html>")):
        result = fetch_multpl_sp500_pe_history()
    assert result == []


def test_returns_empty_on_request_error():
    from src.services.valuation.fetchers.multpl_fetcher import fetch_multpl_sp500_pe_history
    with patch("src.utils.http_client.http_get", side_effect=Exception("network error")):
        result = fetch_multpl_sp500_pe_history()
    assert result == []


# ── Nasdaq100 tests ───────────────────────────────────────────────────────────

def test_nasdaq100_returns_sorted_list():
    from src.services.valuation.fetchers.multpl_fetcher import fetch_multpl_nasdaq100_pe_history
    with patch("src.utils.http_client.http_get", return_value=_mock_response(_SAMPLE_HTML)):
        result = fetch_multpl_nasdaq100_pe_history()
    assert len(result) == 4
    assert result[0]["date"] == "2000-01-01"
    assert result[-1]["date"] == "2026-04-01"
    assert result[-1]["value"] == 27.24


def test_nasdaq100_returns_empty_on_request_error():
    from src.services.valuation.fetchers.multpl_fetcher import fetch_multpl_nasdaq100_pe_history
    with patch("src.utils.http_client.http_get", side_effect=Exception("network error")):
        result = fetch_multpl_nasdaq100_pe_history()
    assert result == []
