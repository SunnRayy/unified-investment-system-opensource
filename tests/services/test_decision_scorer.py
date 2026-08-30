import pytest

pytestmark = pytest.mark.pipeline

import logging
from pathlib import Path

import duckdb


def test_classify_verdict_and_extract_outcome_pct():
    from src.services.decision_scorer import (
        classify_verdict_from_text,
        compute_outcome_pct_from_text,
    )

    assert classify_verdict_from_text("Sell", "【卖飞】错过后续上涨") == "regret"
    assert classify_verdict_from_text("Buy", "止损成功，验证通过") == "good_call"
    assert classify_verdict_from_text("Buy", "观望后错过买点，未买入") == "missed_opportunity"
    assert classify_verdict_from_text("Buy", "躲过大跌，幸亏没买") == "bullet_dodged"
    assert classify_verdict_from_text("Buy", "未见明显信号") is None

    assert compute_outcome_pct_from_text("+3.11% 达到预期") == 3.11
    assert compute_outcome_pct_from_text("-8.99% 回撤明显") == -8.99
    assert compute_outcome_pct_from_text("暂无结论") is None


def test_compute_outcome_pct_from_prices_with_lookback():
    from src.services.decision_scorer import compute_outcome_pct_from_prices

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute(
        """
        INSERT INTO market_daily (code, date, close, open)
        VALUES
          ('SGOV', '2026-01-09', 100, 100),
          ('SGOV', '2026-02-09', 110, 110)
        """
    )

    # log_date is weekend, function should use nearest prior market day (2026-01-09).
    buy_outcome = compute_outcome_pct_from_prices(conn, 1, "US_ETF_SGOV", "Buy", "2026-01-10")
    sell_outcome = compute_outcome_pct_from_prices(conn, 1, "US_ETF_SGOV", "Sell", "2026-01-10")

    assert round(buy_outcome, 2) == 10.0
    assert round(sell_outcome, 2) == -10.0


def test_match_trades_to_insights_backfills_suggestion_source():
    from src.services.decision_scorer import match_trades_to_insights

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result)
        VALUES ('2026-01-13', 'US_ETF_SGOV', 'Buy', '验证通过')
        """
    )
    conn.execute(
        """
        INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model)
        VALUES ('2026-01-12', 'signal', 'trade', '建议买入 SGOV', 1, 'gemini')
        """
    )

    updated = match_trades_to_insights(conn)
    source = conn.execute("SELECT suggestion_source FROM trade_logs LIMIT 1").fetchone()[0]

    assert updated == 1
    assert source == "gemini"


def test_match_trades_to_insights_upgrades_generic_source_to_memo_with_90d_window():
    from src.services.decision_scorer import match_trades_to_insights

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, suggestion_source, ai_suggestion
        ) VALUES (
            '2026-03-20', 'US_STK_VOO', 'Buy', 'imported',
            '战略备忘录 Memo 009: 梯队1 SPX <= 6500 -> 买入 VOO @ Limit $595.00'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO strategy_memos (
            memo_date, title, strategic_bias, key_directives, source_file
        ) VALUES (
            '2026-03-09',
            '投资战略 Memo：滞胀恐慌下的防御与自动反击',
            'defensive',
            '["SPX <= 6500 时执行买入 VOO"]',
            'memo_009.md'
        )
        """
    )

    updated = match_trades_to_insights(conn)
    source = conn.execute("SELECT suggestion_source FROM trade_logs LIMIT 1").fetchone()[0]

    assert updated == 1
    assert source == "memo"


