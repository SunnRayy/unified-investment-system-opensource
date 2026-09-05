"""Phase 1 fetcher tests — written BEFORE implementation (TDD)."""
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch


# ══════════════════════════════════════════════════════════════════
# akshare_index_pe — fetch_cn_index_snapshot
# ══════════════════════════════════════════════════════════════════

class TestFetchCnIndexSnapshot:
    def _pe_df(self):
        return pd.DataFrame(
            [("2024-01-01", 12.5), ("2024-01-02", 13.0)],
            columns=["日期", "滚动市盈率"],
        )

    def _pb_df(self):
        return pd.DataFrame(
            [("2024-01-01", 1.2), ("2024-01-02", 1.3)],
            columns=["日期", "市净率"],
        )

    def test_returns_pe_ttm_from_last_row(self):
        with patch("akshare.stock_index_pe_lg", return_value=self._pe_df()), \
             patch("akshare.stock_index_pb_lg", return_value=self._pb_df()):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_snapshot
            result = fetch_cn_index_snapshot("沪深300")
            assert result["pe_ttm"] == pytest.approx(13.0)

    def test_returns_pb_ratio_from_last_row(self):
        with patch("akshare.stock_index_pe_lg", return_value=self._pe_df()), \
             patch("akshare.stock_index_pb_lg", return_value=self._pb_df()):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_snapshot
            result = fetch_cn_index_snapshot("沪深300")
            assert result["pb_ratio"] == pytest.approx(1.3)

    def test_data_source_is_akshare_index_pe(self):
        with patch("akshare.stock_index_pe_lg", return_value=self._pe_df()), \
             patch("akshare.stock_index_pb_lg", return_value=self._pb_df()):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_snapshot
            result = fetch_cn_index_snapshot("沪深300")
            assert result["data_source"] == "akshare_index_pe"

    def test_passes_symbol_to_akshare(self):
        with patch("akshare.stock_index_pe_lg", return_value=self._pe_df()) as mock_pe, \
             patch("akshare.stock_index_pb_lg", return_value=self._pb_df()):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_snapshot
            fetch_cn_index_snapshot("中证500")
            mock_pe.assert_called_once_with(symbol="中证500")

    def test_returns_empty_on_akshare_exception(self):
        with patch("akshare.stock_index_pe_lg", side_effect=Exception("network error")):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_snapshot
            assert fetch_cn_index_snapshot("沪深300") == {}

    def test_returns_empty_when_pe_df_empty(self):
        with patch("akshare.stock_index_pe_lg", return_value=pd.DataFrame()), \
             patch("akshare.stock_index_pb_lg", return_value=self._pb_df()):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_snapshot
            assert fetch_cn_index_snapshot("沪深300") == {}


# ══════════════════════════════════════════════════════════════════
# akshare_index_pe — fetch_cn_index_history
# ══════════════════════════════════════════════════════════════════

class TestFetchCnIndexHistory:
    def _history_df(self, n=15):
        rows = [(f"2024-{(i % 12) + 1:02d}-01", 10.0 + i) for i in range(n)]
        return pd.DataFrame(rows, columns=["日期", "滚动市盈率"])

    def test_returns_list_of_dicts(self):
        with patch("akshare.stock_index_pe_lg", return_value=self._history_df()):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_history
            result = fetch_cn_index_history("沪深300")
            assert isinstance(result, list)
            assert len(result) >= 10

    def test_each_row_has_date_and_pe_ttm(self):
        with patch("akshare.stock_index_pe_lg", return_value=self._history_df()):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_history
            result = fetch_cn_index_history("沪深300")
            assert "date" in result[0]
            assert "pe_ttm" in result[0]

    def test_filters_zero_and_invalid_pe_rows(self):
        df = pd.DataFrame(
            [("2024-01-01", 0.0), ("2024-01-02", 12.5), ("2024-01-03", None)],
            columns=["日期", "滚动市盈率"],
        )
        with patch("akshare.stock_index_pe_lg", return_value=df):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_history
            result = fetch_cn_index_history("沪深300")
            assert len(result) == 1
            assert result[0]["pe_ttm"] == pytest.approx(12.5)

    def test_returns_empty_list_on_exception(self):
        with patch("akshare.stock_index_pe_lg", side_effect=Exception("timeout")), \
             patch("akshare.stock_zh_index_hist_csindex", side_effect=Exception("csindex fail")):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_history
            assert fetch_cn_index_history("沪深300") == []


# ══════════════════════════════════════════════════════════════════
# akshare_market_pe — fetch_cn_market_snapshot
# ══════════════════════════════════════════════════════════════════

