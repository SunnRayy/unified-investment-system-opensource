"""resolve_language() precedence + the V89 seed (Program BIL / WS-5).

Two outcomes matter more than any count:
  - the OWNER's instance must resolve to 'zh-CN'
  - a fresh/public install with no row must resolve to 'en'

Both are proved here against a real DuckDB file, and every gate is followed by
its red proof — a green test that cannot go red is how five defects reached
this program's gates already.
"""

from __future__ import annotations

import duckdb
import pytest

from src.services.ai_advisor import language_resolver
from src.services.ai_advisor.language_resolver import (
    canonical_language,
    resolve_language,
    resolve_language_code,
)

# The V89 statements, verbatim in shape, so this file tests the migration's SQL
# rather than a paraphrase of it.
_ADD_COLUMN = "ALTER TABLE user_profile ADD COLUMN language VARCHAR"
_EVIDENCE_QUERY = """
    SELECT COUNT(*) FROM ai_reports
    WHERE report_type IN ('brief', 'review')
      AND content_json IS NOT NULL
      AND (content_json LIKE '%宏观形势%'
           OR content_json LIKE '%持仓分析与风险预警%'
           OR content_json LIKE '%交易汇总%'
           OR content_json LIKE '%经验沉淀%'
           OR content_json LIKE '%宏觀形勢%'
           OR content_json LIKE '%交易匯總%')
"""
_SEED = """
    INSERT INTO user_profile (id, language, updated_at)
    VALUES (1, 'zh-CN', CURRENT_TIMESTAMP)
    ON CONFLICT(id) DO UPDATE SET
        language = excluded.language,
        updated_at = excluded.updated_at
    WHERE user_profile.language IS NULL
"""


def _make_db(tmp_path, *, chinese_reports: int = 0, seed_language: bool = True):
    """Build a DB shaped like the real one, then run V89's data step on it."""
    path = str(tmp_path / "profile.duckdb")
    conn = duckdb.connect(path)
    conn.execute(
        "CREATE TABLE user_profile ("
        "  id INTEGER PRIMARY KEY DEFAULT 1, display_name VARCHAR,"
        "  avatar_base64 TEXT, updated_at TIMESTAMP, philosophy TEXT)"
    )
    conn.execute("CREATE TABLE ai_reports (id INTEGER, report_type VARCHAR, content_json TEXT)")
    for i in range(chinese_reports):
        conn.execute(
            "INSERT INTO ai_reports VALUES (?, 'brief', ?)",
            [i, '{"宏观形势": {"narrative": "全球市场稳定。"}}'],
        )
    conn.execute(_ADD_COLUMN)
    if seed_language and conn.execute(_EVIDENCE_QUERY).fetchone()[0] > 0:
        conn.execute(_SEED)
    return conn


@pytest.fixture(autouse=True)
def _no_settings_fallback(monkeypatch):
    """Isolate from the repo's real settings.yaml unless a test opts in."""
    monkeypatch.setattr(
        "src.services.settings_manager.get_configured_language", lambda: None
    )
    monkeypatch.setattr(language_resolver, "_null_language_warned", False)


# ---------------------------------------------------------------------------
# The two outcomes that matter
# ---------------------------------------------------------------------------


def test_owner_instance_resolves_to_zh_cn(tmp_path):
    """The owner's DB carries 43 Chinese-keyed AI reports; V89 must pin zh-CN."""
    conn = _make_db(tmp_path, chinese_reports=43)
    try:
        assert conn.execute("SELECT language FROM user_profile WHERE id = 1").fetchone() == (
            "zh-CN",
        )
        resolution = resolve_language(conn)
        assert resolution["language"] == "zh-CN"
        assert resolution["source"] == "user_profile"
        assert resolution["fallback_reason"] is None
    finally:
        conn.close()


def test_fresh_install_with_no_row_resolves_to_en(tmp_path):
    """A public install has no reports, so no row, so 'en' — and it says why."""
    conn = _make_db(tmp_path, chinese_reports=0)
    try:
        assert conn.execute("SELECT COUNT(*) FROM user_profile").fetchone() == (0,)
        resolution = resolve_language(conn)
        assert resolution["language"] == "en"
        assert resolution["source"] == "default"
        assert resolution["fallback_reason"] == "no user_profile row"
    finally:
        conn.close()


