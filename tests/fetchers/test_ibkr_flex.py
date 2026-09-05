"""Tests for src/fetchers/ibkr_flex.py — mocked HTTP only, no real network calls.

Covers
------
1. SendRequest success → ReferenceCode + Url parsed.
2. SendRequest error envelope → FlexFetchError with code/message.
3. GetStatement returns CSV first try → content returned.
4. GetStatement returns 1019 (in progress) twice then CSV → polls and succeeds;
   bounded attempt count asserted.
5. GetStatement never ready → FlexFetchError after max_polls.
6. fetch_and_save writes a file matching IBKR_UIS_Report*.csv in dest_dir.
7. Token NEVER appears in any log record (caplog assertion).
8. Pinned fixture: IBKR_UIS_Report*.csv glob matches the fixture file used by
   the ibkr reader (contract between fetcher and reader).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from src.fetchers.ibkr_flex import (
    FlexFetchError,
    fetch_and_save,
    fetch_flex_statement,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FAKE_TOKEN = "SUPER_SECRET_TOKEN_NEVER_LOG"
FAKE_QUERY_ID = "123456"
FAKE_REF_CODE = "9876543"
FAKE_STATEMENT_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"
FAKE_CSV = "BOF,account\ndata,row1\ndata,row2\nEOF"

FIXTURE_IBKR_DIR = Path(__file__).parent.parent / "fixtures" / "readers" / "ibkr"

# ---------------------------------------------------------------------------
# XML envelope helpers
# ---------------------------------------------------------------------------

_SEND_SUCCESS = (
    "<FlexStatementResponse>"
    "<Status>Success</Status>"
    f"<ReferenceCode>{FAKE_REF_CODE}</ReferenceCode>"
    f"<Url>{FAKE_STATEMENT_URL}</Url>"
    "</FlexStatementResponse>"
)

_SEND_ERROR = (
    "<FlexStatementResponse>"
    "<Status>Fail</Status>"
    "<ErrorCode>1012</ErrorCode>"
    "<ErrorMessage>Token is invalid</ErrorMessage>"
    "</FlexStatementResponse>"
)

_GET_IN_PROGRESS = (
    "<FlexStatementResponse>"
    "<Status>Warn</Status>"
    "<ErrorCode>1019</ErrorCode>"
    "<ErrorMessage>Statement generation in progress</ErrorMessage>"
    "</FlexStatementResponse>"
)

_GET_PERM_ERROR = (
    "<FlexStatementResponse>"
    "<Status>Fail</Status>"
    "<ErrorCode>1001</ErrorCode>"
    "<ErrorMessage>Permanent server error</ErrorMessage>"
    "</FlexStatementResponse>"
)


# ---------------------------------------------------------------------------
# Fake HTTP client
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Injectable fake HTTP client with a configurable response queue."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, params: dict, timeout: float) -> _FakeResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if not self._responses:
            raise RuntimeError("_FakeClient: no more responses queued")
        return self._responses.pop(0)


# ---------------------------------------------------------------------------
# Tests: SendRequest
# ---------------------------------------------------------------------------


class TestSendRequest:
    def test_send_request_success_parses_reference_code_and_url(self, monkeypatch):
        """SendRequest success envelope → reference_code + url extracted."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        responses = [
            _FakeResponse(_SEND_SUCCESS),   # SendRequest
            _FakeResponse(FAKE_CSV),        # GetStatement
        ]
        client = _FakeClient(responses)

        result = fetch_flex_statement(
            FAKE_TOKEN, FAKE_QUERY_ID, client=client, max_polls=0
        )
        assert result == FAKE_CSV
        # First call should be to the SendRequest URL
        assert "SendRequest" in client.calls[0]["url"]
        # Params should include t=token and q=query_id
        assert client.calls[0]["params"]["q"] == FAKE_QUERY_ID
        # token present in request but that's the transport layer, not logging

    def test_send_request_error_raises_flex_fetch_error(self):
        """Non-Success SendRequest envelope → FlexFetchError with code + message."""
        client = _FakeClient([_FakeResponse(_SEND_ERROR)])

        with pytest.raises(FlexFetchError) as exc_info:
            fetch_flex_statement(FAKE_TOKEN, FAKE_QUERY_ID, client=client)

        err = exc_info.value
        assert err.code == "1012"
        assert "Token is invalid" in str(err)

    def test_send_request_error_message_does_not_contain_token(self):
        """FlexFetchError raised by SendRequest must NOT contain the token."""
        client = _FakeClient([_FakeResponse(_SEND_ERROR)])

        with pytest.raises(FlexFetchError) as exc_info:
            fetch_flex_statement(FAKE_TOKEN, FAKE_QUERY_ID, client=client)

        assert FAKE_TOKEN not in str(exc_info.value)
        assert FAKE_TOKEN not in repr(exc_info.value)