class TestFetchCnMarketSnapshot:
    def test_kechuang50_uses_shiying_column(self):
        df = pd.DataFrame([("2024-01-02", 45.0)], columns=["日期", "市盈率"])
        with patch("akshare.stock_market_pe_lg", return_value=df):
            from src.services.valuation.fetchers.akshare_market_pe import fetch_cn_market_snapshot
            result = fetch_cn_market_snapshot("科创50")
            assert result["pe_ttm"] == pytest.approx(45.0)

    def test_chuangye_uses_average_pe_column(self):
        df = pd.DataFrame(
            [("2024-01-01", 30.0), ("2024-01-02", 32.0)],
            columns=["日期", "平均市盈率"],
        )
        with patch("akshare.stock_market_pe_lg", return_value=df):
            from src.services.valuation.fetchers.akshare_market_pe import fetch_cn_market_snapshot
            result = fetch_cn_market_snapshot("创业板")
            assert result["pe_ttm"] == pytest.approx(32.0)

    def test_data_source_is_akshare_market_pe(self):
        df = pd.DataFrame([("2024-01-01", 45.0)], columns=["日期", "市盈率"])
        with patch("akshare.stock_market_pe_lg", return_value=df):
            from src.services.valuation.fetchers.akshare_market_pe import fetch_cn_market_snapshot
            result = fetch_cn_market_snapshot("科创50")
            assert result["data_source"] == "akshare_market_pe"

    def test_passes_symbol_to_akshare(self):
        df = pd.DataFrame([("2024-01-01", 45.0)], columns=["日期", "市盈率"])
        with patch("akshare.stock_market_pe_lg", return_value=df) as mock_fn:
            from src.services.valuation.fetchers.akshare_market_pe import fetch_cn_market_snapshot
            fetch_cn_market_snapshot("科创50")
            mock_fn.assert_called_once_with(symbol="科创50")

    def test_returns_empty_on_exception(self):
        with patch("akshare.stock_market_pe_lg", side_effect=Exception("timeout")):
            from src.services.valuation.fetchers.akshare_market_pe import fetch_cn_market_snapshot
            assert fetch_cn_market_snapshot("科创50") == {}

    def test_returns_empty_when_df_empty(self):
        with patch("akshare.stock_market_pe_lg", return_value=pd.DataFrame()):
            from src.services.valuation.fetchers.akshare_market_pe import fetch_cn_market_snapshot
            assert fetch_cn_market_snapshot("科创50") == {}


# ══════════════════════════════════════════════════════════════════
# hk_index — fetch_hk_index_snapshot
# ══════════════════════════════════════════════════════════════════

class TestFetchHkIndexSnapshot:
    def _mock_ticker(self, trailing_pe=18.5):
        t = MagicMock()
        t.info = {"trailingPE": trailing_pe, "longName": "CSOP Hang Seng TECH ETF"}
        return t

    def test_returns_pe_ttm(self):
        with patch("yfinance.Ticker", return_value=self._mock_ticker(18.5)):
            from src.services.valuation.fetchers.hk_index import fetch_hk_index_snapshot
            result = fetch_hk_index_snapshot("3033.HK")
            assert result["pe_ttm"] == pytest.approx(18.5)

    def test_data_source_is_yfinance_hk_proxy(self):
        with patch("yfinance.Ticker", return_value=self._mock_ticker()):
            from src.services.valuation.fetchers.hk_index import fetch_hk_index_snapshot
            result = fetch_hk_index_snapshot("3033.HK")
            assert result["data_source"] == "yfinance_hk_proxy"

    def test_returns_empty_when_trailing_pe_absent(self):
        t = MagicMock()
        t.info = {"longName": "Test ETF"}
        with patch("yfinance.Ticker", return_value=t):
            from src.services.valuation.fetchers.hk_index import fetch_hk_index_snapshot
            assert fetch_hk_index_snapshot("3033.HK") == {}

    def test_returns_empty_on_yfinance_exception(self):
        with patch("yfinance.Ticker", side_effect=Exception("connection failed")):
            from src.services.valuation.fetchers.hk_index import fetch_hk_index_snapshot
            assert fetch_hk_index_snapshot("3033.HK") == {}

    def test_rejects_out_of_bounds_pe(self):
        t = MagicMock()
        t.info = {"trailingPE": 0.5}  # below sane lower bound
        with patch("yfinance.Ticker", return_value=t):
            from src.services.valuation.fetchers.hk_index import fetch_hk_index_snapshot
            result = fetch_hk_index_snapshot("3033.HK")
            assert result == {} or result.get("pe_ttm") is None


# ══════════════════════════════════════════════════════════════════
# us_index_pe — fetch_us_index_snapshot
# ══════════════════════════════════════════════════════════════════

