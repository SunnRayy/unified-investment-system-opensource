"""Test decisions API endpoints.

RED phase: These tests MUST fail before implementation exists.
"""
import pytest
from datetime import date, timedelta
from fastapi.testclient import TestClient
from pathlib import Path

from src.api.main import app
from src.api.routes import ai_advisor as ai_advisor_routes
from src.classification.schema import create_classification_tables
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema

# --- Setup Helpers ---

def execute_migration(conn, migration_path: Path):
    """Execute SQL migration, stripping comment lines properly."""
    migration_sql = migration_path.read_text()
    lines = [line for line in migration_sql.split('\n') if not line.strip().startswith('--')]
    clean_sql = '\n'.join(lines)
    for stmt in clean_sql.split(';'):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)

@pytest.fixture
def client():
    """Test client with in-memory database."""
    from src.api.dependencies import get_db
    
    test_conn = DatabaseConnector(":memory:")
    initialize_schema(test_conn)
    
    # Apply migrations
    for mig in sorted(Path("src/database/migrations").glob("*.sql")):
        try:
            execute_migration(test_conn, mig)
        except Exception:
            # Ignore errors if migration is already applied by initialize_schema or other reasons
            # For 007, it might not be in initialize_schema yet
            pass
            
    def override_get_db():
        return test_conn
    
    app.dependency_overrides[get_db] = override_get_db
    
    yield TestClient(app)
    
    app.dependency_overrides.clear()
    test_conn.close()


@pytest.fixture
def file_backed_client(tmp_path, monkeypatch):
    """Test client backed by a temporary DuckDB file for write-path integration."""
    from src.api.dependencies import get_db

    db_path = tmp_path / "integration.duckdb"

    bootstrap = DatabaseConnector(str(db_path))
    initialize_schema(bootstrap)
    create_classification_tables(bootstrap)

    for mig in sorted(Path("src/database/migrations").glob("*.sql")):
        try:
            execute_migration(bootstrap, mig)
        except Exception:
            pass
    bootstrap.run_migrations()
    bootstrap.close()

    def override_get_db():
        conn = DatabaseConnector(str(db_path))
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(ai_advisor_routes, "_DB_PATH", db_path)
    monkeypatch.setattr(
        "src.database.connector.resolve_db_path",
        lambda path="data/unified.duckdb": str(db_path) if path == "data/unified.duckdb" else path,
    )
    yield TestClient(app), db_path

    app.dependency_overrides.clear()

@pytest.fixture
def client_with_decisions_data(client):
    """Test client with mix of insights, trades, and drift logs."""
    from src.api.dependencies import get_db
    conn = app.dependency_overrides[get_db]()
    
    # 1. Insert Insights
    conn.execute("""
        INSERT INTO insights (content, category, created_at, adopted, ai_model, title, insight_date, insight_type)
        VALUES 
        ('Reduce CN Equity', 'recommendation', '2026-02-01 10:00:00', 1, 'my_model', 'Reduce CN Equity Exposure', '2026-02-01', 'strategic'),
        ('Watch US Tech', 'observation', '2026-02-02 09:00:00', NULL, 'my_model', 'Tech Volatility', '2026-02-02', 'tactical')
    """)
    
    # 2. Insert Trades (simulated in trade_logs if it existed, or transactions)
    # Assuming trade_logs table based on plan, or maybe mapping from transactions?
    # Plan mentioned 'trade_logs'. Let's check schema. If not exists, use transactions.
    # Actually, let's assume 'transactions' table is the source for 'trade' type decision items for now,
    # or create a mock if the plan implies a new table. 
    # The plan says: `trade_logs` + `deviation_actions` + `insights`.
    # Let's check if `deviation_actions` exists.
    
    # Check if table exists
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    
    if 'deviation_actions' in tables:
        conn.execute("""
            INSERT INTO deviation_actions (
                asset_class, deviation_pct, tolerance_pct, status, created_at, is_within_tolerance, detected_date
            ) VALUES (
                'Equity', 8.5, 5.0, 'observing', '2026-02-01 12:00:00', 0, '2026-02-01'
            )
        """)
        
    return client

# --- Tests ---

