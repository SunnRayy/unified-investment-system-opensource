"""Tests for integrity check #19: trade_log verdict consistency (V5.8.0)."""
from src.database.connector import DatabaseConnector
from src.validation.data_integrity_gate import _check_trade_log_verdict_consistency


def _make_db() -> DatabaseConnector:
    """In-memory DB with minimal trade_logs schema for check #19."""
    db = DatabaseConnector(":memory:")
    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_trade_logs_id START 1")
    db.execute("""
        CREATE TABLE IF NOT EXISTS trade_logs (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_trade_logs_id'),
            log_date DATE NOT NULL,
            asset_id VARCHAR(50) NOT NULL,
            action VARCHAR(20) NOT NULL,
            verification_result VARCHAR(100),
            verification_status VARCHAR(20) DEFAULT 'pending',
            verification_block_reason VARCHAR,
            verdict VARCHAR(50),
            outcome_pct DECIMAL(10,4),
            decision_reason TEXT,
            ai_suggestion TEXT,
            suggestion_source VARCHAR(50),
            linked_transaction_id INTEGER,
            user_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return db


def test_rule_a_verdict_without_verification_result():
    """Rule A: verdict IS NOT NULL AND verification_result IS NULL → violation."""
    db = _make_db()
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verdict, verification_result, decision_reason)
        VALUES ('2026-01-01', 'EQ_AAPL', 'buy', 'good_call', NULL, 'test')
    """)
    result = _check_trade_log_verdict_consistency(db)
    assert not result.passed, "Should fail: verdict set but no verification_result"
    assert "Rule A" in result.details
    db.close()


def test_rule_b_owner_source_verified_missing_verdict():
    """Rule B v2: owner-source (e.g. 'manual') verified row with NULL verdict → violation."""
    db = _make_db()
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_status,
                                verdict, verification_result, suggestion_source, decision_reason)
        VALUES ('2026-01-01', 'EQ_AAPL', 'buy', 'verified', NULL, '买对了', 'manual', 'test')
    """)
    result = _check_trade_log_verdict_consistency(db)
    assert not result.passed, "Should fail: owner-source verified but verdict is NULL"
    assert "Rule B" in result.details
    db.close()


def test_rule_b_null_source_verified_no_verdict_is_not_violation():
    """Rule B v2 carve-out: NULL suggestion_source verified+no verdict is BY DESIGN (KPI row)."""
    db = _make_db()
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_status,
                                verdict, verification_result, suggestion_source, decision_reason)
        VALUES ('2026-01-01', 'EQ_AAPL', 'buy', 'verified', NULL, NULL, NULL, 'test')
    """)
    result = _check_trade_log_verdict_consistency(db)
    assert result.passed, f"NULL-source verified+no-verdict must NOT violate Rule B; got: {result.details}"
    db.close()


def test_rule_b_imported_source_verified_no_verdict_is_not_violation():
    """Rule B v2 carve-out: 'imported' source verified+no verdict is BY DESIGN (KPI row)."""
    db = _make_db()
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_status,
                                verdict, verification_result, suggestion_source, decision_reason)
        VALUES ('2026-01-01', 'EQ_AAPL', 'buy', 'verified', NULL, NULL, 'imported', 'test')
    """)
    result = _check_trade_log_verdict_consistency(db)
    assert result.passed, f"'imported'-source verified+no-verdict must NOT violate Rule B; got: {result.details}"
    db.close()


def test_rule_b_owner_verdict_present_outcome_pct_null_is_not_violation():
    """Rule B v2: outcome_pct IS NULL no longer triggers a violation — verdict present is sufficient."""
    db = _make_db()
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_status,
                                verdict, outcome_pct, verification_result, suggestion_source, decision_reason)
        VALUES ('2026-01-01', 'EQ_AAPL', 'buy', 'verified', 'good_call', NULL, '买对了', 'manual', 'test')
    """)
    result = _check_trade_log_verdict_consistency(db)
    assert result.passed, (
        f"owner-source verified row with verdict set but outcome_pct NULL must NOT violate Rule B; got: {result.details}"
    )
    db.close()


def test_rule_c_blocked_status_missing_reason():
    """Rule C: verification_status='verification_blocked' AND block_reason IS NULL → violation."""
    db = _make_db()
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_status,
                                verification_block_reason, verification_result, decision_reason)
        VALUES ('2026-01-01', 'EQ_UNKN', 'buy', 'verification_blocked', NULL, '不确定', 'test')
    """)
    result = _check_trade_log_verdict_consistency(db)
    assert not result.passed, "Should fail: verification_blocked but no block_reason"
    assert "Rule C" in result.details
    db.close()


def test_all_rules_pass_on_clean_data():
    """Check passes when all trade_log rows are internally consistent."""
    db = _make_db()
    # Fully verified row
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_status,
                                verdict, outcome_pct, verification_result, decision_reason)
        VALUES ('2026-01-01', 'EQ_AAPL', 'buy', 'verified', 'good_call', 5.5, '买对了', 'test')
    """)
    # Blocked row with reason
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_status,
                                verification_block_reason, verification_result, decision_reason)
        VALUES ('2026-01-10', 'EQ_UNKN', 'buy', 'verification_blocked',
                'no market price at log_date+30d', '不确定', 'test')
    """)
    # Pending row (no verdict yet — fine)
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_status, decision_reason)
        VALUES ('2026-02-01', 'EQ_MSFT', 'sell', 'pending_window', 'test')
    """)
    result = _check_trade_log_verdict_consistency(db)
    assert result.passed, f"Should pass on clean data, got: {result.details}"
    db.close()