class TestFetchUsIndexSnapshot:
    def _mock_ticker(self, trailing_pe=26.5, dividend_yield=0.014):
        t = MagicMock()
        t.info = {"trailingPE": trailing_pe, "dividendYield": dividend_yield}
        return t

    def test_returns_pe_ttm(self):
        with patch("yfinance.Ticker", return_value=self._mock_ticker()):
            from src.services.valuation.fetchers.us_index_pe import fetch_us_index_snapshot
            result = fetch_us_index_snapshot("VOO")
            assert result["pe_ttm"] == pytest.approx(26.5)

    def test_data_source_is_yfinance_index_proxy(self):
        with patch("yfinance.Ticker", return_value=self._mock_ticker()):
            from src.services.valuation.fetchers.us_index_pe import fetch_us_index_snapshot
            result = fetch_us_index_snapshot("VOO")
            assert result["data_source"] == "yfinance_index_proxy"

    def test_normalizes_decimal_yield_to_percent(self):
        with patch("yfinance.Ticker", return_value=self._mock_ticker(dividend_yield=0.014)):
            from src.services.valuation.fetchers.us_index_pe import fetch_us_index_snapshot
            result = fetch_us_index_snapshot("VOO")
            assert result["dividend_yield"] == pytest.approx(1.4)

    def test_passes_percent_yield_through_unchanged(self):
        with patch("yfinance.Ticker", return_value=self._mock_ticker(dividend_yield=1.4)):
            from src.services.valuation.fetchers.us_index_pe import fetch_us_index_snapshot
            result = fetch_us_index_snapshot("VOO")
            assert result["dividend_yield"] == pytest.approx(1.4)

    def test_returns_empty_when_no_trailing_pe(self):
        t = MagicMock()
        t.info = {"dividendYield": 0.014}
        with patch("yfinance.Ticker", return_value=t):
            from src.services.valuation.fetchers.us_index_pe import fetch_us_index_snapshot
            assert fetch_us_index_snapshot("VOO") == {}

    def test_returns_empty_on_yfinance_exception(self):
        with patch("yfinance.Ticker", side_effect=Exception("connection failed")):
            from src.services.valuation.fetchers.us_index_pe import fetch_us_index_snapshot
            assert fetch_us_index_snapshot("VOO") == {}


# ══════════════════════════════════════════════════════════════════
# akshare_index_pe — fetch_cn_index_funddb (P0-B)
# ══════════════════════════════════════════════════════════════════

class TestFetchCnIndexFunddb:
    def _pb_df(self, n: int = 10) -> "pd.DataFrame":
        rows = [(f"2024-{(i % 12) + 1:02d}-01", 1.0 + i * 0.1) for i in range(n)]
        return pd.DataFrame(rows, columns=["日期", "市净率"])

    def test_pb_returns_list_of_dicts(self):
        df = self._pb_df(8)
        with patch("akshare.stock_index_pb_lg", return_value=df):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_funddb
            result = fetch_cn_index_funddb("沪深300", "市净率")
            assert len(result) == 8
            assert "date" in result[0] and "value" in result[0]
            assert all(r["value"] > 0 for r in result)

    def test_non_pb_indicator_returns_empty(self):
        from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_funddb
        assert fetch_cn_index_funddb("沪深300", "市盈率") == []
        assert fetch_cn_index_funddb("沪深300", "股息率") == []

    def test_filters_out_of_bounds_pb_values(self):
        df = pd.DataFrame(
            [("2024-01-01", 0.05), ("2024-01-02", 1.5), ("2024-01-03", 60.0)],
            columns=["日期", "市净率"],
        )
        with patch("akshare.stock_index_pb_lg", return_value=df):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_funddb
            result = fetch_cn_index_funddb("沪深300", "市净率")
            assert len(result) == 1
            assert result[0]["value"] == pytest.approx(1.5)

    def test_returns_empty_on_exception(self):
        with patch("akshare.stock_index_pb_lg", side_effect=Exception("network error")):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_funddb
            assert fetch_cn_index_funddb("沪深300", "市净率") == []

    def test_returns_empty_for_empty_dataframe(self):
        with patch("akshare.stock_index_pb_lg", return_value=pd.DataFrame(columns=["日期", "市净率"])):
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_funddb
            assert fetch_cn_index_funddb("沪深300", "市净率") == []

    def test_symbol_passed_to_stock_index_pb_lg(self):
        df = self._pb_df(5)
        with patch("akshare.stock_index_pb_lg", return_value=df) as mock_fn:
            from src.services.valuation.fetchers.akshare_index_pe import fetch_cn_index_funddb
            fetch_cn_index_funddb("中证500", "市净率")
            mock_fn.assert_called_once_with(symbol="中证500")


# ══════════════════════════════════════════════════════════════════
# fmp — fetch_fmp_us_history (P0-A) + quota tracker (P0-C)
# ══════════════════════════════════════════════════════════════════