def test_owner_gate_goes_red_without_the_seed(tmp_path):
    """Anti-vacuity: skip V89's data step and the owner regresses to English.

    This is the exact regression the workstream exists to prevent, so it is
    asserted as a behaviour, not assumed.
    """
    conn = _make_db(tmp_path, chinese_reports=43, seed_language=False)
    try:
        assert conn.execute("SELECT language FROM user_profile WHERE id = 1").fetchone() is None
        assert resolve_language(conn)["language"] == "en"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


def test_request_override_beats_the_persisted_value(tmp_path):
    conn = _make_db(tmp_path, chinese_reports=5)
    try:
        resolution = resolve_language(conn, request_language="en")
        assert resolution["language"] == "en"
        assert resolution["source"] == "request"
    finally:
        conn.close()


def test_persisted_value_beats_settings_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.services.settings_manager.get_configured_language", lambda: "en"
    )
    conn = _make_db(tmp_path, chinese_reports=5)
    try:
        assert resolve_language(conn)["language"] == "zh-CN"
    finally:
        conn.close()


def test_settings_yaml_is_a_labelled_fallback_not_a_silent_one(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.services.settings_manager.get_configured_language", lambda: "zh-CN"
    )
    conn = _make_db(tmp_path, chinese_reports=0)
    try:
        resolution = resolve_language(conn)
        assert resolution["language"] == "zh-CN"
        assert resolution["source"] == "config_fallback"
        # goal_resolver's rule: a fallback always says WHY.
        assert resolution["fallback_reason"] == "no user_profile row"
    finally:
        conn.close()


def test_null_column_on_an_existing_row_is_labelled_and_warned(tmp_path, caplog):
    conn = _make_db(tmp_path, chinese_reports=0)
    conn.execute("INSERT INTO user_profile (id, display_name) VALUES (1, 'Ray')")
    try:
        with caplog.at_level("WARNING", logger=language_resolver.__name__):
            resolution = resolve_language(conn)
        assert resolution["language"] == "en"
        assert resolution["fallback_reason"] == "user_profile.language is NULL"
        assert "user_profile.language is NULL" in caplog.text
    finally:
        conn.close()


def test_unsupported_persisted_value_is_ignored_not_trusted(tmp_path, caplog):
    conn = _make_db(tmp_path, chinese_reports=0)
    conn.execute("INSERT INTO user_profile (id, language) VALUES (1, 'klingon')")
    try:
        with caplog.at_level("WARNING", logger=language_resolver.__name__):
            resolution = resolve_language(conn)
        assert resolution["language"] == "en"
        assert "unsupported value" in resolution["fallback_reason"]
    finally:
        conn.close()


def test_resolver_never_raises_on_a_broken_db(tmp_path):
    """A missing table must degrade to a labelled default, never a 500."""
    path = str(tmp_path / "empty.duckdb")
    conn = duckdb.connect(path)
    try:
        resolution = resolve_language(conn)
        assert resolution["language"] == "en"
        assert "read failed" in resolution["fallback_reason"]
    finally:
        conn.close()


def test_resolve_language_code_wrapper(tmp_path):
    conn = _make_db(tmp_path, chinese_reports=1)
    try:
        assert resolve_language_code(conn) == "zh-CN"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("en", "en"),
        ("EN", "en"),
        ("en-US", "en"),
        ("zh", "zh-CN"),
        ("zh-CN", "zh-CN"),
        ("zh_CN", "zh-CN"),
        ("zh-Hans", "zh-CN"),
        ("  zh-cn  ", "zh-CN"),
        ("fr", None),
        ("zh-Hant-TW", None),  # Traditional is NOT silently folded into Simplified
        ("", None),
        (None, None),
        (7, None),
    ],
)
def test_canonical_language(raw, expected):
    assert canonical_language(raw) == expected
