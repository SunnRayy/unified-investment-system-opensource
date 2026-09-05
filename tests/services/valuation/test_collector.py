"""Collector refactor tests — TDD for Task 4 (3 row kinds + valuation_history)."""
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

_OLD_OLDEST_DATE = date.today() - timedelta(days=365 * 5)  # 5-year-old history → no backfill needed


@pytest.fixture(autouse=True)
def _zero_akshare_throttle(monkeypatch):
    """Block all real network calls and zero the AKShare throttle for every test.

    refresh_all() unconditionally iterates FUND_TO_INDEX_MAP (Phase B),
    HK_ETF_PROXY_MAP (Phase B), US_INDEX_MAP (Phase C), and the watchlist
    (Phase D) regardless of what a test's own patches cover.  Without this
    fixture those phases make live HTTP calls to AKShare / yfinance, adding
    35-40 s per async test.

    Individual tests that need a specific fetcher to return real data can
    still wrap their own ``with patch(..., return_value=X)`` block — the
    context-manager patch wins inside its scope and the autouse mock is
    automatically restored on exit.

    The real AKSHARE_INDEX_THROTTLE_SECONDS sleep (2 s × 2 fires per call)
    is also zeroed here; production behaviour is unchanged.
    """
    import src.services.valuation.collector as _mod

    # ── Throttle constant ──────────────────────────────────────────────────
    monkeypatch.setattr(_mod, "AKSHARE_INDEX_THROTTLE_SECONDS", 0.0)

    # ── Phase B: CN index (FUND_TO_INDEX_MAP) ─────────────────────────────
    monkeypatch.setattr(_mod, "fetch_cn_index_snapshot", lambda *a, **kw: None)
    monkeypatch.setattr(_mod, "fetch_cn_market_snapshot", lambda *a, **kw: None)
    monkeypatch.setattr(_mod, "fetch_cn_index_funddb", lambda *a, **kw: [])
    monkeypatch.setattr(_mod, "fetch_cn_index_history", lambda *a, **kw: [])
    monkeypatch.setattr(_mod, "fetch_cn_market_history", lambda *a, **kw: [])

    # ── Phase B: HK ETF proxy (HK_ETF_PROXY_MAP) ──────────────────────────
    monkeypatch.setattr(_mod, "fetch_hk_index_snapshot", lambda *a, **kw: None)
    monkeypatch.setattr(_mod, "fetch_hk_index_pe_history", lambda *a, **kw: [])
    monkeypatch.setattr(_mod, "fetch_hk_index_pb_history", lambda *a, **kw: [])

    # ── Phase C: US broad index (US_INDEX_MAP) ─────────────────────────────
    monkeypatch.setattr(_mod, "fetch_yfinance_us_stock", lambda *a, **kw: {})
    monkeypatch.setattr(_mod, "fetch_multpl_sp500_pe_history", lambda *a, **kw: [])
    monkeypatch.setattr(_mod, "fetch_multpl_nasdaq100_pe_history", lambda *a, **kw: [])

    # ── Phase D: watchlist ─────────────────────────────────────────────────
    monkeypatch.setattr(_mod, "fetch_us_index_snapshot", lambda *a, **kw: None)
    monkeypatch.setattr(_mod, "fetch_fmp_us_stock", lambda *a, **kw: {})
    monkeypatch.setattr(_mod, "fetch_fmp_us_history", lambda *a, **kw: [])
    monkeypatch.setattr(_mod, "fetch_yfinance_etf_yield", lambda *a, **kw: None)


def _make_db(history_count=0, watchlist_rows=None, oldest_date=None):
    db = MagicMock()
    watchlist_rows = watchlist_rows or []
    # When history_count > 0, default oldest date to 5 years ago (well beyond 30-day threshold)
    effective_oldest = oldest_date if oldest_date is not None else (
        _OLD_OLDEST_DATE if history_count > 0 else None
    )

    def execute_side_effect(sql, params=None):
        result = MagicMock()
        result.fetchone.return_value = None
        result.fetchall.return_value = []
        sql_lower = sql.lower().strip()

        if "count(*)" in sql_lower and "valuation_history" in sql_lower:
            result.fetchone.return_value = (history_count, effective_oldest)
        elif "from valuation_watchlist" in sql_lower:
            result.fetchall.return_value = watchlist_rows
        elif "from market_sentiment_cache" in sql_lower:
            result.fetchone.return_value = None
        elif "from valuation_reference" in sql_lower or "get_all_references" in sql_lower:
            result.fetchall.return_value = []

        return result

    db.execute.side_effect = execute_side_effect
    return db


