"""Unit tests for InsightManager using an in-memory DuckDB database."""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

from src.services.ai_advisor.insight_manager import (
    Insight,
    InsightManager,
    bridge_ai_insights_to_decision_hub,
)
from src.services.decision_intelligence import GENERIC_SOURCES, normalize_source


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE SEQUENCE IF NOT EXISTS ai_insights_seq START 1;

CREATE TABLE IF NOT EXISTS ai_insights (
    id INTEGER PRIMARY KEY DEFAULT nextval('ai_insights_seq'),
    source_report_id INTEGER,
    category VARCHAR,
    title VARCHAR NOT NULL,
    body VARCHAR NOT NULL,
    tags VARCHAR,
    confidence DECIMAL(3,2),
    status VARCHAR DEFAULT 'raw',
    recurrence_count INTEGER DEFAULT 1,
    entity_refs VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    validated_cases INTEGER DEFAULT 0,
    validated_case_links JSON,
    rule_layer VARCHAR(20)
);

CREATE SEQUENCE IF NOT EXISTS seq_insights_id START 1;

CREATE TABLE IF NOT EXISTS insights (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_insights_id'),
    insight_date DATE NOT NULL,
    insight_type VARCHAR(50) NOT NULL,
    category VARCHAR(100),
    title VARCHAR,
    content TEXT NOT NULL,
    observation_source VARCHAR(100),
    ai_model VARCHAR(100),
    confidence_score DECIMAL(3,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_INSERT_SAMPLE_SQL = """
INSERT INTO ai_insights (category, title, body, tags, confidence, status, recurrence_count, entity_refs)
VALUES
    ('risk',    'Overexposed to tech', 'Tech allocation exceeds 40%', 'tech,sizing', 0.8, 'raw',      1, 'US_STK_AAPL,US_STK_MSFT'),
    ('timing',  'Buy on dip pattern',  'Pattern: buy after -5% day', 'timing',      0.6, 'recurring', 2, 'US_STK_QQQ')
;
"""


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary DuckDB file with ai_insights table pre-populated."""
    path = str(tmp_path / "test_insights.duckdb")
    conn = duckdb.connect(path)
    conn.execute(_CREATE_TABLE_SQL)
    conn.execute(_INSERT_SAMPLE_SQL)
    conn.close()
    return path


@pytest.fixture
def manager(db_path) -> InsightManager:
    return InsightManager(db_path=db_path)


# ---------------------------------------------------------------------------
# Test 1: list_insights returns all non-deprecated rows
# ---------------------------------------------------------------------------

def test_list_insights_returns_two_rows(manager):
    """list_insights() on a table with 2 rows should return 2 Insight objects."""
    results = manager.list_insights()
    assert len(results) == 2
    assert all(isinstance(r, Insight) for r in results)


# ---------------------------------------------------------------------------
# Test 2: promote_insight advances raw → recurring
# ---------------------------------------------------------------------------

def test_promote_insight_advances_status(manager, db_path):
    """promote_insight() should move 'raw' to 'recurring'."""
    # Insight #1 is 'raw'
    result = manager.promote_insight(1)
    assert result is not None
    assert result.status == "recurring"

    # Verify persisted
    fetched = manager.get_insight(1)
    assert fetched.status == "recurring"


# ---------------------------------------------------------------------------
# Test 2: promote_insight bridges principle insights into Decision Hub
# ---------------------------------------------------------------------------

def test_promote_insight_inserts_bridge_row_on_principle_transition(manager, db_path):
    """promote_insight() should insert a Decision Hub insight when reaching principle."""
    conn = duckdb.connect(db_path)
    conn.execute(
        """
        INSERT INTO ai_insights (category, title, body, tags, confidence, status, recurrence_count, entity_refs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "risk",
            "Bridge into decision hub",
            "Promoted principle content",
            "bridge",
            0.91,
            "validated",
            3,
            "US_STK_TEST",
        ],
    )
    insight_id = conn.execute(
        "SELECT id FROM ai_insights WHERE title = ? ORDER BY id DESC LIMIT 1",
        ["Bridge into decision hub"],
    ).fetchone()[0]
    conn.close()

    result = manager.promote_insight(insight_id)
    assert result is not None
    assert result.status == "principle"

    conn = duckdb.connect(db_path, read_only=True)
    row = conn.execute(
        """
        SELECT insight_date, insight_type, category, title, content, observation_source
        FROM insights
        WHERE observation_source = ?
        """,
        [f"ai_insights:{insight_id}"],
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == date.today()
    assert row[1] == "AI_Advisor"
    assert row[2] == "risk"
    assert row[3] == "Bridge into decision hub"
    assert row[4] == "Promoted principle content"
    assert row[5] == f"ai_insights:{insight_id}"


# ---------------------------------------------------------------------------
# Test 3: repeated promote_insight calls do not duplicate bridge rows
# ---------------------------------------------------------------------------

def test_promote_insight_is_idempotent_for_bridge_rows(manager, db_path):
    """Repeated promote_insight() calls should not duplicate the Decision Hub row."""
    conn = duckdb.connect(db_path)
    conn.execute(
        """
        INSERT INTO ai_insights (category, title, body, tags, confidence, status, recurrence_count, entity_refs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "timing",
            "Idempotent bridge",
            "Bridge once only",
            "bridge",
            0.84,
            "validated",
            2,
            "US_STK_TEST_2",
        ],
    )
    insight_id = conn.execute(
        "SELECT id FROM ai_insights WHERE title = ? ORDER BY id DESC LIMIT 1",
        ["Idempotent bridge"],
    ).fetchone()[0]
    conn.close()

    manager.promote_insight(insight_id)
    manager.promote_insight(insight_id)

    conn = duckdb.connect(db_path, read_only=True)
    count = conn.execute(
        "SELECT COUNT(*) FROM insights WHERE observation_source = ?",
        [f"ai_insights:{insight_id}"],
    ).fetchone()[0]
    conn.close()

    assert count == 1


# ---------------------------------------------------------------------------
# Test 4: promote_insight refreshes an existing bridge row instead of leaving stale content
# ---------------------------------------------------------------------------

def test_promote_insight_updates_existing_bridge_row(manager, db_path):
    """promote_insight() should refresh existing Decision Hub rows for the same source."""
    conn = duckdb.connect(db_path)
    conn.execute(
        """
        INSERT INTO ai_insights (category, title, body, tags, confidence, status, recurrence_count, entity_refs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "process",
            "Upsert bridge",
            "Fresh principle content",
            "bridge",
            0.83,
            "validated",
            2,
            "US_STK_UPSERT",
        ],
    )
    insight_id = conn.execute(
        "SELECT id FROM ai_insights WHERE title = ? ORDER BY id DESC LIMIT 1",
        ["Upsert bridge"],
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO insights (insight_date, insight_type, category, title, content, observation_source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            date(2026, 1, 1),
            "legacy",
            "stale",
            "Old bridge title",
            "Old bridge content",
            f"ai_insights:{insight_id}",
        ],
    )
    conn.close()

    result = manager.promote_insight(insight_id)
    assert result is not None
    assert result.status == "principle"

    conn = duckdb.connect(db_path, read_only=True)
    row = conn.execute(
        """
        SELECT insight_type, category, title, content
        FROM insights
        WHERE observation_source = ?
        """,
        [f"ai_insights:{insight_id}"],
    ).fetchone()
    conn.close()

    assert row == ("AI_Advisor", "process", "Upsert bridge", "Fresh principle content")


# ---------------------------------------------------------------------------
# Test 5: promote_insight collapses duplicate bridge rows when present
# ---------------------------------------------------------------------------

def test_promote_insight_collapses_duplicate_bridge_rows(manager, db_path):
    """promote_insight() should reduce duplicate Decision Hub rows to one canonical row."""
    conn = duckdb.connect(db_path)
    conn.execute(
        """
        INSERT INTO ai_insights (category, title, body, tags, confidence, status, recurrence_count, entity_refs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "strategy",
            "Duplicate bridge cleanup",
            "Cleanup duplicate bridge rows",
            "bridge",
            0.79,
            "validated",
            1,
            "US_STK_DUP",
        ],
    )
    insight_id = conn.execute(
        "SELECT id FROM ai_insights WHERE title = ? ORDER BY id DESC LIMIT 1",
        ["Duplicate bridge cleanup"],
    ).fetchone()[0]

    observation_source = f"ai_insights:{insight_id}"
    conn.execute(
        """
        INSERT INTO insights (insight_date, insight_type, category, title, content, observation_source, created_at)
        VALUES
            (CURRENT_DATE, 'AI_Advisor', 'strategy', 'Duplicate bridge cleanup', 'stale duplicate 1', ?, CURRENT_TIMESTAMP),
            (CURRENT_DATE, 'AI_Advisor', 'strategy', 'Duplicate bridge cleanup', 'stale duplicate 2', ?, CURRENT_TIMESTAMP)
        """,
        [observation_source, observation_source],
    )
    conn.close()

    result = manager.promote_insight(insight_id)
    assert result is not None
    assert result.status == "principle"

    conn = duckdb.connect(db_path, read_only=True)
    count = conn.execute(
        "SELECT COUNT(*) FROM insights WHERE observation_source = ?",
        [observation_source],
    ).fetchone()[0]
    conn.close()

    assert count == 1


# ---------------------------------------------------------------------------
# Test 6: promote_insight rolls back status when bridge write fails
# ---------------------------------------------------------------------------

def test_promote_insight_rolls_back_status_when_bridge_write_fails(manager, db_path, monkeypatch):
    """If the bridge write fails, the insight should not remain stuck at principle."""
    conn = duckdb.connect(db_path)
    conn.execute(
        """
        INSERT INTO ai_insights (category, title, body, tags, confidence, status, recurrence_count, entity_refs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "risk",
            "Rollback bridge",
            "Bridge failure should rollback",
            "bridge",
            0.88,
            "validated",
            1,
            "US_STK_ROLLBACK",
        ],
    )
    insight_id = conn.execute(
        "SELECT id FROM ai_insights WHERE title = ? ORDER BY id DESC LIMIT 1",
        ["Rollback bridge"],
    ).fetchone()[0]
    conn.close()

    def fail_bridge(*args, **kwargs):
        raise RuntimeError("bridge insert failed")

    monkeypatch.setattr(manager, "_ensure_decision_hub_insight", fail_bridge)

    with pytest.raises(RuntimeError, match="bridge insert failed"):
        manager.promote_insight(insight_id)

    conn = duckdb.connect(db_path, read_only=True)
    row = conn.execute(
        "SELECT status FROM ai_insights WHERE id = ?",
        [insight_id],
    ).fetchone()
    bridge_count = conn.execute(
        "SELECT COUNT(*) FROM insights WHERE observation_source = ?",
        [f"ai_insights:{insight_id}"],
    ).fetchone()[0]
    conn.close()

    assert row[0] == "validated"
    assert bridge_count == 0


# ---------------------------------------------------------------------------
# Test 6: deduplicate_all handles NULL recurrence_count values safely
# ---------------------------------------------------------------------------

def test_deduplicate_all_handles_null_recurrence_count(manager, db_path):
    """deduplicate_all() should not crash when grouped recurrence_count values are NULL."""
    conn = duckdb.connect(db_path)
    conn.execute(
        """
        INSERT INTO ai_insights (category, title, body, tags, confidence, status, recurrence_count, entity_refs)
        VALUES
            ('risk', 'Null recurrence group', 'First null recurrence', 'null', 0.71, 'raw', NULL, 'US_STK_NULL_1'),
            ('risk', 'Null recurrence group', 'Second null recurrence', 'null', 0.72, 'raw', NULL, 'US_STK_NULL_2')
        """
    )

    result = manager.deduplicate_all(conn)
    keeper = conn.execute(
        """
        SELECT recurrence_count
        FROM ai_insights
        WHERE title = ? AND status != 'deprecated'
        ORDER BY id
        LIMIT 1
        """,
        ["Null recurrence group"],
    ).fetchone()
    conn.close()

    assert result["groups_merged"] == 1
    assert result["duplicates_deprecated"] == 1
    assert keeper[0] == 1


# ---------------------------------------------------------------------------
# Test 7: merge_insights deprecates duplicate and increments recurrence_count
# ---------------------------------------------------------------------------

def test_merge_insights_deprecates_duplicate_and_increments_primary(manager, db_path):
    """merge_insights(primary=1, duplicate=2) should deprecate #2 and increment #1's count."""
    original_count = manager.get_insight(1).recurrence_count  # starts at 1

    result = manager.merge_insights(primary_id=1, duplicate_id=2)

    assert result is not None
    assert result.recurrence_count == original_count + 1

    # Duplicate should be deprecated
    # Note: get_insight does not filter deprecated so we query directly
    conn = duckdb.connect(db_path, read_only=True)
    row = conn.execute("SELECT status FROM ai_insights WHERE id = 2").fetchone()
    conn.close()
    assert row[0] == "deprecated"


# ---------------------------------------------------------------------------
# Test 8: update_insight rejects unknown fields
# ---------------------------------------------------------------------------

def test_update_insight_rejects_unknown_fields(manager):
    """update_insight with unknown fields should not apply them; known fields still work."""
    # Attempt to update 'category' (not in allowed set) and 'title' (allowed)
    result = manager.update_insight(1, {"category": "hacked", "title": "Updated title"})

    assert result is not None
    assert result.title == "Updated title"
    # 'category' must NOT have been changed
    assert result.category == "risk"


# ---------------------------------------------------------------------------
# Test: list_insights sorts by updated_at DESC (recency first, recurrence tiebreak)
# ---------------------------------------------------------------------------

def test_list_insights_sort_updated_at_precedes_high_recurrence(manager, db_path):
    """A row with newer updated_at must appear before an older row with higher recurrence."""
    conn = duckdb.connect(db_path)
    # Insert an older high-recurrence row with a past updated_at
    conn.execute(
        """
        INSERT INTO ai_insights
            (category, title, body, tags, confidence, status, recurrence_count,
             entity_refs, created_at, updated_at)
        VALUES
            ('risk', 'High recurrence old row', 'body', '', 0.9, 'raw', 99,
             'US_STK_OLD',
             TIMESTAMP '2025-01-01 00:00:00',
             TIMESTAMP '2025-01-01 00:00:00')
        """
    )
    # Insert a newer low-recurrence row with a recent updated_at
    conn.execute(
        """
        INSERT INTO ai_insights
            (category, title, body, tags, confidence, status, recurrence_count,
             entity_refs, created_at, updated_at)
        VALUES
            ('risk', 'Low recurrence new row', 'body', '', 0.7, 'raw', 1,
             'US_STK_NEW',
             TIMESTAMP '2026-06-01 00:00:00',
             TIMESTAMP '2026-06-01 00:00:00')
        """
    )
    conn.close()

    results = manager.list_insights()
    titles = [r.title for r in results]

    # The newer row (updated_at 2026-06-01) must come before the older high-recurrence row
    new_idx = titles.index("Low recurrence new row")
    old_idx = titles.index("High recurrence old row")
    assert new_idx < old_idx, (
        f"Expected newer row at lower index; got new_idx={new_idx}, old_idx={old_idx}"
    )


# ---------------------------------------------------------------------------
# Test: heal_insights_recurrence — inflated pattern healed correctly
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# bridge_ai_insights_to_decision_hub tests
# ---------------------------------------------------------------------------

# Use a fresh DB (no pre-populated sample rows) so bridge counts are exact.
@pytest.fixture
def bridge_db_path(tmp_path):
    """DuckDB with both tables but no pre-inserted rows for deterministic bridge tests."""
    path = str(tmp_path / "bridge_insights.duckdb")
    conn = duckdb.connect(path)
    conn.execute(_CREATE_TABLE_SQL)
    conn.close()
    return path


def test_bridge_recommendation_bridged_once_idempotent(bridge_db_path):
    """recommendation row → bridged exactly once; second reconciler call → 0 new rows."""
    conn = duckdb.connect(bridge_db_path)
    conn.execute(
        """INSERT INTO ai_insights
               (category, title, body, confidence, status, recurrence_count, entity_refs)
           VALUES ('recommendation', 'Buy VOO on dips', 'Full buy rationale', 0.85, 'raw', 1, 'US_ETF_VOO')"""
    )
    n1 = bridge_ai_insights_to_decision_hub(conn)
    conn.close()

    assert n1 == 1

    conn = duckdb.connect(bridge_db_path)
    n2 = bridge_ai_insights_to_decision_hub(conn)
    total = conn.execute(
        "SELECT COUNT(*) FROM insights WHERE observation_source LIKE 'ai_insights:%'"
    ).fetchone()[0]
    conn.close()

    assert n2 == 0  # idempotent — no duplicate
    assert total == 1


def test_bridge_raw_not_bridged_deprecated_not_bridged(bridge_db_path):
    """raw non-recommendation → NOT bridged; deprecated rows → NEVER bridged."""
    conn = duckdb.connect(bridge_db_path)
    conn.execute(
        """INSERT INTO ai_insights
               (category, title, body, confidence, status, recurrence_count)
           VALUES ('process', 'Raw lesson not bridged', 'body text', 0.5, 'raw', 1)"""
    )
    conn.execute(
        """INSERT INTO ai_insights
               (category, title, body, confidence, status, recurrence_count)
           VALUES ('strategy', 'Deprecated lesson', 'body text', 0.5, 'deprecated', 3)"""
    )
    n = bridge_ai_insights_to_decision_hub(conn)
    conn.close()

    assert n == 0


def test_bridge_recurring_non_recommendation_as_lesson(bridge_db_path):
    """'recurring' non-recommendation → bridged with category='lesson'."""
    conn = duckdb.connect(bridge_db_path)
    conn.execute(
        """INSERT INTO ai_insights
               (category, title, body, confidence, status, recurrence_count)
           VALUES ('process', 'Recurring process insight', 'Detailed lesson body', 0.7, 'recurring', 2)"""
    )
    insight_id = conn.execute(
        "SELECT id FROM ai_insights WHERE title = ? ORDER BY id DESC LIMIT 1",
        ["Recurring process insight"],
    ).fetchone()[0]

    n = bridge_ai_insights_to_decision_hub(conn)
    assert n == 1

    row = conn.execute(
        "SELECT category, ai_model FROM insights WHERE observation_source = ?",
        [f"ai_insights:{insight_id}"],
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "lesson"
    assert row[1] == "review"


def test_bridge_recommendation_in_funnel_scope_and_ai_model_normalized(bridge_db_path):
    """Bridged recommendation is in funnel scope; ai_model normalizes to a non-generic source."""
    conn = duckdb.connect(bridge_db_path)
    conn.execute(
        """INSERT INTO ai_insights
               (category, title, body, confidence, status, recurrence_count)
           VALUES ('recommendation', 'Funnel rec insight', 'Rec body text', 0.9, 'raw', 1)"""
    )
    insight_id = conn.execute(
        "SELECT id FROM ai_insights WHERE title = ? ORDER BY id DESC LIMIT 1",
        ["Funnel rec insight"],
    ).fetchone()[0]
    bridge_ai_insights_to_decision_hub(conn)

    # Must appear under the funnel query (category != 'lesson')
    funnel_row = conn.execute(
        """SELECT ai_model FROM insights
           WHERE observation_source = ?
             AND COALESCE(category, '') != 'lesson'""",
        [f"ai_insights:{insight_id}"],
    ).fetchone()
    conn.close()

    assert funnel_row is not None, "Bridged recommendation must appear in funnel scope"
    normalized = normalize_source(funnel_row[0])
    assert normalized not in GENERIC_SOURCES, (
        f"ai_model '{funnel_row[0]}' normalized to generic '{normalized}' — must be distinct"
    )


def test_promote_does_not_double_write_after_reconciler(bridge_db_path):
    """Promote writes the bridge row; a subsequent reconciler run must not add a duplicate."""
    mgr = InsightManager(db_path=bridge_db_path)
    conn = duckdb.connect(bridge_db_path)
    conn.execute(
        """INSERT INTO ai_insights
               (category, title, body, confidence, status, recurrence_count)
           VALUES ('risk', 'Promote then reconcile', 'Content here', 0.88, 'validated', 2)"""
    )
    insight_id = conn.execute(
        "SELECT id FROM ai_insights WHERE title = ? ORDER BY id DESC LIMIT 1",
        ["Promote then reconcile"],
    ).fetchone()[0]
    conn.close()

    # Promote to principle — writes the bridge row via _ensure_decision_hub_insight
    result = mgr.promote_insight(insight_id)
    assert result is not None
    assert result.status == "principle"

    # Reconciler sees the row already exists → zero new rows
    conn = duckdb.connect(bridge_db_path)
    n = bridge_ai_insights_to_decision_hub(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM insights WHERE observation_source = ?",
        [f"ai_insights:{insight_id}"],
    ).fetchone()[0]
    conn.close()

    assert n == 0
    assert count == 1


# ---------------------------------------------------------------------------
# Code-review fixes (2026-07-03): per-row bridge isolation + Promote-first source
# ---------------------------------------------------------------------------

def test_bridge_per_row_isolation_one_bad_row_does_not_abort_rest(bridge_db_path):
    """One failing row must not abort bridging of the remaining qualifying rows."""
    from unittest.mock import patch
    import src.services.ai_advisor.insight_manager as im

    conn = duckdb.connect(bridge_db_path)
    conn.execute(
        """INSERT INTO ai_insights
               (category, title, body, confidence, status, recurrence_count, entity_refs)
           VALUES
               ('recommendation', 'Poisoned row', '', 0.5, 'raw', 1, ''),
               ('recommendation', 'Healthy row', '', 0.5, 'raw', 1, '')"""
    )

    real_upsert = im._upsert_bridge_row
    calls = {"n": 0}

    def flaky_upsert(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated per-row failure")
        return real_upsert(*args, **kwargs)

    with patch.object(im, "_upsert_bridge_row", side_effect=flaky_upsert):
        bridged = bridge_ai_insights_to_decision_hub(conn)

    rows = conn.execute("SELECT COUNT(*) FROM insights").fetchone()[0]
    conn.close()

    assert bridged == 1, "the healthy row must still be bridged"
    assert rows == 1


def test_promote_first_bridge_row_gets_review_source(bridge_db_path):
    """Promote running BEFORE the reconciler must still stamp ai_model='review' —
    otherwise the NOT EXISTS reconciler skips the row forever and it buckets as
    generic 'system' in the Decision Hub source mix."""
    from src.services.ai_advisor.insight_manager import InsightManager, Insight

    conn = duckdb.connect(bridge_db_path)
    mgr = InsightManager(db_path=bridge_db_path)
    insight = Insight(
        id=99, category="process", title="Promoted principle", body="Full text",
        tags="", confidence=0.7, status="principle", recurrence_count=3,
        entity_refs="", source_report_id=1,
        created_at="2026-07-01 00:00:00", updated_at="2026-07-01 00:00:00",
    )

    mgr._ensure_decision_hub_insight(conn, insight)

    row = conn.execute(
        "SELECT ai_model, confidence_score FROM insights WHERE observation_source = 'ai_insights:99'"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "review"
    assert float(row[1]) == 0.7
