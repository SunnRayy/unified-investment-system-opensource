# tests/test_schema.py
def test_schema_creates_all_tables():
    """Test that schema creates all required tables (v6 schema)."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema

    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)

    # Query table names
    result = connector.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'main'
        ORDER BY table_name
    """)
    tables = [row[0] for row in result.fetchall()]

    # Tables that exist after fresh bootstrap (schema + migrations).
    # committee_decisions, market_events, economic_indicators, exchange_rates,
    # schema_snapshots, rsu_vesting_schedules, source_authority_rules, and
    # asset_taxonomy are dropped by Migration 16 (Pass F) and must NOT appear.
    expected_tables = [
        # Original v5 tables (orphaned tables removed — dropped via Migration 16)
        'circuit_breaker_logs',
        'deviation_actions',
        'holdings',
        'insights',
        'market_daily',
        'sync_audit_logs',
        'sync_audit_reports',
        'target_allocations',
        'thresholds',
        'trade_logs',
        'transactions',
        'verification_logs',
        # V3 pipeline tables (orphaned tables removed — dropped via Migration 16)
        'asset_registry',
        'asset_source_mappings',
        'balance_sheet_monthly',
        'current_allocations',
        'income_expense_monthly',
        # Phase 6 tables
        'goals',
        # Phase 8 tables
        'market_sentiment_cache',
        # V4.2+ tables
        'strategy_memos',
        'strategy_review_reports',
        # Additional tables
        'asset_analyses',
        'position_deltas',
        'source_upload_history',
        'auth_credentials',
        # V5.3 Valuation Module
        'valuation_snapshots',
        'valuation_reference',
        'valuation_history',
        'valuation_watchlist',
        # V5.6 Cloud profile persistence
        'user_profile',
        # V5.6.1 Import Adapter Framework
        'import_adapter_runs',
        'import_adapter_staged_rows',
        'import_adapter_approvals',
        # V5.8.0 Decision Feedback Loop
        'verdict_audit',
        # V5.10.0 Insight-trade attribution
        'insight_trade_links',
        # Pass D: Classification tables now part of canonical schema
        'taxonomy_classes',
        'asset_tiers',
        'risk_profiles',
        'risk_profile_allocations',
        'classification_rules',
        'classification_audit_log',
        # Migration 011 (V68): F2 loss-side mandatory review trigger, Batch B3
        'value_trap_reviews',
        # Migration 012 (V69): metric governance, Batch B5
        'metric_catalog',
        'data_fixes',
        'ruling_deferred_events',
        # Migration 013 (V70): F3 North Star panel, Batch B6
        'cash_flow_tags',
        'unforced_errors',
        # Migration 014 (V71): F6 Insight Library governance, Batch B7
        # (ai_insights.validated_cases/rule_layer live in the migration file,
        # not schema.sql, since ai_insights itself is created by migration 008)
        'rule_citations',
        # Migration 015 (V72): Fix 2 memo registry + linkage (2026-07-10)
        'memo_registry',
        'memo_asset_map',
        'asset_memo_confirmations',
    ]

    assert sorted(tables) == sorted(expected_tables)
    
    connector.close()


def test_holdings_table_has_currency_column():
    """Test that holdings table includes currency column."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema
    
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    
    result = connector.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'holdings'
    """)
    columns = [row[0] for row in result.fetchall()]
    
    assert 'currency' in columns
    assert 'derived_from_transaction_id' in columns
    
    connector.close()


def test_schema_auto_increments_id():
    """Test that ID column auto-increments using sequence."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema
    from datetime import date
    
    connector = DatabaseConnector(":memory:")
    initialize_schema(connector)
    
    # Insert without ID
    connector.execute("""
        INSERT INTO holdings (snapshot_date, asset_id, asset_name)
        VALUES (?, ?, ?)
    """, (date.today(), 'TEST1', 'Test Asset 1'))
    
    connector.execute("""
        INSERT INTO holdings (snapshot_date, asset_id, asset_name)
        VALUES (?, ?, ?)
    """, (date.today(), 'TEST2', 'Test Asset 2'))
    
    result = connector.execute("SELECT id, asset_id FROM holdings ORDER BY id")
    rows = result.fetchall()
    
    assert len(rows) == 2
    assert rows[0][0] == 1  # First ID should be 1
    assert rows[1][0] == 2  # Second ID should be 2
    
    connector.close()


def test_run_migrations_bootstraps_ai_reports_debug_columns(tmp_path):
    """Migration bootstrap should add LLM debug columns to ai_reports."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema

    db_path = tmp_path / "test.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)

    connector.run_migrations()

    result = connector.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'ai_reports'
        ORDER BY column_name
    """)
    columns = [row[0] for row in result.fetchall()]

    assert "prompt_text" in columns
    assert "raw_response_text" in columns

    connector.close()


def test_run_migrations_adds_trade_logs_linked_memo_id_column(tmp_path):
    """Migration bootstrap should add linked_memo_id to trade_logs."""
    from src.database.connector import DatabaseConnector
    from src.database.schema import initialize_schema

    db_path = tmp_path / "trade_logs_linked_memo.duckdb"
    connector = DatabaseConnector(str(db_path))
    initialize_schema(connector)

    before = connector.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'trade_logs'
        ORDER BY column_name
    """).fetchall()
    assert "linked_memo_id" not in [row[0] for row in before]

    connector.run_migrations()

    after = connector.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'trade_logs'
        ORDER BY column_name
    """).fetchall()
    columns = [row[0] for row in after]
    assert "linked_memo_id" in columns

    connector.close()


def test_run_migrations_adds_trade_logs_verification_status_column(tmp_path):
    """Migration bootstrap should add verification_status to trade_logs."""
    from src.database.connector import DatabaseConnector

    db_path = tmp_path / "trade_logs_verification_status.duckdb"
    connector = DatabaseConnector(str(db_path))
    connector.execute("CREATE SEQUENCE IF NOT EXISTS seq_trade_logs_id START 1")
    connector.execute(
        """
        CREATE TABLE trade_logs (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_trade_logs_id'),
            log_date DATE NOT NULL,
            asset_id VARCHAR(50) NOT NULL,
            action VARCHAR(20) NOT NULL,
            suggestion_source VARCHAR(50)
        )
        """
    )

    before = connector.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'trade_logs'
        ORDER BY column_name
    """
    ).fetchall()
    assert "verification_status" not in [row[0] for row in before]

    connector.run_migrations()

    after = connector.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'trade_logs'
        ORDER BY column_name
    """
    ).fetchall()
    columns = [row[0] for row in after]
    assert "verification_status" in columns

    connector.close()