def test_get_decisions_timeline(client_with_decisions_data):
    """Should return merged timeline of decisions."""
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, suggestion_source, verification_result, verification_status
        )
        VALUES
          ('2026-02-03', 'US_STK_IBIT', 'Buy', 'imported', '验证通过', 'verified'),
          ('2026-02-02', 'US_STK_NVDA', 'Buy', 'manual', '手工记录', 'pending')
        """
    )

    response = client_with_decisions_data.get("/decisions/timeline")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "summary" in data
    
    # Verify merged items
    items = data["items"]
    assert len(items) >= 2  # At least 2 insights + deviation
    
    # Check Insight Item
    insight = next((i for i in items if i["type"] == "insight" and i["title"] == "Reduce CN Equity Exposure"), None)
    assert insight is not None
    assert insight["status"] == "adopted"
    
    # Check Drift Item
    drift = next((i for i in items if i["type"] == "drift"), None)
    if drift:
        assert drift["status"] == "observing"

    # Timeline trade stream should use display scope (includes non-attributed manual trades).
    trade_asset_ids = [i["metadata"]["asset_id"] for i in items if i["type"] == "trade"]
    assert "US_STK_IBIT" in trade_asset_ids
    assert "US_STK_NVDA" in trade_asset_ids
    trade_statuses = {i["metadata"]["asset_id"]: i["verification_status"] for i in items if i["type"] == "trade"}
    assert trade_statuses["US_STK_IBIT"] == "verified"
    assert trade_statuses["US_STK_NVDA"] == "pending"


def test_get_decisions_timeline_excludes_lessons_and_returns_display_fields(client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO insights (
            insight_date, insight_type, category, title, content,
            adopted, ai_model, observation_source
        ) VALUES
          ('2026-03-16', 'recommendation', 'recommendation', 'AMZN RSU 归属 100% 变现',
           'AMZN RSU 归属 100% 变现', 1, 'memo', 'Memo 009 战略'),
          ('2026-03-16', 'lesson', 'lesson', 'RSU 纪律性变现',
           'RSU 纪律性变现 — 展现了极强的防御纪律性', NULL, 'observation', '成长轨迹')
        """
    )

    response = client.get("/decisions/timeline?type=all")
    assert response.status_code == 200
    data = response.json()

    titles = [item["title"] for item in data["items"] if item["type"] == "insight"]
    assert "AMZN RSU 归属 100% 变现" in titles
    assert "RSU 纪律性变现" not in titles

    insight = next(item for item in data["items"] if item["type"] == "insight")
    assert insight["subtype"] == "recommendation"
    assert insight["display_source"] == "memo"
    assert insight["display_status"] == "adopted"
    assert insight["origin_ref"].startswith("insights:")


def test_get_decisions_timeline_trade_includes_memo_link_metadata(client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
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
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, quantity, price, amount,
            suggestion_source, decision_reason, ai_suggestion
        ) VALUES (
            '2026-03-20', 'US_STK_VOO', 'Buy', 8, 595.0, 4760.0,
            'imported',
            '标普500跌到6500点，执行别人恐惧我贪婪的纪律性接盘策略',
            '战略备忘录 Memo 009: 梯队1 SPX <= 6500 -> 买入 VOO @ Limit $595.00'
        )
        """
    )

    response = client.get("/decisions/timeline?type=trade")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["display_source"] == "memo"
    assert item["match_status"] in {"memo_linked", "source_only"}
    assert item["metadata"]["effective_source"] == "memo"
    assert item["metadata"]["linked_title"]
    assert item["metadata"]["linked_ref"].startswith("strategy_memos:")
    assert "Memo 009" in item["metadata"]["reason_excerpt"]


def test_get_decisions_timeline_trade_links_memo_from_content_when_title_and_directives_do_not_match(client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute("ALTER TABLE strategy_memos ADD COLUMN IF NOT EXISTS content TEXT")
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
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, quantity, price, amount, suggestion_source
        ) VALUES (
            '2026-03-20', 'US_STK_VOO', 'Buy', 8, 595.0, 4760.0, 'imported'
        )
        """
    )

    response = client.get("/decisions/timeline?type=trade")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["display_source"] == "memo"
    assert item["match_status"] == "memo_linked"
    assert item["metadata"]["linked_ref"].startswith("strategy_memos:")
    assert item["metadata"]["linked_title"] == "投资战略 Memo：防御框架更新"


