"""Tests for Baidu Finance HK ETF PE/PB history fetcher."""
from unittest.mock import patch, MagicMock


_SAMPLE_API_BODY = [
    ["2020-07-10", "73.37"],
    ["2020-07-14", "68.76"],
    ["2021-01-04", "50.12"],
    ["2026-04-22", "45.97"],
]

_SAMPLE_PB_BODY = [
    ["2020-07-10", "2.50"],
    ["2020-07-14", "2.40"],
    ["2021-01-04", "2.10"],
    ["2026-04-22", "2.37"],
]

_INVALID_BODY = [
    ["2026-04-22", "45.97"],
    ["bad-date", "10.00"],
    ["2026-04-21", "not-a-number"],
    ["2026-04-20", "0.001"],   # below _PE_BOUNDS lower limit
    ["2026-04-19", "9999.99"], # above _PE_BOUNDS upper limit
]


def _mock_baidu_response(body: list) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "Result": [{
            "DisplayData": {
                "resultData": {
                    "tplData": {
                        "result": {
                            "chartInfo": [{"body": body}]
                        }
                    }
                }
            }
        }]
    }
    resp.raise_for_status = MagicMock()
    return resp


def test_pe_history_returns_sorted_list():
    from src.services.valuation.fetchers.hk_baidu import fetch_hk_index_pe_history
    with patch("src.utils.http_client.http_get", return_value=_mock_baidu_response(_SAMPLE_API_BODY)):
        result = fetch_hk_index_pe_history("06969")
    assert len(result) == 4
    assert result[0]["date"] == "2020-07-10"
    assert result[-1]["date"] == "2026-04-22"
    assert result[-1]["pe_ttm"] == 45.97


def test_pe_history_filters_bad_rows():
    from src.services.valuation.fetchers.hk_baidu import fetch_hk_index_pe_history
    with patch("src.utils.http_client.http_get", return_value=_mock_baidu_response(_INVALID_BODY)):
        result = fetch_hk_index_pe_history("06969")
    assert len(result) == 1
    assert result[0]["value" if "value" in result[0] else "pe_ttm"] is not None


def test_pe_history_returns_empty_on_request_error():
    from src.services.valuation.fetchers.hk_baidu import fetch_hk_index_pe_history
    with patch("src.utils.http_client.http_get", side_effect=Exception("network error")):
        result = fetch_hk_index_pe_history("06969")
    assert result == []


def test_pb_history_returns_sorted_list():
    from src.services.valuation.fetchers.hk_baidu import fetch_hk_index_pb_history
    with patch("src.utils.http_client.http_get", return_value=_mock_baidu_response(_SAMPLE_PB_BODY)):
        result = fetch_hk_index_pb_history("06969")
    assert len(result) == 4
    assert result[0]["date"] == "2020-07-10"
    assert result[-1]["date"] == "2026-04-22"
    assert result[-1]["value"] == 2.37


def test_pb_history_returns_empty_on_request_error():
    from src.services.valuation.fetchers.hk_baidu import fetch_hk_index_pb_history
    with patch("src.utils.http_client.http_get", side_effect=Exception("timeout")):
        result = fetch_hk_index_pb_history("06969")
    assert result == []