# ══════════════════════════════════════════════════════════════════
# _needs_history_backfill helper
# ══════════════════════════════════════════════════════════════════

class TestNeedsHistoryBackfill:
    def test_returns_true_when_history_empty(self):
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(_make_db(history_count=0))
        assert collector._needs_history_backfill("沪深300", "pe_ttm") is True

    def test_returns_false_when_history_populated_with_old_data(self):
        from src.services.valuation.collector import ValuationCollector
        # 5 years of history — oldest date is well beyond 30-day threshold
        collector = ValuationCollector(_make_db(history_count=1800, oldest_date=_OLD_OLDEST_DATE))
        assert collector._needs_history_backfill("沪深300", "pe_ttm") is False

    def test_returns_true_when_only_recent_data(self):
        from src.services.valuation.collector import ValuationCollector
        # Only today's data (1 row, oldest = today) — needs backfill despite count > 0
        collector = ValuationCollector(_make_db(history_count=1, oldest_date=date.today()))
        assert collector._needs_history_backfill("沪深300", "pe_ttm") is True


# ══════════════════════════════════════════════════════════════════
# _bulk_insert_history helper
# ══════════════════════════════════════════════════════════════════

class TestBulkInsertHistory:
    def test_calls_insert_for_each_history_point(self):
        db = MagicMock()
        db.execute.return_value = MagicMock()
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(db)

        history = [
            {"date": "2024-01-01", "pe_ttm": 12.0},
            {"date": "2024-01-02", "pe_ttm": 12.5},
            {"date": "2024-01-03", "pe_ttm": 13.0},
        ]
        count = collector._bulk_insert_history("沪深300", "pe_ttm", history, "akshare_index_pe")
        assert count == 3
        assert db.execute.call_count == 3

    def test_returns_zero_on_empty_history(self):
        db = MagicMock()
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(db)
        count = collector._bulk_insert_history("沪深300", "pe_ttm", [], "akshare_index_pe")
        assert count == 0


# ══════════════════════════════════════════════════════════════════
# _upsert_history helper
# ══════════════════════════════════════════════════════════════════

class TestUpsertHistory:
    def test_calls_db_execute_with_upsert_sql(self):
        db = MagicMock()
        db.execute.return_value = MagicMock()
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(db)

        collector._upsert_history("沪深300", "pe_ttm", "2024-01-10", 13.5, "akshare_index_pe")
        db.execute.assert_called_once()
        sql = db.execute.call_args[0][0]
        assert "valuation_history" in sql.lower()


# ══════════════════════════════════════════════════════════════════
# _get_history_percentile helper
# ══════════════════════════════════════════════════════════════════

