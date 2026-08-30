"""Tests for market sentiment macro analyzer."""

import pytest
from unittest.mock import MagicMock, patch


def _mock_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_classification_thresholds_cover_expected_zones():
    """Key classifiers should map values to documented zones."""
    from src.financial_analysis.macro_analyzer import MacroAnalyzer

    analyzer = MacroAnalyzer()

    assert analyzer._classify_fear_greed(20) == ("Extreme Fear", "red")
    assert analyzer._classify_fear_greed(50) == ("Neutral", "yellow")
    assert analyzer._classify_vix(14.9) == ("Low", "green")
    assert analyzer._classify_vix(26) == ("Elevated", "orange")
    assert analyzer._classify_buffett(140) == ("Significantly Overvalued", "red")
    assert analyzer._classify_gold_silver(72) == ("Hold", "yellow")
    assert analyzer._classify_btc_dominance(61) == ("BTC Dominant", "red")


def test_classify_cape_thresholds():
    """Shiller CAPE classifier should map values to correct zones."""
    from src.financial_analysis.macro_analyzer import MacroAnalyzer

    analyzer = MacroAnalyzer()

    assert analyzer._classify_cape(12.0) == ("Deeply Undervalued", "green")
    assert analyzer._classify_cape(17.5) == ("Fair Value", "light-green")
    assert analyzer._classify_cape(22.0) == ("Slightly Elevated", "yellow")
    assert analyzer._classify_cape(28.5) == ("Elevated", "orange")
    assert analyzer._classify_cape(35.0) == ("Significantly Overvalued", "red")
    # Boundary checks
    assert analyzer._classify_cape(14.9)[0] == "Deeply Undervalued"
    assert analyzer._classify_cape(15.0)[0] == "Fair Value"
    assert analyzer._classify_cape(20.0)[0] == "Slightly Elevated"
    assert analyzer._classify_cape(25.0)[0] == "Elevated"
    assert analyzer._classify_cape(32.0)[0] == "Significantly Overvalued"


def test_fetch_shiller_cape_parses_multpl_meta_description():
    """Strategy 1: parse CAPE from the <meta description> tag (most reliable path).

    multpl.com's meta description always contains the current value as plain text:
    'Current Shiller PE Ratio is 41.57, a change of ...'
    This is far more robust than table parsing.
    """
    from unittest.mock import patch, MagicMock
    from src.financial_analysis.macro_analyzer import MacroAnalyzer

    html_body = (
        '<meta name="description" content="Shiller PE Ratio table by month, historic, '
        'and current data. Current Shiller PE Ratio is 41.57, a change of -1.13 from '
        'previous market close." />'
    )
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.text = html_body

    with patch("src.financial_analysis.macro_analyzer.http_get", return_value=mock_resp):
        result = MacroAnalyzer()._fetch_shiller_cape()

    assert result["indicator_key"] == "shiller_cape"
    assert result["section"] == "equity_macro"
    assert result["value"] == pytest.approx(41.57)
    assert result["display_value"] == "41.6"
    assert result["zone"] == "Significantly Overvalued"
    assert result["zone_color"] == "red"


def test_fetch_shiller_cape_falls_back_to_table_parse():
    """Strategy 2: parse the multi-line table cell when meta description is absent.

    Actual multpl.com table structure (confirmed from live HTML):
    <td>Jun 5, 2026</td>
    <td>
    &#x2002;
    41.57
    </td>
    """
    from unittest.mock import patch, MagicMock
    from src.financial_analysis.macro_analyzer import MacroAnalyzer

    html_body = (
        "<td>Jun 5, 2026</td>\n"
        "<td>\n"
        "&#x2002;\n"
        "34.52\n"
        "</td>\n"
    )
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.text = html_body

    with patch("src.financial_analysis.macro_analyzer.http_get", return_value=mock_resp):
        result = MacroAnalyzer()._fetch_shiller_cape()

    assert result["indicator_key"] == "shiller_cape"
    assert result["value"] == pytest.approx(34.52)
    assert result["zone"] == "Significantly Overvalued"


def test_fetch_shiller_cape_returns_unavailable_on_error():
    """_fetch_shiller_cape should return Unavailable indicator when the request fails."""
    from unittest.mock import patch
    from src.financial_analysis.macro_analyzer import MacroAnalyzer

    with patch(
        "src.financial_analysis.macro_analyzer.http_get",
        side_effect=Exception("network error"),
    ):
        result = MacroAnalyzer()._fetch_shiller_cape()

    assert result["indicator_key"] == "shiller_cape"
    assert result["value"] is None
    assert result["zone_color"] == "grey"