def test_match_trades_to_insights_matches_memo_from_content_only():
    from src.services.decision_scorer import match_trades_to_insights

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute("ALTER TABLE strategy_memos ADD COLUMN IF NOT EXISTS content TEXT")
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, suggestion_source, ai_suggestion
        ) VALUES (
            '2026-03-20', 'US_STK_VOO', 'Buy', 'imported',
            '执行战略备忘录里的纪律性买入'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO strategy_memos (
            memo_date, title, strategic_bias, key_directives, source_file, content
        ) VALUES (
            '2026-03-09',
            '投资战略 Memo：防御框架更新',
            'defensive',
            '["维持分散，等待更明确的买点"]',
            'memo_010.md',
            '如果 VOO 回撤到目标区间，分批执行买入并保持纪律。'
        )
        """
    )

    updated = match_trades_to_insights(conn)
    source = conn.execute("SELECT suggestion_source FROM trade_logs LIMIT 1").fetchone()[0]

    assert updated == 1
    assert source == "memo"


def test_build_trade_display_scope_sql_includes_manual_imported_and_memo_linked():
    from src.services.decision_scorer import build_trade_display_scope_sql

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)

    linked_tx_id = conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type, amount_gross, source_system
        ) VALUES (
            '2026-03-20', 'US_STK_IBIT', 'IBIT', 'BUY', 1000, 'AIA'
        )
        RETURNING id
        """
    ).fetchone()[0]

    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, suggestion_source)
        VALUES ('2026-03-20', 'US_STK_NVDA', 'Buy', 'manual')
        """
    )
    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, suggestion_source)
        VALUES ('2026-03-20', 'US_STK_SGOV', 'Buy', 'imported')
        """
    )
    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, linked_transaction_id)
        VALUES ('2026-03-20', 'US_STK_IBIT', 'Buy', ?)
        """,
        [linked_tx_id],
    )
    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action)
        VALUES ('2026-03-20', 'US_STK_EMPTY', 'Buy')
        """
    )

    scope = build_trade_display_scope_sql("tl")
    rows = conn.execute(
        f"SELECT asset_id FROM trade_logs tl WHERE {scope} ORDER BY asset_id"
    ).fetchall()
    scoped_assets = {row[0] for row in rows}

    assert "US_STK_NVDA" in scoped_assets
    assert "US_STK_SGOV" in scoped_assets
    assert "US_STK_IBIT" in scoped_assets
    assert "US_STK_EMPTY" not in scoped_assets


def test_build_ai_attribution_scope_sql_excludes_non_attributed_trades():
    from src.services.decision_scorer import build_ai_attribution_scope_sql

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute("ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS linked_memo_id INTEGER")

    memo_id = conn.execute(
        """
        INSERT INTO strategy_memos (
            memo_date, title, strategic_bias, key_directives, source_file
        ) VALUES (
            '2026-03-18', 'VOO defensive ladder', 'defensive',
            '["Buy VOO on drawdown"]', 'memo_999.md'
        )
        RETURNING id
        """
    ).fetchone()[0]
    aia_tx_id = conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type, amount_gross, source_system
        ) VALUES (
            '2026-03-20', 'US_STK_IBIT', 'IBIT', 'BUY', 1000, 'AIA'
        )
        RETURNING id
        """
    ).fetchone()[0]
    schwab_tx_id = conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type, amount_gross, source_system
        ) VALUES (
            '2026-03-20', 'US_STK_SPY', 'SPY', 'BUY', 1200, 'Schwab_CSV'
        )
        RETURNING id
        """
    ).fetchone()[0]

    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, suggestion_source)
        VALUES
          ('2026-03-20', 'US_STK_NVDA', 'Buy', 'manual'),
          ('2026-03-20', 'US_STK_SGOV', 'Buy', 'imported')
        """
    )
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, suggestion_source, linked_memo_id
        ) VALUES (
            '2026-03-20', 'US_STK_VOO', 'Buy', 'manual', ?
        )
        """,
        [memo_id],
    )
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, suggestion_source, linked_transaction_id
        ) VALUES (
            '2026-03-20', 'US_STK_IBIT', 'Buy', 'imported', ?
        )
        """,
        [aia_tx_id],
    )
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, suggestion_source, linked_transaction_id
        ) VALUES (
            '2026-03-20', 'US_STK_SPY', 'Buy', 'imported', ?
        )
        """,
        [schwab_tx_id],
    )

    scope = build_ai_attribution_scope_sql("tl", include_linked_memo=True)
    rows = conn.execute(
        f"SELECT asset_id FROM trade_logs tl WHERE {scope} ORDER BY asset_id"
    ).fetchall()
    attributed_assets = {row[0] for row in rows}

    assert "US_STK_VOO" in attributed_assets
    assert "US_STK_IBIT" not in attributed_assets
    assert "US_STK_NVDA" not in attributed_assets
    assert "US_STK_SGOV" not in attributed_assets
    assert "US_STK_SPY" not in attributed_assets