def test_imported_memo_then_ai_trade_links_in_decisions_timeline(file_backed_client):
    client, db_path = file_backed_client

    with DatabaseConnector(str(db_path), read_only=False) as db:
        db.execute(
            """
            INSERT INTO asset_registry (canonical_id, display_name, base_currency)
            VALUES ('US_STK_VOO', 'VOO', 'USD')
            """
        )
        db.execute(
            """
            INSERT INTO strategy_memos (memo_date, title, strategic_bias, key_directives, source_file, content)
            VALUES ('2026-03-21', '投资战略 Memo：防守反击节奏', 'defensive', '[]', '2026-03-21-memo_013.md',
                    '如果 VOO 回撤到目标区间，分批执行买入。')
            """
        )
        db.execute(
            """
            INSERT INTO trade_logs (
                log_date, asset_id, asset_name, action, quantity, price, amount,
                suggestion_source, decision_reason
            ) VALUES (
                '2026-03-22', 'US_STK_VOO', 'VOO', 'Buy', 8, 595.0, 4760.0,
                'imported', '等待回撤后分批买入 VOO'
            )
            """
        )

    response = client.get("/decisions/timeline?type=trade")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["display_source"] == "memo"
    assert item["match_status"] == "memo_linked"
    assert item["metadata"]["linked_ref"].startswith("strategy_memos:")
    assert item["metadata"]["linked_title"] == "投资战略 Memo：防守反击节奏"


def test_promote_ai_insight_bridges_into_decisions_timeline_with_runtime_db_path(tmp_path, monkeypatch):
    stale_db_path = tmp_path / "stale-ai-advisor.duckdb"
    live_db_path = tmp_path / "live-runtime.duckdb"

    def bootstrap_db(path: Path) -> None:
        db = DatabaseConnector(str(path))
        initialize_schema(db)
        create_classification_tables(db)
        for mig in sorted(Path("src/database/migrations").glob("*.sql")):
            try:
                execute_migration(db, mig)
            except Exception:
                pass
        db.run_migrations()
        db.close()

    bootstrap_db(stale_db_path)
    bootstrap_db(live_db_path)

    for db_path in (stale_db_path, live_db_path):
        with DatabaseConnector(str(db_path), read_only=False) as db:
            db.execute(
                """
                INSERT INTO ai_insights (
                    category, title, body, tags, confidence, status, recurrence_count, entity_refs
                ) VALUES (
                    'risk', 'Bridge into Decision Hub', 'Promoted principle should surface in timeline',
                    'bridge', 0.95, 'validated', 2, 'US_STK_TEST'
                )
                """
            )

    monkeypatch.setenv("UIS_DB_PATH", str(live_db_path))
    monkeypatch.setattr(ai_advisor_routes, "_DB_PATH", stale_db_path)

    client = TestClient(app)

    promote_response = client.post("/ai-advisor/insights/1/promote")
    assert promote_response.status_code == 200, promote_response.text

    timeline_response = client.get("/decisions/timeline?type=insight")
    assert timeline_response.status_code == 200, timeline_response.text

    insight_titles = [item["title"] for item in timeline_response.json()["items"] if item["type"] == "insight"]
    assert "Bridge into Decision Hub" in insight_titles


def test_get_decisions_stats(client_with_decisions_data):
    """Should return summary stats."""
    response = client_with_decisions_data.get("/decisions/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_insights"] >= 2
    assert data["adopted_count"] >= 1
    assert data["adoption_rate"] > 0
    assert "ai_trades_total" in data
    assert "ai_scored_total" in data
    assert "ai_last_sync_date" in data