def test_classify_brent_thresholds():
    from src.financial_analysis.macro_analyzer import MacroAnalyzer

    analyzer = MacroAnalyzer()

    assert analyzer._classify_brent(69.9) == ("Safe", "green")
    assert analyzer._classify_brent(70.0) == ("Normal", "yellow")
    assert analyzer._classify_brent(89.9) == ("Normal", "yellow")
    assert analyzer._classify_brent(90.0) == ("Elevated", "orange")
    assert analyzer._classify_brent(99.9) == ("Elevated", "orange")
    assert analyzer._classify_brent(100.0) == ("Danger", "red")


def test_classify_us10y_thresholds():
    from src.financial_analysis.macro_analyzer import MacroAnalyzer

    analyzer = MacroAnalyzer()

    assert analyzer._classify_us10y(3.49) == ("Accommodative", "green")
    assert analyzer._classify_us10y(3.5) == ("Normal", "yellow")
    assert analyzer._classify_us10y(3.99) == ("Normal", "yellow")
    assert analyzer._classify_us10y(4.0) == ("Elevated", "orange")
    assert analyzer._classify_us10y(4.49) == ("Elevated", "orange")
    assert analyzer._classify_us10y(4.5) == ("Restrictive", "red")


@patch("src.financial_analysis.macro_analyzer.yf")
def test_brent_crude_yfinance_failure_returns_unavailable(mock_yf):
    from src.financial_analysis.macro_analyzer import MacroAnalyzer

    mock_yf.Ticker.side_effect = RuntimeError("yfinance down")

    indicator = MacroAnalyzer()._fetch_brent_crude()

    assert indicator["indicator_key"] == "brent_crude"
    assert indicator["value"] is None
    assert indicator["zone_color"] == "grey"


def test_fetch_all_without_fred_key_marks_fred_indicators_unavailable():
    """FRED indicators should be unavailable when API key is missing.

    Architecture changes since original test was written:
    - HTTP calls now go through http_get (src.utils.http_client), not requests.get directly.
    - Gold/Silver ratio now uses yfinance (GC=F / SI=F), not goldprice.org.
    - VIX and US10Y now have yfinance fallbacks (^VIX / ^TNX); without FRED key they
      fall back to yfinance and may return real values — they are no longer guaranteed Unavailable.
    - Buffett indicators (us/cn/jp/eu) have no yfinance fallback — they remain Unavailable.
    """
    import pandas as pd
    from unittest.mock import patch, MagicMock
    from src.financial_analysis.macro_analyzer import MacroAnalyzer

    def fake_http_get(url, params=None, timeout=None, headers=None, **kwargs):
        if "fearandgreed" in url:
            return _mock_response({"fear_and_greed": {"score": 48}})
        if "alternative.me" in url:
            return _mock_response({"data": [{"value": "35"}]})
        if "market_chart" in url:
            return _mock_response({
                "prices": [[1, 100.0], [2, 101.0], [3, 99.5], [4, 102.0], [5, 101.5]]
            })
        if "coingecko" in url and "global" in url:
            return _mock_response({"data": {"market_cap_percentage": {"btc": 58.4}}})
        if "multpl.com" in url:
            # Use meta-description format (Strategy 1 — most reliable)
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.text = (
                '<meta name="description" content="Current Shiller PE Ratio is 33.70,'
                ' a change of -0.50 from previous market close." />'
            )
            return resp
        raise AssertionError(f"Unexpected URL in test: {url}")

    def _make_price_history(price: float) -> pd.DataFrame:
        import datetime
        df = pd.DataFrame(
            {"Close": [price]},
            index=pd.DatetimeIndex([datetime.date.today()])
        )
        return df

    # Mock yfinance Ticker for gold (GC=F), silver (SI=F), VIX (^VIX), and TNX (^TNX).
    # fast_info["lastPrice"] is the primary path in _yf_last_price — must be mocked
    # explicitly so MagicMock's default __float__ (returns 1.0) doesn't short-circuit.
    def mock_yf_ticker(ticker_sym):
        m = MagicMock()
        prices = {
            "GC=F": 2900.0,
            "SI=F": 29.0,
            "^VIX": 18.5,
            "^TNX": 4.26,
            "BZ=F": 82.0,
        }
        price = prices.get(ticker_sym)
        if price is not None:
            m.history.return_value = _make_price_history(price)
            m.fast_info = {"lastPrice": price}
        else:
            m.history.return_value = pd.DataFrame()
            m.fast_info = {}
        return m

    import src.financial_analysis.macro_analyzer as ma_mod
    with patch("src.financial_analysis.macro_analyzer.http_get", side_effect=fake_http_get), \
         patch.object(ma_mod, "yf", create=True) as mock_yf:
        mock_yf.Ticker.side_effect = mock_yf_ticker
        indicators = MacroAnalyzer(fred_api_key=None).fetch_all()

    by_key = {item["indicator_key"]: item for item in indicators}

    assert len(indicators) == 14  # +1 for shiller_cape (added in Issue #10 follow-up)
    assert by_key["fear_greed"]["value"] == 48
    # Gold/silver: ratio = 2900/29 = 100.0 (from yfinance mock)
    assert by_key["gold_silver_ratio"]["value"] == pytest.approx(100.0)
    assert by_key["crypto_fear_greed"]["value"] == 35
    assert by_key["btc_dominance"]["value"] == 58.4

    # VIX + US10Y: fall back to yfinance when FRED key is missing → return real values
    assert by_key["vix"]["value"] == pytest.approx(18.5)
    assert by_key["us10y"]["value"] == pytest.approx(4.26)

    # Shiller CAPE: fetched from multpl.com mock
    assert by_key["shiller_cape"]["value"] == pytest.approx(33.70)
    assert by_key["shiller_cape"]["zone"] == "Significantly Overvalued"

    # Buffett indicators: no yfinance fallback → Unavailable
    for fred_key in ("buffett_us", "buffett_cn", "buffett_jp", "buffett_eu"):
        assert by_key[fred_key]["value"] is None, f"Expected {fred_key} to be None"
        assert by_key[fred_key]["zone"] == "Unavailable"
        assert by_key[fred_key]["zone_color"] == "grey"