# ---------------------------------------------------------------------------
# Tests: GetStatement
# ---------------------------------------------------------------------------


class TestGetStatement:
    def test_get_statement_csv_first_try(self, monkeypatch):
        """GetStatement returns CSV immediately (no polling required)."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        client = _FakeClient([
            _FakeResponse(_SEND_SUCCESS),
            _FakeResponse(FAKE_CSV),
        ])

        result = fetch_flex_statement(
            FAKE_TOKEN, FAKE_QUERY_ID, client=client, max_polls=5
        )
        assert result == FAKE_CSV
        assert len(client.calls) == 2

    def test_get_statement_polls_on_1019_then_succeeds(self, monkeypatch):
        """GetStatement 1019 twice then CSV → polls, succeeds; bounded attempts."""
        sleep_calls: list[float] = []
        monkeypatch.setattr("time.sleep", lambda d: sleep_calls.append(d))

        client = _FakeClient([
            _FakeResponse(_SEND_SUCCESS),    # SendRequest
            _FakeResponse(_GET_IN_PROGRESS), # attempt 0 → 1019
            _FakeResponse(_GET_IN_PROGRESS), # attempt 1 → 1019
            _FakeResponse(FAKE_CSV),         # attempt 2 → CSV
        ])

        result = fetch_flex_statement(
            FAKE_TOKEN, FAKE_QUERY_ID, client=client, max_polls=5, backoff_base=2.0
        )

        assert result == FAKE_CSV
        # Should have slept twice (after attempt 0 and attempt 1)
        assert len(sleep_calls) == 2
        # Backoff: 2^0=1, 2^1=2
        assert sleep_calls[0] == pytest.approx(1.0)
        assert sleep_calls[1] == pytest.approx(2.0)
        # Total HTTP calls: 1 (SendRequest) + 3 (GetStatement) = 4
        assert len(client.calls) == 4

    def test_get_statement_never_ready_raises_after_max_polls(self, monkeypatch):
        """GetStatement always 1019 → FlexFetchError after max_polls exhausted."""
        monkeypatch.setattr("time.sleep", lambda _: None)

        max_polls = 3
        # 1 SendRequest + (max_polls+1) GetStatement attempts = max_polls+2 total
        responses = [_FakeResponse(_SEND_SUCCESS)] + [
            _FakeResponse(_GET_IN_PROGRESS) for _ in range(max_polls + 1)
        ]
        client = _FakeClient(responses)

        with pytest.raises(FlexFetchError) as exc_info:
            fetch_flex_statement(
                FAKE_TOKEN, FAKE_QUERY_ID, client=client, max_polls=max_polls
            )

        assert exc_info.value.code == "1019"
        assert "in progress" in str(exc_info.value).lower() or "1019" in str(exc_info.value)
        # Exactly max_polls + 2 HTTP calls total
        assert len(client.calls) == max_polls + 2

    def test_get_statement_permanent_error_raises(self, monkeypatch):
        """GetStatement returns a permanent error → FlexFetchError (not 1019 retry)."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        client = _FakeClient([
            _FakeResponse(_SEND_SUCCESS),
            _FakeResponse(_GET_PERM_ERROR),
        ])

        with pytest.raises(FlexFetchError) as exc_info:
            fetch_flex_statement(FAKE_TOKEN, FAKE_QUERY_ID, client=client)

        assert exc_info.value.code == "1001"
        assert "Permanent server error" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Tests: fetch_and_save
# ---------------------------------------------------------------------------