def test_get_decisions_stats_uses_alert_counts_for_pending_actions_and_drift(client, monkeypatch):
    import src.api.routes.decisions as decisions_routes

    monkeypatch.setattr(
        decisions_routes,
        "generate_alerts",
        lambda db: [
            {"category": "drift", "priority": "high", "title": "Equity drift", "message": "Drift"},
            {"category": "drift", "priority": "medium", "title": "FI drift", "message": "Drift"},
            {"category": "strategy", "priority": "low", "title": "Memo", "message": "Review"},
        ],
    )

    response = client.get("/decisions/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["pending_actions_count"] == 3
    assert data["active_drift_alerts"] == 2
    assert data["pending_count"] == 0


def test_get_decisions_scorecard(client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, asset_name, action, price, quantity, amount,
            suggestion_source, verification_date, verification_result, verdict, outcome_pct, decision_grade,
            verification_status
        ) VALUES (
            '2026-01-13', 'US_ETF_SGOV', 'SGOV', 'Buy', 100, 10, 1000,
            'imported', '2026-04-13', '+3.11% 达到预期', 'good_call', 3.11, 'A', 'verified'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, asset_name, action, price, quantity, amount,
            suggestion_source, verification_date, verification_result, verdict, outcome_pct, decision_grade,
            verification_status
        ) VALUES (
            '2026-01-14', 'US_STK_NVDA', 'NVDA', 'Buy', 200, 2, 400,
            'manual', '2026-04-14', '+1.00% 手工记录', 'good_call', 1.0, 'A', 'pending'
        )
        """
    )

    response = client.get("/decisions/scorecard?limit=10")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) == 2
    by_asset = {item["asset_id"]: item for item in data["items"]}
    assert by_asset["US_ETF_SGOV"]["verification_status"] == "verified"
    assert by_asset["US_STK_NVDA"]["verification_status"] == "pending"
    assert by_asset["US_STK_NVDA"]["source"] == "manual"
    assert by_asset["US_ETF_SGOV"]["verdict"] in {"good_call", "regret", None}


def test_scorecard_lazy_scoring_and_source_match(client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, asset_name, action, verification_result,
            verdict, outcome_pct, suggestion_source
        ) VALUES (
            '2026-01-13', 'US_ETF_SGOV', 'SGOV', 'Buy', '明智且及时的操作',
            NULL, NULL, NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO insights (insight_date, insight_type, category, content, adopted, ai_model, title)
        VALUES ('2026-01-12', 'signal', 'trade', '建议买入 SGOV', 1, 'gemini', '买入 SGOV')
        """
    )
    conn.execute(
        """
        INSERT INTO market_daily (code, date, close, open)
        VALUES
          ('SGOV', '2026-01-13', 100, 100),
          ('SGOV', '2026-02-12', 110, 110)
        """
    )

    response = client.get("/decisions/scorecard?limit=10")
    assert response.status_code == 200

    row = conn.execute(
        """
        SELECT verdict, outcome_pct, suggestion_source
        FROM trade_logs
        WHERE asset_id = 'US_ETF_SGOV'
        """
    ).fetchone()
    assert row[0] == "good_call"
    assert round(float(row[1]), 2) == 10.0


def test_decisions_scorecard_returns_match_metadata_and_unscored_reason(client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO insights (
            insight_date, insight_type, category, title, content, adopted, ai_model, observation_source
        ) VALUES (
            '2026-03-05', 'recommendation', 'recommendation',
            '建立通过 Schwab 通道离岸布局打折美股防线的方案',
            '建议将离岸资金先买入 SGOV，等待更优反攻时机', 1, 'memo', 'Memo 006 战略'
        )
        """
    )
    # verification_date must be a future date so verify_on > today holds.
    # Use 30 days out rather than a hardcoded date that becomes stale.
    future_vdate = (date.today() + timedelta(days=30)).isoformat()
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, asset_name, action, price, quantity, amount,
            suggestion_source, verification_date, verification_result, decision_reason, ai_suggestion
        ) VALUES (
            '2026-03-05', 'US_STK_SGOV', 'SGOV', 'Buy', 100, 10, 1000,
            NULL, ?, NULL, '资金通道重构后的现金管理', '建议将离岸资金先买入 SGOV，等待更优反攻时机'
        )
        """,
        [future_vdate],
    )

    response = client.get("/decisions/scorecard?limit=10")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["linked_insight_title"] == "建立通过 Schwab 通道离岸布局打折美股防线的方案"
    assert item["match_status"] == "matched"
    assert item["why_unscored"] == "awaiting_verification_window"