def test_score_all_trades_updates_verdict_and_outcome():
    from src.services.decision_scorer import score_all_trades

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result, suggestion_source)
        VALUES
          ('2026-01-13', 'US_ETF_SGOV', 'Sell', '【卖飞】错过后续上涨', 'aia'),
          ('2026-01-14', 'US_STK_NVDA', 'Buy', '止损成功，验证通过', 'aia')
        """
    )
    conn.execute(
        """
        INSERT INTO market_daily (code, date, close, open)
        VALUES
          ('SGOV', '2026-01-13', 100, 100),
          ('SGOV', '2026-02-12', 110, 110),
          ('NVDA', '2026-01-14', 200, 200),
          ('NVDA', '2026-02-13', 220, 220)
        """
    )

    scored = score_all_trades(conn)
    rows = conn.execute(
        """
        SELECT asset_id, verdict, outcome_pct
        FROM trade_logs
        ORDER BY log_date
        """
    ).fetchall()

    assert scored == 2
    assert rows[0][1] == "regret"
    assert float(rows[0][2]) == -10.0
    assert rows[1][1] == "good_call"
    assert float(rows[1][2]) == 10.0


def test_score_single_trade_scopes_matching_and_updates_to_requested_trade_only():
    from src.services.decision_scorer import score_single_trade

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    target_id = conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, verification_result, suggestion_source
        ) VALUES (
            '2026-01-13', 'US_ETF_SGOV', 'Buy', '止损成功，验证通过', 'imported'
        )
        RETURNING id
        """
    ).fetchone()[0]
    other_id = conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, verification_result, suggestion_source
        ) VALUES (
            '2026-01-14', 'US_STK_NVDA', 'Buy', '止损成功，验证通过', 'imported'
        )
        RETURNING id
        """
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model)
        VALUES
          ('2026-01-12', 'signal', 'trade', '建议买入 SGOV', 1, 'gemini'),
          ('2026-01-13', 'signal', 'trade', '建议买入 NVDA', 1, 'claude')
        """
    )
    conn.execute(
        """
        INSERT INTO market_daily (code, date, close, open)
        VALUES
          ('SGOV', '2026-01-13', 100, 100),
          ('SGOV', '2026-02-12', 110, 110),
          ('NVDA', '2026-01-14', 200, 200),
          ('NVDA', '2026-02-13', 220, 220)
        """
    )

    scored = score_single_trade(conn, target_id)
    rows = conn.execute(
        """
        SELECT id, suggestion_source, verdict, outcome_pct
        FROM trade_logs
        WHERE id IN (?, ?)
        ORDER BY id
        """,
        [target_id, other_id],
    ).fetchall()

    assert scored == 1
    target_row = next(row for row in rows if row[0] == target_id)
    other_row = next(row for row in rows if row[0] == other_id)
    assert target_row[1] == "gemini"
    assert target_row[2] == "good_call"
    assert float(target_row[3]) == 10.0
    assert other_row[1] == "imported"
    assert other_row[2] is None
    assert other_row[3] is None


def test_score_single_trade_logs_debug_when_manual_trade_is_noop(caplog):
    from src.services.decision_scorer import score_single_trade

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    trade_id = conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, amount, suggestion_source
        ) VALUES (
            '2026-03-20', 'US_STK_VOO', 'Buy', 4760.0, 'manual'
        )
        RETURNING id
        """
    ).fetchone()[0]

    with caplog.at_level(logging.DEBUG, logger="src.services.decision_scorer"):
        scored = score_single_trade(conn, trade_id)

    assert scored == 0
    assert "manual/no-verification" in caplog.text


def test_score_all_trades_scores_manual_trade():
    from src.services.decision_scorer import score_all_trades

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, verification_result, suggestion_source
        ) VALUES
          ('2026-01-13', 'US_ETF_SGOV', 'Sell', '【卖飞】错过后续上涨', 'aia_trades_md'),
          ('2026-01-14', 'US_STK_NVDA', 'Buy', '止损成功，验证通过', 'manual')
        """
    )
    conn.execute(
        """
        INSERT INTO market_daily (code, date, close, open)
        VALUES
          ('SGOV', '2026-01-13', 100, 100),
          ('SGOV', '2026-02-12', 110, 110),
          ('NVDA', '2026-01-14', 200, 200),
          ('NVDA', '2026-02-13', 220, 220)
        """
    )

    scored = score_all_trades(conn)
    rows = conn.execute(
        """
        SELECT asset_id, suggestion_source, verdict, outcome_pct
        FROM trade_logs
        ORDER BY log_date
        """
    ).fetchall()

    assert scored == 2
    assert rows[0][0] == "US_ETF_SGOV"
    assert rows[0][2] == "regret"
    assert float(rows[0][3]) == -10.0
    assert rows[1][0] == "US_STK_NVDA"
    assert rows[1][2] == "good_call"
    assert float(rows[1][3]) == 10.0