class TestFetchFmpUsHistory:
    def _fmp_payload(self, n: int = 10) -> list[dict]:
        return [
            {
                "date": f"202{i // 4}-Q{(i % 4) + 1}-01",
                "peRatio": 20.0 + i,
                "pbRatio": 3.0 + i * 0.1,
                "priceToSalesRatio": 5.0 + i * 0.2,
                "enterpriseValueOverEBITDA": 15.0 + i,
                "dividendYield": 1.5 + i * 0.05,
            }
            for i in range(n)
        ]

    def _mock_resp(self, payload, status=200):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = payload
        return r

    def test_returns_dict_with_pe_and_pb_keys(self):
        # fmp.py now uses http_get from src.utils.http_client, not requests.get directly.
        with patch.dict("os.environ", {"FMP_API_KEY": "testkey"}), \
             patch("src.services.valuation.fetchers.fmp.http_get",
                   return_value=self._mock_resp(self._fmp_payload(8))):
            from src.services.valuation.fetchers import fmp as fmp_mod
            fmp_mod._FMP_QUOTA.clear()
            fmp_mod._fmp_403_warned = False  # reset per-process warning flag
            result = fmp_mod.fetch_fmp_us_history("AAPL")
            assert "pe_ttm" in result
            assert "pb_ratio" in result
            assert len(result["pe_ttm"]) == 8

    def test_each_point_has_date_and_value(self):
        with patch.dict("os.environ", {"FMP_API_KEY": "testkey"}), \
             patch("src.services.valuation.fetchers.fmp.http_get",
                   return_value=self._mock_resp(self._fmp_payload(5))):
            from src.services.valuation.fetchers import fmp as fmp_mod
            fmp_mod._FMP_QUOTA.clear()
            fmp_mod._fmp_403_warned = False
            result = fmp_mod.fetch_fmp_us_history("MSFT")
            assert all("date" in p and "value" in p for p in result["pe_ttm"])

    def test_returns_empty_when_no_api_key(self):
        with patch.dict("os.environ", {"FMP_API_KEY": ""}):
            from src.services.valuation.fetchers import fmp as fmp_mod
            fmp_mod._FMP_QUOTA.clear()
            result = fmp_mod.fetch_fmp_us_history("AAPL")
            assert result == {}

    def test_returns_empty_on_403(self):
        with patch.dict("os.environ", {"FMP_API_KEY": "testkey"}), \
             patch("src.services.valuation.fetchers.fmp.http_get",
                   return_value=self._mock_resp(None, status=403)):
            from src.services.valuation.fetchers import fmp as fmp_mod
            fmp_mod._FMP_QUOTA.clear()
            fmp_mod._fmp_403_warned = False
            assert fmp_mod.fetch_fmp_us_history("AAPL") == {}

    def test_returns_empty_on_network_error(self):
        with patch.dict("os.environ", {"FMP_API_KEY": "testkey"}), \
             patch("src.services.valuation.fetchers.fmp.http_get",
                   side_effect=Exception("timeout")):
            from src.services.valuation.fetchers import fmp as fmp_mod
            fmp_mod._FMP_QUOTA.clear()
            assert fmp_mod.fetch_fmp_us_history("AAPL") == {}


class TestFmpQuotaTracker:
    def test_allows_calls_under_limit(self):
        from src.services.valuation.fetchers import fmp as fmp_mod
        fmp_mod._FMP_QUOTA.clear()
        assert fmp_mod._check_fmp_quota() is True

    def test_blocks_at_daily_limit(self):
        from src.services.valuation.fetchers import fmp as fmp_mod
        fmp_mod._FMP_QUOTA.clear()
        from datetime import date as _date
        today = _date.today().isoformat()
        fmp_mod._FMP_QUOTA[today] = fmp_mod.FMP_DAILY_LIMIT
        assert fmp_mod._check_fmp_quota() is False

    def test_quota_blocked_returns_empty_from_history(self):
        with patch.dict("os.environ", {"FMP_API_KEY": "testkey"}):
            from src.services.valuation.fetchers import fmp as fmp_mod
            from datetime import date as _date
            today = _date.today().isoformat()
            fmp_mod._FMP_QUOTA[today] = fmp_mod.FMP_DAILY_LIMIT
            result = fmp_mod.fetch_fmp_us_history("AAPL")
            fmp_mod._FMP_QUOTA.clear()
            assert result == {}

    def test_counter_increments_per_call(self):
        from src.services.valuation.fetchers import fmp as fmp_mod
        fmp_mod._FMP_QUOTA.clear()
        from datetime import date as _date
        today = _date.today().isoformat()
        fmp_mod._check_fmp_quota()
        fmp_mod._check_fmp_quota()
        assert fmp_mod._FMP_QUOTA.get(today, 0) == 2