def test_decisions_scorecard_handles_read_only_dependency_without_closing_link_db(tmp_path):
    from src.api.dependencies import get_db

    db_path = tmp_path / "scorecard-readonly.duckdb"
    writer = DatabaseConnector(str(db_path))
    initialize_schema(writer)
    for mig in sorted(Path("src/database/migrations").glob("*.sql")):
        try:
            execute_migration(writer, mig)
        except Exception:
            pass

    writer.execute(
        """
        INSERT INTO insights (
            insight_date, insight_type, category, title, content, adopted, ai_model, observation_source
        ) VALUES (
            '2026-03-05', 'recommendation', 'recommendation',
            '建立通过 Schwab 通道离岸布局打折美股防线的方案',
            '建议将离岸资金先买入 SGOV，等待更优反攻时机', 1, 'memo', 'Memo 006 战略'
        )
        """
    )
    future_vdate2 = (date.today() + timedelta(days=30)).isoformat()
    writer.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, asset_name, action, price, quantity, amount,
            suggestion_source, verification_date, verification_result, decision_reason, ai_suggestion
        ) VALUES (
            '2026-03-05', 'US_STK_SGOV', 'SGOV', 'Buy', 100, 10, 1000,
            NULL, ?, NULL, '资金通道重构后的现金管理', '建议将离岸资金先买入 SGOV，等待更优反攻时机'
        )
        """,
        [future_vdate2],
    )
    writer.close()

    read_only_conn = DatabaseConnector(str(db_path), read_only=True)

    def override_get_db():
        return read_only_conn

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.get("/decisions/scorecard?limit=10")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["linked_insight_title"] == "建立通过 Schwab 通道离岸布局打折美股防线的方案"
    finally:
        app.dependency_overrides.clear()
        read_only_conn.close()


def test_decisions_intelligence_returns_structured_sections(client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO insights (
            insight_date, insight_type, category, title, content, adopted, ai_model, observation_source
        ) VALUES
          ('2026-03-16', 'recommendation', 'recommendation', 'AMZN RSU 归属 100% 变现',
           'AMZN RSU 归属 100% 变现', 1, 'memo', 'Memo 009 战略'),
          ('2026-03-16', 'lesson', 'lesson', 'RSU 纪律性变现',
           'RSU 纪律性变现 — 展现了极强的防御纪律性', NULL, 'observation', '成长轨迹')
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
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, quantity, price, amount,
            suggestion_source, decision_reason, ai_suggestion
        ) VALUES (
            '2026-03-20', 'US_STK_VOO', 'Buy', 8, 595.0, 4760.0,
            'memo',
            '标普500跌到6500点，执行别人恐惧我贪婪的纪律性接盘策略',
            '战略备忘录 Memo 009: 梯队1 SPX <= 6500 -> 买入 VOO @ Limit $595.00'
        )
        """
    )

    response = client.get("/decisions/intelligence")
    assert response.status_code == 200
    data = response.json()

    assert "decision_patterns" in data
    assert "growth_timeline" in data
    assert "raw_sections" in data
    assert data["raw_sections"] == []
    assert data["growth_timeline"][0]["title"] == "RSU 纪律性变现"
    assert data["decision_patterns"]["funnel"]["linked_adopted_trades"] >= 1
    by_source = {row["source"]: row for row in data["decision_patterns"]["sources"]}
    assert by_source["memo"]["linked_trades"] >= 1





def test_get_decisions_funnel(client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO insights (insight_date, insight_type, category, content, adopted)
        VALUES ('2026-01-01', 'signal', 'macro', 'a', 1)
        """
    )
    conn.execute(
        """
        INSERT INTO strategy_memos (memo_date, title, strategic_bias, key_directives, source_file)
        VALUES ('2026-01-05', 'SGOV Strategy', 'defensive', '["买入 SGOV 作为现金替代"]', 'memo.md')
        """
    )
    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, verdict, suggestion_source)
        VALUES ('2026-01-10', 'US_ETF_SGOV', 'Buy', 'good_call', 'memo')
        """
    )
    conn.execute(
        """
        INSERT INTO trade_logs (log_date, asset_id, action, verdict, suggestion_source)
        VALUES ('2026-01-11', 'US_STK_NVDA', 'Buy', 'good_call', 'manual')
        """
    )

    response = client.get("/decisions/funnel")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert "good_call" in data
    assert data["good_call"] == 1


def test_get_decisions_leaderboard(client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO strategy_memos (memo_date, title, strategic_bias, key_directives, source_file)
        VALUES
          ('2026-01-05', 'SGOV Strategy', 'defensive', '["买入 SGOV"]', 'memo_sgov.md'),
          ('2026-01-05', 'NVDA Strategy', 'aggressive', '["卖出 NVDA"]', 'memo_nvda.md')
        """
    )
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, verdict, outcome_pct, suggestion_source
        ) VALUES
          ('2026-01-10', 'US_ETF_SGOV', 'Buy', 'good_call', 1.2, 'memo'),
          ('2026-01-11', 'US_STK_NVDA', 'Sell', 'regret', -2.3, 'memo'),
          ('2026-01-12', 'CN_FUND_900002', 'Sell', 'good_call', 3.0, 'manual')
        """
    )

    response = client.get("/decisions/leaderboard")
    assert response.status_code == 200
    data = response.json()
    assert "sources" in data
    assert len(data["sources"]) >= 1
    assert all(item["source"] != "manual" for item in data["sources"])