def test_buffett_us_computed_uses_equities_over_gdp():
    """US Buffett = NCBEILQ027S ($millions) / GDP ($billions) * 100, near-current
    (replaces the World Bank series frozen at 2020). Owner report 2026-06-28."""
    from src.financial_analysis.macro_analyzer import MacroAnalyzer
    analyzer = MacroAnalyzer(fred_api_key="dummy")

    def fake_latest(series_id):
        return {
            "NCBEILQ027S": (69_511_628.0, "2026-01-01"),  # $millions
            "GDP": (31_865.721, "2026-01-01"),            # $billions
        }[series_id]

    with patch.object(analyzer, "_fred_latest", side_effect=fake_latest):
        r = analyzer._fetch_buffett_us_computed()

    # 69,511.628B / 31,865.721B * 100 = 218.1%
    assert r["indicator_key"] == "buffett_us"
    assert abs(r["value"] - 218.1) < 0.2
    assert r["display_value"] == "218.1%"
    assert r["zone"] == "Significantly Overvalued"
    assert "NCBEILQ027S" in r["description"]

    # PRD 2026-07-07 F4.3/defect(c): this Fed-Z.1-derived variant must be
    # tagged distinctly from the classic World Bank TMC/GDP fallback so the
    # two are never confused (the audit finding: 194.9% mislabeled as ~235%).
    assert r["methodology"] == "buffett_fed_z1_corp_equities_gdp"
    assert "NCBEILQ027S" in r["data_source"]


def test_buffett_us_computed_falls_back_to_world_bank_on_error():
    """If the computed path fails, fall back to the World Bank series WITH a
    staleness warning rather than crashing."""
    from src.financial_analysis.macro_analyzer import MacroAnalyzer
    analyzer = MacroAnalyzer(fred_api_key="dummy")

    wb_payload = {"observations": [{"date": "2020-01-01", "value": "194.889"}]}
    with patch.object(analyzer, "_fred_latest", side_effect=RuntimeError("FRED down")), patch(
        "src.financial_analysis.macro_analyzer.http_get", return_value=_mock_response(wb_payload)
    ):
        r = analyzer._fetch_buffett_us_computed()

    assert r["indicator_key"] == "buffett_us"
    assert abs(r["value"] - 194.889) < 0.01
    assert "STALE" in r["description"]

    # PRD 2026-07-07 F4.3: the classic World Bank TMC/GDP fallback must carry
    # its own distinct methodology tag (never confused with buffett_fed_z1_corp_equities_gdp).
    assert r["methodology"] == "buffett_classic_tmc_gdp"
    assert "DDDM01USA156NWDB" in r["data_source"]


def test_staleness_note_flags_old_data_only():
    from src.financial_analysis.macro_analyzer import MacroAnalyzer
    analyzer = MacroAnalyzer(fred_api_key="dummy")
    assert "STALE" in analyzer._staleness_note("2020-01-01")
    assert analyzer._staleness_note("2025-10-01") == ""
    assert analyzer._staleness_note("") == ""