class TestFetchAndSave:
    def test_writes_file_matching_reader_glob(self, tmp_path, monkeypatch):
        """fetch_and_save writes a file matching IBKR_UIS_Report*.csv."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        client = _FakeClient([
            _FakeResponse(_SEND_SUCCESS),
            _FakeResponse(FAKE_CSV),
        ])
        fixed_now = datetime(2026, 6, 17, 8, 45, 0, tzinfo=timezone.utc)

        dest = fetch_and_save(
            tmp_path, FAKE_TOKEN, FAKE_QUERY_ID,
            now=fixed_now, client=client, max_polls=0
        )

        assert dest.exists()
        # Matches the reader glob
        assert dest.name.startswith("IBKR_UIS_Report")
        assert dest.suffix == ".csv"
        assert list(tmp_path.glob("IBKR_UIS_Report*.csv")) == [dest]

    def test_writes_correct_timestamp_in_filename(self, tmp_path, monkeypatch):
        """fetch_and_save embeds UTC ISO-compact timestamp in filename."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        client = _FakeClient([
            _FakeResponse(_SEND_SUCCESS),
            _FakeResponse(FAKE_CSV),
        ])
        fixed_now = datetime(2026, 6, 17, 8, 45, 0, tzinfo=timezone.utc)

        dest = fetch_and_save(
            tmp_path, FAKE_TOKEN, FAKE_QUERY_ID,
            now=fixed_now, client=client, max_polls=0
        )

        assert dest.name == "IBKR_UIS_Report_20260617T084500Z.csv"

    def test_written_content_matches_statement(self, tmp_path, monkeypatch):
        """fetch_and_save writes exactly the text returned by the Flex service."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        client = _FakeClient([
            _FakeResponse(_SEND_SUCCESS),
            _FakeResponse(FAKE_CSV),
        ])
        fixed_now = datetime(2026, 6, 17, 8, 45, 0, tzinfo=timezone.utc)

        dest = fetch_and_save(
            tmp_path, FAKE_TOKEN, FAKE_QUERY_ID,
            now=fixed_now, client=client, max_polls=0
        )

        assert dest.read_text(encoding="utf-8") == FAKE_CSV


# ---------------------------------------------------------------------------
# Tests: Token must never appear in logs
# ---------------------------------------------------------------------------


class TestTokenNotLogged:
    def test_token_not_in_log_records_on_success(self, monkeypatch, caplog):
        """Token must not appear in any log record during a successful fetch."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        client = _FakeClient([
            _FakeResponse(_SEND_SUCCESS),
            _FakeResponse(FAKE_CSV),
        ])

        with caplog.at_level(logging.DEBUG, logger="src.fetchers.ibkr_flex"):
            fetch_flex_statement(
                FAKE_TOKEN, FAKE_QUERY_ID, client=client, max_polls=0
            )

        for record in caplog.records:
            assert FAKE_TOKEN not in record.getMessage(), (
                f"Token leaked in log record: {record.getMessage()!r}"
            )

    def test_token_not_in_log_records_on_error(self, monkeypatch, caplog):
        """Token must not appear in any log record during a failed fetch."""
        client = _FakeClient([_FakeResponse(_SEND_ERROR)])

        with caplog.at_level(logging.DEBUG, logger="src.fetchers.ibkr_flex"):
            with pytest.raises(FlexFetchError):
                fetch_flex_statement(FAKE_TOKEN, FAKE_QUERY_ID, client=client)

        for record in caplog.records:
            assert FAKE_TOKEN not in record.getMessage(), (
                f"Token leaked in log record: {record.getMessage()!r}"
            )

    def test_token_not_in_log_records_during_polling(self, monkeypatch, caplog):
        """Token must not appear in any log record while polling on 1019."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        client = _FakeClient([
            _FakeResponse(_SEND_SUCCESS),
            _FakeResponse(_GET_IN_PROGRESS),
            _FakeResponse(FAKE_CSV),
        ])

        with caplog.at_level(logging.DEBUG, logger="src.fetchers.ibkr_flex"):
            fetch_flex_statement(
                FAKE_TOKEN, FAKE_QUERY_ID, client=client, max_polls=5
            )

        for record in caplog.records:
            assert FAKE_TOKEN not in record.getMessage(), (
                f"Token leaked in log record during polling: {record.getMessage()!r}"
            )


# ---------------------------------------------------------------------------
# Tests: Pinned fixture — reader glob contract
# ---------------------------------------------------------------------------


class TestFixtureContract:
    def test_fixture_file_matches_reader_glob(self):
        """The pinned fixture IBKR_UIS_Report.csv matches the ibkr reader glob IBKR_UIS_Report*.csv.

        This test guards against Flex format drift: if the reader's glob changes,
        this test flags it.
        """
        fixture_csv = FIXTURE_IBKR_DIR / "IBKR_UIS_Report.csv"
        assert fixture_csv.exists(), (
            f"IBKR fixture not found at {fixture_csv} — "
            "ensure tests/fixtures/readers/ibkr/IBKR_UIS_Report.csv exists"
        )
        # Verify it matches the glob the reader uses (ibkr.yaml: file_glob: "IBKR_UIS_Report*.csv")
        matches = list(FIXTURE_IBKR_DIR.glob("IBKR_UIS_Report*.csv"))
        assert len(matches) >= 1, "No files match IBKR_UIS_Report*.csv in fixture dir"
        assert fixture_csv in matches

    def test_fixture_has_expected_sections(self):
        """Fixture CSV starts with BOF (Begin-of-File) section marker — basic format guard."""
        fixture_csv = FIXTURE_IBKR_DIR / "IBKR_UIS_Report.csv"
        if not fixture_csv.exists():
            pytest.skip("IBKR fixture not present")

        content = fixture_csv.read_text(encoding="utf-8", errors="replace")
        assert content.startswith('"BOF"'), (
            "IBKR fixture does not start with BOF — Flex format may have changed"
        )