def test_compute_funnel_and_leaderboard():
    from src.services.decision_scorer import compute_adoption_funnel, compute_leaderboard

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)

    conn.execute(
        """
        INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model)
        VALUES
          ('2026-01-09', 'signal', 'macro', '建议买入 SGOV', 1, 'aia'),
          ('2026-01-10', 'signal', 'macro', 'b', 0, 'aia'),
          ('2026-01-11', 'signal', 'macro', 'c', NULL, 'aia')
        """
    )
    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, verdict, outcome_pct, suggestion_source)
        VALUES
          ('2026-01-10', 'US_ETF_SGOV', 'Buy', 'good_call', 1.2, 'aia'),
          ('2026-01-11', 'US_STK_NVDA', 'Buy', 'regret', -2.2, 'aia'),
          ('2026-01-12', 'CN_FUND_900002', 'Sell', 'good_call', 3.5, 'manual')
        """
    )

    funnel = compute_adoption_funnel(conn)
    leaderboard = compute_leaderboard(conn)

    assert funnel["total"] == 3
    assert funnel["adopted"] == 1
    assert funnel["rejected"] == 1
    assert funnel["good_call"] == 1
    # NVDA trade (suggestion_source='aia') is attributed via nearby aia insight, so regret=1
    assert funnel["regret"] == 1

    assert len(leaderboard) == 1
    sources = {item["source"]: item for item in leaderboard}
    # Both aia trades (SGOV + NVDA) are attributed via nearby aia insights
    assert sources["aia"]["scored"] == 2


# ── A3: Verdict classifier symmetry fix tests ────────────────────────────────


def test_classify_verdict_sell_good_call_only():
    """Sell + text with only GOOD_CALL_KEYWORDS → 'good_call' (previously unreachable)."""
    from src.services.decision_scorer import classify_verdict_from_text

    # "止损成功" is in GOOD_CALL_KEYWORDS; no REGRET_KEYWORDS present.
    assert classify_verdict_from_text("Sell", "止损成功，符合预期") == "good_call"
    # "卖对了" is another GOOD_CALL keyword; same expectation.
    assert classify_verdict_from_text("卖出", "卖对了") == "good_call"


def test_classify_verdict_sell_regret_only_regression():
    """Sell + text with only REGRET_KEYWORDS → still 'regret' (regression guard)."""
    from src.services.decision_scorer import classify_verdict_from_text

    # "卖飞" is the canonical regret keyword from prod data; must remain 'regret'.
    assert classify_verdict_from_text("Sell", "【卖飞】错过后续上涨") == "regret"
    assert classify_verdict_from_text("Sell", "早卖了，后悔") == "regret"


def test_classify_verdict_buy_regret_only():
    """Buy + REGRET_KEYWORDS text → 'regret' for all REGRET keywords (not just 亏了/失误/后悔)."""
    from src.services.decision_scorer import classify_verdict_from_text

    # "踏空" was NOT in the old buy limited-subset — this demonstrates the fix.
    assert classify_verdict_from_text("Buy", "踏空了真可惜") == "regret"
    assert classify_verdict_from_text("Buy", "亏了不少") == "regret"


def test_classify_verdict_both_match_numeric_wins():
    """Both keyword sets + numeric outcome_pct → threshold suggestion used as tie-break."""
    from src.services.decision_scorer import classify_verdict_from_text

    # Text has both "亏了" (REGRET) and "止损成功" (GOOD_CALL).
    # For a Buy with outcome_pct=+10% and US Equity band=5%:
    #   derive_verdict_suggestion("Buy", 10.0, "US Equity") → VERDICT_GOOD_CALL
    mixed_text = "亏了一点，但整体止损成功"
    result = classify_verdict_from_text("Buy", mixed_text, outcome_pct=10.0, asset_class="US Equity")
    assert result == "good_call"

    # For a Buy with outcome_pct=-10%:
    #   derive_verdict_suggestion("Buy", -10.0, "US Equity") → VERDICT_REGRET
    result_neg = classify_verdict_from_text("Buy", mixed_text, outcome_pct=-10.0, asset_class="US Equity")
    assert result_neg == "regret"


def test_classify_verdict_both_match_count_wins_and_tie_is_none():
    """Both sets match, no numeric signal → count wins; equal counts → None.

    Keyword selection avoids "成功" (standalone GOOD_CALL keyword that also
    appears inside "止损成功") to keep match counts unambiguous.
    Uses "明智" and "及时" as clean single-match GOOD_CALL keywords.
    """
    from src.services.decision_scorer import classify_verdict_from_text

    # REGRET: "亏了"(1) + "踏空"(1) = 2; GOOD_CALL: "明智"(1) = 1 → regret wins.
    regret_heavy = "亏了，也踏空了，算是明智的教训"
    assert classify_verdict_from_text("Buy", regret_heavy, outcome_pct=None) == "regret"

    # GOOD_CALL: "明智"(1) + "及时"(1) = 2; REGRET: "亏了"(1) = 1 → good_call wins.
    good_heavy = "亏了一点，但操作明智且及时"
    assert classify_verdict_from_text("Buy", good_heavy, outcome_pct=None) == "good_call"

    # Exactly one REGRET keyword and one GOOD_CALL keyword → tied counts → None.
    tied = "亏了，明智"  # REGRET: 亏了(1) = 1; GOOD_CALL: 明智(1) = 1 → tied → None
    assert classify_verdict_from_text("Buy", tied, outcome_pct=None) is None


# ── B3: compute_insight_adoption_metrics — shared-function unit tests ─────────


def _seed_adoption_db(conn: "duckdb.DuckDBPyConnection") -> None:
    """Seed a mix of lesson/non-lesson and adopted/pending/rejected insights."""
    conn.execute(
        """
        INSERT INTO insights (insight_date, insight_type, category, content, adopted, created_at, title)
        VALUES
          ('2026-01-01', 'recommendation', 'recommendation', 'Buy SGOV',     1,    '2026-01-01 10:00:00', 'SGOV'),
          ('2026-01-02', 'recommendation', 'macro',          'Hold bonds',   1,    '2026-01-02 10:00:00', 'Bonds'),
          ('2026-01-03', 'observation',    'observation',    'Watch rates',  NULL, '2026-01-03 10:00:00', 'Rates'),
          ('2026-01-04', 'lesson',         'lesson',         'Learned X',    NULL, '2026-01-04 10:00:00', 'Lesson A')
        """
    )


def test_compute_insight_adoption_metrics_correct_values():
    """Shared function returns correct totals, adopted count, and adoption_rate."""
    from src.services.decision_scorer import compute_insight_adoption_metrics

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    _seed_adoption_db(conn)

    result = compute_insight_adoption_metrics(conn)

    # lesson is excluded → 3 non-lesson insights
    assert result["total_insights"] == 3
    assert result["adopted_count"] == 2
    assert result["pending_count"] == 1
    # 2/3 * 100 = 66.666… → rounded to 1dp = 66.7
    assert result["adoption_rate"] == 66.7


def test_compute_insight_adoption_metrics_empty_db():
    """Returns zeroed dict without raising when no insights exist."""
    from src.services.decision_scorer import compute_insight_adoption_metrics

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)

    result = compute_insight_adoption_metrics(conn)

    assert result["total_insights"] == 0
    assert result["adopted_count"] == 0
    assert result["adoption_rate"] == 0.0


def test_compute_insight_adoption_metrics_excludes_lessons_completely():
    """Only lesson rows → total=0, adoption_rate=0."""
    from src.services.decision_scorer import compute_insight_adoption_metrics

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute(
        """
        INSERT INTO insights (insight_date, insight_type, category, content, adopted, title)
        VALUES ('2026-01-01', 'lesson', 'lesson', 'Lesson only', 1, 'L')
        """
    )

    result = compute_insight_adoption_metrics(conn)

    assert result["total_insights"] == 0
    assert result["adoption_rate"] == 0.0


def test_log_verdict_audit_both_matched_flag():
    """Both keyword sets matching sets both_matched=True; mismatch stays strict.

    mismatch strictly means numeric-vs-keyword disagreement — with no numeric
    signal (outcome_pct=None → suggested=None) it must be False even though the
    narrative was mixed. Mixed narrative is captured by both_matched alone.
    """
    from pathlib import Path
    import duckdb
    from src.services.decision_scorer import _log_verdict_audit

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)

    # Insert a placeholder trade so the FK is satisfied (trade_id=1 not enforced in DuckDB,
    # but the table must exist — schema.sql already creates it).
    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result, suggestion_source)
        VALUES ('2026-01-10', 'US_STK_VOO', 'Buy', '亏了，止损成功', 'aia')
        """
    )
    trade_id = conn.execute("SELECT id FROM trade_logs LIMIT 1").fetchone()[0]

    # Both "亏了" (REGRET) and "止损成功" (GOOD_CALL) are present.
    _log_verdict_audit(
        conn,
        trade_id,
        "Buy",
        outcome_pct=None,
        verification_result="亏了，止损成功",
        final_verdict=None,
    )

    row = conn.execute(
        "SELECT mismatch, both_matched FROM verdict_audit WHERE trade_id = ? LIMIT 1",
        [trade_id],
    ).fetchone()

    assert row is not None, "verdict_audit row was not written"
    mismatch, both_matched = row
    assert both_matched is True, "both_matched should be True when both keyword sets matched"
    assert mismatch is False, (
        "mismatch must stay strict (numeric vs keyword disagreement only); "
        "no numeric signal here, so no mismatch"
    )


