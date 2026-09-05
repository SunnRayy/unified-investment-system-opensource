from unittest.mock import MagicMock


def _make_builder(db_mock=None):
    from src.services.ai_advisor.context_builder import ContextBuilder

    cb = ContextBuilder.__new__(ContextBuilder)
    cb._db = db_mock if db_mock is not None else MagicMock()
    return cb


# Row schema: (memo_date, title, strategic_bias, key_directives, content)


def test_build_strategy_context_renders_all_memos_in_timeframe():
    db = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = [
        ("2026-03-20", "最新策略", "defensive", '["a", "b"]', "L" * 1005),
        ("2026-03-10", "旧策略", "offensive", "[]", "O" * 50),
    ]
    db.execute.return_value = result

    cb = _make_builder(db_mock=db)
    rendered = cb.build_strategy_context("30d")

    assert "最新策略" in rendered
    assert "旧策略" in rendered
    assert "L" * 1005 in rendered


def test_build_strategy_context_includes_full_content_without_truncation():
    db = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = [
        ("2026-03-20", "最新策略", "defensive", '["x"]', "L" * 2005),
        ("2026-03-10", "旧策略", "offensive", "[]", "O" * 50),
    ]
    db.execute.return_value = result

    cb = _make_builder(db_mock=db)
    rendered = cb.build_strategy_context("60d")

    assert "L" * 2005 in rendered
    assert "O" * 50 in rendered


def test_build_strategy_context_renders_content_when_key_directives_empty():
    """GitHub #25 — a memo whose key_directives is an empty JSON array must still
    render its full content, never a bare '[]'."""
    db = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = [
        ("2026-04-30", "科技板块清仓 Project Meridian", "offensive", "[]",
         "本次决议清仓恒生科技全部仓位，资金转入货币基金。理由：估值高位、止盈满足。"),
    ]
    db.execute.return_value = result

    cb = _make_builder(db_mock=db)
    rendered = cb.build_strategy_context("90d")

    assert "本次决议清仓恒生科技全部仓位" in rendered
    # the bare empty-array string must not leak into the rendered block
    assert "\n[]" not in rendered
    assert "Project Meridian" in rendered


def test_build_strategy_context_falls_back_to_directives_when_content_empty():
    """If content is somehow blank, fall back to the parsed directive bullets
    rather than emitting an empty body."""
    db = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = [
        ("2026-04-30", "无正文备忘", "neutral", '["保留现金", "等待回调"]', ""),
    ]
    db.execute.return_value = result

    cb = _make_builder(db_mock=db)
    rendered = cb.build_strategy_context("30d")

    assert "- 保留现金" in rendered
    assert "- 等待回调" in rendered


def test_build_strategy_context_returns_not_found_when_db_empty():
    db = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = []
    db.execute.return_value = result

    cb = _make_builder(db_mock=db)
    rendered = cb.build_strategy_context("30d")

    assert rendered == "No strategy memos found."
