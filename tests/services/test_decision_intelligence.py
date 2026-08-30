import pytest

pytestmark = pytest.mark.pipeline

from pathlib import Path
from unittest.mock import MagicMock

import duckdb


def _init_db():
    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    migration_sql = Path("src/database/migrations/007_phase5_insights_title.sql").read_text(encoding="utf-8")
    conn.execute(migration_sql)
    return conn


def test_duplicate_system_sources_merged_into_one_bucket():
    """Sources with ai_model NULL, 'other', and 'unknown' must collapse into ONE 'system' row."""
    from src.services.decision_intelligence import get_decision_intelligence

    conn = _init_db()
    conn.execute(
        """
        INSERT INTO insights (insight_date, insight_type, content, ai_model, adopted, created_at, title)
        VALUES
          ('2026-01-01', 'recommendation', 'content A', NULL,      1, '2026-01-01', 'A'),
          ('2026-01-02', 'recommendation', 'content B', 'unknown', 0, '2026-01-02', 'B'),
          ('2026-01-03', 'recommendation', 'content C', 'other',   NULL, '2026-01-03', 'C')
        """
    )

    result = get_decision_intelligence(conn, {})
    sources = result["decision_patterns"]["sources"]
    system_rows = [s for s in sources if s["source"] == "system"]

    assert len(system_rows) == 1, (
        f"Expected exactly 1 'system' row, got {len(system_rows)}: {system_rows}"
    )
    row = system_rows[0]
    assert row["total"] == 3, f"Expected total=3 (merged), got {row['total']}"
    assert row["adopted"] == 1, f"Expected adopted=1, got {row['adopted']}"
    assert row["rejected"] == 1, f"Expected rejected=1, got {row['rejected']}"
    assert row["pending"] == 1, f"Expected pending=1, got {row['pending']}"
    conn.close()


def test_strategy_memos_has_content_col_caches_pragma_result():
    from src.services import decision_intelligence as module

    module._STRATEGY_MEMOS_HAS_CONTENT_CACHE.clear()
    db = MagicMock()
    pragma_result = MagicMock()
    pragma_result.fetchall.return_value = [
        (0, "id", "INTEGER", False, None, True),
        (1, "content", "TEXT", False, None, False),
    ]
    db.execute.return_value = pragma_result

    assert module._strategy_memos_has_content_col(db) is True
    assert module._strategy_memos_has_content_col(db) is True
    db.execute.assert_called_once_with("PRAGMA table_info('strategy_memos')")