class TestGetHistoryPercentile:
    def test_returns_none_tuple_when_no_history(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(db)
        pct, yrs = collector._get_history_percentile("沪深300", "pe_ttm", 13.0)
        assert pct is None
        assert yrs == 0

    def test_returns_percentile_when_history_available(self):
        db = MagicMock()
        # Data within the last 10-year window
        base = date.today() - timedelta(days=9 * 365)
        rows = [(10.0 + i, base + timedelta(days=i * 365 // 30)) for i in range(30)]
        db.execute.return_value.fetchall.return_value = rows
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(db)
        pct, yrs = collector._get_history_percentile("沪深300", "pe_ttm", 20.0)
        assert pct is not None

    def test_default_uses_10yr_cutoff_in_sql(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(db)
        collector._get_history_percentile("沪深300", "pe_ttm", 13.0)
        sql, params = db.execute.call_args[0]
        assert "observed_date >= ?" in sql
        expected_cutoff = (date.today() - timedelta(days=10 * 365)).isoformat()
        assert params[2] == expected_cutoff

    def test_years_zero_fetches_full_history(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(db)
        collector._get_history_percentile("沪深300", "pe_ttm", 13.0, years=0)
        sql, params = db.execute.call_args[0]
        assert "observed_date >= ?" not in sql
        assert len(params) == 2


# ══════════════════════════════════════════════════════════════════
# refresh_all — holding rows
# ══════════════════════════════════════════════════════════════════

class TestRefreshAllHoldingRows:
    @pytest.mark.asyncio
    async def test_holding_rows_written_for_active_holdings(self):
        db = _make_db()
        holdings = [
            {"asset_id": "US_STK_MSFT", "display_name": "Microsoft"},
        ]
        with patch("src.services.valuation.collector.fetch_wealthos_active_holdings", return_value=holdings), \
             patch("src.services.valuation.collector.fetch_fmp_us_stock", return_value={"pe_forward": 32.0, "pe_ttm": 35.0, "data_source": "fmp"}), \
             patch("src.services.valuation.collector.get_all_references", return_value=[]), \
             patch("src.services.valuation.collector.ValuationCollector._write_snapshot") as mock_write:
            from src.services.valuation.collector import ValuationCollector, _daily_counts
            _daily_counts.clear()
            collector = ValuationCollector(db)
            await collector.refresh_all()

        # At least one snapshot written
        assert mock_write.call_count >= 1
        # The FIRST snapshot written is the holding row (Phase A runs before Phase B/C)
        _, _, _, _, metrics = mock_write.call_args_list[0][0]
        assert metrics.get("row_kind") == "holding"

    @pytest.mark.asyncio
    async def test_cn_fund_holding_row_written_as_not_estimable(self):
        db = _make_db()
        holdings = [{"asset_id": "CN_FUND_900001", "display_name": "景顺300"}]
        with patch("src.services.valuation.collector.fetch_wealthos_active_holdings", return_value=holdings), \
             patch("src.services.valuation.collector.get_all_references", return_value=[]), \
             patch("src.services.valuation.collector.ValuationCollector._write_snapshot") as mock_write:
            from src.services.valuation.collector import ValuationCollector, _daily_counts
            _daily_counts.clear()
            collector = ValuationCollector(db)
            await collector.refresh_all()

        written_metrics = [c[0][4] for c in mock_write.call_args_list]
        # CN fund holding row should be is_estimable=False (no PE on fund itself)
        holding_rows = [m for m in written_metrics if m.get("row_kind") == "holding"]
        assert any(not m.get("is_estimable", True) for m in holding_rows)


# ══════════════════════════════════════════════════════════════════
# refresh_all — tracked_index rows
# ══════════════════════════════════════════════════════════════════

class TestRefreshAllTrackedIndexRows:
    @pytest.mark.asyncio
    async def test_tracked_index_row_written_for_fund_in_map(self):
        db = _make_db(history_count=100)  # history already populated
        holdings = [{"asset_id": "CN_FUND_900001", "display_name": "景顺300"}]
        snapshot = {"pe_ttm": 13.2, "pb_ratio": 1.4, "data_source": "akshare_index_pe"}

        with patch("src.services.valuation.collector.fetch_wealthos_active_holdings", return_value=holdings), \
             patch("src.services.valuation.collector.fetch_cn_index_snapshot", return_value=snapshot), \
             patch("src.services.valuation.collector.get_all_references", return_value=[]), \
             patch("src.services.valuation.collector.ValuationCollector._write_snapshot") as mock_write:
            from src.services.valuation.collector import ValuationCollector, _daily_counts
            _daily_counts.clear()
            collector = ValuationCollector(db)
            await collector.refresh_all()

        written_metrics = [c[0][4] for c in mock_write.call_args_list]
        tracked = [m for m in written_metrics if m.get("row_kind") == "tracked_index"]
        assert len(tracked) >= 1

    @pytest.mark.asyncio
    async def test_tracked_index_ticker_is_index_name(self):
        db = _make_db(history_count=100)
        holdings = [{"asset_id": "CN_FUND_900001", "display_name": "景顺300"}]
        snapshot = {"pe_ttm": 13.2, "pb_ratio": 1.4, "data_source": "akshare_index_pe"}

        with patch("src.services.valuation.collector.fetch_wealthos_active_holdings", return_value=holdings), \
             patch("src.services.valuation.collector.fetch_cn_index_snapshot", return_value=snapshot), \
             patch("src.services.valuation.collector.get_all_references", return_value=[]), \
             patch("src.services.valuation.collector.ValuationCollector._write_snapshot") as mock_write:
            from src.services.valuation.collector import ValuationCollector, _daily_counts
            _daily_counts.clear()
            collector = ValuationCollector(db)
            await collector.refresh_all()

        # The tracked_index snapshot ticker should be the index name, not the fund code
        tickers = [c[0][1] for c in mock_write.call_args_list]  # arg[1] = ticker
        assert "沪深300" in tickers

    @pytest.mark.asyncio
    async def test_tracked_index_bulk_inserts_history_on_first_run(self):
        db = _make_db(history_count=0)  # no history yet
        holdings = [{"asset_id": "CN_FUND_900001", "display_name": "景顺300"}]
        snapshot = {"pe_ttm": 13.2, "pb_ratio": 1.4, "data_source": "akshare_index_pe"}
        fake_history = [{"date": f"2024-{i:02d}-01", "pe_ttm": 12.0 + i} for i in range(1, 16)]

        with patch("src.services.valuation.collector.fetch_wealthos_active_holdings", return_value=holdings), \
             patch("src.services.valuation.collector.fetch_cn_index_snapshot", return_value=snapshot), \
             patch("src.services.valuation.collector.fetch_cn_index_history", return_value=fake_history) as mock_hist, \
             patch("src.services.valuation.collector.get_all_references", return_value=[]), \
             patch("src.services.valuation.collector.ValuationCollector._write_snapshot"):
            from src.services.valuation.collector import ValuationCollector, _daily_counts
            _daily_counts.clear()
            collector = ValuationCollector(db)
            await collector.refresh_all()

        # All 3 FUND_TO_INDEX_MAP entries trigger backfill when history_count=0
        mock_hist.assert_any_call("沪深300")

    @pytest.mark.asyncio
    async def test_tracked_index_skips_history_backfill_when_already_populated(self):
        db = _make_db(history_count=3000)  # already has history
        holdings = [{"asset_id": "CN_FUND_900001", "display_name": "景顺300"}]
        snapshot = {"pe_ttm": 13.2, "pb_ratio": 1.4, "data_source": "akshare_index_pe"}

        with patch("src.services.valuation.collector.fetch_wealthos_active_holdings", return_value=holdings), \
             patch("src.services.valuation.collector.fetch_cn_index_snapshot", return_value=snapshot), \
             patch("src.services.valuation.collector.fetch_cn_index_history") as mock_hist, \
             patch("src.services.valuation.collector.get_all_references", return_value=[]), \
             patch("src.services.valuation.collector.ValuationCollector._write_snapshot"):
            from src.services.valuation.collector import ValuationCollector, _daily_counts
            _daily_counts.clear()
            collector = ValuationCollector(db)
            await collector.refresh_all()

        mock_hist.assert_not_called()


# ══════════════════════════════════════════════════════════════════
# refresh_all — watchlist rows
# ══════════════════════════════════════════════════════════════════

class TestRefreshAllWatchlistRows:
    @pytest.mark.asyncio
    async def test_watchlist_rows_written_for_each_watchlist_item(self):
        watchlist = [("QQQ", "Nasdaq 100", "US_INDEX", None)]
        db = _make_db(watchlist_rows=watchlist)
        snapshot = {"pe_ttm": 35.0, "dividend_yield": 0.7, "data_source": "yfinance_index_proxy"}

        with patch("src.services.valuation.collector.fetch_wealthos_active_holdings", return_value=[]), \
             patch("src.services.valuation.collector.fetch_us_index_snapshot", return_value=snapshot), \
             patch("src.services.valuation.collector.get_all_references", return_value=[]), \
             patch("src.services.valuation.collector.ValuationCollector._write_snapshot") as mock_write:
            from src.services.valuation.collector import ValuationCollector, _daily_counts
            _daily_counts.clear()
            collector = ValuationCollector(db)
            await collector.refresh_all()

        written_metrics = [c[0][4] for c in mock_write.call_args_list]
        watchlist_rows = [m for m in written_metrics if m.get("row_kind") == "watchlist"]
        assert len(watchlist_rows) >= 1

    @pytest.mark.asyncio
    async def test_watchlist_row_ticker_matches_watchlist_ticker(self):
        watchlist = [("QQQ", "Nasdaq 100", "US_INDEX", None)]
        db = _make_db(watchlist_rows=watchlist)
        snapshot = {"pe_ttm": 35.0, "data_source": "yfinance_index_proxy"}

        with patch("src.services.valuation.collector.fetch_wealthos_active_holdings", return_value=[]), \
             patch("src.services.valuation.collector.fetch_us_index_snapshot", return_value=snapshot), \
             patch("src.services.valuation.collector.get_all_references", return_value=[]), \
             patch("src.services.valuation.collector.ValuationCollector._write_snapshot") as mock_write:
            from src.services.valuation.collector import ValuationCollector, _daily_counts
            _daily_counts.clear()
            collector = ValuationCollector(db)
            await collector.refresh_all()

        tickers = [c[0][1] for c in mock_write.call_args_list]
        assert "QQQ" in tickers

    @pytest.mark.asyncio
    async def test_watchlist_hk_index_uses_hk_fetcher(self):
        watchlist = [("3033.HK", "CSOP HSTECH", "HK_INDEX", None)]
        db = _make_db(watchlist_rows=watchlist)
        snapshot = {"pe_ttm": 18.5, "data_source": "yfinance_hk_proxy"}

        with patch("src.services.valuation.collector.fetch_wealthos_active_holdings", return_value=[]), \
             patch("src.services.valuation.collector.fetch_hk_index_snapshot", return_value=snapshot) as mock_hk, \
             patch("src.services.valuation.collector.get_all_references", return_value=[]), \
             patch("src.services.valuation.collector.ValuationCollector._write_snapshot"):
            from src.services.valuation.collector import ValuationCollector, _daily_counts
            _daily_counts.clear()
            collector = ValuationCollector(db)
            await collector.refresh_all()

        # HK_ETF_PROXY_MAP (Phase B) also calls fetch_hk_index_snapshot for 3033.HK
        mock_hk.assert_any_call("3033.HK")


# ══════════════════════════════════════════════════════════════════
# _needs_history_backfill — new count<100 threshold
# ══════════════════════════════════════════════════════════════════

class TestNeedsHistoryBackfillCountThreshold:
    def test_returns_true_when_history_count_less_than_100(self):
        """Sparse history (< 100 rows) with old dates still needs backfill."""
        old_date = date.today() - timedelta(days=365 * 3)
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(_make_db(history_count=50, oldest_date=old_date))
        assert collector._needs_history_backfill("沪深300", "pe_ttm") is True

    def test_returns_false_at_100_rows_with_deep_history(self):
        """Exactly 100+ rows with deep history → no backfill needed."""
        old_date = date.today() - timedelta(days=365 * 5)
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(_make_db(history_count=100, oldest_date=old_date))
        assert collector._needs_history_backfill("沪深300", "pe_ttm") is False


# ══════════════════════════════════════════════════════════════════
# reference key = index name → signal not N/A
# ══════════════════════════════════════════════════════════════════

class TestTrackedIndexSignalWithIndexNameReference:
    @pytest.mark.asyncio
    async def test_signal_classified_when_reference_keyed_to_index_name(self):
        """Signal must not be N/A when valuation_reference row is keyed to index Chinese name."""
        from src.services.valuation.signal import ValuationReference

        db = _make_db(history_count=100)
        holdings = [{"asset_id": "CN_FUND_900001", "display_name": "景顺300"}]
        snapshot = {"pe_ttm": 13.2, "pb_ratio": 1.4, "data_source": "akshare_index_pe"}
        ref = ValuationReference(
            ticker="沪深300", metric="pe_ttm",
            low_threshold=10.0, high_threshold=15.0,
            historical_mean=12.5, rate_sensitive=False,
        )

        with patch("src.services.valuation.collector.fetch_wealthos_active_holdings", return_value=holdings), \
             patch("src.services.valuation.collector.fetch_cn_index_snapshot", return_value=snapshot), \
             patch("src.services.valuation.collector.get_all_references", return_value=[ref]), \
             patch("src.services.valuation.collector.ValuationCollector._write_snapshot") as mock_write:
            from src.services.valuation.collector import ValuationCollector, _daily_counts
            _daily_counts.clear()
            collector = ValuationCollector(db)
            await collector.refresh_all()

        all_calls = [(c[0][1], c[0][4]) for c in mock_write.call_args_list]
        csi300 = [(t, m) for t, m in all_calls if t == "沪深300" and m.get("row_kind") == "tracked_index"]
        assert len(csi300) >= 1, "Expected 沪深300 tracked_index row"
        for _ticker, m in csi300:
            if m.get("pe_ttm") is not None:
                assert m.get("valuation_signal") != "N/A", (
                    f"Expected classified signal for 沪深300, got N/A (signal_basis={m.get('signal_basis')})"
                )
                assert m.get("signal_basis") != "no_reference_config"

    @pytest.mark.asyncio
    async def test_signal_is_na_when_no_reference_row_exists(self):
        """Confirm N/A signal when references list is empty (no config)."""
        db = _make_db(history_count=100)
        holdings = [{"asset_id": "CN_FUND_900001", "display_name": "景顺300"}]
        snapshot = {"pe_ttm": 13.2, "data_source": "akshare_index_pe"}

        with patch("src.services.valuation.collector.fetch_wealthos_active_holdings", return_value=holdings), \
             patch("src.services.valuation.collector.fetch_cn_index_snapshot", return_value=snapshot), \
             patch("src.services.valuation.collector.get_all_references", return_value=[]), \
             patch("src.services.valuation.collector.ValuationCollector._write_snapshot") as mock_write:
            from src.services.valuation.collector import ValuationCollector, _daily_counts
            _daily_counts.clear()
            collector = ValuationCollector(db)
            await collector.refresh_all()

        all_calls = [(c[0][1], c[0][4]) for c in mock_write.call_args_list]
        tracked = [(t, m) for t, m in all_calls if m.get("row_kind") == "tracked_index"]
        assert len(tracked) >= 1
        assert all(m.get("valuation_signal") == "N/A" for _, m in tracked)
        assert all(m.get("signal_basis") == "no_reference_config" for _, m in tracked)


# ══════════════════════════════════════════════════════════════════
# US broad index collection (_collect_tracked_us_index)
# ══════════════════════════════════════════════════════════════════

class TestCollectTrackedUsIndex:
    def test_returns_metrics_with_correct_shape(self):
        db = _make_db(history_count=100)
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(db)
        with patch("src.services.valuation.collector.fetch_yfinance_us_stock",
                   return_value={"pe_ttm": 27.24, "data_source": "yfinance"}):
            result = collector._collect_tracked_us_index("S&P500", "SPY", 1.0, {})
        assert result is not None
        assert result["pe_ttm"] == 27.24
        assert result["asset_class"] == "US_INDEX"
        assert result["linked_ticker"] == "SPY"
        assert result["row_kind"] == "tracked_index"

    def test_returns_none_when_no_pe(self):
        db = _make_db()
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(db)
        with patch("src.services.valuation.collector.fetch_yfinance_us_stock", return_value={}):
            result = collector._collect_tracked_us_index("S&P500", "SPY", 1.0, {})
        assert result is None

    def test_seeds_sp500_history_on_first_run(self):
        db = _make_db(history_count=0)
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(db)
        multpl_data = [{"date": "2020-01-01", "value": 24.0}, {"date": "2021-01-01", "value": 28.0}]
        with patch("src.services.valuation.collector.fetch_yfinance_us_stock",
                   return_value={"pe_ttm": 27.0, "data_source": "yfinance"}), \
             patch("src.services.valuation.collector.fetch_multpl_sp500_pe_history",
                   return_value=multpl_data) as mock_multpl:
            collector._collect_tracked_us_index("S&P500", "SPY", 1.0, {})
        mock_multpl.assert_called_once()

    def test_no_history_seed_for_nasdaq100(self):
        db = _make_db(history_count=0)
        from src.services.valuation.collector import ValuationCollector
        collector = ValuationCollector(db)
        with patch("src.services.valuation.collector.fetch_yfinance_us_stock",
                   return_value={"pe_ttm": 35.0, "data_source": "yfinance"}), \
             patch("src.services.valuation.collector.fetch_multpl_sp500_pe_history") as mock_multpl:
            collector._collect_tracked_us_index("Nasdaq100", "QQQ", 1.0, {})
        mock_multpl.assert_not_called()