def test_get_decisions_leaderboard_uses_effective_source_over_generic_aia_tag(client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO strategy_memos (
            memo_date, title, strategic_bias, key_directives, source_file
        ) VALUES (
            '2026-03-09',
            '投资战略 Memo：滞胀恐慌下的防御与自动反击',
            'defensive',
            '["重塑别人恐惧我贪婪，SPX <= 6500 买入 VOO"]',
            'memo_009.md'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, verdict, outcome_pct, suggestion_source,
            decision_reason, ai_suggestion
        ) VALUES (
            '2026-03-20', 'US_STK_VOO', 'Buy', 'good_call', 3.1, 'memo',
            '执行 Memo 009 纪律性反击',
            '战略备忘录 Memo 009: 梯队1 SPX <= 6500 -> 买入 VOO @ Limit $595.00'
        )
        """
    )

    response = client.get("/decisions/leaderboard")
    assert response.status_code == 200
    data = response.json()
    by_source = {row["source"]: row for row in data["sources"]}
    assert "memo" in by_source
    assert "aia_trades_md" not in by_source


# ── B3: single-source-of-truth for shared adoption metrics ────────────────────


def _seed_b3_insights(conn: DatabaseConnector) -> None:
    """Seed a representative mix of insights for B3 adoption-metric tests."""
    conn.execute(
        """
        INSERT INTO insights (
            insight_date, insight_type, category, content, adopted, created_at, title
        ) VALUES
          ('2026-04-01', 'recommendation', 'recommendation', 'Overweight SGOV', 1,
           '2026-04-01 09:00:00', 'SGOV'),
          ('2026-04-02', 'recommendation', 'macro',          'Hold bonds',      1,
           '2026-04-02 09:00:00', 'Bonds'),
          ('2026-04-03', 'observation',    'observation',    'Watch rates',     NULL,
           '2026-04-03 09:00:00', 'Rates'),
          ('2026-04-04', 'lesson',         'lesson',         'Learned X',       NULL,
           '2026-04-04 09:00:00', 'Lesson A')
        """
    )


def test_decisions_stats_and_verification_report_agree_on_adoption_metrics(client):
    """GET /decisions/stats and compute_verification_report() must return the same
    total_insights and adoption_rate for the same seeded data (B3 contract)."""
    from src.api.dependencies import get_db
    from src.services.verification_service import compute_verification_report

    conn = app.dependency_overrides[get_db]()
    _seed_b3_insights(conn)

    # Live path: Decision Hub stats endpoint
    response = client.get("/decisions/stats")
    assert response.status_code == 200
    stats = response.json()

    # Snapshot path: compute_verification_report persists + returns the same KPIs
    report = compute_verification_report(conn)

    assert stats["total_insights"] == report["total_insights"], (
        f"total_insights mismatch: stats={stats['total_insights']} "
        f"report={report['total_insights']}"
    )
    assert stats["adoption_rate"] == report["adoption_rate"], (
        f"adoption_rate mismatch: stats={stats['adoption_rate']} "
        f"report={report['adoption_rate']}"
    )

    # Sanity-check the expected values (3 non-lesson insights, 2 adopted)
    assert stats["total_insights"] == 3
    assert stats["adoption_rate"] == 66.7


def test_decisions_stats_adoption_metrics_match_shared_function(client):
    """GET /decisions/stats values for total_insights, adopted_count, adoption_rate
    are exactly those returned by the shared compute_insight_adoption_metrics()."""
    from src.api.dependencies import get_db
    from src.services.decision_scorer import compute_insight_adoption_metrics

    conn = app.dependency_overrides[get_db]()
    _seed_b3_insights(conn)

    response = client.get("/decisions/stats")
    assert response.status_code == 200
    stats = response.json()

    shared = compute_insight_adoption_metrics(conn)

    assert stats["total_insights"] == shared["total_insights"]
    assert stats["adopted_count"] == shared["adopted_count"]
    assert stats["adoption_rate"] == shared["adoption_rate"]