# ---------------------------------------------------------------------------
# Code-review fixes (2026-07-03): substring keyword counts + bulk maturity gate
# ---------------------------------------------------------------------------

def test_count_keyword_hits_ignores_substring_of_matched_longer_keyword():
    """'止损成功' must count as ONE good_call hit, not two via its substring '成功'."""
    from src.services.decision_scorer import _count_keyword_hits, GOOD_CALL_KEYWORDS
    assert _count_keyword_hits("止损成功", GOOD_CALL_KEYWORDS) == 1


def test_classify_sell_mixed_narrative_regret_wins_after_substring_fix():
    """Sell + '止损成功，亏了，后悔': good_call=1 (dedup'd), regret=2 → regret, not a tie/None."""
    from src.services.decision_scorer import classify_verdict_from_text
    verdict = classify_verdict_from_text("Sell", "止损成功，亏了，后悔")
    assert verdict == "regret"


def test_score_all_trades_maturity_gate_skips_young_trades():
    """Bulk scoring must mirror score_single_trade's 30-day gate: a day-0 text verdict
    would permanently pre-empt the matured +30d market-price scoring (P9 runs bulk
    scoring on every sync)."""
    from datetime import date, timedelta
    from src.services.decision_scorer import score_all_trades

    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)

    young = (date.today() - timedelta(days=5)).isoformat()
    old = (date.today() - timedelta(days=45)).isoformat()
    conn.execute(
        f"""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result, suggestion_source)
        VALUES
          ('{young}', 'US_STK_YOUNG', 'Sell', '【卖飞】错过后续上涨', 'aia'),
          ('{old}',   'US_STK_OLD',   'Sell', '【卖飞】错过后续上涨', 'aia')
        """
    )

    score_all_trades(conn)
    rows = dict(
        conn.execute("SELECT asset_id, verdict FROM trade_logs").fetchall()
    )
    conn.close()

    assert rows["US_STK_OLD"] == "regret", "matured trade must be scored"
    assert rows["US_STK_YOUNG"] is None, "young trade must stay unscored until 30d maturity"
